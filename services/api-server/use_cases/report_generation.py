from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.report_agent import ReportAgent
from agents.report_export_agent import ReportExportAgent
from db.repositories.report_repository import ReportRepository
from use_cases.common import deep_merge

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
    report_record = report_repository.create(
        project_id,
        generated["structuredReport"],
        generated["reportMarkdown"],
    )
    return {
        "generated": generated,
        "reportRecord": report_record,
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
