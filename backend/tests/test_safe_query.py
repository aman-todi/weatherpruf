"""The where_clause validator and the fixed queries (spec §4.1).

Two kinds of test here, and they are worth keeping apart:

* ``validate_where_clause`` is pure and gets exhaustive coverage — this is
  where the adversarial battery lives, because a rejection that never touches
  the database is the point;
* the execution path runs against the real local Postgres over the real
  read-only role, because the properties worth proving (the bound ``user_id``
  actually scopes the result, the role really cannot write) do not exist in a
  fake.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app import db, safe_query
from app.config import get_settings
from app.errors import UnsafeQueryError
from app.models import ItemCreate
from app.services import items as items_service

# A stand-in for what category_field_defs holds, so the pure tests do not need
# a database. test_json_key_allowlist_comes_from_the_database checks the real
# thing lines up with it.
KNOWN_FIELDS = {"sleeve_length", "shoe_type", "material", "fit"}


def validate(clause: str) -> str:
    return safe_query.validate_where_clause(clause, KNOWN_FIELDS)


# --- what must be accepted --------------------------------------------------

ACCEPTED = [
    "category = 'jacket'",
    "category IN ('jacket','coat') AND warmth_rating >= 4",
    "warmth_rating BETWEEN 2 AND 4",
    "brand IS NOT NULL",
    "brand IS NULL OR brand = 'Nike'",
    "tags && ARRAY['floral','date-night']",
    "tags @> ARRAY['rain']",
    "tags <@ ARRAY['a','b']",
    "'navy' = ANY(colors)",
    "notes ILIKE '%floral%'",
    "brand LIKE 'Nik%'",
    "fields->>'shoe_type' = 'boot'",
    "fields->'material' = 'wool'",
    "NOT (category = 'hat')",
    "formality <> 'athletic'",
    "warmth_rating = -1",
    "true",
    "(category = 'shoes' AND fields->>'shoe_type' IN ('boot','sneaker')) OR warmth_rating > 3",
    # A keyword inside a string literal is not a keyword. `notes` is exactly
    # where words like these show up, so this must not be rejected.
    "notes ILIKE '%goes with jeans%'",
    "notes ILIKE '%select the grey one%'",
    "brand = 'Levi''s'",
]


@pytest.mark.parametrize("clause", ACCEPTED)
def test_accepts_valid_predicates(clause: str) -> None:
    assert validate(clause)


def test_reserialises_rather_than_echoing_the_input() -> None:
    """The executed SQL comes from sqlglot's generator, not from the caller."""
    assert validate("category='jacket'   AND    warmth_rating>=4") == (
        "category = 'jacket' AND warmth_rating >= 4"
    )


def test_a_quote_in_a_literal_is_re_escaped_not_passed_through() -> None:
    assert validate("brand = 'Levi''s'") == "brand = 'Levi''s'"


# --- the adversarial battery ------------------------------------------------
#
# The six the ticket names, plus the variations worth having. Each case is
# (clause, a fragment the error message must contain) so a case cannot pass by
# being rejected for an unrelated reason.

REJECTED = [
    # the six named in Ticket 3
    ("category = 'hat'; DROP TABLE items", "semicolon"),
    ("category IN (SELECT category FROM items)", "SELECT"),
    ("DROP TABLE items", "DROP"),
    ("created_at > '2020-01-01'", "not a queryable column"),
    ("user_id = '11111111-1111-1111-1111-111111111111'", "not a queryable column"),
    ("pg_sleep(10) IS NULL", "function call"),
    # union injection
    ("1 = 1 UNION SELECT 1", "UNION"),
    ("category = 'hat' UNION ALL SELECT * FROM closet_query_view", "UNION"),
    # comment-based truncation
    ("category = 'hat' --", "comment"),
    ("category = 'hat' /* rest */", "comment"),
    ("category = 'hat') OR (1=1) --", "comment"),
    # casting tricks
    ("warmth_rating::text = '4'", "cast"),
    ("CAST(warmth_rating AS text) = '4'", "cast"),
    # functions outside the allowlist
    ("length(brand) > 3", "function call"),
    ("lower(brand) = 'nike'", "function call"),
    ("pg_read_file('/etc/passwd') IS NOT NULL", "function call"),
    ("current_setting('request.jwt.claim.sub') IS NOT NULL", "function call"),
    ("version() IS NOT NULL", "function call"),
    # fields->> with a key that is not a real field
    ("fields->>'not_a_real_field' = 'x'", "not a category-specific field"),
    ("fields->>'a'->>'b' = 'x'", "traversal"),
    ("notes->>'x' = 'y'", "traversal"),
    ("colors->>'0' = 'navy'", "traversal"),
    # other statements and structure
    ("WITH x AS (SELECT 1) SELECT 1", "WITH"),
    ("DELETE FROM items", "DELETE"),
    ("UPDATE items SET brand = 'x'", "UPDATE"),
    ("INSERT INTO items (brand) VALUES ('x')", "INSERT"),
    ("GRANT SELECT ON items TO wardrobe_readonly", "GRANT"),
    ("ALTER TABLE items DROP COLUMN brand", "ALTER"),
    ("EXISTS (SELECT 1 FROM auth.users)", "EXISTS"),
    ("category = 'hat' AND (SELECT true)", "SELECT"),
    # qualified names
    ("items.user_id IS NOT NULL", "table-qualified"),
    ("closet_query_view.category = 'hat'", "table-qualified"),
    # quoting tricks
    ("category = 'unterminated", "unterminated"),
    ("category = 'a''; DROP TABLE items; --'", "semicolon"),
    ("category = $$hat$$", "dollar-quoted"),
    ("category = E'\\x68at'", "backslash"),
    # shape
    ("", "empty"),
    ("   ", "empty"),
    ("category", "boolean expression"),
    ("'hat'", "boolean expression"),
    ("category = 'hat' AND", "not valid SQL"),
    ("((((category = 'hat')", "not valid SQL"),
]


@pytest.mark.parametrize("clause,expected_fragment", REJECTED)
def test_rejects_unsafe_predicates(clause: str, expected_fragment: str) -> None:
    with pytest.raises(UnsafeQueryError) as raised:
        validate(clause)
    assert expected_fragment.lower() in raised.value.message.lower(), raised.value.message


def test_an_absurdly_long_predicate_is_rejected() -> None:
    with pytest.raises(UnsafeQueryError):
        validate("category = 'hat' AND " + " AND ".join(["1=1"] * 500))


def test_a_deeply_nested_predicate_is_rejected() -> None:
    with pytest.raises(UnsafeQueryError):
        validate("(" * 200 + "category = 'hat'" + ")" * 200)


def test_rejection_carries_the_allowed_columns_so_the_model_can_correct_itself() -> None:
    with pytest.raises(UnsafeQueryError) as raised:
        validate("created_at > '2020-01-01'")
    assert set(raised.value.details["allowed_columns"]) == safe_query.ALLOWED_COLUMNS


def test_user_id_is_not_in_the_column_allowlist() -> None:
    """The only user scoping is the bound parameter, so a predicate must never
    be able to name the column at all."""
    assert "user_id" not in safe_query.ALLOWED_COLUMNS
    assert "id" not in safe_query.ALLOWED_COLUMNS


def test_allowlist_matches_the_spec() -> None:
    assert safe_query.ALLOWED_COLUMNS == {
        "category",
        "colors",
        "brand",
        "warmth_rating",
        "formality",
        "tags",
        "notes",
        "fields",
    }


# --- execution against the real database ------------------------------------


@pytest.fixture
async def stocked_closet(user_id: uuid.UUID) -> uuid.UUID:
    for payload in [
        ItemCreate(
            category="jacket",
            colors=["navy"],
            brand="Patagonia",
            warmth_rating=4,
            formality="casual",
            tags=["rain"],
            notes="Good in a downpour.",
        ),
        ItemCreate(category="tshirt", colors=["red"], brand="Nike", warmth_rating=1),
        ItemCreate(
            category="shoes",
            colors=["brown"],
            fields={"shoe_type": "boot"},
            tags=["date-night"],
        ),
    ]:
        await items_service.create_item(user_id, payload)
    return user_id


async def test_runs_a_valid_predicate(stocked_closet: uuid.UUID) -> None:
    result = await safe_query.run_where_clause(stocked_closet, "warmth_rating >= 4")
    assert result["count"] == 1
    assert result["items"][0]["brand"] == "Patagonia"
    assert result["where_clause_executed"] == "warmth_rating >= 4"


async def test_returns_tags_colors_and_notes(stocked_closet: uuid.UUID) -> None:
    """Spec §4 passes these through deliberately — they carry the detail that
    distinguishes two otherwise similar items."""
    result = await safe_query.run_where_clause(stocked_closet, "category = 'jacket'")
    item = result["items"][0]
    assert item["tags"] == ["rain"]
    assert item["colors"] == ["navy"]
    assert item["notes"] == "Good in a downpour."


async def test_json_field_access_runs(stocked_closet: uuid.UUID) -> None:
    result = await safe_query.run_where_clause(stocked_closet, "fields->>'shoe_type' = 'boot'")
    assert result["count"] == 1


async def test_json_key_allowlist_comes_from_the_database(stocked_closet: uuid.UUID) -> None:
    """A key that exists in category_field_defs is accepted and one that does
    not is rejected, with the set read from the database rather than hardcoded."""
    from app.services import taxonomy

    known = await taxonomy.known_field_names()
    assert "shoe_type" in known
    assert safe_query.validate_where_clause("fields->>'shoe_type' = 'boot'", known)
    with pytest.raises(UnsafeQueryError):
        safe_query.validate_where_clause("fields->>'invented' = 'x'", known)


async def test_a_permissive_predicate_still_only_sees_the_caller(
    stocked_closet: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    """The bound user_id, not the predicate, is what scopes the result — and it
    has to be, because closet_query_view is not protected by RLS."""
    await items_service.create_item(
        other_user_id, ItemCreate(category="hat", brand="SomeoneElsesHat")
    )

    result = await safe_query.run_where_clause(stocked_closet, "true")
    assert result["count"] == 3
    assert "SomeoneElsesHat" not in {item["brand"] for item in result["items"]}

    # An OR cannot widen past the parenthesised predicate either.
    widened = await safe_query.run_where_clause(
        stocked_closet, "category = 'jacket' OR brand IS NULL OR brand IS NOT NULL"
    )
    assert widened["count"] == 3


async def test_the_row_limit_is_applied(user_id: uuid.UUID) -> None:
    settings = get_settings()
    limit = settings.query_result_limit
    for index in range(limit + 3):
        await items_service.create_item(
            user_id, ItemCreate(category="tshirt", brand=f"brand-{index:03d}")
        )

    result = await safe_query.run_where_clause(user_id, "category = 'tshirt'")
    assert result["count"] == limit
    assert result["truncated"] is True


async def test_a_rejected_predicate_never_reaches_the_database(
    stocked_closet: uuid.UUID,
) -> None:
    before = await _count_all_items()
    for clause, _ in REJECTED:
        with pytest.raises(UnsafeQueryError):
            await safe_query.run_where_clause(stocked_closet, clause)
    assert await _count_all_items() == before

    async with db.app_pool().acquire() as conn:
        assert await conn.fetchval("select to_regclass('public.items') is not null")
        assert await conn.fetchval("select to_regclass('public.closet_query_view') is not null")


async def test_rejections_are_logged(stocked_closet: uuid.UUID, caplog) -> None:
    """Spec §4.1 step 6 — rejections are logged so the allowlist can be tuned
    and abuse spotted."""
    with caplog.at_level("WARNING", logger="app.safe_query"):
        with pytest.raises(UnsafeQueryError):
            await safe_query.run_where_clause(stocked_closet, "DROP TABLE items")
    assert any("rejected where_clause" in record.message for record in caplog.records)
    assert any("DROP TABLE items" in record.getMessage() for record in caplog.records)


async def test_the_read_only_role_cannot_write(user_id: uuid.UUID) -> None:
    """The actual security boundary (spec §3), asserted rather than assumed."""
    async with db.readonly_transaction() as conn:
        for statement in (
            "insert into public.items (user_id, category_id) values "
            "('11111111-1111-1111-1111-111111111111', 'hat')",
            "update public.items set brand = 'x'",
            "delete from public.items",
            "select * from public.items limit 1",
            "select * from auth.users limit 1",
        ):
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(statement)


# --- the fixed queries ------------------------------------------------------


async def test_list_category_is_scoped_and_shaped(
    stocked_closet: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    await items_service.create_item(
        other_user_id,
        ItemCreate(category="shoes", brand="Theirs", fields={"shoe_type": "sneaker"}),
    )

    result = await safe_query.list_category(stocked_closet, "shoes")
    assert result["count"] == 1
    assert result["category"] == "shoes"
    assert result["items"][0]["fields"] == {"shoe_type": "boot"}
    assert result["truncated"] is False


async def test_list_category_of_an_empty_category(stocked_closet: uuid.UUID) -> None:
    result = await safe_query.list_category(stocked_closet, "hat")
    assert result["count"] == 0
    assert result["items"] == []


async def test_closet_summary(stocked_closet: uuid.UUID, other_user_id: uuid.UUID) -> None:
    await items_service.create_item(other_user_id, ItemCreate(category="hat", brand="Theirs"))

    summary = await safe_query.closet_summary(stocked_closet)
    assert summary["total_items"] == 3
    assert summary["items_per_category"] == {"jacket": 1, "shoes": 1, "tshirt": 1}
    assert summary["brands"] == ["Nike", "Patagonia"]
    assert "Theirs" not in summary["brands"]
    assert summary["tags"] == ["date-night", "rain"]
    assert summary["formality_distribution"] == {"casual": 1, "unspecified": 2}
    assert summary["warmth_distribution"] == {"1": 1, "4": 1, "unspecified": 1}


async def test_closet_summary_of_an_empty_closet(user_id: uuid.UUID) -> None:
    summary = await safe_query.closet_summary(user_id)
    assert summary["total_items"] == 0
    assert summary["items_per_category"] == {}
    assert summary["brands"] == []


async def _count_all_items() -> int:
    async with db.app_pool().acquire() as conn:
        return await conn.fetchval("select count(*) from public.items")


class TestIdentifierCasing:
    """Postgres folds unquoted identifiers to lower case before resolving them,
    so a predicate written in the SQL-keyword-uppercase style a model may well
    produce must not be rejected as an unknown column."""

    @pytest.mark.parametrize(
        "clause",
        [
            "CATEGORY = 'jacket'",
            "Category = 'jacket'",
            "WARMTH_RATING >= 4",
            "category = 'coat' AND Brand IS NOT NULL",
            "FIELDS->>'shoe_type' = 'boot'",
        ],
    )
    def test_unquoted_identifiers_are_case_insensitive(self, clause):
        # Returns without raising; the generated SQL names the real column.
        validated = validate(clause)
        assert "CATEGORY" not in validated
        assert "FIELDS" not in validated

    @pytest.mark.parametrize(
        "clause",
        [
            '"User_Id" is not null',
            '"user_id" is not null',
            "\"CREATED_AT\" > '2020-01-01'",
        ],
    )
    def test_quoted_identifiers_keep_their_case_and_are_still_rejected(self, clause):
        """Case folding must not become a way to reach a column that is off
        the allowlist."""
        with pytest.raises(UnsafeQueryError):
            validate(clause)

    def test_case_folding_does_not_admit_a_disallowed_column(self):
        with pytest.raises(UnsafeQueryError):
            validate("USER_ID is not null")
        with pytest.raises(UnsafeQueryError):
            validate("Created_At is not null")
