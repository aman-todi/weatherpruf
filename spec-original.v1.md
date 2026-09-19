# weatherpruf — v1 Build Spec

This is the build spec for weatherpruf: a digital-closet app whose **primary interface is an AI
assistant** connected over MCP, with a thin web app beside it. It defines the architecture, the data
model, the MCP and REST contracts, the security design, the deployment target, and the tickets to
build it in — in the order they should be built and where they can run in parallel. It is written to
be handed to Claude Code (or any implementer) as the source of truth to build against; §8 breaks the
work into tickets with an explicit orchestration plan.

Read §0–§7 for the design, then §8 for the build order. The backend should cite this spec by section
number in comments (`spec §4.1`, `spec §5`, …) so the reasoning stays attached to the code.

---

## 0. Background

weatherpruf is a personal project (learning + portfolio; expected traffic: very light — single-digit
concurrent users). The core idea: a web app manages a user's digital closet, but the **day-to-day
interface is an AI assistant** (Claude.ai) connected via MCP (Model Context Protocol) — not the web
app itself. The web app exists to onboard, manage account/auth, browse and correct the closet
visually, and instruct the user how to connect their assistant. All day-to-day interaction ("add a
t-shirt to my closet", "what should I wear for my dinner date tonight?") happens through the
assistant, which calls MCP tools against this backend.

The design principle that runs through the whole spec: **the backend does no outfit reasoning, and it
fetches no weather.** It exposes the closet's structure and one safe way to query it. The assistant
is expected to already know or ask for the user's location, find current weather with its own
capabilities (general knowledge, web search — not this app's job), read the closet's category/field
taxonomy from `get_closet_structure`, build a filtering query against that structure, and combine the
returned items into a recommendation itself. This keeps the backend to CRUD plus one guarded
query-execution path, and lets the model do the reasoning it is good at while the server does the
data custody it is good at.

### Decisions locked in

- **Auth**: Supabase Auth for both the web app's own login/session and the MCP OAuth 2.1
  authorization the connector uses. Tokens carry standard Supabase claims and work directly with Row
  Level Security (RLS), so the same policies protect REST and MCP calls alike. The MCP client
  onboarding path is **Dynamic Client Registration (DCR)** — deprecated in favour of Client ID
  Metadata Documents (CIMD) but still functional and what Supabase supports; treat the move to CIMD
  as a future configuration change, not a rewrite.
- **Database**: Supabase Postgres.
- **MCP server**: FastMCP (v4), mounted directly inside the FastAPI app as an ASGI sub-application —
  one process, one deploy, a shared auth and data-access layer between REST and MCP.
- **Frontend**: React + Vite + TypeScript, served from the same container as the backend on one
  origin (§2).
- **Compute**: AWS ECS Express Mode (the current AWS-recommended path now that App Runner has stopped
  accepting new customers), in region `us-east-2` (§7).
- **Claude integration surface**: a remote MCP server with OAuth (a connector), not a plugin — see
  §1.
- **No server-side outfit reasoning, no server-side weather integration.** The assistant does both
  with its own knowledge/tools; the backend only serves closet data.

### Explicit non-goals for v1

No mobile app; no in-app chat interface embedded in the web app; no photo upload / image storage
(text categories, fields and tags only); no subscription tiers or billing; no push notifications or
scheduled reminders; no multi-tenant / team accounts; no Claude plugin or skill bundle (§1).

### Tags: denormalized, no registry

Tags are **a plain array directly on each item** — no separate `tags` / `item_tags` registry table,
and no per-user tag table. `user_profile` stays the single place for account-level info
(`home_location`, `unit_preference`). Two consequences to design for from the start:

- There is no canonical list of a user's own tags, which the closet browser's tag filter needs, so
  provide `GET /api/tags` that **derives** the list from the items carrying each tag (§6). Deriving
  rather than storing keeps one source of truth: a tag stops existing the moment nothing carries it.
- Without a registry there is nothing to reconcile typo variants against, so **tags are trimmed,
  lower-cased and de-duplicated on write**. `Date-Night` and `date-night` are one tag.

---

## 1. Claude integration surface: connector vs. plugin

Anthropic's guidance treats an MCP server and a plugin as complementary: build a remote MCP server
with OAuth first, for connectivity and core functionality, and only optionally add a plugin that
bundles skills on top. They serve different purposes:

| | MCP server (connector) | Plugin |
|---|---|---|
| Mental model | "Claude can call your API" | "Claude knows how to *use* your product" |
| Contains | Tools, prompts, resources | Skills, MCP connector references, slash commands |
| Works in | Claude.ai (web/mobile), Desktop, Cowork, Claude Code | Claude Code and Cowork only |

That last row settles it: the primary interface here is Claude.ai itself (plain web/mobile chat), and
plugins do not run there — only in Claude Code and Cowork. **Build a remote MCP server with OAuth (a
connector).** It is the only surface that reaches this app's actual audience.

For the same reason, the "assistant-side work" the app leans on — teaching the assistant how to build
a safe `where_clause` for `query_closet_items`, or how to parse a multi-item message for
`batch_add_items` — must be carried in a way that reaches plain Claude.ai chat:

- **Skills** have the same reach problem as plugins: Claude Code and Cowork only.
- **MCP prompts** are user-invoked (closer to a slash command a person explicitly picks), not
  something the model consults automatically while deciding how to call a tool; client support is
  also inconsistent.
- What reliably shapes the model's autonomous tool-use, on every client, is **rich tool
  descriptions** (which the model reads when deciding whether and how to call a tool) and
  **instructional content returned inside a tool's own response** — e.g. `get_closet_structure`'s
  `query_instructions` field, and system-prompt-style guidance embedded in `batch_add_items`'s
  description. Design the tools this way.

**v1 scope: connector only.** A companion plugin bundling a deeper usage playbook for Claude Code /
Cowork users is a reasonable v2 idea, but it is additive for a subset of users and out of scope here.

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
        │                    │  │  Supabase (§7)                     │  │
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

No external weather API and no Claude plugin (§0/§1). The REST API, the MCP tools, and the OAuth
proxy all live in one FastAPI process, one ECS Express Mode service, and one Supabase project.

**Build a shared service layer under both surfaces, first.** Neither the REST routes nor the MCP
tools should write their own SQL against the user-scoped tables — both call the same functions in
`backend/app/services/` (item CRUD, category-field validation, the profile, the usage counter). The
rules that must hold identically however a change arrives — the 200-item cap, the five-colour limit,
field validation, RLS scoping — are then enforced in exactly one place, and the two surfaces cannot
drift. This is deliberately built as part of the foundations ticket (§8, Ticket 1) rather than left
for the REST and MCP tickets to coordinate on, because it is what lets those two tickets run in
genuine parallel against a frozen contract.

**Serve the frontend from the same container.** FastAPI serves the built Vite bundle at `/` with a
fallback to `index.html` for client-side routes, alongside `/api`, `/mcp` and the OAuth endpoints.
One image, one origin, one deploy. This removes whole categories of configuration rather than solving
them: no CORS, no second certificate or domain, no second deploy pipeline. The `index.html` fallback
is also required by the OAuth flow, because Supabase hard-navigates the browser to `/oauth/consent`
(§6) — that path only resolves if the container serves the SPA for it while `/api/*` still returns
JSON. The seam is cheap to reverse if it is ever wanted: the API client honours `VITE_API_BASE_URL`
for a split-origin deployment, and `CORS_ALLOW_ORIGINS` exists for that case.

Because Vite inlines `VITE_*` values at build time, the Supabase project URL and anon key are baked
into the image at `docker build` (via `--build-arg`), not supplied to the running task. Both are
publishable — the anon key is safe in a bundle because RLS protects the data — so they are repository
variables, not secrets.

---

## 3. Data model

### Terminology

- **Category**: a fixed top-level garment type (see the seed list). Defines which category-specific
  fields are relevant via a field template.
- **Common fields**: attributes that apply across nearly every category, promoted to first-class
  columns on `items` so the safe query tool (§4) can allowlist a small, stable column set: `colors`,
  `brand`, `warmth_rating`, `formality`, `tags`, `notes`.
- **Category-specific fields**: niche attributes relevant only to some categories (e.g.
  `sleeve_length`, `rise`, `heel_height`, `waterproof`, `neckline`) — data-driven via
  `category_field_defs`, stored in the `fields` JSONB column, queried via `fields->>'key'`.
- **Tags**: free-form strings on an item (e.g. `date-night`, `floral`, `multi-color`). A plain array,
  no registry (§0).
- **Notes**: free text on an item for anything a structured field does not cover.

### Category seed list

Seed once, extendable later via `category_field_defs` without a redeploy. Seed these **26
categories**:

`tshirt`, `polo`, `shirt`, `blouse`, `tank_top`, `sweater`, `sweatshirt`, `hoodie`, `jacket`, `coat`,
`blazer`, `cardigan`, `vest`, `jeans`, `trousers`, `shorts`, `lower` *(joggers/leggings/sweatpants)*,
`skirt`, `dress`, `jumpsuit`, `two_piece_suit`, `shoes`, `belt`, `hat`, `scarf`, `tie`.

`shoes` carries a category-specific enum field (`shoe_type`: sneaker/boot/sandal/formal/other) rather
than being split into separate shoe categories, keeping the list from exploding while letting the
assistant still distinguish shoe types via a field. Seed a sensible category-specific field template
per category (at minimum: `shoes` → `shoe_type`; garments with sleeves → `sleeve_length`; bottoms →
`fit`; most things → `material`) — roughly **75** `category_field_defs` rows.

### Tables (Supabase Postgres)

**`categories`** — `id` (text PK, e.g. `tshirt`), `display_name`, `sort_order`.

**`category_field_defs`** — category-specific fields only: `id` (uuid PK), `category_id` (FK),
`field_name`, `field_type` (`text`|`enum`|`number`|`boolean`), `required`, `allowed_values` (text[],
populated only for `enum`), `display_order`. Add `unique (category_id, field_name)` and a check that
`allowed_values` is populated for `enum` and empty otherwise — that pair is what lets the seed
migration be re-run to extend the taxonomy without duplicating rows.

**`items`** — `id` (uuid PK), `user_id` (FK → auth.users, RLS-scoped), `category_id` (FK), `colors`
(text[], **max 5** via a check constraint), `brand`, `warmth_rating` (int, 1–5), `formality`
(`casual`|`smart_casual`|`formal`|`athletic`), `tags` (text[]), `notes`, `fields` (jsonb, validated
against `category_field_defs`), `created_at`, `updated_at`. Enforce the colour cap, `warmth_rating`
range, `formality` enum and "`fields` is a JSON object" as **database check constraints as well as in
the application**, and maintain `updated_at` with a trigger so a future write path cannot forget it.

**`user_profile`** — `user_id` (PK, FK → auth.users), `home_location` (free text, e.g. "Flint, MI"),
`unit_preference` (`fahrenheit`|`celsius`).

**`usage_counters`** — backs the per-user daily MCP call cap (§5): `user_id`, `day` (date, UTC),
`call_count`, PK `(user_id, day)`.

Put RLS on every user-scoped table (`auth.uid() = user_id`), applied identically whether the request
came via the web app's session JWT or the connector's OAuth access token, and add `force row level
security` on the three tables holding user data so the policy applies even to the table owner. Give
`usage_counters` a select policy for its owner but **no insert or update policy**, so a user cannot
reset their own quota — the counter is written only by the server, as the application role, scoped by
`user_id`. Make `categories` and `category_field_defs` readable by any signed-in user and writable
only by the service role.

### `closet_query_view` and the read-only role — the real security boundary

```sql
CREATE VIEW closet_query_view AS
SELECT id, user_id, category_id AS category, colors, brand, warmth_rating,
       formality, tags, notes, fields
FROM items;
```

This view is the **only** thing the safe query tool (§4.1) selects from. Create a dedicated read-only
Postgres role (`wardrobe_readonly`) granted `SELECT` on it and nothing else — not other columns of
`items`, and no writes of any kind. **This role, not the AST validation in §4.1, is the actual
security backstop; the validation in front of it is defense in depth.**

Two properties of the view are load-bearing, and both must be got right:

- **Do not grant the view to `authenticated`.** It is not `security_invoker`, so it runs with its
  owner's privileges and RLS on `items` does **not** apply through it. Granting it to `authenticated`
  (which is what exposing it to PostgREST does) would hand every signed-in user every other user's
  closet. Only `wardrobe_readonly` may select it, and every statement that does binds `user_id` as a
  parameter — that bound parameter, not RLS, is what keeps users apart on the query path.
- **Turn the project's Data API (PostgREST) off entirely**, because nothing in this app uses it: the
  frontend uses Supabase only for auth, and the backend reaches the database directly over asyncpg.
  That removes the exposure route above rather than merely defending against it. Keep the `revoke` in
  the migrations regardless, so the guarantee survives the Data API being switched back on.

Harden the role two further ways:

- Create it **without a password**, so no credential is committed; an operator sets one after
  applying the migration, and its connection string is a secret separate from the application role's.
- Give it `default_transaction_read_only`, a `statement_timeout` and an
  `idle_in_transaction_session_timeout` as **role-level** settings, so the guarantees hold even for a
  connection the application forgot to configure. Re-assert the first two per transaction as well.

Verify this, do not assume it: ship `db/tests/verify_readonly_role.sql` that asserts eight statements
are each refused (insert/update/delete through both the view and `items`; select on `items`,
`user_profile`, `auth.users`; and `create table`), runnable against a real Supabase project as
readily as a local one.

---

## 4. MCP tool contract

**Nine** tools, mounted via FastMCP at `/mcp`, authenticated via Supabase OAuth 2.1 (bearer token
validated against Supabase's JWKS endpoint). Carry the tool descriptions and the assistant-facing
instructional text in a dedicated module so they can be tuned without touching tool logic.

Validate tokens the way FastMCP's own `SupabaseProvider` does: JWKS endpoint
`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`, expected issuer `{SUPABASE_URL}/auth/v1`, RS256 and
ES256 accepted. Accept the legacy HS256 project secret as a fallback for projects not yet on
asymmetric signing — and sign local test tokens with it, so the auth path under test is the real one
rather than a test-only bypass. (In production the connector holds a proxy-issued reference token that
the OAuth proxy swaps for the stored Supabase token per call, then validates through this same
verifier — see §7.)

| Tool | Purpose | Key inputs | Returns |
|---|---|---|---|
| `get_closet_structure` | The assistant's one-shot map of the closet taxonomy | — | every category, its field template, the six common-field definitions, plus `query_instructions` |
| `add_item` | Add a single closet item | `category`, `colors?`, `brand?`, `warmth_rating?`, `formality?`, `tags?`, `notes?`, `fields?` | created item, or a clear error if the 200-item cap is hit |
| `update_item` | Edit an item | `item_id`, any of the above fields | updated item |
| `remove_item` | Delete an item | `item_id` | confirmation |
| `batch_add_items` | Add several items from one user message in one call (§4.2) | `items` (list, same shape as `add_item`, max 20) | per-item created/failed results |
| `query_closet_items` | The assistant's general filtering tool (§4.1) | `where_clause` (a SQL boolean predicate string) | matching items, including `tags`/`notes`/`colors`/`brand` |
| `list_category_items` | Simple listing for one category, no predicate | `category` | up to 100 items in that category, same field shape as the query tool |
| `get_closet_summary` | High-level overview without pulling every item | — | total count, per-category counts, distinct brands/tags, rough formality/warmth distribution |
| `get_user_profile` | Avoids re-asking for location/units | — | `home_location`, `unit_preference` |

Implement `list_category_items` and `get_closet_summary` as fixed, parameterized queries against
`closet_query_view` with no free-text predicate at all — meaningfully simpler and safer than
`query_closet_items`, and the ones to steer the assistant toward first for "show me category X" or
"what's generally in here". Do **not** add a `get_weather` tool — the assistant finds weather itself.

`get_closet_structure`'s `query_instructions` field must document, in plain text plus a short
example, exactly what `query_closet_items` accepts: the columns and types (`category`, `colors`
text[], `brand`, `warmth_rating` int, `formality` text, `tags` text[], `notes`, and `fields` jsonb
with its per-category keys), the allowed operators, and a couple of example predicates. Two properties
of the query surface must be stated there so the assistant does not waste a call learning them:

- **`fields->>'key'` returns text, so `<`/`<=`/`>`/`>=` on it compare alphabetically** (`> '30'` is
  true for `'9'`), and casts are rejected (§4.1) — so tell the assistant to use `=`/`IN` on
  number-typed fields and rank the results itself. `warmth_rating` is a real integer column and is the
  one numeric field that orders correctly; a `number`-typed *category* field is filterable but not
  rangeable.
- **`ANY(...)` is supported; `ALL(ARRAY[...])` is not** (sqlglot parses `ALL` there as an anonymous
  function the allowlist refuses). Advertise only `ANY`; the array-containment operators cover what
  `ALL` would.

### 4.1 Safe execution design for `query_closet_items`

This is the highest-risk component; build it to this design exactly. The assistant supplies a single
SQL **boolean expression** (a WHERE-clause predicate) — never a full statement. The server must:

1. Reject the input outright if it contains a semicolon; the keywords
   `SELECT`/`INSERT`/`UPDATE`/`DELETE`/`DROP`/`ALTER`/`GRANT`; a CTE; or a subquery. Run this textual
   pre-scan **with string literals blanked out**, so a legitimate predicate like
   `notes ILIKE '%goes with jeans%'` is not rejected for containing "with" — the scan's only output is
   a yes/no, it never feeds the SQL that gets built.
2. Parse the predicate with **sqlglot** (Postgres dialect) into an AST. sqlglot is a
   parser/transpiler, not a security validator, and its parser is lenient — so this step is a
   filtering convenience and a source of good error messages, **not the security boundary**.
3. Walk the AST against an **allowlist of node types** (not a denylist of bad words), rejecting any
   column outside the allowlist (`category`, `colors`, `brand`, `warmth_rating`, `formality`, `tags`,
   `notes`, `fields`), any `fields->>'x'` where `x` is not a real field name from
   `category_field_defs`, and any function call outside a small allowlist (comparison operators,
   `AND`/`OR`/`NOT`, `IN`, `BETWEEN`, `IS [NOT] NULL`, `ILIKE`, the array operators `&&`/`@>`/`<@`,
   `ANY`). Fold unquoted identifiers to lower case before the column check, exactly as Postgres
   resolves them, so `CATEGORY = 'jacket'` is accepted while a quoted `"user_id"` still matches no
   allowlist name. Anything the parser produces that is not explicitly permitted fails closed.
4. Re-serialize the validated AST back to SQL (never string-concatenate the raw input) and execute it
   as `SELECT * FROM closet_query_view WHERE user_id = $1 AND (<validated>) LIMIT 50` over the
   **read-only DB role** (§3), with `user_id` always bound as a parameter and never derived from
   assistant-supplied text. Check the re-serialized SQL for `;`, `--` and `/*` before running it.
5. Apply a server-side statement timeout regardless of what the predicate does, and enforce
   node-count and nesting-depth caps (checked textually before the recursive parser runs and again on
   the parsed tree) so a deeply nested predicate cannot exhaust the Python stack. Also re-run
   `sqlglot.parse` to confirm the input was a single expression, since `parse_one` silently keeps only
   the first statement.
6. Log rejected predicates, to spot abuse and tune the allowlist.

The real guarantees against SQL injection and cross-user exposure come from steps 3–4 (allowlisted
read-only role, parameterized `user_id`). Verify with an adversarial battery of at least ~35 attacks
(second statements, subqueries, `DROP TABLE`, comment truncation, `UNION`, casts, `pg_sleep`,
`pg_read_file`, `current_setting`, `version()`, the jsonb exists operator, a deep parenthesis bomb, a
long `OR` chain, a table-qualified column, a bind placeholder, a quoted `"user_id"`, and a direct
`user_id` comparison) — every one rejected, a set of legitimate predicates accepted, and a
match-everything predicate confirmed to return nothing belonging to a second user. Re-running that
battery after any change to the validator is the cheapest way to keep this component honest.

### 4.2 `batch_add_items` design

The point is to let one user message like "add three t-shirts — a red Nike one, a blue polo, and a
black hoodie" become **one MCP call**, not three (the call budget is tight, §5). Write the tool's
description as system-prompt-style instructions to the assistant, roughly:

> Before calling this tool, call `get_closet_structure` first if you have not already this
> conversation, so you know each category's required fields. Parse the user's message into one
> structured item object per item described, matching `add_item`'s shape. If a category's required
> fields are missing and cannot be reasonably inferred, ask the user before calling — don't guess or
> silently omit. Call once with the full list once you have enough for every item.

Cap input at **20 items per call** — well under the 200-item closet cap, enough to keep one call from
doing unbounded work. Process each item independently and return a per-item result (`created` with the
new item, or `failed` with a reason — invalid category, missing required field, or the 200-item cap
reached partway through). Make it **partial-success**, not all-or-nothing: if item 3 of 5 would exceed
the cap, keep items 1–2 and tell the assistant exactly which succeeded and which did not and why.

---

## 5. Limits and account controls

- **Per-user closet size cap**: 200 items (`MAX_ITEMS_PER_USER`). Enforce in `add_item` and
  `batch_add_items` on both surfaces — reject with a clear error once at the cap.
- **Per-user daily MCP usage cap**: track a **call count**, not a "session" count — a single
  Claude.ai conversation can be one long-lived session or many short ones, whereas a call counter is
  simple and robust. **Default 50 calls per user per UTC day** (`DAILY_MCP_CALL_LIMIT`), tracked in
  `usage_counters`, incremented atomically per call, checked before any tool body runs. Exceeding it
  returns a clear structured error the assistant can relay, including when the budget resets. Use a
  single conditional upsert so two concurrent calls cannot both slip past the cap and a refused call
  does not inflate the count. Carry `calls_remaining_today` in every tool response so the model reads
  the live figure rather than a number baked into its instructions — nothing needs rewriting if the
  cap changes. (`batch_add_items`, `list_category_items` and `get_closet_summary` exist partly to
  spend the budget efficiently: one call instead of N.)
- **Cross-server token replay**: Supabase does not implement RFC 8707 resource indicators, so an
  access token minted for a *different* MCP server backed by the *same* Supabase project would also
  validate here. For a project hosting only this app there is no second server to replay from — but do
  not add an unrelated MCP server to the same project without revisiting this. Provide
  `EXPECTED_TOKEN_AUDIENCE` but leave it **off by default**: every token from a project carries the
  same audience, so the claim cannot distinguish this case, and enforcing a guessed value fails closed
  on the connector path (the hardest one to test before it is live).
- **Account and data deletion**: a web-app-only action, deliberately **not** an MCP tool — deleting
  an entire closet is irreversible and should not be one careless chat message away. Give the Settings
  page a "Delete my account" flow with an explicit type-to-confirm step; the backend endpoint deletes
  the user's `items`, `user_profile` row, `usage_counters` rows, and the Supabase auth user itself.

---

## 6. Web app and REST API

### Web app (v1 scope)

- **Auth pages**: sign up / log in via Supabase Auth (magic link — least code, no password-reset
  flow to build).
- **Closet browser**: list of items, filterable by category and tags, each showing category, common
  fields, category-specific fields, tags and notes. Add/edit/delete directly from this view — not
  assistant-only, so mistakes can be corrected without a conversation. Render the add/edit form's
  category-specific inputs dynamically from the categories endpoint (`text`→input, `enum`→select over
  `allowed_values`, `number`→number input, `boolean`→checkbox), so adding a field in the database
  needs no frontend change.
- **Settings**: `home_location`, `unit_preference`, and the "Delete my account" flow.
- **"Connect your assistant" page**: static instructions plus the connector URL (`PUBLIC_BASE_URL` +
  `/mcp`).
- **OAuth consent screen** (`/oauth/consent`): Supabase is the authorization server but does not
  render consent itself — it redirects here with an `authorization_id`, and this page must fetch the
  request, show who is asking and what they asked for, and call approve/deny. Two requirements follow:
  the host must fall back to `index.html` for this path (met by same-origin hosting, §2), and the path
  must survive a signed-out user — the magic-link redirect has to return them to the full URL, query
  string included, or the pending authorization is lost.

No embedded chat interface in v1.

### REST API

All routes under `/api`, all requiring a Supabase access token, all resolving to the same `user_id`
as the MCP path.

| Method | Path | |
|---|---|---|
| `GET` | `/api/categories` | Every category with its field template — drives the dynamic form. |
| `GET` | `/api/items` | `category`, repeatable `tags` (AND), `limit`, `offset`. Returns `{items, total}`, where `total` is the whole closet, not the filtered count, so the UI can show how much of the 200-item cap is spent while a filter is applied. |
| `GET` | `/api/tags` | The caller's distinct tags with usage counts, derived from the items carrying them (§0). |
| `POST` | `/api/items` | 201 with the created item. |
| `GET` `PATCH` `DELETE` | `/api/items/{id}` | `PATCH` is partial; `DELETE` returns 204. |
| `GET` `PUT` | `/api/profile` | Never 404s — a user with no row gets the defaults. |
| `GET` | `/api/me` | Account summary: item count and limit, calls used today and the daily limit, and the connector URL the Connect page displays. |
| `GET` | `/api/limits` | The caps applying to this account. |
| `DELETE` | `/api/account` | Items, profile, usage counters, and the Supabase auth user. |

Return **one error envelope** for domain errors and request-validation failures alike:
`{"error": "<stable code>", "message": "<human readable>", "details": {…}}`, overriding FastAPI's
default `{"detail": [...]}` so a client has one shape to understand. Stable codes: `closet_full`,
`validation_failed`, `unknown_category`, `not_found`, `daily_limit_reached`, `unsafe_query`. Write
`message` to be shown to a person — which is also what makes it usable verbatim by the assistant on
the MCP side.

---

## 7. Deployment and infrastructure

Ship the whole product as **one container image** on an **ECS Express Mode** service in
**`us-east-2`**, deployed by GitHub Actions. Express Mode provisions the Fargate service, ALB, TLS
certificate, auto-scaling and a public HTTPS URL from just an image plus two IAM roles, so there is no
task-definition or service YAML to maintain. That generated HTTPS URL already meets the connector's
HTTPS requirement — a custom domain (§ below) is polish, added afterwards.

**Secrets as JSON keys, injected at task start.** Put everything sensitive in one Secrets Manager
secret and reference it key-by-key from the task definition, so no value lands in the task definition,
the console, or a workflow log. ECS reads secrets at task start only, so a rotated secret needs a
forced deployment to reach running tasks. The secret holds: `DATABASE_URL`, `READONLY_DATABASE_URL`
(a **different** credential — the read-only role is the real boundary, §3),
`SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET` (present but may be empty when the project uses
asymmetric keys — the key must exist or ECS refuses to start the task on the missing reference), and
`MCP_OAUTH_JWT_SIGNING_KEY` (below). Non-secret settings — `SUPABASE_URL`, `SUPABASE_ANON_KEY` (read
at build time and inlined into the bundle), `SUPABASE_OAUTH_CLIENT_ID`, `PUBLIC_BASE_URL`,
`DAILY_MCP_CALL_LIMIT` — are repository variables.

**CI IAM.** The target AWS account is a managed-IAM project that denies creating IAM identity
providers, so GitHub OIDC is not available. Instead, create a locked-down IAM user whose only
permission is to assume a deploy role; hold its access keys as GitHub secrets, and keep all deploy
permissions on the role (scope `iam:PassRole` to exactly the task-execution and infrastructure roles).
Rotate the key periodically. The account also needs its ECS/ELB/Application-Auto-Scaling
service-linked roles created once before the first Express Mode deploy.

**Database over the Supabase Session pooler (IPv4).** Supabase's direct connection is IPv6-only
without the paid add-on, and the default VPC that Express Mode places tasks in is IPv4-only. Route
**both** connection strings through the Session pooler (`aws-0-<region>.pooler.supabase.com:5432`,
session mode on port 5432 — not the transaction pooler's 6543 — so prepared statements and
per-transaction `SET`s survive), with the project ref folded into the username
(`postgres.<ref>`, `wardrobe_readonly.<ref>`). Percent-encode the passwords in the DSN.

**MCP connector OAuth — the app must front Supabase's OAuth server.** Claude's connector expects the
**MCP server itself** to be the OAuth authorization server: it does DCR and runs the whole flow
against *this* origin (`POST /register`, `/authorize`, `/token`,
`/.well-known/oauth-authorization-server`), and does **not** follow the RFC 9728
`authorization_servers` pointer to an external server. So the app runs a FastMCP `OAuthProxy` that
serves those OAuth endpoints at this origin and bridges them to Supabase. The token model: a connector
holds a proxy-issued **reference** token; on each `/mcp` request the proxy swaps it for the stored
upstream Supabase token and re-validates it through the same verifier the REST path uses — identity and
RLS unchanged. This needs a public upstream client registered with Supabase (redirect_uri
`{PUBLIC_BASE_URL}/auth/callback`, its public client id in `SUPABASE_OAUTH_CLIENT_ID`) and a stable
`MCP_OAUTH_JWT_SIGNING_KEY`. Because the proxy's token store is per-instance and in-memory, **pin the
service to one Fargate task** (`max-task-count: 1`); connected users re-authorize once after each
deploy. The upgrade path to horizontal scaling is a shared/persistent token store.

**Custom domain and TLS.** Once deployed, add a custom domain on the existing Express Mode ALB (not a
separate distribution): an ACM certificate alongside the ECS-managed `on.aws` cert (SNI selects), a
`:80`→`:443` redirect listener, and a host-header rule forwarding both hostnames to the target group.
DNS is a CNAME to the ALB (Route 53 is not required). Then set `PUBLIC_BASE_URL` to the custom origin
and add it to Supabase's Site/Redirect URLs. Note that `UpdateExpressGatewayService` can rewrite the
host-header rule and drop the custom host on a deploy, so re-check and re-apply it after every deploy.

**CI/CD.** On pushes to `main` touching `backend/`, `frontend/`, the Dockerfile or the workflow: apply
migrations to a throwaway Postgres, run lint + the frontend build + the test suite, then build the
image, push to ECR, deploy, and re-apply the ALB host rule. Do **not** run migrations against the real
Supabase project from the pipeline — apply those by hand before the deploy that depends on them.

The one-time Supabase and AWS setup — every manual step, with which value ends up in which environment
variable — must be captured in two runbooks (`docs/setup-supabase.md` and `infra/README.md`) as part
of the deployment ticket, because none of it can be done by the pipeline.

---

## 8. Tickets and orchestration

Eight tickets. **Orchestration:**

- **Ticket 1 is sequential and first** — everything depends on it.
- **Tickets 2 and 3 run concurrently** once 1 is done (the shared service layer from Ticket 1 is what
  lets them, against a frozen contract).
- **Ticket 4 depends on 3.** **Ticket 5 depends on 2** and can overlap 3–4.
- **Ticket 6 depends on 1–4 being stable** and can overlap 5. **Ticket 7 depends on 6.**
- **Ticket 8 is last** and depends on everything.

```
1 ─┬─▶ 2 ───────────▶ 5 ─┐
   └─▶ 3 ──▶ 4 ──────────┼─▶ 6 ──▶ 7 ──▶ 8
                          ┘
```

### Ticket 1 — Foundations: schema, RLS, safe-query infrastructure, shared services, FastAPI skeleton
*Sequential — do this first.*

- Migrations for `categories`, `category_field_defs`, `items` (six common-field columns plus `fields`
  JSONB), `user_profile`, `usage_counters`, and the `closet_query_view` view, with all the check
  constraints and the `updated_at` trigger (§3).
- Seed the 26 categories and ~75 category-specific field defs (§3).
- Create the `wardrobe_readonly` role with `SELECT`-only on `closet_query_view` (no password,
  role-level read-only/timeout settings), and ship `db/tests/verify_readonly_role.sql`.
- RLS (with `force row level security`) on every user-scoped table, and the `usage_counters`
  no-insert/no-update asymmetry.
- Scaffold FastAPI: project structure, two asyncpg pools (app role + read-only role), a shared
  auth-validation dependency accepting either a session JWT or an OAuth access token and resolving to
  `user_id`, a health check reporting both pools — **and the shared service layer** (item CRUD,
  category-field validation, profile, usage counter) that Tickets 2 and 3 both build on (§2).
- **Definition of done**: migrations apply cleanly to a fresh Postgres; the read-only role's write
  attempts all fail (`verify_readonly_role.sql` passes); a manually-issued test JWT validates through
  the auth dependency; RLS blocks cross-user reads with two seeded test users.

### Ticket 2 — REST API for closet management
*Depends on 1. Concurrent with 3.*

- CRUD for items, scoped to the authenticated user, enforcing the 200-item and five-colour caps via
  the shared services; the categories endpoint (with field templates); profile read/update; the
  derived `/api/tags`; `/api/me`, `/api/limits`; account deletion (cascading through items, profile,
  usage counters, and the Supabase auth user).
- The unified error envelope (§6) for domain errors and request-validation alike.
- **Definition of done**: create → list → filter → update → delete works end-to-end for a test user;
  a 201st item and a 6th colour are each rejected clearly; account deletion leaves no residual rows.

### Ticket 3 — MCP server: tools, safe query execution, rate limiting
*Depends on 1. Concurrent with 2.*

- Mount FastMCP as an ASGI sub-app at `/mcp`; validate Supabase OAuth access tokens via JWKS,
  resolving to the same `user_id`/RLS context as REST.
- Implement all nine tools (§4) over the shared services — `get_closet_structure`, `add_item`,
  `update_item`, `remove_item`, `batch_add_items`, `query_closet_items`, `list_category_items`,
  `get_closet_summary`, `get_user_profile` — with `query_instructions` and the `batch_add_items`
  instructional description.
- `query_closet_items` per §4.1 (sqlglot AST allowlist, read-only role, parameterized `user_id`, row
  limit, statement timeout, depth/size caps, rejection logging); `list_category_items` and
  `get_closet_summary` as fixed queries; `batch_add_items` per §4.2.
- The per-user daily call-count cap (§5) as a check-and-increment before each tool body, and the
  200-item cap inside `add_item`/`batch_add_items`.
- A test client (official MCP Python SDK) exercising all nine tools, the batch partial-success case,
  and the full adversarial `where_clause` battery.
- **Definition of done**: happy-path calls succeed with correctly-shaped, RLS-scoped results across
  all nine tools; the batch partial-success case behaves as designed; every adversarial predicate is
  rejected before touching the database; the daily cap visibly triggers when lowered for testing.

### Ticket 4 — MCP connector OAuth proxy
*Depends on 3.*

- A FastMCP `OAuthProxy` that serves `/authorize`, `/token`, `/register`, `/auth/callback` and the
  authorization-server metadata at this origin and bridges them to Supabase, with the
  reference-token-swap model (§7); re-expose the proxy routes at the root since FastMCP registers
  them on the `/mcp` sub-app, and serve the transport at both `/mcp` and `/mcp/`.
- Register the Supabase upstream client and wire `SUPABASE_OAUTH_CLIENT_ID` +
  `MCP_OAUTH_JWT_SIGNING_KEY`; when either is absent, fall back to advertising Supabase as an external
  authorization server. Pin the service to a single task (in-memory token store).
- **Definition of done**: a real Claude.ai remote connector completes DCR and the OAuth flow against
  the app origin (via a tunnel for local testing) and successfully calls `get_closet_structure`
  followed by `query_closet_items`.

### Ticket 5 — React + Vite frontend
*Depends on 2. Can overlap 3–4.*

- Auth pages (magic link); closet browser (list/filter by category and tags, view/edit/delete); the
  dynamic add/edit form (six common fields plus category-specific fields rendered from the categories
  endpoint); Settings (`home_location`, `unit_preference`, type-to-confirm account deletion); the
  "Connect your assistant" page; and the **`/oauth/consent` screen** (§6), including the magic-link
  redirect that preserves a pending `authorization_id` across sign-in.
- The API client uses relative URLs (same origin); one fetch wrapper attaches the Supabase token and
  unwraps the error envelope into a typed error.
- **Definition of done**: a new user can sign up, add items across at least five categories with
  colors/brand/tags/notes, filter/edit/delete them, and delete their account — all without the
  assistant.

### Ticket 6 — Deployment: Docker + ECS Express Mode
*Depends on 1–4 being stable. Can overlap 5.*

- One Dockerfile building the frontend and serving it alongside the API and MCP sub-app on one origin
  (§2); the ECS Express Mode service, Secrets Manager secret, the three IAM roles and the CI
  access-key user (§7); the deploy workflow (migrations-on-throwaway-Postgres → lint → build → test →
  build image → push → deploy → re-apply ALB rule).
- The two runbooks (`docs/setup-supabase.md`, `infra/README.md`) capturing every manual step and the
  environment-variable map.
- **Definition of done**: the deployed URL serves `/`, `/api` and `/mcp`; `/health` reports both DB
  pools healthy; a real Claude.ai connector completes OAuth against the deployed instance (not just
  localhost) and calls a tool.

### Ticket 7 — Custom domain and TLS
*Depends on 6.*

- ACM certificate for the custom domain added to the existing Express Mode ALB (SNI alongside the
  `on.aws` cert), a `:80`→`:443` redirect, and a host-header rule forwarding both hostnames; DNS
  CNAME to the ALB; `PUBLIC_BASE_URL` and Supabase Site/Redirect URLs updated; the post-deploy
  host-rule re-check documented (§7).
- **Definition of done**: the custom domain serves the whole app over HTTPS, the generated `on.aws`
  URL still works, and the connector works against the custom domain.

### Ticket 8 — End-to-end verification and polish
*Last — depends on everything.*

- A seed script for realistic demo data (items across most categories, varied colors/warmth/
  formality/tags, including differentiating details like `floral`, `multi-color`, and rain notes).
- An end-to-end smoke test through Claude.ai: add a single item via conversation (assistant consults
  `get_closet_structure`); describe several items at once and confirm `batch_add_items` is used; "what's
  in my closet" → `get_closet_summary`; "what shoes do I have" → `list_category_items`; and an outfit
  recommendation for a specific occasion — confirm the assistant finds weather itself, builds a
  sensible `where_clause`, and returns a reasonable pick that references tags/notes where they matter.
  Keep an eye on total calls spent against the 50/day cap.
- Re-run the adversarial query battery against the deployed instance; confirm the daily and 200-item
  caps read clearly when hit in a real conversation; finish the README (local dev, connecting an
  assistant, environment variables, deployment).
- **Definition of done**: the full "sign up → connect assistant → add item via conversation → get a
  recommendation" flow works without manual intervention; the deployed adversarial battery passes; a
  new developer can follow the README to stand up a local instance from scratch.
