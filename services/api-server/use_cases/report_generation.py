from __future__ import annotations

from typing import Any, Dict

from agents.report_agent import ReportAgent
from agents.report_export_agent import ReportExportAgent
from db.repositories.report_repository import ReportRepository
from use_cases.common import deep_merge


def generate_pf_report_use_case(
    project_data: Dict[str, Any],
    input_data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Generate a structured PF review report and markdown text."""
    source_data = deep_merge(project_data, input_data)
    agent = ReportAgent()
    structured_report = agent.generate_structured_report(source_data)
    markdown = agent.generate_report(source_data)
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
