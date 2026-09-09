"""Category listing — backs the frontend's dynamic add/edit form."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.models import Category
from app.services import taxonomy

router = APIRouter(tags=["taxonomy"])


@router.get(
    "/categories",
    response_model=list[Category],
    summary="Every category with its category-specific field template",
)
async def list_categories(_: CurrentUser) -> list[Category]:
    return await taxonomy.load_categories()
