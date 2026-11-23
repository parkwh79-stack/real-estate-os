from __future__ import annotations

"""OIDC asymmetric JWT / JWKS token verification.

Complements the existing pilot HS256 path (core/idp.py). Fetches the IdP's JWKS,
selects the signing key by `kid`, and verifies an RS256 or ES256 token with
issuer/audience/expiry checks. Signature verification is delegated to PyJWT
(lazy-imported — no hand-rolled crypto); JWKS fetch/selection and claim/config
logic are pure-stdlib and unit-tested. Live verification needs PyJWT installed
and a reachable JWKS endpoint.
"""

import base64
import http.client
import json
import os
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional


ALLOWED_ASYMMETRIC_ALGORITHMS = frozenset({"RS256", "ES256"})
_JWK_TYPES_BY_ALGORITHM = {"RS256": "RSA", "ES256": "EC"}
_JWKS_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_JWKS_CACHE_LOCK = threading.Lock()


class OidcVerifierUnavailable(RuntimeError):
    """The configured JWKS authority could not be read within its time budget."""


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


def fetch_jwks(
    jwks_url: Optional[str] = None,
    *,
    timeout: float = 5.0,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    jwks_url = jwks_url or oidc_config()["jwksUrl"]
    if not jwks_url:
        raise RuntimeError("REALYTICS_OIDC_JWKS_URL is not configured.")
    cache_ttl = max(0, int(os.environ.get("REALYTICS_OIDC_JWKS_CACHE_SECONDS") or 600))
    now = time.monotonic()
    with _JWKS_CACHE_LOCK:
        cached = _JWKS_CACHE.get(jwks_url)
        if not force_refresh and cached and cached[0] > now:
            return cached[1]

    # Keep retries inside the caller's existing timeout budget. A quick DNS,
    # proxy, or connection reset may recover immediately, while a persistent
    # outage still fails closed before the BFF's 10-second request deadline.
    deadline = now + max(0.1, float(timeout))
    last_error: Exception | None = None
    payload: Dict[str, Any] | None = None
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            with urllib.request.urlopen(  # noqa: S310 (configured URL)
                jwks_url,
                timeout=max(0.1, remaining),
            ) as response:
                decoded = json.loads(response.read().decode("utf-8"))
            if not isinstance(decoded, dict) or not isinstance(
                decoded.get("keys"), list
            ):
                raise ValueError("JWKS response must contain a keys array")
            payload = decoded
            break
        except (
            OSError,
            http.client.HTTPException,
            json.JSONDecodeError,
            UnicodeError,
            ValueError,
        ) as exc:
            last_error = exc
            if attempt == 1:
                break
    if payload is None:
        raise OidcVerifierUnavailable(
            "OIDC JWKS authority is unavailable."
        ) from last_error

    with _JWKS_CACHE_LOCK:
        _JWKS_CACHE[jwks_url] = (time.monotonic() + cache_ttl, payload)
    return payload


def verify_oidc_token(
    token: str,
    *,
    jwks: Optional[Dict[str, Any]] = None,
    audience: Optional[str] = None,
    issuer: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify an asymmetric OIDC token against the IdP JWKS.

    jwks can be injected (tests / cached); otherwise it is fetched from the
    configured endpoint. Raises ValueError on validation failure, or RuntimeError
    if the JWKS authority or PyJWT verifier is unavailable.
    """
    header = decode_unverified_header(token)
    alg = header.get("alg")
    if alg not in ALLOWED_ASYMMETRIC_ALGORITHMS:
        raise ValueError(
            f"Unsupported token alg '{alg}'; OIDC path requires RS256 or ES256."
        )

    injected_jwks = jwks is not None
    jwks = jwks if injected_jwks else fetch_jwks()
    jwk = select_jwk(jwks, header.get("kid"))
    if not jwk and not injected_jwks:
        # Refresh once immediately so a rotated signing key does not have to
        # wait for the normal in-process cache window.
        jwks = fetch_jwks(force_refresh=True)
        jwk = select_jwk(jwks, header.get("kid"))
    if not jwk:
        raise ValueError("No matching JWKS key for token kid.")
    if jwk.get("alg") and jwk.get("alg") != alg:
        raise ValueError("Token algorithm does not match the selected JWKS key.")
    expected_kty = _JWK_TYPES_BY_ALGORITHM[alg]
    if jwk.get("kty") != expected_kty:
        raise ValueError(
            f"Token algorithm {alg} requires JWKS key type {expected_kty}."
        )

    try:
        import jwt  # PyJWT, lazy-imported
    except ImportError as exc:  # pragma: no cover - depends on deploy env
        raise RuntimeError(
            "OIDC asymmetric verification selected but PyJWT[crypto] is not installed. "
            "Add PyJWT[crypto] to requirements and `pip install -r requirements.txt`."
        ) from exc

    public_key = jwt.PyJWK.from_dict(jwk, algorithm=alg).key
    config = oidc_config()
    audience = audience or config["audience"]
    issuer = issuer or config["issuer"]
    options = {"verify_aud": bool(audience)}
    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=[alg],
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
        "asymmetricVerifierAvailable": pyjwt_available,
        "rs256Available": pyjwt_available,
        "allowedAlgorithms": sorted(ALLOWED_ASYMMETRIC_ALGORITHMS),
        "jwksCacheSeconds": max(
            0, int(os.environ.get("REALYTICS_OIDC_JWKS_CACHE_SECONDS") or 600)
        ),
        "note": "OIDC ES256/RS256 JWKS verification; pilot HS256 remains available (core/idp.py).",
    }


def _now() -> int:
    return int(time.time())


def _first_env(*keys: str) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return None
