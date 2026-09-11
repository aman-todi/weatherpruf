# Wardrobe MCP App — v1 Build Spec

*Revision 4 — folds in the decisions made while Tickets 1–6 were actually built. Revision 3 added
the connector-vs-plugin reasoning as §1; this revision changes the daily call cap, records what the
implementation found out that the design could not have known, and marks the build status of each
ticket. Superseded sections from earlier drafts are not shown separately; this document remains the
current source of truth.*

### What changed in Revision 4, and why

Everything here came out of building the thing rather than designing it. Grouped by how much it
should change your mental model.

**Changed a stated decision**

- **Daily call cap raised from 10/day to 50/day** (§5). Revision 3 flagged 10 as tight and said to
  raise it if that proved true in use. It did: one end-to-end walkthrough spends almost the whole
  budget. Still one environment variable.
- **FastMCP v4, not v3** (§0). v4 is the current major. The mounted-ASGI-sub-app pattern the
  architecture depends on is unchanged between them.

**Found a hole the design had left open**

- **`closet_query_view` must not be granted to `authenticated`** (§3). The view runs with its
  owner's privileges, so RLS on `items` does *not* apply through it. Exposing it to PostgREST
  would have been a cross-user read — the exact thing the rest of the design is careful about.
  Only the read-only role may select it, and only ever with a bound `user_id`.
- **Identifier case folding in the query validator** (§4.1). `CATEGORY = 'jacket'` is valid SQL —
  Postgres folds unquoted identifiers to lower case — but a naive allowlist rejects it as an
  unknown column, costing the user a call for a predicate that was never wrong.
- **`fields->>'key'` orders alphabetically, not numerically** (§4.1). The field template advertises
  `number`-typed fields, but jsonb extraction returns text and casts are rejected, so
  `fields->>'inseam_inches' > '30'` is true for `'9'`. A silent wrong answer, not an error.

**Added something the design did not call for**

- **A shared service layer** between the REST and MCP surfaces (§2). Not in the original ticket
  split, and the single most useful structural decision in the build: the 200-item cap, the colour
  limit and category-field validation are enforced once, so the two surfaces cannot drift.
- **`GET /api/tags`** (§6). Tags have no registry table by design, which left the web app with no
  way to know which tags a user has. Derived from the items that carry them.
- **A unified error envelope** across both surfaces (§7, Ticket 2).
- **A hosting decision the spec never made**: the frontend runs in the same container on the same
  origin (§2). Revision 3 named React + Vite but never said where it would run, which left CORS, a
  second certificate and a second deploy pipeline as implied-but-unowned work. Putting it in one
  image deletes all three.

**Learned about the platform**

- **ECS Express Mode already serves HTTPS**, so the custom domain in Ticket 5 is polish, not the
  prerequisite it was written as (§7).
- **Supabase does not implement RFC 8707 resource indicators** (§5), so a token minted for another
  MCP server on the same project would validate here. A non-issue for a single-app project, but it
  belongs in writing.

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
- **MCP server**: FastMCP (v4 — Revision 3 said v3; v4 is the current major and the
  mounted-sub-app pattern is unchanged), mounted directly inside the FastAPI app as an ASGI
  sub-application — one process, one deploy, shared auth and data-access layer between REST and
  MCP.
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

### A note on one ambiguous piece of earlier feedback — **confirmed in Revision 4**

The instruction "I don't think we need user 'tags', name and other info can probably go into one
user/user_profile table" is read here as: **drop the separate normalized `tags` + `item_tags`
tables** — tags become a plain denormalized array directly on each item, no per-user tag registry.
The `user_profile` table remains the single place for account-level info (`home_location`,
`unit_preference`); nothing else is added to it since no other specific field was named.

This reading was confirmed during the build. Tags live on the item; items are RLS-scoped to their
owner; so tags are per-user transitively without a registry table. Two consequences fell out of it,
both handled rather than merely noted:

- There is no canonical list of a user's own tags, which the closet browser's tag filter needs.
  `GET /api/tags` derives it from the items carrying each tag (§6). Deriving rather than storing
  keeps one source of truth and means a tag stops existing the moment nothing carries it, with no
  orphan rows to clean up.
- Without a registry there is nothing to reconcile typo variants against, so tags are trimmed,
  lower-cased and de-duplicated on write. `Date-Night` and `date-night` are one tag.

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

**A shared service layer sits under both surfaces** (added in Revision 4; not in the original
ticket split). Neither the REST routes nor the MCP tools write their own SQL against the
user-scoped tables — both call the same functions in `backend/app/services/`, which own item CRUD,
category-field validation, the profile, and the usage counter. The rules that must hold identically
however a change arrives — the 200-item cap, the five-colour limit, field validation, RLS scoping —
are therefore enforced in exactly one place. Ticket 3 anticipated the need ("reusing Ticket 2's
data-access functions where possible... coordinate on shared service functions"); building the
layer up front in Ticket 1 turned that coordination problem into a non-issue and is what let
Tickets 2 and 3 genuinely run in parallel.

**The frontend ships in the same container** (decided in Revision 4; the spec had not said where
it would be hosted). FastAPI serves the built Vite bundle at `/` with a fallback to `index.html`
for client-side routes, alongside `/api` and `/mcp`. One image, one origin, one deploy.

This is not merely convenient — it removes whole categories of configuration rather than solving
them: no CORS, no second certificate or domain, no second deploy pipeline, and `PUBLIC_BASE_URL` is
simply where everything is. The alternative (S3 + CloudFront, or a Vercel-class host) buys CDN edge
caching and independent frontend deploys, neither of which is worth that cost at single-digit
concurrent users. The seam is cheap to reverse if it ever is: the API client already honours
`VITE_API_BASE_URL` for a split-origin deployment, and `CORS_ALLOW_ORIGINS` still exists for
exactly that case.

One consequence worth knowing: Vite inlines `VITE_*` values at build time, so the Supabase project
is baked into the image by `docker build --build-arg` rather than supplied to the running task.
Both values are publishable — the anon key is safe in a bundle because RLS is what protects the
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

Constraints as built (Revision 4): the colour cap is a check constraint as specified, and
`warmth_rating` (1–5), `formality` (the four values above) and `fields` (must be a JSON object) are
checked in the database as well as in the application. `updated_at` is maintained by a trigger
rather than by application code, so it cannot be forgotten on a write path added later.
`category_field_defs` additionally carries `unique (category_id, field_name)` and a check that
`allowed_values` is populated for `enum` fields and empty for every other type — which is what lets
the seed migration be re-run to extend the taxonomy without duplicating rows.

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

One deliberate asymmetry (Revision 4): `usage_counters` is *readable* by its owner but has no
insert or update policy at all, so a user cannot reset their own daily quota. The counter is
written only by the server, as the application role, with the statement scoped by `user_id`.
`categories` and `category_field_defs` are readable by any signed-in user and writable by nobody
but the service role.

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

**The view is not granted to `authenticated`, and this matters more than it looks** (Revision 4).
The view is not `security_invoker`, so it executes with its owner's privileges and RLS on `items`
does **not** apply through it. Exposing it to PostgREST — which is what granting it to
`authenticated` would do — would have handed every signed-in user every other user's closet. Only
`wardrobe_readonly` may select it, and every statement that does binds `user_id` as a parameter.
That bound parameter, not RLS, is what keeps users apart on the query path; the design should be
read with that in mind wherever it says "RLS protects both surfaces", because on this one path it
does not.

Better still, **the project's Data API (PostgREST) can be switched off entirely**
(Revision 4), because nothing in this app uses it: the frontend uses Supabase only for auth, and
the backend reaches the database directly over asyncpg. That removes the exposure route above
rather than defending against it. The `revoke` stays regardless, so the guarantee survives the
Data API being turned back on.

Two further hardening decisions on the role, neither of which the design called for:

- It is created **without a password**, so no credential is committed. An operator sets one after
  applying the migration (`alter role wardrobe_readonly with login password '...'`), and its
  connection string is kept as a secret separate from the application role's.
- It carries `default_transaction_read_only`, a `statement_timeout` and an
  `idle_in_transaction_session_timeout` as *role-level* settings, so the guarantees hold even for a
  connection the application forgot to configure. The application re-asserts the first two per
  transaction as well; belt and braces, on the component where that is warranted.

Verifying this is not optional and not an assumption: `db/tests/verify_readonly_role.sql` asserts
eight statements are each refused (insert, update and delete through both the view and `items`
directly; select on `items`, `user_profile` and `auth.users`; and `create table`), and runs against
a real Supabase project as readily as against a local one.

---

## 4. MCP tool contract

**Nine** tools, mounted via FastMCP at `/mcp`, authenticated via Supabase OAuth 2.1 (bearer token
validated against Supabase's JWKS endpoint). Revision 3 said "eight" while listing nine in the
table below; nine is correct and nine were built.

On token validation as implemented: the JWKS endpoint is
`{SUPABASE_URL}/auth/v1/.well-known/jwks.json` and the expected issuer is
`{SUPABASE_URL}/auth/v1`, with RS256 and ES256 both accepted — derived the same way FastMCP's own
`SupabaseProvider` derives them. The legacy HS256 project secret is accepted as a fallback for
projects that have not moved to asymmetric signing, and is what the local test-token helper signs
with, so the auth path under test is the real one rather than a test-only bypass.

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

Two things the built `query_instructions` says that Revision 3 did not anticipate, because both
only surfaced once real predicates were run through the validator:

- **`fields->>'key'` returns text, so `<`, `<=`, `>`, `>=` on it compare alphabetically.**
  `fields->>'inseam_inches' > '30'` is true for `'9'`. Casts are rejected (§4.1), so there is no
  `::numeric` escape hatch, and the instructions therefore tell the assistant to use `=` or `IN` on
  number-typed fields and rank the returned items itself. `warmth_rating` is a real integer column
  and is the one numeric field that orders correctly. This is worth knowing when extending the
  field templates: a `number`-typed category field is filterable but not rangeable.
- **`ANY(...)` is supported, `ALL(ARRAY[...])` is not.** sqlglot parses `ALL` in that position as
  an anonymous function, which the node allowlist refuses. §4.1 below lists both; the instructions
  advertise only `ANY`, so the tool is self-consistent, and `ALL` buys nothing here that `ANY` and
  the array containment operators do not already cover. Accepted rather than worked around.

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

**Refinements from the build (Revision 4).** The six steps above survived contact intact; these are
additions, not corrections.

- **The allowlist is over AST *node types*, not a denylist of bad words.** Anything the parser
  produces that is not explicitly permitted is rejected, so a construct nobody predicted — a cast
  trick, an unknown function, a window expression — fails closed rather than needing to have been
  thought of in advance.
- **Unquoted identifiers are folded to lower case before the column check**, exactly as Postgres
  resolves them. Without this, `CATEGORY = 'jacket'` — valid SQL, and a plausible thing for a model
  writing SQL-shaped text to emit — is rejected as an unknown column, spending one of the user's
  daily calls on a predicate that was never wrong. Quoted identifiers keep their case, also as
  Postgres does, so `"User_Id"` is still not a name any allowlist matches.
- **The textual pre-scan runs with string literals blanked out.** Otherwise
  `notes ILIKE '%goes with jeans%'` is rejected for containing "with" — and `notes` is precisely
  where the differentiating detail the assistant needs lives. This costs nothing in safety: the
  scan's only output is a yes/no, it never feeds the SQL that gets built, and every structural
  construct those keywords stand for is independently refused by the node allowlist.
- **Node-count and nesting-depth caps**, checked textually before the recursive parser runs and
  again on the parsed tree. A deeply nested predicate would otherwise exhaust the Python stack
  inside a request, which is a crash rather than a rejection.
- **`sqlglot.parse` is re-run to confirm the input was a single expression**, because
  `parse_one` silently keeps only the first statement rather than complaining.
- The re-serialised SQL is checked for `;`, `--` and `/*` before the statement is built — a
  guard on the generator itself, not on the input.

**How this was verified.** Beyond the branch's own tests, an independently written battery of 35
attacks was run against it: second statements, subqueries, `DROP TABLE`, comment truncation,
`UNION`, casts, `pg_sleep`, `pg_read_file`, `current_setting`, `version()`, the jsonb exists
operator, a 300-deep parenthesis bomb, a 500-term `OR` chain, a table-qualified column, a bind
placeholder, a quoted `"user_id"`, and a direct `user_id` comparison. All 35 were rejected; 14
legitimate predicates were accepted; and a predicate deliberately written to match every row
returned nothing belonging to a second user. Re-running that battery after any change to the
validator is the cheapest way to keep this component honest.

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
  short ones), whereas a call counter is simple, robust, and easy to reason about. **Cap value: 50
  calls per user per UTC day** (raised from 10 in Revision 4 — see below), tracked in
  `usage_counters`, incremented atomically per call, checked before executing any tool body.
  Configurable via `DAILY_MCP_CALL_LIMIT`. Exceeding it returns a clear structured error the
  assistant can relay ("today's usage limit is reached, try again tomorrow"), including when the
  budget resets.

  Revision 3 set this at 10/day, flagged it as tight, and said to raise it if that proved true in
  practice. It did — a `get_closet_structure` call plus a couple of `query_closet_items` calls
  across a few recommendation questions was most of a day's budget, and a single end-to-end test
  walkthrough spent nearly all of it. **50/day is the default from Revision 4.**

  The reasons `batch_add_items`, `list_category_items` and `get_closet_summary` exist have not
  changed: one batch call instead of N `add_item` calls, one summary call instead of several
  targeted queries. Call efficiency is worth having at any cap, and the tool descriptions still
  steer the assistant that way without naming a number — every tool response carries
  `calls_remaining_today`, so the model reads the live figure rather than a value baked into its
  instructions. Nothing needs rewriting if the cap changes again.

  The counter is a single conditional upsert, so two concurrent tool calls cannot both slip past
  the cap, and a call that is refused does not inflate the count. Because `usage_counters` has no
  insert or update policy for `authenticated` (§3), a user cannot reset their own quota.

- **Cross-server token replay** (Revision 4): Supabase does not implement RFC 8707 resource
  indicators, so an access token minted for a *different* MCP server backed by the *same* Supabase
  project would also validate here. For a project hosting only this app there is no second server
  to replay from, so this is a non-issue — but it stops being one the moment an unrelated MCP
  server is added to the same project. Audience checking is available
  (`EXPECTED_TOKEN_AUDIENCE`) but off by default: every token from a given project carries the same
  audience, so the claim cannot distinguish this case anyway, and enforcing a guessed value fails
  closed on the connector path, which is the hardest one to test before it is live.
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

### REST API as built (Revision 4)

All routes under `/api`, all requiring a Supabase access token, all resolving to the same `user_id`
as the MCP path.

| Method | Path | |
|---|---|---|
| `GET` | `/api/categories` | Every category with its field template — drives the dynamic form. |
| `GET` | `/api/items` | `category`, repeatable `tags` (AND semantics), `limit`, `offset`. Returns `{items, total}`, where `total` is the whole closet, not the filtered count, so the UI can show how much of the 200-item cap is spent while a filter is applied. |
| `GET` | `/api/tags` | **Added in Revision 4.** The caller's distinct tags with usage counts, derived from the items carrying them. Backs the closet browser's tag filter, which otherwise had no source given there is no tag registry table. Deriving client-side from a page of `/api/items` would give an incomplete list. |
| `POST` | `/api/items` | |
| `GET` `PATCH` `DELETE` | `/api/items/{id}` | |
| `GET` `PUT` | `/api/profile` | Never 404s — a user with no row gets the defaults. |
| `GET` | `/api/me` | Account summary: item count and limit, calls used today and the daily limit, and the connector URL the "Connect your assistant" page displays. |
| `GET` | `/api/limits` | The caps applying to this account. |
| `DELETE` | `/api/account` | |

**One error envelope** (Revision 4). Domain errors and request-validation failures both return
`{"error": "<stable code>", "message": "<human readable>", "details": {…}}`, so a client has one
shape to understand rather than two. FastAPI's default `{"detail": [...]}` for body validation is
overridden to match. Stable codes: `closet_full`, `validation_failed`, `unknown_category`,
`not_found`, `daily_limit_reached`, `unsafe_query`. `message` is written to be shown to a person —
which is also what makes it usable verbatim by the assistant on the MCP side.

---

## 7. Tickets for Claude Code

Six tickets. **Ticket 1 first.** **Tickets 2 and 3 concurrent after that.** **Ticket 4 depends on
Ticket 2.** **Ticket 5 can scaffold anytime, finishes after 1–3 are stable.** **Ticket 6 last.**

### Build status (Revision 4)

| Ticket | State |
|---|---|
| 1 — Foundations | **Done.** Verified against a real local Postgres. |
| 2 — REST API | **Done.** Plus `/api/tags`, `/api/me`, `/api/limits` and the unified error envelope (§6). |
| 3 — MCP server | **Done** except the manual Claude.ai connector OAuth test, which needs a deployed HTTPS instance. |
| 4 — Frontend | **Done** except clicking through the magic-link flow, which needs a real Supabase project. Served from the backend container on one origin (§2). |
| 5 — Deployment | **Scaffolded.** Dockerfile, CI and deploy workflow written; never built or deployed — see below. |
| 6 — Verification | **Partial.** Seed data and the adversarial battery done; the Claude.ai end-to-end walkthrough needs a deployed instance. |

**How the sequencing actually went, in case it is repeated for v2.** Building the shared service
layer (§2) as part of Ticket 1 rather than leaving Tickets 2 and 3 to "coordinate on shared service
functions" is what made the concurrency work: Tickets 3 and 4 then ran in genuinely parallel
sessions against a frozen contract and merged with no conflicts. The ticket boundaries in this
section are otherwise sound; the one thing worth moving earlier is the shared layer.

**What could not be verified, and why.** Three things were built but not exercised end to end,
because the environment they were built in blocks the necessary network egress: the Docker image
was never built (Docker Hub's blob CDN is blocked), the AWS deployment specifics follow the
official `aws-actions/amazon-ecs-deploy-express-service` action's documented inputs rather than a
reading of the AWS docs (`docs.aws.amazon.com` is blocked), and the Supabase dashboard steps come
from search results rather than the live docs (`supabase.com` is blocked). Each is flagged in place
in the runbook it belongs to rather than only here. What *was* proven about the image is its
install step, reproduced outside Docker in a clean virtualenv.

**Runbooks for the manual plumbing**, added in Revision 4 because none of it can be done by a
pipeline: `docs/setup-supabase.md` (project, migrations, read-only role password, asymmetric JWT
keys, OAuth 2.1 server with DCR, magic-link redirect URLs) and `infra/README.md` (ECR, three IAM
roles with least-privilege policy documents, Secrets Manager, repository configuration).

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
- Implement the per-user daily call-count cap (§5, 50/day) as a check-and-increment against
  `usage_counters` before each tool body runs, and the 200-item cap inside `add_item` and
  `batch_add_items`.
- Write a test client script (using the official MCP Python SDK's `Client`) exercising all nine
  tools against a locally running server, including: a `batch_add_items` call mixing valid and
  invalid items to confirm partial success works as designed, and a battery of adversarial
  `where_clause` inputs for `query_closet_items` — a semicolon-separated second statement, an
  attempted subquery, a `DROP TABLE`, a column not in the allowlist, an attempt to reference
  another user's `user_id` directly, and a disallowed function call — all must be rejected with
  clear errors, none may reach the database as written.
- **Definition of done**: the test script's happy-path calls succeed with correctly-shaped,
  RLS-scoped results across all nine tools; the `batch_add_items` partial-success case behaves as
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

- Single Dockerfile for the FastAPI app (serving both REST and the mounted MCP sub-app),
  **and the built frontend alongside them** — see §2. Revision 3 left the frontend's hosting
  unstated; Revision 4 puts it in the same image on the same origin.
- ECS Express Mode service definition, environment/secrets wiring for Supabase keys (including the
  read-only role's connection string, kept separate from the main app's DB credentials), health
  checks.
- ~~Custom domain + TLS (required for Claude.ai's remote connector, which needs HTTPS).~~
  **Corrected in Revision 4: Express Mode's generated URL is already HTTPS with a managed
  certificate, so the connector's requirement is met without a custom domain.** A custom domain is
  polish, and shipping v1 on the generated URL is entirely reasonable. If you do want one: an ACM
  certificate in the same region, attached to the service's load balancer, with a Route 53 alias
  pointing at it — then update `PUBLIC_BASE_URL` so the Connect page hands out the new URL.
- **Definition of done**: the deployed URL serves both REST and `/mcp`; a real Claude.ai remote
  connector completes OAuth against the deployed instance (not just localhost) and successfully
  calls a tool.

**As built (Revision 4).** Express Mode provisions the service, load balancer, TLS, auto-scaling
and URL itself, so there is no task definition or service definition to maintain — the deploy
passes configuration to `aws-actions/amazon-ecs-deploy-express-service` and ECS creates the rest.
Secrets come from one Secrets Manager entry read as individual JSON keys, so no value reaches the
task definition or a workflow log. CI applies the migrations to a throwaway Postgres and runs lint
plus the full test suite before anything is built or deployed; Supabase migrations are deliberately
*not* in the pipeline, since they are applied by hand ahead of the deploy that needs them. Note
that ECS injects secrets at task start, so a rotated secret does not reach running tasks without a
forced deployment.

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
  "floral" or "multi-color"). Keep an eye on total call count spent across this whole test — at
  50/day (Revision 4) a full walkthrough fits comfortably, which it did not at 10.
- Re-run the adversarial query battery from Ticket 3 once more against the deployed instance, not
  just locally.
- Confirm the daily call cap and 200-item cap behave sensibly when hit through a real conversation
  (clear, non-confusing message back to the user).
- README: local dev setup, how to connect an assistant, environment variables, deployment steps.
- **Definition of done**: the full "sign up → connect assistant → add item via conversation → get a
  recommendation" flow works without manual intervention; the deployed adversarial-query battery
  passes; a new developer could follow the README to stand up a local dev instance from scratch.
