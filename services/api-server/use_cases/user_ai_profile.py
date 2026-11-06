from __future__ import annotations

import os
from typing import Any, Dict

from agents.ai_connection import PROVIDER_ALIASES, resolve_ai_context, safe_ai_context
from core.secret_vault import resolve_user_credential
from storage import decrypt_user_ai_api_key, get_user_ai_profile


def system_ai_fallback_policy() -> Dict[str, Any]:
    """Read the deployed default at request time, without resolving a user key."""
    configured_provider = (os.environ.get("REALYTICS_AI_PROVIDER") or "").strip()
    provider = configured_provider or (os.environ.get("REALYTICS_FREE_AI_PROVIDER") or "").strip() or "gemini"
    model = os.environ.get("REALYTICS_AI_MODEL") if configured_provider else os.environ.get("REALYTICS_FREE_AI_MODEL")
    context = resolve_ai_context({"provider": provider, **({"model": model.strip()} if model and model.strip() else {})})
    return {"provider": context["provider"], "model": context["model"],
            "configured": bool(configured_provider), "available": bool(context.get("direct_model_call")),
            "description": "개인 키를 사용할 수 없고 시스템 대체가 허용된 경우 이 기본 AI를 사용합니다. 실제 호출은 개인정보·예산 정책을 따르며 사용할 수 없으면 계산 규칙으로 계속합니다."}


def _preserve_routing_controls(context: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for name in ("data_classification", "dataClassification", "risk_level", "riskLevel", "max_cost_usd", "maxCostUsd",
                 "local_only", "localOnly", "prohibited_providers", "prohibitedProviders"):
        if name in override:
            context[name] = override[name]
    return context


def _restrict_profile_fallback(context: Dict[str, Any], *, has_personal_key: bool) -> Dict[str, Any]:
    # AIConnection resolves context again before execution. A flag on this
    # object alone would not prevent an environment key or another provider
    # becoming available there; the existing hard routing filter survives it.
    prohibited = set(PROVIDER_ALIASES.values())
    if has_personal_key:
        prohibited.discard(context["provider"])
    for provider in [*(context.get("prohibited_providers") or []), *(context.get("prohibitedProviders") or [])]:
        prohibited.add(PROVIDER_ALIASES.get(str(provider).strip().lower(), str(provider).strip().lower()))
    context["prohibited_providers"] = sorted(prohibited)
    context["allow_system_fallback"] = False
    if not has_personal_key:
        context.update(direct_model_call=False, server_side_extraction=False, requires_api_key="personal_api_key")
    return context


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
    3. Configured system default; Gemini remains the unconfigured default.

    The returned context may include an in-memory `api_key` for the actual model
    call. Always use `safe_ai_context()` before logging or returning it.
    """
    override = dict(override or {})
    if override.get("apiKey") or override.get("api_key"):
        raise ValueError("Inline AI credentials are forbidden; use /auth/ai-credentials")
    profile = get_user_ai_profile(str(user_id), include_secret=True, tenant_id=tenant_id) if user_id and use_legacy_profile else None
    active_profile = profile if profile and profile.get("status") == "active" else None
    fallback_disabled = active_profile is not None and active_profile.get("allowSystemFallback") is False
    if override.get("provider") and not fallback_disabled:
        return _preserve_routing_controls(resolve_ai_context(
            {
                **override,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "host": host,
                "selection_source": override.get("selection_source") or "explicit_request_override",
            },
            user_id=user_id,
            host=host,
        ), override)

    if active_profile:
        profile = active_profile
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
            api_key = decrypt_user_ai_api_key(str(user_id), tenant_id=tenant_id)
        if api_key or fallback_disabled:
            provider = profile.get("provider") or system_ai_fallback_policy()["provider"]
            if override.get("provider") and PROVIDER_ALIASES.get(str(override["provider"]).strip().lower(), str(override["provider"]).strip().lower()) != PROVIDER_ALIASES.get(str(provider).strip().lower(), str(provider).strip().lower()):
                raise ValueError("시스템 대체가 꺼진 개인 AI 프로필의 공급자를 요청에서 변경할 수 없습니다.")
            context = resolve_ai_context(
                {
                    "provider": provider,
                    "model": override.get("model") or profile.get("model"),
                    "api_key": api_key,
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "host": host,
                    "selection_source": "user_ai_profile" if api_key else "user_ai_profile_fallback_disabled",
                },
                user_id=user_id,
                host=host,
            )
            context = _preserve_routing_controls(context, override)
            return _restrict_profile_fallback(context, has_personal_key=bool(api_key)) if fallback_disabled else context

    fallback = system_ai_fallback_policy()
    return _preserve_routing_controls(resolve_ai_context(
        {
            "provider": fallback["provider"],
            "model": fallback["model"],
            "user_id": user_id,
            "tenant_id": tenant_id,
            "host": host,
            "selection_source": "user_ai_profile_without_key_system_fallback" if active_profile else "system_free_fallback",
        },
        user_id=user_id,
        host=host,
    ), override)


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
