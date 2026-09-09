#!/usr/bin/env python3
"""Mint a Supabase-shaped HS256 test token for local development.

    ./scripts/make_test_token.py                      # random user id
    ./scripts/make_test_token.py --sub <uuid>         # a specific user
    ./scripts/make_test_token.py --seed-user          # also insert into auth.users

The token is the same shape the FastAPI auth dependency expects from a real
Supabase session JWT or OAuth access token, so it exercises the real code path
rather than a test-only bypass.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import uuid

try:
    import jwt
except ImportError:
    sys.exit("PyJWT is required: run this with backend/.venv/bin/python")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sub", default=None, help="user id to put in the token (default: random)")
    parser.add_argument("--email", default=None)
    parser.add_argument("--ttl", type=int, default=3600, help="lifetime in seconds")
    parser.add_argument(
        "--secret",
        default=os.environ.get("SUPABASE_JWT_SECRET", "local-dev-secret-change-me"),
        help="HS256 signing secret; must match SUPABASE_JWT_SECRET",
    )
    parser.add_argument("--issuer", default=os.environ.get("SUPABASE_URL", ""))
    parser.add_argument(
        "--seed-user",
        action="store_true",
        help="also insert the user into auth.users in the local database",
    )
    parser.add_argument("--db", default=os.environ.get("LOCAL_DB", "wardrobe_dev"))
    parser.add_argument("--quiet", action="store_true", help="print only the token")
    args = parser.parse_args()

    sub = args.sub or str(uuid.uuid4())
    email = args.email or f"{sub[:8]}@example.test"
    now = int(time.time())

    claims = {
        "sub": sub,
        "email": email,
        "role": "authenticated",
        "aud": "authenticated",
        "iat": now,
        "exp": now + args.ttl,
        "app_metadata": {"provider": "email"},
        "user_metadata": {},
    }
    if args.issuer:
        claims["iss"] = f"{args.issuer.rstrip('/')}/auth/v1"

    if args.seed_user:
        subprocess.run(
            [
                "su", "postgres", "-c",
                f"psql -q -d {args.db} -c \"insert into auth.users (id, email) "
                f"values ('{sub}', '{email}') on conflict (id) do nothing\"",
            ],
            check=True,
        )

    token = jwt.encode(claims, args.secret, algorithm="HS256")
    if args.quiet:
        print(token)
    else:
        print(f"user_id: {sub}")
        print(f"email:   {email}")
        print(f"token:   {token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
