"""Serving the built frontend from the same origin as the API.

One container is the whole product: the React bundle at ``/``, the REST API at
``/api``, and the MCP connector at ``/mcp``. That collapses a set of problems
rather than solving them — there is no CORS configuration, no second
certificate, no second deploy, and ``PUBLIC_BASE_URL`` is simply where
everything is. At this app's traffic (single-digit concurrent users, spec §0)
the cost of serving static files from the Python process is not measurable.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

logger = logging.getLogger(__name__)

# Prefixes owned by the application. A request under one of these that matched
# no route is a genuine 404 and must say so, rather than being handed the SPA
# shell with a 200 — a mistyped endpoint returning HTML is a miserable thing to
# debug from the client side, and a connector would see it as a protocol error.
_API_PREFIXES = ("api/", "mcp", "health", "docs", "redoc", "openapi.json")


class SinglePageApp(StaticFiles):
    """Static files with a client-side-routing fallback.

    A React route like ``/settings`` has no file behind it, so a plain static
    handler 404s on a page reload. Anything that is not a real file and not an
    application path falls back to ``index.html`` and lets the router decide —
    including, deliberately, showing its own not-found page.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            if path.startswith(_API_PREFIXES):
                raise
            return FileResponse(Path(self.directory) / "index.html")


def mount_frontend(application: FastAPI, dist_path: str) -> bool:
    """Mount the built frontend at ``/`` if it is present.

    Returns whether it was mounted. Absence is normal, not an error: in local
    development Vite serves the frontend on its own port, and a backend-only
    image is a legitimate way to run this.

    Must be called *after* the API and MCP routes are registered — Starlette
    matches in registration order, and this mount matches every path.
    """
    directory = Path(dist_path)
    if not (directory / "index.html").is_file():
        logger.info("no frontend build at %s; serving the API and connector only", directory)
        return False

    application.mount("/", SinglePageApp(directory=directory, html=True), name="frontend")
    logger.info("serving the frontend from %s", directory)
    return True
