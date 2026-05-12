import json
import os
from dataclasses import dataclass
from typing import Any, Dict


CLAUDE_ALIASES = {"anthropic", "claude", "claude_api", "claude-desktop", "claude_desktop"}
OPENAI_ALIASES = {"openai", "chatgpt", "gpt"}
SECRET_KEYS = {"api_key", "apikey", "token", "secret", "password"}


@dataclass
class AIExtractionResult:
    data: Dict[str, Any] | None
    meta: Dict[str, Any]
    error: str | None = None


def safe_ai_context(context: Dict[str, Any] | None) -> Dict[str, Any]:
    safe: Dict[str, Any] = {}
    for key, value in (context or {}).items():
        if any(secret in key.lower() for secret in SECRET_KEYS):
            safe[key] = "[redacted]"
        elif isinstance(value, dict):
            safe[key] = safe_ai_context(value)
        else:
            safe[key] = value
    return safe


def resolve_ai_context(
    ai_context: Dict[str, Any] | None = None,
    *,
    user_id: str | None = None,
    host: str | None = None,
) -> Dict[str, Any]:
    requested = dict(ai_context or {})
    provider = (
        requested.get("provider")
        or requested.get("ai_provider")
        or os.environ.get("REALYTICS_AI_PROVIDER")
        or "openai"
    )
    provider = str(provider).strip().lower()
    if provider in OPENAI_ALIASES:
        provider = "openai"
    elif provider in {"claude-desktop", "claude_desktop"}:
        provider = "claude_desktop"
    elif provider in CLAUDE_ALIASES:
        provider = "anthropic"

    resolved_user_id = (
        user_id
        or requested.get("user_id")
        or os.environ.get("REALYTICS_USER_ID")
        or os.environ.get("MCP_USER_ID")
        or "anonymous"
    )
    resolved_host = host or requested.get("host") or os.environ.get("REALYTICS_AI_HOST") or "api"

    if provider == "openai":
        model = requested.get("model") or os.environ.get("OPENAI_MODEL") or "gpt-4o"
        direct_model_call = bool(os.environ.get("OPENAI_API_KEY"))
        requires_api_key = None if direct_model_call else "OPENAI_API_KEY"
        runtime_provider = "openai"
    elif provider in {"anthropic", "claude_desktop"}:
        model = requested.get("model") or os.environ.get("ANTHROPIC_MODEL") or "claude-3-5-sonnet-latest"
        direct_model_call = bool(os.environ.get("ANTHROPIC_API_KEY"))
        requires_api_key = None if direct_model_call else "ANTHROPIC_API_KEY"
        runtime_provider = "anthropic" if direct_model_call else "claude_desktop_host"
    else:
        model = requested.get("model") or "unknown"
        direct_model_call = False
        requires_api_key = None
        runtime_provider = "unsupported"

    return {
        "host": resolved_host,
        "provider": provider,
        "runtime_provider": runtime_provider,
        "model": model,
        "user_id": str(resolved_user_id),
        "direct_model_call": direct_model_call,
        "requires_api_key": requires_api_key,
    }


class AIConnection:
    def extract_json(
        self,
        prompt: str,
        *,
        system: str,
        ai_context: Dict[str, Any] | None = None,
    ) -> AIExtractionResult:
        context = resolve_ai_context(ai_context)
        provider = context["provider"]

        if provider == "openai":
            return self._extract_with_openai(prompt, system, context)
        if provider in {"anthropic", "claude_desktop"}:
            return self._extract_with_anthropic(prompt, system, context)

        return AIExtractionResult(
            data=None,
            meta=context,
            error=f"Unsupported AI provider: {provider}",
        )

    def _extract_with_openai(
        self,
        prompt: str,
        system: str,
        context: Dict[str, Any],
    ) -> AIExtractionResult:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return AIExtractionResult(
                data=None,
                meta=context,
                error="OPENAI_API_KEY not configured",
            )

        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=context["model"],
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            return AIExtractionResult(
                data=_load_json_object(content),
                meta={**context, "parsing_runtime": "openai.chat.completions"},
            )
        except Exception as exc:
            return AIExtractionResult(data=None, meta=context, error=str(exc))

    def _extract_with_anthropic(
        self,
        prompt: str,
        system: str,
        context: Dict[str, Any],
    ) -> AIExtractionResult:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return AIExtractionResult(
                data=None,
                meta=context,
                error=(
                    "ANTHROPIC_API_KEY not configured. Claude Desktop can host MCP tool calls, "
                    "but the MCP server cannot call the Claude Desktop UI model internally."
                ),
            )

        try:
            from anthropic import Anthropic

            client = Anthropic(api_key=api_key)
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
                meta={**context, "parsing_runtime": "anthropic.messages"},
            )
        except Exception as exc:
            return AIExtractionResult(data=None, meta=context, error=str(exc))


def _load_json_object(content: str) -> Dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return json.loads(cleaned)
