#!/usr/bin/env python3
"""Seed a closet with realistic demo data (spec §7, Ticket 6).

Enough breadth to make filtering demos meaningful: items across most
categories, with varied colours, warmth ratings, formality and tags — including
the differentiating details the assistant is meant to pick up on, like
"floral", "multi-color" and notes about fit or fabric.

    scripts/seed_demo_data.py --user-id <uuid>       # an existing user
    scripts/seed_demo_data.py --create-user          # local: make one and print it
    scripts/seed_demo_data.py --user-id <uuid> --replace

Run it with the backend virtualenv's interpreter so the app package is
importable, e.g. `backend/.venv/bin/python scripts/seed_demo_data.py ...`.
It goes through the normal service layer, so every item is validated against
its category's field template exactly as a real write would be.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # so the .env file next to the app is picked up

from app import db  # noqa: E402
from app.models import ItemCreate, UserProfileUpdate  # noqa: E402
from app.services import items as items_service  # noqa: E402
from app.services import profile as profile_service  # noqa: E402

# (category, colors, brand, warmth, formality, tags, notes, fields)
DEMO_ITEMS: list[tuple] = [
    # --- tops ---------------------------------------------------------------
    ("tshirt", ["white"], "Uniqlo", 1, "casual", ["everyday", "basic"],
     "Slightly boxy, holds up well in the wash.",
     {"sleeve_length": "short", "neckline": "crew", "material": "cotton"}),
    ("tshirt", ["navy", "white"], "Everlane", 1, "casual", ["stripes", "weekend"],
     "Breton stripe. Reads a bit smarter than a plain tee.",
     {"sleeve_length": "short", "neckline": "crew", "material": "cotton"}),
    ("tshirt", ["black"], "Nike", 1, "athletic", ["gym", "moisture-wicking"],
     "Only shirt that survives a hot run.",
     {"sleeve_length": "short", "neckline": "crew", "material": "polyester"}),
    ("polo", ["forest green"], "Lacoste", 2, "smart_casual", ["summer", "golf"],
     None, {"sleeve_length": "short", "material": "pique cotton"}),
    ("shirt", ["light blue"], "Charles Tyrwhitt", 2, "formal", ["work", "interview"],
     "Non-iron. The safe choice when it matters.",
     {"sleeve_length": "long", "material": "cotton poplin"}),
    ("shirt", ["white", "red"], "J.Crew", 2, "smart_casual", ["gingham", "multi-color"],
     "Small check — busy under a patterned jacket.",
     {"sleeve_length": "long", "material": "cotton"}),
    ("blouse", ["cream"], "& Other Stories", 2, "smart_casual", ["floral", "date-night"],
     "Floral print, silk-feel. Gets compliments every time.",
     {"sleeve_length": "long", "neckline": "v_neck", "material": "viscose"}),
    ("tank_top", ["black"], "Uniqlo", 1, "casual", ["layering", "hot-weather"],
     None, {"neckline": "scoop", "material": "cotton"}),

    # --- knitwear and mid-layers -------------------------------------------
    ("sweater", ["oatmeal"], "Everlane", 4, "smart_casual", ["autumn", "cosy"],
     "Merino. Warm without being bulky under a coat.",
     {"sleeve_length": "long", "neckline": "crew", "material": "merino wool"}),
    ("sweater", ["charcoal"], "COS", 4, "smart_casual", ["winter", "work"],
     None, {"sleeve_length": "long", "neckline": "turtleneck", "material": "lambswool"}),
    ("cardigan", ["camel"], "Uniqlo", 3, "smart_casual", ["layering", "office-cold"],
     "Lives on the back of a chair at work.",
     {"sleeve_length": "long", "material": "cotton blend"}),
    ("hoodie", ["heather grey"], "Champion", 3, "casual", ["weekend", "lounge"],
     None, {"sleeve_length": "long", "material": "cotton fleece", "closure": "pullover"}),
    ("sweatshirt", ["navy"], "Uniqlo", 3, "casual", ["weekend"],
     None, {"sleeve_length": "long", "material": "cotton"}),
    ("vest", ["olive"], "Patagonia", 3, "casual", ["layering", "commute"],
     "Fleece-lined. Good for that 50°F ambiguous morning.",
     {"material": "recycled polyester", "closure": "zip"}),

    # --- outerwear ----------------------------------------------------------
    ("jacket", ["olive"], "Barbour", 4, "smart_casual", ["rain", "commute", "autumn"],
     "Waxed cotton. Needs re-waxing each autumn but sheds a drizzle beautifully.",
     {"sleeve_length": "long", "material": "waxed cotton", "waterproof": True,
      "closure": "zip"}),
    ("jacket", ["black"], "Uniqlo", 3, "casual", ["packable", "travel"],
     "Folds into its own pocket. Lives in a bag.",
     {"sleeve_length": "long", "material": "nylon", "waterproof": False, "closure": "zip"}),
    ("coat", ["camel"], "Max Mara", 5, "formal", ["winter", "smart", "wool"],
     "The good coat. Wool, no hood — fine in cold and dry, useless in rain.",
     {"sleeve_length": "long", "material": "wool", "waterproof": False, "length": "knee",
      "closure": "button"}),
    ("coat", ["black"], "The North Face", 5, "casual", ["winter", "snow", "warmest"],
     "Down. Overkill above freezing but nothing else works below 20°F.",
     {"sleeve_length": "long", "material": "down", "waterproof": True, "length": "knee",
      "closure": "zip"}),
    ("blazer", ["navy"], "Suitsupply", 3, "formal", ["work", "interview", "dinner"],
     "Unstructured, so it works with jeans as well as trousers.",
     {"sleeve_length": "long", "material": "wool blend"}),

    # --- bottoms ------------------------------------------------------------
    ("jeans", ["indigo"], "Levi's", 3, "casual", ["everyday", "dark-wash"],
     "Dark enough to pass as smart casual with the blazer.",
     {"fit": "slim", "rise": "mid", "material": "denim", "inseam_inches": 32}),
    ("jeans", ["black"], "Uniqlo", 3, "smart_casual", ["evening", "date-night"],
     None, {"fit": "slim", "rise": "mid", "material": "stretch denim", "inseam_inches": 32}),
    ("trousers", ["charcoal"], "Suitsupply", 3, "formal", ["work", "interview"],
     "Pairs with the navy blazer.",
     {"fit": "slim", "rise": "mid", "material": "wool", "inseam_inches": 32}),
    ("trousers", ["khaki"], "Dockers", 2, "smart_casual", ["work", "warm-weather"],
     None, {"fit": "regular", "rise": "mid", "material": "cotton twill", "inseam_inches": 32}),
    ("shorts", ["navy"], "J.Crew", 1, "casual", ["summer", "hot-weather"],
     None, {"fit": "regular", "rise": "mid", "material": "cotton"}),
    ("lower", ["black"], "Lululemon", 2, "athletic", ["gym", "running", "cold-run"],
     "Warm enough to run in down to about 35°F.",
     {"fit": "slim", "rise": "high", "material": "nylon blend", "inseam_inches": 28}),
    ("skirt", ["black"], "COS", 2, "smart_casual", ["work", "versatile"],
     None, {"fit": "regular", "rise": "high", "material": "wool blend", "length": "midi"}),

    # --- one-pieces ---------------------------------------------------------
    ("dress", ["navy", "white"], "Reformation", 2, "smart_casual",
     ["floral", "multi-color", "date-night", "summer"],
     "Floral, ditsy print. The one I reach for when it's warm and I want to look put together.",
     {"sleeve_length": "short", "neckline": "v_neck", "material": "viscose", "length": "midi"}),
    ("dress", ["black"], "COS", 2, "formal", ["evening", "wedding-guest", "versatile"],
     "Plain enough to re-wear with different accessories.",
     {"sleeve_length": "sleeveless", "neckline": "crew", "material": "crepe", "length": "midi"}),
    ("jumpsuit", ["olive"], "Madewell", 2, "casual", ["weekend", "one-and-done"],
     None, {"sleeve_length": "short", "material": "tencel"}),
    ("two_piece_suit", ["navy"], "Suitsupply", 3, "formal", ["wedding", "interview", "formal"],
     "The full suit. Jacket also works on its own.",
     {"suit_pieces": "jacket_trousers", "material": "wool"}),

    # --- shoes --------------------------------------------------------------
    ("shoes", ["white"], "Common Projects", 2, "smart_casual", ["everyday", "clean"],
     "Leather sneakers. Smart enough for dinner, ruined by rain.",
     {"shoe_type": "sneaker", "heel_height": "flat", "material": "leather",
      "waterproof": False}),
    ("shoes", ["black"], "Nike", 1, "athletic", ["gym", "running"],
     None, {"shoe_type": "sneaker", "heel_height": "flat", "material": "mesh",
            "waterproof": False}),
    ("shoes", ["brown"], "Blundstone", 4, "casual", ["rain", "winter", "commute"],
     "Chelsea boots. What I actually wear when it's wet.",
     {"shoe_type": "boot", "heel_height": "low", "material": "leather", "waterproof": True}),
    ("shoes", ["black"], "Loake", 3, "formal", ["work", "interview", "wedding"],
     "Oxfords. Need a shine before anything important.",
     {"shoe_type": "formal", "heel_height": "low", "material": "leather",
      "waterproof": False}),
    ("shoes", ["tan"], "Birkenstock", 1, "casual", ["summer", "hot-weather"],
     None, {"shoe_type": "sandal", "heel_height": "flat", "material": "suede",
            "waterproof": False}),

    # --- accessories --------------------------------------------------------
    ("belt", ["brown"], "Anderson's", 1, "smart_casual", ["everyday"],
     "Matches the Blundstones.", {"material": "leather", "accessory_size": "34"}),
    ("belt", ["black"], "Loake", 1, "formal", ["work"],
     None, {"material": "leather", "accessory_size": "34"}),
    ("hat", ["charcoal"], "Carhartt", 3, "casual", ["winter", "cold"],
     "Beanie. Anything below 40°F and it comes out.",
     {"material": "acrylic", "waterproof": False, "accessory_size": "one size"}),
    ("hat", ["natural"], None, 1, "casual", ["summer", "sun"],
     "Straw. Purely for sun.", {"material": "straw", "accessory_size": "one size"}),
    ("scarf", ["burgundy", "grey"], "Acne Studios", 4, "smart_casual",
     ["winter", "multi-color"],
     "Oversized wool. Doubles as a blanket on flights.",
     {"material": "wool", "accessory_size": "oversized"}),
    ("tie", ["navy", "white"], "Drake's", 1, "formal", ["work", "interview", "stripes"],
     "Repp stripe. Goes with the navy suit and the light blue shirt.",
     {"material": "silk", "accessory_size": "regular"}),
]


async def seed(user_id: uuid.UUID, *, replace: bool, home_location: str) -> None:
    if replace:
        existing = await items_service.list_items(user_id, limit=200)
        for item in existing:
            await items_service.delete_item(user_id, item.id)
        print(f"removed {len(existing)} existing item(s)")

    payloads = [
        ItemCreate(
            category=category,
            colors=colors,
            brand=brand,
            warmth_rating=warmth,
            formality=formality,
            tags=tags,
            notes=notes,
            fields=fields,
        )
        for category, colors, brand, warmth, formality, tags, notes, fields in DEMO_ITEMS
    ]

    results = await items_service.create_items(user_id, payloads[:20])
    results += await items_service.create_items(user_id, payloads[20:])

    created = [r for r in results if r["status"] == "created"]
    failed = [r for r in results if r["status"] != "created"]

    for failure in failed:
        source = DEMO_ITEMS[failure["index"]]
        print(f"  FAILED {source[0]}: {failure['message']}", file=sys.stderr)

    await profile_service.upsert_profile(
        user_id, UserProfileUpdate(home_location=home_location, unit_preference="fahrenheit")
    )

    print(f"seeded {len(created)} item(s) across {len({d[0] for d in DEMO_ITEMS})} categories")
    print(f"home_location set to {home_location!r}")
    if failed:
        sys.exit(f"{len(failed)} item(s) failed to seed")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user-id", help="seed this existing user's closet")
    group.add_argument(
        "--create-user",
        action="store_true",
        help="create a new auth.users row first (local development only)",
    )
    parser.add_argument(
        "--replace", action="store_true", help="delete the user's existing items first"
    )
    parser.add_argument("--home-location", default="Flint, MI")
    args = parser.parse_args()

    await db.connect()
    try:
        if args.create_user:
            user_id = uuid.uuid4()
            async with db.app_pool().acquire() as conn:
                await conn.execute(
                    "insert into auth.users (id, email) values ($1, $2)",
                    user_id,
                    f"{user_id}@example.test",
                )
            print(f"created user {user_id}")
        else:
            user_id = uuid.UUID(args.user_id)

        await seed(user_id, replace=args.replace, home_location=args.home_location)
    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
