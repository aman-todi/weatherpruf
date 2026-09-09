# Supabase setup

Everything in this file is manual, one-time, and needs your credentials — so it
is yours to run, not the deploy pipeline's. Work top to bottom; each step says
what it produces and which environment variable it ends up in.

> **How this was written.** `supabase.com` is blocked from the development
> environment this repository was built in, so the dashboard navigation below
> comes from search results rather than a reading of the live docs, and menu
> labels may have moved. The parts that are *verified* are the ones the code
> depends on: the JWKS URL, the token issuer, and the signing algorithms. Those
> were checked against FastMCP's own `SupabaseProvider`, which derives them the
> same way this app does (`{project_url}/auth/v1/.well-known/jwks.json` and
> issuer `{project_url}/auth/v1`, RS256 or ES256).

## 1. Create the project

Create a Supabase project in a region close to where the ECS service will run —
every request makes at least one database round trip, so cross-region latency is
paid on every call.

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
  psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f "$f"
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

Under **Authentication → JWT Keys** (labelled "JWT Signing Keys" in some
versions), move the project to **ES256** or **RS256**.

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
2. Enable **Dynamic Client Registration**, so Claude.ai can register itself
   without you pre-creating a client.

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

- **Site URL**: the deployed frontend origin (e.g. `https://wardrobe.example.com`).
- **Redirect URLs**: add both the deployed origin and `http://localhost:5173`
  so magic links work in local development.

Magic link is what the spec chose for v1 (§6) — least code, no password reset
flow to build.

## 7. Where each value ends up

**Backend** (AWS Secrets Manager for the secrets, ECS environment variables for
the rest — see [`infra/README.md`](../infra/README.md)):

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
| `VITE_API_BASE_URL` | the deployed backend URL |

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

**Budget your first test.** The daily cap is 10 assistant calls per user
(`DAILY_MCP_CALL_LIMIT`), and a single end-to-end walkthrough spends most of it.
Raise it temporarily while testing rather than being confused by a limit error
halfway through.

## Rotating credentials later

- **Read-only role password**: `alter role wardrobe_readonly with password ...`,
  update the Secrets Manager value, then force a new ECS deployment. ECS injects
  secrets at task start, so a rotated secret does not reach running tasks.
- **`service_role` key**: rotating it from the dashboard invalidates the old key
  immediately, so update the secret and redeploy in the same window.
- **JWT signing keys**: Supabase rotates with an overlap period so tokens signed
  by the previous key keep validating. The backend caches JWKS for ten minutes,
  so allow that long before expecting the new key to be picked up.
