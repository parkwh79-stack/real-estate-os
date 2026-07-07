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
)

RESOLVED_REVIEW_STATUSES = {"approved", "waived", "resolved"}
APPROVED_ASSUMPTION_STATUSES = {"approved", "active", "resolved"}
TRACEABILITY_RISK_CODES = {"SOURCE_LOCATOR_MISSING", "EVIDENCE_TRACE_WEAK"}
# Pod AI Review finding categories that are grounded in deterministic signals
# (evidence gaps, source-locator gaps, data-quality, and scenario-engine risk
# flags). Only these can block readiness — per the invariant that an LLM opinion
# alone must never gate PASS/FAIL. Advisory categories (guardrail, info) never block.
DETERMINISTIC_REVIEW_CATEGORIES = {
    "missing_evidence",
    "source_locator_gap",
    "data_quality",
    "risk_note",
    "recalculation_needed",
}


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
    open_traceability_risks = [
        record
        for record in risk_records
        if str(record.get("key") or "").upper() in TRACEABILITY_RISK_CODES
        and str(record.get("reviewStatus") or record.get("status") or "open").lower()
        not in RESOLVED_REVIEW_STATUSES
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
        for finding in (review.get("findings") or [])
        if str(finding.get("severity") or "").lower() == "blocking"
        and str(finding.get("category") or "") in DETERMINISTIC_REVIEW_CATEGORIES
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
        "activeAssumptionOverrideCount": len(active_overrides),
        "latestCalculationRunCount": len(latest_run_by_engine),
        "latestMetricCount": len(deterministic_metrics),
        "staleMetricCount": len(stale_metrics),
        "sourceLocatorMissingCount": len(missing_locator_records),
        "openTraceabilityRiskCount": len(open_traceability_risks),
        "sampleBlockingRiskKeys": _sample_keys(unreviewed_blocking_risks),
        "sampleStaleMetricKeys": _sample_keys(stale_metrics),
        "sampleMissingLocatorKeys": _sample_keys(missing_locator_records),
        "podAiReviewBlockerCount": len(pod_ai_review_blockers),
        "podAiReviewBlockerSummary": [
            {"podKey": b["podKey"], "category": b["finding"].get("category"), "message": str(b["finding"].get("message") or "")[:120]}
            for b in pod_ai_review_blockers[:8]
        ],
    })

    context["gates"].extend([
        _gate(
            "DEAL_STATE_RECORDS_PRESENT",
            has_records,
            "Deal State records must exist before treating the project as approval-ready.",
            "warning",
        ),
        _gate(
            "DEAL_STATE_BLOCKING_RISKS_CLEARED",
            not unreviewed_blocking_risks,
            f"{len(unreviewed_blocking_risks)} blocking Deal State risk issue(s) remain open.",
            "blocking",
        ),
        _gate(
            "DEAL_STATE_REQUIRED_EVIDENCE_PRESENT",
            not has_records or bool(evidence_records),
            "At least one source-backed evidence record must be present in Deal State.",
            "blocking",
        ),
        _gate(
            "DEAL_STATE_REPORT_GRADE_METRICS_PRESENT",
            not has_records or bool(deterministic_metrics),
            "Report-grade metrics must come from deterministic CalculationRun records.",
            "blocking",
        ),
        _gate(
            "DEAL_STATE_CORE_METRICS_LATEST",
            not has_records or not stale_metrics,
            f"{len(stale_metrics)} metric(s) are not sourced from the latest CalculationRun for their engine.",
            "blocking",
        ),
        _gate(
            "DEAL_STATE_ASSUMPTION_OVERRIDES_APPROVED",
            not has_records or not has_assumption_requirement or bool(approved_assumptions) or bool(active_overrides),
            "Assumption conflicts or assumption records require an approved assumption or active assumption override.",
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
        "DEAL_STATE_SOURCE_LOCATORS_ATTACHED": "Attach document page, Excel cell, API row, evidence ID, or Agent artifact locators to Deal State records.",
        "DEAL_STATE_SOURCE_LOCATOR_COMPLETENESS_SUFFICIENT": "Resolve SOURCE_LOCATOR_MISSING or EVIDENCE_TRACE_WEAK risks before approval.",
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
        "activeAssumptionOverrideCount": 0,
        "latestCalculationRunCount": 0,
        "latestMetricCount": 0,
        "staleMetricCount": 0,
        "sourceLocatorMissingCount": 0,
        "openTraceabilityRiskCount": 0,
        "sampleBlockingRiskKeys": [],
        "sampleStaleMetricKeys": [],
        "sampleMissingLocatorKeys": [],
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
    code = str(record.get("key") or record.get("code") or "").upper()
    if severity in {"blocking", "critical"}:
        return True
    if any(token in code for token in ("SENIOR_SHORTFALL", "UNSOLD_LTV_BREACH", "REJECT", "BLOCKING")):
        return True
    return False


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
