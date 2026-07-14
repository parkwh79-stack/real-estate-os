from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable
from uuid import uuid4

SECRET_KEYS = {"api_key", "apikey", "token", "secret", "password"}
logger = logging.getLogger(__name__)


def _safe_context(context: Dict[str, Any] | None) -> Dict[str, Any]:
    safe: Dict[str, Any] = {}
    for key, value in (context or {}).items():
        if any(secret in key.lower() for secret in SECRET_KEYS):
            safe[key] = "[redacted]"
        elif isinstance(value, dict):
            safe[key] = _safe_context(value)
        else:
            safe[key] = value
    return safe


def brain_source_from_context(context: Dict[str, Any] | None) -> str:
    source = (context or {}).get("selection_source")
    if source == "explicit_request_override":
        return "request_override"
    if source == "user_ai_profile":
        return "user_profile"
    if source in {"system_free_fallback", "user_ai_profile_without_key_system_fallback"}:
        return "system_fallback"
    if source == "tenant_policy":
        return "tenant_policy"
    if not (context or {}).get("direct_model_call"):
        return "deterministic_fallback"
    return "request_override" if (context or {}).get("api_key") else "system_fallback"


def key_mode_from_context(context: Dict[str, Any] | None) -> str:
    if (context or {}).get("api_key"):
        return "byok"
    if (context or {}).get("direct_model_call"):
        return "system_key"
    return "no_key"


def prompt_hash(prompt: str, system: str = "") -> str:
    digest = hashlib.sha256()
    digest.update((system or "").encode("utf-8", errors="ignore"))
    digest.update(b"\n---prompt---\n")
    digest.update((prompt or "").encode("utf-8", errors="ignore"))
    return digest.hexdigest()


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def build_ai_brain_used_audit_event(
    *,
    context: Dict[str, Any],
    prompt: str,
    system: str,
    agent_id: str | None = None,
    project_id: str | None = None,
    deal_id: str | None = None,
    run_id: str | None = None,
    status: str = "success",
    error: str | None = None,
    token_usage: Dict[str, Any] | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    input_evidence_ids: Iterable[str] | None = None,
    output_finding_ids: Iterable[str] | None = None,
) -> Dict[str, Any]:
    safe_context = _safe_context(context)
    completed = completed_at or datetime.now(timezone.utc).isoformat()
    started = started_at or completed
    effective_deal_id = str(deal_id or project_id or context.get("deal_id") or context.get("project_id") or "")
    metadata = {
        "schemaVersion": "realytics-ai-brain-used-audit-v1",
        "dealId": effective_deal_id or None,
        "agentId": agent_id or context.get("agent_id") or "AIConnection",
        "tenantId": context.get("tenant_id") or context.get("tenantId"),
        "provider": safe_context.get("provider"),
        "runtimeProvider": safe_context.get("runtime_provider"),
        "model": safe_context.get("model"),
        "brainSource": brain_source_from_context(context),
        "keyMode": key_mode_from_context(context),
        "promptHash": prompt_hash(prompt, system),
        "inputEvidenceIds": list(input_evidence_ids or _list(context.get("inputEvidenceIds") or context.get("input_evidence_ids"))),
        "outputFindingIds": list(output_finding_ids or _list(context.get("outputFindingIds") or context.get("output_finding_ids"))),
        "tokenUsage": token_usage or {},
        "startedAt": started,
        "completedAt": completed,
        "status": status,
        "error": error,
        "aiContext": safe_context,
    }
    return {
        "eventType": "ai_brain_used",
        "createdAt": completed,
        "userId": context.get("user_id") or context.get("userId") or "anonymous",
        "projectId": str(project_id or context.get("project_id") or effective_deal_id or "") or None,
        "runId": run_id or context.get("run_id") or context.get("runId"),
        "metadata": metadata,
    }


def record_ai_brain_used(event: Dict[str, Any]) -> Dict[str, Any] | None:
    event = dict(event)
    event.setdefault("eventId", str(uuid4()))
    stored: Dict[str, Any] | None = None
    failures: list[str] = []
    try:
        from storage import record_user_activity_log

        stored = record_user_activity_log(event)
    except Exception as exc:
        failures.append(f"activity_log:{exc}")
    try:
        from storage import upsert_brain_usage_event_record

        metadata = dict(event.get("metadata") or {})
        usage = dict(metadata.get("tokenUsage") or {})
        status = str(metadata.get("status") or "failed")
        upsert_brain_usage_event_record({
            "schemaVersion": "realytics-brain-usage-event-v1",
            "eventId": f"brain-usage:{event['eventId']}",
            "runId": event.get("runId") or f"direct-ai:{event['eventId']}",
            "stepId": None,
            "tenantId": metadata.get("tenantId") or "default",
            "projectId": event.get("projectId"),
            "agentId": metadata.get("agentId") or "AIConnection",
            "agentVersion": metadata.get("agentVersion") or "legacy-ai-connection-v1",
            "brainProfileId": metadata.get("aiContext", {}).get("brain_profile_id"),
            "provider": metadata.get("provider"),
            "model": metadata.get("model"),
            "promptVersion": metadata.get("promptVersion"),
            "status": "completed" if status == "success" else (
                "fallback" if status == "fallback_used" else "failed"
            ),
            "inputTokens": usage.get("promptTokens") or usage.get("inputTokens"),
            "outputTokens": usage.get("completionTokens") or usage.get("outputTokens"),
            "estimatedCostUsd": usage.get("estimatedCostUsd"),
            "latencyMs": metadata.get("latencyMs"),
            "fallbackReason": metadata.get("error") if status != "success" else None,
            "occurredAt": event.get("createdAt"),
            "metadata": {
                "routingEventId": metadata.get("aiContext", {}).get("brain_routing_event_id"),
                "brainSource": metadata.get("brainSource"),
                "keyMode": metadata.get("keyMode"),
                "promptHash": metadata.get("promptHash"),
                "promptPersisted": False,
                "responsePersisted": False,
                "credentialsPersisted": False,
            },
        })
    except Exception as exc:
        failures.append(f"brain_usage:{exc}")
    if failures:
        logger.error("AI audit persistence failed: %s", "; ".join(failures))
        if os.environ.get("REALYTICS_AUDIT_FAIL_CLOSED", "false").lower() in {"1", "true", "yes"}:
            raise RuntimeError("AI_AUDIT_PERSISTENCE_FAILED: " + "; ".join(failures))
    return stored or event
