"""Ticket 1 DoD: RLS blocks cross-user access through the app's own data layer,
and the shared services enforce the caps and validation both surfaces rely on.
"""

from __future__ import annotations

import uuid

import pytest

from app.config import get_settings
from app.db import readonly_transaction
from app.errors import ClosetFullError, NotFoundError, UnknownCategoryError, ValidationError
from app.models import ItemCreate, ItemUpdate, UserProfileUpdate
from app.services import items, profile, taxonomy, usage


async def test_taxonomy_is_seeded_with_the_full_category_list():
    categories = await taxonomy.load_categories()
    ids = {c.id for c in categories}

    assert len(categories) == 26
    assert {"tshirt", "jeans", "shoes", "two_piece_suit", "lower"} <= ids

    shoes = await taxonomy.get_category("shoes")
    shoe_type = next(f for f in shoes.fields if f.field_name == "shoe_type")
    assert shoe_type.field_type == "enum"
    assert shoe_type.required
    assert "sneaker" in (shoe_type.allowed_values or [])


async def test_unknown_category_names_the_valid_ones():
    with pytest.raises(UnknownCategoryError) as excinfo:
        await taxonomy.get_category("cape")
    assert "tshirt" in str(excinfo.value)


async def test_create_and_read_back_an_item(user_id):
    created = await items.create_item(
        user_id,
        ItemCreate(
            category="tshirt",
            colors=["Red", " red ", "white"],  # trimmed, lowercased, de-duplicated
            brand="Nike",
            warmth_rating=1,
            formality="casual",
            tags=["Gym", "gym"],
            notes="soft cotton",
            fields={"sleeve_length": "Short", "material": "cotton"},
        ),
    )

    assert created.colors == ["red", "white"]
    assert created.tags == ["gym"]
    assert created.fields == {"sleeve_length": "short", "material": "cotton"}

    fetched = await items.get_item(user_id, created.id)
    assert fetched.id == created.id
    assert fetched.brand == "Nike"


async def test_rls_hides_other_users_items(user_id, other_user_id):
    mine = await items.create_item(user_id, ItemCreate(category="tshirt", notes="mine"))
    await items.create_item(other_user_id, ItemCreate(category="coat", notes="theirs"))

    assert [i.notes for i in await items.list_items(user_id)] == ["mine"]
    assert [i.notes for i in await items.list_items(other_user_id)] == ["theirs"]

    # Knowing the id is not enough to read, edit or delete someone else's item.
    with pytest.raises(NotFoundError):
        await items.get_item(other_user_id, mine.id)
    with pytest.raises(NotFoundError):
        await items.update_item(other_user_id, mine.id, ItemUpdate(brand="stolen"))
    with pytest.raises(NotFoundError):
        await items.delete_item(other_user_id, mine.id)

    assert (await items.get_item(user_id, mine.id)).brand is None


async def test_category_field_validation(user_id):
    with pytest.raises(ValidationError) as excinfo:
        await items.create_item(user_id, ItemCreate(category="shoes", fields={"material": "suede"}))
    assert "shoe_type" in str(excinfo.value)  # required field missing

    with pytest.raises(ValidationError):
        await items.create_item(
            user_id, ItemCreate(category="shoes", fields={"shoe_type": "flip_flop"})
        )  # not in the enum

    with pytest.raises(ValidationError) as excinfo:
        await items.create_item(
            user_id, ItemCreate(category="tshirt", fields={"heel_height": "high"})
        )
    assert "unknown field" in str(excinfo.value)  # belongs to shoes, not tshirt

    ok = await items.create_item(
        user_id,
        ItemCreate(category="shoes", fields={"shoe_type": "Boot", "waterproof": "yes"}),
    )
    assert ok.fields == {"shoe_type": "boot", "waterproof": True}


async def test_sixth_color_is_rejected():
    with pytest.raises(Exception):
        ItemCreate(category="tshirt", colors=["a", "b", "c", "d", "e", "f"])


async def test_updating_the_category_revalidates_fields(user_id):
    item = await items.create_item(
        user_id, ItemCreate(category="shoes", fields={"shoe_type": "sneaker"})
    )

    # shoe_type is meaningless on a hat, and hat has no required fields.
    moved = await items.update_item(user_id, item.id, ItemUpdate(category="hat", fields={}))
    assert moved.category == "hat"
    assert moved.fields == {}

    with pytest.raises(ValidationError):
        await items.update_item(user_id, moved.id, ItemUpdate(fields={"shoe_type": "boot"}))


async def test_partial_update_leaves_untouched_fields_alone(user_id):
    item = await items.create_item(
        user_id, ItemCreate(category="jeans", brand="Levi's", tags=["work"], notes="hemmed")
    )
    updated = await items.update_item(user_id, item.id, ItemUpdate(tags=["weekend"]))

    assert updated.tags == ["weekend"]
    assert updated.brand == "Levi's"
    assert updated.notes == "hemmed"


async def test_listing_filters_by_category_and_tags(user_id):
    await items.create_item(user_id, ItemCreate(category="tshirt", tags=["gym", "summer"]))
    await items.create_item(user_id, ItemCreate(category="tshirt", tags=["summer"]))
    await items.create_item(user_id, ItemCreate(category="coat", tags=["winter"]))

    assert len(await items.list_items(user_id, category="tshirt")) == 2
    assert len(await items.list_items(user_id, tags=["summer"])) == 2
    assert len(await items.list_items(user_id, tags=["gym", "summer"])) == 1
    assert len(await items.list_items(user_id, category="coat", tags=["summer"])) == 0


async def test_closet_cap_rejects_the_next_item(user_id, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_items_per_user", 3)

    for _ in range(3):
        await items.create_item(user_id, ItemCreate(category="tshirt"))

    with pytest.raises(ClosetFullError) as excinfo:
        await items.create_item(user_id, ItemCreate(category="tshirt"))
    assert "3-item limit" in str(excinfo.value)


async def test_batch_add_is_partial_success(user_id, monkeypatch):
    """Spec §4.2: a bad item in the middle must not cost the user the good ones."""
    settings = get_settings()
    monkeypatch.setattr(settings, "max_items_per_user", 3)

    results = await items.create_items(
        user_id,
        [
            ItemCreate(category="tshirt", notes="first"),
            ItemCreate(category="shoes", notes="missing required shoe_type"),
            ItemCreate(category="coat", notes="second"),
            ItemCreate(category="hat", notes="third"),
            ItemCreate(category="belt", notes="over the cap"),
        ],
    )

    statuses = [r["status"] for r in results]
    assert statuses == ["created", "failed", "created", "created", "failed"]
    assert results[1]["error"] == "validation_failed"
    assert results[4]["error"] == "closet_full"

    kept = {i.notes for i in await items.list_items(user_id)}
    assert kept == {"first", "second", "third"}


async def test_profile_defaults_then_round_trips(user_id):
    assert await profile.get_profile(user_id) == (await profile.get_profile(user_id))
    default = await profile.get_profile(user_id)
    assert default.home_location is None
    assert default.unit_preference == "fahrenheit"

    saved = await profile.upsert_profile(
        user_id, UserProfileUpdate(home_location="Flint, MI", unit_preference="celsius")
    )
    assert saved.home_location == "Flint, MI"
    assert saved.unit_preference == "celsius"

    # A partial update must not blank out the other field.
    again = await profile.upsert_profile(user_id, UserProfileUpdate(home_location="Ann Arbor, MI"))
    assert again.home_location == "Ann Arbor, MI"
    assert again.unit_preference == "celsius"


async def test_daily_call_cap_blocks_the_eleventh_call(user_id):
    for expected in range(1, 4):
        assert await usage.check_and_increment(user_id, limit=3) == expected

    with pytest.raises(Exception) as excinfo:
        await usage.check_and_increment(user_id, limit=3)
    assert "limit of 3" in str(excinfo.value)

    # The rejected call did not inflate the counter.
    assert await usage.calls_used_today(user_id) == 3


async def test_account_deletion_leaves_nothing_behind(user_id):
    await items.create_item(user_id, ItemCreate(category="tshirt"))
    await profile.upsert_profile(user_id, UserProfileUpdate(home_location="Flint, MI"))
    await usage.check_and_increment(user_id, limit=10)

    deleted = await profile.delete_account(user_id)
    assert deleted["items"] == 1
    assert deleted["user_profile"] == 1
    assert deleted["usage_counters"] == 1
    assert deleted["auth_user"] == 1

    from app.db import app_pool

    async with app_pool().acquire() as conn:
        for table in ("items", "user_profile", "usage_counters"):
            assert await conn.fetchval(
                f"select count(*) from public.{table} where user_id = $1", user_id
            ) == 0
        assert await conn.fetchval("select count(*) from auth.users where id = $1", user_id) == 0


async def test_readonly_pool_cannot_write(user_id):
    """The safe-query backstop, exercised through the pool the MCP tool uses."""
    await items.create_item(user_id, ItemCreate(category="tshirt", brand="Nike"))

    async with readonly_transaction() as conn:
        rows = await conn.fetch(
            "select brand from public.closet_query_view where user_id = $1", user_id
        )
        assert [r["brand"] for r in rows] == ["Nike"]

        with pytest.raises(Exception):
            await conn.execute("update public.items set brand = 'pwned'")
        with pytest.raises(Exception):
            await conn.execute("select * from public.items")
