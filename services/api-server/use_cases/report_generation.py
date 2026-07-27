from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.report_agent import ReportAgent
from agents.report_export_agent import ReportExportAgent
from db.repositories.orchestration_repository import OrchestrationRepository
from db.repositories.project_snapshot_repository import (
    ProjectSnapshotRepository,
    verify_project_snapshot_hash,
)
from db.repositories.report_repository import ReportRepository
from storage import list_deal_state_records, list_pod_ai_review_records
from use_cases.common import deep_merge
from use_cases.orchestration_approval import refresh_approval_readiness_gate

REPORT_TEMPLATE_TO_REPORT_SECTION = {
    "cover": "cover",
    "financial_terms": "financialTerms",
    "credit_protection": "claimPreservation",
    "discount_trigger": "discountTrigger",
    "sources_and_uses": "fundingStructure",
    "use_of_funds": "useOfFunds",
    "land_acquisition": "landSecuring",
    "schedule": "projectSchedule",
    "finance_structure": "financialStructureDiagram",
    "sales_collection": "salesPaymentSchedule",
    "sales_revenue_waterfall": "salesRevenueDistribution",
    "summary_cash_flow": "summaryCashFlow",
    "borrower_analysis": "borrowerAnalysis",
    "contractor_analysis": "contractorAnalysis",
    "marketability_analysis": "marketAnalysis",
    "business_feasibility": "businessFeasibility",
    "exit_strategy_and_risk": "exitStrategy",
    "conditions_precedent_subsequent": "drawdownConditions",
    "eod": "eodTriggers",
    "fund_execution_and_repayment_order": "fundExecutionOrder",
}

REPORT_SECTION_CATEGORIES = {
    "landSecuring": "land",
    "marketAnalysis": "market",
    "discountTrigger": "market",
    "businessFeasibility": "feasibility",
    "summaryCashFlow": "cashflow",
    "fundingStructure": "cashflow",
    "useOfFunds": "cashflow",
    "salesRevenueDistribution": "cashflow",
    "financialTerms": "pf",
    "financialStructureDiagram": "pf",
    "exitStrategy": "risk",
    "claimPreservation": "risk",
    "drawdownConditions": "risk",
    "eodTriggers": "risk",
    "fundExecutionOrder": "risk",
}

# Report-section category -> Chief pods whose Pod AI Review findings back that
# section, so a report paragraph carries the reviewer's evidence gaps / caveats.
# cost_schedule backs the feasibility/cashflow sections (cost + schedule feed the
# cash-flow / feasibility numbers), else its findings would never reach a section.
_SECTION_CATEGORY_PODS = {
    "land": ("land_legal",),
    "market": ("market_product",),
    "feasibility": ("feasibility_cashflow", "cost_schedule"),
    "cashflow": ("feasibility_cashflow", "cost_schedule"),
    "pf": ("pf_credit",),
    "risk": ("pf_credit", "land_legal", "public_policy"),
}

# Report-paragraph evidence is drawn from the review's deterministic findings
# (provenance guaranteed) filtered by severity — an LLM opinion alone (advisory
# categories) must never appear as report-grade evidence.
_ADVISORY_REVIEW_CATEGORIES = {"guardrail", "info", "note"}


def _load_pod_review_findings_by_pod(project_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Latest unresolved Pod AI Review findings per pod (deterministic-grounded).

    Best-effort: read/shape errors yield {} so report generation never breaks."""
    try:
        reviews = list_pod_ai_review_records(str(project_id), limit=200)
    except Exception:
        return {}
    latest_by_pod: Dict[str, Dict[str, Any]] = {}
    for review in reviews:
        pod_key = str(review.get("podKey") or "")
        if not pod_key:
            continue
        current = latest_by_pod.get(pod_key)
        if current is None or str(review.get("updatedAt") or "") >= str(current.get("updatedAt") or ""):
            latest_by_pod[pod_key] = review
    out: Dict[str, List[Dict[str, Any]]] = {}
    for pod_key, review in latest_by_pod.items():
        if str(review.get("reviewStatus") or "").lower() in {"approved", "waived", "resolved"}:
            continue
        source = review.get("deterministicFindings")
        if not isinstance(source, list):
            source = review.get("findings") or []
        findings = [
            {
                "findingId": f.get("findingId"),
                "podKey": pod_key,
                "reviewId": review.get("reviewId"),
                "category": f.get("category"),
                "severity": f.get("severity"),
                "message": f.get("message"),
                "recommendedAction": f.get("recommendedAction"),
            }
            for f in source
            if isinstance(f, dict)
            and str(f.get("severity") or "").lower() in {"blocking", "high", "medium"}
            and str(f.get("category") or "").lower() not in _ADVISORY_REVIEW_CATEGORIES
        ]
        if findings:
            out[pod_key] = findings
    return out


def generate_pf_report_use_case(
    project_data: Dict[str, Any],
    input_data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Generate a structured PF review report and markdown text."""
    input_data = input_data or {}
    source_data = deep_merge(project_data, input_data)
    agent = ReportAgent()
    structured_report = agent.generate_structured_report(source_data)
    structured_report["title"] = "AI PF 심사보고서"
    structured_report["displayUnit"] = "백만원"
    markdown = agent.generate_report(source_data)
    selected_template = _select_report_template(project_data, input_data)
    if selected_template:
        _apply_report_template(structured_report, selected_template)
        markdown = _append_template_application_summary(markdown, structured_report["templateApplication"])
    return {
        "structuredReport": structured_report,
        "reportMarkdown": markdown,
    }


def generate_and_save_pf_report_use_case(
    report_repository: ReportRepository,
    project_id: str,
    project_data: Dict[str, Any],
    input_data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Generate and persist a PF review report through the report repository."""
    generated = generate_pf_report_use_case(project_data, input_data)
    paragraph_evidence_links = build_paragraph_evidence_links(
        project_id,
        generated["structuredReport"],
    )
    generated["paragraphEvidenceLinks"] = paragraph_evidence_links
    generated["structuredReport"]["paragraphEvidenceLinks"] = paragraph_evidence_links
    report_record = report_repository.create(
        project_id,
        generated["structuredReport"],
        generated["reportMarkdown"],
    )
    return {
        "generated": generated,
        "reportRecord": report_record,
    }


def build_paragraph_evidence_links(
    project_id: str,
    structured_report: Dict[str, Any],
) -> Dict[str, Any]:
    """Create report-section evidence links from persisted Deal State records.

    This is intentionally generated server-side at report creation time so the
    saved report has an auditable paragraph-to-record map instead of relying on
    a later frontend heuristic.
    """
    records = list_deal_state_records(project_id, limit=1000)
    pod_findings = _load_pod_review_findings_by_pod(project_id)
    sections = _report_sections(structured_report)
    links = [
        _build_section_link(project_id, section, records, pod_findings)
        for section in sections
    ]
    linked_record_ids = {
        item.get("recordId")
        for link in links
        for item in link.get("linkedRecords", [])
        if item.get("recordId")
    }
    source_locator_ready = sum(
        1
        for link in links
        if (link.get("sourceLocatorCoverage") or {}).get("linkedWithLocator", 0) > 0
    )
    linked_finding_ids = {
        f.get("findingId")
        for link in links
        for f in link.get("podAiReviewFindings", [])
        if f.get("findingId")
    }
    return {
        "schemaVersion": "realytics-paragraph-evidence-links-v1",
        "projectId": project_id,
        "linkMethod": "server_deal_state_section_match_v1",
        "generatedFrom": "deal_state_records+pod_ai_reviews",
        "recordCount": len(records),
        "sectionCount": len(links),
        "linkedRecordCount": len(linked_record_ids),
        "sourceLocatorReadySectionCount": source_locator_ready,
        "podAiReviewFindingCount": len(linked_finding_ids),
        "sectionsWithPodReviewCount": sum(1 for link in links if link.get("podAiReviewFindings")),
        "links": links,
    }


def export_report_use_case(
    report_record: Dict[str, Any],
    input_data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Render a saved report record into a requested export format."""
    input_data = input_data or {}
    export_format = input_data.get("format", "markdown")
    return ReportExportAgent().export(report_record, export_format)


def export_saved_report_use_case(
    report_repository: ReportRepository,
    project_id: str,
    report_id: str,
    input_data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Find, render, save, and return export metadata for a saved report."""
    report_record = report_repository.get(project_id, report_id)
    if not report_record:
        raise LookupError("Report not found")
    rendered = export_report_use_case(report_record, input_data)
    return report_repository.add_export(report_record, rendered)


def publish_saved_report_use_case(
    report_repository: ReportRepository,
    orchestration_repository: OrchestrationRepository,
    project_snapshot_repository: ProjectSnapshotRepository,
    project_id: str,
    report_id: str,
    *,
    published_by: str,
    published_at: str,
) -> Dict[str, Any]:
    """Publish only a report bound to an approved, currently eligible PF run."""
    report_record = report_repository.get(project_id, report_id)
    if not report_record:
        raise LookupError("Report not found")

    run_id = str(report_record.get("run_id") or "").strip()
    if not run_id:
        raise ValueError(
            "Only an orchestration-bound report can be finally published; manual reports remain drafts"
        )
    run = orchestration_repository.get(project_id, run_id)
    if not run:
        raise ValueError("The report's source orchestration run is unavailable")
    if str(run.get("approvalStatus") or "").lower() != "approved":
        raise ValueError("The source PF review run must be approved before report publication")

    maker_id = str(
        run.get("user_id")
        or run.get("requestedBy")
        or run.get("requested_by")
        or (run.get("options") or {}).get("requestedBy")
        or (run.get("options") or {}).get("requested_by")
        or ""
    ).strip()
    if (
        maker_id
        and maker_id.casefold() not in {"system", "anonymous", "unknown"}
        and maker_id.casefold() == str(published_by).strip().casefold()
    ):
        raise ValueError(
            "Maker-checker violation: the PF review requester cannot finally publish the same report"
        )

    gate = refresh_approval_readiness_gate(run)
    if gate.get("pass") is not True:
        blocker_codes = [
            str(item.get("code"))
            for item in (gate.get("blockers") or [])
            if isinstance(item, dict) and item.get("code")
        ]
        suffix = f" Blocking gates: {', '.join(blocker_codes[:12])}." if blocker_codes else ""
        raise ValueError(
            "Approval Readiness Gate must still pass at report publication time."
            + suffix
        )

    snapshot_meta = (run.get("artifacts") or {}).get("projectSnapshot") or {}
    snapshot_id = str(snapshot_meta.get("snapshotId") or f"snapshot-{run_id}")
    snapshot = project_snapshot_repository.get(project_id, snapshot_id)
    if not snapshot:
        raise ValueError("The immutable project review snapshot is unavailable")
    if str(snapshot.get("runId") or "") != run_id:
        raise ValueError("Project snapshot lineage does not match the report run")
    if str(snapshot.get("projectId") or "") != project_id:
        raise ValueError("Project snapshot lineage does not match the report project")

    snapshot_hash = str(snapshot.get("snapshotHash") or "")
    run_snapshot_hash = str(snapshot_meta.get("snapshotHash") or "")
    if not snapshot_hash or not run_snapshot_hash:
        raise ValueError(
            "The project review snapshot is not hash-sealed; final publication is blocked"
        )
    if not verify_project_snapshot_hash(snapshot):
        raise ValueError(
            "The project review snapshot hash seal is invalid; final publication is blocked"
        )
    if snapshot_hash != run_snapshot_hash:
        raise ValueError(
            "Project snapshot hash lineage does not match the approved report run"
        )

    approval = run.get("approval") or {}
    decision_package = {
        "schemaVersion": "realytics-decision-package-manifest-v1",
        "projectId": project_id,
        "projectSnapshotId": snapshot_id,
        "projectSnapshotSchemaVersion": snapshot.get("schemaVersion"),
        "projectSnapshotHash": snapshot_hash,
        "orchestrationRunId": run_id,
        "workflow": run.get("workflow"),
        "approvalStatus": run.get("approvalStatus"),
        "approvedBy": approval.get("approved_by") or approval.get("approvedBy"),
        "approvedAt": approval.get("approved_at") or approval.get("approvedAt"),
        "approvalReadiness": {
            "schemaVersion": gate.get("schemaVersion"),
            "status": gate.get("status"),
            "gateType": gate.get("gateType"),
            "blockingIssueCount": (gate.get("summary") or {}).get("blockingIssueCount"),
            "warningCount": (gate.get("summary") or {}).get("warningCount"),
        },
    }
    return report_repository.publish(
        report_record,
        published_by=published_by,
        published_at=published_at,
        decision_package=decision_package,
    )


def _select_report_template(
    project_data: Dict[str, Any],
    input_data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    template_id = (
        input_data.get("templateId")
        or input_data.get("template_id")
        or input_data.get("reportTemplateId")
        or input_data.get("report_template_id")
        or (input_data.get("reportTemplate") or {}).get("templateId")
    )
    templates = list(project_data.get("reportTemplates") or [])
    if template_id:
        return next((item for item in templates if item.get("templateId") == template_id), None)

    latest_template_id = (project_data.get("_meta") or {}).get("latestReportTemplateId")
    if latest_template_id:
        return next((item for item in templates if item.get("templateId") == latest_template_id), None)
    active = next((item for item in templates if item.get("status") == "active"), None)
    return active


def _apply_report_template(
    structured_report: Dict[str, Any],
    template: Dict[str, Any],
) -> None:
    sections = structured_report.get("sections") or {}
    template_sections = sorted(
        list(template.get("sections") or []),
        key=lambda item: item.get("order") or 0,
    )
    coverage: List[Dict[str, Any]] = []
    section_order: List[Dict[str, Any]] = []
    disabled_sections: List[str] = []

    for index, section in enumerate(template_sections, start=1):
        template_key = section.get("sectionKey") or section.get("key") or f"section_{index}"
        report_section_key = REPORT_TEMPLATE_TO_REPORT_SECTION.get(template_key, _snake_to_camel(template_key))
        enabled = section.get("enabled", True) is not False
        has_report_section = report_section_key in sections
        row = {
            "templateSectionKey": template_key,
            "reportSectionKey": report_section_key,
            "title": section.get("title") or str(template_key).replace("_", " ").title(),
            "enabled": enabled,
            "order": index,
            "sourceAgents": section.get("sourceAgents") or [],
            "requiredEvidence": section.get("requiredEvidence") or [],
            "hasReportSection": has_report_section,
            "templateTextPresent": bool(section.get("templateText")),
        }
        coverage.append(row)
        if enabled and has_report_section:
            section_order.append({
                "index": len(section_order) + 1,
                "key": report_section_key,
                "title": row["title"],
                "templateSectionKey": template_key,
                "sourceAgents": row["sourceAgents"],
                "requiredEvidence": row["requiredEvidence"],
            })
        elif not enabled:
            disabled_sections.append(template_key)

    if section_order:
        structured_report["sectionOrder"] = section_order

    applied = {
        "schemaVersion": "realytics-report-template-application-v1",
        "templateId": template.get("templateId"),
        "name": template.get("name"),
        "version": template.get("version"),
        "status": template.get("status"),
        "locale": template.get("locale"),
        "updatedAt": template.get("updatedAt"),
        "sectionCoverage": coverage,
        "disabledSections": disabled_sections,
        "unmappedSections": [
            row for row in coverage
            if row["enabled"] and not row["hasReportSection"]
        ],
        "style": template.get("style") or {},
        "variables": template.get("variables") or {},
        "approvalBlock": template.get("approvalBlock") or {},
    }
    structured_report["appliedTemplate"] = {
        key: applied.get(key)
        for key in ["templateId", "name", "version", "status", "locale", "updatedAt"]
    }
    structured_report["templateApplication"] = applied
    structured_report.setdefault("qualityChecks", {})["templateApplied"] = bool(template.get("templateId"))
    structured_report.setdefault("qualityChecks", {})["templateUnmappedSectionCount"] = len(applied["unmappedSections"])


def _report_sections(structured_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    section_order = structured_report.get("sectionOrder") or []
    if section_order:
        return [
            {
                "index": section.get("index") or index,
                "key": section.get("key") or section.get("reportSectionKey"),
                "title": section.get("title"),
                "templateSectionKey": section.get("templateSectionKey"),
                "sourceAgents": section.get("sourceAgents") or [],
                "requiredEvidence": section.get("requiredEvidence") or [],
            }
            for index, section in enumerate(section_order, start=1)
        ]
    sections = structured_report.get("sections") or {}
    return [
        {
            "index": index,
            "key": key,
            "title": str(key),
            "templateSectionKey": None,
            "sourceAgents": [],
            "requiredEvidence": [],
        }
        for index, key in enumerate(sections.keys(), start=1)
    ]


def _build_section_link(
    project_id: str,
    section: Dict[str, Any],
    records: List[Dict[str, Any]],
    pod_findings: Dict[str, List[Dict[str, Any]]] | None = None,
) -> Dict[str, Any]:
    section_key = str(section.get("key") or "")
    paragraph_id = f"section:{section_key or section.get('index') or 'unknown'}"
    matched = _match_records_for_section(section, records)
    compact_records = [_compact_deal_state_record(record) for record in matched[:12]]
    linked_with_locator = len([record for record in compact_records if _has_source_locator(record)])
    record_type_ids = {
        "evidence": [],
        "metric": [],
        "risk_issue": [],
        "assumption": [],
    }
    for record in compact_records:
        record_type = record.get("recordType")
        if record_type in record_type_ids and record.get("recordId"):
            record_type_ids[record_type].append(record["recordId"])
    # Attach the Pod AI Review findings from the pods that back this section's
    # category, so the report paragraph carries the reviewer's evidence caveats.
    category = REPORT_SECTION_CATEGORIES.get(section_key) or _section_category(
        " ".join([section_key, str(section.get("title") or ""), " ".join(section.get("sourceAgents") or [])]).lower()
    )
    pod_review_findings: List[Dict[str, Any]] = []
    for pod_key in _SECTION_CATEGORY_PODS.get(category, ()):
        pod_review_findings.extend((pod_findings or {}).get(pod_key, []))
    blocking_review = any(str(f.get("severity")).lower() == "blocking" for f in pod_review_findings)
    return {
        "paragraphId": paragraph_id,
        "projectId": project_id,
        "sectionKey": section_key,
        "templateSectionKey": section.get("templateSectionKey"),
        "title": section.get("title") or section_key,
        "linkMethod": "server_deal_state_section_match_v1",
        "sourceAgents": section.get("sourceAgents") or [],
        "requiredEvidence": section.get("requiredEvidence") or [],
        "linkedRecordCount": len(compact_records),
        "evidenceRecordIds": record_type_ids["evidence"],
        "metricRecordIds": record_type_ids["metric"],
        "riskIssueRecordIds": record_type_ids["risk_issue"],
        "assumptionRecordIds": record_type_ids["assumption"],
        "sourceLocatorCoverage": {
            "linked": len(compact_records),
            "linkedWithLocator": linked_with_locator,
            "missingLocator": max(len(compact_records) - linked_with_locator, 0),
        },
        "linkedRecords": compact_records,
        "podAiReviewFindings": pod_review_findings[:8],
        "podAiReviewBlocking": blocking_review,
    }


def _match_records_for_section(
    section: Dict[str, Any],
    records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    query = " ".join(
        [
            str(section.get("key") or ""),
            str(section.get("templateSectionKey") or ""),
            str(section.get("title") or ""),
            " ".join(section.get("sourceAgents") or []),
            " ".join(section.get("requiredEvidence") or []),
        ]
    ).lower()
    category = REPORT_SECTION_CATEGORIES.get(str(section.get("key") or "")) or _section_category(query)
    keywords = _section_keywords(category)
    required = [str(item).lower() for item in section.get("requiredEvidence") or []]
    agents = [str(item).lower() for item in section.get("sourceAgents") or []]

    scored: List[tuple[int, Dict[str, Any]]] = []
    for record in records:
        haystack = _record_haystack(record)
        score = 0
        if any(item and item in haystack for item in required):
            score += 8
        if any(agent and agent in haystack for agent in agents):
            score += 6
        score += sum(2 for keyword in keywords if keyword and keyword in haystack)
        if record.get("recordType") == _preferred_record_type(category):
            score += 1
        if score > 0:
            scored.append((score, record))
    scored.sort(key=lambda item: (item[0], _has_source_locator(item[1]), str(item[1].get("updatedAt") or "")), reverse=True)
    return [record for _score, record in scored]


def _section_category(query: str) -> str:
    if any(token in query for token in ["land", "parcel", "pnu", "registry", "official", "acquisition"]):
        return "land"
    if any(token in query for token in ["market", "sale", "price", "discount", "presale"]):
        return "market"
    if any(token in query for token in ["feasibility", "business", "revenue", "cost", "profit"]):
        return "feasibility"
    if any(token in query for token in ["cash", "waterfall", "fund", "useoffunds", "funding"]):
        return "cashflow"
    if any(token in query for token in ["financial", "pf", "tranche", "ltv", "ltc", "dscr", "exit"]):
        return "pf"
    if any(token in query for token in ["risk", "eod", "condition", "covenant", "legal", "claim", "리스크", "법무", "약정"]):
        return "risk"
    return "general"


def _section_keywords(category: str) -> List[str]:
    return {
        "land": ["land", "parcel", "pnu", "registry", "official", "land_price", "landSecuring".lower(), "토지", "필지", "등기", "공시지가"],
        "market": ["market", "sale", "price", "comparable", "discount", "marketability", "분양", "시장", "가격", "비교사례"],
        "feasibility": ["feasibility", "revenue", "cost", "profit", "business", "사업수지", "수익", "비용"],
        "cashflow": ["cash", "cashflow", "waterfall", "max_minus", "funding", "useoffunds", "현금", "자금", "상환"],
        "pf": ["pf", "ltv", "ltc", "dscr", "tranche", "exit", "loan", "financial", "금융", "대출", "트랜치"],
        "risk": [
            "risk",
            "legal",
            "covenant",
            "eod",
            "condition",
            "claim",
            "shortfall",
            "breach",
            "reject",
            "senior",
            "mezzanine",
            "junior",
            "unsold",
            "리스크",
            "법무",
            "약정",
            "인출",
            "부족",
            "위반",
        ],
        "general": [],
    }.get(category, [])


def _preferred_record_type(category: str) -> str | None:
    return {
        "land": "evidence",
        "market": "evidence",
        "feasibility": "metric",
        "cashflow": "metric",
        "pf": "metric",
        "risk": "risk_issue",
    }.get(category)


def _record_haystack(record: Dict[str, Any]) -> str:
    parts = [
        record.get("recordType"),
        record.get("key"),
        record.get("label"),
        record.get("sourceType"),
        record.get("calculationRunId"),
        record.get("riskRegisterRunId"),
        record.get("evidenceGraphRunId"),
        record.get("evidenceGraphNodeId"),
        record.get("termId"),
        record.get("canonicalField"),
        record.get("reviewStatus"),
        record.get("severity"),
        record.get("unit"),
        record.get("sourceLocator"),
        record.get("value"),
    ]
    return " ".join(str(part) for part in parts if part is not None).lower()


def _compact_deal_state_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "recordId": record.get("recordId"),
        "recordType": record.get("recordType"),
        "key": record.get("key"),
        "label": record.get("label"),
        "value": record.get("value"),
        "unit": record.get("unit"),
        "sourceType": record.get("sourceType"),
        "sourceLocator": record.get("sourceLocator") or {},
        "calculationRunId": record.get("calculationRunId"),
        "riskRegisterRunId": record.get("riskRegisterRunId"),
        "evidenceGraphRunId": record.get("evidenceGraphRunId"),
        "evidenceGraphNodeId": record.get("evidenceGraphNodeId"),
        "confidence": record.get("confidence"),
        "severity": record.get("severity"),
        "reviewStatus": record.get("reviewStatus"),
        "termId": record.get("termId"),
        "canonicalField": record.get("canonicalField"),
    }


def _has_source_locator(record: Dict[str, Any]) -> bool:
    locator = record.get("sourceLocator") or {}
    if not isinstance(locator, dict):
        return False
    return any(value not in (None, "") for value in locator.values())


def _append_template_application_summary(markdown: str, application: Dict[str, Any]) -> str:
    if not application.get("templateId"):
        return markdown
    enabled_count = len([
        row for row in application.get("sectionCoverage", [])
        if row.get("enabled") and row.get("hasReportSection")
    ])
    unmapped_count = len(application.get("unmappedSections") or [])
    return (
        markdown
        + "\n\n---\n\n"
        + "## 적용 보고서 템플릿\n\n"
        + f"- 템플릿명: {application.get('name') or '-'}\n"
        + f"- 템플릿 ID: {application.get('templateId') or '-'}\n"
        + f"- 버전: v{application.get('version') or '-'}\n"
        + f"- 적용 섹션 수: {enabled_count}\n"
        + f"- 미매핑 섹션 수: {unmapped_count}\n"
    )


def _snake_to_camel(value: str) -> str:
    parts = str(value).split("_")
    if not parts:
        return value
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])
