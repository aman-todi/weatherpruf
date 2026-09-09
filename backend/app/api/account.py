"""Profile, account summary, and the account-deletion flow.

Deletion is a web-app-only action on purpose (spec §5): wiping an entire closet
is irreversible and should not be one careless chat message away, so it is
deliberately absent from the MCP tool list.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.config import get_settings
from app.models import UserProfile, UserProfileUpdate
from app.services import items as items_service
from app.services import profile as profile_service
from app.services import usage as usage_service

router = APIRouter(tags=["account"])


class AccountSummary(BaseModel):
    user_id: str
    email: str | None
    item_count: int
    item_limit: int
    calls_used_today: int
    daily_call_limit: int
    mcp_url: str


class AccountDeletion(BaseModel):
    deleted: dict[str, int]


@router.get("/profile", response_model=UserProfile, summary="Read the caller's profile")
async def get_profile(user: CurrentUser) -> UserProfile:
    return await profile_service.get_profile(user.user_id)


@router.put("/profile", response_model=UserProfile, summary="Update the caller's profile")
async def update_profile(patch: UserProfileUpdate, user: CurrentUser) -> UserProfile:
    return await profile_service.upsert_profile(user.user_id, patch)


@router.get(
    "/me",
    response_model=AccountSummary,
    summary="Account summary, including the connector URL and today's usage",
)
async def get_me(user: CurrentUser) -> AccountSummary:
    settings = get_settings()
    return AccountSummary(
        user_id=str(user.user_id),
        email=user.email,
        item_count=await items_service.count_items(user.user_id),
        item_limit=settings.max_items_per_user,
        calls_used_today=await usage_service.calls_used_today(user.user_id),
        daily_call_limit=settings.daily_mcp_call_limit,
        mcp_url=settings.mcp_url,
    )


@router.delete(
    "/account",
    response_model=AccountDeletion,
    summary="Delete the account and every row belonging to it",
)
async def delete_account(user: CurrentUser) -> AccountDeletion:
    return AccountDeletion(deleted=await profile_service.delete_account(user.user_id))
