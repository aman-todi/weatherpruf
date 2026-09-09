"""Categories and their category-specific field templates.

Reference data: global, small, and changes only when someone adds a row to
``category_field_defs``. Cached in-process with a short TTL so the taxonomy can
be extended without a redeploy while still not costing a round trip per call.
"""

from __future__ import annotations

import time
from typing import Any

from app.db import app_pool
from app.errors import UnknownCategoryError, ValidationError
from app.models import Category, CategoryFieldDef

_CACHE_TTL_SECONDS = 60.0

_cache: list[Category] | None = None
_cache_loaded_at = 0.0

_TRUTHY = {"true", "t", "yes", "y", "1"}
_FALSY = {"false", "f", "no", "n", "0"}


def invalidate_cache() -> None:
    global _cache, _cache_loaded_at
    _cache = None
    _cache_loaded_at = 0.0


async def load_categories(*, refresh: bool = False) -> list[Category]:
    """Every category with its field template, ordered for display."""
    global _cache, _cache_loaded_at

    fresh = (time.monotonic() - _cache_loaded_at) < _CACHE_TTL_SECONDS
    if not refresh and _cache is not None and fresh:
        return _cache

    async with app_pool().acquire() as conn:
        category_rows = await conn.fetch(
            "select id, display_name, sort_order from public.categories order by sort_order, id"
        )
        field_rows = await conn.fetch(
            """
            select category_id, field_name, field_type, required, allowed_values, display_order
            from public.category_field_defs
            order by category_id, display_order, field_name
            """
        )

    by_category: dict[str, list[CategoryFieldDef]] = {}
    for row in field_rows:
        by_category.setdefault(row["category_id"], []).append(
            CategoryFieldDef(
                field_name=row["field_name"],
                field_type=row["field_type"],
                required=row["required"],
                allowed_values=list(row["allowed_values"]) if row["allowed_values"] else None,
                display_order=row["display_order"],
            )
        )

    categories = [
        Category(
            id=row["id"],
            display_name=row["display_name"],
            sort_order=row["sort_order"],
            fields=by_category.get(row["id"], []),
        )
        for row in category_rows
    ]

    _cache = categories
    _cache_loaded_at = time.monotonic()
    return categories


async def get_category(category_id: str) -> Category:
    categories = await load_categories()
    for category in categories:
        if category.id == category_id:
            return category

    # A category added since the cache was filled is worth one retry.
    for category in await load_categories(refresh=True):
        if category.id == category_id:
            return category

    known = ", ".join(c.id for c in await load_categories())
    raise UnknownCategoryError(
        f"'{category_id}' is not a known category. Valid categories: {known}",
        category=category_id,
    )


async def known_field_names() -> set[str]:
    """Every category-specific field name in use, for the query validator's
    ``fields->>'x'`` allowlist (spec §4.1 step 3)."""
    return {field.field_name for category in await load_categories() for field in category.fields}


def _coerce(field: CategoryFieldDef, value: Any) -> Any:
    name = field.field_name
    if field.field_type == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUTHY:
            return True
        if text in _FALSY:
            return False
        raise ValidationError(f"field '{name}' must be true or false, got {value!r}", field=name)

    if field.field_type == "number":
        if isinstance(value, bool):
            raise ValidationError(f"field '{name}' must be a number", field=name)
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"field '{name}' must be a number, got {value!r}", field=name
            ) from exc
        return int(number) if number.is_integer() else number

    text = str(value).strip()
    if field.field_type == "enum":
        allowed = field.allowed_values or []
        match = next((option for option in allowed if option.lower() == text.lower()), None)
        if match is None:
            raise ValidationError(
                f"field '{name}' must be one of: {', '.join(allowed)}. Got {value!r}",
                field=name,
                allowed_values=allowed,
            )
        return match
    return text


async def validate_fields(
    category_id: str,
    fields: dict[str, Any] | None,
    *,
    require_required: bool = True,
) -> dict[str, Any]:
    """Check ``fields`` against the category's template and normalise values.

    ``require_required`` is off for partial updates, where a required field the
    item already has must not be demanded again in the patch body.
    """
    category = await get_category(category_id)
    defs = {field.field_name: field for field in category.fields}
    supplied = {key: value for key, value in (fields or {}).items() if value is not None}

    unknown = sorted(set(supplied) - set(defs))
    if unknown:
        allowed = ", ".join(sorted(defs)) or "(none)"
        raise ValidationError(
            f"unknown field(s) for category '{category_id}': {', '.join(unknown)}. "
            f"Allowed fields: {allowed}",
            category=category_id,
            unknown_fields=unknown,
        )

    validated = {key: _coerce(defs[key], value) for key, value in supplied.items()}

    if require_required:
        missing = sorted(
            name for name, field in defs.items() if field.required and name not in validated
        )
        if missing:
            raise ValidationError(
                f"category '{category_id}' requires field(s): {', '.join(missing)}",
                category=category_id,
                missing_fields=missing,
            )
    return validated
