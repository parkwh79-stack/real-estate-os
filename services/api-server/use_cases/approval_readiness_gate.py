from __future__ import annotations

"""Deal State based Approval Readiness Gate.

Approval readiness must be judged from the shared Deal State ledger first, not
from scattered orchestration artifacts. Artifacts may still provide legacy
context, but report-grade approval blockers should be traceable through
``deal_state_records`` and their linked CalculationRun / evidence / assumption
records.
"""

from typing import Any, Dict, List

from storage import (
    list_assumption_override_records,
    list_calculation_run_records,
    list_deal_state_records,
    list_pod_ai_review_records,
    list_legal_review_records,
    list_permit_review_records,
)

RESOLVED_REVIEW_STATUSES = {"approved", "waived", "resolved"}
APPROVED_ASSUMPTION_STATUSES = {"approved", "active", "resolved"}
TRACEABILITY_RISK_CODES = {"SOURCE_LOCATOR_MISSING", "EVIDENCE_TRACE_WEAK"}
AUTO_SUPPLEMENT_REVIEW_RISK_TOKENS = {
    "SUPPLEMENTED_NEEDS_REVIEW",
    "NEEDS_APPROVAL",
    "CANDIDATE_NEEDS_APPROVAL",
}
# Deterministic Pod AI Review provenance is guaranteed by reading the review's
# `deterministicFindings` (set by the reviewer regardless of whether an LLM
# supplied the display findings) — never the merged/LLM `findings`. So gating is
# by SEVERITY, excluding only advisory categories. An LLM opinion alone can never
# gate PASS/FAIL (AGENTS.md §1).
ADVISORY_REVIEW_CATEGORIES = {"guardrail", "info", "note"}


def _deterministic_review_findings(review: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The review's deterministic findings (falls back to `findings` for records
    written before the field existed). Only well-formed dict findings."""
    findings = review.get("deterministicFindings")
    if not isinstance(findings, list):
        findings = review.get("findings") or []
    return [f for f in findings if isinstance(f, dict)]


def build_deal_state_approval_readiness(project_id: str) -> Dict[str, Any]:
    """Evaluate approval readiness from the Deal State ledger.

    The five production-critical checks are:
    - open blocking ``risk_issue`` rows,
    - required evidence presence,
    - core metrics sourced from latest CalculationRun records,
    - approved assumptions or active assumption overrides,
    - source locator completeness.
    """
    context = _empty_context(project_id)
    try:
        records = list_deal_state_records(str(project_id), limit=1000)
        calculation_runs = list_calculation_run_records(str(project_id), limit=500)
        active_overrides = list_assumption_override_records(str(project_id), status="active")
    except Exception as exc:
        context["gates"].append(_gate(
            "DEAL_STATE_RECORDS_READABLE",
            False,
            f"Deal State records could not be read: {exc}",
            "blocking",
        ))
        context["readable"] = False
        return _finalize_context(context)

    risk_records = [record for record in records if record.get("recordType") == "risk_issue"]
    evidence_records = [record for record in records if record.get("recordType") == "evidence"]
    metric_records = [record for record in records if record.get("recordType") == "metric"]
    assumption_records = [record for record in records if record.get("recordType") == "assumption"]
    auto_supplement_records = [record for record in records if record.get("recordType") == "auto_supplement_attempt"]
    missing_locator_records = [record for record in records if not _has_source_locator(record)]

    blocking_risks = [record for record in risk_records if _is_blocking_deal_state_risk(record)]
    unreviewed_blocking_risks = [
        record
        for record in blocking_risks
        if str(record.get("reviewStatus") or record.get("status") or "open").lower()
        not in RESOLVED_REVIEW_STATUSES
    ]

    latest_run_by_engine = _latest_calculation_runs_by_engine(calculation_runs)
    latest_metrics_by_key = _latest_metric_records_by_key(metric_records)
    deterministic_metrics = [
        record for record in latest_metrics_by_key.values()
        if record.get("calculationRunId")
    ]
    stale_metrics = [
        record
        for record in deterministic_metrics
        if not _metric_uses_latest_calculation_run(record, latest_run_by_engine)
    ]

    approved_assumptions = [
        record
        for record in assumption_records
        if str(record.get("reviewStatus") or record.get("approvalStatus") or record.get("status") or "").lower()
        in APPROVED_ASSUMPTION_STATUSES
    ]
    open_assumption_candidates = [
        record
        for record in assumption_records
        if _requires_assumption_candidate_review(record)
    ]
    open_traceability_risks = [
        record
        for record in risk_records
        if _risk_code(record) in TRACEABILITY_RISK_CODES
        and str(record.get("reviewStatus") or record.get("status") or "open").lower()
        not in RESOLVED_REVIEW_STATUSES
    ]
    open_auto_supplement_risks = [
        record
        for record in risk_records
        if _is_auto_supplement_review_risk(record)
    ]
    open_auto_supplement_attempts = [
        record
        for record in auto_supplement_records
        if _auto_supplement_attempt_has_open_gap(record)
    ]

    # Pod AI Review blockers — count only deterministic-grounded blocking findings
    # from the latest review per pod (never gate on an LLM opinion alone).
    try:
        pod_ai_reviews = list_pod_ai_review_records(str(project_id), limit=200)
    except Exception:
        pod_ai_reviews = []
    latest_review_by_pod: Dict[str, Dict[str, Any]] = {}
    for review in pod_ai_reviews:
        pod_key = str(review.get("podKey") or "")
        current = latest_review_by_pod.get(pod_key)
        if current is None or str(review.get("updatedAt") or "") >= str(current.get("updatedAt") or ""):
            latest_review_by_pod[pod_key] = review
    pod_ai_review_blockers = [
        {"podKey": pod_key, "finding": finding}
        for pod_key, review in latest_review_by_pod.items()
        if str(review.get("reviewStatus") or "").lower() not in RESOLVED_REVIEW_STATUSES
        for finding in _deterministic_review_findings(review)
        if str(finding.get("severity") or "").lower() == "blocking"
        and str(finding.get("category") or "").lower() not in ADVISORY_REVIEW_CATEGORIES
    ]

    try:
        legal_reviews = list_legal_review_records(str(project_id), limit=20)
        permit_reviews = list_permit_review_records(str(project_id), limit=20)
    except Exception:
        legal_reviews, permit_reviews = [], []
    latest_legal = legal_reviews[0] if legal_reviews else None
    latest_permit = permit_reviews[0] if permit_reviews else None
    legal_status = str((latest_legal or {}).get("status") or "not_run").lower()
    permit_status = str((latest_permit or {}).get("status") or "not_run").lower()
    law_context_connected = bool((latest_legal or {}).get("mcp", {}).get("connected"))
    legal_blocking_risks = [
        record for record in risk_records
        if str(record.get("category") or "").lower() == "legal"
        and _is_blocking_deal_state_risk(record)
        and str(record.get("reviewStatus") or record.get("status") or "open").lower() not in RESOLVED_REVIEW_STATUSES
    ]

    has_records = bool(records)
    has_assumption_requirement = bool(assumption_records) or _has_data_quality_or_assumption_risk(risk_records)

    context.update({
        "readable": True,
        "recordCount": len(records),
        "riskIssueCount": len(risk_records),
        "blockingRiskCount": len(unreviewed_blocking_risks),
        "evidenceCount": len(evidence_records),
        "metricCount": len(metric_records),
        "assumptionCount": len(assumption_records),
        "openAssumptionCandidateCount": len(open_assumption_candidates),
        "autoSupplementAttemptCount": len(auto_supplement_records),
        "openAutoSupplementRiskCount": len(open_auto_supplement_risks),
        "openAutoSupplementGapCount": len(open_auto_supplement_attempts),
        "activeAssumptionOverrideCount": len(active_overrides),
        "latestCalculationRunCount": len(latest_run_by_engine),
        "latestMetricCount": len(deterministic_metrics),
        "staleMetricCount": len(stale_metrics),
        "sourceLocatorMissingCount": len(missing_locator_records),
        "openTraceabilityRiskCount": len(open_traceability_risks),
        "sampleBlockingRiskKeys": _sample_keys(unreviewed_blocking_risks),
        "sampleStaleMetricKeys": _sample_keys(stale_metrics),
        "sampleMissingLocatorKeys": _sample_keys(missing_locator_records),
        "sampleOpenAssumptionCandidateKeys": _sample_keys(open_assumption_candidates),
        "sampleAutoSupplementRiskKeys": _sample_keys(open_auto_supplement_risks),
        "sampleAutoSupplementGapKeys": _sample_keys(open_auto_supplement_attempts),
        "podAiReviewBlockerCount": len(pod_ai_review_blockers),
        "podAiReviewBlockerSummary": [
            {"podKey": b["podKey"], "category": b["finding"].get("category"), "message": str(b["finding"].get("message") or "")[:120]}
            for b in pod_ai_review_blockers[:8]
        ],
        "legalReviewStatus": legal_status,
        "permitReviewStatus": permit_status,
        "legalBlockingRiskCount": len(legal_blocking_risks),
        "koreanLawMcpStatus": (latest_legal or {}).get("mcp", {}).get("status", "not_run"),
        "koreanLawMcpFailureReason": (latest_legal or {}).get("mcp", {}).get("failureReason"),
        "koreanLawMcpConnected": law_context_connected,
    })

    context["gates"].extend([
        _gate(
            "DEAL_STATE_RECORDS_PRESENT",
            has_records,
            "Deal State records must exist before treating the project as approval-ready.",
            "blocking",
        ),
        _gate(
            "DEAL_STATE_BLOCKING_RISKS_CLEARED",
            not unreviewed_blocking_risks,
            f"{len(unreviewed_blocking_risks)} blocking Deal State risk issue(s) remain open.",
            "blocking",
        ),
        _gate(
            "DEAL_STATE_REQUIRED_EVIDENCE_PRESENT",
            has_records and bool(evidence_records),
            "At least one source-backed evidence record must be present in Deal State.",
            "blocking",
        ),
        _gate(
            "DEAL_STATE_REPORT_GRADE_METRICS_PRESENT",
            has_records and bool(deterministic_metrics),
            "Report-grade metrics must come from deterministic CalculationRun records.",
            "blocking",
        ),
        _gate(
            "DEAL_STATE_CORE_METRICS_LATEST",
            has_records and not stale_metrics,
            f"{len(stale_metrics)} metric(s) are not sourced from the latest CalculationRun for their engine.",
            "blocking",
        ),
        _gate(
            "DEAL_STATE_ASSUMPTION_OVERRIDES_APPROVED",
            has_records and (not has_assumption_requirement or bool(approved_assumptions) or bool(active_overrides)),
            "Assumption conflicts or assumption records require an approved assumption or active assumption override.",
            "blocking",
        ),
        _gate(
            "DEAL_STATE_ASSUMPTION_CANDIDATES_REVIEWED",
            not open_assumption_candidates,
            f"{len(open_assumption_candidates)} auto-supplemented or benchmark assumption candidate(s) still require reviewer approval.",
            "blocking",
        ),
        _gate(
            "DEAL_STATE_AUTO_SUPPLEMENT_REVIEW_ITEMS_CLEARED",
            not open_auto_supplement_risks,
            f"{len(open_auto_supplement_risks)} auto-supplement risk review item(s) remain open.",
            "blocking",
        ),
        _gate(
            "DEAL_STATE_AUTO_SUPPLEMENT_GAPS_RESOLVED",
            not open_auto_supplement_attempts,
            f"{len(open_auto_supplement_attempts)} auto-supplement attempt(s) still report failed or user-material-required gaps.",
            "blocking",
        ),
        _gate(
            "DEAL_STATE_SOURCE_LOCATORS_ATTACHED",
            len(missing_locator_records) == 0,
            f"{len(missing_locator_records)} Deal State record(s) lack source locators.",
            "blocking" if has_records else "warning",
        ),
        _gate(
            "DEAL_STATE_SOURCE_LOCATOR_COMPLETENESS_SUFFICIENT",
            not has_records or not open_traceability_risks,
            f"{len(open_traceability_risks)} open source-locator completeness risk issue(s) remain.",
            "blocking",
        ),
        _gate(
            "POD_AI_REVIEW_BLOCKERS_CLEARED",
            not pod_ai_review_blockers,
            f"{len(pod_ai_review_blockers)} unresolved blocking Pod AI Review finding(s) (evidence/scenario-grounded) remain.",
            "blocking",
        ),
        _gate(
            "LEGAL_POD_REVIEW_COMPLETE",
            bool(latest_legal) and not legal_blocking_risks,
            "Legal Pod review must be run and all blocking legal issues must be resolved, waived, or cured.",
            "blocking",
        ),
        _gate(
            "PERMIT_REVIEW_AVAILABLE",
            bool(latest_permit) and permit_status not in {"not_run", "missing"},
            "Permit review evidence and status must be available before approval readiness.",
            "blocking",
        ),
        _gate(
            "KOREAN_LAW_EVIDENCE_AVAILABLE",
            bool(latest_legal) and law_context_connected,
            "Korean Law MCP or official law API evidence must be connected for approval-grade legal review.",
            "blocking",
        ),
    ])
    return _finalize_context(context)


def deal_state_approval_actions(gates: List[Dict[str, Any]]) -> List[str]:
    action_by_code = {
        "DEAL_STATE_RECORDS_READABLE": "Repair Deal State storage access before approval readiness can be evaluated.",
        "DEAL_STATE_RECORDS_PRESENT": "Run the required Agent Pods so metrics, risks, evidence, and assumptions are materialized into Deal State.",
        "DEAL_STATE_BLOCKING_RISKS_CLEARED": "Approve, waive, resolve, or cure blocking Deal State risk issues before approval.",
        "DEAL_STATE_REQUIRED_EVIDENCE_PRESENT": "Attach or auto-supplement source-backed evidence records before approval.",
        "DEAL_STATE_REPORT_GRADE_METRICS_PRESENT": "Run deterministic calculation engines so report-grade metrics are persisted.",
        "DEAL_STATE_CORE_METRICS_LATEST": "Rerun downstream calculations or refresh stale metrics so the approval gate uses the latest CalculationRun per engine.",
        "DEAL_STATE_ASSUMPTION_OVERRIDES_APPROVED": "Approve the relevant assumption or create an active assumption override for unresolved data-quality conflicts.",
        "DEAL_STATE_ASSUMPTION_CANDIDATES_REVIEWED": "Review AI/benchmark-supplemented assumption candidates and approve, waive, resolve, or replace them with source-backed values.",
        "DEAL_STATE_AUTO_SUPPLEMENT_REVIEW_ITEMS_CLEARED": "Resolve auto-supplement review risks before using supplemented values in approval-grade outputs.",
        "DEAL_STATE_AUTO_SUPPLEMENT_GAPS_RESOLVED": "Close failed auto-supplement attempts by attaching evidence, approving a documented assumption, or waiving with rationale.",
        "DEAL_STATE_SOURCE_LOCATORS_ATTACHED": "Attach document page, Excel cell, API row, evidence ID, or Agent artifact locators to Deal State records.",
        "DEAL_STATE_SOURCE_LOCATOR_COMPLETENESS_SUFFICIENT": "Resolve SOURCE_LOCATOR_MISSING or EVIDENCE_TRACE_WEAK risks before approval.",
        "LEGAL_POD_REVIEW_COMPLETE": "Run Legal Pod, attach parcel-level registry/encumbrance evidence, and resolve or waive legal blockers.",
        "PERMIT_REVIEW_AVAILABLE": "Attach permit, zoning, law, or contract review evidence and run the permit review.",
        "KOREAN_LAW_EVIDENCE_AVAILABLE": "Configure the Korean Law MCP bridge or LAW_OC and rerun Legal Pod so statutory references and citation checks are persisted.",
    }
    return list(dict.fromkeys(
        action_by_code.get(gate.get("code"), gate.get("message", "Review Deal State approval gate."))
        for gate in gates
        if not gate.get("pass")
    ))


def _empty_context(project_id: str) -> Dict[str, Any]:
    return {
        "schemaVersion": "realytics-deal-state-approval-readiness-v1",
        "basis": "deal_state_records",
        "projectId": project_id,
        "readable": True,
        "recordCount": 0,
        "riskIssueCount": 0,
        "blockingRiskCount": 0,
        "evidenceCount": 0,
        "metricCount": 0,
        "assumptionCount": 0,
        "openAssumptionCandidateCount": 0,
        "autoSupplementAttemptCount": 0,
        "openAutoSupplementRiskCount": 0,
        "openAutoSupplementGapCount": 0,
        "activeAssumptionOverrideCount": 0,
        "latestCalculationRunCount": 0,
        "latestMetricCount": 0,
        "staleMetricCount": 0,
        "sourceLocatorMissingCount": 0,
        "openTraceabilityRiskCount": 0,
        "sampleBlockingRiskKeys": [],
        "sampleStaleMetricKeys": [],
        "sampleMissingLocatorKeys": [],
        "sampleOpenAssumptionCandidateKeys": [],
        "sampleAutoSupplementRiskKeys": [],
        "sampleAutoSupplementGapKeys": [],
        "gates": [],
    }


def _finalize_context(context: Dict[str, Any]) -> Dict[str, Any]:
    blockers = [gate for gate in context["gates"] if not gate["pass"] and gate["severity"] == "blocking"]
    warnings = [gate for gate in context["gates"] if not gate["pass"] and gate["severity"] == "warning"]
    context["pass"] = not blockers
    context["status"] = "ready" if context["pass"] else "not_ready"
    context["blockingIssueCount"] = len(blockers)
    context["warningCount"] = len(warnings)
    context["blockers"] = blockers
    context["warnings"] = warnings
    context["requiredActions"] = deal_state_approval_actions(blockers + warnings)
    return context


def _gate(code: str, passed: bool, message: str, severity: str) -> Dict[str, Any]:
    return {
        "code": code,
        "pass": bool(passed),
        "severity": severity,
        "message": message,
        "basis": "deal_state_records",
    }


def _latest_calculation_runs_by_engine(calculation_runs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for run in sorted(
        [item for item in calculation_runs if isinstance(item, dict)],
        key=lambda item: item.get("createdAt") or item.get("updatedAt") or "",
        reverse=True,
    ):
        engine_key = str(run.get("engineKey") or "")
        if engine_key and engine_key not in latest:
            latest[engine_key] = run
    return latest


def _latest_metric_records_by_key(metric_records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for record in sorted(
        [item for item in metric_records if isinstance(item, dict)],
        key=lambda item: item.get("updatedAt") or item.get("createdAt") or "",
        reverse=True,
    ):
        key = str(record.get("key") or record.get("metricKey") or "")
        if key and key not in latest:
            latest[key] = record
    return latest


def _metric_uses_latest_calculation_run(
    metric_record: Dict[str, Any],
    latest_run_by_engine: Dict[str, Dict[str, Any]],
) -> bool:
    calculation_run_id = metric_record.get("calculationRunId")
    if not calculation_run_id:
        return False
    engine_key = _engine_key_from_metric(metric_record)
    if not engine_key:
        return True
    latest = latest_run_by_engine.get(engine_key)
    if not latest:
        return True
    return str(latest.get("calculationRunId")) == str(calculation_run_id)


def _engine_key_from_metric(metric_record: Dict[str, Any]) -> str | None:
    locator = metric_record.get("sourceLocator") if isinstance(metric_record.get("sourceLocator"), dict) else {}
    if locator.get("engineKey"):
        return str(locator.get("engineKey"))
    key = str(metric_record.get("key") or metric_record.get("metricKey") or "")
    if "." in key:
        return key.split(".", 1)[0]
    return None


def _has_source_locator(record: Dict[str, Any]) -> bool:
    locator = record.get("sourceLocator") or record.get("source_locator")
    if not isinstance(locator, dict):
        return False
    return any(value is not None and value != "" for value in locator.values())


def _is_blocking_deal_state_risk(record: Dict[str, Any]) -> bool:
    severity = str(record.get("severity") or record.get("riskLevel") or record.get("grade") or "").lower()
    code = _risk_code(record)
    if record.get("blocking") is True:
        return True
    if severity in {"blocking", "critical"}:
        return True
    if any(token in code for token in ("SENIOR_SHORTFALL", "UNSOLD_LTV_BREACH", "REJECT", "BLOCKING")):
        return True
    return False


def _risk_code(record: Dict[str, Any]) -> str:
    return str(record.get("riskCode") or record.get("code") or record.get("key") or "").upper()


def _review_status(record: Dict[str, Any]) -> str:
    return str(record.get("reviewStatus") or record.get("approvalStatus") or record.get("status") or "").lower()


def _requires_assumption_candidate_review(record: Dict[str, Any]) -> bool:
    status = _review_status(record)
    if status in RESOLVED_REVIEW_STATUSES or status in APPROVED_ASSUMPTION_STATUSES:
        return False
    assumption_type = str(record.get("assumptionType") or "").lower()
    source_type = str(record.get("sourceType") or "").lower()
    approval_status = str(record.get("approvalStatus") or "").lower()
    if approval_status in {"candidate_needs_review", "candidate", "needs_review"}:
        return True
    if source_type == "auto_evidence_supplement":
        return True
    return any(token in assumption_type for token in ("proxy", "candidate", "benchmark"))


def _is_auto_supplement_review_risk(record: Dict[str, Any]) -> bool:
    if _review_status(record) in RESOLVED_REVIEW_STATUSES:
        return False
    code = _risk_code(record)
    source_type = str(record.get("sourceType") or "").lower()
    if source_type == "auto_evidence_supplement" and str(record.get("reviewStatus") or "").lower() in {"needs_revision", "needs_review", "unreviewed", "open", ""}:
        return True
    return any(token in code for token in AUTO_SUPPLEMENT_REVIEW_RISK_TOKENS)


def _auto_supplement_attempt_has_open_gap(record: Dict[str, Any]) -> bool:
    if _review_status(record) in RESOLVED_REVIEW_STATUSES:
        return False
    summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
    needs_user_material = int(summary.get("needsUserMaterialCount") or 0)
    failed = int(summary.get("failedCount") or 0)
    return needs_user_material > 0 or failed > 0


def _has_data_quality_or_assumption_risk(risk_records: List[Dict[str, Any]]) -> bool:
    for record in risk_records:
        category = str(record.get("category") or record.get("sourceType") or "").lower()
        code = str(record.get("key") or record.get("code") or "").upper()
        if "data_quality" in category or "assumption" in category:
            return True
        if "DATA_QUALITY" in code or "ASSUMPTION" in code or "CONSISTENCY" in code:
            return True
    return False


def _sample_keys(records: List[Dict[str, Any]], limit: int = 5) -> List[str]:
    return [
        str(record.get("key") or record.get("recordId") or record.get("calculationRunId") or "unknown")
        for record in records[:limit]
    ]
