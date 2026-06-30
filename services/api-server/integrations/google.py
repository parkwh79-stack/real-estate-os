from __future__ import annotations

"""Google OAuth 2.0 / OpenID Connect login integration.

Mirrors the Kakao integration: build the authorize URL, exchange the
authorization code for tokens, fetch the userinfo, and shape REALYTICS
user/session records. Dependency-free (urllib) and non-fabricating — if the
Google app credentials are not configured the authorize-url builder reports
``ready: False`` and the caller decides what to do (pilot mock vs. honest error).
"""

import json
import os
from typing import Any, Dict
from urllib import request as urllib_request
from urllib.parse import urlencode

from fastapi import HTTPException


GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"


def _default_redirect_uri() -> str:
    base = os.environ.get("REALYTICS_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{base}/integrations/google/callback"


def google_integration_status() -> Dict[str, Any]:
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI") or _default_redirect_uri()
    return {
        "schemaVersion": "realytics-google-integration-status-v1",
        "login": {
            "provider": "google",
            "oidcDiscoveryUrl": GOOGLE_DISCOVERY_URL,
            "issuer": GOOGLE_ISSUER,
            "clientIdConfigured": bool(client_id),
            "clientSecretConfigured": bool(os.environ.get("GOOGLE_CLIENT_SECRET")),
            "redirectUriConfigured": bool(os.environ.get("GOOGLE_REDIRECT_URI")),
            "redirectUri": redirect_uri,
            "scope": os.environ.get("GOOGLE_LOGIN_SCOPE") or "openid email profile",
        },
        "limitations": [
            "Google Login requires a registered OAuth 2.0 client (Client ID/Secret) and authorized redirect URI in Google Cloud Console.",
            "Without credentials the login falls back to a clearly-labelled pilot mock and does not authenticate a real Google account.",
        ],
    }


def build_google_authorize_url(
    *,
    state: str,
    redirect_uri: str | None = None,
    prompt: str | None = "consent",
) -> Dict[str, Any]:
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    resolved_redirect_uri = redirect_uri or os.environ.get("GOOGLE_REDIRECT_URI") or _default_redirect_uri()
    if not client_id:
        return {
            "schemaVersion": "realytics-google-authorize-url-v1",
            "ready": False,
            "missingConfig": ["GOOGLE_CLIENT_ID"],
            "authorizeUrl": None,
        }
    params = {
        "client_id": client_id,
        "redirect_uri": resolved_redirect_uri,
        "response_type": "code",
        "scope": os.environ.get("GOOGLE_LOGIN_SCOPE") or "openid email profile",
        "state": state,
        "access_type": "offline",
        "include_granted_scopes": "true",
    }
    if prompt:
        params["prompt"] = prompt
    return {
        "schemaVersion": "realytics-google-authorize-url-v1",
        "ready": True,
        "authorizeUrl": f"{GOOGLE_AUTHORIZATION_URL}?{urlencode(params)}",
        "redirectUri": resolved_redirect_uri,
        "scope": params["scope"],
        "state": state,
    }


def google_login_configured() -> bool:
    """Real Google OAuth requires both the client id and secret (auth-code flow)."""
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def exchange_google_authorization_code(code: str, *, redirect_uri: str | None = None) -> Dict[str, Any]:
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    resolved_redirect_uri = redirect_uri or os.environ.get("GOOGLE_REDIRECT_URI") or _default_redirect_uri()
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail={"error": "google_login_not_configured"})
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": resolved_redirect_uri,
    }
    return _post_form_json(GOOGLE_TOKEN_URL, form)


def fetch_google_user_info(access_token: str) -> Dict[str, Any]:
    if not access_token:
        raise HTTPException(status_code=401, detail={"error": "missing_google_access_token"})
    req = urllib_request.Request(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    with urllib_request.urlopen(req, timeout=10) as response:  # nosec - official Google endpoint constant
        return json.loads(response.read().decode("utf-8"))


def build_google_identity_records(
    *,
    tokens: Dict[str, Any],
    user_info: Dict[str, Any] | None,
    redirect_path: str,
) -> Dict[str, Any]:
    info = user_info or {}
    subject = info.get("sub")
    if not subject:
        raise HTTPException(status_code=401, detail={"error": "missing_google_subject"})
    email = info.get("email")
    display_name = info.get("name") or (str(email) if email else f"google-{subject}")
    tenant_id = (
        os.environ.get("GOOGLE_DEFAULT_TENANT_ID")
        or os.environ.get("REALYTICS_DEFAULT_TENANT_ID")
        or "google-workspace"
    )
    role = os.environ.get("GOOGLE_DEFAULT_ROLE") or "viewer"
    user_id = str(email or f"google:{subject}")
    user = {
        "userId": user_id,
        "tenantId": tenant_id,
        "email": str(email) if email else None,
        "displayName": str(display_name),
        "role": role,
        "status": "active",
        "metadata": {
            "provider": "google",
            "googleSubject": str(subject),
            "emailVerified": bool(info.get("email_verified")),
            "idTokenPresent": bool(tokens.get("id_token")),
        },
    }
    session = {
        "userId": user_id,
        "tenantId": tenant_id,
        "provider": "google",
        "host": "google_oauth_callback",
        "metadata": {
            "googleSubject": str(subject),
            "tokenType": tokens.get("token_type"),
            "scope": tokens.get("scope"),
            "idTokenPresent": bool(tokens.get("id_token")),
            "redirectPath": redirect_path or "/dashboard",
        },
    }
    return {
        "user": user,
        "session": session,
        "redirectPath": redirect_path or "/dashboard",
    }


def _post_form_json(url: str, form: Dict[str, Any]) -> Dict[str, Any]:
    data = urlencode(form).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=10) as response:  # nosec - official Google endpoint constant
        return json.loads(response.read().decode("utf-8"))
