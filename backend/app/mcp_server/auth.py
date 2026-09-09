"""Wiring Supabase token validation into FastMCP's auth.

The MCP surface does not get its own notion of identity. Every request through
``/mcp`` is verified by ``app.auth.authenticate_bearer`` — the same function,
the same JWKS-first-with-HS256-fallback logic, and the same resolved
``user_id`` — that the REST path uses, which is the whole point of the auth
decision in spec §0: one set of RLS policies protects both surfaces because
both resolve a token the same way.

FastMCP's role here is a *resource server*. It verifies bearer tokens; it does
not issue them. Supabase is the authorization server Claude.ai runs the OAuth
2.1 flow against, and when ``SUPABASE_URL`` is configured this module advertises
that fact through RFC 9728 protected resource metadata, which is how a remote
connector discovers where to send the user to log in.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastmcp.server.auth import AccessToken, RemoteAuthProvider
from fastmcp.server.auth import TokenVerifier as FastMCPTokenVerifier
from fastmcp.server.dependencies import get_access_token

from app.auth import AuthError, authenticate_bearer
from app.config import Settings

logger = logging.getLogger(__name__)


class SupabaseTokenVerifier(FastMCPTokenVerifier):
    """Verify a Supabase session JWT or OAuth 2.1 access token.

    Returning ``None`` is FastMCP's "reject this request"; it turns into a 401
    with a ``WWW-Authenticate`` challenge, which is what makes a connector start
    the OAuth flow rather than fail outright.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__(base_url=base_url)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            user = authenticate_bearer(token)
        except AuthError as exc:
            # Info, not warning: an expired token on a long-lived connector
            # session is routine, and the client will refresh and retry.
            logger.info("rejected MCP bearer token: %s", exc)
            return None

        scopes = _scopes_from_claims(user.claims)
        return AccessToken(
            token=token,
            # Supabase does not put the connector's client id in an access
            # token, so identify the principal by the subject. Nothing in this
            # server authorises on client_id; RLS keys off the subject alone.
            client_id=str(user.user_id),
            subject=str(user.user_id),
            scopes=scopes,
            expires_at=user.claims.get("exp"),
            claims=dict(user.claims),
        )


def _scopes_from_claims(claims: dict) -> list[str]:
    """Supabase writes scopes as a space-delimited ``scope`` claim when it
    issues an OAuth access token, and omits it for a session JWT."""
    raw = claims.get("scope") or claims.get("scp") or ""
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return [part for part in str(raw).split() if part]


def build_auth_provider(settings: Settings):
    """The provider to hand FastMCP.

    With ``SUPABASE_URL`` set the verifier is wrapped in a
    ``RemoteAuthProvider`` so ``/.well-known/oauth-protected-resource`` names
    Supabase as the authorization server — the discovery step Claude.ai's remote
    connector setup performs before it can run the OAuth flow.

    Without it (purely local development, where tokens are HS256-signed by
    ``scripts/make_test_token.py``) there is no authorization server to point
    at, so the bare verifier is used. Tokens are still verified identically;
    only discovery is missing, and there is nothing to discover.

    The two URLs are deliberately different. ``base_url`` is the site root
    because RFC 9728 well-known URIs are origin-rooted: the discovery document
    lives at ``/.well-known/oauth-protected-resource/mcp/``, at the top of the
    host, not under the resource's own path. ``resource_base_url`` is the
    ``/mcp`` URL, which is the resource actually being protected — it is what
    gets named as ``resource`` in that document and what supplies the ``/mcp``
    segment the well-known path has appended to it.

    Getting this wrong is invisible until a connector tries to authenticate:
    the 401's ``WWW-Authenticate`` challenge points at the advertised URL, and
    if the document is not served there the client 404s and the OAuth flow
    never starts. ``app.main`` re-exposes these routes at the root for exactly
    that reason — see ``build_mcp_app``.
    """
    verifier = SupabaseTokenVerifier(base_url=settings.public_base_url)

    if not settings.supabase_url:
        logger.warning(
            "SUPABASE_URL is not set: /mcp will verify tokens but will not advertise an "
            "authorization server, so a remote connector cannot complete OAuth against it"
        )
        return verifier

    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[settings.token_issuer],
        base_url=settings.public_base_url,
        resource_base_url=settings.mcp_url,
        resource_name="Wardrobe closet",
        resource_documentation=None,
    )


def current_user_id() -> UUID:
    """The authenticated user for the tool call in flight.

    FastMCP has already verified the token by the time a tool body runs — this
    only reads the result back out of the request context. It raising is a bug,
    not an auth failure: an unauthenticated request never reaches a tool.
    """
    token = get_access_token()
    if token is None or not token.subject:
        raise RuntimeError("no authenticated user on this MCP request")
    return UUID(token.subject)
