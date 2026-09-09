"""The MCP connector (Ticket 3).

Contract with ``app.main``: ``build_mcp_app()`` returns an ASGI application to
mount at ``/mcp``, or ``None`` when the server cannot be built (FastMCP not
installed, or required configuration missing). Everything it needs already
exists in the shared layer:

* ``app.auth.authenticate_bearer``  — resolve a Supabase OAuth access token
* ``app.services.taxonomy``         — categories and field templates
* ``app.services.items``            — add / update / remove / batch add
* ``app.services.profile``          — get_user_profile
* ``app.services.usage``            — the per-user daily call cap
* ``app.safe_query``                — validate and run a where_clause

Tools should not write their own SQL against the user-scoped tables; use the
services so the caps and RLS scoping behave identically on both surfaces.
"""

from __future__ import annotations


def build_mcp_app():  # pragma: no cover - replaced by Ticket 3
    return None
