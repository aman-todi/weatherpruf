# weatherpruf — v1 Spec (reverse-engineered)

*This is the design of weatherpruf as it was actually built and shipped, written after the fact.
Earlier drafts of this file were a forward-looking build spec organised around tickets and revisions;
this version replaces that framing entirely. It describes what exists, why each decision went the way
it did, and — because a lot of the interesting decisions only became visible during the build — what
the plan did not anticipate and how the shipped system differs from it.*

**What this document is for.** Three things. (1) A single place to understand how weatherpruf is put
together without reading the whole codebase. (2) A record of the reasoning behind the non-obvious
choices, so a future change does not quietly undo one. (3) A template: the shape of this
project — an MCP connector as the primary interface, a thin web app beside it, one container, RLS as
the security spine — is reusable, and the section headings here are roughly the order in which those
decisions have to be made for the next tool like it.

**Where the truth actually lives.** This spec is the map, not the territory. The code is the source
of truth for behaviour; the runbooks are the source of truth for the deployed system:

- [`README.md`](README.md) — local development, environment variables, the REST surface.
- [`docs/architecture.md`](docs/architecture.md) — the component diagram and the two request paths.
- [`docs/networking.md`](docs/networking.md) — how a request reaches the app, TLS, the pooler.
- [`docs/setup-supabase.md`](docs/setup-supabase.md) — the one-time Supabase setup.
- [`infra/README.md`](infra/README.md) — the AWS deploy, IAM, secrets, and the MCP OAuth proxy.

The backend cites this spec by section number (`spec §4.1`, `spec §5`, …) in ~30 places, so the
section numbering below is deliberately stable across this rewrite even where the prose changed.

**Status: v1 is live** at **https://app.weatherpruf.live** — web app, REST API, and MCP connector
(`https://app.weatherpruf.live/mcp/`, trailing slash) all served from one container on ECS Express
Mode in `us-east-2`, backed by Supabase. Everything the original plan left "to be verified against a
deployed instance" has been verified against the deployed instance.

---

## 0. Background

weatherpruf is a personal project (learning + portfolio; expected traffic: single-digit concurrent
users). The core idea: a web app manages a user's digital closet, but the **primary interface for
day-to-day use is an AI assistant** (Claude.ai) connected via MCP (Model Context Protocol) — not the
web app. The web app exists to onboard, manage account/auth, browse and correct the closet visually,
and hand the user the connector URL. All day-to-day interaction ("add a t-shirt to my closet", "what
should I wear for my dinner date tonight?") happens through the assistant, which calls MCP tools
against this backend.

The design principle that runs through the whole thing: **the backend does no outfit reasoning, and
it fetches no weather.** It exposes the closet's structure and one safe way to query it. The
assistant is expected to already know or ask for the user's location, find current weather with its
own capabilities (general knowledge, web search — not this app's job), read the closet's
category/field taxonomy from `get_closet_structure`, build a filtering query against that structure,
and combine the returned items into a recommendation itself. That keeps the backend to CRUD plus one
guarded query-execution path — and it is what makes weatherpruf a good MCP-first design rather than a
chatbot with a database: the model does the reasoning it is good at, the server does the data custody
it is good at, and neither reaches into the other's job.

### Decisions locked in before any code

These were settled up front and did not change during the build:

- **Auth**: Supabase Auth for both the web app's login/session and the OAuth 2.1 authorization the
  connector uses. Tokens carry standard Supabase claims and work directly with Row Level Security
  (RLS), so the same policies protect the REST and MCP surfaces alike. This relies on Dynamic Client
  Registration (DCR) — the deprecated-but-functional MCP client onboarding path — rather than Client
  ID Metadata Documents (CIMD). A known, accepted limitation; if Supabase ships CIMD support later,
  moving to it is configuration, not a rewrite.
- **Database**: Supabase Postgres.
- **MCP server**: FastMCP (v4), mounted directly inside the FastAPI app as an ASGI sub-application —
  one process, one deploy, a shared auth and data-access layer between REST and MCP.
- **Frontend**: React + Vite + TypeScript.
- **No server-side outfit reasoning, no server-side weather.** The assistant does both with its own
  knowledge/tools; the backend only serves closet data.
- **Claude integration surface**: a remote MCP server with OAuth (a connector), not a plugin — see
  §1.

### Decisions that were forced by the build, not the plan

These are the ones a future project should expect to hit too, because the plan could not have known
them:

- **Compute is AWS ECS Express Mode**, chosen as the current AWS-recommended replacement for App
  Runner (which stopped accepting new customers as of 30 April 2026). What the plan could not know is
  that this account is on the **new AWS experience with a managed-IAM project**, and that shaped
  half the deploy: a single assigned Region (`us-east-2`), no GitHub OIDC (the managed SCP denies
  `iam:*Provider*`), so CI uses a scoped access-key user instead. See §7 and `infra/README.md`.
- **The connector needs a same-origin OAuth server.** The plan assumed FastMCP's `RemoteAuthProvider`
  advertising Supabase as the authorization server (RFC 9728) would be enough. It is not: Claude's
  connector ignores that pointer and does DCR and the whole OAuth flow against the MCP server's *own*
  origin. The app therefore runs a FastMCP `OAuthProxy` that fronts Supabase. This is the single
  biggest thing the design got wrong on paper, and §7 / `infra/README.md` cover the fix.
- **The database is reached over the Supabase Session pooler, not the direct connection**, because
  the direct host is IPv6-only and the VPC is IPv4-only. See `docs/networking.md`.

### Explicit non-goals for v1

No mobile app; no in-app chat interface; no photo upload / image storage (text categories, fields
and tags only); no subscription tiers or billing; no push notifications or scheduled reminders; no
multi-tenant / team accounts; no Claude plugin or skill bundle (§1).

### On tags, and the one ambiguous piece of early feedback

Early feedback said, roughly, "I don't think we need user 'tags', name and other info can probably go
into one user/user_profile table." That was read as: **drop a separate normalized `tags` /
`item_tags` registry** — tags become a plain denormalized array directly on each item, with no
per-user tag registry — while `user_profile` stays the single place for account-level info
(`home_location`, `unit_preference`). The build confirmed the reading, and two consequences fell out
of it that are handled rather than merely noted:

- There is no canonical list of a user's own tags, which the closet browser's tag filter needs.
  `GET /api/tags` derives it from the items carrying each tag (§6). Deriving rather than storing
  keeps one source of truth: a tag stops existing the moment nothing carries it, with no orphan rows.
- Without a registry there is nothing to reconcile typo variants against, so tags are trimmed,
  lower-cased and de-duplicated on write. `Date-Night` and `date-night` are one tag.

---

## 1. Claude integration surface: connector vs. plugin

Anthropic's guidance for third-party integrations treats an MCP server and a plugin as
complementary, not interchangeable: build a remote MCP server with OAuth first, for connectivity and
core functionality, and only optionally add a plugin that bundles skills on top. They serve different
purposes:

| | MCP server (connector) | Plugin |
|---|---|---|
| Mental model | "Claude can call your API" | "Claude knows how to *use* your product" |
| Contains | Tools, prompts, resources | Skills, MCP connector references, slash commands |
| Works in | Claude.ai (web/mobile), Desktop, Cowork, Claude Code | Claude Code and Cowork only |

That last row settles it: the stated primary interface is Claude.ai itself (plain web/mobile chat),
and plugins do not run there — only in Claude Code and Cowork. A remote MCP server with OAuth (a
connector) is not just the right choice, it is the only one that reaches this app's actual audience.

This is also why the "assistant-side work" the app leans on — teaching the assistant how to build a
safe `where_clause` for `query_closet_items`, or how to parse a multi-item message for
`batch_add_items` — is carried the way it is (§4) rather than in a bundled skill or MCP's own
"prompts" primitive:

- **Skills** have the same reach problem as plugins: Claude Code and Cowork only, not plain Claude.ai
  chat.
- **MCP prompts** are user-invoked, closer to a slash command a person explicitly picks, not
  something the model consults automatically while deciding how to call a tool; client support for
  the primitive is also inconsistent.
- What reliably shapes the model's own autonomous tool-use, on every client including plain
  Claude.ai chat, is exactly what this app does: rich tool **descriptions** (which the model reads
  when deciding whether and how to call a tool) and instructional content returned **inside a tool's
  own response** — `get_closet_structure`'s `query_instructions` field, and the system-prompt-style
  guidance embedded in `batch_add_items`'s description (`backend/app/mcp_server/instructions.py`).

**v1 scope: connector only.** A companion plugin bundling a deeper usage playbook for Claude Code /
Cowork users is a reasonable v2 idea, but it is additive for a subset of users, not required, and out
of scope for v1.

*Template note.* If the next tool's primary users are in Claude Code or Cowork rather than
Claude.ai, this decision flips — lead with a plugin/skill, keep the MCP server underneath. The reach
table is the thing to re-check first, before any architecture.

---

## 2. Architecture overview

```
┌─────────────────┐        ┌──────────────────────────────────────────┐
│   React + Vite   │  REST  │              FastAPI app                │
│   web frontend   │───────▶│  ┌────────────────────────────────────┐  │
│  (served by the  │        │  │  REST routes (/api, auth-gated via  │  │
│   same container │        │  │  Supabase session JWT)             │  │
│   at /)          │        │  │  - items CRUD, categories, tags     │  │
│  - signup/login  │        │  │  - profile, account deletion        │  │
│  - closet browser│        │  └────────────────────────────────────┘  │
│  - consent screen│        │  ┌────────────────────────────────────┐  │
│  - "connect your │        │  │  FastMCP sub-app, mounted at /mcp   │  │
│    assistant"    │        │  │  (auth-gated via Supabase OAuth     │  │
└─────────────────┘        │  │  token, validated via JWKS)         │  │
                             │  │  - 9 tools (§4)                     │  │
        ▲                   │  └────────────────────────────────────┘  │
        │                   │  ┌────────────────────────────────────┐  │
        │ OAuth 2.1          │  │  OAuth proxy (/authorize /token     │  │
        │ (DCR, PKCE)        │  │  /register /auth/callback), fronting│  │
        │                    │  │  Supabase — see §7                 │  │
┌───────┴─────────┐          │  └────────────────────────────────────┘  │
│   Claude.ai      │          │  ┌────────────────────────────────────┐  │
│  (connector,     │          │  │  shared service layer (services/)   │  │
│   finds weather   │          │  │  — both surfaces go through it      │  │
│   itself)         │          │  └────────────────────────────────────┘  │
└─────────────────┘          └──────────────────────────────────────────┘
                                          │
                                          ▼
                           ┌──────────────────┐      ┌──────────────┐
                           │  Supabase Auth    │      │  Supabase    │
                           │  (OAuth 2.1 AS +  │      │  Postgres    │
                           │  session auth)    │      │  (RLS + read-│
                           └──────────────────┘      │  only role)  │
                                                       └──────────────┘
```

There is no external weather API and no Claude plugin — both scoped out (§0/§1). The REST API, the
MCP tools, and the OAuth proxy all sit in the same FastAPI process, the same ECS Express Mode
service, and the same Supabase project. For the rendered component diagram and the two request paths
(web user vs. Claude connector), see [`docs/architecture.md`](docs/architecture.md).

**A shared service layer sits under both surfaces.** Neither the REST routes nor the MCP tools write
their own SQL against the user-scoped tables — both call the same functions in
`backend/app/services/` (`items`, `profile`, `taxonomy`, `usage`, `admission`), which own item CRUD,
category-field validation, the profile, and the usage counter. The rules that must hold identically
however a change arrives — the 200-item cap, the five-colour limit, field validation, RLS scoping —
are therefore enforced in exactly one place. **This was the single most useful structural decision in
the build.** It was not in the original ticket split; building it up front turned "the REST ticket
and the MCP ticket must coordinate on shared functions" from a coordination problem into a non-issue,
and let the two surfaces genuinely be built in parallel against a frozen contract and merge with no
conflicts.

*Template note.* When two surfaces (a UI API and an agent API) sit over the same data, build the
shared service layer **first**, before either surface. It is cheap up front and expensive to retrofit
once the surfaces have each grown their own SQL.

**The frontend ships in the same container.** FastAPI serves the built Vite bundle at `/` with a
fallback to `index.html` for client-side routes (`backend/app/static.py`), alongside `/api`, `/mcp`
and the OAuth endpoints. One image, one origin, one deploy. This is not merely convenient — it
removes whole categories of configuration rather than solving them: no CORS, no second certificate or
domain, no second deploy pipeline. The SPA fallback also turned out to be load-bearing for the OAuth
flow: Supabase hard-navigates to `/oauth/consent`, which only resolves because the container falls
back to `index.html` for it while `/api/*` still returns JSON. The alternative (S3 + CloudFront, or a
Vercel-class host) buys CDN edge caching and independent frontend deploys, neither worth the cost at
single-digit concurrent users. The seam is cheap to reverse: the API client honours
`VITE_API_BASE_URL` for a split-origin deployment, and `CORS_ALLOW_ORIGINS` still exists for that
case.

One consequence worth knowing: Vite inlines `VITE_*` values at build time, so the Supabase project
URL and anon key are baked into the image by `docker build --build-arg` rather than supplied to the
running task. Both are publishable — the anon key is safe in a bundle because RLS protects the
data — so they are repository variables, not secrets.

---

## 3. Data model

### Terminology

- **Category**: a fixed top-level garment type (see the seed list below). Defines which
  category-specific fields are relevant via a field template.
- **Common fields**: attributes that apply across nearly every category, promoted to first-class
  columns on `items` rather than nested in per-category JSONB, specifically so the safe query tool
  (§4) can allowlist a small, stable column set: `colors`, `brand`, `warmth_rating`, `formality`,
  `tags`, `notes`.
- **Category-specific fields**: niche attributes relevant only to some categories (e.g.
  `sleeve_length`, `rise`, `heel_height`, `waterproof`, `neckline`) — data-driven via
  `category_field_defs`, stored in the `fields` JSONB column, queried via `fields->>'key'`.
- **Tags**: free-form strings the user attaches to an item (e.g. `date-night`, `floral`,
  `multi-color`). A plain array directly on the item — no registry table (§0).
- **Notes**: free text on an item for anything that does not fit a structured field.

### Category seed list

Seeded once (`db/migrations/0002_seed_taxonomy.sql`), extendable later without a redeploy. **26
categories** as shipped:

`tshirt`, `polo`, `shirt`, `blouse`, `tank_top`, `sweater`, `sweatshirt`, `hoodie`, `jacket`, `coat`,
`blazer`, `cardigan`, `vest`, `jeans`, `trousers`, `shorts`, `lower` *(joggers/leggings/sweatpants)*,
`skirt`, `dress`, `jumpsuit`, `two_piece_suit`, `shoes`, `belt`, `hat`, `scarf`, `tie`.

`shoes` carries a category-specific enum field (`shoe_type`: sneaker/boot/sandal/formal/other) rather
than being split into separate shoe categories, keeping the list from exploding while still letting
the assistant distinguish shoe types via a field. The migrations seed **75** `category_field_defs`
rows across those categories, and the seed migration can be re-run to extend the taxonomy without
duplicating rows (see the unique constraint below).

### Tables (Supabase Postgres)

**`categories`** — `id` (text PK, e.g. `tshirt`), `display_name`, `sort_order`.

**`category_field_defs`** — category-specific fields only (the six common fields are columns on
`items`): `id` (uuid PK), `category_id` (FK), `field_name`, `field_type`
(`text`|`enum`|`number`|`boolean`), `required`, `allowed_values` (text[], populated only for `enum`),
`display_order`. Carries `unique (category_id, field_name)` and a check that `allowed_values` is
populated for `enum` and empty otherwise — which is what lets the seed migration be re-run safely.

**`items`** — `id` (uuid PK), `user_id` (FK → auth.users, RLS-scoped), `category_id` (FK), `colors`
(text[], max 5 via a check constraint), `brand`, `warmth_rating` (int 1–5), `formality`
(`casual`|`smart_casual`|`formal`|`athletic`), `tags` (text[]), `notes`, `fields` (jsonb, validated
against `category_field_defs`), `created_at`, `updated_at`. As built, the colour cap, `warmth_rating`
range, `formality` enum, and "`fields` is a JSON object" are checked in the database as well as in the
application, and `updated_at` is maintained by a trigger so it cannot be forgotten on a write path
added later.

**`user_profile`** — `user_id` (PK, FK → auth.users), `home_location` (free text, e.g. "Flint, MI"),
`unit_preference` (`fahrenheit`|`celsius`).

**`usage_counters`** — backs the per-user daily MCP call cap (§5): `user_id`, `day` (date, UTC),
`call_count`, PK `(user_id, day)`.

RLS applies to every user-scoped table (`auth.uid() = user_id`), identically whether the request
arrived via the web app's session JWT or the connector's OAuth access token. The three tables holding
user data additionally carry `force row level security`, so the policy applies even to the table
owner. One deliberate asymmetry: `usage_counters` is *readable* by its owner but has **no insert or
update policy at all**, so a user cannot reset their own daily quota — the counter is written only by
the server, as the application role, scoped by `user_id`. `categories` and `category_field_defs` are
readable by any signed-in user and writable only by the service role.

### `closet_query_view` and the read-only role — the actual security boundary

```sql
CREATE VIEW closet_query_view AS
SELECT id, user_id, category_id AS category, colors, brand, warmth_rating,
       formality, tags, notes, fields
FROM items;
```

This view is the **only** thing the safe query tool (§4.1) selects from, and a dedicated read-only
Postgres role (`wardrobe_readonly`) is granted `SELECT` on it — nothing else, no other columns of
`items`, no writes of any kind. **This role, not the AST validation in §4.1, is the real backstop.**

Two things about the view matter more than they look:

- **It is not granted to `authenticated`, and this is deliberate.** The view is not
  `security_invoker`, so it runs with its owner's privileges and RLS on `items` does **not** apply
  through it. Granting it to `authenticated` — which is what exposing it to PostgREST would do — would
  have handed every signed-in user every other user's closet. Only `wardrobe_readonly` may select it,
  and every statement that does binds `user_id` as a parameter. That bound parameter, not RLS, is
  what keeps users apart on the query path; read "RLS protects both surfaces" with that one exception
  in mind.
- **The Data API (PostgREST) is switched off entirely**, because nothing uses it: the frontend uses
  Supabase only for auth, and the backend reaches the database directly over asyncpg. That removes
  the exposure route above rather than merely defending against it. The `revoke` stays in
  `0003_rls.sql` regardless, so the guarantee survives the Data API being turned back on. See
  `docs/setup-supabase.md` for the toggle.

Two hardening choices on the role, neither of which the design originally called for:

- It is created **without a password**, so no credential is committed; an operator sets one after
  applying the migration, and its connection string is a secret separate from the application role's.
- It carries `default_transaction_read_only`, a `statement_timeout` and an
  `idle_in_transaction_session_timeout` as **role-level** settings, so the guarantees hold even for a
  connection the app forgot to configure. The app re-asserts the first two per transaction as well.

Verifying this is not an assumption: `db/tests/verify_readonly_role.sql` asserts eight statements are
each refused (insert/update/delete through both the view and `items`; select on `items`,
`user_profile`, `auth.users`; and `create table`), and runs against a real Supabase project as
readily as a local one.

---

## 4. MCP tool contract

**Nine** tools, mounted via FastMCP at `/mcp`, authenticated via Supabase OAuth 2.1 (bearer token
validated against Supabase's JWKS endpoint). Definitions live in `backend/app/mcp_server/tools.py`;
their descriptions and the assistant-facing instructional text live in
`backend/app/mcp_server/instructions.py`.

On token validation as built: the JWKS endpoint is
`{SUPABASE_URL}/auth/v1/.well-known/jwks.json` and the expected issuer is `{SUPABASE_URL}/auth/v1`,
with RS256 and ES256 both accepted — derived the same way FastMCP's own `SupabaseProvider` derives
them. The legacy HS256 project secret is accepted as a fallback for projects not yet on asymmetric
signing, and is what the local test-token helper signs with, so the auth path under test is the real
one rather than a test-only bypass. In production the connector does not present these tokens
directly — it holds a proxy-issued reference token that the OAuth proxy swaps for the stored Supabase
token on each call, which is then validated by this same verifier (§7).

| Tool | Purpose | Key inputs | Returns |
|---|---|---|---|
| `get_closet_structure` | The assistant's one-shot map of the entire closet taxonomy | — | every category, its field template, the six common-field definitions, plus `query_instructions` |
| `add_item` | Add a single closet item | `category`, `colors?`, `brand?`, `warmth_rating?`, `formality?`, `tags?`, `notes?`, `fields?` | created item, or a clear error if the 200-item cap is hit |
| `update_item` | Edit an item | `item_id`, any of the above fields | updated item |
| `remove_item` | Delete an item | `item_id` | confirmation |
| `batch_add_items` | Add several items from one user message in a single call (§4.2) | `items` (list, same shape as `add_item`, max 20) | per-item created/failed results |
| `query_closet_items` | The assistant's general filtering tool (§4.1) | `where_clause` (a SQL boolean predicate string) | matching items, including `tags`/`notes`/`colors`/`brand` (they carry differentiating info like "floral") |
| `list_category_items` | Simple listing for one category, no predicate | `category` | up to 100 items in that category, same field shape as the query tool |
| `get_closet_summary` | High-level overview without pulling every item | — | total count, per-category counts, distinct brands/tags, rough formality/warmth distribution |
| `get_user_profile` | Avoids re-asking for location/units | — | `home_location`, `unit_preference` |

`list_category_items` and `get_closet_summary` are fixed, low-risk queries against
`closet_query_view` with no free-text predicate at all — meaningfully simpler and safer than
`query_closet_items`, and worth reaching for first when the need is "show me category X" or "what's
generally in here" rather than an occasion-driven filter. There is deliberately no `get_weather`
tool — the assistant finds weather itself.

`get_closet_structure`'s `query_instructions` field documents, in plain text plus a short example,
exactly what `query_closet_items` accepts: the columns and types (`category`, `colors` text[],
`brand`, `warmth_rating` int, `formality` text, `tags` text[], `notes`, and `fields` jsonb with its
per-category keys), the allowed operators, and a couple of example predicates. This is the "standard
to build the query" the assistant gets up front so it does not guess syntax — carried in tool
description/response content (§1) rather than a skill or prompt, since that is what the model consults
automatically on every client.

Two things `query_instructions` says that the design did not anticipate, both surfaced only once real
predicates were run through the validator:

- **`fields->>'key'` returns text, so `<`, `<=`, `>`, `>=` on it compare alphabetically.**
  `fields->>'inseam_inches' > '30'` is true for `'9'`. Casts are rejected (§4.1), so there is no
  `::numeric` escape hatch; the instructions therefore tell the assistant to use `=` or `IN` on
  number-typed fields and rank the returned items itself. `warmth_rating` is a real integer column
  and is the one numeric field that orders correctly. Worth knowing when extending field templates: a
  `number`-typed category field is filterable but not rangeable.
- **`ANY(...)` is supported, `ALL(ARRAY[...])` is not.** sqlglot parses `ALL` in that position as an
  anonymous function, which the node allowlist refuses. The instructions advertise only `ANY`, so the
  tool is self-consistent, and `ALL` buys nothing the array-containment operators do not already
  cover.

### 4.1 Safe execution design for `query_closet_items`

This is the highest-risk component in the build and gets its own section. The assistant supplies a
single SQL **boolean expression** (a WHERE-clause predicate) — never a full statement. The server
(`backend/app/safe_query.py`):

1. Rejects the input outright if it contains a semicolon; the keywords
   `SELECT`/`INSERT`/`UPDATE`/`DELETE`/`DROP`/`ALTER`/`GRANT`; a CTE; or a subquery — it should be a
   bare predicate, nothing else.
2. Parses the predicate with **sqlglot** (Postgres dialect) into an AST. sqlglot's own docs state it
   is a parser/transpiler, not a security validator, and its parser is intentionally lenient — so
   this step is a filtering convenience and a source of good error messages for the assistant, **not
   the security boundary**.
3. Walks the AST and rejects anything referencing a column outside the allowlist (`category`,
   `colors`, `brand`, `warmth_rating`, `formality`, `tags`, `notes`, `fields`), any `fields->>'x'`
   access where `x` is not a real field name from `category_field_defs`, and any function call outside
   a small allowlist (comparison operators, `AND`/`OR`/`NOT`, `IN`, `BETWEEN`, `IS [NOT] NULL`,
   `ILIKE`, the array operators `&&`/`@>`/`<@`, `ANY`).
4. Re-serializes the validated AST back to SQL (never string-concatenates the raw input) and executes
   it as `SELECT * FROM closet_query_view WHERE user_id = $1 AND (<validated>) LIMIT 50` over the
   **read-only DB role** (§3), with `user_id` always bound as a parameter and never derived from
   assistant-supplied text.
5. Applies a server-side statement timeout regardless of what the predicate does.
6. Logs rejected predicates, to spot abuse and tune the allowlist.

The actual guarantees against SQL injection or cross-user exposure come from steps 3–4 (allowlisted
read-only role, parameterized `user_id`) — the AST walk is defense-in-depth on top, not a
replacement.

Refinements from the build — additions, not corrections:

- **The allowlist is over AST node types, not a denylist of bad words.** Anything the parser produces
  that is not explicitly permitted is rejected, so an unforeseen construct fails closed rather than
  needing to have been thought of.
- **Unquoted identifiers are folded to lower case before the column check**, exactly as Postgres
  resolves them. Without this, `CATEGORY = 'jacket'` — valid SQL, and plausible model output — is
  rejected as an unknown column, spending one of the user's daily calls on a predicate that was never
  wrong. Quoted identifiers keep their case, as Postgres does, so `"User_Id"` still matches no
  allowlist name.
- **The textual pre-scan runs with string literals blanked out.** Otherwise
  `notes ILIKE '%goes with jeans%'` is rejected for containing "with" — and `notes` is precisely
  where the differentiating detail lives. This costs nothing in safety: the scan's only output is a
  yes/no, it never feeds the SQL that gets built, and every construct those keywords stand for is
  independently refused by the node allowlist.
- **Node-count and nesting-depth caps**, checked textually before the recursive parser runs and again
  on the parsed tree — a deeply nested predicate would otherwise exhaust the Python stack (a crash,
  not a rejection).
- **`sqlglot.parse` is re-run to confirm a single expression**, because `parse_one` silently keeps
  only the first statement.
- The re-serialised SQL is checked for `;`, `--` and `/*` before the statement is built — a guard on
  the generator, not the input.

**How this was verified.** Beyond the branch's own tests, an independently written battery of 35
attacks was run against it: second statements, subqueries, `DROP TABLE`, comment truncation, `UNION`,
casts, `pg_sleep`, `pg_read_file`, `current_setting`, `version()`, the jsonb exists operator, a
300-deep parenthesis bomb, a 500-term `OR` chain, a table-qualified column, a bind placeholder, a
quoted `"user_id"`, and a direct `user_id` comparison. All 35 were rejected; 14 legitimate predicates
were accepted; and a predicate written to match every row returned nothing belonging to a second
user. Re-running that battery after any change to the validator is the cheapest way to keep this
component honest (`backend/tests/test_safe_query.py`).

### 4.2 `batch_add_items` design

The point of this tool is to let one user message like "add three t-shirts — a red Nike one, a blue
polo, and a black hoodie" become **one MCP call**, not three (the call budget is tight, §5). Its
description is written deliberately as system-prompt-style instructions to the assistant
(`instructions.py`), roughly:

> Before calling this tool, call `get_closet_structure` first if you have not already this
> conversation, so you know each category's required fields. Parse the user's message into one
> structured item object per item described, matching `add_item`'s shape. If a category's required
> fields are missing and cannot be reasonably inferred, ask the user before calling — don't guess or
> silently omit. Call once with the full list once you have enough for every item.

Input is capped at **20 items per call** — well under the 200-item closet cap, enough to keep one
call from doing unbounded work. The tool processes each item independently and returns a per-item
result (`created` with the new item, or `failed` with a reason — invalid category, missing required
field, or the 200-item cap reached partway through). This is deliberately partial-success rather than
all-or-nothing: if item 3 of 5 would exceed the cap, items 1–2 are kept and the response says exactly
which succeeded and which did not and why, so the assistant reports it back accurately rather than the
user losing everything over one failure.

---

## 5. Limits and account controls

- **Per-user closet size cap**: 200 items (`MAX_ITEMS_PER_USER`). Enforced in `add_item` and
  `batch_add_items` on both surfaces — reject with a clear error once at the cap.
- **Per-user daily MCP usage cap**: tracked as a **call count**, not a "session" count — a single
  Claude.ai conversation can be one long-lived session covering a whole day or many short ones,
  whereas a call counter is simple and robust. **Cap: 50 calls per user per UTC day**
  (`DAILY_MCP_CALL_LIMIT`), tracked in `usage_counters`, incremented atomically per call, checked
  before any tool body runs. Exceeding it returns a clear structured error the assistant can relay,
  including when the budget resets.

  The cap started at 10 in the plan, flagged as tight. It was: a `get_closet_structure` call plus a
  couple of `query_closet_items` calls across a few recommendation questions was most of a day's
  budget, and a single end-to-end walkthrough spent nearly all of it. **50 is the shipped default**;
  10 remains a one-line change back. The reasons `batch_add_items`, `list_category_items` and
  `get_closet_summary` exist have not changed — one call instead of N. Every tool response carries
  `calls_remaining_today`, so the model reads the live figure rather than a value baked into its
  instructions, and nothing needs rewriting if the cap changes again.

  The counter is a single conditional upsert, so two concurrent calls cannot both slip past the cap,
  and a refused call does not inflate the count. Because `usage_counters` has no insert or update
  policy for `authenticated` (§3), a user cannot reset their own quota.

- **Cross-server token replay**: Supabase does not implement RFC 8707 resource indicators, so an
  access token minted for a *different* MCP server backed by the *same* Supabase project would also
  validate here. For a project hosting only this app there is no second server to replay from — but
  it stops being a non-issue the moment an unrelated MCP server is added to the same project. Audience
  checking is available (`EXPECTED_TOKEN_AUDIENCE`) but off by default: every token from a given
  project carries the same audience, so the claim cannot distinguish this case anyway, and enforcing
  a guessed value fails closed on the connector path, which is the hardest one to test before it is
  live. `backend/app/auth.py` explains the reasoning at length.
- **Account and data deletion**: a web-app-only action, deliberately **not** an MCP tool — deleting
  an entire closet is irreversible and should not be one careless chat message away. The Settings
  page has a "Delete my account" flow with an explicit type-to-confirm step; `DELETE /api/account`
  deletes the user's `items`, their `user_profile` row, their `usage_counters` rows, and the Supabase
  auth user itself.

---

## 6. Web app and REST API

### Web app (v1 scope)

- **Auth pages**: sign up / log in via Supabase Auth (magic link — least code, no password-reset flow
  to build).
- **Closet browser**: list of items, filterable by category and tags, each showing its category,
  common fields, category-specific fields, tags and notes. Add/edit/delete directly from this view —
  not assistant-only, so mistakes can be corrected without a conversation.
- **Settings**: `home_location`, `unit_preference`, and the "Delete my account" flow.
- **"Connect your assistant" page**: static instructions plus the connector URL to paste into
  Claude.ai (`PUBLIC_BASE_URL` + `/mcp`).
- **OAuth consent screen** (`/oauth/consent`): Supabase is the authorization server but does not
  render consent itself — it redirects here with an `authorization_id`, and this page shows who is
  asking and calls approve/deny. This is a v1 requirement that only became visible during the OAuth
  work (§7), not something the original web-app scope listed.

No embedded chat interface in v1. See `frontend/README.md` for the frontend's shape.

### REST API as built

All routes under `/api`, all requiring a Supabase access token, all resolving to the same `user_id`
as the MCP path.

| Method | Path | |
|---|---|---|
| `GET` | `/api/categories` | Every category with its field template — drives the dynamic form. |
| `GET` | `/api/items` | `category`, repeatable `tags` (AND), `limit`, `offset`. Returns `{items, total}`, where `total` is the whole closet, not the filtered count, so the UI can show how much of the 200-item cap is spent while a filter is applied. |
| `GET` | `/api/tags` | The caller's distinct tags with usage counts, derived from the items carrying them. Backs the closet browser's tag filter, which otherwise had no source given there is no tag registry table (§0). |
| `POST` | `/api/items` | 201 with the created item. |
| `GET` `PATCH` `DELETE` | `/api/items/{id}` | `PATCH` is partial; `DELETE` returns 204. |
| `GET` `PUT` | `/api/profile` | Never 404s — a user with no row gets the defaults. |
| `GET` | `/api/me` | Account summary: item count and limit, calls used today and the daily limit, and the connector URL the Connect page displays. |
| `GET` | `/api/limits` | The caps applying to this account. |
| `DELETE` | `/api/account` | Items, profile, usage counters, and the Supabase auth user. |

**One error envelope.** Domain errors and request-validation failures both return
`{"error": "<stable code>", "message": "<human readable>", "details": {…}}`, so a client has one
shape to understand. FastAPI's default `{"detail": [...]}` for body validation is overridden to
match. Stable codes: `closet_full`, `validation_failed`, `unknown_category`, `not_found`,
`daily_limit_reached`, `unsafe_query`. `message` is written to be shown to a person — which is also
what makes it usable verbatim by the assistant on the MCP side.

---

## 7. Deployment and operations

v1 runs as **one container image** on an **ECS Express Mode** service in **`us-east-2`**, live at
**https://app.weatherpruf.live**, deployed by GitHub Actions (`.github/workflows/deploy.yml`). Full
runbooks: [`infra/README.md`](infra/README.md) (AWS) and [`docs/setup-supabase.md`](docs/setup-supabase.md)
(Supabase); the networking specifics are in [`docs/networking.md`](docs/networking.md). This section
is the reasoning, not the step list.

**ECS Express Mode over a hand-rolled service.** Express Mode provisions the Fargate service, ALB,
TLS certificate, auto-scaling and a public HTTPS URL from just an image plus two IAM roles, so there
is no task-definition or service YAML checked in. That HTTPS URL is what met the connector's HTTPS
requirement without any custom-domain work — the custom domain came later, as polish.

**Secrets as JSON keys, injected at task start.** Everything sensitive lives in one Secrets Manager
secret (`weatherpruf/backend`), referenced key-by-key from the task definition, so no value lands in
the task definition, the console, or a workflow log. ECS reads secrets **at task start only**, so a
rotated secret needs a forced deployment to reach running tasks.

**CI uses a scoped IAM access-key user, not GitHub OIDC.** The managed-IAM project denies
`iam:*Provider*` at every plan level, so an OIDC identity provider cannot be created here. A
locked-down user (`weatherpruf-ci`) whose only permission is to assume the deploy role holds the
access keys; all deploy permissions stay on the role. The trade-off (a long-lived key) is mitigated
by tight scoping and rotation.

**The MCP connector OAuth proxy — the biggest gap between plan and reality.** The plan had FastMCP's
`RemoteAuthProvider` merely *advertise* Supabase as the authorization server via RFC 9728. Claude's
connector ignores that pointer: it does DCR and the whole OAuth flow against the MCP server's own
origin (`POST /register`, `/authorize`, `/token`,
`/.well-known/oauth-authorization-server`). So the app runs a FastMCP `OAuthProxy`
(`backend/app/mcp_server/auth.py`) that serves those endpoints at this origin and bridges them to
Supabase. The token model: a connector holds a proxy-issued **reference** token; on each `/mcp`
request the proxy swaps it for the stored upstream Supabase token and re-validates it through the same
verifier the REST path uses — identity and RLS unchanged. This added a Supabase upstream client, the
`SUPABASE_OAUTH_CLIENT_ID` variable, the `MCP_OAUTH_JWT_SIGNING_KEY` secret, and a **single-task
pin**: the proxy's token store is per-instance and in-memory, so the service runs `max-task-count: 1`
and connectors must re-authorize once after each deploy. The upgrade path is a shared/persistent
store (e.g. a Postgres-backed `AsyncKeyValue`), which would let the task count rise again.

**Database over the Session pooler (IPv4).** The Supabase direct host is IPv6-only without the paid
add-on, and the default VPC Express Mode uses is IPv4-only, so both connection strings use the Session
pooler (`aws-0-<region>.pooler.supabase.com:5432`, port 5432 not 6543 so prepared statements and
per-transaction `SET`s survive), with the project ref folded into the username. See
`docs/networking.md`.

**Custom domain on the existing ALB, via Squarespace, not Route 53.** `app.weatherpruf.live` was added
to the Express Mode ALB in place — an ACM cert alongside the ECS-managed `on.aws` cert (SNI picks),
a `:80`→`:443` redirect, and a host-header rule forwarding both hostnames. DNS is a Squarespace CNAME
to the ALB. The one thing to watch: `UpdateExpressGatewayService` can rewrite the host-header rule and
drop the custom host on a deploy, so it is re-checked and re-applied after every deploy
(`infra/README.md` → "Custom domain and TLS").

**CI/CD.** Pushes to `main` touching `backend/`, `frontend/`, `infra/Dockerfile` or the workflow run
the pipeline: apply migrations to a throwaway Postgres, run lint + the frontend build + the test
suite, then build the image (Vite inlines `VITE_*`), push to ECR, deploy, and re-apply the ALB host
rule. Migrations against the **real** Supabase project are deliberately **not** in the pipeline —
they are applied by hand before the deploy that needs them.

### How the build actually went (a template retrospective)

The plan split the work into six tickets — foundations, REST API, MCP server, frontend, deployment,
end-to-end verification — with the middle ones runnable in parallel. That sequencing was sound, with
two lessons worth carrying to the next project:

- **Build the shared service layer (§2) as part of foundations, not as coordination between the two
  surface tickets.** Doing so is what let the REST and MCP work run genuinely in parallel and merge
  without conflict. This is the single most useful thing to repeat.
- **The last mile — OAuth against a real connector, the deploy on a real account, the Supabase
  dashboard steps — is where the plan was most wrong, and it could not have been otherwise.** Three
  whole areas (the OAuth proxy, the managed-IAM AWS realities, the IPv6/pooler problem) only surfaced
  when the code met a real environment. The runbooks (`infra/README.md` "Redos", `docs/networking.md`,
  `docs/setup-supabase.md`) capture each of these in place. The template lesson is not "plan better"
  — it is **budget real time for the first live deploy of anything that talks OAuth or touches a
  managed cloud account, and write down what you find as you find it**, because none of it is
  reproducible from documentation alone.

The security-critical pieces held up as designed: the read-only role and parameterized `user_id`
(§3/§4.1), the RLS spine, the per-call cap. Those are the parts worth over-engineering up front; the
plumbing around them is the part to expect surprises in.
