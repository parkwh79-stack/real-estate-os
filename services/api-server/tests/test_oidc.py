from __future__ import annotations

import base64
import json

import pytest

from core import oidc


def _b64url(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def _token(header, payload):
    return f"{_b64url(header)}.{_b64url(payload)}.sig"


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


def test_non_rs256_rejected():
    tok = _token({"alg": "HS256", "kid": "k1"}, {"sub": "u"})
    with pytest.raises(ValueError, match="RS256"):
        oidc.verify_oidc_token(tok, jwks={"keys": [{"kid": "k1", "kty": "RSA"}]})


def test_no_matching_kid_rejected():
    tok = _token({"alg": "RS256", "kid": "nope"}, {"sub": "u"})
    with pytest.raises(ValueError, match="No matching JWKS key"):
        oidc.verify_oidc_token(tok, jwks={"keys": [{"kid": "k1", "kty": "RSA"}]})


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
