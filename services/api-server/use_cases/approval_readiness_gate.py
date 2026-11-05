from __future__ import annotations

"""Deal State based Approval Readiness Gate.

Approval readiness must be judged from the shared Deal State ledger first, not
from scattered orchestration artifacts. Artifacts may still provide legacy
context, but report-grade approval blockers should be traceable through
``deal_state_records`` and their linked CalculationRun / evidence / assumption
records.
"""

from collections.abc import Mapping
import json
from copy import deepcopy
from typing import Any, Dict, List

from db.postgres import postgres_configured
from db.repositories.legal_intelligence_repository import (
    PostgresLegalTenantOverlayRepository,
)
from db.repositories.legal_review_projection_repository import (
    PostgresLegalReviewProjectionRepository,
)
from db.repositories.calculation_run_repository import (
    PostgresCalculationRunRepository,
)
from db.postgres_runtime_pool import api_tenant_transaction
from db.repositories.request_project_repository import current_project_scope

from storage import (
    get_project_record,
    list_assumption_override_records,
    list_calculation_run_records,
    list_deal_state_records,
    list_pod_ai_review_records,
    list_legal_review_records,
    list_permit_review_records,
    storage_backend,
    _with_effective_assumption_override_status,
)
from use_cases.legal_review_decisions import (
    legal_evidence_readiness,
    legal_human_review_status,
)

RESOLVED_REVIEW_STATUSES = {"approved", "waived", "resolved"}
APPROVED_ASSUMPTION_STATUSES = {"approved", "active", "resolved"}
TRACEABILITY_RISK_CODES = {"SOURCE_LOCATOR_MISSING", "EVIDENCE_TRACE_WEAK"}
AUTO_SUPPLEMENT_REVIEW_RISK_TOKENS = {
    "SUPPLEMENTED_NEEDS_REVIEW",
    "NEEDS_APPROVAL",
    "CANDIDATE_NEEDS_APPROVAL",
}
RECORD_ONLY_LOCATOR_TYPES = {
    "legal_review",
    "legal_review_record",
    "agent_record",
}
EVIDENCE_LOCATOR_KEYS = {
    "artifactKey",
    "apiRecordId",
    "apiRowId",
    "calculationRunId",
    "cell",
    "comparableId",
    "comparableCount",
    "documentId",
    "evidenceId",
    "exactLocator",
    "legalEvidenceItemId",
    "officialUrl",
    "overrideId",
    "page",
    "pageNumber",
    "pnu",
    "range",
    "row",
    "sheet",
    "sheetName",
    "sourceDocumentId",
    "sourceFetchId",
    "sourceUrl",
    "table",
    "url",
}
# Deterministic Pod AI Review provenance is guaranteed by reading the review's
# `deterministicFindings` (set by the reviewer regardless of whether an LLM
# supplied the display findings) — never the merged/LLM `findings`. So gating is
# by SEVERITY, excluding only advisory categories. An LLM opinion alone can never
# gate PASS/FAIL (AGENTS.md §1).
ADVISORY_REVIEW_CATEGORIES = {"guardrail", "info", "note"}
LEGAL_REVIEW_COMPLETE_STATUSES = {
    "clear",
    "caution",
    "reviewed",
    "approved",
    "conditional",
}
PERMIT_REVIEW_COMPLETE_STATUSES = {
    "clear",
    "caution",
    "reviewed",
    "approved",
}
PERMIT_PARSER_VERIFIED_STATUSES = {
    "verified",
    "completed",
    "approved",
}


def _deterministic_review_findings(review: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The review's deterministic findings (falls back to `findings` for records
    written before the field existed). Only well-formed dict findings."""
    findings = review.get("deterministicFindings")
    if not isinstance(findings, list):
        findings = review.get("findings") or []
    return [f for f in findings if isinstance(f, dict)]


def build_deal_state_approval_readiness(
    project_id: str,
    *,
    tenant_id: str | None = None,
) -> Dict[str, Any]:
    """Evaluate approval readiness from the Deal State ledger.

    The five production-critical checks are:
    - open blocking ``risk_issue`` rows,
    - required evidence presence,
    - core metrics sourced from latest CalculationRun records,
    - approved assumptions or active assumption overrides,
    - source locator completeness.
    """
    context = _empty_context(project_id)
    normalized_storage = storage_backend() in {"postgres", "postgresql"}
    try:
        records = list_deal_state_records(str(project_id), limit=1000)
        if normalized_storage:
            scoped_tenant = str(tenant_id or "").strip()
            if not scoped_tenant:
                raise RuntimeError(
                    "tenant_id is required for PostgreSQL approval readiness"
                )
            calculation_runs = PostgresCalculationRunRepository(
                tenant_id=scoped_tenant,
                transaction_factory=_readiness_transaction_factory(scoped_tenant),
            ).latest_runs_by_engine(str(project_id))
            active_overrides = load_active_normalized_assumption_overrides(
                str(project_id), tenant_id=scoped_tenant,
            )
        else:
            calculation_runs = list_calculation_run_records(
                str(project_id),
                limit=500,
            )
            active_overrides = list_assumption_override_records(str(project_id), status="active")
    except Exception as exc:
        context["gates"].append(_gate(
            "DEAL_STATE_RECORDS_READABLE",
            False,
            "Deal State records could not be read. Retry or contact an operator.",
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

    legal_review_readable = permit_review_readable = True
    if normalized_storage:
        projection = PostgresLegalReviewProjectionRepository(
            tenant_id=scoped_tenant,
            project_id=str(project_id),
            connection_factory=lambda: _readiness_transaction_factory(scoped_tenant)(scoped_tenant),
        )
        try:
            legal_reviews = projection.list_legal_reviews(limit=1)
        except Exception:
            legal_reviews, legal_review_readable = [], False
        try:
            permit_reviews = projection.list_permit_reviews(limit=1)
        except Exception:
            permit_reviews, permit_review_readable = [], False
    else:
        try:
            legal_reviews = list_legal_review_records(str(project_id), limit=20)
            permit_reviews = list_permit_review_records(str(project_id), limit=20)
        except Exception:
            legal_reviews, permit_reviews = [], []
            legal_review_readable = permit_review_readable = False
    latest_legal = legal_reviews[0] if legal_reviews else None
    latest_permit = permit_reviews[0] if permit_reviews else None
    legal_status = str(
        (latest_legal or {}).get("status") or ("not_run" if legal_review_readable else "unavailable")
    ).lower()
    permit_status = str(
        (latest_permit or {}).get("status") or ("not_run" if permit_review_readable else "unavailable")
    ).lower()
    permit_parser_status = str(
        (latest_permit or {}).get("parserStatus") or ("not_available" if permit_review_readable else "unavailable")
    ).lower()
    permit_evidence_status = str(
        (latest_permit or {}).get("evidenceStatus") or ("missing" if permit_review_readable else "unavailable")
    ).lower()
    permit_verified_evidence_count = _non_negative_int(
        (latest_permit or {}).get("verifiedEvidenceCount")
    )
    permit_source_locators = (
        (latest_permit or {}).get("sourceLocators")
        if isinstance((latest_permit or {}).get("sourceLocators"), list)
        else []
    )
    legacy_legal_evidence = legal_evidence_readiness(latest_legal)
    normalized_legal_required = _normalized_legal_readiness_required()
    normalized_legal = (
        _load_normalized_legal_readiness(
            str(project_id),
            latest_legal,
            tenant_id=tenant_id,
        )
        if normalized_legal_required
        else None
    )
    legal_evidence = normalized_legal or legacy_legal_evidence
    legal_human_status = (
        str(normalized_legal.get("humanReviewStatus") or "pending").lower()
        if normalized_legal is not None
        else legal_human_review_status(latest_legal)
    )
    legal_human_review_complete = legal_human_status in {"approved", "counsel_approved"}
    legal_pod_review_complete = (
        legal_review_readable
        and bool(normalized_legal)
        and bool(normalized_legal.get("pass"))
        and legal_human_review_complete
        if normalized_legal_required
        else (
            bool(latest_legal)
            and legal_status in LEGAL_REVIEW_COMPLETE_STATUSES
        )
    )
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
        "legalReviewRecordReadable": legal_review_readable,
        "legalHumanReviewStatus": legal_human_status,
        "legalEvidenceStatus": legal_evidence["evidenceStatus"],
        "legalEvidenceVerifiedReferenceCount": legal_evidence["verifiedReferenceCount"],
        "legalEvidenceVerifiedCitationCount": legal_evidence["verifiedCitationCount"],
        "legalEvidenceRequiredClaimCount": legal_evidence["requiredClaimCount"],
        "legalEvidenceVerifiedClaimCount": legal_evidence["verifiedClaimCount"],
        "legalEvidenceClaimCoverageComplete": legal_evidence["claimCoverageComplete"],
        "legalEvidenceRequiredClaimPolicyValid": legal_evidence.get(
            "requiredClaimPolicyValid",
            False,
        ),
        "legalEvidenceRequiredClaimSetId": legal_evidence.get(
            "requiredClaimSetId"
        ),
        "legalEvidenceRequiredClaimSetVersion": legal_evidence.get(
            "requiredClaimSetVersion"
        ),
        "legalEvidenceRequiredClaimSetSha256": legal_evidence.get(
            "requiredClaimSetSha256"
        ),
        "legalEvidenceSnapshotId": legal_evidence["evidenceSnapshotId"],
        "legalEvidenceSnapshotGateEligible": legal_evidence["snapshotGateEligible"],
        "legalEvidenceStale": legal_evidence["stale"],
        "legalEvidenceOpenConflictCount": legal_evidence["openConflictCount"],
        "legalEvidenceSource": legal_evidence.get(
            "source",
            "legacy_projection_fallback",
        ),
        "legalEvidenceNormalizedRequired": normalized_legal_required,
        "legalEvidenceNormalizedReadable": (
            bool(normalized_legal.get("readable"))
            if normalized_legal is not None
            else None
        ),
        "legalEvidenceNormalizedReason": (
            normalized_legal.get("reason")
            if normalized_legal is not None
            else "postgres_not_required"
        ),
        "legalReviewProjectionId": legal_evidence.get(
            "legalReviewProjectionId"
        ),
        "legalReviewProjectionVersion": legal_evidence.get(
            "legalReviewProjectionVersion"
        ),
        "legalReviewProjectionSha256": legal_evidence.get(
            "legalReviewProjectionSha256"
        ),
        "legalReviewProjectionValid": legal_evidence.get(
            "legalReviewProjectionValid",
            False,
        ),
        "legalReviewProjectionSuperseded": legal_evidence.get(
            "legalReviewProjectionSuperseded",
            False,
        ),
        "legalEvidenceSnapshotVersion": legal_evidence.get("snapshotVersion"),
        "legalEvidenceSnapshotSha256": legal_evidence.get("snapshotSha256"),
        "legalEvidenceSnapshotSuperseded": legal_evidence.get(
            "snapshotSuperseded",
            False,
        ),
        "legalEvidenceLineageValid": legal_evidence.get(
            "lineageValid",
            legal_evidence["pass"],
        ),
        "legalEvidenceLatestDecisionId": legal_evidence.get("latestDecisionId"),
        "legalEvidenceLatestDecisionVersion": legal_evidence.get(
            "latestDecisionVersion"
        ),
        "legalEvidenceLatestDecisionSupersedesId": legal_evidence.get(
            "latestDecisionSupersedesId"
        ),
        "legalEvidenceLatestDecisionChainValid": legal_evidence.get(
            "latestDecisionChainValid",
            False,
        ),
        "legalEvidenceLatestDecisionType": legal_evidence.get(
            "latestDecisionType"
        ),
        "legalEvidenceLatestDecisionProjectionId": legal_evidence.get(
            "latestDecisionLegalReviewProjectionId"
        ),
        "legalEvidenceLatestDecisionProjectionVersion": legal_evidence.get(
            "latestDecisionLegalReviewProjectionVersion"
        ),
        "legalEvidenceLatestDecisionProjectionSha256": legal_evidence.get(
            "latestDecisionLegalReviewProjectionSha256"
        ),
        "legalEvidenceLatestDecisionProjectionBindingValid": (
            legal_evidence.get(
                "latestDecisionProjectionBindingValid",
                False,
            )
        ),
        "legalEvidenceLatestDecisionTimeValid": legal_evidence.get(
            "latestDecisionTimeValid",
            False,
        ),
        "legalActiveBlockingMatterCount": legal_evidence.get(
            "activeBlockingMatterCount",
            0,
        ),
        "legalWaivedMatterCount": legal_evidence.get(
            "waivedMatterCount",
            0,
        ),
        "legalInvalidWaiverCount": legal_evidence.get(
            "invalidWaiverCount",
            0,
        ),
        "legalMatterWaiverStateValid": legal_evidence.get(
            "matterWaiverStateValid",
            False,
        ),
        "legalMatterWaiverGovernanceValid": legal_evidence.get(
            "matterWaiverGovernanceValid",
            False,
        ),
        "legalGovernanceChangedAfterDecision": legal_evidence.get(
            "governanceChangedAfterDecision",
            True,
        ),
        "legalLatestMatterGovernanceAt": legal_evidence.get(
            "latestMatterGovernanceAt"
        ),
        "legalLatestWaiverGovernanceAt": legal_evidence.get(
            "latestWaiverGovernanceAt"
        ),
        "permitReviewStatus": permit_status,
        "permitReviewRecordReadable": permit_review_readable,
        "permitParserStatus": permit_parser_status,
        "permitEvidenceStatus": permit_evidence_status,
        "permitVerifiedEvidenceCount": permit_verified_evidence_count,
        "legalBlockingRiskCount": len(legal_blocking_risks),
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
            legal_pod_review_complete
            and not legal_blocking_risks,
            "Legal Pod review must be run and all blocking legal issues must be resolved, waived, or cured.",
            "blocking",
        ),
        _gate(
            "PERMIT_REVIEW_AVAILABLE",
            bool(latest_permit)
            and permit_status in PERMIT_REVIEW_COMPLETE_STATUSES
            and permit_parser_status in PERMIT_PARSER_VERIFIED_STATUSES
            and permit_evidence_status == "verified"
            and permit_verified_evidence_count > 0
            and bool(permit_source_locators),
            (
                "Permit review records could not be read. Retry before approval."
                if not permit_review_readable
                else (
                    "Permit/regulation review requires approved parser output, "
                    "at least one verified source-located evidence item, and a "
                    "non-blocking review status."
                )
            ),
            "blocking",
        ),
        _gate(
            "LEGAL_NORMALIZED_POSTGRES_READABLE",
            not normalized_legal_required
            or bool(normalized_legal and normalized_legal.get("readable")),
            (
                "Normalized PostgreSQL legal evidence, lineage, and review "
                "decision state must be readable for every approval evaluation."
            ),
            "blocking",
        ),
        _gate(
            "LEGAL_NORMALIZED_MATTERS_AND_WAIVERS_CLEAR",
            (
                not normalized_legal_required
                or bool(
                    normalized_legal
                    and normalized_legal.get("readable")
                    and normalized_legal.get(
                        "matterWaiverGovernanceValid"
                    )
                    and _non_negative_int(
                        normalized_legal.get("activeBlockingMatterCount")
                    ) == 0
                    and _non_negative_int(
                        normalized_legal.get("invalidWaiverCount")
                    ) == 0
                    and not normalized_legal.get(
                        "governanceChangedAfterDecision",
                        True,
                    )
                )
            ),
            (
                "Latest normalized legal-matter leaves must have no active "
                "blockers, every waived matter must have an exact current "
                "approved waiver, and no matter/waiver event may postdate "
                "the latest legal approval decision."
            ),
            "blocking",
        ),
        _gate(
            "KOREAN_LAW_EVIDENCE_AVAILABLE",
            bool(legal_evidence["pass"]),
            (
                "Legal Intelligence evidence must have a gate-eligible sealed "
                "snapshot, at least one exact verified citation, current "
                "source lineage, complete required-claim coverage, and no "
                "open conflicts."
            ),
            "blocking",
        ),
        _gate(
            "LEGAL_HUMAN_REVIEW_COMPLETE",
            legal_human_review_complete,
            "A trusted legal reviewer or counsel must approve the Legal Pod review.",
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
        "LEGAL_NORMALIZED_POSTGRES_READABLE": "Restore normalized PostgreSQL Legal Intelligence access and tenant scope before evaluating approval readiness.",
        "LEGAL_NORMALIZED_MATTERS_AND_WAIVERS_CLEAR": "Resolve active normalized legal matters, replace invalid or expired waivers, and obtain a new legal approval after the latest matter or waiver event.",
        "KOREAN_LAW_EVIDENCE_AVAILABLE": "Refresh Legal Intelligence from an authoritative source, resolve citation conflicts, and persist at least one verified statutory reference.",
        "LEGAL_HUMAN_REVIEW_COMPLETE": "Have an authorized legal reviewer or counsel approve the verified Legal Pod review.",
    }
    return list(dict.fromkeys(
        action_by_code.get(gate.get("code"), gate.get("message", "Review Deal State approval gate."))
        for gate in gates
        if not gate.get("pass")
    ))


def _normalized_legal_readiness_required() -> bool:
    # Approval authority never falls back to the legacy JSON projection.
    # Tests may monkeypatch this boundary to exercise compatibility parsing,
    # but no runtime configuration can opt out of normalized PostgreSQL.
    return True


def _readiness_transaction_factory(tenant_id: str):
    """Use the owning request/worker pool without switching identities."""
    scope = current_project_scope()
    if scope is None:
        return api_tenant_transaction
    if scope.tenant_id != tenant_id:
        raise ValueError("Readiness tenant does not match the active runtime scope")
    if scope.worker_id and scope.transaction_factory is None:
        raise RuntimeError("Worker readiness requires its dedicated transaction factory")
    return scope.transaction_factory or api_tenant_transaction


def load_active_normalized_assumption_overrides(project_id: str, *, tenant_id: str) -> list[dict[str, Any]]:
    """Read persisted override authority; the app_state document is not a fallback."""
    with _readiness_transaction_factory(tenant_id)(tenant_id) as connection:
        rows = connection.execute(
            """
            SELECT tenant_id, project_id, override_id, check_key, canonical_field,
                   status, approved_by, approved_at, updated_at, payload_json
              FROM realytics.assumption_overrides
             WHERE tenant_id = %s AND project_id = %s AND status = 'active'
             ORDER BY updated_at DESC, override_id
            """,
            (tenant_id, project_id),
        ).fetchall()
    result = []
    for row in rows:
        if row[0] != tenant_id or row[1] != project_id:
            raise ValueError("Assumption override row violates its runtime scope")
        payload = json.loads(row[9]) if isinstance(row[9], str) else deepcopy(row[9])
        if not isinstance(payload, dict):
            raise ValueError("Assumption override payload is invalid")
        if any(payload.get(key) not in (None, "", tenant_id) for key in ("tenantId", "tenant_id")):
            raise ValueError("Assumption override payload tenant mismatch")
        if payload.get("projectId") not in (None, "", project_id):
            raise ValueError("Assumption override payload project mismatch")
        payload.update({
            "tenantId": row[0], "projectId": row[1], "overrideId": row[2],
            "checkKey": row[3], "canonicalField": row[4], "status": row[5],
            "approvedBy": row[6],
            "approvedAt": row[7].isoformat() if hasattr(row[7], "isoformat") else row[7],
            "updatedAt": row[8].isoformat() if hasattr(row[8], "isoformat") else row[8],
        })
        effective = _with_effective_assumption_override_status(payload)
        if effective.get("effectiveStatus") == "active":
            result.append(effective)
    return result


def _load_normalized_legal_readiness(
    project_id: str,
    legal_review: Mapping[str, Any] | None,
    *,
    tenant_id: str | None,
) -> dict[str, Any]:
    # Retain the argument for compatibility with callers while deliberately
    # refusing to use the legacy JSON projection as approval authority.
    del legal_review
    scoped_tenant = str(tenant_id or "").strip()
    if not scoped_tenant:
        try:
            project = get_project_record(project_id) or {}
        except Exception:
            project = {}
        metadata = (
            project.get("_meta")
            if isinstance(project.get("_meta"), Mapping)
            else {}
        )
        scoped_tenant = str(
            project.get("tenantId")
            or project.get("tenant_id")
            or metadata.get("tenantId")
            or metadata.get("tenant_id")
            or ""
        ).strip()

    if not scoped_tenant:
        return _normalized_legal_failure(
            "normalized_tenant_scope_missing",
            snapshot_id=None,
            readable=True,
            details={"missingFields": ["tenantId"]},
        )

    try:
        transaction_factory = _readiness_transaction_factory(scoped_tenant)
        repository = PostgresLegalTenantOverlayRepository(
            tenant_id=scoped_tenant,
            project_id=project_id,
            connection_factory=lambda: transaction_factory(scoped_tenant),
        )
        return repository.evaluate_latest_approval_readiness()
    except Exception as exc:
        return _normalized_legal_failure(
            "normalized_postgres_unavailable",
            snapshot_id=None,
            readable=False,
            details={"errorType": type(exc).__name__},
        )


def _normalized_legal_failure(
    reason: str,
    *,
    snapshot_id: str | None,
    readable: bool,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": "realytics-normalized-legal-readiness-v1",
        "source": "normalized_postgres",
        "readable": readable,
        "pass": False,
        "reason": reason,
        "legalReviewProjectionId": None,
        "legalReviewProjectionReviewId": None,
        "legalReviewProjectionVersion": None,
        "legalReviewProjectionSha256": None,
        "legalReviewProjectionValid": False,
        "legalReviewProjectionSuperseded": False,
        "legalReviewProjectionAnalysisOnly": True,
        "legalReviewProjectionReportEligible": False,
        "evidenceStatus": "missing",
        "verifiedReferenceCount": 0,
        "verifiedCitationCount": 0,
        "requiredClaimCount": 0,
        "verifiedClaimCount": 0,
        "claimCoverageComplete": False,
        "requiredClaimPolicyValid": False,
        "requiredClaimSetId": None,
        "requiredClaimSetVersion": None,
        "requiredClaimSetSha256": None,
        "evidenceSnapshotId": snapshot_id,
        "snapshotVersion": None,
        "snapshotSha256": None,
        "snapshotGateEligible": False,
        "snapshotSuperseded": False,
        "stale": True,
        "openConflictCount": 0,
        "lineageValid": False,
        "invalidLineageCount": 0,
        "coverageComplete": False,
        "latestDecisionId": None,
        "latestDecisionVersion": None,
        "latestDecisionSupersedesId": None,
        "latestDecisionExpectedPredecessorId": None,
        "latestDecisionChainValid": False,
        "latestDecisionType": None,
        "latestDecisionLegalReviewProjectionId": None,
        "latestDecisionLegalReviewProjectionVersion": None,
        "latestDecisionLegalReviewProjectionSha256": None,
        "latestDecisionProjectionBindingValid": False,
        "latestDecisionTimeValid": False,
        "latestDecisionAt": None,
        "activeBlockingMatterCount": 0,
        "waivedMatterCount": 0,
        "invalidWaiverCount": 0,
        "matterWaiverStateValid": False,
        "matterWaiverGovernanceValid": False,
        "governanceChangedAfterDecision": True,
        "latestMatterGovernanceAt": None,
        "latestWaiverGovernanceAt": None,
        "humanReviewStatus": "pending",
        "details": dict(details or {}),
    }


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
        return False
    latest = latest_run_by_engine.get(engine_key)
    if not latest:
        return False
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
    # ``recordLocator`` identifies the Legal Pod/Agent output itself. It is
    # useful for audit navigation but is not independent source evidence and
    # must never satisfy an approval traceability gate.
    evidence_locator = (
        record.get("evidenceLocator")
        or record.get("evidence_locator")
    )
    is_legal_record = (
        str(record.get("category") or "").strip().casefold() == "legal"
        or str(record.get("recordType") or "").strip().casefold()
        == "legal_evidence"
        or str(record.get("sourceType") or "").strip().casefold()
        in {"legal_pod", "legal_evidence", "official_legal_source"}
    )
    # Legal records use the explicit two-locator contract.  A legacy
    # ``sourceLocator`` may remain in the projection for old readers, but it
    # cannot satisfy this Gate after the record/evidence split.
    locator = evidence_locator
    if not is_legal_record and not isinstance(locator, dict):
        locator = record.get("sourceLocator") or record.get("source_locator")
    if not isinstance(locator, dict):
        return False
    locator_type = str(
        locator.get("sourceType")
        or locator.get("type")
        or ""
    ).strip().lower()
    if locator_type in RECORD_ONLY_LOCATOR_TYPES:
        return False
    return any(
        locator.get(key) is not None and locator.get(key) != ""
        for key in EVIDENCE_LOCATOR_KEYS
    )


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


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
