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

from fastmcp.server.auth import AccessToken, OAuthProxy, RemoteAuthProvider
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

    Three modes, chosen by configuration:

    1. **OAuth proxy** (``mcp_oauth_proxy_enabled``): the app serves its own
       ``/authorize``, ``/token``, ``/register`` and authorization-server
       metadata at this origin, proxying them to Supabase's OAuth 2.1 server.
       This is what Claude.ai's remote connector needs — it performs Dynamic
       Client Registration and runs the whole OAuth flow against the MCP
       server's own origin, rather than following the RFC 9728
       ``authorization_servers`` pointer to Supabase. The client holds a
       FastMCP-issued reference token; on every ``/mcp`` request the proxy swaps
       it for the stored upstream Supabase token and re-validates it through the
       same ``SupabaseTokenVerifier``, so identity and RLS are unchanged.

    2. **Remote authorization server** (``SUPABASE_URL`` set, proxy not
       configured): the verifier is wrapped in ``RemoteAuthProvider`` so
       ``/.well-known/oauth-protected-resource`` merely *names* Supabase as the
       authorization server. Correct per spec and enough for a client that
       follows the pointer, but not Claude's connector.

    3. **Bare verifier** (no ``SUPABASE_URL``, i.e. local development where
       tokens are HS256-signed by ``scripts/make_test_token.py``): tokens are
       still verified identically; there is simply no authorization server to
       advertise.

    ``base_url`` is the site root because the discovery documents are
    origin-rooted; ``resource_base_url`` is the ``/mcp`` URL, the resource being
    protected. ``app.main`` re-exposes the provider's routes at the root — see
    ``build_mcp_app`` — because the provider registers them on a sub-app mounted
    at ``/mcp``, while a connector expects them at the origin root.
    """
    verifier = SupabaseTokenVerifier(base_url=settings.public_base_url)

    if not settings.supabase_url:
        logger.warning(
            "SUPABASE_URL is not set: /mcp will verify tokens but will not advertise an "
            "authorization server, so a remote connector cannot complete OAuth against it"
        )
        return verifier

    if settings.mcp_oauth_proxy_enabled:
        logger.info(
            "MCP auth: OAuth proxy fronting Supabase (%s) from %s",
            settings.token_issuer,
            settings.public_base_url,
        )
        return OAuthProxy(
            upstream_authorization_endpoint=settings.upstream_authorization_endpoint,
            upstream_token_endpoint=settings.upstream_token_endpoint,
            # A public client pre-registered with Supabase; no secret, so a
            # stable jwt_signing_key is required (OAuthProxy enforces this).
            upstream_client_id=settings.supabase_oauth_client_id,
            upstream_client_secret=None,
            token_endpoint_auth_method="none",
            jwt_signing_key=settings.mcp_oauth_jwt_signing_key,
            token_verifier=verifier,
            base_url=settings.public_base_url,
            resource_base_url=settings.mcp_url,
            # Keep the consent screen on: every DCR client shares the one
            # upstream client id, so this per-client consent is what guards
            # against a confused-deputy replay. The user also approves once on
            # Supabase's own /oauth/consent screen further along the flow.
            require_authorization_consent=True,
        )

    logger.info(
        "MCP auth: advertising Supabase (%s) as an external authorization server",
        settings.token_issuer,
    )
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
