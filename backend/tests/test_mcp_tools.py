"""The MCP tool surface (spec §4).

These run the tools through FastMCP's own in-memory client, so the tool
registration, the generated input schemas, the auth wiring and the result
serialisation are all exercised — not just the Python functions underneath.
The end-to-end pass over a real HTTP server lives in
``scripts/mcp_test_client.py``; this is the part that belongs in CI.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app import db
from app.config import get_settings
from app.mcp_server import build_server
from app.mcp_server.tools import CLOSET_TOOL_MARKER
from app.models import ItemCreate
from app.services import items as items_service

pytest.importorskip("fastmcp", reason="the MCP surface needs the 'mcp' extra")

from fastmcp import Client  # noqa: E402
from fastmcp.server.auth import AccessToken  # noqa: E402

EXPECTED_TOOLS = {
    "get_closet_structure",
    "add_item",
    "update_item",
    "remove_item",
    "batch_add_items",
    "query_closet_items",
    "list_category_items",
    "get_closet_summary",
    "get_user_profile",
}


@pytest.fixture
def server():
    return build_server()


@pytest.fixture
async def call(server, user_id: uuid.UUID, monkeypatch):
    """Call tools as ``user_id``, through FastMCP's in-memory transport.

    Auth is stubbed at exactly one seam — the access token FastMCP has already
    verified — rather than by handing the tools a user id directly, so the
    tools still read their caller the way they do in production. Token
    *verification* itself is covered by tests/test_auth.py and by the HTTP
    round trip in scripts/mcp_test_client.py.
    """
    token = AccessToken(
        token="test",
        client_id=str(user_id),
        subject=str(user_id),
        scopes=[],
        claims={"sub": str(user_id)},
    )
    monkeypatch.setattr("app.mcp_server.auth.get_access_token", lambda: token)

    async def _call(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        async with Client(server) as client:
            result = await client.call_tool(name, arguments or {})
        return _payload(result)

    return _call


def _payload(result: Any) -> dict[str, Any]:
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for block in getattr(result, "content", None) or []:
        if getattr(block, "text", None):
            return json.loads(block.text)
    return {}


@pytest.fixture
async def reset_quota(user_id: uuid.UUID):
    """Clear the daily counter before a test.

    A test that makes several calls would otherwise run into the 10-call cap,
    which has its own tests below.
    """

    async def _reset(target: uuid.UUID | None = None) -> None:
        async with db.app_pool().acquire() as conn:
            await conn.execute(
                "delete from public.usage_counters where user_id = $1",
                target or user_id,
            )

    await _reset()
    return _reset


# --- registration and schemas -----------------------------------------------


async def test_every_tool_in_the_spec_is_registered(server) -> None:
    async with Client(server) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert names == EXPECTED_TOOLS


async def test_no_tool_exposes_user_id(server) -> None:
    """The caller comes from the verified token. If it ever appeared as an
    argument, an assistant could ask for someone else's closet."""
    async with Client(server) as client:
        for tool in await client.list_tools():
            assert "user_id" not in (tool.input_schema or {}).get("properties", {}), tool.name


async def test_every_tool_goes_through_the_quota_wrapper(server) -> None:
    """Spec §5 says the cap is checked before *any* tool body runs, so this
    asserts the wrapper is present rather than trusting nine call sites."""
    for name in EXPECTED_TOOLS:
        tool = await server.get_tool(name)
        assert tool is not None, f"{name} is not registered"
        assert getattr(tool.fn, CLOSET_TOOL_MARKER, False), f"{name} is not wrapped"


async def test_tools_carry_their_descriptions(server) -> None:
    """Spec §1: the descriptions are what steer the model on plain Claude.ai,
    so an empty one is a bug, not a cosmetic gap."""
    async with Client(server) as client:
        for tool in await client.list_tools():
            assert tool.description and len(tool.description) > 80, tool.name


async def test_batch_add_items_description_is_the_one_from_the_spec(server) -> None:
    async with Client(server) as client:
        tool = next(t for t in await client.list_tools() if t.name == "batch_add_items")
    description = tool.description
    # The four instructions spec §4.2 drafts.
    assert "get_closet_structure" in description
    assert "one structured item object per garment" in description
    assert "ASK THE USER" in description
    assert "20 items per call" in description


# --- OAuth discovery (spec §4: "authenticated via Supabase OAuth 2.1") ------


async def test_oauth_discovery_is_reachable_where_it_is_advertised(monkeypatch) -> None:
    """RFC 9728 puts the discovery document at the origin root, but the MCP
    sub-app is mounted under /mcp and cannot serve a root path itself.

    If these two drift apart the 401 challenge advertises a URL that 404s, and
    a remote connector never starts its OAuth flow — a break that is invisible
    until someone tries to connect Claude.ai. So this asserts the document is
    actually served at the path the challenge names.
    """
    from app.main import create_app
    from app.mcp_server import build_mcp_app

    settings = get_settings()
    monkeypatch.setattr(settings, "supabase_url", "https://example-project.supabase.co")
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000")

    mcp_app = build_mcp_app()
    assert mcp_app is not None
    advertised = [route.path for route in mcp_app.root_oauth_routes]
    assert advertised, "no discovery routes were exposed for the parent to publish"
    assert any(path.startswith("/.well-known/oauth-protected-resource") for path in advertised), (
        advertised
    )

    # ...and the parent app actually publishes them at the root.
    published = {getattr(route, "path", None) for route in create_app().routes}
    for path in advertised:
        assert path in published, f"{path} is advertised but not served at the root"


async def test_no_authorization_server_is_advertised_without_supabase(monkeypatch) -> None:
    """Local development has no authorization server to point at, and claiming
    one that does not exist would be worse than claiming none."""
    from app.mcp_server.auth import SupabaseTokenVerifier, build_auth_provider

    settings = get_settings()
    monkeypatch.setattr(settings, "supabase_url", "")
    provider = build_auth_provider(settings)
    assert isinstance(provider, SupabaseTokenVerifier)


# --- get_closet_structure ---------------------------------------------------


async def test_get_closet_structure(call, reset_quota) -> None:
    structure = await call("get_closet_structure")
    categories = structure["categories"]

    assert len(categories) >= 20
    assert {"tshirt", "jeans", "shoes"} <= {category["id"] for category in categories}

    shoes = next(category for category in categories if category["id"] == "shoes")
    assert any(field["field_name"] == "shoe_type" for field in shoes["fields"])

    assert len(structure["common_fields"]) == 6
    assert structure["limits"]["max_items_per_closet"] == get_settings().max_items_per_user


async def test_query_instructions_document_the_grammar(call, reset_quota) -> None:
    """Spec §4/§1: this text is how the assistant learns the query syntax, so
    it has to actually cover the grammar the validator accepts."""
    instructions = (await call("get_closet_structure"))["query_instructions"]

    for column in ("category", "colors", "brand", "warmth_rating", "formality", "tags", "notes"):
        assert column in instructions
    for operator in ("BETWEEN", "IS NULL", "ILIKE", "&&", "@>", "ANY(", "IN ("):
        assert operator in instructions
    assert "fields->>" in instructions
    assert "EXAMPLES" in instructions
    # It must say what is refused, not only what is allowed.
    assert "subqueries" in instructions and "semicolons" in instructions


async def test_the_documented_examples_actually_validate(call, reset_quota) -> None:
    """Every example predicate in query_instructions must survive the
    validator — a documented example that gets rejected would teach the model
    the wrong syntax."""
    from app.safe_query import validate_where_clause
    from app.services import taxonomy

    instructions = (await call("get_closet_structure"))["query_instructions"]
    known = await taxonomy.known_field_names()

    body = instructions.split("EXAMPLES", 1)[1].split("PRACTICAL NOTES", 1)[0]
    examples = [line.strip() for line in body.splitlines() if line.strip()]
    assert len(examples) >= 5

    for example in examples:
        assert validate_where_clause(example, known), example


# --- add_item ---------------------------------------------------------------


async def test_add_item(call, reset_quota, user_id: uuid.UUID) -> None:
    result = await call(
        "add_item",
        {
            "category": "jacket",
            "colors": ["Navy", "navy", "white"],
            "brand": " Patagonia ",
            "warmth_rating": 4,
            "formality": "casual",
            "tags": ["Rain"],
            "notes": "Packs down small.",
        },
    )
    assert result["status"] == "created"
    item = result["item"]
    assert item["colors"] == ["navy", "white"]  # de-duplicated and lower-cased
    assert item["brand"] == "Patagonia"
    assert item["tags"] == ["rain"]
    assert uuid.UUID(item["id"])


async def test_add_item_reports_the_remaining_budget(call, reset_quota) -> None:
    result = await call("add_item", {"category": "hat"})
    settings = get_settings()
    assert result["usage"]["calls_used_today"] == 1
    assert result["usage"]["daily_limit"] == settings.daily_mcp_call_limit
    assert result["usage"]["calls_remaining_today"] == settings.daily_mcp_call_limit - 1


async def test_add_item_rejects_an_unknown_category(call, reset_quota) -> None:
    result = await call("add_item", {"category": "space_suit"})
    assert result["error"] == "unknown_category"
    # The message lists the real ones so the assistant can correct itself.
    assert "tshirt" in result["message"]


async def test_add_item_rejects_a_sixth_colour(call, reset_quota) -> None:
    result = await call(
        "add_item", {"category": "tshirt", "colors": ["a", "b", "c", "d", "e", "f"]}
    )
    assert result["error"] == "validation_failed"


async def test_add_item_requires_a_categorys_required_fields(call, reset_quota) -> None:
    result = await call("add_item", {"category": "shoes"})
    assert result["error"] == "validation_failed"
    assert "shoe_type" in result["message"]


async def test_add_item_enforces_the_closet_cap(call, reset_quota, user_id, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "max_items_per_user", 2)
    try:
        await items_service.create_item(user_id, ItemCreate(category="hat"))
        await items_service.create_item(user_id, ItemCreate(category="hat"))
        result = await call("add_item", {"category": "hat"})
        assert result["error"] == "closet_full"
        assert "2-item limit" in result["message"]
    finally:
        monkeypatch.undo()


# --- batch_add_items --------------------------------------------------------


async def test_batch_add_items_partial_success(call, reset_quota) -> None:
    """Spec §4.2: one bad item must not cost the caller the good ones, and the
    result has to say which is which."""
    result = await call(
        "batch_add_items",
        {
            "items": [
                {"category": "tshirt", "colors": ["red"], "brand": "Nike"},
                {"category": "space_suit"},
                {"category": "tshirt", "fields": {"heel_height": "tall"}},
                {"category": "coat", "warmth_rating": 99},
                {"category": "polo", "colors": ["blue"]},
            ]
        },
    )

    assert result["created_count"] == 2
    assert result["failed_count"] == 3
    assert result["total_submitted"] == 5

    statuses = [entry["status"] for entry in result["results"]]
    assert statuses == ["created", "failed", "failed", "failed", "created"]
    assert [entry["index"] for entry in result["results"]] == [0, 1, 2, 3, 4]

    assert result["results"][1]["error"] == "unknown_category"
    assert "heel_height" in result["results"][2]["message"]
    # The last item is valid and comes after three failures — the point of the
    # partial-success contract.
    assert result["results"][4]["item"]["category"] == "polo"

    for entry in result["results"]:
        if entry["status"] == "failed":
            assert entry["message"]


async def test_batch_add_items_caps_the_batch_size(call, reset_quota) -> None:
    result = await call("batch_add_items", {"items": [{"category": "hat"}] * 21})
    assert result["error"] == "validation_failed"
    assert "20" in result["message"]


async def test_batch_add_items_rejects_an_empty_list(call, reset_quota) -> None:
    assert (await call("batch_add_items", {"items": []}))["error"] == "validation_failed"


async def test_batch_add_items_handles_a_bare_value_in_the_list(call, reset_quota) -> None:
    result = await call("batch_add_items", {"items": [{"category": "hat"}, "a red shirt"]})
    assert result["created_count"] == 1
    assert result["results"][1]["status"] == "failed"


async def test_batch_add_items_when_the_closet_cap_lands_mid_batch(
    call, reset_quota, user_id, monkeypatch
) -> None:
    """Spec §4.2 names this case specifically: items before the cap are kept."""
    monkeypatch.setattr(get_settings(), "max_items_per_user", 2)
    try:
        result = await call(
            "batch_add_items",
            {"items": [{"category": "hat"}, {"category": "hat"}, {"category": "hat"}]},
        )
        assert result["created_count"] == 2
        assert result["results"][2]["error"] == "closet_full"
        assert "earlier items were still added" in result["results"][2]["message"]
    finally:
        monkeypatch.undo()


# --- reads ------------------------------------------------------------------


@pytest.fixture
async def stocked(user_id: uuid.UUID):
    for payload in [
        ItemCreate(
            category="jacket",
            colors=["navy"],
            brand="Patagonia",
            warmth_rating=4,
            tags=["rain"],
            notes="Good in a downpour.",
        ),
        ItemCreate(category="tshirt", colors=["red"], brand="Nike", warmth_rating=1),
        ItemCreate(category="shoes", fields={"shoe_type": "boot"}, tags=["date-night"]),
    ]:
        await items_service.create_item(user_id, payload)


async def test_query_closet_items(call, reset_quota, stocked) -> None:
    result = await call("query_closet_items", {"where_clause": "warmth_rating >= 4"})
    assert result["count"] == 1
    assert result["items"][0]["brand"] == "Patagonia"
    assert result["where_clause_executed"] == "warmth_rating >= 4"


async def test_query_closet_items_rejects_an_unsafe_clause(call, reset_quota, stocked) -> None:
    result = await call(
        "query_closet_items", {"where_clause": "category = 'hat'; DROP TABLE items"}
    )
    assert result["error"] == "unsafe_query"
    assert "items" not in result  # no rows, and nothing that looks like rows
    assert "semicolon" in result["message"]
    # The message points at where the grammar is documented.
    assert "query_instructions" in result["message"]


async def test_query_closet_items_does_not_see_another_user(
    call, reset_quota, stocked, other_user_id: uuid.UUID
) -> None:
    await items_service.create_item(
        other_user_id, ItemCreate(category="hat", brand="SomeoneElsesHat")
    )
    result = await call("query_closet_items", {"where_clause": "true"})
    assert result["count"] == 3
    assert "SomeoneElsesHat" not in {item["brand"] for item in result["items"]}


async def test_list_category_items(call, reset_quota, stocked) -> None:
    result = await call("list_category_items", {"category": "Shoes"})
    assert result["category"] == "shoes"  # normalised
    assert result["count"] == 1
    assert result["items"][0]["fields"] == {"shoe_type": "boot"}


async def test_list_category_items_rejects_an_unknown_category(call, reset_quota) -> None:
    """An empty list would read to the assistant as "you own no shoes"; an
    error tells it the category id was wrong."""
    result = await call("list_category_items", {"category": "space_suit"})
    assert result["error"] == "unknown_category"


async def test_get_closet_summary(call, reset_quota, stocked) -> None:
    summary = await call("get_closet_summary")
    assert summary["total_items"] == 3
    assert summary["items_per_category"] == {"jacket": 1, "shoes": 1, "tshirt": 1}
    assert summary["brands"] == ["Nike", "Patagonia"]
    assert summary["tags"] == ["date-night", "rain"]
    assert summary["warmth_distribution"] == {"1": 1, "4": 1, "unspecified": 1}


async def test_get_user_profile(call, reset_quota) -> None:
    profile = await call("get_user_profile")
    assert profile["unit_preference"] == "fahrenheit"
    assert profile["home_location"] is None
    # Spec §0: the server never returns weather, and says so.
    assert "weather" in profile["note"].lower()


# --- update / remove --------------------------------------------------------


async def test_update_item_only_touches_what_it_is_given(call, reset_quota, user_id) -> None:
    item = await items_service.create_item(
        user_id,
        ItemCreate(category="jacket", colors=["navy"], warmth_rating=4, tags=["rain"]),
    )
    result = await call("update_item", {"item_id": str(item.id), "brand": "Arc'teryx"})

    assert result["status"] == "updated"
    assert result["item"]["brand"] == "Arc'teryx"
    assert result["item"]["warmth_rating"] == 4
    assert result["item"]["colors"] == ["navy"]
    assert result["item"]["tags"] == ["rain"]


async def test_update_item_needs_at_least_one_change(call, reset_quota, user_id) -> None:
    item = await items_service.create_item(user_id, ItemCreate(category="hat"))
    result = await call("update_item", {"item_id": str(item.id)})
    assert result["error"] == "validation_failed"


async def test_update_item_rejects_a_malformed_id(call, reset_quota) -> None:
    result = await call("update_item", {"item_id": "not-a-uuid", "brand": "x"})
    assert result["error"] == "validation_failed"
    assert "query_closet_items" in result["message"]


async def test_update_item_cannot_reach_another_users_item(
    call, reset_quota, other_user_id: uuid.UUID
) -> None:
    theirs = await items_service.create_item(other_user_id, ItemCreate(category="hat"))
    result = await call("update_item", {"item_id": str(theirs.id), "brand": "Mine now"})
    assert result["error"] == "not_found"


async def test_remove_item(call, reset_quota, user_id) -> None:
    item = await items_service.create_item(user_id, ItemCreate(category="hat"))

    result = await call("remove_item", {"item_id": str(item.id)})
    assert result["status"] == "removed"

    again = await call("remove_item", {"item_id": str(item.id)})
    assert again["error"] == "not_found"


async def test_remove_item_cannot_reach_another_users_item(
    call, reset_quota, other_user_id: uuid.UUID
) -> None:
    theirs = await items_service.create_item(other_user_id, ItemCreate(category="hat"))
    result = await call("remove_item", {"item_id": str(theirs.id)})
    assert result["error"] == "not_found"

    surviving = await items_service.get_item(other_user_id, theirs.id)
    assert surviving.id == theirs.id


# --- the daily call cap (spec §5) -------------------------------------------


async def test_the_daily_cap_triggers(call, reset_quota, user_id, monkeypatch) -> None:
    """Lowered to 3 for the test, exactly as the ticket's DoD asks."""
    monkeypatch.setattr(get_settings(), "daily_mcp_call_limit", 3)
    try:
        for expected_remaining in (2, 1, 0):
            result = await call("get_user_profile")
            assert "error" not in result
            assert result["usage"]["calls_remaining_today"] == expected_remaining

        refused = await call("get_user_profile")
        assert refused["error"] == "daily_limit_reached"
        assert "3 assistant calls" in refused["message"]
        assert "tomorrow" in refused["message"]
        assert refused["details"]["limit"] == 3
        assert "resets_at" in refused["details"]
    finally:
        monkeypatch.undo()


async def test_the_cap_stops_the_tool_body_running(call, reset_quota, user_id, monkeypatch) -> None:
    """Spec §5 says the check runs *before* the tool body. A write refused by
    the cap must not have written anything."""
    monkeypatch.setattr(get_settings(), "daily_mcp_call_limit", 1)
    try:
        assert (await call("add_item", {"category": "hat"}))["status"] == "created"

        refused = await call("add_item", {"category": "jacket"})
        assert refused["error"] == "daily_limit_reached"

        assert await items_service.count_items(user_id) == 1
    finally:
        monkeypatch.undo()


async def test_the_cap_applies_to_every_tool(call, reset_quota, user_id, monkeypatch) -> None:
    from app.services import usage

    monkeypatch.setattr(get_settings(), "daily_mcp_call_limit", 1)
    try:
        # Spend the single call outside the tool surface, so every call below
        # is over the cap.
        await usage.check_and_increment(user_id)

        for name, arguments in [
            ("get_closet_structure", {}),
            ("get_user_profile", {}),
            ("get_closet_summary", {}),
            ("add_item", {"category": "hat"}),
            ("batch_add_items", {"items": [{"category": "hat"}]}),
            ("update_item", {"item_id": str(uuid.uuid4()), "brand": "x"}),
            ("remove_item", {"item_id": str(uuid.uuid4())}),
            ("query_closet_items", {"where_clause": "true"}),
            ("list_category_items", {"category": "hat"}),
        ]:
            result = await call(name, arguments)
            assert result["error"] == "daily_limit_reached", name
    finally:
        monkeypatch.undo()


async def test_the_cap_is_per_user(call, reset_quota, user_id, other_user_id, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "daily_mcp_call_limit", 1)
    try:
        await call("get_user_profile")
        assert (await call("get_user_profile"))["error"] == "daily_limit_reached"

        # The other user's budget is untouched.
        from app.services import usage

        assert await usage.check_and_increment(other_user_id) == 1
    finally:
        monkeypatch.undo()
