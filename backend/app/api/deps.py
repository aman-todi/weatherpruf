"""Shared route dependencies.

``CurrentUser`` is the ``Annotated`` form of the auth dependency: it puts the
``Depends`` call in the type rather than in a default argument, which is the
current FastAPI idiom and keeps the signatures readable.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.auth import AuthenticatedUser, require_user

CurrentUser = Annotated[AuthenticatedUser, Depends(require_user)]
