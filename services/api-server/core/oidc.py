from __future__ import annotations

"""OIDC RS256 / JWKS token verification (production auth hardening).

Complements the existing pilot HS256 path (core/idp.py). Fetches the IdP's JWKS,
selects the signing key by `kid`, and verifies an RS256 ID/access token with
issuer/audience/expiry checks. RS256 signature verification is delegated to PyJWT
(lazy-imported — no hand-rolled crypto); JWKS fetch/selection and claim/config
logic are pure-stdlib and unit-tested. Live verification needs PyJWT installed
and a reachable JWKS endpoint.
"""

import base64
import json
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional


def oidc_configured() -> bool:
    config = oidc_config()
    return bool(config["jwksUrl"] and config["issuer"])


def oidc_config() -> Dict[str, Any]:
    return {
        "issuer": _first_env("REALYTICS_IDP_ISSUER", "REALYTICS_OIDC_ISSUER"),
        "audience": _first_env("REALYTICS_IDP_AUDIENCE", "REALYTICS_OIDC_AUDIENCE"),
        "jwksUrl": _first_env("REALYTICS_IDP_JWKS_URL", "REALYTICS_OIDC_JWKS_URL"),
    }


def _b64url_json(segment: str) -> Dict[str, Any]:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


def decode_unverified_header(token: str) -> Dict[str, Any]:
    """Read the JWT header (alg/kid) WITHOUT verifying the signature."""
    try:
        header_segment = token.split(".", 1)[0]
        return _b64url_json(header_segment)
    except Exception as exc:
        raise ValueError("Malformed JWT header") from exc


def select_jwk(jwks: Dict[str, Any], kid: Optional[str]) -> Optional[Dict[str, Any]]:
    """Pick the signing key from a JWKS document by kid (or the sole key)."""
    keys: List[Dict[str, Any]] = (jwks or {}).get("keys") or []
    if not keys:
        return None
    if kid:
        for key in keys:
            if key.get("kid") == kid:
                return key
        return None
    return keys[0] if len(keys) == 1 else None


def fetch_jwks(jwks_url: Optional[str] = None, *, timeout: float = 5.0) -> Dict[str, Any]:
    jwks_url = jwks_url or oidc_config()["jwksUrl"]
    if not jwks_url:
        raise RuntimeError("REALYTICS_OIDC_JWKS_URL is not configured.")
    with urllib.request.urlopen(jwks_url, timeout=timeout) as response:  # noqa: S310 (configured URL)
        return json.loads(response.read().decode("utf-8"))


def verify_oidc_token(
    token: str,
    *,
    jwks: Optional[Dict[str, Any]] = None,
    audience: Optional[str] = None,
    issuer: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify an RS256 OIDC token against the IdP JWKS. Returns the claims.

    jwks can be injected (tests / cached); otherwise it is fetched from the
    configured endpoint. Raises ValueError on validation failure, or RuntimeError
    if PyJWT is unavailable.
    """
    header = decode_unverified_header(token)
    alg = header.get("alg")
    if alg != "RS256":
        raise ValueError(f"Unsupported token alg '{alg}'; OIDC path requires RS256.")

    jwks = jwks if jwks is not None else fetch_jwks()
    jwk = select_jwk(jwks, header.get("kid"))
    if not jwk:
        raise ValueError("No matching JWKS key for token kid.")

    try:
        import jwt  # PyJWT, lazy-imported
        from jwt.algorithms import RSAAlgorithm
    except ImportError as exc:  # pragma: no cover - depends on deploy env
        raise RuntimeError(
            "OIDC RS256 verification selected but PyJWT[crypto] is not installed. "
            "Add PyJWT[crypto] to requirements and `pip install -r requirements.txt`."
        ) from exc

    public_key = RSAAlgorithm.from_jwk(json.dumps(jwk))
    config = oidc_config()
    audience = audience or config["audience"]
    issuer = issuer or config["issuer"]
    options = {"verify_aud": bool(audience)}
    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options=options,
        )
    except Exception as exc:  # jwt.PyJWTError and friends
        raise ValueError(f"OIDC token verification failed: {exc}") from exc
    return claims


def oidc_status() -> Dict[str, Any]:
    pyjwt_available = True
    try:
        import jwt  # noqa: F401
    except ImportError:
        pyjwt_available = False
    config = oidc_config()
    return {
        "configured": oidc_configured(),
        "issuer": config["issuer"],
        "audience": config["audience"],
        "jwksUrlConfigured": bool(config["jwksUrl"]),
        "rs256Available": pyjwt_available,
        "note": "OIDC RS256/JWKS verification; pilot HS256 remains available (core/idp.py).",
    }


def _now() -> int:
    return int(time.time())


def _first_env(*keys: str) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return None
