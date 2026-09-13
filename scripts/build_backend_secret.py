#!/usr/bin/env python3
"""Build the ``weatherpruf/backend`` Secrets Manager JSON from ``backend/.env``.

The AWS secret carries five JSON keys, injected into the ECS task one-by-one
(see ``infra/README.md``). This script is the single source of truth for that
JSON so a rotation never has to be hand-assembled — which is what let a bad
DSN and a dropped key slip in during setup.

Two transforms happen here and nowhere else:

* **Direct → Session pooler.** ``backend/.env`` holds Supabase's *direct*
  connection strings (``db.<ref>.supabase.co``), which are IPv6-only; the ECS
  tasks run in an IPv4-only VPC, so the secret must use the *Session pooler*
  (IPv4). The username gains the project ref (``postgres.<ref>`` /
  ``wardrobe_readonly.<ref>``) and the host becomes the pooler.
* **Percent-encode the password.** Put the *raw* Supabase password in
  ``backend/.env``; asyncpg parses the DSN, so URL-special characters must be
  percent-encoded here (a raw ``:`` or ``@`` in the read-only password crashed
  the container once).

Usage::

    # dry run — prints masked keys only
    python scripts/build_backend_secret.py

    # write the secret JSON (mode 0600) for the AWS CLI to read
    python scripts/build_backend_secret.py --out /tmp/secret.json
    aws secretsmanager put-secret-value --secret-id weatherpruf/backend \
      --secret-string file:///tmp/secret.json --region us-east-2
    rm -f /tmp/secret.json   # then force a new ECS deployment

Nothing here is printed in the clear; ``--out`` is the only path a secret value
travels, and it is written 0600.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from urllib.parse import quote, urlparse

# The Supabase project's Session-pooler host. The region segment is the
# *Supabase* project region (ca-central-1), not the AWS region. Update this if
# the Supabase project ever moves.
POOLER_HOST = "aws-0-ca-central-1.pooler.supabase.com"
POOLER_PORT = 5432  # Session mode (transaction mode is 6543 and drops prepared statements)

# The five keys the ECS task reads (order is cosmetic; ECS reads by name).
SECRET_KEYS = (
    "DATABASE_URL",
    "READONLY_DATABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_JWT_SECRET",
    "MCP_OAUTH_JWT_SIGNING_KEY",
)

# Split a postgres URL into scheme/user/password/host:port/path so a password
# containing ':' or '@' survives (username has no ':'; host has no '@').
_DSN = re.compile(r"^(postgres(?:ql)?://)([^:]+):(.*)@([^@/]+)(/.*)?$")


def read_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw = line.split("=", 1)
            values[key.strip()] = raw.strip().strip('"').strip("'")
    return values


def project_ref(supabase_url: str) -> str:
    host = urlparse(supabase_url).hostname or ""
    ref = host.split(".")[0]
    if not ref:
        raise SystemExit("could not derive the project ref from SUPABASE_URL")
    return ref


def to_pooler(url: str, ref: str) -> str:
    """Rewrite a direct (or already-pooler) DSN to the Session pooler form."""
    match = _DSN.match(url)
    if not match:
        raise SystemExit("a database URL did not match the expected postgres:// shape")
    scheme, user, password, _hostport, path = match.groups()
    base_user = user.split(".", 1)[0]  # idempotent if the ref is already appended
    return f"{scheme}{base_user}.{ref}:{quote(password, safe='')}@{POOLER_HOST}:{POOLER_PORT}{path or '/postgres'}"


def build(env: dict[str, str]) -> dict[str, str]:
    missing = [k for k in ("SUPABASE_URL", *SECRET_KEYS) if k not in env]
    # SUPABASE_JWT_SECRET is legitimately empty; only treat it as missing if the key is absent.
    if missing:
        raise SystemExit(f"backend/.env is missing: {', '.join(missing)}")
    ref = project_ref(env["SUPABASE_URL"])
    return {
        "DATABASE_URL": to_pooler(env["DATABASE_URL"], ref),
        "READONLY_DATABASE_URL": to_pooler(env["READONLY_DATABASE_URL"], ref),
        "SUPABASE_SERVICE_ROLE_KEY": env["SUPABASE_SERVICE_ROLE_KEY"],
        "SUPABASE_JWT_SECRET": env["SUPABASE_JWT_SECRET"],
        "MCP_OAUTH_JWT_SIGNING_KEY": env["MCP_OAUTH_JWT_SIGNING_KEY"],
    }


def masked(secret: dict[str, str]) -> None:
    def hide(url: str) -> str:
        m = _DSN.match(url)
        if not m:
            return "<unparsed>"
        scheme, user, _pw, hostport, path = m.groups()
        return f"{scheme}{user}:***@{hostport}{path or '/postgres'}"

    print("keys:", sorted(secret))
    print("  DATABASE_URL          ->", hide(secret["DATABASE_URL"]))
    print("  READONLY_DATABASE_URL ->", hide(secret["READONLY_DATABASE_URL"]))
    for key in ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET", "MCP_OAUTH_JWT_SIGNING_KEY"):
        value = secret[key]
        print(f"  {key} -> len={len(value)}{' (empty)' if value == '' else ''}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="backend/.env", help="path to the env file")
    parser.add_argument("--out", help="write the secret JSON here (mode 0600); omit for a dry run")
    args = parser.parse_args()

    secret = build(read_env(args.env))
    masked(secret)

    if args.out:
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(secret, handle)
        print(f"\nwrote {args.out} (0600) with {len(secret)} keys")
    else:
        print("\n(dry run — pass --out <path> to write the JSON for the AWS CLI)")


if __name__ == "__main__":
    main()
