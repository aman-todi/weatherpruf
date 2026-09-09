"""Closet item CRUD for the web app.

Note what is *not* here: no filtering by a free-text predicate. The web app
filters by category and tags, which are fixed parameterized queries. The
assistant's general-purpose predicate path is the MCP tool, and it goes through
a separate read-only database role for that reason.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser
from app.config import get_settings
from app.models import Item, ItemCreate, ItemUpdate
from app.services import items as items_service

router = APIRouter(tags=["items"])


class TagCount(BaseModel):
    tag: str
    item_count: int


class ItemListResponse(BaseModel):
    items: list[Item]
    total: int = Field(description="The user's whole closet count, ignoring any filter.")


@router.get("/items", response_model=ItemListResponse, summary="List the caller's items")
async def list_items(
    user: CurrentUser,
    category: Annotated[str | None, Query(description="Restrict to one category id.")] = None,
    tags: Annotated[
        list[str] | None,
        Query(description="Repeatable. An item must carry every tag listed."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ItemListResponse:
    found = await items_service.list_items(
        user.user_id,
        category=category.strip().lower() if category else None,
        tags=tags,
        limit=limit,
        offset=offset,
    )
    # `total` is the closet size, not the filtered count — the UI uses it to
    # show how much of the 200-item cap is spent.
    return ItemListResponse(items=found, total=await items_service.count_items(user.user_id))


@router.get(
    "/tags",
    response_model=list[TagCount],
    summary="The caller's distinct tags, most-used first",
)
async def list_tags(user: CurrentUser) -> list[TagCount]:
    """Backs the closet browser's tag filter. Tags have no registry table, so
    this is derived from the items that carry them."""
    return [TagCount(**row) for row in await items_service.list_tags(user.user_id)]


@router.post(
    "/items",
    response_model=Item,
    status_code=status.HTTP_201_CREATED,
    summary="Add an item",
)
async def create_item(payload: ItemCreate, user: CurrentUser) -> Item:
    return await items_service.create_item(user.user_id, payload)


@router.get("/items/{item_id}", response_model=Item, summary="Read one item")
async def get_item(item_id: UUID, user: CurrentUser) -> Item:
    return await items_service.get_item(user.user_id, item_id)


@router.patch("/items/{item_id}", response_model=Item, summary="Edit an item")
async def update_item(item_id: UUID, patch: ItemUpdate, user: CurrentUser) -> Item:
    return await items_service.update_item(user.user_id, item_id, patch)


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an item",
)
async def delete_item(item_id: UUID, user: CurrentUser) -> Response:
    await items_service.delete_item(user.user_id, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/limits", tags=["meta"], summary="The caps that apply to this account")
async def get_limits(user: CurrentUser) -> dict[str, int]:
    settings = get_settings()
    return {
        "item_count": await items_service.count_items(user.user_id),
        "item_limit": settings.max_items_per_user,
        "max_colors_per_item": settings.max_colors_per_item,
    }
