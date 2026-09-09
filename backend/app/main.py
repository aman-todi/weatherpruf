"""FastAPI application: the REST API for the web app plus the mounted MCP
sub-app Claude.ai connects to, in one process sharing one auth and data-access
layer (spec §2)."""

from __future__ import annotations

import importlib.util
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.config import get_settings
from app.errors import WardrobeError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    await db.connect(settings)
    logger.info("connected to database; MCP served at %s", settings.mcp_url)
    try:
        yield
    finally:
        await db.disconnect()


def create_app() -> FastAPI:
    settings = get_settings()

    application = FastAPI(
        title="Wardrobe MCP App",
        version="0.1.0",
        description=(
            "Manages a digital closet. Day-to-day use happens through an AI assistant over the "
            "MCP connector at /mcp; this REST API backs the web app that onboards users and "
            "lets them browse and correct their closet directly."
        ),
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(WardrobeError)
    async def _wardrobe_error_handler(_: Request, exc: WardrobeError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @application.exception_handler(RequestValidationError)
    async def _request_validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        """Render body/query validation failures in the same envelope as domain
        errors, so a client only has to understand one error shape."""
        problems = [
            {
                "field": ".".join(str(part) for part in error["loc"][1:]) or "body",
                "message": error["msg"].removeprefix("Value error, "),
            }
            for error in exc.errors()
        ]
        summary = "; ".join(f"{p['field']}: {p['message']}" for p in problems)
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_failed",
                "message": summary or "the request body is not valid",
                "details": {"problems": problems},
            },
        )

    @application.get("/health", tags=["meta"], summary="Liveness and dependency check")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "environment": settings.environment,
            "database": await db.healthcheck(),
        }

    _mount_rest_api(application)
    _mount_mcp(application)
    return application


def _mount_rest_api(application: FastAPI) -> None:
    """Ticket 2's routes, mounted under /api."""
    from app.api import api_router

    application.include_router(api_router, prefix="/api")


def _mount_mcp(application: FastAPI) -> None:
    """Ticket 3's FastMCP sub-app, mounted at /mcp.

    ``build_mcp_app`` returns an ASGI app, or ``None`` when the connector is
    not configured, so the REST API still runs in environments that do not need
    it. A missing *package* is tolerated the same way; a package that exists
    but fails to import is a real bug and is left to raise, rather than being
    swallowed into a warning that looks the same as "not installed".
    """
    if importlib.util.find_spec("app.mcp_server") is None:
        logger.warning("app.mcp_server is not present; /mcp will not be served")
        return

    from app.mcp_server import build_mcp_app

    mcp_app = build_mcp_app()
    if mcp_app is None:
        logger.warning("MCP server is not configured; /mcp will not be served")
        return
    application.mount("/mcp", mcp_app)
    logger.info("mounted MCP sub-app at /mcp")

    # RFC 9728 requires a protected resource's discovery document to live at the
    # origin root (/.well-known/oauth-protected-resource/mcp/), which a sub-app
    # mounted under /mcp cannot serve. Without this, the 401 challenge points at
    # a URL that 404s and a remote connector never starts its OAuth flow.
    for route in getattr(mcp_app, "well_known_routes", []):
        application.router.routes.append(route)
        logger.info("published OAuth discovery route at %s", route.path)


app = create_app()
