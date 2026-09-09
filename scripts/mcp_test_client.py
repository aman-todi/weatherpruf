#!/usr/bin/env python3
"""Drive every MCP tool against a locally running server (Ticket 3's DoD).

Run the backend first, then this:

    cd backend && .venv/bin/uvicorn app.main:app --port 8000
    backend/.venv/bin/python scripts/mcp_test_client.py

It speaks the real protocol over the real transport with the official MCP
Python SDK's ``Client`` — no in-process shortcut — so what it exercises is what
Claude.ai would exercise: token verification, the session handshake, the tool
schemas, and the tool bodies.

What it covers:

* every tool in spec §4, on its happy path, with the shapes checked;
* ``batch_add_items`` with valid and invalid items mixed, confirming partial
  success (spec §4.2);
* an adversarial ``where_clause`` battery — every case must be rejected, and
  the run fails loudly if any of them reaches the database;
* cross-user isolation: a second user's items must not appear in the first
  user's results, which matters more than usual on the query path because
  ``closet_query_view`` is not protected by RLS (see db/migrations/0003);
* the daily call cap actually firing.

It needs direct database access as well as HTTP: to seed the two test users
(the foreign keys require them), to mint tokens, and to reset the daily counter
between phases so the cap does not stop the run before the phase that is
supposed to test it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

try:
    import asyncpg
    import jwt
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
except ImportError as exc:  # pragma: no cover - a setup problem, not a test failure
    sys.exit(
        f"missing dependency ({exc}). Run this with backend/.venv/bin/python "
        "after `pip install -e '.[dev,mcp]'`."
    )

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover - older transports use httpx directly
    import httpx


# --- tiny reporting harness -------------------------------------------------

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  \033[32mPASS\033[0m {name}")
    else:
        FAILED.append(name)
        print(f"  \033[31mFAIL\033[0m {name}{(' — ' + detail) if detail else ''}")
    return condition


def heading(text: str) -> None:
    print(f"\n\033[1m{text}\033[0m")


def load_env() -> dict[str, str]:
    """Read backend/.env so the script signs tokens with the same secret the
    running server verifies with."""
    values: dict[str, str] = {}
    env_file = ROOT / "backend" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    values.update({k: v for k, v in os.environ.items() if k in values or k.isupper()})
    return values


def mint_token(user_id: uuid.UUID, secret: str, issuer: str) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "email": f"{str(user_id)[:8]}@example.test",
        "role": "authenticated",
        "aud": "authenticated",
        "iat": now,
        "exp": now + 3600,
    }
    if issuer:
        claims["iss"] = f"{issuer.rstrip('/')}/auth/v1"
    return jwt.encode(claims, secret, algorithm="HS256")


def payload(result: Any) -> dict[str, Any]:
    """The tool's structured result, whichever way this SDK version carries it."""
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        # FastMCP wraps a non-object return in {"result": ...}; unwrap it.
        if set(structured) == {"result"} and isinstance(structured["result"], dict):
            return structured["result"]
        return structured
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"_text": text}
    return {}


def error_text(result: Any) -> str:
    """Everything a rejection could have been reported through, flattened, so a
    check for "was this rejected" cannot miss it."""
    parts = [json.dumps(payload(result), default=str)]
    for block in getattr(result, "content", None) or []:
        if getattr(block, "text", None):
            parts.append(block.text)
    return " ".join(parts)


def connect(url: str, token: str) -> Client:
    """A Client over streamable HTTP carrying a bearer token.

    The transport takes its headers from the httpx client it is given, which is
    how the access token gets onto every request.
    """
    http_client = httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
        follow_redirects=True,
    )
    return Client(streamable_http_client(url, http_client=http_client))


# --- database helpers -------------------------------------------------------


async def seed_user(pool: asyncpg.Pool, user_id: uuid.UUID) -> None:
    await pool.execute(
        "insert into auth.users (id, email) values ($1, $2) on conflict (id) do nothing",
        user_id,
        f"{str(user_id)[:8]}@example.test",
    )


async def purge_user(pool: asyncpg.Pool, user_id: uuid.UUID) -> None:
    await pool.execute("delete from auth.users where id = $1", user_id)


async def reset_quota(pool: asyncpg.Pool, user_id: uuid.UUID) -> None:
    """Clear today's call count.

    Exercising nine tools plus an adversarial battery costs far more than the
    10-call daily budget, so the counter is reset between phases. The cap phase
    at the end deliberately does not reset, so the real check-and-increment path
    is what fires there.
    """
    await pool.execute(
        "delete from public.usage_counters where user_id = $1 "
        "and day = (now() at time zone 'utc')::date",
        user_id,
    )


async def set_quota(pool: asyncpg.Pool, user_id: uuid.UUID, count: int) -> None:
    await pool.execute(
        """
        insert into public.usage_counters (user_id, day, call_count)
        values ($1, (now() at time zone 'utc')::date, $2)
        on conflict (user_id, day) do update set call_count = $2
        """,
        user_id,
        count,
    )


# --- the adversarial battery ------------------------------------------------
#
# Every entry must be rejected. The six named in the ticket are marked; the rest
# are the variations worth having once you start thinking like an attacker.

ADVERSARIAL_CLAUSES: list[tuple[str, str]] = [
    # -- the six the ticket names --
    ("category = 'hat'; DROP TABLE public.items", "semicolon-separated second statement"),
    ("category IN (SELECT category FROM public.items)", "subquery"),
    ("DROP TABLE public.items", "DROP TABLE"),
    ("created_at > '2020-01-01'", "column outside the allowlist"),
    (
        "user_id = '00000000-0000-0000-0000-000000000000'",
        "another user's user_id referenced directly",
    ),
    ("pg_sleep(10) IS NULL", "disallowed function call"),
    # -- union injection --
    ("1 = 1 UNION SELECT 1", "UNION injection"),
    (
        "category = 'hat' UNION ALL SELECT id, user_id, category, colors, brand, "
        "warmth_rating, formality, tags, notes, fields FROM public.closet_query_view",
        "UNION ALL exfiltrating the whole view",
    ),
    # -- comment-based truncation --
    ("category = 'hat' --", "line-comment truncation"),
    ("category = 'hat' /* and the rest */", "block-comment truncation"),
    ("category = 'hat') OR (1=1) --", "paren break-out plus comment"),
    # -- casting tricks --
    ("warmth_rating::text = '4'", "type cast with ::"),
    ("CAST(warmth_rating AS text) = '4'", "type cast with CAST()"),
    # -- other functions --
    ("pg_read_file('/etc/passwd') IS NOT NULL", "file read function"),
    ("length(brand) > 3", "an ordinary but non-allowlisted function"),
    ("current_setting('request.jwt.claim.sub') IS NOT NULL", "session-setting read"),
    ("version() IS NOT NULL", "version()"),
    # -- fields->> with an unknown key --
    ("fields->>'not_a_real_field' = 'x'", "fields->> with an unknown key"),
    ("fields->>'a'->>'b' = 'x'", "chained JSON traversal"),
    ("notes->>'x' = 'y'", "JSON traversal on a non-JSON column"),
    # -- other statements and structure --
    ("WITH x AS (SELECT 1) SELECT 1", "CTE"),
    ("DELETE FROM public.items", "DELETE"),
    ("UPDATE public.items SET brand = 'x'", "UPDATE"),
    ("GRANT SELECT ON public.items TO wardrobe_readonly", "GRANT"),
    ("EXISTS (SELECT 1 FROM auth.users)", "EXISTS subquery"),
    ("items.user_id IS NOT NULL", "table-qualified column"),
    ("closet_query_view.category = 'hat'", "view-qualified column"),
    ("category = 'hat' AND (SELECT true)", "scalar subquery in a conjunct"),
    # -- string / quoting tricks --
    ("category = 'unterminated", "unterminated string literal"),
    ("category = 'a''; DROP TABLE public.items; --'", "escaped-quote break-out attempt"),
    ("category = $$hat$$", "dollar-quoted string"),
    ("category = E'\\x68at'", "backslash escape string"),
    # -- resource exhaustion --
    ("category = 'hat' AND " + " AND ".join(["1=1"] * 300), "absurdly long predicate"),
]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000/mcp/")
    parser.add_argument("--db", default=None, help="override DATABASE_URL")
    args = parser.parse_args()

    env = load_env()
    secret = env.get("SUPABASE_JWT_SECRET") or "local-dev-secret-change-me"
    issuer = env.get("SUPABASE_URL", "")
    database_url = args.db or env.get(
        "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/wardrobe_dev"
    )

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=3)
    assert pool is not None

    user_id = uuid.uuid4()
    other_id = uuid.uuid4()
    await seed_user(pool, user_id)
    await seed_user(pool, other_id)

    print(f"server:     {args.url}")
    print(f"test user:  {user_id}")
    print(f"other user: {other_id}")

    token = mint_token(user_id, secret, issuer)
    other_token = mint_token(other_id, secret, issuer)

    try:
        async with connect(args.url, token) as client:
            await reset_quota(pool, user_id)
            await run_suite(client, pool, user_id, args.url, other_id, other_token)
    finally:
        await purge_user(pool, user_id)
        await purge_user(pool, other_id)
        await pool.close()

    print(f"\n\033[1m{PASSED} passed, {len(FAILED)} failed\033[0m")
    if FAILED:
        for name in FAILED:
            print(f"  \033[31m-\033[0m {name}")
        return 1
    return 0


async def run_suite(
    client: Client,
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    url: str,
    other_id: uuid.UUID,
    other_token: str,
) -> None:
    async def call(name: str, arguments: dict[str, Any] | None = None) -> Any:
        """One tool call, with the daily counter cleared first.

        Nine tools plus a 30-case battery is many times the daily budget, so the
        quota is reset per call here; the cap gets its own phase at the end.
        """
        await reset_quota(pool, user_id)
        return await client.call_tool(name, arguments or {})

    # -- protocol surface ----------------------------------------------------
    heading("Protocol and tool surface")
    tools = await client.list_tools()
    names = sorted(tool.name for tool in tools.tools)
    expected = sorted(
        [
            "get_closet_structure",
            "add_item",
            "update_item",
            "remove_item",
            "batch_add_items",
            "query_closet_items",
            "list_category_items",
            "get_closet_summary",
            "get_user_profile",
        ]
    )
    check("every tool in spec §4 is registered", names == expected, f"got {names}")
    check(
        "no tool exposes user_id as an argument",
        all("user_id" not in (tool.input_schema or {}).get("properties", {}) for tool in tools.tools),
    )
    check(
        "batch_add_items carries its system-prompt-style description (spec §4.2)",
        all(
            phrase in (next(t for t in tools.tools if t.name == "batch_add_items").description or "")
            for phrase in ("get_closet_structure", "ASK THE USER", "20 items")
        ),
    )

    # -- unauthenticated -----------------------------------------------------
    heading("Authentication")
    async with httpx.AsyncClient(timeout=15.0) as raw:
        anon = await raw.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
    check("an unauthenticated request is refused", anon.status_code == 401, str(anon.status_code))
    check(
        "the 401 carries a WWW-Authenticate challenge",
        "www-authenticate" in {k.lower() for k in anon.headers},
    )

    async with httpx.AsyncClient(timeout=15.0) as raw:
        bad = await raw.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer not-a-real-token",
            },
        )
    check("a forged token is refused", bad.status_code == 401, str(bad.status_code))

    # -- get_closet_structure ------------------------------------------------
    heading("get_closet_structure")
    structure = payload(await call("get_closet_structure"))
    categories = structure.get("categories", [])
    check("returns the category list", len(categories) >= 20, f"{len(categories)} categories")
    check(
        "categories carry their field templates",
        any(category.get("fields") for category in categories),
    )
    check("returns the six common fields", len(structure.get("common_fields", [])) == 6)
    instructions = structure.get("query_instructions", "")
    check(
        "query_instructions documents the grammar (spec §4)",
        all(
            token in instructions
            for token in ("where_clause", "warmth_rating", "ILIKE", "tags &&", "fields->>")
        ),
    )
    check("query_instructions carries worked examples", "EXAMPLES" in instructions)
    check("reports the limits", structure.get("limits", {}).get("max_items_per_closet") == 200)

    shoe_fields = next(
        (c["fields"] for c in categories if c["id"] == "shoes"),
        [],
    )
    check("shoes has its shoe_type field", any(f["field_name"] == "shoe_type" for f in shoe_fields))

    # -- get_user_profile ----------------------------------------------------
    heading("get_user_profile")
    profile = payload(await call("get_user_profile"))
    check("returns unit_preference", profile.get("unit_preference") in ("fahrenheit", "celsius"))
    check("home_location is present (may be null)", "home_location" in profile)

    # -- add_item ------------------------------------------------------------
    heading("add_item")
    added = payload(
        await call(
            "add_item",
            {
                "category": "jacket",
                "colors": ["navy", "white"],
                "brand": "Patagonia",
                "warmth_rating": 4,
                "formality": "casual",
                "tags": ["rain", "outdoor"],
                "notes": "Packs down small. Good in a downpour.",
            },
        )
    )
    check("creates the item", added.get("status") == "created", json.dumps(added)[:200])
    jacket = added.get("item", {})
    jacket_id = jacket.get("id")
    check("returns the created item with an id", bool(jacket_id))
    check("stores the colours", jacket.get("colors") == ["navy", "white"])
    check("reports the remaining daily budget", "usage" in added)

    rejected = payload(await call("add_item", {"category": "not_a_real_category"}))
    check(
        "an unknown category is rejected with the valid list",
        rejected.get("error") == "unknown_category" and "jacket" in rejected.get("message", ""),
        json.dumps(rejected)[:200],
    )

    too_many = payload(
        await call(
            "add_item",
            {"category": "tshirt", "colors": ["a", "b", "c", "d", "e", "f"]},
        )
    )
    check(
        "a sixth colour is rejected",
        too_many.get("error") == "validation_failed",
        json.dumps(too_many)[:200],
    )

    # -- batch_add_items -----------------------------------------------------
    heading("batch_add_items — partial success (spec §4.2)")
    batch = payload(
        await call(
            "batch_add_items",
            {
                "items": [
                    {"category": "tshirt", "colors": ["red"], "brand": "Nike"},
                    {"category": "polo", "colors": ["blue"]},
                    {"category": "hoodie", "colors": ["black"], "warmth_rating": 3},
                    # invalid: category does not exist
                    {"category": "space_suit", "colors": ["silver"]},
                    # invalid: not a field of tshirt
                    {"category": "tshirt", "fields": {"heel_height": "tall"}},
                    # invalid: warmth_rating out of range
                    {"category": "coat", "warmth_rating": 99},
                    # invalid: six colours
                    {"category": "shirt", "colors": ["a", "b", "c", "d", "e", "f"]},
                    # valid again, after the invalid ones — proves the batch did
                    # not abort at the first failure
                    {
                        "category": "shoes",
                        "colors": ["brown"],
                        "fields": {"shoe_type": "boot"},
                        "tags": ["date-night"],
                        "notes": "Leather, needs a dry day.",
                    },
                ]
            },
        )
    )
    results = batch.get("results", [])
    check("one result per submitted item", len(results) == 8, f"{len(results)} results")
    check("four items were created", batch.get("created_count") == 4, json.dumps(batch)[:300])
    check("four items failed", batch.get("failed_count") == 4)
    check(
        "results keep the submitted order",
        [entry["index"] for entry in results] == list(range(8)),
    )
    check(
        "the valid item AFTER the invalid ones still succeeded (partial success)",
        results[7].get("status") == "created",
        json.dumps(results[7])[:200],
    )
    check(
        "each failure says why",
        all(entry.get("message") for entry in results if entry["status"] == "failed"),
    )
    check(
        "the unknown category is named as such",
        results[3].get("error") == "unknown_category",
        json.dumps(results[3])[:200],
    )

    over_cap = payload(
        await call("batch_add_items", {"items": [{"category": "tshirt"}] * 21})
    )
    check(
        "more than 20 items in one call is rejected",
        over_cap.get("error") == "validation_failed",
        json.dumps(over_cap)[:200],
    )

    # -- list_category_items -------------------------------------------------
    heading("list_category_items")
    listing = payload(await call("list_category_items", {"category": "tshirt"}))
    check("lists the category's items", listing.get("count") == 1, json.dumps(listing)[:200])
    check("echoes the category", listing.get("category") == "tshirt")
    check(
        "items carry the full field shape",
        set(listing["items"][0]) >= {"id", "category", "colors", "brand", "tags", "notes", "fields"},
    )
    bad_category = payload(await call("list_category_items", {"category": "space_suit"}))
    check(
        "an unknown category is an error, not an empty list",
        bad_category.get("error") == "unknown_category",
        json.dumps(bad_category)[:200],
    )

    # -- get_closet_summary --------------------------------------------------
    heading("get_closet_summary")
    summary = payload(await call("get_closet_summary"))
    check("counts every item", summary.get("total_items") == 5, json.dumps(summary)[:250])
    check("counts per category", summary.get("items_per_category", {}).get("jacket") == 1)
    check("lists distinct brands", "Patagonia" in summary.get("brands", []))
    check("lists distinct tags", "date-night" in summary.get("tags", []))
    check("reports a warmth distribution", bool(summary.get("warmth_distribution")))
    check("reports a formality distribution", bool(summary.get("formality_distribution")))

    # -- query_closet_items, happy path --------------------------------------
    heading("query_closet_items — valid predicates")
    happy: list[tuple[str, int]] = [
        ("category IN ('jacket','coat') AND warmth_rating >= 4", 1),
        ("tags && ARRAY['date-night']", 1),
        ("category = 'shoes' AND fields->>'shoe_type' = 'boot'", 1),
        ("notes ILIKE '%downpour%'", 1),
        ("'red' = ANY(colors)", 1),
        ("brand IS NULL", 3),
        ("warmth_rating BETWEEN 3 AND 5", 2),
        ("NOT (category = 'jacket') AND category = 'polo'", 1),
        ("tags @> ARRAY['rain','outdoor']", 1),
        # A literal containing a keyword must survive the prescan, because
        # `notes` is exactly where such words show up.
        ("notes ILIKE '%needs a dry day%'", 1),
    ]
    for clause, expected_count in happy:
        result = payload(await call("query_closet_items", {"where_clause": clause}))
        check(
            f"{clause[:52]!r} matches {expected_count}",
            result.get("count") == expected_count,
            f"got {result.get('count')} / {result.get('error', '')} {result.get('message', '')}"[:180],
        )

    echoed = payload(
        await call("query_closet_items", {"where_clause": "category='jacket'"})
    )
    check(
        "the executed (re-serialised) predicate is echoed back",
        echoed.get("where_clause_executed") == "category = 'jacket'",
        str(echoed.get("where_clause_executed")),
    )

    # -- cross-user isolation ------------------------------------------------
    heading("Cross-user isolation")
    await seed_user(pool, other_id)
    async with connect(url, other_token) as other_client:
        await reset_quota(pool, other_id)
        other_added = payload(
            await other_client.call_tool(
                "add_item",
                {"category": "hat", "brand": "SomeoneElsesHat", "colors": ["pink"]},
            )
        )
        check("the second user can add an item", other_added.get("status") == "created")

    everything = payload(await call("query_closet_items", {"where_clause": "true"}))
    brands = {item.get("brand") for item in everything.get("items", [])}
    check(
        "a permissive predicate returns only the caller's items",
        "SomeoneElsesHat" not in brands and everything.get("count") == 5,
        f"count={everything.get('count')} brands={brands}",
    )
    hats = payload(await call("list_category_items", {"category": "hat"}))
    check("list_category_items does not leak the other user's hat", hats.get("count") == 0)
    other_summary = payload(await call("get_closet_summary"))
    check(
        "get_closet_summary does not count the other user's items",
        other_summary.get("total_items") == 5,
    )

    # -- the adversarial battery ---------------------------------------------
    heading(f"Adversarial where_clause battery ({len(ADVERSARIAL_CLAUSES)} cases)")
    before = await pool.fetchval("select count(*) from public.items")

    for clause, description in ADVERSARIAL_CLAUSES:
        result = payload(await call("query_closet_items", {"where_clause": clause}))
        text = error_text(result)
        rejected_cleanly = (
            result.get("error") == "unsafe_query"
            and bool(result.get("message"))
            and "items" not in result
        )
        check(
            f"rejected: {description}",
            rejected_cleanly,
            f"clause={clause[:60]!r} -> {text[:200]}",
        )

    after = await pool.fetchval("select count(*) from public.items")
    check(
        "no adversarial clause changed the database",
        before == after,
        f"{before} items before, {after} after",
    )
    still_there = await pool.fetchval(
        "select to_regclass('public.items') is not null and "
        "to_regclass('public.closet_query_view') is not null"
    )
    check("items and closet_query_view still exist", bool(still_there))

    # -- the read-only role really is read-only ------------------------------
    heading("The read-only role (the actual security boundary, spec §3/§4.1)")
    env = load_env()
    ro_url = env.get("READONLY_DATABASE_URL", "")
    if ro_url:
        ro = await asyncpg.connect(ro_url)
        try:
            for statement, label in [
                ("insert into public.items (user_id, category_id) values ($1, 'hat')", "INSERT"),
                ("update public.items set brand = 'x'", "UPDATE"),
                ("delete from public.items", "DELETE"),
                ("select * from public.items limit 1", "SELECT on items"),
                ("select * from auth.users limit 1", "SELECT on auth.users"),
            ]:
                try:
                    if "$1" in statement:
                        await ro.execute(statement, user_id)
                    else:
                        await ro.execute(statement)
                    check(f"the read-only role cannot {label}", False, "it succeeded")
                except asyncpg.PostgresError as exc:
                    check(f"the read-only role cannot {label}", True, type(exc).__name__)
            rows = await ro.fetch(
                "select id from public.closet_query_view where user_id = $1", user_id
            )
            check("the read-only role can SELECT the view", len(rows) == 5)
        finally:
            await ro.close()
    else:
        check("READONLY_DATABASE_URL is configured", False, "not set in backend/.env")

    # -- update_item / remove_item -------------------------------------------
    heading("update_item and remove_item")
    updated = payload(
        await call(
            "update_item",
            {"item_id": jacket_id, "brand": "Arc'teryx", "tags": ["rain", "commute"]},
        )
    )
    check("updates the named fields", updated.get("item", {}).get("brand") == "Arc'teryx")
    check(
        "leaves unnamed fields alone",
        updated.get("item", {}).get("warmth_rating") == 4
        and updated.get("item", {}).get("colors") == ["navy", "white"],
        json.dumps(updated.get("item", {}))[:200],
    )
    check("replaces the tags it was given", updated.get("item", {}).get("tags") == ["rain", "commute"])

    missing = payload(
        await call("update_item", {"item_id": str(uuid.uuid4()), "brand": "Nobody"})
    )
    check("updating an unknown item is a not_found error", missing.get("error") == "not_found")

    malformed = payload(await call("update_item", {"item_id": "not-a-uuid", "brand": "x"}))
    check(
        "a malformed item id is rejected with guidance",
        malformed.get("error") == "validation_failed",
        json.dumps(malformed)[:200],
    )

    # An item id belonging to someone else must not be updatable.
    other_item_id = await pool.fetchval(
        "select id from public.items where user_id = $1", other_id
    )
    stolen = payload(await call("update_item", {"item_id": str(other_item_id), "brand": "Mine now"}))
    check(
        "another user's item cannot be updated",
        stolen.get("error") == "not_found",
        json.dumps(stolen)[:200],
    )
    stolen_delete = payload(await call("remove_item", {"item_id": str(other_item_id)}))
    check(
        "another user's item cannot be deleted",
        stolen_delete.get("error") == "not_found",
        json.dumps(stolen_delete)[:200],
    )

    removed = payload(await call("remove_item", {"item_id": jacket_id}))
    check("removes the item", removed.get("status") == "removed", json.dumps(removed)[:200])
    gone = payload(await call("query_closet_items", {"where_clause": "category = 'jacket'"}))
    check("the removed item is gone", gone.get("count") == 0)
    twice = payload(await call("remove_item", {"item_id": jacket_id}))
    check("removing it again is a not_found error", twice.get("error") == "not_found")

    # -- the daily call cap --------------------------------------------------
    heading("Daily call cap (spec §5)")
    limit = structure["limits"]["daily_tool_calls_per_user"]
    print(f"  server's configured limit: {limit} calls/user/day")

    # No reset from here on: these calls go through the real check-and-increment.
    await set_quota(pool, user_id, limit - 1)
    last = payload(await client.call_tool("get_closet_summary", {}))
    check(
        "the final call within the budget succeeds",
        "total_items" in last,
        json.dumps(last)[:200],
    )
    check(
        "it reports the budget as spent",
        last.get("usage", {}).get("calls_remaining_today") == 0,
        json.dumps(last.get("usage", {})),
    )

    over = payload(await client.call_tool("get_closet_summary", {}))
    check(
        "the next call is refused with daily_limit_reached",
        over.get("error") == "daily_limit_reached",
        json.dumps(over)[:200],
    )
    check(
        "the refusal tells the assistant when it resets",
        "resets_at" in over.get("details", {}) and "tomorrow" in over.get("message", ""),
        json.dumps(over)[:250],
    )

    for tool_name, arguments in [
        ("get_closet_structure", {}),
        ("query_closet_items", {"where_clause": "true"}),
        ("add_item", {"category": "hat"}),
    ]:
        refused = payload(await client.call_tool(tool_name, arguments))
        check(
            f"the cap also stops {tool_name}",
            refused.get("error") == "daily_limit_reached",
            json.dumps(refused)[:150],
        )

    over_count = await pool.fetchval(
        "select call_count from public.usage_counters where user_id = $1 "
        "and day = (now() at time zone 'utc')::date",
        user_id,
    )
    check(
        "a refused call does not increment the counter past the limit",
        over_count == limit,
        f"counter is {over_count}, limit is {limit}",
    )
    still_five = await pool.fetchval(
        "select count(*) from public.items where user_id = $1", user_id
    )
    check(
        "the add_item refused by the cap did not write anything",
        still_five == 4,
        f"{still_five} items",
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
