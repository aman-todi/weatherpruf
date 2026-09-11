# Supabase setup

Everything in this file is manual, one-time, and needs your credentials — so it
is yours to run, not the deploy pipeline's. Work top to bottom; each step says
what it produces and which environment variable it ends up in.

> **How this was written.** `supabase.com` is blocked from the development
> environment this repository was built in, so the dashboard navigation below
> comes from search results rather than a reading of the live docs, and menu
> labels may have moved — step 4's had, and has since been corrected against the
> live dashboard; treat the rest with the same suspicion. The parts that are
> *verified* are the ones the code depends on: the JWKS URL, the token issuer,
> and the signing algorithms. Those were checked against FastMCP's own
> `SupabaseProvider`, which derives them the same way this app does
> (`{project_url}/auth/v1/.well-known/jwks.json` and issuer
> `{project_url}/auth/v1`, RS256 or ES256).

## 1. Create the project

Create a Supabase project in a region close to where the ECS service will run —
every request makes at least one database round trip, so cross-region latency is
paid on every call.

### The security toggles on the creation screen

The new-project screen offers **Enable Data API**, **Automatically expose new
tables**, and **Enable automatic RLS**.

**Turn the Data API off.** This app never uses it. The Data API is PostgREST at
`/rest/v1/`; every Supabase HTTP call in this codebase goes to `/auth/v1/`
instead (JWKS for token verification, and the admin endpoint that deletes a user
during account deletion), the frontend uses only `supabase.auth.*` for the magic
link, and the database is reached directly over asyncpg with `DATABASE_URL`. The
web app talks to this project's own FastAPI at `/api`, not to Supabase. With the
Data API off, the other two toggles are moot — they only govern what PostgREST
exposes — and Supabase generally greys them out.

This is worth more than tidiness. §3 of the spec explains why
`closet_query_view` must never be granted to `authenticated`: the view runs with
its owner's privileges, so RLS on `items` does not apply through it, and
exposing it would be a cross-user read. **That risk exists only because
PostgREST would expose the view.** With the Data API off it is structurally
absent rather than merely defended against. The `revoke` in
`0003_rls.sql` stays regardless — it is what keeps the guarantee if the Data API
is ever switched back on.

Enabling all three is also a safe choice, if you would rather not turn things
off on a screen you are seeing for the first time. The migrations set every
grant explicitly and enable RLS on all five tables themselves — and `force row
level security` on the three that hold user data, which applies even to the
table owner — and the revoke above is already in `0003`. `closet_query_view` is
not a sixth: it is a view, it cannot carry RLS at all, and the revoke is
precisely why that does not matter. If you go that way, **turn "Enable
automatic RLS" on** as a safety net for any table added later that forgets to.

Either way it is reversible under **Project Settings → API**.

One caveat worth knowing before you decide: Supabase Studio's **Table Editor**
may depend on the Data API to browse rows, and could show an error or an empty
grid with it off. The **SQL Editor is unaffected**, and that is what every step
in this runbook uses. If you want the Table Editor for poking around, turn the
Data API back on and rely on the revoke.

From **Project Settings → API**, note:

| Value | Goes to | Sensitivity |
|---|---|---|
| Project URL (`https://<ref>.supabase.co`) | `SUPABASE_URL` | Public. |
| `service_role` key | `SUPABASE_SERVICE_ROLE_KEY` | **Secret.** Bypasses RLS entirely. Server-side only — never in the frontend, never in a repository variable. |
| `anon` key | frontend `VITE_SUPABASE_ANON_KEY` | Public by design; RLS is what protects the data. |

## 2. Apply the migrations

Run the files in `db/migrations/` **in filename order**, through the SQL editor
or the CLI:

```bash
for f in db/migrations/*.sql; do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
```

**Skip `db/local/00_auth_shim.sql`.** It fakes `auth.users` and `auth.uid()` for
a bare local Postgres; Supabase already provides both, and the shim would
collide with them.

Sanity check afterwards:

```sql
select count(*) from public.categories;          -- 26
select count(*) from public.category_field_defs; -- 75
select count(*) from pg_policies where schemaname = 'public';
```

## 3. Give the read-only role a password

`0004_readonly_role.sql` creates `wardrobe_readonly` **without** a password, so
no secret is ever committed. Generate one and set it:

```sql
alter role wardrobe_readonly with login password '<generated>';
```

Then build `READONLY_DATABASE_URL` from your normal connection string with the
username and password swapped for this role.

This must be a **different credential** from `DATABASE_URL`. The read-only role
is the actual security boundary behind `query_closet_items` (spec §4.1) — the
sqlglot AST validation in front of it is defence in depth, not the boundary. If
the query path gets the application role's credentials, the only real guarantee
is gone and nothing will visibly break to tell you.

Verify it directly rather than assuming, connected **as that role**:

```sql
select count(*) from public.closet_query_view;   -- works
select count(*) from public.items;               -- must fail: permission denied
update public.items set brand = 'x';             -- must fail
```

`db/tests/verify_readonly_role.sql` runs that whole battery — eight statements
that must each be refused — and is worth running against the real project once:

```bash
psql "$READONLY_DATABASE_URL" -f db/tests/verify_readonly_role.sql
```

> **Connection pooling.** If you route this role through Supabase's pooler
> rather than a direct connection, check that the role-level settings from the
> migration (`default_transaction_read_only`, `statement_timeout`) still apply —
> pooled connections do not always carry role-level `SET`s. The application
> re-asserts both per transaction (`app/db.py`), so they are belt-and-braces
> rather than load-bearing, but it is worth knowing which one is protecting you.

## 4. Switch JWT signing to asymmetric keys

**Check this before changing anything — it is usually already done.** Supabase
has created new projects with asymmetric signing keys by default since 1 October
2025, so a recently created project needs no action here.

The reliable check costs one request and does not depend on a dashboard label:

```bash
curl -s https://<ref>.supabase.co/auth/v1/.well-known/jwks.json | jq
```

A populated `keys` array means asymmetric signing is on and this step is
finished. `{"keys":[]}` or a 404 means the project is still on the legacy HS256
secret. That endpoint is the same URL `app/config.py` derives from
`SUPABASE_URL`, so this is a direct check on what the backend actually uses —
better evidence than any menu.

If you do need to switch, the keys live under **Project Settings → JWT Keys**
(`/project/<ref>/settings/jwt`) — *not* under Authentication, where earlier
revisions of this file sent you. Click **Migrate JWT secret**: it imports the
legacy secret into the new system and creates an asymmetric key alongside it,
which you then rotate to. Each step is reversible and zero-downtime.

**Pick RS256 or ES256** (ECC P-256 is ES256). Supabase also offers Ed25519, and
`_ASYMMETRIC_ALGORITHMS` in `app/auth.py` is a hardcoded allowlist of those
two — an Ed25519 key publishes fine in JWKS and then fails verification at
runtime with a confusing algorithm error. RS256 is Supabase's default, so the default is
safe.

This matters because the backend verifies tokens against your project's JWKS
endpoint, which only exists for asymmetric keys. The app also accepts the legacy
HS256 project secret via `SUPABASE_JWT_SECRET` as a fallback, but that means
sharing a symmetric secret with every service that verifies a token — avoid it
in production. Locally it is exactly what `scripts/make_test_token.py` signs
with, which is why the fallback exists at all.

No code change is needed for either algorithm: `app/auth.py` reads the header
and picks the path.

## 5. Enable the OAuth 2.1 server

Under **Authentication → OAuth Server**:

1. Enable the OAuth 2.1 authorization server.
2. Enable **Dynamic Client Registration** (labelled "Allow Dynamic OAuth
   Apps"), so Claude.ai can register itself without you pre-creating a client.
3. Leave **Authorization Path** at `/oauth/consent`. It must match
   `CONSENT_PATH` in `frontend/src/lib/oauth.ts`; change one and you must change
   the other.
4. Check the **Site URL** shown on this screen. It is the shared value from
   Authentication → URL Configuration and defaults to `http://localhost:3000`,
   which is wrong for this app on both counts — the dev server is Vite on
   **5173**, and in production it needs to be the deployed frontend origin. Fix
   it in step 6; the preview here should then read
   `https://<your-domain>/oauth/consent`.

### Supabase does not render the consent screen — the app does

This is the part that is easy to miss, and it dead-ends the connector flow when
it is missed. Supabase is the authorization server, but it does not ask the user
for consent itself: it redirects to the Authorization Path with an
`authorization_id` and expects your app to approve or deny.

`frontend/src/pages/ConsentPage.tsx` is that screen. It reads the
`authorization_id`, fetches the request with
`supabase.auth.oauth.getAuthorizationDetails`, shows who is asking and what they
asked for, and calls `approveAuthorization` or `denyAuthorization`. Nothing to
configure — but two deployment requirements follow from it:

- **The host must fall back to `index.html` for unknown paths.** Supabase
  hard-navigates to `/oauth/consent`; a server with no SPA rewrite returns 404
  and the flow ends there. **This is already handled**: the frontend is served
  by the same container as the API (see `infra/README.md`), and
  `backend/app/static.py` falls back to `index.html` for any path that is not a
  real file and not an application route. Verified —
  `/oauth/consent?authorization_id=…` returns the app, while `/api/nope` still
  returns a JSON 404 rather than the shell. If the frontend is ever split onto
  a separate static host, that host has to be configured to do the same.
- **`/oauth/consent` must be reachable without a session surviving.** A user who
  is signed out when Claude.ai sends them there gets the login page; the magic
  link has to return them to the full URL, query string included, or the pending
  request is lost. `magicLinkRedirect()` in `frontend/src/lib/oauth.ts` is what
  keeps that intact — worth knowing before someone "simplifies" it back to
  `window.location.origin`.

DCR is the MCP client onboarding path this design depends on. The spec calls
this out as a known, accepted limitation (§0): DCR is deprecated in favour of
Client ID Metadata Documents but still functional. If Supabase later ships CIMD
support, moving to it is a configuration change, not a rewrite.

Your project then serves discovery at:

```
https://<ref>.supabase.co/.well-known/oauth-authorization-server
```

### A security note worth reading once

Supabase does not implement **RFC 8707 resource indicators**. A consequence: an
access token minted for a *different* MCP server backed by the *same* Supabase
project would also validate against this one. For a project hosting only this
app that is a non-issue — there is no second server to replay a token from — but
do not add an unrelated MCP server to this same project without revisiting it.

Relatedly, `EXPECTED_TOKEN_AUDIENCE` is unset by default so the `aud` claim is
not checked. That is deliberate, and `app/auth.py` explains the reasoning at
length: the claim cannot distinguish the case above, and enforcing a guessed
value would fail closed on the connector path, which is the hardest one to test
before it is live. Once you have seen a real connector token and know its
audience, setting the variable adds the assertion.

## 6. Configure the web app's auth

Under **Authentication → URL Configuration**:

- **Site URL**: the deployed origin (e.g. `https://wardrobe.example.com`).
- **Redirect URLs**: add both the deployed origin and `http://localhost:5173`
  so magic links work in local development.

The frontend and the API share one origin — the container serves the React
bundle at `/`, the REST API at `/api` and the connector at `/mcp` — so this is
the same host you will use for `PUBLIC_BASE_URL`, and there is no separate
frontend domain to register here.

Magic link is what the spec chose for v1 (§6) — least code, no password reset
flow to build.

## 7. Where each value ends up

**Backend** (AWS Secrets Manager for the secrets, ECS environment variables for
the rest — see [`infra/README.md`](../infra/README.md)). `CORS_ALLOW_ORIGINS` is
absent on purpose: same origin, so no CORS:

| Variable | Source | Secret? |
|---|---|---|
| `SUPABASE_URL` | Step 1 | no |
| `SUPABASE_SERVICE_ROLE_KEY` | Step 1 | **yes** |
| `DATABASE_URL` | Project Settings → Database | **yes** |
| `READONLY_DATABASE_URL` | Step 3 | **yes**, and separate from the above |
| `SUPABASE_JWT_SECRET` | only if you stayed on HS256 | **yes** |
| `EXPECTED_TOKEN_AUDIENCE` | optional, step 5 | no |

**Frontend** (`frontend/.env`, baked into the build — so nothing secret):

| Variable | Source |
|---|---|
| `VITE_SUPABASE_URL` | Step 1 |
| `VITE_SUPABASE_ANON_KEY` | Step 1 |

`VITE_API_BASE_URL` is deliberately **not** in that list. The API is on the same
origin as the app, so the client uses relative URLs; the Vite dev server proxies
`/api` to the backend to give development the same shape. Set it only if the
frontend is ever split onto its own domain.

Note that these are **build-time** values: Vite inlines them into the bundle, so
they are supplied to `docker build` (as `--build-arg`) rather than to the running
task. Both are publishable — the anon key is safe in a bundle precisely because
RLS is what protects the data — so they belong in repository *variables*, not
secrets. The `service_role` key must never reach this build.

## 8. Connect Claude.ai

Once the backend is deployed over HTTPS (see [`infra/README.md`](../infra/README.md)):

1. In Claude.ai, add a custom connector pointing at `https://<your-domain>/mcp`.
2. Complete the OAuth flow — Claude registers itself via DCR, you sign in with
   Supabase and approve.
3. Confirm it works by asking Claude something that needs the closet, e.g.
   "what's in my closet?", which should call `get_closet_summary`.

The web app's "Connect your assistant" page shows this URL, taken from
`PUBLIC_BASE_URL` with `/mcp` appended — so set that variable to the URL people
will actually paste, not the ECS-generated one, if you have a custom domain.

**Budget your first test.** The daily cap is 50 assistant calls per user
(`DAILY_MCP_CALL_LIMIT`), which leaves room for a full end-to-end walkthrough
without the limit getting in the way. The spec originally proposed 10, which a
single walkthrough would have spent almost entirely.

The cap still exists, so if you are testing hard and hit it, raise the variable
and redeploy rather than being confused by a limit error mid-conversation. Every
tool response carries `calls_remaining_today`, so the assistant can tell you
where it stands before it runs out.

## Rotating credentials later

- **Read-only role password**: `alter role wardrobe_readonly with password ...`,
  update the Secrets Manager value, then force a new ECS deployment. ECS injects
  secrets at task start, so a rotated secret does not reach running tasks.
- **`service_role` key**: rotating it from the dashboard invalidates the old key
  immediately, so update the secret and redeploy in the same window.
- **JWT signing keys**: Supabase rotates with an overlap period so tokens signed
  by the previous key keep validating. The backend caches JWKS for ten minutes,
  so allow that long before expecting the new key to be picked up.
