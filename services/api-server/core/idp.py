from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict

from fastapi import HTTPException, Request

from core.runtime_profile import security_default


SECRET_HINT_KEYS = {"secret", "token", "key", "password"}


@dataclass
class IdpPrincipal:
    user_id: str
    tenant_id: str
    email: str | None
    display_name: str
    role: str
    provider: str
    principal_type: str
    claims: Dict[str, Any]
    token_id: str | None = None


def idp_status() -> Dict[str, Any]:
    mode = idp_mode()
    return {
        "schemaVersion": "realytics-idp-status-v1",
        "mode": mode,
        "enabled": mode != "off",
        "provider": os.environ.get("REALYTICS_IDP_PROVIDER") or "external-idp",
        "issuer": _first_env("REALYTICS_IDP_ISSUER", "REALYTICS_OIDC_ISSUER"),
        "audience": _first_env("REALYTICS_IDP_AUDIENCE", "REALYTICS_OIDC_AUDIENCE"),
        "jwksUrlConfigured": bool(_first_env("REALYTICS_IDP_JWKS_URL", "REALYTICS_OIDC_JWKS_URL")),
        "hs256SecretConfigured": bool(os.environ.get("REALYTICS_IDP_JWT_SECRET")),
        "unsignedDevTokensAllowed": _allow_unsigned_dev_tokens(),
        "tenantIsolationMode": tenant_isolation_mode(),
        "secretPolicy": "Secrets are read from environment/secret manager only and are not returned by this API.",
        "limitations": _limitations(mode),
    }


def idp_mode() -> str:
    return (os.environ.get("REALYTICS_IDP_MODE") or "off").lower()


def tenant_isolation_mode() -> str:
    return (
        os.environ.get("REALYTICS_TENANT_ISOLATION_MODE")
        or security_default(local="permissive", hardened="strict")
    ).lower()


def principal_from_request(request: Request) -> IdpPrincipal | None:
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    token = _bearer_token(auth_header)
    if not token:
        return None
    return principal_from_bearer_token(token)


def principal_from_bearer_token(token: str) -> IdpPrincipal:
    mode = idp_mode()
    if mode == "off":
        raise HTTPException(status_code=401, detail={"error": "idp_disabled"})
    if mode in {"hs256", "jwt_hs256"}:
        claims = _decode_hs256_jwt(token)
    elif mode in {"oidc", "jwks", "rs256"}:
        try:
            from core.oidc import verify_oidc_token

            claims = verify_oidc_token(token)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail={"error": "invalid_oidc_token"}) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"error": "oidc_verifier_unavailable"}) from exc
    elif mode in {"dev", "unsigned-dev"}:
        if not _allow_unsigned_dev_tokens():
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "unsigned_dev_tokens_disabled",
                    "message": "Set REALYTICS_IDP_ALLOW_UNSIGNED_DEV_TOKENS=true only for local development.",
                },
            )
        claims = _decode_unverified_jwt(token)
    else:
        raise HTTPException(status_code=501, detail={"error": "unsupported_idp_mode", "mode": mode})
    _validate_standard_claims(claims)
    return _principal_from_claims(claims)


def enforce_tenant_header_match(request: Request, principal: IdpPrincipal | None) -> None:
    if not principal:
        return
    requested_tenant = (
        request.headers.get("x-realytics-tenant-id")
        or request.headers.get("x-tenant-id")
        or request.query_params.get("tenant_id")
    )
    if requested_tenant and requested_tenant != principal.tenant_id:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "tenant_mismatch",
                "message": "Requested tenant does not match authenticated principal tenant.",
                "requestedTenantId": requested_tenant,
                "principalTenantId": principal.tenant_id,
            },
        )


def sanitized_idp_config() -> Dict[str, Any]:
    status = idp_status()
    return {
        key: value
        for key, value in status.items()
        if not any(secret_key in key.lower() for secret_key in SECRET_HINT_KEYS)
    }


def _bearer_token(auth_header: str | None) -> str | None:
    if not auth_header:
        return None
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail={"error": "invalid_authorization_header"})
    return token.strip()


def _decode_hs256_jwt(token: str) -> Dict[str, Any]:
    secret = os.environ.get("REALYTICS_IDP_JWT_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail={"error": "idp_secret_not_configured"})
    header, payload, signature = _split_jwt(token)
    header_obj = _json_b64url_decode(header)
    if header_obj.get("alg") != "HS256":
        raise HTTPException(status_code=401, detail={"error": "unsupported_jwt_alg", "alg": header_obj.get("alg")})
    expected = hmac.new(secret.encode("utf-8"), f"{header}.{payload}".encode("utf-8"), hashlib.sha256).digest()
    actual = _b64url_decode(signature)
    if not hmac.compare_digest(expected, actual):
        raise HTTPException(status_code=401, detail={"error": "invalid_token_signature"})
    return _json_b64url_decode(payload)


def _decode_unverified_jwt(token: str) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        raise HTTPException(status_code=401, detail={"error": "invalid_jwt"})
    return _json_b64url_decode(parts[1])


def _split_jwt(token: str) -> tuple[str, str, str]:
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail={"error": "invalid_jwt"})
    return parts[0], parts[1], parts[2]


def _validate_standard_claims(claims: Dict[str, Any]) -> None:
    now = int(time.time())
    exp = claims.get("exp")
    if exp is not None and int(exp) < now:
        raise HTTPException(status_code=401, detail={"error": "token_expired"})
    nbf = claims.get("nbf")
    if nbf is not None and int(nbf) > now:
        raise HTTPException(status_code=401, detail={"error": "token_not_yet_valid"})
    issuer = os.environ.get("REALYTICS_IDP_ISSUER")
    if issuer and claims.get("iss") != issuer:
        raise HTTPException(status_code=401, detail={"error": "issuer_mismatch"})
    audience = os.environ.get("REALYTICS_IDP_AUDIENCE")
    if audience:
        aud = claims.get("aud")
        valid = audience in aud if isinstance(aud, list) else aud == audience
        if not valid:
            raise HTTPException(status_code=401, detail={"error": "audience_mismatch"})


def _principal_from_claims(claims: Dict[str, Any]) -> IdpPrincipal:
    user_id = (
        claims.get("realytics_user_id")
        or claims.get("preferred_username")
        or claims.get("email")
        or claims.get("sub")
    )
    if not user_id:
        raise HTTPException(status_code=401, detail={"error": "missing_user_claim"})
    tenant_id = claims.get("realytics_tenant_id") or claims.get("tenant_id") or claims.get("tid")
    if not tenant_id:
        raise HTTPException(status_code=401, detail={"error": "missing_tenant_claim"})
    role = claims.get("realytics_role") or claims.get("role") or _first_role(claims) or "viewer"
    email = claims.get("email")
    display_name = claims.get("name") or claims.get("display_name") or str(user_id)
    return IdpPrincipal(
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        email=str(email) if email else None,
        display_name=str(display_name),
        role=str(role),
        provider=os.environ.get("REALYTICS_IDP_PROVIDER") or claims.get("iss") or "external-idp",
        principal_type=_principal_type_from_claims(claims),
        claims=claims,
        token_id=claims.get("jti"),
    )


def _principal_type_from_claims(claims: Dict[str, Any]) -> str:
    """Classify signed bearer principals without guessing that clients are humans.

    Hardened approval paths require the IdP to map an explicit
    ``realytics_principal_type=human`` claim. Common client-credential markers
    are still classified as service for audit clarity; every other token remains
    unknown and therefore fails closed at the Parser V2 approval boundary.
    """

    explicit = str(claims.get("realytics_principal_type") or "").casefold()
    if explicit in {"human", "service"}:
        return explicit
    grant_type = str(claims.get("gty") or claims.get("grant_type") or "").casefold()
    token_use = str(claims.get("token_use") or "").casefold()
    identity_type = str(claims.get("idtyp") or "").casefold()
    if grant_type == "client_credentials" or token_use == "client" or identity_type == "app":
        return "service"
    return "unknown"


def _first_role(claims: Dict[str, Any]) -> str | None:
    roles = claims.get("roles") or claims.get("groups")
    if isinstance(roles, list) and roles:
        return str(roles[0])
    return None


def _allow_unsigned_dev_tokens() -> bool:
    return (os.environ.get("REALYTICS_IDP_ALLOW_UNSIGNED_DEV_TOKENS") or "false").lower() in {"1", "true", "yes"}


def _limitations(mode: str) -> list[str]:
    if mode in {"oidc", "jwks", "rs256"}:
        return ["OIDC RS256/JWKS signature, issuer, audience, and expiry validation is enabled."]
    if mode in {"hs256", "jwt_hs256"}:
        return [
            "HS256 JWT verification is enabled for pilot/enterprise proxy integrations.",
            "Use OIDC JWKS/RS256 mode for hardened deployments.",
        ]
    if mode in {"dev", "unsigned-dev"}:
        return [
            "Unsigned token parsing is for local development only and must not be enabled in production.",
        ]
    return [
        "External IdP is disabled; REALYTICS uses local pilot session/header identity.",
    ]


def _json_b64url_decode(value: str) -> Dict[str, Any]:
    decoded = _b64url_decode(value)
    try:
        return json.loads(decoded.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=401, detail={"error": "invalid_jwt_json"}) from exc


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise HTTPException(status_code=401, detail={"error": "invalid_jwt_encoding"}) from exc


def _first_env(*keys: str) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return None
