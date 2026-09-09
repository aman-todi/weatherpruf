"""The per-user daily MCP call cap (spec §5).

A call count, not a session count: one Claude.ai conversation can be a single
long-lived session covering a whole day, so sessions do not map onto a
meaningful quota. Checked and incremented atomically before any tool body runs.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from app.config import get_settings
from app.db import app_pool
from app.errors import UsageLimitExceededError


async def check_and_increment(user_id: UUID, *, limit: int | None = None) -> int:
    """Reserve one call from today's budget, or raise if it is spent.

    Runs as the application role: ``usage_counters`` intentionally has no
    insert/update policy for ``authenticated``, so a user cannot reset their own
    quota. The statement is scoped by ``user_id`` and is a single atomic upsert,
    so concurrent tool calls cannot both slip past the cap.

    Returns the number of calls used today, including this one.
    """
    settings = get_settings()
    limit = settings.daily_mcp_call_limit if limit is None else limit

    async with app_pool().acquire() as conn:
        used = await conn.fetchval(
            """
            insert into public.usage_counters (user_id, day, call_count)
            values ($1, (now() at time zone 'utc')::date, 1)
            on conflict (user_id, day) do update
                set call_count = public.usage_counters.call_count + 1
                where public.usage_counters.call_count < $2
            returning call_count
            """,
            user_id,
            limit,
        )
        if used is None:
            raise UsageLimitExceededError(
                f"Today's usage limit of {limit} assistant calls is reached. "
                "The limit resets at midnight UTC — please try again tomorrow.",
                limit=limit,
                used=limit,
                resets_at=f"{date.today() + timedelta(days=1)}T00:00:00Z",
            )
    return used


async def calls_used_today(user_id: UUID) -> int:
    async with app_pool().acquire() as conn:
        return (
            await conn.fetchval(
                """
                select call_count from public.usage_counters
                where user_id = $1 and day = (now() at time zone 'utc')::date
                """,
                user_id,
            )
            or 0
        )
