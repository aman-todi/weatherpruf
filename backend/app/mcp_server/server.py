"""Building the FastMCP server and its ASGI app.

``app.main`` mounts what ``build_mcp_app()`` returns at ``/mcp`` and tolerates
``None``, so this module's job is to either produce a working sub-app or decline
clearly. It declines when FastMCP is not installed (the REST API and the
frontend's dev backend do not need it) — never by raising during import, which
would take the whole process down with it.

One wrinkle worth naming, because it is invisible until requests start failing:
Starlette does **not** run a mounted sub-application's lifespan. FastMCP's HTTP
app uses that lifespan to start its session manager, so a plainly-mounted
sub-app would 500 on the first request with "task group is not initialized".
``_LifespanOnFirstRequest`` below starts it on demand instead, which keeps the
``build_mcp_app() -> ASGI app`` contract with ``app.main`` intact rather than
requiring the parent app to know about ours.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class _LifespanOnFirstRequest:
    """Run a mounted ASGI app's lifespan lazily, on its first request.

    Starlette's ``Mount`` forwards ``http`` and ``websocket`` scopes to the
    child but not ``lifespan``, so a sub-app that needs startup never gets it.
    This wrapper enters the child's lifespan context before forwarding the first
    request and holds it open for the life of the process.

    The lock makes the startup happen exactly once even if several requests
    arrive together. Shutdown is deliberately not driven from here: the parent's
    ``lifespan`` shutdown does not reach a mounted app either, and the session
    manager's resources are process-scoped, so they go when the process does.
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._lock = asyncio.Lock()
        self._context: Any = None
        self._started = False

    async def _ensure_started(self) -> None:
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
            router = getattr(self._app, "router", None)
            lifespan = getattr(router, "lifespan_context", None)
            if lifespan is None:
                self._started = True
                return
            self._context = lifespan(self._app)
            await self._context.__aenter__()
            self._started = True
            logger.info("MCP sub-app lifespan started")

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            # Reached only if something mounts this in a way that *does*
            # forward lifespan; the real app is then started here rather than
            # on the first request, and the request path becomes a no-op.
            await self._ensure_started()
            await self._app(scope, receive, send)
            return
        await self._ensure_started()
        await self._app(scope, receive, send)


def build_server(settings: Settings | None = None):
    """The configured ``FastMCP`` instance, with all eight tools registered."""
    from fastmcp import FastMCP

    from app.mcp_server import instructions
    from app.mcp_server.auth import build_auth_provider
    from app.mcp_server.tools import register_tools

    settings = settings or get_settings()

    mcp = FastMCP(
        name="Wardrobe",
        version="0.1.0",
        instructions=instructions.SERVER_INSTRUCTIONS,
        auth=build_auth_provider(settings),
        # Errors raised out of a tool body are bugs, not messages for the user:
        # domain problems already come back as structured results. Masking their
        # detail in production keeps internals (SQL text, connection strings in
        # a driver error) out of a chat transcript.
        mask_error_details=settings.is_production,
    )
    register_tools(mcp)
    return mcp


def build_mcp_app():
    """The ASGI app ``app.main`` mounts at ``/mcp``, or ``None``.

    Returns ``None`` rather than raising when FastMCP is absent, so a
    deployment that only serves the REST API still boots.
    """
    try:
        import fastmcp  # noqa: F401
    except ImportError:
        logger.warning(
            "fastmcp is not installed; /mcp will not be served. "
            "Install the 'mcp' extra to enable the connector."
        )
        return None

    settings = get_settings()
    mcp = build_server(settings)

    # Mounted at /mcp by the parent, so the sub-app serves the endpoint at its
    # own root; stateless_http keeps each call self-contained, which is what a
    # remote connector behind a load balancer wants.
    http_app = mcp.http_app(path="/", stateless_http=True)

    app = _LifespanOnFirstRequest(http_app)

    # RFC 9728 puts a protected resource's discovery document at the *origin
    # root* — /.well-known/oauth-protected-resource/mcp/ — not under the
    # resource's own path. FastMCP registers it on this sub-app, which is
    # mounted at /mcp, so as mounted it would only ever be reachable at
    # /mcp/.well-known/..., while the 401 challenge correctly advertises the
    # root URL. A connector follows the advertised URL, 404s, and never starts
    # the OAuth flow.
    #
    # So the routes are handed to the parent to re-expose at the root. Kept as
    # an attribute rather than a change to build_mcp_app's return type, so a
    # caller that does not know about it still gets a working ASGI app.
    app.well_known_routes = [
        route
        for route in http_app.routes
        if str(getattr(route, "path", "")).startswith("/.well-known/")
    ]
    logger.info(
        "MCP server built; /mcp is served, %d discovery route(s) to publish at the root",
        len(app.well_known_routes),
    )
    return app
