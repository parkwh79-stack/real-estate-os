from __future__ import annotations

from typing import Any, Dict

from fastapi import Request

from core.idp import verified_principal_from_request_state


def credential_session_binding_from_request(
    request: Request,
    context: Dict[str, Any],
) -> Dict[str, str] | None:
    """Return the server-verified session boundary for a BYOK credential.

    Browser headers and payload fields are never accepted as a credential
    session identity. Local compatibility sessions use the already-resolved
    server session. Bearer identities use only the ``session_id`` claim from
    the JWT principal that was verified earlier in the same request.
    """

    if context.get("authenticated") is not True:
        return None
    user = context.get("user")
    if not isinstance(user, dict):
        return None
    user_id = str(user.get("userId") or "").strip()
    tenant_id = str(user.get("tenantId") or "").strip()
    if not user_id or not tenant_id:
        return None

    session = context.get("session")
    if isinstance(session, dict):
        session_id = str(session.get("sessionId") or "").strip()
        if not session_id:
            return None
        return {
            "userId": user_id,
            "tenantId": tenant_id,
            "sessionId": session_id,
            "source": "server_session",
        }

    if context.get("authSource") != "bearer_token":
        return None
    principal = verified_principal_from_request_state(request)
    if principal is None:
        return None
    session_id = str(principal.claims.get("session_id") or "").strip()
    if not session_id:
        return None
    if principal.user_id != user_id:
        return None
    return {
        "userId": user_id,
        "tenantId": tenant_id,
        "sessionId": session_id,
        "source": "verified_bearer_claims",
    }
