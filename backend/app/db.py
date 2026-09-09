"""Database access.

Two connection pools, deliberately kept apart:

* ``app_pool`` — the application role. Used for every read and write on the
  user-scoped tables. Each unit of work runs inside a transaction that does
  ``set local role authenticated`` and pins the caller's user id into
  ``request.jwt.claim.sub``, so the RLS policies in db/migrations/0003 decide
  what the query can see. The app never filters by ``user_id`` in application
  code alone.

* ``readonly_pool`` — the ``wardrobe_readonly`` role from db/migrations/0004.
  SELECT on ``closet_query_view`` and nothing else, in read-only transactions.
  This is the only pool the assistant-supplied predicate in
  ``query_closet_items`` ever reaches, and it is the real security boundary
  behind that tool (spec §4.1).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg

from app.config import Settings, get_settings

_app_pool: asyncpg.Pool | None = None
_readonly_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Hand jsonb back as parsed Python rather than as a string."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def connect(settings: Settings | None = None) -> None:
    """Open both pools. Called once on application startup."""
    global _app_pool, _readonly_pool
    settings = settings or get_settings()

    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set")

    _app_pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        init=_init_connection,
    )

    # Optional so the REST API can still boot on a deployment that has not been
    # given the read-only credentials yet; query_closet_items raises if missing.
    if settings.readonly_database_url:
        _readonly_pool = await asyncpg.create_pool(
            settings.readonly_database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            init=_init_connection,
        )


async def disconnect() -> None:
    global _app_pool, _readonly_pool
    for pool in (_app_pool, _readonly_pool):
        if pool is not None:
            await pool.close()
    _app_pool = None
    _readonly_pool = None


def app_pool() -> asyncpg.Pool:
    if _app_pool is None:
        raise RuntimeError("database pool is not initialised; call db.connect() first")
    return _app_pool


def readonly_pool() -> asyncpg.Pool:
    if _readonly_pool is None:
        raise RuntimeError(
            "READONLY_DATABASE_URL is not configured; the safe query path is unavailable"
        )
    return _readonly_pool


@asynccontextmanager
async def rls_transaction(user_id: UUID | str) -> AsyncIterator[asyncpg.Connection]:
    """A transaction that sees exactly what ``user_id`` is allowed to see.

    Drops to the ``authenticated`` role and sets the JWT ``sub`` claim the RLS
    policies read, mirroring what PostgREST does for a request carrying that
    user's token. Both settings are transaction-local, so a connection returned
    to the pool carries nothing over to the next caller.
    """
    async with app_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", str(user_id))
            await conn.execute("set local role authenticated")
            yield conn


@asynccontextmanager
async def readonly_transaction(
    statement_timeout_ms: int | None = None,
) -> AsyncIterator[asyncpg.Connection]:
    """A read-only transaction over the restricted role.

    The role already carries ``default_transaction_read_only`` and a statement
    timeout as role-level settings; both are re-asserted here so the guarantee
    does not depend on the role's configuration having been applied.
    """
    settings = get_settings()
    timeout = statement_timeout_ms or settings.query_statement_timeout_ms
    async with readonly_pool().acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute(f"set local statement_timeout = {int(timeout)}")
            yield conn


async def healthcheck() -> dict[str, str]:
    """Report on both pools without failing the whole check if one is down."""
    status: dict[str, str] = {}
    for name, pool in (("app", _app_pool), ("readonly", _readonly_pool)):
        if pool is None:
            status[name] = "not_configured"
            continue
        try:
            async with pool.acquire() as conn:
                await conn.execute("select 1")
            status[name] = "ok"
        except Exception as exc:  # noqa: BLE001 — surfaced as status text, not raised
            status[name] = f"error: {type(exc).__name__}"
    return status
