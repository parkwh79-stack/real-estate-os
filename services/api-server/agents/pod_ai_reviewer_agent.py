from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from uuid import uuid4

from agents.ai_connection import AIConnection, resolve_ai_context, safe_ai_context


POD_AI_REVIEW_GUARDRAILS = [
    "LLM is an evidence-aware reviewer, not a deterministic calculator.",
    "Do not create PNU, official land price, registry status, market comparables, or PF metrics without source evidence.",
    "Do not waive blocking risks or mark legal/PF readiness complete without evidence or human approval.",
    "Financial calculations must come from deterministic engines and calculationRun records.",
]


class PodAIReviewerAgent:
    """Review a Chief Pod artifact without replacing deterministic engines.

    The agent attempts an LLM evidence-aware review when an AI provider is
    configured. If no model call is available, it returns a clearly marked
    deterministic fallback review so the UI and audit trail never confuse a
    local guardrail check with an AI opinion.
    """

    def __init__(self, ai: AIConnection | None = None):
        self.ai = ai or AIConnection()

    def review_pod(
        self,
        *,
        project_id: str,
        pod_key: str,
        source_artifact: Dict[str, Any],
        source_artifact_key: str | None = None,
        project_data: Dict[str, Any] | None = None,
        options: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        options = options or {}
        now = datetime.now(timezone.utc).isoformat()
        agent_id = _agent_id_for_pod(pod_key)
        evidence_ids = _collect_values(source_artifact, {"evidenceId", "evidence_id"})
        assumption_ids = _collect_values(source_artifact, {"assumptionId", "assumption_id"})
        source_locators = _collect_source_locators(source_artifact)
        ai_context = _review_ai_context(options, project_id=project_id, agent_id=agent_id, evidence_ids=evidence_ids)
        if str(options.get("podAiReviewMode") or options.get("pod_ai_review_mode") or "").lower() in {"deterministic", "deterministic_only"}:
            ai_result = {
                "llmReviewAccepted": False,
                "review": None,
                "aiBrainUsed": {**safe_ai_context(ai_context), "mode": "deterministic_test_or_manual_review"},
                "aiBrainUsedEventId": None,
                "llmError": "LLM review skipped by podAiReviewMode=deterministic.",
            }
        else:
            ai_result = self._try_llm_review(
                pod_key=pod_key,
                source_artifact=source_artifact,
                source_artifact_key=source_artifact_key,
                ai_context=ai_context,
            )

        review_mode = "llm_evidence_aware_review" if ai_result.get("llmReviewAccepted") else "deterministic_fallback_review"
        review_payload = ai_result.get("review") if isinstance(ai_result.get("review"), dict) else {}
        deterministic = self._deterministic_review(
            pod_key=pod_key,
            source_artifact=source_artifact,
            source_artifact_key=source_artifact_key,
            evidence_ids=evidence_ids,
            source_locators=source_locators,
        )
        findings = _normalize_findings(review_payload.get("findings")) if review_payload else []
        if not findings:
            findings = deterministic["findings"]
        else:
            findings.extend(deterministic["guardrailFindings"])

        review_status = _status_from_findings(findings)
        confidence = _bounded_float(review_payload.get("confidence"), default=deterministic["confidence"])
        review_id = f"pod-ai-review:{pod_key}:{uuid4()}"
        report_fragments = review_payload.get("reportFragments") if isinstance(review_payload.get("reportFragments"), dict) else deterministic["reportFragments"]

        return {
            "schemaVersion": "realytics-pod-ai-review-v1",
            "reviewId": review_id,
            "projectId": project_id,
            "runId": options.get("runId") or options.get("run_id"),
            "podKey": pod_key,
            "sourceArtifactKey": source_artifact_key,
            "sourceArtifactKeys": [source_artifact_key] if source_artifact_key else [],
            "agentId": agent_id,
            "reviewMode": review_mode,
            "reviewStatus": review_status,
            "confidence": confidence,
            "findings": findings,
            "handoffs": _normalize_handoffs(review_payload.get("handoffs"), agent_id=agent_id) if review_payload else deterministic["handoffs"],
            "reportFragments": report_fragments,
            "inputEvidenceIds": evidence_ids,
            "inputAssumptionIds": assumption_ids,
            "sourceLocators": source_locators[:50],
            "guardrails": POD_AI_REVIEW_GUARDRAILS,
            "aiBrainUsed": safe_ai_context(ai_result.get("aiBrainUsed") or ai_context),
            "aiBrainUsedEventId": ai_result.get("aiBrainUsedEventId"),
            "llmReviewAccepted": ai_result.get("llmReviewAccepted", False),
            "llmError": ai_result.get("llmError"),
            "createdAt": now,
            "updatedAt": now,
        }

    def _try_llm_review(
        self,
        *,
        pod_key: str,
        source_artifact: Dict[str, Any],
        source_artifact_key: str | None,
        ai_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        system = (
            "You are a REALYTICS Pod AI Reviewer. Review deterministic pod artifacts only. "
            "Do not invent facts, source locators, PNU, official land prices, registry conclusions, "
            "market comparables, or financial calculations. Return JSON only."
        )
        prompt = json.dumps(
            {
                "task": "Review this Chief Deal Orchestrator Pod artifact for evidence gaps, contradictions, handoffs, and report-grade limitations.",
                "podKey": pod_key,
                "sourceArtifactKey": source_artifact_key,
                "requiredOutput": {
                    "confidence": "0..1",
                    "findings": [
                        {
                            "category": "missing_evidence|source_locator_gap|data_quality|recalculation_needed|risk_note|handoff|guardrail",
                            "severity": "info|medium|high|blocking",
                            "message": "Korean or English concise finding",
                            "evidenceIds": [],
                            "sourceLocators": [],
                            "recommendedAction": "specific next action",
                        }
                    ],
                    "handoffs": [],
                    "reportFragments": {
                        "riskMemo": "",
                        "missingDataRequest": "",
                        "approvalConditionDraft": "",
                    },
                },
                "guardrails": POD_AI_REVIEW_GUARDRAILS,
                "artifact": _compact_json(source_artifact, max_chars=16000),
            },
            ensure_ascii=False,
        )
        result = self.ai.extract_json(prompt, system=system, ai_context=ai_context)
        if result.data:
            return {
                "llmReviewAccepted": True,
                "review": result.data,
                "aiBrainUsed": result.meta,
                "aiBrainUsedEventId": (result.meta or {}).get("ai_brain_used_event_id"),
            }
        return {
            "llmReviewAccepted": False,
            "review": None,
            "aiBrainUsed": result.meta,
            "aiBrainUsedEventId": (result.meta or {}).get("ai_brain_used_event_id"),
            "llmError": result.error,
        }

    def _deterministic_review(
        self,
        *,
        pod_key: str,
        source_artifact: Dict[str, Any],
        source_artifact_key: str | None,
        evidence_ids: List[str],
        source_locators: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        findings: List[Dict[str, Any]] = []
        if not source_locators:
            findings.append(_finding(
                "source_locator_gap",
                "high",
                "Pod artifact has no explicit source locator. Attach document page, Excel cell, API row, or artifact key before report-grade use.",
                recommended_action="Run Evidence Graph/source locator enrichment or attach locator-backed evidence.",
            ))
        if pod_key == "land_legal":
            findings.extend(_land_legal_findings(source_artifact, source_artifact_key))
        if not evidence_ids:
            findings.append(_finding(
                "missing_evidence",
                "medium",
                "No input evidence IDs were found in the pod artifact. This review can guide next actions but should not be treated as legal/PF-ready evidence.",
                recommended_action="Attach evidence IDs from uploaded documents, VWorld/public API records, or registry/official land price evidence.",
            ))
        if not findings:
            findings.append(_finding(
                "guardrail",
                "info",
                "No immediate deterministic evidence gap was detected, but human review is still required before report-grade use.",
                recommended_action="Reviewer should confirm evidence and calculation provenance.",
            ))
        handoffs = []
        if any(item["severity"] in {"high", "blocking"} for item in findings):
            handoffs.append({
                "handoffId": f"handoff:pod-ai-review:{pod_key}:{uuid4()}",
                "fromAgent": _agent_id_for_pod(pod_key),
                "toAgent": "ChiefDealOrchestrator",
                "handoffType": "review_request",
                "payload": {"findingCount": len(findings), "sourceArtifactKey": source_artifact_key},
                "evidenceIds": evidence_ids,
                "confidence": 0.72,
            })
        return {
            "findings": findings,
            "guardrailFindings": [],
            "handoffs": handoffs,
            "reportFragments": {
                "riskMemo": "Pod AI review used deterministic guardrails because no usable LLM review was available; findings are evidence-gap checks, not final credit conclusions.",
                "missingDataRequest": "; ".join(item.get("recommendedAction") or item["message"] for item in findings[:3]),
                "approvalConditionDraft": "",
            },
            "confidence": 0.68,
        }


def _review_ai_context(options: Dict[str, Any], *, project_id: str, agent_id: str, evidence_ids: List[str]) -> Dict[str, Any]:
    base = dict(options.get("ai_context") or options.get("aiContext") or {})
    base.setdefault("project_id", project_id)
    base.setdefault("agent_id", agent_id)
    base.setdefault("input_evidence_ids", evidence_ids)
    return resolve_ai_context(base)


def _agent_id_for_pod(pod_key: str) -> str:
    return {
        "land_legal": "LandLegalPodAIReviewer",
        "market_product": "MarketProductPodAIReviewer",
        "cost_schedule": "CostSchedulePodAIReviewer",
        "feasibility_cashflow": "FeasibilityCashflowPodAIReviewer",
        "pf_credit": "PFCreditPodAIReviewer",
        "public_policy": "PublicPolicyPodAIReviewer",
    }.get(pod_key, "PodAIReviewerAgent")


def _finding(category: str, severity: str, message: str, *, recommended_action: str | None = None) -> Dict[str, Any]:
    return {
        "findingId": f"finding:{category}:{uuid4()}",
        "category": category,
        "severity": severity,
        "message": message,
        "evidenceIds": [],
        "sourceLocators": [],
        "recommendedAction": recommended_action,
    }


def _land_legal_findings(source_artifact: Dict[str, Any], source_artifact_key: str | None) -> List[Dict[str, Any]]:
    text = json.dumps(source_artifact, ensure_ascii=False, default=str).lower()
    findings: List[Dict[str, Any]] = []
    if "pnu" not in text:
        findings.append(_finding(
            "missing_evidence",
            "blocking",
            "Land-Legal Pod artifact does not show PNU evidence. PNU must be resolved before legal/PF readiness.",
            recommended_action="Run batch PNU enrichment or upload parcel/PNU evidence.",
        ))
    if "official" not in text and "공시지가" not in text:
        findings.append(_finding(
            "missing_evidence",
            "high",
            "Official land price evidence is not visible in the Land-Legal Pod artifact.",
            recommended_action="Attach official land price evidence with base year, base date, source, and fetchedAt.",
        ))
    if "registry" not in text and "등기" not in text and "encumbrance" not in text:
        findings.append(_finding(
            "missing_evidence",
            "high",
            "Registry or encumbrance evidence is not visible in the Land-Legal Pod artifact.",
            recommended_action="Upload registry/encumbrance evidence or create a registry evidence work order.",
        ))
    if source_artifact_key:
        for item in findings:
            item["sourceLocators"].append({"sourceType": "chief_pod_artifact", "artifactKey": source_artifact_key})
    return findings


def _status_from_findings(findings: List[Dict[str, Any]]) -> str:
    severities = {item.get("severity") for item in findings}
    if "blocking" in severities:
        return "blocking"
    if "high" in severities:
        return "needs_human_review"
    if severities.intersection({"medium"}):
        return "warning"
    return "pass"


def _normalize_findings(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    findings: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        finding = {
            "findingId": str(item.get("findingId") or f"finding:llm:{uuid4()}"),
            "category": str(item.get("category") or "risk_note"),
            "severity": str(item.get("severity") or "medium"),
            "message": str(item.get("message") or item.get("finding") or "Review finding"),
            "evidenceIds": _as_str_list(item.get("evidenceIds") or item.get("evidence_ids")),
            "sourceLocators": item.get("sourceLocators") if isinstance(item.get("sourceLocators"), list) else [],
            "recommendedAction": item.get("recommendedAction") or item.get("recommended_action"),
        }
        if finding["severity"] not in {"info", "medium", "high", "blocking"}:
            finding["severity"] = "medium"
        findings.append(finding)
    return findings


def _normalize_handoffs(value: Any, *, agent_id: str) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    handoffs: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        handoffs.append({
            "handoffId": str(item.get("handoffId") or f"handoff:pod-ai-review:{uuid4()}"),
            "fromAgent": str(item.get("fromAgent") or agent_id),
            "toAgent": str(item.get("toAgent") or "ChiefDealOrchestrator"),
            "handoffType": str(item.get("handoffType") or "review_request"),
            "payload": item.get("payload") if isinstance(item.get("payload"), dict) else {},
            "evidenceIds": _as_str_list(item.get("evidenceIds") or item.get("evidence_ids")),
            "confidence": _bounded_float(item.get("confidence"), default=0.7),
        })
    return handoffs


def _collect_values(value: Any, keys: set[str]) -> List[str]:
    found: List[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and item is not None:
                found.extend(_as_str_list(item))
            else:
                found.extend(_collect_values(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_values(item, keys))
    return sorted({item for item in found if item})


def _collect_source_locators(value: Any) -> List[Dict[str, Any]]:
    locators: List[Dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"sourceLocator", "source_locator"} and isinstance(item, dict):
                locators.append(item)
            elif key in {"sourceLocators", "source_locators"} and isinstance(item, list):
                locators.extend([locator for locator in item if isinstance(locator, dict)])
            else:
                locators.extend(_collect_source_locators(item))
    elif isinstance(value, list):
        for item in value:
            locators.extend(_collect_source_locators(item))
    return locators


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _bounded_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _compact_json(value: Any, *, max_chars: int) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"
