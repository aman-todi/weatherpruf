# Wardrobe MCP App — v1 Build Spec

*Revision 3 — adds the connector-vs-plugin reasoning as a new §1, plus the earlier revision's
category/tooling/limits changes. Superseded sections from earlier drafts are not shown separately;
this document is the current source of truth.*

## 0. Background

This is a personal project (learning + portfolio, expected traffic: very light — single-digit
concurrent users). The core idea: a web app manages a user's digital closet, but the **primary
interface for day-to-day use is an AI assistant** (Claude.ai) connected via MCP (Model Context
Protocol) — not the web app itself. The web app exists to onboard, manage account/auth, browse the
closet visually, and instruct the user how to connect their assistant. All day-to-day interaction
("add a t-shirt to my closet", "what should I wear for my dinner date tonight?") happens through
the assistant, which calls MCP tools against this app's backend.

Key design principle carried through this whole spec: **the backend does not do outfit
reasoning, and it does not fetch weather itself.** It exposes the closet's structure and a safe way
to query it. The assistant is expected to already know or ask for the user's location, find current
weather using its own capabilities (general knowledge, web search — not this app's job), understand
the closet's category/field taxonomy from `get_closet_structure`, construct a filtering query
against that structure, and combine the returned items into a recommendation itself. This keeps the
backend to CRUD plus one safe query-execution path.

### Decisions locked in before this document

- **Auth**: Supabase Auth for both the web app's own login/session and the MCP OAuth 2.1
  authorization server Claude.ai authenticates against. Tokens carry standard Supabase claims and
  work directly with Row Level Security (RLS), so the same policies protect REST and MCP calls
  alike. This still relies on Dynamic Client Registration (the now-deprecated-but-functional MCP
  client onboarding path) rather than Client ID Metadata Documents — a known, accepted limitation.
- **Database**: Supabase Postgres.
- **MCP server**: FastMCP (v3), mounted directly inside the FastAPI app as an ASGI sub-application
  — one process, one deploy, shared auth and data-access layer between REST and MCP.
- **Compute**: AWS ECS Express Mode (current AWS-recommended replacement for App Runner, which
  stopped accepting new customers as of April 30, 2026).
- **Frontend**: React + Vite.
- **No server-side outfit reasoning, no server-side weather integration.** The assistant does both
  using its own knowledge/tools; the backend only serves closet data.
- **Claude integration surface**: a remote MCP server with OAuth (a connector), not a plugin — see
  §1 for the reasoning.

### Explicit non-goals for v1

- No mobile app (unrelated to any prior native mobile wardrobe project — do not reuse or reference
  that schema/decisions).
- No in-app chat interface embedded in the web app.
- No photo upload / image storage for items — text-based categories, fields, and tags only.
- No subscription tiers or billing.
- No push notifications or scheduled reminders.
- No multi-tenant / team accounts.
- No Claude plugin/skill bundle — see §1.

### A note on one ambiguous piece of earlier feedback

The instruction "I don't think we need user 'tags', name and other info can probably go into one
user/user_profile table" is read here as: **drop the separate normalized `tags` + `item_tags`
tables** — tags become a plain denormalized array directly on each item, no per-user tag registry.
The `user_profile` table remains the single place for account-level info (`home_location`,
`unit_preference`); nothing else is added to it since no other specific field was named. Flag if
this misreads the intent.

---

## 1. Claude integration surface: connector vs. plugin

Anthropic's own guidance for third-party integrations treats an MCP server and a plugin as
complementary, not interchangeable: the recommended pattern is to build a remote MCP server with
OAuth first, for connectivity and core functionality, and only optionally add a plugin that bundles
skills to help users get more out of that server. They serve different purposes:

| | MCP server (connector) | Plugin |
|---|---|---|
| Mental model | "Claude can call your API" | "Claude knows how to *use* your product" |
| Contains | Tools, prompts, resources | Skills, MCP connector references, slash commands |
| Works in | Claude.ai (web/mobile), Desktop, Cowork, Claude Code | Claude Code and Cowork only |

That last row settles it for this app: the stated primary interface is Claude.ai itself (plain
web/mobile chat), and plugins do not currently run there — only in Claude Code and Cowork. A
plugin, with or without skills bundled in, simply wouldn't reach the target user. **A remote MCP
server with OAuth (a connector) is not just the right choice here, it's the only one that reaches
this app's actual audience.**

It's worth being precise about *why* the "assistant-side work" this app leans on — teaching the
assistant how to build a safe `where_clause` for `query_closet_items`, or how to parse a multi-item
message for `batch_add_items` — is designed the way it already is (§4) rather than reaching for a
bundled skill or MCP's own "prompts" primitive:

- **Skills** have the same reach problem as plugins in general — they only apply in Claude Code and
  Cowork, not plain Claude.ai chat.
- **MCP prompts** are the wrong tool for a different reason: they're user-invoked, closer to a
  slash command a person explicitly picks, not something the model consults automatically while
  deciding how to call a tool. Client support for the prompts primitive is also inconsistent across
  MCP clients.
- What reliably influences the model's own autonomous tool-use behavior, on every client including
  plain Claude.ai chat, is exactly what this spec already does: rich tool *descriptions* (which the
  model reads when deciding whether and how to call a tool) and instructional content returned
  *inside a tool's own response* — like `get_closet_structure`'s `query_instructions` field, and the
  system-prompt-style guidance embedded directly in `batch_add_items`'s tool description. No design
  change follows from this — it validates the existing approach rather than replacing it.

**v1 scope: connector only**, per Tickets 1–6. A companion plugin — bundling a skill with a deeper
usage playbook, referencing the same MCP server URL — is a reasonable v2 idea for users specifically
on Claude Code or Cowork, but it's additive for a subset of users, not required, and explicitly out
of scope for v1.

---

## 2. Architecture overview

```
┌─────────────────┐        ┌──────────────────────────────────────────┐
│   React + Vite   │  REST  │              FastAPI app                │
│   web frontend   │───────▶│  ┌────────────────────────────────────┐  │
│                  │        │  │  REST routes (web app: auth-gated  │  │
│  - signup/login  │        │  │  via Supabase session JWT)         │  │
│  - closet browser│        │  │  - items CRUD, categories           │  │
│  - delete account│        │  │  - account deletion                 │  │
│  - "connect your │        │  └────────────────────────────────────┘  │
│    assistant"    │        │  ┌────────────────────────────────────┐  │
│    instructions  │        │  │  FastMCP sub-app, mounted at /mcp  │  │
└─────────────────┘        │  │  (auth-gated via Supabase OAuth    │  │
                             │  │  access token, validated via JWKS) │  │
        ▲                   │  │  - get_closet_structure             │  │
        │                   │  │  - add_item / update_item /         │  │
        │ OAuth 2.1          │  │    remove_item / batch_add_items    │  │
        │ (DCR, PKCE)        │  │  - query_closet_items (safe SQL)   │  │
        │                    │  │  - list_category_items              │  │
┌───────┴─────────┐          │  │  - get_closet_summary                │  │
│   Claude.ai      │          │  │  - get_user_profile                 │  │
│  (connector,     │          │  └────────────────────────────────────┘  │
│   finds weather   │          └──────────────────────────────────────────┘
│   itself; not     │                          │
│   this app's job) │                          ▼
└─────────────────┘                 ┌──────────────────┐      ┌──────────────┐
                                     │  Supabase Auth    │      │  Supabase    │
                                     │  (OAuth 2.1 AS +  │      │  Postgres    │
                                     │  session auth)    │      │  (RLS +      │
                                     └──────────────────┘      │  read-only   │
                                                                 │  query role) │
                                                                 └──────────────┘
```

No external weather API integration in this app at all, and no Claude plugin — both scoped out per
§0/§1. Both the REST API and MCP tools sit behind the same FastAPI process, ECS Express Mode
service, and Supabase project.

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
  `sleeve_length`, `rise`, `heel_height`, `waterproof`, `neckline`) — still data-driven via
  `category_field_defs`, still stored in the `fields` JSONB column, queried via `fields->>'key'`.
- **Tags**: free-form strings the user attaches to an item (e.g. `date-night`, `floral`,
  `multi-color`). Denormalized as a plain array directly on the item — no separate registry table.
- **Notes**: free text on an item for anything that doesn't fit a structured field.

### Standard predefined category seed list

Seeded once in Ticket 1, extendable later via `category_field_defs` without a redeploy:

`tshirt`, `polo`, `shirt`, `blouse`, `tank_top`, `sweater`, `sweatshirt`, `hoodie`, `jacket`, `coat`,
`blazer`, `cardigan`, `vest`, `jeans`, `trousers`, `shorts`, `lower` *(joggers/leggings/sweatpants)*,
`skirt`, `dress`, `jumpsuit`, `two_piece_suit`, `shoes`, `belt`, `hat`, `scarf`, `tie`.

`shoes` carries a category-specific enum field (e.g. `shoe_type`: sneaker/boot/sandal/formal/other)
rather than being split into separate shoe categories, keeping the category list from exploding
while still letting the assistant distinguish shoe types via a field.

### Tables (Supabase Postgres)

**`categories`**
| column | type | notes |
|---|---|---|
| `id` | text (PK) | e.g. `tshirt`, `jeans` |
| `display_name` | text | e.g. "T-Shirt" |
| `sort_order` | int | for UI ordering |

**`category_field_defs`** — category-specific fields only (not the six common fields, which are
columns on `items` directly)
| column | type | notes |
|---|---|---|
| `id` | uuid (PK) | |
| `category_id` | text (FK → categories) | |
| `field_name` | text | e.g. `sleeve_length` |
| `field_type` | text | `text` \| `enum` \| `number` \| `boolean` |
| `required` | boolean | |
| `allowed_values` | text[] | populated only for `enum` type |
| `display_order` | int | |

**`items`**
| column | type | notes |
|---|---|---|
| `id` | uuid (PK) | |
| `user_id` | uuid (FK → auth.users) | RLS-scoped |
| `category_id` | text (FK → categories) | |
| `colors` | text[] | max 5 elements — enforce via a check constraint (`array_length(colors,1) <= 5`) and app-level validation |
| `brand` | text | optional |
| `warmth_rating` | int | optional, e.g. 1 (very light) – 5 (heaviest) |
| `formality` | text | optional enum, e.g. `casual` \| `smart_casual` \| `formal` \| `athletic` |
| `tags` | text[] | denormalized, free-form |
| `notes` | text | optional free text |
| `fields` | jsonb | category-specific fields only, validated against `category_field_defs` |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

**`user_profile`**
| column | type | notes |
|---|---|---|
| `user_id` | uuid (PK, FK → auth.users) | |
| `home_location` | text | free-text location, e.g. "Flint, MI" |
| `unit_preference` | text | `fahrenheit` \| `celsius` |

**`usage_counters`** — backs the per-user daily MCP call cap (§5)
| column | type | notes |
|---|---|---|
| `user_id` | uuid (FK → auth.users) | |
| `day` | date (UTC) | |
| `call_count` | int | incremented per MCP tool call |
| | | primary key `(user_id, day)` |

RLS policy shape: every table scoped by `user_id` restricts rows to `auth.uid() = user_id`,
identically whether the request came in via the web app's session JWT or Claude.ai's OAuth access
token.

**`closet_query_view`** — a real Postgres view, the *only* thing the safe query tool (§4) is
allowed to select from:

```sql
CREATE VIEW closet_query_view AS
SELECT id, user_id, category_id AS category, colors, brand, warmth_rating,
       formality, tags, notes, fields
FROM items;
```

A dedicated **read-only Postgres role** is granted `SELECT` on `closet_query_view` only — nothing
else, not even other columns of `items` directly, and no write privileges of any kind. This role,
not the AST validation described in §4, is the actual security backstop.

---

## 4. MCP tool contract

Eight tools, mounted via FastMCP at `/mcp`, authenticated via Supabase OAuth 2.1 (bearer token
validated against Supabase's JWKS endpoint).

| Tool | Purpose | Key inputs | Returns |
|---|---|---|---|
| `get_closet_structure` | One-shot: the assistant's map of the entire closet taxonomy | — | every category, its category-specific field template, the six common-field definitions, plus `query_instructions` (see below) |
| `add_item` | Add a single closet item | `category`, `colors?`, `brand?`, `warmth_rating?`, `formality?`, `tags?`, `notes?`, `fields?` | created item, or a clear error if the 200-item cap is hit |
| `update_item` | Edit an item | `item_id`, any of the above fields | updated item |
| `remove_item` | Delete an item | `item_id` | confirmation |
| `batch_add_items` | Add several items from one user message in a single call — see §4.2 | `items` (list of objects, same shape as `add_item`'s inputs, max 20 per call) | per-item created/failed results |
| `query_closet_items` | The assistant's general filtering tool — see §4.1 | `where_clause` (a SQL boolean predicate string) | matching items (including `tags`, `notes`, `colors`, `brand` — deliberately passed through since they carry differentiating info like "floral" or "multi-color") |
| `list_category_items` | Simple convenience listing for one category, no predicate needed | `category` | up to 100 items in that category, same full field shape as `query_closet_items` |
| `get_closet_summary` | High-level overview without pulling every item | — | total item count, per-category counts, distinct brands/tags seen, and a rough formality/warmth distribution |
| `get_user_profile` | Avoids re-asking for location/units every time | — | `home_location`, `unit_preference` |

`list_category_items` and `get_closet_summary` are both implemented as fixed, low-risk queries
against `closet_query_view` (no free-text predicate involved at all) — meaningfully simpler and
safer than `query_closet_items`, and worth reaching for first when the assistant's need is just "show
me category X" or "what's generally in here" rather than an occasion/weather-driven filter.

There is deliberately no `get_weather` tool — the assistant finds weather itself.

`get_closet_structure`'s `query_instructions` field documents, in plain text plus a short example,
exactly what `query_closet_items` will accept: the column list and types available (`category`,
`colors` text[], `brand`, `warmth_rating` int, `formality` text, `tags` text[], `notes`, and `fields`
jsonb with its per-category keys), the allowed operators, and one or two example predicates (e.g.
`category IN ('jacket','coat') AND warmth_rating >= 4` or `tags && ARRAY['floral']`). This is the
"standard to build the query" the assistant is given up front so it doesn't have to guess syntax —
and, per §1, this is deliberately carried in tool description/response content rather than a skill
or an MCP prompt, since that's what the model actually consults automatically on every client.

### 4.1 Safe execution design for `query_closet_items`

This is the highest-risk component in the whole build and deserves its own section. The assistant
supplies a single SQL **boolean expression** (a WHERE-clause predicate) — never a full statement.
The server:

1. Rejects the input outright if it contains a semicolon, the keywords `SELECT`/`INSERT`/
   `UPDATE`/`DELETE`/`DROP`/`ALTER`/`GRANT`, a CTE, or a subquery — this should be a bare predicate,
   nothing else.
2. Parses the predicate with **sqlglot** (Postgres dialect) into an AST. Note explicitly:
   sqlglot's own documentation states it is a parser/transpiler, not a security validator, and its
   parser is intentionally lenient — so this step is a filtering convenience and a source of good
   error messages for the assistant, **not the security boundary**.
3. Walks the AST and rejects anything referencing a column outside the allowlist
   (`category`, `colors`, `brand`, `warmth_rating`, `formality`, `tags`, `notes`, `fields`), any
   `fields->>'x'` access where `x` isn't a real field name from `category_field_defs`, and any
   function call outside a small allowlist (comparison operators, `AND`/`OR`/`NOT`, `IN`,
   `BETWEEN`, `IS [NOT] NULL`, `ILIKE`, the array operators `&&`/`@>`/`<@`, `ANY`/`ALL`).
4. Re-serializes the validated AST back to SQL text (never string-concatenates the assistant's raw
   input) and executes it as `SELECT * FROM closet_query_view WHERE user_id = $1 AND (<validated>)
   LIMIT 50` over the **read-only DB role** described in §3, with `user_id` always bound as a
   parameter and never derived from assistant-supplied text.
5. Applies a server-side statement timeout (a couple of seconds) regardless of what the predicate
   does.
6. Logs rejected predicates, to spot abuse and to tune the allowlist over time.

The actual guarantees against SQL injection or data exposure across users come from steps 3–4
(allowlisted read-only role, parameterized `user_id`) — the AST walk is defense-in-depth on top of
that, not a replacement for it.

### 4.2 `batch_add_items` design

The point of this tool is to let one user message like "add three t-shirts — a red Nike one, a
blue polo, and a black hoodie" become **one MCP call**, not three (call budget is tight per §5).
The tool's description text is written deliberately as system-prompt-style instructions to the
assistant, roughly:

> Before calling this tool, call `get_closet_structure` first if you haven't already this
> conversation, so you know each category's required fields. Parse the user's message into one
> structured item object per item they described, matching `add_item`'s shape. If a category's
> required fields are missing and can't be reasonably inferred from what the user said, ask the
> user for them before calling this tool — don't guess or silently omit required fields. Call this
> tool once with the full list once you have enough information for every item.

Input is capped at **20 items per call** — well under the 200-item closet cap, but enough to keep
one call from doing unbounded work. The tool processes each item independently and returns a
per-item result (`created` with the new item, or `failed` with a reason — invalid category, missing
required field, or the 200-item closet cap reached partway through the batch). This is deliberately
partial-success rather than all-or-nothing: if item 3 of 5 would exceed the closet cap, items 1–2
are still kept and the response tells the assistant exactly which items succeeded and which didn't
and why, so it can report that back accurately rather than the user losing everything over one
failure.

---

## 5. Limits and account controls

- **Per-user closet size cap**: 200 items. Enforced in `add_item` and `batch_add_items` (both REST
  and MCP) — reject with a clear error once at the cap.
- **Per-user daily MCP usage cap**: tracked as a **call count**, not a "session" count — an MCP
  connection/session doesn't map cleanly onto a meaningful daily quota (a single ongoing Claude.ai
  conversation can be one long-lived session covering an entire day's worth of questions, or many
  short ones), whereas a call counter is simple, robust, and easy to reason about. **Cap value: 10
  calls per user per UTC day**, tracked in `usage_counters`, incremented atomically per call,
  checked before executing any tool body. Configurable via an environment variable. Exceeding it
  returns a clear structured error the assistant can relay ("today's usage limit is reached, try
  again tomorrow").

  Worth knowing going in that 10/day is tight — a `get_closet_structure` call plus a couple of
  `query_closet_items` calls across a few recommendation questions is most of a day's budget.
  `batch_add_items`, `list_category_items`, and `get_closet_summary` were added partly to help
  stretch this budget further (one batch call instead of N `add_item` calls; one summary call
  instead of several targeted queries), but if 10/day turns out too tight once you're actually
  using it, it's a one-line env var change to raise.
- **Account and data deletion**: a web-app-only action (deliberately **not** exposed as an MCP
  tool — deleting an entire closet is irreversible and shouldn't be one careless chat message away).
  Settings page has a "Delete my account" flow with an explicit confirmation step; the backend
  endpoint deletes all of the user's `items`, their `user_profile` row, their `usage_counters` rows,
  and the Supabase auth user itself.

---

## 6. Web app (v1 scope)

- **Auth pages**: sign up / log in via Supabase Auth (magic link — least code).
- **"Connect your assistant" page**: static instructions + the MCP server URL to paste into
  Claude.ai's remote connector setup.
- **Closet browser view**: list of items, filterable by category and tags, each item showing its
  category, common fields (colors, brand, warmth rating, formality), category-specific fields,
  tags, and notes. Add/edit/delete from this view directly (not assistant-only — useful for
  correcting mistakes without a conversation).
- **Settings**: `home_location`, `unit_preference`, and the "Delete my account" flow.

No embedded chat interface in v1.

---

## 7. Tickets for Claude Code

Six tickets. **Ticket 1 first.** **Tickets 2 and 3 concurrent after that.** **Ticket 4 depends on
Ticket 2.** **Ticket 5 can scaffold anytime, finishes after 1–3 are stable.** **Ticket 6 last.**

### Ticket 1 — Foundations: Supabase schema, RLS, safe-query infrastructure, FastAPI skeleton
*Sequential — do this first.*

- Migrations for `categories`, `category_field_defs`, `items` (with the six common-field columns
  plus `fields` JSONB), `user_profile`, `usage_counters`, plus the `closet_query_view` view.
- Seed `categories` with the full list in §3, and `category_field_defs` with a sensible
  category-specific field template per category (at minimum: `shoes` gets `shoe_type`; garments
  with sleeves get `sleeve_length`; bottoms get `fit`; anything reasonably get `material`).
- Create the dedicated read-only Postgres role with `SELECT`-only grant on `closet_query_view`, and
  confirm directly (a manual test, not just an assumption) that this role cannot `INSERT`/`UPDATE`/
  `DELETE` even against `closet_query_view` or `items`.
- RLS policies on every user-scoped table.
- Enable Supabase Auth's OAuth 2.1 server capability alongside standard session auth.
- Scaffold FastAPI: project structure, Supabase client wiring, shared auth-validation dependency
  accepting either a session JWT or an OAuth access token and resolving to `user_id`, health check.
- **Definition of done**: migrations apply cleanly to a fresh project; the read-only role's write
  attempts fail as expected; a manually-issued test JWT validates via the FastAPI auth dependency;
  RLS confirmed to block cross-user reads with two seeded test users.

### Ticket 2 — REST API for closet management
*Depends on Ticket 1. Can run concurrently with Ticket 3.*

- CRUD endpoints for items, scoped to the authenticated user, enforcing the 200-item cap and the
  5-color-max constraint on write.
- Endpoint to list categories and their category-specific field templates (for the frontend's
  dynamic forms).
- Endpoint for reading/updating `user_profile`.
- Account deletion endpoint: cascades through items, user_profile, usage_counters, and the
  Supabase auth user itself.
- **Definition of done**: full create → list → filter → update → delete flow works end-to-end for a
  test user; attempting a 201st item is rejected with a clear error; attempting a 6th color is
  rejected; account deletion endpoint leaves no residual rows for that `user_id` anywhere.

### Ticket 3 — MCP server: tools, safe query execution, OAuth, rate limiting
*Depends on Ticket 1. Can run concurrently with Ticket 2.*

- Mount FastMCP as an ASGI sub-app at `/mcp`; wire auth to validate Supabase OAuth access tokens via
  JWKS, resolving to the same `user_id`/RLS context as the REST path.
- Implement `get_closet_structure`, `add_item`, `update_item`, `remove_item`, `get_user_profile` —
  reusing Ticket 2's data-access functions where possible rather than duplicating query logic;
  coordinate on shared service functions if both tickets land close together.
- Implement `query_closet_items` per the full safe-execution design in §4.1: sqlglot-based AST
  allowlist validation, execution over the read-only role, parameterized `user_id`, row limit,
  statement timeout, rejection logging.
- Implement `list_category_items` and `get_closet_summary` as fixed parameterized queries against
  `closet_query_view` (no assistant-supplied predicate text involved for either).
- Implement `batch_add_items` per §4.2: the 20-item-per-call cap, per-item partial-success/failure
  results, and the closet-cap-reached-partway-through case.
- Implement the per-user daily call-count cap (§5, 10/day) as a check-and-increment against
  `usage_counters` before each tool body runs, and the 200-item cap inside `add_item` and
  `batch_add_items`.
- Write a test client script (using the official MCP Python SDK's `Client`) exercising all eight
  tools against a locally running server, including: a `batch_add_items` call mixing valid and
  invalid items to confirm partial success works as designed, and a battery of adversarial
  `where_clause` inputs for `query_closet_items` — a semicolon-separated second statement, an
  attempted subquery, a `DROP TABLE`, a column not in the allowlist, an attempt to reference
  another user's `user_id` directly, and a disallowed function call — all must be rejected with
  clear errors, none may reach the database as written.
- **Definition of done**: the test script's happy-path calls succeed with correctly-shaped,
  RLS-scoped results across all eight tools; the `batch_add_items` partial-success case behaves as
  designed; every adversarial `where_clause` input in the battery above is rejected before touching
  the database; a manual Claude.ai remote-connector test (via a tunneled local instance) completes
  the OAuth flow and successfully calls `get_closet_structure` followed by `query_closet_items`;
  the daily call cap visibly triggers when artificially lowered to a small number for testing.

### Ticket 4 — React + Vite frontend
*Depends on Ticket 2's endpoints being stable.*

- Auth pages via Supabase Auth client SDK.
- Closet browser: list/filter by category and tags, view/edit/delete an item.
- Dynamic add/edit item form: the six common fields (color picker allowing up to 5, brand, warmth
  rating, formality, free-form tag input, notes) plus category-specific fields rendered from the
  field-template endpoint.
- Settings page: `home_location`, `unit_preference`, and a "Delete my account" flow with an
  explicit confirmation step (e.g. type-to-confirm) before calling the deletion endpoint.
- "Connect your assistant" static instructions page with the MCP server URL.
- **Definition of done**: a new user can sign up, add items across at least five categories with
  colors/brand/tags/notes set, see them correctly filtered in the browser, edit/delete them, and
  successfully delete their account (confirmed gone from the backend afterward) — all without
  touching the assistant.

### Ticket 5 — Deployment: Docker + ECS Express Mode
*Dockerfile/config can be scaffolded anytime; final deploy depends on Tickets 1–3.*

- Single Dockerfile for the FastAPI app (serving both REST and the mounted MCP sub-app).
- ECS Express Mode service definition, environment/secrets wiring for Supabase keys (including the
  read-only role's connection string, kept separate from the main app's DB credentials), health
  checks.
- Custom domain + TLS (required for Claude.ai's remote connector, which needs HTTPS).
- **Definition of done**: the deployed URL serves both REST and `/mcp`; a real Claude.ai remote
  connector completes OAuth against the deployed instance (not just localhost) and successfully
  calls a tool.

### Ticket 6 — End-to-end verification and polish
*Last — depends on everything above.*

- Seed script for realistic demo data: items across most categories, varied colors/warmth/
  formality/tags, enough to make filtering demos meaningful.
- End-to-end smoke test through Claude.ai covering: adding a single item via conversation (assistant
  should consult `get_closet_structure` for what to ask), describing several items to add at once
  in one message and confirming the assistant uses `batch_add_items` rather than several `add_item`
  calls, asking "what's in my closet" and confirming `get_closet_summary` is used, asking to see one
  category (e.g. "what shoes do I have") and confirming `list_category_items` is used, and finally
  asking for an outfit recommendation for a specific occasion — confirm the assistant finds weather
  itself, builds a sensible `where_clause` via `query_closet_items`, and returns a reasonable top
  1–2 picks using the seeded data, referencing tags/notes where they matter (e.g. picking up on
  "floral" or "multi-color"). Keep an eye on total call count spent across this whole test given the
  10/day cap.
- Re-run the adversarial query battery from Ticket 3 once more against the deployed instance, not
  just locally.
- Confirm the daily call cap and 200-item cap behave sensibly when hit through a real conversation
  (clear, non-confusing message back to the user).
- README: local dev setup, how to connect an assistant, environment variables, deployment steps.
- **Definition of done**: the full "sign up → connect assistant → add item via conversation → get a
  recommendation" flow works without manual intervention; the deployed adversarial-query battery
  passes; a new developer could follow the README to stand up a local dev instance from scratch.
