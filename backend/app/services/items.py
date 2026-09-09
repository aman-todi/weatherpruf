"""Closet item CRUD, scoped by RLS to the calling user.

Every function here opens an ``rls_transaction`` so the database, not this
code, decides which rows the caller can touch. The 200-item cap and the
category-field validation are enforced here so both surfaces get them.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from app.config import get_settings
from app.db import rls_transaction
from app.errors import ClosetFullError, NotFoundError, ValidationError
from app.models import Item, ItemCreate, ItemUpdate
from app.services import taxonomy

_ITEM_COLUMNS = (
    "id, category_id as category, colors, brand, warmth_rating, formality, "
    "tags, notes, fields, created_at, updated_at"
)


def _to_item(row: asyncpg.Record) -> Item:
    return Item(
        id=row["id"],
        category=row["category"],
        colors=list(row["colors"] or []),
        brand=row["brand"],
        warmth_rating=row["warmth_rating"],
        formality=row["formality"],
        tags=list(row["tags"] or []),
        notes=row["notes"],
        fields=row["fields"] or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def count_items(user_id: UUID, conn: asyncpg.Connection | None = None) -> int:
    if conn is not None:
        return await conn.fetchval("select count(*) from public.items")
    async with rls_transaction(user_id) as connection:
        return await connection.fetchval("select count(*) from public.items")


async def _insert(
    conn: asyncpg.Connection, user_id: UUID, payload: ItemCreate, validated_fields: dict[str, Any]
) -> Item:
    row = await conn.fetchrow(
        f"""
        insert into public.items
            (user_id, category_id, colors, brand, warmth_rating, formality, tags, notes, fields)
        values ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        returning {_ITEM_COLUMNS}
        """,
        user_id,
        payload.category,
        payload.colors,
        payload.brand,
        payload.warmth_rating,
        payload.formality,
        payload.tags,
        payload.notes,
        validated_fields,
    )
    return _to_item(row)


async def create_item(user_id: UUID, payload: ItemCreate) -> Item:
    """Add one item, rejecting the write once the closet cap is reached."""
    settings = get_settings()
    validated_fields = await taxonomy.validate_fields(payload.category, payload.fields)

    async with rls_transaction(user_id) as conn:
        current = await count_items(user_id, conn)
        if current >= settings.max_items_per_user:
            raise ClosetFullError(
                f"Your closet is at its {settings.max_items_per_user}-item limit. "
                "Remove an item before adding another.",
                limit=settings.max_items_per_user,
                current_count=current,
            )
        return await _insert(conn, user_id, payload, validated_fields)


async def create_items(user_id: UUID, payloads: list[ItemCreate]) -> list[dict[str, Any]]:
    """Add several items, one result per input (spec §4.2).

    Deliberately partial-success: an item that fails validation or runs into
    the closet cap does not roll back the ones already created, and the caller
    is told exactly which succeeded and which did not, and why.
    """
    settings = get_settings()
    results: list[dict[str, Any]] = []

    async with rls_transaction(user_id) as conn:
        remaining = settings.max_items_per_user - await count_items(user_id, conn)

        for index, payload in enumerate(payloads):
            if remaining <= 0:
                results.append(
                    {
                        "index": index,
                        "status": "failed",
                        "error": "closet_full",
                        "message": (
                            f"Your closet reached its {settings.max_items_per_user}-item limit "
                            "partway through this batch; the earlier items were still added."
                        ),
                    }
                )
                continue

            try:
                validated_fields = await taxonomy.validate_fields(payload.category, payload.fields)
                # Each item gets its own savepoint so one bad row does not
                # poison the transaction the rest of the batch is running in.
                async with conn.transaction():
                    item = await _insert(conn, user_id, payload, validated_fields)
            except ValidationError as exc:
                results.append(
                    {
                        "index": index,
                        "status": "failed",
                        "error": exc.code,
                        "message": exc.message,
                        **({"details": exc.details} if exc.details else {}),
                    }
                )
                continue
            except asyncpg.PostgresError as exc:
                results.append(
                    {
                        "index": index,
                        "status": "failed",
                        "error": "database_rejected_item",
                        "message": str(exc).split("\n")[0],
                    }
                )
                continue

            remaining -= 1
            results.append(
                {"index": index, "status": "created", "item": item.model_dump(mode="json")}
            )

    return results


async def get_item(user_id: UUID, item_id: UUID) -> Item:
    async with rls_transaction(user_id) as conn:
        row = await conn.fetchrow(
            f"select {_ITEM_COLUMNS} from public.items where id = $1", item_id
        )
    if row is None:
        raise NotFoundError(f"No item with id {item_id} in your closet.", item_id=str(item_id))
    return _to_item(row)


async def list_items(
    user_id: UUID,
    *,
    category: str | None = None,
    tags: list[str] | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[Item]:
    """List the caller's items, optionally narrowed by category and tags.

    Tag matching is "has all of these tags" (``@>``), which is what a filter UI
    means by ticking two tags.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if category:
        params.append(category)
        clauses.append(f"category_id = ${len(params)}")
    if tags:
        params.append([tag.strip().lower() for tag in tags if tag.strip()])
        clauses.append(f"tags @> ${len(params)}")

    where = f"where {' and '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])

    async with rls_transaction(user_id) as conn:
        rows = await conn.fetch(
            f"""
            select {_ITEM_COLUMNS} from public.items
            {where}
            order by created_at desc, id
            limit ${len(params) - 1} offset ${len(params)}
            """,
            *params,
        )
    return [_to_item(row) for row in rows]


async def update_item(user_id: UUID, item_id: UUID, patch: ItemUpdate) -> Item:
    """Apply a partial update.

    Changing the category re-validates ``fields`` against the *new* template,
    since a field that was valid for a jacket may be meaningless on a hat.
    """
    supplied = patch.model_dump(exclude_unset=True)
    if not supplied:
        return await get_item(user_id, item_id)

    async with rls_transaction(user_id) as conn:
        existing = await conn.fetchrow(
            f"select {_ITEM_COLUMNS} from public.items where id = $1", item_id
        )
        if existing is None:
            raise NotFoundError(f"No item with id {item_id} in your closet.", item_id=str(item_id))

        category = supplied.get("category") or existing["category"]
        category_changed = "category" in supplied and supplied["category"] != existing["category"]

        if "fields" in supplied or category_changed:
            merged = supplied.get("fields")
            if merged is None:
                merged = dict(existing["fields"] or {})
            validated_fields = await taxonomy.validate_fields(
                category, merged, require_required=category_changed or "fields" in supplied
            )
            supplied["fields"] = validated_fields

        assignments: list[str] = []
        params: list[Any] = []
        column_for = {"category": "category_id"}
        for key, value in supplied.items():
            params.append(value)
            assignments.append(f"{column_for.get(key, key)} = ${len(params)}")

        params.append(item_id)
        row = await conn.fetchrow(
            f"""
            update public.items set {', '.join(assignments)}
            where id = ${len(params)}
            returning {_ITEM_COLUMNS}
            """,
            *params,
        )

    if row is None:
        raise NotFoundError(f"No item with id {item_id} in your closet.", item_id=str(item_id))
    return _to_item(row)


async def delete_item(user_id: UUID, item_id: UUID) -> None:
    async with rls_transaction(user_id) as conn:
        deleted = await conn.fetchval(
            "delete from public.items where id = $1 returning id", item_id
        )
    if deleted is None:
        raise NotFoundError(f"No item with id {item_id} in your closet.", item_id=str(item_id))
