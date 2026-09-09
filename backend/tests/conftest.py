"""Test fixtures.

The suite runs against a real local Postgres created by scripts/db_apply.sh,
not a mock: RLS, the check constraints and the read-only role's grants are the
things most worth testing, and none of them exist in a fake.
"""

from __future__ import annotations

import os
import uuid

import pytest_asyncio

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/wardrobe_dev")
os.environ.setdefault(
    "READONLY_DATABASE_URL",
    "postgresql://wardrobe_readonly:readonly-local-dev@127.0.0.1:5432/wardrobe_dev",
)
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-at-least-32-bytes-long!!")
os.environ.setdefault("SUPABASE_URL", "")

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.services import taxonomy  # noqa: E402


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _database():
    get_settings.cache_clear()
    await db.connect()
    taxonomy.invalidate_cache()
    try:
        yield
    finally:
        await db.disconnect()


@pytest_asyncio.fixture
async def user_id() -> uuid.UUID:
    """A freshly created auth user, torn down (with everything it owns) after."""
    new_id = uuid.uuid4()
    async with db.app_pool().acquire() as conn:
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            new_id,
            f"{new_id}@example.test",
        )
    try:
        yield new_id
    finally:
        async with db.app_pool().acquire() as conn:
            await conn.execute("delete from auth.users where id = $1", new_id)


@pytest_asyncio.fixture
async def other_user_id() -> uuid.UUID:
    new_id = uuid.uuid4()
    async with db.app_pool().acquire() as conn:
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            new_id,
            f"{new_id}@example.test",
        )
    try:
        yield new_id
    finally:
        async with db.app_pool().acquire() as conn:
            await conn.execute("delete from auth.users where id = $1", new_id)
