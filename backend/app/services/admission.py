"""Sign-up capacity: admit the first ``max_users`` users, block the rest.

Admission is by **sign-up order** (``auth.users.created_at`` rank), so the first
``max_users`` users are always admitted no matter what the current total is, and
a user admitted once stays admitted. That monotonicity is why admitted ids are
cached in-process — the check then costs a query only for a user who is not yet
known to be in.

Enforced at both surfaces: the REST API via ``require_admitted_user`` in
``app/api/deps.py`` (so the web app can show a message), and the MCP connector via
``SupabaseTokenVerifier`` (so a capped user gets no tools either).
"""

from __future__ import annotations

import logging
from uuid import UUID

from app.config import get_settings
from app.db import app_pool
from app.errors import AtCapacityError

logger = logging.getLogger(__name__)

# Ids known to be within the cap. Never grows past what the query admits, and is
# safe to lose on restart — it just re-warms.
_admitted: set[UUID] = set()

_MESSAGE = "weatherpruf isn't taking new sign-ups right now. Please check back in a few weeks."


async def ensure_capacity(user_id: UUID) -> None:
    """Raise :class:`AtCapacityError` if this user is beyond the sign-up cap."""
    if user_id in _admitted:
        return

    settings = get_settings()
    async with app_pool().acquire() as conn:
        # 1-based rank of this user among all users by sign-up time. ``None`` only
        # if the row is somehow absent for a verified token — fail open then,
        # rather than lock a real user out of their own closet.
        rank = await conn.fetchval(
            """
            select count(*)
            from auth.users
            where created_at <= (select created_at from auth.users where id = $1)
            """,
            user_id,
        )

    if rank is None or rank <= settings.max_users:
        _admitted.add(user_id)
        return

    logger.info(
        "blocked user %s: sign-up rank %s exceeds cap %s", user_id, rank, settings.max_users
    )
    raise AtCapacityError(_MESSAGE)
