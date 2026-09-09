"""REST API routes for the web app (Ticket 2).

Everything here is a thin adapter over ``app.services``: the same functions the
MCP tools call, so the 200-item cap, the colour limit, category-field
validation and RLS scoping behave identically whichever surface a change
arrives through.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.account import router as account_router
from app.api.items import router as items_router
from app.api.taxonomy import router as taxonomy_router

api_router = APIRouter()
api_router.include_router(taxonomy_router)
api_router.include_router(items_router)
api_router.include_router(account_router)

__all__ = ["api_router"]
