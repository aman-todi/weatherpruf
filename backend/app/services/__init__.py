"""Shared data-access layer.

Both surfaces — the REST API (Ticket 2) and the MCP tools (Ticket 3) — go
through these functions rather than writing their own SQL, so the 200-item cap,
the colour limit, category-field validation and RLS scoping behave identically
however a change arrives.
"""
