from __future__ import annotations

import os
from typing import Any, Dict

from agents.ai_connection import resolve_ai_context, safe_ai_context
from core.secret_vault import resolve_user_credential
from storage import decrypt_user_ai_api_key, get_user_ai_profile


FREE_FALLBACK_PROVIDER = os.environ.get("REALYTICS_FREE_AI_PROVIDER") or "gemini"
FREE_FALLBACK_MODEL = os.environ.get("REALYTICS_FREE_AI_MODEL") or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"


def build_user_ai_context(
    *,
    user_id: str | None,
    tenant_id: str | None = None,
    session_id: str | None = None,
    host: str = "api",
    override: Dict[str, Any] | None = None,
    use_legacy_profile: bool = True,
) -> Dict[str, Any]:
    """Resolve the AI brain for a logged-in user.

    Precedence:
    1. Explicit provider/model override without an inline credential.
    2. User BYOK profile stored after login.
    3. System free fallback, normally Gemini 2.5 Flash.

    The returned context may include an in-memory `api_key` for the actual model
    call. Always use `safe_ai_context()` before logging or returning it.
    """
    override = dict(override or {})
    if override.get("apiKey") or override.get("api_key"):
        raise ValueError("Inline AI credentials are forbidden; use /auth/ai-credentials")
    if override.get("provider"):
        return resolve_ai_context(
            {
                **override,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "host": host,
                "selection_source": override.get("selection_source") or "explicit_request_override",
            },
            user_id=user_id,
            host=host,
        )

    profile = get_user_ai_profile(str(user_id), include_secret=True) if user_id and use_legacy_profile else None
    if profile and profile.get("status") == "active":
        credential_ref = profile.get("credentialRef")
        if credential_ref:
            # A session-bound reference must never fall through to a legacy
            # persisted key when the user/tenant/session boundary does not
            # match or the reference has expired.
            api_key = resolve_user_credential(
                credential_ref,
                user_id=str(user_id),
                tenant_id=tenant_id,
                session_id=session_id,
            )
        else:
            api_key = decrypt_user_ai_api_key(str(user_id))
        if api_key or profile.get("allowSystemFallback"):
            return resolve_ai_context(
                {
                    "provider": profile.get("provider") or FREE_FALLBACK_PROVIDER,
                    "model": profile.get("model") or FREE_FALLBACK_MODEL,
                    "api_key": api_key,
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "host": host,
                    "selection_source": "user_ai_profile" if api_key else "user_ai_profile_without_key_system_fallback",
                },
                user_id=user_id,
                host=host,
            )

    return resolve_ai_context(
        {
            "provider": FREE_FALLBACK_PROVIDER,
            "model": FREE_FALLBACK_MODEL,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "host": host,
            "selection_source": "system_free_fallback",
        },
        user_id=user_id,
        host=host,
    )


def safe_user_ai_context_summary(context: Dict[str, Any]) -> Dict[str, Any]:
    safe = safe_ai_context(context)
    return {
        "provider": safe.get("provider"),
        "runtimeProvider": safe.get("runtime_provider"),
        "model": safe.get("model"),
        "directModelCall": safe.get("direct_model_call"),
        "requiresApiKey": context.get("requires_api_key"),
        "selectionSource": safe.get("selection_source"),
        "host": safe.get("host"),
    }
