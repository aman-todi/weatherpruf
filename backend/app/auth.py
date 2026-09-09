"""Token validation shared by the REST API and the MCP server.

Both entry points authenticate the same way and resolve to the same
``user_id``: the web app sends a Supabase *session* JWT, Claude.ai sends a
Supabase *OAuth 2.1 access token*, and both are Supabase-issued JWTs whose
``sub`` claim is the user id the RLS policies key off. That is the whole point
of the auth decision in spec §0 — one set of policies protects both paths.

Signature verification prefers the project's JWKS endpoint (asymmetric keys,
the current Supabase default). The legacy HS256 project secret is accepted as a
fallback so projects that have not migrated still work, and so local
development can mint a test token without a network round trip.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Annotated, Any
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Supabase stamps `authenticated` as the audience on session tokens. OAuth
# access tokens issued to a connector may carry the client id instead, so the
# audience is not treated as a security control — the issuer, signature and
# expiry are.
_ASYMMETRIC_ALGORITHMS = ["RS256", "ES256"]


class AuthError(Exception):
    """Raised when a token cannot be trusted. Callers map this to their own
    transport's error shape (HTTP 401 for REST, a tool error for MCP)."""


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: UUID
    email: str | None
    role: str
    token: str
    claims: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def is_anonymous(self) -> bool:
        return bool(self.claims.get("is_anonymous"))


class TokenVerifier:
    """Verifies Supabase JWTs, caching the project's signing keys."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._jwk_client: PyJWKClient | None = None
        self._jwks_unavailable_until = 0.0

    def _client(self) -> PyJWKClient | None:
        if not self._settings.supabase_url:
            return None
        if self._jwk_client is None:
            self._jwk_client = PyJWKClient(
                self._settings.jwks_url,
                cache_keys=True,
                lifespan=600,
            )
        return self._jwk_client

    def _decode(self, token: str) -> dict[str, Any]:
        options = {"require": ["exp", "sub"], "verify_aud": False}
        issuer = self._settings.token_issuer if self._settings.supabase_url else None

        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")

        if algorithm in _ASYMMETRIC_ALGORITHMS:
            client = self._client()
            if client is None:
                raise AuthError("token is asymmetrically signed but SUPABASE_URL is not set")
            if time.monotonic() < self._jwks_unavailable_until:
                raise AuthError("signing keys are temporarily unavailable")
            try:
                signing_key = client.get_signing_key_from_jwt(token).key
            except (jwt.PyJWKClientError, httpx.HTTPError) as exc:
                # Back off briefly so a JWKS outage does not turn into a
                # request-rate hammer on Supabase.
                self._jwks_unavailable_until = time.monotonic() + 15
                raise AuthError(f"could not fetch signing key: {exc}") from exc
            return jwt.decode(
                token,
                signing_key,
                algorithms=_ASYMMETRIC_ALGORITHMS,
                issuer=issuer,
                options=options,
            )

        if algorithm == "HS256":
            secret = self._settings.supabase_jwt_secret
            if not secret:
                raise AuthError("token is HS256-signed but SUPABASE_JWT_SECRET is not set")
            return jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                issuer=issuer,
                options=options,
            )

        raise AuthError(f"unsupported token algorithm: {algorithm!r}")

    def verify(self, token: str) -> AuthenticatedUser:
        if not token or not token.strip():
            raise AuthError("missing bearer token")
        try:
            claims = self._decode(token)
        except AuthError:
            raise
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthError(f"invalid token: {exc}") from exc

        subject = claims.get("sub")
        try:
            user_id = UUID(str(subject))
        except (TypeError, ValueError) as exc:
            raise AuthError("token subject is not a user id") from exc

        return AuthenticatedUser(
            user_id=user_id,
            email=claims.get("email"),
            role=claims.get("role", "authenticated"),
            token=token,
            claims=claims,
        )


_verifier: TokenVerifier | None = None


def get_verifier() -> TokenVerifier:
    global _verifier
    if _verifier is None:
        _verifier = TokenVerifier()
    return _verifier


def reset_verifier() -> None:
    """Drop the cached verifier. Used by tests that change settings."""
    global _verifier
    _verifier = None


def authenticate_bearer(token: str) -> AuthenticatedUser:
    """Entry point for non-HTTP callers (the MCP server's auth hook)."""
    return get_verifier().verify(token)


_bearer_scheme = HTTPBearer(auto_error=False, description="Supabase access token")


BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


async def require_user(request: Request, credentials: BearerCredentials) -> AuthenticatedUser:
    """FastAPI dependency resolving a request to its authenticated user."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header with a bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user = get_verifier().verify(credentials.credentials)
    except AuthError as exc:
        client = request.client.host if request.client else "?"
        logger.info("rejected token from %s: %s", client, exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return user
