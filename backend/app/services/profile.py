"""User profile reads/writes and the full account-deletion cascade."""

from __future__ import annotations

import logging
from uuid import UUID

import httpx

from app.config import get_settings
from app.db import app_pool, rls_transaction
from app.models import UserProfile, UserProfileUpdate

logger = logging.getLogger(__name__)


async def get_profile(user_id: UUID) -> UserProfile:
    """A user with no profile row yet gets the defaults rather than a 404 —
    ``get_user_profile`` should never make the assistant handle an error just
    because the user has not visited Settings."""
    async with rls_transaction(user_id) as conn:
        row = await conn.fetchrow(
            "select home_location, unit_preference from public.user_profile where user_id = $1",
            user_id,
        )
    if row is None:
        return UserProfile()
    return UserProfile(home_location=row["home_location"], unit_preference=row["unit_preference"])


async def upsert_profile(user_id: UUID, patch: UserProfileUpdate) -> UserProfile:
    supplied = patch.model_dump(exclude_unset=True)

    async with rls_transaction(user_id) as conn:
        row = await conn.fetchrow(
            """
            insert into public.user_profile (user_id, home_location, unit_preference)
            values ($1, $2, coalesce($3, 'fahrenheit'))
            on conflict (user_id) do update
                set home_location   = coalesce($2, public.user_profile.home_location),
                    unit_preference = coalesce($3, public.user_profile.unit_preference)
            returning home_location, unit_preference
            """,
            user_id,
            supplied.get("home_location"),
            supplied.get("unit_preference"),
        )
    return UserProfile(home_location=row["home_location"], unit_preference=row["unit_preference"])


async def delete_account(user_id: UUID) -> dict[str, int]:
    """Delete every trace of a user (spec §5).

    Runs as the application role rather than through RLS, because it has to
    clear ``usage_counters`` too, which users cannot write to themselves. Every
    statement is still explicitly scoped by ``user_id``.

    Deliberately not exposed as an MCP tool — deleting an entire closet should
    not be one careless chat message away.
    """
    settings = get_settings()
    deleted: dict[str, int] = {}

    async with app_pool().acquire() as conn:
        async with conn.transaction():
            for table in ("items", "user_profile", "usage_counters"):
                result = await conn.execute(
                    f"delete from public.{table} where user_id = $1", user_id
                )
                deleted[table] = int(result.rsplit(" ", 1)[-1])

    deleted["auth_user"] = 1 if await _delete_auth_user(user_id, settings) else 0
    return deleted


async def _delete_auth_user(user_id: UUID, settings) -> bool:
    """Remove the Supabase auth user itself.

    Prefers the Admin API so Supabase can run its own cleanup (sessions,
    identities, refresh tokens). Falls back to a direct delete when the project
    is not configured — which is the case for the local dev shim.
    """
    if settings.supabase_url and settings.supabase_service_role_key:
        url = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}"
        headers = {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.delete(url, headers=headers)
            if response.status_code in (200, 204, 404):
                return response.status_code != 404
            logger.error(
                "Supabase admin user delete failed for %s: %s %s",
                user_id,
                response.status_code,
                response.text[:200],
            )
        except httpx.HTTPError:
            logger.exception("Supabase admin user delete errored for %s", user_id)

    async with app_pool().acquire() as conn:
        result = await conn.execute("delete from auth.users where id = $1", user_id)
    return int(result.rsplit(" ", 1)[-1]) > 0
