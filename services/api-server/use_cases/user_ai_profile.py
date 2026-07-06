from __future__ import annotations

import os
from typing import Any, Dict

from agents.ai_connection import resolve_ai_context, safe_ai_context
from storage import decrypt_user_ai_api_key, get_user_ai_profile


FREE_FALLBACK_PROVIDER = os.environ.get("REALYTICS_FREE_AI_PROVIDER") or "gemini"
FREE_FALLBACK_MODEL = os.environ.get("REALYTICS_FREE_AI_MODEL") or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"


def build_user_ai_context(
    *,
    user_id: str | None,
    tenant_id: str | None = None,
    host: str = "api",
    override: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Resolve the AI brain for a logged-in user.

    Precedence:
    1. Explicit override provider/model/apiKey from an API request.
    2. User BYOK profile stored after login.
    3. System free fallback, normally Gemini 2.5 Flash.

    The returned context may include an in-memory `api_key` for the actual model
    call. Always use `safe_ai_context()` before logging or returning it.
    """
    override = dict(override or {})
    if override.get("provider") or override.get("apiKey") or override.get("api_key"):
        return resolve_ai_context(
            {
                **override,
                "api_key": override.get("apiKey") or override.get("api_key"),
                "user_id": user_id,
                "tenant_id": tenant_id,
                "host": host,
                "selection_source": override.get("selection_source") or "explicit_request_override",
            },
            user_id=user_id,
            host=host,
        )

    profile = get_user_ai_profile(str(user_id or ""), include_secret=False) if user_id else None
    if profile and profile.get("status") == "active":
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
        "requiresApiKey": safe.get("requires_api_key"),
        "selectionSource": safe.get("selection_source"),
        "host": safe.get("host"),
    }
