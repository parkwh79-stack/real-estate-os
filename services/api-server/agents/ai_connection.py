import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict

from agents.ai_audit import build_ai_brain_used_audit_event, record_ai_brain_used
from agent_platform.brains.control import record_brain_provider_result, route_ai_connection_candidates
from core.secret_redaction import (
    collect_secret_values,
    redact_secret_text,
    redact_sensitive_value,
)


CLAUDE_API_ALIASES = {"anthropic", "claude", "claude_api"}
CLAUDE_DESKTOP_ALIASES = {"claude-desktop", "claude_desktop", "mcp_host", "host_llm"}
OPENAI_ALIASES = {"openai", "chatgpt", "gpt"}

# Provider aliases normalised to a canonical provider key.
PROVIDER_ALIASES = {
    "openai": "openai", "chatgpt": "openai", "gpt": "openai",
    "anthropic": "anthropic", "claude": "anthropic", "claude_api": "anthropic",
    "claude-desktop": "claude_desktop", "claude_desktop": "claude_desktop",
    "mcp_host": "claude_desktop", "host_llm": "claude_desktop",
    "gemini": "gemini", "google": "gemini",
    "groq": "groq",
    "openrouter": "openrouter",
    "deepseek": "deepseek",
    "together": "together", "togetherai": "together",
    "local": "local", "ollama": "local", "vllm": "local", "self_hosted": "local",
}

# OpenAI-compatible providers: anything that speaks the OpenAI Chat Completions
# API can be reached by overriding base_url on the OpenAI SDK client. This lets
# REALYTICS use Gemini, Groq, OpenRouter, DeepSeek, Together, or a self-hosted
# (Ollama/vLLM) endpoint through one code path. Free tiers (Google AI Studio,
# Groq, OpenRouter) and on-prem open models (privacy for sensitive PF data) are
# usable without new SDKs.
OPENAI_COMPATIBLE_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "base_url": None,  # default OpenAI endpoint
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o",
        "json_mode": True,
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
        "default_model": "gemini-2.5-flash",
        "json_mode": True,
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",
        "json_mode": True,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "default_model": "meta-llama/llama-3.3-70b-instruct",
        "json_mode": False,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
        "json_mode": True,
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "json_mode": True,
    },
    # Self-hosted open models (Ollama/vLLM/LM Studio). base_url and model come
    # from environment; api key is optional (Ollama ignores it).
    "local": {
        "base_url": None,
        "base_url_env": "REALYTICS_LLM_BASE_URL",
        "api_key_env": "REALYTICS_LLM_API_KEY",
        "default_model": "qwen2.5",
        "json_mode": False,
        "is_local": True,
    },
}


@dataclass
class AIExtractionResult:
    data: Dict[str, Any] | None
    meta: Dict[str, Any]
    error: str | None = None


def safe_ai_context(context: Dict[str, Any] | None) -> Dict[str, Any]:
    source = context or {}
    return redact_sensitive_value(source, secrets=collect_secret_values(source))


def _safe_provider_error(
    error: Any,
    context: Dict[str, Any] | None,
    *additional_secrets: Any,
) -> str:
    source = context or {}
    return redact_secret_text(
        error,
        secrets=(*collect_secret_values(source), *additional_secrets),
    )


def resolve_ai_context(
    ai_context: Dict[str, Any] | None = None,
    *,
    user_id: str | None = None,
    host: str | None = None,
) -> Dict[str, Any]:
    requested = dict(ai_context or {})
    raw_provider = (
        requested.get("provider")
        or requested.get("ai_provider")
        or os.environ.get("REALYTICS_AI_PROVIDER")
        or "openai"
    )
    provider = PROVIDER_ALIASES.get(str(raw_provider).strip().lower(), str(raw_provider).strip().lower())

    resolved_user_id = (
        user_id
        or requested.get("user_id")
        or os.environ.get("REALYTICS_USER_ID")
        or os.environ.get("MCP_USER_ID")
        or "anonymous"
    )
    resolved_host = host or requested.get("host") or os.environ.get("REALYTICS_AI_HOST") or "api"

    explicit_api_key = requested.get("api_key") or requested.get("apiKey")

    base = {
        "host": resolved_host,
        "provider": provider,
        "user_id": str(resolved_user_id),
        "tenant_id": requested.get("tenant_id") or requested.get("tenantId"),
        "project_id": requested.get("project_id") or requested.get("projectId"),
        "deal_id": requested.get("deal_id") or requested.get("dealId"),
        "run_id": requested.get("run_id") or requested.get("runId"),
        "agent_id": requested.get("agent_id") or requested.get("agentId"),
        "input_evidence_ids": requested.get("input_evidence_ids") or requested.get("inputEvidenceIds"),
        "output_finding_ids": requested.get("output_finding_ids") or requested.get("outputFindingIds"),
        "selection_source": requested.get("selection_source"),
        "base_url": None,
        "api_key_env": None,
        "api_key": explicit_api_key,
        "supports_json_mode": False,
        "is_local": False,
    }

    if provider == "anthropic":
        model = requested.get("model") or os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-6"
        direct = bool(explicit_api_key or os.environ.get("ANTHROPIC_API_KEY"))
        base.update({
            "runtime_provider": "anthropic",
            "model": model,
            "api_key_env": "ANTHROPIC_API_KEY",
            "direct_model_call": direct,
            "requires_api_key": None if direct else "ANTHROPIC_API_KEY",
            "server_side_extraction": direct,
            "host_llm_available": False,
        })
        return base

    if provider == "claude_desktop":
        model = requested.get("model") or os.environ.get("CLAUDE_DESKTOP_MODEL") or "claude-desktop-host"
        base.update({
            "runtime_provider": "mcp_host_llm",
            "model": model,
            "direct_model_call": False,
            "requires_api_key": None,
            "server_side_extraction": False,
            "host_llm_available": True,
        })
        return base

    if provider in OPENAI_COMPATIBLE_PROVIDERS:
        spec = OPENAI_COMPATIBLE_PROVIDERS[provider]
        is_local = bool(spec.get("is_local"))
        base_url = spec.get("base_url")
        if spec.get("base_url_env"):
            base_url = os.environ.get(spec["base_url_env"]) or base_url
        api_key_env = spec.get("api_key_env")
        env_model = os.environ.get(f"{provider.upper()}_MODEL") or (
            os.environ.get("REALYTICS_LLM_MODEL") if is_local else None
        )
        model = requested.get("model") or env_model or spec.get("default_model") or "unknown"
        has_key = bool(explicit_api_key or (api_key_env and os.environ.get(api_key_env)))
        # Local endpoints are usable once a base_url is configured, even without a key.
        direct = (is_local and bool(base_url)) or (not is_local and has_key)
        requires_api_key = None
        if not direct:
            requires_api_key = spec.get("base_url_env") if is_local else api_key_env
        base.update({
            "runtime_provider": "openai" if provider == "openai" else "openai_compatible",
            "model": model,
            "base_url": base_url,
            "api_key_env": api_key_env,
            "supports_json_mode": bool(spec.get("json_mode")),
            "is_local": is_local,
            "direct_model_call": direct,
            "requires_api_key": requires_api_key,
            "server_side_extraction": direct,
            "host_llm_available": False,
        })
        return base

    base.update({
        "runtime_provider": "unsupported",
        "model": requested.get("model") or "unknown",
        "direct_model_call": False,
        "requires_api_key": None,
        "server_side_extraction": False,
        "host_llm_available": False,
    })
    return base


def _route_execution_context(
    ai_context: Dict[str, Any] | None,
    *,
    task_type: str,
    context_token_estimate: int,
    estimated_output_tokens: int = 2000,
    include_fallbacks: bool = True,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    requested_context = dict(ai_context or {})
    primary = resolve_ai_context(requested_context)
    routing_controls = {
        "data_classification": requested_context.get("data_classification")
        or requested_context.get("dataClassification")
        or primary.get("data_classification")
        or "confidential",
        "risk_level": requested_context.get("risk_level")
        or requested_context.get("riskLevel")
        or primary.get("risk_level")
        or "high",
        "max_cost_usd": requested_context.get("max_cost_usd")
        if requested_context.get("max_cost_usd") is not None
        else requested_context.get("maxCostUsd", primary.get("max_cost_usd") or 0),
        "local_only": bool(
            requested_context.get("local_only")
            if requested_context.get("local_only") is not None
            else requested_context.get("localOnly", primary.get("local_only"))
        ),
        "prohibited_providers": list(
            requested_context.get("prohibited_providers")
            or requested_context.get("prohibitedProviders")
            or primary.get("prohibited_providers")
            or []
        ),
        "request_id": requested_context.get("request_id")
        or requested_context.get("requestId")
        or primary.get("request_id"),
    }
    primary.update(routing_controls)
    candidates = [primary]
    common = {
        "tenant_id": primary.get("tenant_id"),
        "user_id": primary.get("user_id"),
        "project_id": primary.get("project_id"),
        "run_id": primary.get("run_id"),
        **routing_controls,
    }
    for provider in (("local", "anthropic", "openai", "gemini", "groq", "openrouter", "deepseek", "together") if include_fallbacks else ()):
        if provider == primary.get("provider"):
            continue
        fallback = resolve_ai_context({"provider": provider, **common})
        fallback.update(common)
        if fallback.get("direct_model_call"):
            candidates.append(fallback)
    selected, routing = route_ai_connection_candidates(
        candidates,
        task_type=task_type,
        context_token_estimate=context_token_estimate,
        estimated_output_tokens=estimated_output_tokens,
    )
    context = dict(selected or primary)
    context["brain_routing_event_id"] = routing.get("routingEventId")
    context["brain_routing_policy_version"] = routing.get("routingPolicyVersion")
    context["brain_routing_selected_profile_id"] = routing.get("selectedProfileId")
    context["brain_routing_fallback_used"] = bool(
        selected and selected.get("provider") != primary.get("provider")
    )
    return context, routing


class AIConnection:
    def embed_text(
        self,
        text: str,
        *,
        ai_context: Dict[str, Any] | None = None,
    ) -> list[float]:
        """Create an embedding through the same router, health, and audit boundary."""
        embed_model = os.environ.get("REALYTICS_EMBED_MODEL", "text-embedding-3-small")
        requested = {**(ai_context or {}), "model": embed_model}
        context, routing = _route_execution_context(
            requested,
            task_type="embedding_retrieval",
            context_token_estimate=max(1, len(text) // 3),
            estimated_output_tokens=0,
            include_fallbacks=False,
        )
        context = {
            **context,
            "task_type": "embedding_retrieval",
            "model": embed_model,
            "brain_profile_id": f"runtime:{context.get('provider')}:{embed_model}",
        }
        started_at = datetime.now(timezone.utc).isoformat()
        if routing.get("blocked"):
            failed = AIExtractionResult(
                data=None,
                meta=safe_ai_context(context),
                error=str(routing.get("blockedReason") or "Central Brain Router blocked embedding."),
            )
            self._audit_extract_json(text, "Embedding retrieval", context, failed, started_at)
            raise RuntimeError(failed.error)
        if context.get("provider") not in OPENAI_COMPATIBLE_PROVIDERS:
            failed = AIExtractionResult(
                data=None,
                meta=safe_ai_context(context),
                error=f"Embedding provider is not OpenAI-compatible: {context.get('provider')}",
            )
            self._audit_extract_json(text, "Embedding retrieval", context, failed, started_at)
            raise RuntimeError(failed.error)
        api_key_env = context.get("api_key_env")
        api_key = context.get("api_key") or (os.environ.get(api_key_env) if api_key_env else None)
        if not context.get("is_local") and not api_key:
            failed = AIExtractionResult(
                data=None,
                meta=safe_ai_context(context),
                error=f"{api_key_env or 'API key'} not configured for embedding provider.",
            )
            self._audit_extract_json(text, "Embedding retrieval", context, failed, started_at)
            raise RuntimeError(failed.error)
        try:
            from openai import OpenAI

            kwargs: Dict[str, Any] = {"api_key": api_key or "not-needed"}
            if context.get("base_url"):
                kwargs["base_url"] = context["base_url"]
            context["provider_call_attempted"] = True
            response = OpenAI(**kwargs).embeddings.create(model=embed_model, input=text)
            vector = list(response.data[0].embedding)
            completed = AIExtractionResult(
                data={"embeddingDimensions": len(vector)},
                meta={
                    **safe_ai_context(context),
                    "parsing_runtime": f"embedding:{context.get('provider')}",
                    "token_usage": _normalize_token_usage(
                        getattr(response, "usage", None),
                        provider="openai_compatible_embedding",
                    ),
                },
            )
            self._audit_extract_json(text, "Embedding retrieval", context, completed, started_at)
            return vector
        except Exception as exc:
            safe_error = _safe_provider_error(exc, context, api_key)
            failed = AIExtractionResult(data=None, meta=safe_ai_context(context), error=safe_error)
            self._audit_extract_json(text, "Embedding retrieval", context, failed, started_at)
            # Do not retain the raw SDK exception as ``__cause__``: traceback
            # formatters may print it even though the public message is safe.
            raise RuntimeError(safe_error) from None

    def extract_json(
        self,
        prompt: str,
        *,
        system: str,
        ai_context: Dict[str, Any] | None = None,
    ) -> AIExtractionResult:
        started_at = datetime.now(timezone.utc).isoformat()
        context, routing = _route_execution_context(
            ai_context,
            task_type=str((ai_context or {}).get("task_type") or (ai_context or {}).get("taskType") or "structured_extraction"),
            context_token_estimate=max(1, (len(prompt) + len(system)) // 3),
        )
        if routing.get("blocked"):
            result = AIExtractionResult(
                data=None,
                meta=safe_ai_context(context),
                error=str(routing.get("blockedReason") or "Central Brain Router blocked this call."),
            )
            self._audit_extract_json(prompt, system, context, result, started_at)
            return result
        result = self._extract_for_context(prompt, system, context)
        self._audit_extract_json(prompt, system, context, result, started_at)
        if result.data is not None or not context.get("provider_call_attempted"):
            return result

        failed_provider = str(context.get("provider") or "")
        prohibited = sorted(set([
            *((ai_context or {}).get("prohibited_providers") or []),
            failed_provider,
        ]) - {""})
        fallback_input = {
            **(ai_context or {}),
            "prohibited_providers": prohibited,
            "task_type": str((ai_context or {}).get("task_type") or "structured_extraction"),
        }
        fallback_context, fallback_routing = _route_execution_context(
            fallback_input,
            task_type=fallback_input["task_type"],
            context_token_estimate=max(1, (len(prompt) + len(system)) // 3),
        )
        if fallback_routing.get("blocked") or fallback_context.get("provider") == failed_provider:
            return result
        fallback_started_at = datetime.now(timezone.utc).isoformat()
        fallback_result = self._extract_for_context(prompt, system, fallback_context)
        fallback_result.meta.update({
            "fallbackFromProvider": failed_provider,
            "fallbackReason": result.error,
        })
        self._audit_extract_json(
            prompt,
            system,
            fallback_context,
            fallback_result,
            fallback_started_at,
        )
        return fallback_result

    def _extract_for_context(
        self,
        prompt: str,
        system: str,
        context: Dict[str, Any],
    ) -> AIExtractionResult:
        provider = context["provider"]
        if provider == "anthropic":
            return self._extract_with_anthropic(prompt, system, context)
        if provider == "claude_desktop":
            return AIExtractionResult(
                data=None,
                meta=safe_ai_context(context),
                error=(
                    "Claude Desktop is the MCP host LLM. The server cannot call the "
                    "Claude Desktop UI model internally; return a host_llm_extraction request instead."
                ),
            )
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            return self._extract_with_openai_compatible(prompt, system, context)
        return AIExtractionResult(
            data=None,
            meta=safe_ai_context(context),
            error=f"Unsupported AI provider: {provider}",
        )

    def _audit_extract_json(
        self,
        prompt: str,
        system: str,
        context: Dict[str, Any],
        result: AIExtractionResult,
        started_at: str,
    ) -> None:
        status = "success" if result.data is not None else "failed"
        if result.error and not context.get("direct_model_call"):
            status = "fallback_used"
        event = build_ai_brain_used_audit_event(
            context={**context, **(result.meta or {})},
            prompt=prompt,
            system=system,
            status=status,
            error=_safe_provider_error(result.error, context) if result.error else None,
            token_usage=(result.meta or {}).get("token_usage"),
            started_at=started_at,
        )
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        latency_ms = max(0, int((datetime.now(timezone.utc) - started).total_seconds() * 1000))
        event.setdefault("metadata", {})["latencyMs"] = latency_ms
        stored = record_ai_brain_used(event)
        if context.get("provider_call_attempted"):
            record_brain_provider_result(
                context,
                success=result.data is not None,
                latency_ms=latency_ms,
                error=_safe_provider_error(result.error, context) if result.error else None,
            )
        if stored and isinstance(result.meta, dict):
            result.meta["ai_brain_used_event_id"] = stored.get("eventId")

    def _extract_with_openai_compatible(
        self,
        prompt: str,
        system: str,
        context: Dict[str, Any],
    ) -> AIExtractionResult:
        """Call any OpenAI Chat Completions-compatible endpoint via base_url.

        Covers OpenAI plus Gemini (OpenAI-compat), Groq, OpenRouter, DeepSeek,
        Together, and self-hosted Ollama/vLLM. Sensitive deployments should
        prefer a local base_url so PF data never leaves the network.
        """
        api_key_env = context.get("api_key_env")
        api_key = context.get("api_key") or (os.environ.get(api_key_env) if api_key_env else None)
        base_url = context.get("base_url")
        is_local = context.get("is_local")

        if is_local and not base_url:
            return AIExtractionResult(
                data=None,
                meta=safe_ai_context(context),
                error="REALYTICS_LLM_BASE_URL not configured for the local/self-hosted model.",
            )
        if not is_local and not api_key:
            return AIExtractionResult(
                data=None,
                meta=safe_ai_context(context),
                error=f"{api_key_env or 'API key'} not configured for provider '{context['provider']}'.",
            )

        try:
            from openai import OpenAI

            client_kwargs: Dict[str, Any] = {"api_key": api_key or "not-needed"}
            if base_url:
                client_kwargs["base_url"] = base_url
            client = OpenAI(**client_kwargs)

            request_kwargs: Dict[str, Any] = {
                "model": context["model"],
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
            }
            if context.get("supports_json_mode"):
                request_kwargs["response_format"] = {"type": "json_object"}

            context["provider_call_attempted"] = True
            response = client.chat.completions.create(**request_kwargs)
            content = response.choices[0].message.content or "{}"
            return AIExtractionResult(
                data=_load_json_object(content),
                meta={
                    **safe_ai_context(context),
                    "parsing_runtime": f"openai_compatible:{context['provider']}",
                    "token_usage": _normalize_token_usage(getattr(response, "usage", None), provider="openai_compatible"),
                },
            )
        except Exception as exc:
            return AIExtractionResult(
                data=None,
                meta=safe_ai_context(context),
                error=_safe_provider_error(exc, context, api_key),
            )

    def _extract_with_anthropic(
        self,
        prompt: str,
        system: str,
        context: Dict[str, Any],
    ) -> AIExtractionResult:
        api_key = context.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return AIExtractionResult(
                data=None,
                meta=safe_ai_context(context),
                error=(
                    "ANTHROPIC_API_KEY not configured. Claude Desktop can host MCP tool calls, "
                    "but the MCP server cannot call the Claude Desktop UI model internally."
                ),
            )

        try:
            from anthropic import Anthropic

            client = Anthropic(api_key=api_key)
            context["provider_call_attempted"] = True
            response = client.messages.create(
                model=context["model"],
                max_tokens=4096,
                temperature=0.0,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            content = "".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            )
            return AIExtractionResult(
                data=_load_json_object(content),
                meta={
                    **safe_ai_context(context),
                    "parsing_runtime": "anthropic.messages",
                    "token_usage": _normalize_token_usage(getattr(response, "usage", None), provider="anthropic"),
                },
            )
        except Exception as exc:
            return AIExtractionResult(
                data=None,
                meta=safe_ai_context(context),
                error=_safe_provider_error(exc, context, api_key),
            )


def _usage_value(usage: Any, *names: str) -> int | None:
    for name in names:
        value = None
        if isinstance(usage, dict):
            value = usage.get(name)
        else:
            value = getattr(usage, name, None)
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _normalize_token_usage(usage: Any, *, provider: str) -> Dict[str, Any]:
    """Normalize provider-specific token usage into one audit-safe shape."""
    if not usage:
        return {}
    prompt_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
    completion_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
    total_tokens = _usage_value(usage, "total_tokens")
    if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
    result = {
        "provider": provider,
        "promptTokens": prompt_tokens,
        "completionTokens": completion_tokens,
        "totalTokens": total_tokens,
    }
    return {key: value for key, value in result.items() if value is not None}


def _load_json_object(content: str) -> Dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return json.loads(cleaned)
