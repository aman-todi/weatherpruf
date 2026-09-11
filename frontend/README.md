# weatherpruf — web app

React + Vite + TypeScript. The web app is the *secondary* interface: it exists to onboard a user,
let them browse and correct their closet, and hand them the MCP URL. Day-to-day use happens in the
assistant. There is deliberately no chat UI here.

## Running it

```bash
cd frontend
npm install
cp .env.example .env.local   # then fill in the three values
npm run dev                  # http://localhost:5173
```

`VITE_API_BASE_URL` must point at a running backend (`uvicorn app.main:app` on
`http://localhost:8000` by default), and the backend's `CORS_ALLOW_ORIGINS` must include
`http://localhost:5173`.

```bash
npm run build        # tsc -b && vite build
npm run lint
npm run typecheck
```

## Shape

```
src/
  lib/
    types.ts      the REST contract, transcribed from backend/app/models.py
    api.ts        one fetch wrapper; attaches the Supabase access token, unwraps
                  the {error, message, details} envelope into a typed ApiError
    supabase.ts   the auth client, plus a "is this configured?" flag so a missing
                  .env produces a setup screen instead of a blank page
    format.ts     enum ids -> human labels, colour name -> swatch
    oauth.ts      the consent path, and the magic-link redirect that preserves
                  a pending authorization_id across sign-in
  auth/           session context; magic-link sign-in
  components/     Layout, Modal, ItemCard, ItemForm and its two custom inputs
  pages/          Login, Closet, Settings, Connect, Consent
```

### Two things worth knowing

**`total` is the whole closet, not the filtered count.** `GET /api/items` returns the user's total
item count regardless of the `category`/`tags` filters, so it drives the "N of 200 items" meter and
nothing else. Pagination decides whether to offer *Load more* from whether the last page came back
full.

**Categories drive the form, not a hardcoded list.** Every category-specific input is rendered from
`GET /api/categories`: `text` → text input, `enum` → select over `allowed_values`, `number` →
number input, `boolean` → checkbox. `required: true` is enforced before submit. Adding a field to
`category_field_defs` in the database makes it appear here with no frontend change.
