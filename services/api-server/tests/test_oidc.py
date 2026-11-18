from __future__ import annotations

import base64
import json
import time

import pytest

from core import oidc


def _b64url(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def _token(header, payload):
    return f"{_b64url(header)}.{_b64url(payload)}.sig"


class _JwksResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._payload


def test_decode_unverified_header():
    tok = _token({"alg": "RS256", "kid": "k1"}, {"sub": "u"})
    h = oidc.decode_unverified_header(tok)
    assert h["alg"] == "RS256" and h["kid"] == "k1"


def test_select_jwk_by_kid():
    jwks = {"keys": [{"kid": "a", "kty": "RSA"}, {"kid": "b", "kty": "RSA"}]}
    assert oidc.select_jwk(jwks, "b")["kid"] == "b"
    assert oidc.select_jwk(jwks, "missing") is None
    assert oidc.select_jwk({"keys": [{"kid": "only"}]}, None)["kid"] == "only"
    assert oidc.select_jwk({"keys": []}, "a") is None


def test_fetch_jwks_retries_one_transient_network_failure_and_caches(monkeypatch):
    jwks_url = "https://identity.example/.well-known/retry-jwks.json"
    calls = []

    def _urlopen(url, *, timeout):
        calls.append((url, timeout))
        if len(calls) == 1:
            raise OSError("transient connection reset")
        return _JwksResponse({"keys": [{"kid": "recovered", "kty": "RSA"}]})

    monkeypatch.setattr(oidc.urllib.request, "urlopen", _urlopen)
    monkeypatch.setenv("REALYTICS_OIDC_JWKS_CACHE_SECONDS", "600")

    first = oidc.fetch_jwks(jwks_url)
    cached = oidc.fetch_jwks(jwks_url)

    assert first == cached
    assert first["keys"][0]["kid"] == "recovered"
    assert len(calls) == 2
    assert all(call[0] == jwks_url for call in calls)


def test_fetch_jwks_persistent_network_failure_is_typed_and_bounded(monkeypatch):
    jwks_url = "https://identity.example/.well-known/unavailable-jwks.json"
    calls = []

    def _urlopen(url, *, timeout):
        calls.append((url, timeout))
        raise OSError("network unavailable")

    monkeypatch.setattr(oidc.urllib.request, "urlopen", _urlopen)

    with pytest.raises(oidc.OidcVerifierUnavailable, match="authority is unavailable"):
        oidc.fetch_jwks(jwks_url)

    assert len(calls) == 2


def test_non_rs256_rejected():
    tok = _token({"alg": "HS256", "kid": "k1"}, {"sub": "u"})
    with pytest.raises(ValueError, match="RS256"):
        oidc.verify_oidc_token(tok, jwks={"keys": [{"kid": "k1", "kty": "RSA"}]})


def test_no_matching_kid_rejected():
    tok = _token({"alg": "RS256", "kid": "nope"}, {"sub": "u"})
    with pytest.raises(ValueError, match="No matching JWKS key"):
        oidc.verify_oidc_token(tok, jwks={"keys": [{"kid": "k1", "kty": "RSA"}]})


def test_es256_token_verifies_against_supabase_style_jwk():
    # These are required runtime dependencies. A missing verifier must fail CI.
    import jwt
    from cryptography.hazmat.primitives.asymmetric import ec
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "supabase-es256", "alg": "ES256", "use": "sig"})
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "supabase-user",
            "iss": "https://project.supabase.co/auth/v1",
            "aud": "authenticated",
            "iat": now,
            "exp": now + 300,
        },
        private_key,
        algorithm="ES256",
        headers={"kid": "supabase-es256"},
    )

    claims = oidc.verify_oidc_token(
        token,
        jwks={"keys": [public_jwk]},
        issuer="https://project.supabase.co/auth/v1",
        audience="authenticated",
    )

    assert claims["sub"] == "supabase-user"


def test_algorithm_and_jwk_type_must_match():
    token = _token({"alg": "ES256", "kid": "wrong-type"}, {"sub": "u"})
    with pytest.raises(ValueError, match="requires JWKS key type EC"):
        oidc.verify_oidc_token(
            token,
            jwks={"keys": [{"kid": "wrong-type", "alg": "ES256", "kty": "RSA"}]},
        )


def test_verify_errors_clearly_without_pyjwt():
    # PyJWT is not a hard dep; a matching key but missing lib must fail loudly.
    tok = _token({"alg": "RS256", "kid": "k1"}, {"sub": "u"})
    import builtins

    real_import = builtins.__import__

    def _no_jwt(name, *a, **k):
        if name == "jwt" or name.startswith("jwt."):
            raise ImportError("no PyJWT")
        return real_import(name, *a, **k)

    builtins.__import__ = _no_jwt
    try:
        with pytest.raises(RuntimeError, match="PyJWT"):
            oidc.verify_oidc_token(tok, jwks={"keys": [{"kid": "k1", "kty": "RSA"}]})
    finally:
        builtins.__import__ = real_import


def test_oidc_status_shape(monkeypatch):
    monkeypatch.delenv("REALYTICS_OIDC_JWKS_URL", raising=False)
    monkeypatch.delenv("REALYTICS_OIDC_ISSUER", raising=False)
    st = oidc.oidc_status()
    assert st["configured"] is False
    assert "rs256Available" in st and "jwksUrlConfigured" in st
