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
            project_data=project_data or {},
            options=options,
            evidence_ids=evidence_ids,
            source_locators=source_locators,
        )
        document_facts = deterministic.get("documentFacts") if isinstance(deterministic.get("documentFacts"), dict) else {}
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
            "documentFacts": document_facts,
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
        project_data: Dict[str, Any],
        options: Dict[str, Any],
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
        document_facts: Dict[str, Any] = {}
        if pod_key == "market_product":
            document_facts = _document_market_facts(source_artifact, project_data)
            findings.extend(_market_product_findings(source_artifact, source_artifact_key, project_data, options))
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
                "payload": {"findingCount": len(findings), "sourceArtifactKey": source_artifact_key, "documentFacts": document_facts},
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
            "documentFacts": document_facts,
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


def _market_product_findings(
    source_artifact: Dict[str, Any],
    source_artifact_key: str | None,
    project_data: Dict[str, Any],
    options: Dict[str, Any],
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    marketability = _first_dict_value(source_artifact, {"marketabilityAnalysis", "marketability"})
    pricing = _first_dict_value(marketability, {"pricing"}) if marketability else {}
    comparable_count = _count_market_comparables(source_artifact)
    proposed_price = _first_numeric_value(
        source_artifact,
        {
            "proposedPricePerPy",
            "proposed_price_per_py",
            "weightedAveragePricePerPy",
            "pricePerPy",
            "price_per_py",
        },
    )
    proposed_basis = ""
    if isinstance(pricing, dict):
        proposed_basis = str(pricing.get("proposedPriceBasis") or pricing.get("proposed_price_basis") or "")
    risk_flags = set(_collect_values(source_artifact, {"riskFlags", "risk_flags", "code"}))
    missing_required = set(_collect_values(source_artifact, {"missingRequiredData", "missing_required_data"}))
    document_facts = _document_market_facts(source_artifact, project_data)
    reconciliation = _first_dict_value(source_artifact, {"documentMarketReconciliation"})
    reconciliation_status = str(reconciliation.get("status") or document_facts.get("reconciliationStatus") or "").lower()
    reconciliation_issues = reconciliation.get("issues") if isinstance(reconciliation.get("issues"), list) else []

    if document_facts.get("hasDocumentMarketEvidence") and not reconciliation:
        findings.append(_finding(
            "document_market_reconciliation_missing",
            "medium",
            "Uploaded business plan / IM market facts are visible, but the pod artifact does not include a reconciliation result.",
            recommended_action="Run Market Evidence Agent reconciliation before treating parsed sale price, product mix, or rent assumptions as report-grade inputs.",
        ))
    elif document_facts.get("hasDocumentMarketEvidence") and (
        reconciliation_issues
        or reconciliation_status in {"needs_review", "needs_evidence", "blocking", "blocked"}
    ):
        findings.append(_finding(
            "document_market_reconciliation_needs_review",
            "high",
            "Uploaded business plan / IM market facts require reconciliation before marketability scoring and feasibility handoff.",
            recommended_action="Review pricing checks, rent checks, and evidence records; approve a baseline assumption or attach corrected market evidence with source locators.",
        ))

    if comparable_count == 0 or "NO_COMPARABLE_DATA" in risk_flags:
        findings.append(_finding(
            "no_comparable_data",
            "high",
            "Market-Product Pod has no usable transaction, presale, listing, or manual comparable evidence. Price adequacy and absorption should not be treated as confirmed.",
            recommended_action="Attach source-dated transaction/presale/listing comparables or run the market evidence connector before scoring marketability.",
        ))
    elif comparable_count < 3:
        findings.append(_finding(
            "thin_comparable_data",
            "medium",
            f"Only {comparable_count} comparable case(s) are visible. This is enough for a preliminary view, but not enough for report-grade price judgement.",
            recommended_action="Add at least three relevant comparables with source metadata, base date, product type, area, price, and distance.",
        ))

    pricing_judgement = str(pricing.get("judgement") or "") if isinstance(pricing, dict) else ""
    proposed_basis_status = str(pricing.get("proposedPriceBasisStatus") or "") if isinstance(pricing, dict) else ""
    if (
        proposed_price in (None, 0)
        or not proposed_basis
        or proposed_basis_status == "missing"
        or pricing_judgement == "needs_proposed_price_basis"
        or "PROPOSED_PRICE_BASIS_MISSING" in risk_flags
        or "proposed_price_per_py_basis" in missing_required
    ):
        findings.append(_finding(
            "proposed_price_basis_missing",
            "high",
            "Comparable evidence may exist, but the proposed sale price per py basis is missing or not traceable to product mix, total revenue, saleable area, or an explicit pricing assumption.",
            recommended_action="Derive proposed price per py from product mix/revenue/area, or attach an explicit pricing assumption with evidence ID and source locator.",
        ))

    if _market_income_review_required(project_data, source_artifact, options) and not _has_income_or_cap_rate_evidence(source_artifact):
        findings.append(_finding(
            "rent_cap_rate_evidence_missing",
            "high",
            "This track or asset type needs rent, NOI, or Cap Rate evidence, but the Market-Product Pod artifact does not show a source-backed income basis.",
            recommended_action="Attach rent roll, lease comparable, NOI bridge, appraisal, or Cap Rate comparable before using this for real-asset finance, REIT, fund, or refinancing review.",
        ))

    data_status = str((marketability or {}).get("dataStatus") or "").lower() if isinstance(marketability, dict) else ""
    score = (marketability or {}).get("score") if isinstance(marketability, dict) else None
    checks = (marketability or {}).get("checks") if isinstance(marketability, dict) else None
    report_paragraphs = (marketability or {}).get("reportParagraphs") if isinstance(marketability, dict) else None
    if (
        not marketability
        or data_status in {"needs_comparable_data", "insufficient", "not_applicable"}
        or score in (None, "")
        or not checks
        or not report_paragraphs
    ):
        findings.append(_finding(
            "marketability_judgement_basis_weak",
            "medium",
            "Marketability judgement is preliminary because score, checks, report paragraphs, or data-status support is incomplete.",
            recommended_action="Run Marketability Analysis after market comparables, product mix, proposed price basis, demand/supply assumptions, and source metadata are attached.",
        ))

    if source_artifact_key:
        for item in findings:
            item["sourceLocators"].append({"sourceType": "chief_pod_artifact", "artifactKey": source_artifact_key})
    return findings


def _document_market_facts(source_artifact: Dict[str, Any], project_data: Dict[str, Any]) -> Dict[str, Any]:
    facts = dict(_first_dict_value(source_artifact, {"documentParsedMarketFacts"}))
    document_evidence = _document_market_evidence(project_data)
    if not facts and document_evidence:
        facts = {
            "schemaVersion": "realytics-document-market-facts-v1",
            "hasDocumentMarketEvidence": True,
            "sourceDocument": document_evidence.get("sourceDocument") if isinstance(document_evidence.get("sourceDocument"), dict) else {},
            "parserVersion": document_evidence.get("parserVersion"),
            "productMixCount": _list_count(document_evidence.get("productMix") or document_evidence.get("product_mix")),
            "saleComparableCount": sum(
                _list_count(document_evidence.get(key))
                for key in (
                    "comparables",
                    "manualComparables",
                    "manual_comparables",
                    "transactionComparables",
                    "transaction_comparables",
                    "presaleComparables",
                    "presale_comparables",
                    "listingComparables",
                    "listing_comparables",
                )
            ),
            "rentComparableCount": _list_count(document_evidence.get("rentComparables") or document_evidence.get("rent_comparables")),
            "proposedPricePerPy": document_evidence.get("proposedPricePerPy") or document_evidence.get("proposed_price_per_py"),
            "demandLevel": document_evidence.get("demandLevel") or document_evidence.get("demand_level"),
            "upcomingSupplyUnits": document_evidence.get("upcomingSupplyUnits") or document_evidence.get("upcoming_supply_units"),
        }
    if not facts:
        return {}
    reconciliation = _first_dict_value(source_artifact, {"documentMarketReconciliation"})
    if reconciliation:
        facts.setdefault("reconciliationStatus", reconciliation.get("status"))
        facts.setdefault("issueCount", len(reconciliation.get("issues") or []))
        summary = reconciliation.get("evidenceSummary") if isinstance(reconciliation.get("evidenceSummary"), dict) else {}
        for key in ("evidenceRecordCount", "sourceLocatorCount", "fieldLocatorCoverage"):
            if key in summary:
                facts.setdefault(key, summary.get(key))
    facts.setdefault("factCount", _document_fact_count(facts))
    facts.setdefault("hasDocumentMarketEvidence", True)
    return facts


def _document_market_evidence(project_data: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("document_market_evidence", "documentMarketEvidence", "market_evidence", "marketEvidence"):
        value = project_data.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _document_fact_count(facts: Dict[str, Any]) -> int:
    total = 0
    for key in ("productMixCount", "saleComparableCount", "rentComparableCount"):
        try:
            total += int(facts.get(key) or 0)
        except (TypeError, ValueError):
            pass
    for key in ("proposedPricePerPy", "demandLevel", "upcomingSupplyUnits"):
        if facts.get(key) not in (None, ""):
            total += 1
    return total


def _first_dict_value(value: Any, keys: set[str]) -> Dict[str, Any]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, dict):
                return item
        for item in value.values():
            found = _first_dict_value(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _first_dict_value(item, keys)
            if found:
                return found
    return {}


def _count_market_comparables(value: Any) -> int:
    comparable_keys = {
        "comparables",
        "normalizedComparables",
        "normalized_comparables",
        "transactionComparables",
        "transaction_comparables",
        "manualComparables",
        "manual_comparables",
        "presaleComparables",
        "presale_comparables",
        "listingComparables",
        "listing_comparables",
        "rentComparables",
        "rent_comparables",
        "liveMarketComparables",
        "live_market_comparables",
    }
    total = 0
    if isinstance(value, dict):
        for key, item in value.items():
            if key in comparable_keys:
                if isinstance(item, list):
                    total += len([row for row in item if row])
                elif isinstance(item, dict):
                    rows = item.get("rows") or item.get("records") or item.get("items") or item.get("comparables")
                    if isinstance(rows, list):
                        total += len([row for row in rows if row])
            total += _count_market_comparables(item)
    elif isinstance(value, list):
        for item in value:
            total += _count_market_comparables(item)
    return total


def _first_numeric_value(value: Any, keys: set[str]) -> float | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys:
                parsed = _to_float(item)
                if parsed is not None:
                    return parsed
        for item in value.values():
            parsed = _first_numeric_value(item, keys)
            if parsed is not None:
                return parsed
    elif isinstance(value, list):
        for item in value:
            parsed = _first_numeric_value(item, keys)
            if parsed is not None:
                return parsed
    return None


def _market_income_review_required(project_data: Dict[str, Any], source_artifact: Dict[str, Any], options: Dict[str, Any]) -> bool:
    workflow_track = str(options.get("workflowTrack") or options.get("workflow_track") or "").lower()
    project_type = str((project_data.get("project") or {}).get("type") or project_data.get("projectType") or "").lower()
    text = json.dumps({"projectType": project_type, "artifact": source_artifact}, ensure_ascii=False, default=str).lower()
    if workflow_track in {"real_asset_finance", "reit", "fund", "finance"}:
        return True
    income_keywords = {
        "logistics",
        "office",
        "retail",
        "commercial",
        "reit",
        "fund",
        "rental",
        "lease",
        "refinancing",
        "cap rate",
        "cap_rate",
        "noi",
    }
    return any(keyword in text for keyword in income_keywords)


def _has_income_or_cap_rate_evidence(value: Any) -> bool:
    income_keys = {
        "capRate",
        "cap_rate",
        "noi",
        "netOperatingIncome",
        "net_operating_income",
        "rent",
        "rentPerPy",
        "rent_per_py",
        "rentPerPyeong",
        "rent_per_pyeong",
        "monthlyRent",
        "monthly_rent",
        "annualRent",
        "annual_rent",
        "leaseIncome",
        "lease_income",
    }
    return _first_numeric_value(value, income_keys) not in (None, 0)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
