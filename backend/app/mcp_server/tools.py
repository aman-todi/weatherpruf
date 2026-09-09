"""The eight MCP tools (spec §4).

Every tool here is a thin adapter. The rules — the 200-item cap, the five-colour
limit, category-field validation, batch partial success, the daily call cap —
live in ``app.services`` and ``app.safe_query``, so the MCP surface and the REST
API cannot drift apart. A tool body that finds itself writing SQL against a
user-scoped table is in the wrong place.

Two conventions run through the file:

* ``@closet_tool`` wraps every tool. It resolves the authenticated user, spends
  one call from the daily budget *before* the body runs (spec §5), and turns a
  domain error into a structured result.
* A domain error comes back as a normal result carrying an ``error`` code and a
  ``message``, not as a protocol-level exception. The assistant's job when the
  closet is full or the daily cap is reached is to say so in plain language, and
  it does that better with ``{"error": "daily_limit_reached", "message": ...,
  "details": {"resets_at": ...}}`` than with a flattened error string. Genuine
  faults — a dropped database connection, a bug — are left to propagate as tool
  errors, because those are not something to relay to the user.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Annotated, Any
from uuid import UUID

from fastmcp import FastMCP
from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

from app import safe_query
from app.config import get_settings
from app.errors import ValidationError, WardrobeError
from app.mcp_server import instructions
from app.mcp_server.auth import current_user_id
from app.models import Item, ItemCreate, ItemUpdate
from app.services import items as items_service
from app.services import profile as profile_service
from app.services import taxonomy
from app.services import usage

logger = logging.getLogger(__name__)

# Set on every wrapped tool so a test can assert no tool was registered without
# the auth-and-quota wrapper (see tests/test_mcp_tools.py).
CLOSET_TOOL_MARKER = "__closet_tool__"


def closet_tool(func):
    """Resolve the caller, spend one daily call, and normalise domain errors.

    The cap is checked and incremented before the body runs, per spec §5, so a
    tool that fails validation still costs a call — the point of the counter is
    to bound work done on a user's behalf, and a rejected call has still been
    served. The wrapped function receives ``user_id`` as its first argument.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        user_id = current_user_id()
        try:
            calls_used = await usage.check_and_increment(user_id)
        except WardrobeError as exc:
            logger.info("daily cap reached for user %s", user_id)
            return exc.to_dict()

        try:
            result = await func(user_id, *args, **kwargs)
        except WardrobeError as exc:
            logger.info("%s failed for user %s: %s", func.__name__, user_id, exc.message)
            return exc.to_dict()
        except PydanticValidationError as exc:
            # A model the assistant supplied did not fit its schema. Worth
            # returning rather than raising: the field-level detail is what
            # lets it correct the call instead of retrying the same thing.
            return ValidationError(
                _summarise_pydantic(exc), issues=_pydantic_issues(exc)
            ).to_dict()

        settings = get_settings()
        if isinstance(result, dict):
            result.setdefault(
                "usage",
                {
                    "calls_used_today": calls_used,
                    "daily_limit": settings.daily_mcp_call_limit,
                    "calls_remaining_today": max(
                        0, settings.daily_mcp_call_limit - calls_used
                    ),
                },
            )
        return result

    # `user_id` is resolved from the verified token, never sent by the client,
    # so it must not appear in the tool's input schema. functools.wraps sets
    # __wrapped__, which inspect.signature follows by default, so the parameter
    # has to be dropped explicitly — otherwise FastMCP would advertise it as an
    # argument and the assistant could try to pass one.
    signature = inspect.signature(func)
    parameters = list(signature.parameters.values())
    if not parameters or parameters[0].name != "user_id":
        raise TypeError(
            f"{func.__name__} must take user_id as its first parameter to be a closet tool"
        )
    wrapper.__signature__ = signature.replace(parameters=parameters[1:])
    wrapper.__annotations__ = {
        name: annotation
        for name, annotation in getattr(func, "__annotations__", {}).items()
        if name != "user_id"
    }

    setattr(wrapper, CLOSET_TOOL_MARKER, True)
    return wrapper


def _pydantic_issues(exc: PydanticValidationError) -> list[dict[str, str]]:
    return [
        {"field": ".".join(str(part) for part in error["loc"]), "problem": error["msg"]}
        for error in exc.errors()
    ]


def _summarise_pydantic(exc: PydanticValidationError) -> str:
    issues = _pydantic_issues(exc)
    detail = "; ".join(f"{issue['field']}: {issue['problem']}" for issue in issues[:5])
    return f"The arguments were not valid: {detail}"


def _item_dict(item: Item) -> dict[str, Any]:
    return item.model_dump(mode="json")


# --- shared argument annotations -------------------------------------------
# Repeated across add_item / update_item / batch_add_items so the three take an
# identical shape, as spec §4 requires.

_CategoryArg = Annotated[
    str, Field(description="A category id from get_closet_structure, e.g. 'tshirt'.")
]
_ColorsArg = Annotated[
    list[str] | None,
    Field(description="Up to 5 colour names, lower case, e.g. ['navy', 'white']."),
]
_BrandArg = Annotated[str | None, Field(description="Brand name, if the user gave one.")]
_WarmthArg = Annotated[
    int | None,
    Field(description="How warm the garment is: 1 = very light, 5 = heaviest.", ge=1, le=5),
]
_FormalityArg = Annotated[
    str | None,
    Field(description="One of: casual, smart_casual, formal, athletic."),
]
_TagsArg = Annotated[
    list[str] | None,
    Field(description="Free-form labels, e.g. ['date-night', 'floral']."),
]
_NotesArg = Annotated[
    str | None,
    Field(description="Anything about the item that does not fit a structured field."),
]
_FieldsArg = Annotated[
    dict[str, Any] | None,
    Field(
        description=(
            "Category-specific fields for this category, from get_closet_structure — "
            "e.g. {'shoe_type': 'boot'} for shoes."
        )
    ),
]
_ItemIdArg = Annotated[
    str, Field(description="The item's id, as returned by any of the read tools.")
]


def _parse_item_id(item_id: str) -> UUID:
    try:
        return UUID(str(item_id).strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"'{item_id}' is not a valid item id. Item ids come back from "
            "query_closet_items, list_category_items, add_item or batch_add_items — "
            "read one rather than constructing it.",
            item_id=str(item_id),
        ) from exc


def register_tools(mcp: FastMCP) -> None:
    """Register all eight tools on the server (spec §4)."""

    # -- structure ----------------------------------------------------------

    @mcp.tool(
        name="get_closet_structure",
        description=instructions.GET_CLOSET_STRUCTURE_DESCRIPTION,
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    @closet_tool
    async def get_closet_structure(user_id: UUID) -> dict[str, Any]:
        categories = await taxonomy.load_categories()
        settings = get_settings()
        return {
            "categories": [category.model_dump(mode="json") for category in categories],
            "common_fields": _COMMON_FIELDS,
            # Spec §4/§1: the query grammar is handed to the assistant inside
            # the tool response, which is what it actually consults.
            "query_instructions": instructions.QUERY_INSTRUCTIONS,
            "limits": {
                "max_items_per_closet": settings.max_items_per_user,
                "max_colors_per_item": settings.max_colors_per_item,
                "max_items_per_batch_add": settings.max_batch_items,
                "max_rows_from_query_closet_items": settings.query_result_limit,
                "max_rows_from_list_category_items": settings.category_listing_limit,
                "daily_tool_calls_per_user": settings.daily_mcp_call_limit,
            },
        }

    # -- writes -------------------------------------------------------------

    @mcp.tool(
        name="add_item",
        description=instructions.ADD_ITEM_DESCRIPTION,
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    @closet_tool
    async def add_item(
        user_id: UUID,
        category: _CategoryArg,
        colors: _ColorsArg = None,
        brand: _BrandArg = None,
        warmth_rating: _WarmthArg = None,
        formality: _FormalityArg = None,
        tags: _TagsArg = None,
        notes: _NotesArg = None,
        fields: _FieldsArg = None,
    ) -> dict[str, Any]:
        payload = ItemCreate(
            category=category,
            colors=colors or [],
            brand=brand,
            warmth_rating=warmth_rating,
            formality=formality,
            tags=tags or [],
            notes=notes,
            fields=fields or {},
        )
        item = await items_service.create_item(user_id, payload)
        return {"status": "created", "item": _item_dict(item)}

    @mcp.tool(
        name="batch_add_items",
        description=instructions.BATCH_ADD_ITEMS_DESCRIPTION,
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    @closet_tool
    async def batch_add_items(
        user_id: UUID,
        items: Annotated[
            list[dict[str, Any]],
            Field(
                description=(
                    "One object per garment, each in the same shape add_item takes: "
                    "category (required) plus any of colors, brand, warmth_rating, "
                    "formality, tags, notes, fields."
                )
            ),
        ],
    ) -> dict[str, Any]:
        settings = get_settings()

        if not items:
            raise ValidationError("No items were supplied.")
        if len(items) > settings.max_batch_items:
            raise ValidationError(
                f"{len(items)} items were supplied; at most {settings.max_batch_items} "
                "can be added in one call. Split them across two calls.",
                supplied=len(items),
                limit=settings.max_batch_items,
            )

        # An item whose shape is wrong never reaches the service. It becomes a
        # per-item failure so one malformed entry does not cost the caller the
        # whole batch — the same partial-success contract spec §4.2 describes
        # for validation and cap failures.
        payloads: dict[int, ItemCreate] = {}
        results: list[dict[str, Any]] = []
        for index, raw in enumerate(items):
            if not isinstance(raw, dict):
                results.append(
                    {
                        "index": index,
                        "status": "failed",
                        "error": "validation_failed",
                        "message": "Each item must be an object, not a bare value.",
                    }
                )
                continue
            try:
                payloads[index] = ItemCreate(**raw)
            except PydanticValidationError as exc:
                results.append(
                    {
                        "index": index,
                        "status": "failed",
                        "error": "validation_failed",
                        "message": _summarise_pydantic(exc),
                        "details": {"issues": _pydantic_issues(exc)},
                    }
                )
            except TypeError as exc:
                results.append(
                    {
                        "index": index,
                        "status": "failed",
                        "error": "validation_failed",
                        "message": f"The item could not be read: {exc}",
                    }
                )

        ordered_indexes = sorted(payloads)
        service_results = await items_service.create_items(
            user_id, [payloads[index] for index in ordered_indexes]
        )
        # create_items indexes its results by position within the list it was
        # given; map them back onto the caller's original positions.
        for position, result in zip(ordered_indexes, service_results, strict=True):
            results.append({**result, "index": position})

        results.sort(key=lambda entry: entry["index"])
        created = sum(1 for entry in results if entry["status"] == "created")
        return {
            "results": results,
            "created_count": created,
            "failed_count": len(results) - created,
            "total_submitted": len(items),
        }

    @mcp.tool(
        name="update_item",
        description=instructions.UPDATE_ITEM_DESCRIPTION,
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    @closet_tool
    async def update_item(
        user_id: UUID,
        item_id: _ItemIdArg,
        category: Annotated[str | None, Field(description="Move the item to another category.")] = None,
        colors: _ColorsArg = None,
        brand: _BrandArg = None,
        warmth_rating: _WarmthArg = None,
        formality: _FormalityArg = None,
        tags: _TagsArg = None,
        notes: _NotesArg = None,
        fields: _FieldsArg = None,
    ) -> dict[str, Any]:
        parsed_id = _parse_item_id(item_id)

        # Only what the assistant actually named is sent on, so an omitted
        # argument leaves the stored value alone instead of nulling it.
        supplied = {
            key: value
            for key, value in {
                "category": category,
                "colors": colors,
                "brand": brand,
                "warmth_rating": warmth_rating,
                "formality": formality,
                "tags": tags,
                "notes": notes,
                "fields": fields,
            }.items()
            if value is not None
        }
        if not supplied:
            raise ValidationError(
                "No changes were supplied. Pass at least one field to update."
            )

        item = await items_service.update_item(user_id, parsed_id, ItemUpdate(**supplied))
        return {"status": "updated", "item": _item_dict(item)}

    @mcp.tool(
        name="remove_item",
        description=instructions.REMOVE_ITEM_DESCRIPTION,
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @closet_tool
    async def remove_item(user_id: UUID, item_id: _ItemIdArg) -> dict[str, Any]:
        parsed_id = _parse_item_id(item_id)
        await items_service.delete_item(user_id, parsed_id)
        return {
            "status": "removed",
            "item_id": str(parsed_id),
            "message": "The item was permanently deleted from the closet.",
        }

    # -- reads --------------------------------------------------------------

    @mcp.tool(
        name="query_closet_items",
        description=instructions.QUERY_CLOSET_ITEMS_DESCRIPTION,
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    @closet_tool
    async def query_closet_items(
        user_id: UUID,
        where_clause: Annotated[
            str,
            Field(
                description=(
                    "A SQL boolean predicate over category, colors, brand, warmth_rating, "
                    "formality, tags, notes and fields — a bare WHERE clause with no SELECT, "
                    "no FROM and no semicolon. See query_instructions from "
                    "get_closet_structure. Example: "
                    "category IN ('jacket','coat') AND warmth_rating >= 4"
                )
            ),
        ],
    ) -> dict[str, Any]:
        return await safe_query.run_where_clause(user_id, where_clause)

    @mcp.tool(
        name="list_category_items",
        description=instructions.LIST_CATEGORY_ITEMS_DESCRIPTION,
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    @closet_tool
    async def list_category_items(user_id: UUID, category: _CategoryArg) -> dict[str, Any]:
        normalised = str(category).strip().lower()
        # Validates the id against the taxonomy so an invented category comes
        # back as "not a known category, here are the real ones" rather than as
        # an empty list the assistant would read as an empty closet.
        await taxonomy.get_category(normalised)
        return await safe_query.list_category(user_id, normalised)

    @mcp.tool(
        name="get_closet_summary",
        description=instructions.GET_CLOSET_SUMMARY_DESCRIPTION,
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    @closet_tool
    async def get_closet_summary(user_id: UUID) -> dict[str, Any]:
        return await safe_query.closet_summary(user_id)

    @mcp.tool(
        name="get_user_profile",
        description=instructions.GET_USER_PROFILE_DESCRIPTION,
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )
    @closet_tool
    async def get_user_profile(user_id: UUID) -> dict[str, Any]:
        user_profile = await profile_service.get_profile(user_id)
        return {
            "home_location": user_profile.home_location,
            "unit_preference": user_profile.unit_preference,
            "note": (
                "Weather is not available from this server — find it yourself for this "
                "location, or ask the user if home_location is null."
            ),
        }


# The six common fields, described for the assistant rather than for a form.
# Deliberately mirrors the column list in ``safe_query.ALLOWED_COLUMNS``.
_COMMON_FIELDS = [
    {
        "name": "colors",
        "type": "text[]",
        "description": "Up to 5 lower-case colour names. Query with 'navy' = ANY(colors).",
    },
    {
        "name": "brand",
        "type": "text",
        "description": "Free text, often null.",
    },
    {
        "name": "warmth_rating",
        "type": "int",
        "description": "1 = very light, 5 = heaviest. Often null. The main lever for weather.",
    },
    {
        "name": "formality",
        "type": "text",
        "description": "One of: casual, smart_casual, formal, athletic. Often null.",
    },
    {
        "name": "tags",
        "type": "text[]",
        "description": (
            "Free-form lower-case labels the user chose, e.g. 'date-night', 'floral'. "
            "Query with tags && ARRAY['a'] (any) or tags @> ARRAY['a'] (all)."
        ),
    },
    {
        "name": "notes",
        "type": "text",
        "description": (
            "Free text about the item. Search it with ILIKE, and read it before "
            "recommending — it is where the differentiating detail lives."
        ),
    },
]
