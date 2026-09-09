"""Ticket 1 DoD: a manually-issued test JWT validates via the auth dependency."""

from __future__ import annotations

import time
import uuid

import jwt
import pytest

from app.auth import AuthError, TokenVerifier
from app.config import Settings


def _settings(**overrides) -> Settings:
    base = {"supabase_url": "", "supabase_jwt_secret": "test-secret-at-least-32-bytes-long!!"}
    return Settings(**{**base, **overrides})


def _token(secret: str = "test-secret-at-least-32-bytes-long!!", **claim_overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": str(uuid.uuid4()),
        "email": "someone@example.test",
        "role": "authenticated",
        "aud": "authenticated",
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, secret, algorithm="HS256")


def test_valid_session_token_resolves_to_a_user_id():
    subject = str(uuid.uuid4())
    verifier = TokenVerifier(_settings())

    user = verifier.verify(_token(sub=subject, email="alice@example.test"))

    assert str(user.user_id) == subject
    assert user.email == "alice@example.test"
    assert user.role == "authenticated"


def test_oauth_access_token_with_a_client_audience_is_accepted():
    """Claude.ai's OAuth access token may carry the client id as its audience.
    The issuer, signature and expiry are the security controls, not `aud`."""
    verifier = TokenVerifier(_settings())
    user = verifier.verify(_token(aud="mcp-client-abc123"))
    assert user.user_id is not None


@pytest.mark.parametrize(
    ("description", "kwargs", "secret"),
    [
        ("expired", {"exp": int(time.time()) - 60}, "test-secret-at-least-32-bytes-long!!"),
        ("signed with the wrong secret", {}, "not-the-secret-but-also-32-bytes-ok"),
        ("subject is not a uuid", {"sub": "definitely-not-a-uuid"}, "test-secret-at-least-32-bytes-long!!"),
    ],
)
def test_bad_tokens_are_rejected(description, kwargs, secret):
    verifier = TokenVerifier(_settings())
    with pytest.raises(AuthError):
        verifier.verify(_token(secret, **kwargs))


def test_token_without_a_subject_is_rejected():
    now = int(time.time())
    token = jwt.encode({"exp": now + 60, "role": "authenticated"}, "test-secret-at-least-32-bytes-long!!", algorithm="HS256")
    with pytest.raises(AuthError):
        TokenVerifier(_settings()).verify(token)


def test_empty_token_is_rejected():
    with pytest.raises(AuthError):
        TokenVerifier(_settings()).verify("")


def test_algorithm_none_is_rejected():
    """The classic JWT downgrade: a token asking to be trusted unsigned."""
    token = jwt.encode({"sub": str(uuid.uuid4())}, key="", algorithm="none")
    with pytest.raises(AuthError):
        TokenVerifier(_settings()).verify(token)


def test_hs256_token_rejected_when_no_secret_is_configured():
    verifier = TokenVerifier(_settings(supabase_jwt_secret=""))
    with pytest.raises(AuthError):
        verifier.verify(_token())


def test_issuer_is_checked_when_supabase_url_is_set():
    settings = _settings(supabase_url="https://project.supabase.co")
    verifier = TokenVerifier(settings)

    good = _token(iss="https://project.supabase.co/auth/v1")
    assert verifier.verify(good).user_id is not None

    with pytest.raises(AuthError):
        verifier.verify(_token(iss="https://evil.example.com/auth/v1"))
