from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from storage import list_deal_state_records, upsert_deal_state_record


SOURCE_LOCATOR_MISSING = "SOURCE_LOCATOR_MISSING"
EVIDENCE_TRACE_WEAK = "EVIDENCE_TRACE_WEAK"
_AUDIT_RISK_CODES = {SOURCE_LOCATOR_MISSING, EVIDENCE_TRACE_WEAK}

_STRONG_LOCATOR_KEYS = {
    "documentId",
    "sourceDocumentId",
    "archiveId",
    "fileName",
    "page",
    "pageNumber",
    "sheet",
    "sheetName",
    "cell",
    "cellRange",
    "apiRowId",
    "apiRecordId",
    "evidenceId",
    "artifactKey",
    "nodeId",
    "locator",
    "path",
}
_WEAK_LOCATOR_KEYS = {
    "runId",
    "calculationRunId",
    "engineKey",
    "graphId",
    "registerId",
}


def run_source_locator_completeness_audit_use_case(
    project_id: str,
    *,
    report_records: List[Dict[str, Any]] | None = None,
    actor: str = "source_locator_auditor",
    persist: bool = True,
) -> Dict[str, Any]:
    """Check Deal State and report paragraph evidence links for traceability.

    The audit persists risk_issue rows so Risk Register, Approval Gate, and
    Report Studio all see traceability defects through the same Deal State
    registry.
    """
    report_records = report_records or []
    records = [
        record
        for record in list_deal_state_records(project_id, limit=1000)
        if not (
            record.get("recordType") == "risk_issue"
            and str(record.get("key") or "") in _AUDIT_RISK_CODES
        )
    ]
    missing_records: List[Dict[str, Any]] = []
    weak_records: List[Dict[str, Any]] = []
    for record in records:
        if not _locator_has_value(record.get("sourceLocator")):
            missing_records.append(record)
        elif not _is_strong_locator(record.get("sourceLocator")):
            weak_records.append(record)

    paragraph_issues = _audit_report_paragraph_links(report_records)
    now = datetime.now(timezone.utc).isoformat()
    summary = {
        "schemaVersion": "realytics-source-locator-completeness-audit-v1",
        "projectId": project_id,
        "checkedAt": now,
        "checkedBy": actor,
        "checkedDealStateRecordCount": len(records),
        "checkedReportCount": len(report_records),
        "hasReviewableRecords": bool(records or report_records),
        "missingLocatorCount": len(missing_records),
        "weakTraceCount": len(weak_records),
        "weakParagraphCount": len(paragraph_issues),
        "missingExamples": [_record_example(record) for record in missing_records[:20]],
        "weakExamples": [_record_example(record) for record in weak_records[:20]],
        "weakParagraphExamples": paragraph_issues[:20],
        "persisted": persist,
        "analysisOnly": not persist,
    }
    if not persist:
        return {**summary, "auditId": None, "sourceLocatorMissingRisk": None, "evidenceTraceWeakRisk": None}
    audit_id = f"source-locator-audit:{uuid4()}"
    missing_risk = _persist_missing_locator_risk(
        project_id,
        audit_id=audit_id,
        now=now,
        actor=actor,
        missing_records=missing_records,
        checked_count=len(records),
    )
    weak_risk = _persist_weak_trace_risk(
        project_id,
        audit_id=audit_id,
        now=now,
        actor=actor,
        weak_records=weak_records,
        weak_paragraphs=paragraph_issues,
        checked_count=len(records),
    )
    return {
        **summary, "auditId": audit_id,
        "sourceLocatorMissingRisk": missing_risk,
        "evidenceTraceWeakRisk": weak_risk,
    }


def _persist_missing_locator_risk(
    project_id: str,
    *,
    audit_id: str,
    now: str,
    actor: str,
    missing_records: List[Dict[str, Any]],
    checked_count: int,
) -> Dict[str, Any]:
    count = len(missing_records)
    review_status = "open" if count else "resolved"
    severity = "high" if count else "low"
    return upsert_deal_state_record(
        project_id,
        {
            "recordId": f"risk_issue:{SOURCE_LOCATOR_MISSING}",
            "recordType": "risk_issue",
            "key": SOURCE_LOCATOR_MISSING,
            "title": "Source locator missing",
            "description": "Deal State records are missing document page, Excel cell, API row, evidence ID, or Agent artifact locator.",
            "severity": severity,
            "reviewStatus": review_status,
            "sourceType": "source_locator_audit",
            "sourceLocator": {"artifactKey": audit_id, "checkedAt": now},
            "value": {
                "missingLocatorCount": count,
                "checkedDealStateRecordCount": checked_count,
                "examples": [_record_example(record) for record in missing_records[:20]],
            },
            "reviewedBy": actor if review_status == "resolved" else None,
            "reviewedAt": now if review_status == "resolved" else None,
            "updatedAt": now,
        },
    )


def _persist_weak_trace_risk(
    project_id: str,
    *,
    audit_id: str,
    now: str,
    actor: str,
    weak_records: List[Dict[str, Any]],
    weak_paragraphs: List[Dict[str, Any]],
    checked_count: int,
) -> Dict[str, Any]:
    count = len(weak_records) + len(weak_paragraphs)
    review_status = "open" if count else "resolved"
    severity = "medium" if count else "low"
    return upsert_deal_state_record(
        project_id,
        {
            "recordId": f"risk_issue:{EVIDENCE_TRACE_WEAK}",
            "recordType": "risk_issue",
            "key": EVIDENCE_TRACE_WEAK,
            "title": "Evidence trace weak",
            "description": "Some numbers, risk rows, or report paragraphs only have weak traceability or linked records without precise source locators.",
            "severity": severity,
            "reviewStatus": review_status,
            "sourceType": "source_locator_audit",
            "sourceLocator": {"artifactKey": audit_id, "checkedAt": now},
            "value": {
                "weakTraceCount": len(weak_records),
                "weakParagraphCount": len(weak_paragraphs),
                "checkedDealStateRecordCount": checked_count,
                "recordExamples": [_record_example(record) for record in weak_records[:20]],
                "paragraphExamples": weak_paragraphs[:20],
            },
            "reviewedBy": actor if review_status == "resolved" else None,
            "reviewedAt": now if review_status == "resolved" else None,
            "updatedAt": now,
        },
    )


def _audit_report_paragraph_links(report_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    weak: List[Dict[str, Any]] = []
    for report in report_records:
        report_payload = report.get("report") or {}
        paragraph_links = report_payload.get("paragraphEvidenceLinks") or {}
        for link in paragraph_links.get("links") or []:
            coverage = link.get("sourceLocatorCoverage") or {}
            linked_count = int(coverage.get("linked") or link.get("linkedRecordCount") or 0)
            missing_locator = int(coverage.get("missingLocator") or 0)
            if linked_count == 0 or missing_locator > 0:
                weak.append(
                    {
                        "reportId": report.get("report_id"),
                        "version": report.get("version"),
                        "paragraphId": link.get("paragraphId"),
                        "sectionKey": link.get("sectionKey"),
                        "title": link.get("title"),
                        "linkedRecordCount": linked_count,
                        "missingLocator": missing_locator,
                    }
                )
    return weak


def _record_example(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "recordId": record.get("recordId"),
        "recordType": record.get("recordType"),
        "key": record.get("key"),
        "sourceType": record.get("sourceType"),
        "sourceLocator": record.get("sourceLocator") or {},
        "reviewStatus": record.get("reviewStatus"),
        "severity": record.get("severity"),
    }


def _locator_has_value(locator: Any) -> bool:
    if not isinstance(locator, dict):
        return False
    return any(value not in (None, "", [], {}) for value in locator.values())


def _is_strong_locator(locator: Any) -> bool:
    if not isinstance(locator, dict):
        return False
    if any(locator.get(key) not in (None, "", [], {}) for key in _STRONG_LOCATOR_KEYS):
        return True
    nested = locator.get("sourceLocator") or locator.get("locator")
    if isinstance(nested, dict):
        return _is_strong_locator(nested)
    if any(locator.get(key) not in (None, "", [], {}) for key in _WEAK_LOCATOR_KEYS):
        return False
    return False
