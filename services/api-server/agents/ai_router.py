from __future__ import annotations

"""Automatic AI provider selection.

Picks an appropriate, *available* LLM for a task without the caller hard-coding a
provider. Order is residency-aware (prefer a self-hosted/local endpoint for
sensitive PF data), then capable cloud models, then any configured fallback.
Returns a resolved ai_context, or None when nothing is configured — callers then
fall back to deterministic behaviour. No secrets are returned in the summary.
"""

import os
from typing import Any, Dict, List, Optional

from agents.ai_connection import resolve_ai_context, safe_ai_context

# Default residency-aware preference order. Local/self-hosted first (PF data stays
# on-prem), then strong general models, then remaining configured providers.
_DEFAULT_ORDER: List[str] = [
    "local",
    "anthropic",
    "openai",
    "gemini",
    "groq",
    "openrouter",
    "deepseek",
    "together",
]

_USABLE_RUNTIMES = {"anthropic", "openai", "openai_compatible"}


def _is_usable(ctx: Dict[str, Any]) -> bool:
    return (
        ctx.get("requires_api_key") is None
        and ctx.get("runtime_provider") in _USABLE_RUNTIMES
        and bool(ctx.get("model"))
    )


def select_ai_context(
    *,
    preferred: Optional[str] = None,
    tier: str = "coordination",
) -> Optional[Dict[str, Any]]:
    """Return a usable resolved ai_context, or None if no provider is configured.

    Precedence: explicit `preferred` -> REALYTICS_AI_PROVIDER env -> residency-aware
    default order. `tier` is accepted for future per-step routing (sensitive /
    cheap / high-stakes); today it only annotates the selection.
    """
    order: List[str] = []
    if preferred:
        order.append(preferred)
    env_provider = os.environ.get("REALYTICS_AI_PROVIDER")
    if env_provider:
        order.append(env_provider)
    order.extend(_DEFAULT_ORDER)

    seen = set()
    for provider in order:
        key = str(provider).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        ctx = resolve_ai_context({"provider": key})
        if _is_usable(ctx):
            ctx = {**ctx, "selectedTier": tier, "autoSelected": True}
            return ctx
    return None


def describe_selection(ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Secret-safe summary of the selected AI (for logs / artifacts)."""
    if not ctx:
        return {"selected": False, "reason": "no AI provider configured; using deterministic fallback"}
    safe = safe_ai_context(ctx)
    return {
        "selected": True,
        "provider": safe.get("provider"),
        "runtimeProvider": safe.get("runtime_provider"),
        "model": safe.get("model"),
        "isLocal": safe.get("is_local", False),
        "tier": safe.get("selectedTier"),
    }
