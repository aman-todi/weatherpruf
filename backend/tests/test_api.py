"""Ticket 2 DoD: the full create -> list -> filter -> update -> delete flow,
the caps, and an account deletion that leaves nothing behind.

These drive the real app through ASGI, including the real auth dependency —
tokens are minted the same way scripts/make_test_token.py does, so there is no
test-only auth bypass anywhere in the suite.
"""

from __future__ import annotations

import time
import uuid

import httpx
import jwt
import pytest
import pytest_asyncio

from app.config import get_settings
from app.main import create_app

SECRET = "test-secret-at-least-32-bytes-long!!"


def _token(user_id: uuid.UUID) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": str(user_id),
            "email": f"{user_id}@example.test",
            "role": "authenticated",
            "aud": "authenticated",
            "iat": now,
            "exp": now + 3600,
        },
        SECRET,
        algorithm="HS256",
    )


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest_asyncio.fixture
async def auth(user_id):
    return {"Authorization": f"Bearer {_token(user_id)}"}


async def test_health_needs_no_token(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/items"),
        ("post", "/api/items"),
        ("get", "/api/categories"),
        ("get", "/api/profile"),
        ("get", "/api/me"),
        ("delete", "/api/account"),
    ],
)
async def test_every_api_route_requires_a_token(client, method, path):
    assert (await getattr(client, method)(path)).status_code == 401


async def test_a_garbage_token_is_rejected(client):
    response = await client.get("/api/items", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_categories_drive_the_dynamic_form(client, auth):
    response = await client.get("/api/categories", headers=auth)
    assert response.status_code == 200

    categories = response.json()
    assert len(categories) == 26
    assert [c["sort_order"] for c in categories] == sorted(c["sort_order"] for c in categories)

    shoes = next(c for c in categories if c["id"] == "shoes")
    shoe_type = next(f for f in shoes["fields"] if f["field_name"] == "shoe_type")
    assert shoe_type == {
        "field_name": "shoe_type",
        "field_type": "enum",
        "required": True,
        "allowed_values": ["sneaker", "boot", "sandal", "formal", "loafer", "heel", "other"],
        "display_order": 10,
    }

    # Non-enum fields carry no allowed_values, so the form knows to render an input.
    material = next(f for f in shoes["fields"] if f["field_name"] == "material")
    assert material["field_type"] == "text"
    assert material["allowed_values"] is None


async def test_full_item_lifecycle(client, auth):
    created = await client.post(
        "/api/items",
        headers=auth,
        json={
            "category": "jacket",
            "colors": ["Olive", "black"],
            "brand": "Barbour",
            "warmth_rating": 4,
            "formality": "smart_casual",
            "tags": ["Rain", "commute"],
            "notes": "waxed cotton, needs re-waxing each autumn",
            "fields": {"waterproof": "true", "sleeve_length": "long"},
        },
    )
    assert created.status_code == 201
    item = created.json()
    assert item["colors"] == ["olive", "black"]
    assert item["tags"] == ["rain", "commute"]
    assert item["fields"] == {"waterproof": True, "sleeve_length": "long"}

    item_id = item["id"]

    fetched = await client.get(f"/api/items/{item_id}", headers=auth)
    assert fetched.status_code == 200
    assert fetched.json()["brand"] == "Barbour"

    listed = (await client.get("/api/items", headers=auth)).json()
    assert listed["total"] == 1
    assert [i["id"] for i in listed["items"]] == [item_id]

    patched = await client.patch(
        f"/api/items/{item_id}", headers=auth, json={"warmth_rating": 5, "tags": ["winter"]}
    )
    assert patched.status_code == 200
    assert patched.json()["warmth_rating"] == 5
    assert patched.json()["tags"] == ["winter"]
    assert patched.json()["brand"] == "Barbour"  # untouched

    assert (await client.delete(f"/api/items/{item_id}", headers=auth)).status_code == 204
    assert (await client.get(f"/api/items/{item_id}", headers=auth)).status_code == 404
    assert (await client.get("/api/items", headers=auth)).json()["total"] == 0


async def test_filtering_by_category_and_tags(client, auth):
    for payload in (
        {"category": "tshirt", "tags": ["gym", "summer"]},
        {"category": "tshirt", "tags": ["summer"]},
        {"category": "coat", "tags": ["winter"]},
    ):
        assert (await client.post("/api/items", headers=auth, json=payload)).status_code == 201

    async def count(params: str) -> int:
        return len((await client.get(f"/api/items?{params}", headers=auth)).json()["items"])

    assert await count("category=tshirt") == 2
    assert await count("tags=summer") == 2
    assert await count("tags=gym&tags=summer") == 1  # AND, not OR
    assert await count("category=coat&tags=summer") == 0

    # `total` stays the whole closet even when the list is filtered.
    filtered = (await client.get("/api/items?category=coat", headers=auth)).json()
    assert len(filtered["items"]) == 1
    assert filtered["total"] == 3


async def test_tags_are_derived_from_items(client, auth):
    """Tags have no registry table, so the tag list must follow the items --
    including a tag ceasing to exist when the last item carrying it goes."""
    assert (await client.get("/api/tags", headers=auth)).json() == []

    first = await client.post(
        "/api/items", headers=auth, json={"category": "tshirt", "tags": ["Summer", "gym"]}
    )
    await client.post("/api/items", headers=auth, json={"category": "shorts", "tags": ["summer"]})

    assert (await client.get("/api/tags", headers=auth)).json() == [
        {"tag": "summer", "item_count": 2},  # case-folded on write, so these are one tag
        {"tag": "gym", "item_count": 1},
    ]

    await client.delete(f"/api/items/{first.json()['id']}", headers=auth)
    assert (await client.get("/api/tags", headers=auth)).json() == [
        {"tag": "summer", "item_count": 1}
    ]


async def test_tags_do_not_leak_between_users(client, user_id, other_user_id):
    mine = {"Authorization": f"Bearer {_token(user_id)}"}
    theirs = {"Authorization": f"Bearer {_token(other_user_id)}"}

    await client.post("/api/items", headers=mine, json={"category": "hat", "tags": ["private"]})

    assert (await client.get("/api/tags", headers=theirs)).json() == []
    assert [t["tag"] for t in (await client.get("/api/tags", headers=mine)).json()] == ["private"]


async def test_items_are_scoped_to_their_owner(client, user_id, other_user_id):
    mine = {"Authorization": f"Bearer {_token(user_id)}"}
    theirs = {"Authorization": f"Bearer {_token(other_user_id)}"}

    created = await client.post("/api/items", headers=mine, json={"category": "hat"})
    item_id = created.json()["id"]

    assert (await client.get("/api/items", headers=theirs)).json()["total"] == 0
    assert (await client.get(f"/api/items/{item_id}", headers=theirs)).status_code == 404
    assert (
        await client.patch(f"/api/items/{item_id}", headers=theirs, json={"brand": "x"})
    ).status_code == 404
    assert (await client.delete(f"/api/items/{item_id}", headers=theirs)).status_code == 404

    # Still there for its owner.
    assert (await client.get(f"/api/items/{item_id}", headers=mine)).status_code == 200


async def test_a_sixth_color_is_rejected(client, auth):
    response = await client.post(
        "/api/items",
        headers=auth,
        json={"category": "tshirt", "colors": ["a", "b", "c", "d", "e", "f"]},
    )
    assert response.status_code == 422
    body = response.json()
    # Body-validation failures use the same envelope as domain errors, so a
    # client only has to understand one error shape.
    assert body["error"] == "validation_failed"
    assert "at most 5 colors are allowed" in body["message"]
    assert body["details"]["problems"][0]["field"] == "colors"


async def test_unknown_category_and_bad_fields_are_rejected(client, auth):
    unknown = await client.post("/api/items", headers=auth, json={"category": "cape"})
    assert unknown.status_code == 422
    assert unknown.json()["error"] == "unknown_category"

    missing_required = await client.post("/api/items", headers=auth, json={"category": "shoes"})
    assert missing_required.status_code == 422
    assert "shoe_type" in missing_required.json()["message"]

    wrong_enum = await client.post(
        "/api/items", headers=auth, json={"category": "shoes", "fields": {"shoe_type": "clog"}}
    )
    assert wrong_enum.status_code == 422
    assert wrong_enum.json()["details"]["allowed_values"]


async def test_the_201st_item_is_rejected(client, auth, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_items_per_user", 2)

    for _ in range(2):
        assert (
            await client.post("/api/items", headers=auth, json={"category": "tshirt"})
        ).status_code == 201

    over = await client.post("/api/items", headers=auth, json={"category": "tshirt"})
    assert over.status_code == 409
    assert over.json()["error"] == "closet_full"
    assert "2-item limit" in over.json()["message"]


async def test_profile_round_trip(client, auth):
    default = (await client.get("/api/profile", headers=auth)).json()
    assert default == {"home_location": None, "unit_preference": "fahrenheit"}

    saved = await client.put(
        "/api/profile",
        headers=auth,
        json={"home_location": "Flint, MI", "unit_preference": "celsius"},
    )
    assert saved.json() == {"home_location": "Flint, MI", "unit_preference": "celsius"}

    partial = await client.put("/api/profile", headers=auth, json={"home_location": "Detroit, MI"})
    assert partial.json() == {"home_location": "Detroit, MI", "unit_preference": "celsius"}

    bad = await client.put("/api/profile", headers=auth, json={"unit_preference": "kelvin"})
    assert bad.status_code == 422
    assert bad.json()["error"] == "validation_failed"

    unknown_key = await client.put("/api/profile", headers=auth, json={"timezone": "UTC"})
    assert unknown_key.status_code == 422  # extra="forbid" on the update models


async def test_me_reports_the_connector_url_and_usage(client, auth, user_id):
    await client.post("/api/items", headers=auth, json={"category": "belt"})

    me = (await client.get("/api/me", headers=auth)).json()
    assert me["user_id"] == str(user_id)
    assert me["item_count"] == 1
    assert me["item_limit"] == get_settings().max_items_per_user
    assert me["calls_used_today"] == 0
    assert me["daily_call_limit"] == get_settings().daily_mcp_call_limit
    assert me["mcp_url"].endswith("/mcp")


async def test_account_deletion_leaves_no_residual_rows(client, auth, user_id):
    await client.post("/api/items", headers=auth, json={"category": "tshirt"})
    await client.put("/api/profile", headers=auth, json={"home_location": "Flint, MI"})

    from app.services import usage

    await usage.check_and_increment(user_id, limit=10)

    response = await client.delete("/api/account", headers=auth)
    assert response.status_code == 200
    assert response.json()["deleted"] == {
        "items": 1,
        "user_profile": 1,
        "usage_counters": 1,
        "auth_user": 1,
    }

    from app.db import app_pool

    async with app_pool().acquire() as conn:
        for table in ("items", "user_profile", "usage_counters"):
            remaining = await conn.fetchval(
                f"select count(*) from public.{table} where user_id = $1", user_id
            )
            assert remaining == 0
        users = await conn.fetchval("select count(*) from auth.users where id = $1", user_id)
        assert users == 0
