# weatherpruf

An MCP-powered app that dresses you every day from your digital closet, based on the weather and
your plan for the day.

The web app manages the closet. The **day-to-day interface is an AI assistant** (Claude.ai)
connected over MCP — "add a t-shirt to my closet", "what should I wear for my dinner date tonight?"
all happen in chat, not in the UI.

A design principle runs through the whole thing: **the backend does no outfit reasoning and fetches
no weather.** It exposes the closet's structure and one safe way to query it. The assistant finds
the weather itself, reads the taxonomy from `get_closet_structure`, builds a filter, and does the
combining. That keeps the server to CRUD plus a single guarded query path.

Full design: [`wardrobe-mcp-app-v1-spec.md`](wardrobe-mcp-app-v1-spec.md).

## Layout

```
db/
  migrations/     numbered SQL, applied in filename order — schema, seeds, RLS, read-only role
  local/          auth-schema shim so the real migrations run on a bare local Postgres
  tests/          SQL assertions for RLS and the read-only role's grants
backend/
  app/
    config.py     settings from the environment
    db.py         two pools: the app role (RLS-scoped) and the read-only query role
    auth.py       Supabase JWT validation, shared by REST and MCP
    models.py     request/response shapes shared by REST and MCP
    errors.py     domain errors each surface renders in its own way
    services/     the shared data-access layer — both surfaces go through it
    api/          REST routes for the web app
    mcp_server/   the FastMCP sub-app mounted at /mcp
  tests/
frontend/         React + Vite web app
infra/            Dockerfile and ECS Express Mode config
scripts/          local database and dev helpers
```

`services/` exists so `add_item` over MCP and `POST /api/items` cannot drift: the 200-item cap,
the five-colour limit, category-field validation and RLS scoping are enforced in one place.

## Local development

Requires Python 3.11+, Node 20+, and a local PostgreSQL 16.

### 1. Database

```bash
./scripts/db_apply.sh wardrobe_dev my-readonly-password
```

That recreates `wardrobe_dev`, applies the local auth shim, then every migration in order, and
prints the two connection strings to put in `backend/.env`.

To check the security properties hold — RLS blocks cross-user reads and writes, the column
constraints bite, and the read-only role really cannot write anything:

```bash
./scripts/db_verify.sh
```

### 2. Backend

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,mcp]'
cp .env.example .env      # then fill in the two connection strings
uvicorn app.main:app --reload
```

`GET /health` reports the status of both database pools.

```bash
pytest                    # runs against the local wardrobe_dev database
```

### 3. A token for manual API calls

There is no login screen to click through when you are poking at the API with `curl`, so mint a
token in the same shape Supabase issues:

```bash
backend/.venv/bin/python scripts/make_test_token.py --seed-user
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/items
```

`--seed-user` also inserts the user into the local `auth.users` table, which the foreign keys
require. The token is HS256-signed with `SUPABASE_JWT_SECRET`; the auth dependency verifies it
through exactly the same code path as a real Supabase token, with no test-only bypass.

### 4. Demo data

```bash
backend/.venv/bin/python scripts/seed_demo_data.py --create-user
backend/.venv/bin/python scripts/seed_demo_data.py --user-id <uuid> --replace
```

41 items across all 26 categories with varied colours, warmth, formality and
tags — including the differentiating details the assistant is meant to notice,
like `floral`, `multi-color`, and notes about what actually keeps rain out.
Enough breadth for filtering demos to mean something.

## Deployment

See [`infra/README.md`](infra/README.md). One image serves both the REST API
and the MCP sub-app, deployed as an ECS Express Mode service by
`.github/workflows/deploy.yml`. Read the caveat at the top of that file about
what has and has not been verified.

## Against a real Supabase project

Run the files in `db/migrations/` in filename order through the SQL editor or the Supabase CLI.
**Skip `db/local/00_auth_shim.sql`** — Supabase already provides `auth.users` and `auth.uid()`, and
the shim would collide with them.

After `0004_readonly_role.sql`, give the read-only role a password (it is created without one so no
secret lands in the repo) and use the result as `READONLY_DATABASE_URL`:

```sql
alter role wardrobe_readonly with login password '<generated>';
```

Keep that credential separate from `DATABASE_URL`. The read-only role is the actual security
backstop behind `query_closet_items` — the AST validation in front of it is defence in depth, not
the boundary.

## Environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Application role. Every request runs inside `set local role authenticated`, so RLS still applies. |
| `READONLY_DATABASE_URL` | The `wardrobe_readonly` role. `SELECT` on `closet_query_view` only. Separate secret. |
| `SUPABASE_URL` | Project URL. Derives the JWKS endpoint and the expected token issuer. |
| `SUPABASE_SERVICE_ROLE_KEY` | Used only by the account-deletion flow, to delete the Supabase auth user. |
| `SUPABASE_JWT_SECRET` | Legacy HS256 project secret. Also what `make_test_token.py` signs with locally. |
| `PUBLIC_BASE_URL` | Public origin; `/mcp` is appended to give users the connector URL. |
| `CORS_ALLOW_ORIGINS` | Comma-separated origins for the web app. |
| `MAX_ITEMS_PER_USER` | Closet cap. Default 200. |
| `DAILY_MCP_CALL_LIMIT` | Assistant calls per user per UTC day. Default 10 — deliberately tight; raising it is a one-line change. |

## REST API

All routes are under `/api` and require `Authorization: Bearer <supabase access token>`.

| Method | Path | |
|---|---|---|
| `GET` | `/api/categories` | Every category with its field template — drives the dynamic form. |
| `GET` | `/api/items` | Filter with `category`, repeatable `tags` (AND), `limit`, `offset`. Returns `{items, total}`, where `total` is the whole closet. |
| `POST` | `/api/items` | 201 with the created item. |
| `GET` `PATCH` `DELETE` | `/api/items/{id}` | `PATCH` is partial; `DELETE` returns 204. |
| `GET` `PUT` | `/api/profile` | Never 404s — a user with no row gets the defaults. |
| `GET` | `/api/me` | Account summary, today's usage, and the connector URL for the Connect page. |
| `GET` | `/api/limits` | The caps that apply to this account. |
| `DELETE` | `/api/account` | Items, profile, usage counters, and the Supabase auth user. |

Errors — domain and request-validation alike — come back in one envelope:

```json
{"error": "closet_full", "message": "Your closet is at its 200-item limit...", "details": {}}
```

`error` is a stable code (`closet_full`, `validation_failed`, `unknown_category`, `not_found`,
`daily_limit_reached`, `unsafe_query`); `message` is written to be shown to a person.

## Status

| Ticket | Scope | State |
|---|---|---|
| 1 | Schema, RLS, read-only role, FastAPI skeleton, shared services | done |
| 2 | REST API for closet management | done |
| 3 | MCP server: tools, safe query execution, rate limiting | in progress |
| 4 | React + Vite frontend | in progress |
| 5 | Docker + ECS Express Mode | scaffolded — see `infra/README.md` for what is unverified |
| 6 | End-to-end verification and polish | seed script done; the Claude.ai smoke test needs a deployed instance |
