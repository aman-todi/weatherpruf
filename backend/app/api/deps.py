"""Shared route dependencies.

``CurrentUser`` is the ``Annotated`` form of the auth dependency: it puts the
``Depends`` call in the type rather than in a default argument, which is the
current FastAPI idiom and keeps the signatures readable.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.auth import AuthenticatedUser, require_user
from app.services.admission import ensure_capacity


async def require_admitted_user(
    user: Annotated[AuthenticatedUser, Depends(require_user)],
) -> AuthenticatedUser:
    """``require_user`` plus the sign-up cap: a user beyond ``max_users`` gets a
    403 ``at_capacity`` on every route, which the web app renders as a message."""
    await ensure_capacity(user.user_id)
    return user


CurrentUser = Annotated[AuthenticatedUser, Depends(require_admitted_user)]
