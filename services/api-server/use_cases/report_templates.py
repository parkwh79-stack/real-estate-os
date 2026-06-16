from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from db.repositories.project_repository import ProjectRepository


REPORT_TEMPLATE_SCHEMA_VERSION = "realytics-report-template-v1"
DEFAULT_PHASE11_SECTIONS = [
    "cover",
    "financial_terms",
    "credit_protection",
    "discount_trigger",
    "sources_and_uses",
    "use_of_funds",
    "land_acquisition",
    "schedule",
    "finance_structure",
    "sales_collection",
    "sales_revenue_waterfall",
    "summary_cash_flow",
    "borrower_analysis",
    "contractor_analysis",
    "marketability_analysis",
    "business_feasibility",
    "exit_strategy_and_risk",
    "conditions_precedent_subsequent",
    "eod",
    "fund_execution_and_repayment_order",
]


def list_report_templates_use_case(
    project_repository: ProjectRepository,
    project_id: str,
) -> Dict[str, Any]:
    project = project_repository.get(project_id)
    if not project:
        raise LookupError("Project not found")
    return {
        "schemaVersion": "realytics-report-template-list-v1",
        "projectId": project_id,
        "templates": list(project.get("reportTemplates") or []),
    }


def get_report_template_use_case(
    project_repository: ProjectRepository,
    project_id: str,
    template_id: str,
) -> Dict[str, Any]:
    templates = list_report_templates_use_case(project_repository, project_id)["templates"]
    for template in templates:
        if template.get("templateId") == template_id:
            return template
    raise LookupError("Report template not found")


def upsert_report_template_use_case(
    project_repository: ProjectRepository,
    project_id: str,
    input_data: Dict[str, Any],
    *,
    edited_by: str,
) -> Dict[str, Any]:
    project = project_repository.get(project_id)
    if not project:
        raise LookupError("Project not found")

    now = datetime.now(timezone.utc).isoformat()
    template_id = input_data.get("templateId") or input_data.get("template_id") or f"rpt-template-{uuid4()}"
    template = {
        "schemaVersion": REPORT_TEMPLATE_SCHEMA_VERSION,
        "templateId": template_id,
        "name": input_data.get("name") or "Phase 11 PF Review Template",
        "reportType": input_data.get("reportType") or input_data.get("report_type") or "phase11_pf_review",
        "status": input_data.get("status") or "draft",
        "locale": input_data.get("locale") or "ko-KR",
        "sections": _normalize_sections(input_data.get("sections")),
        "style": input_data.get("style") or {},
        "variables": input_data.get("variables") or {},
        "approvalBlock": input_data.get("approvalBlock") or input_data.get("approval_block") or {},
        "editedBy": edited_by,
        "updatedAt": now,
        "createdAt": input_data.get("createdAt") or now,
        "changeMemo": input_data.get("changeMemo") or input_data.get("change_memo") or "",
    }

    templates = list(project.get("reportTemplates") or [])
    replaced = False
    for index, existing in enumerate(templates):
        if existing.get("templateId") == template_id:
            template["createdAt"] = existing.get("createdAt") or template["createdAt"]
            template["version"] = int(existing.get("version") or 1) + 1
            history = list(existing.get("history") or [])
            history.append(
                {
                    "version": existing.get("version") or 1,
                    "editedBy": existing.get("editedBy"),
                    "updatedAt": existing.get("updatedAt"),
                    "changeMemo": existing.get("changeMemo"),
                }
            )
            template["history"] = history[-20:]
            templates[index] = template
            replaced = True
            break
    if not replaced:
        template["version"] = 1
        template["history"] = []
        templates.append(template)

    updated = project_repository.update(
        project_id,
        {
            "reportTemplates": templates,
            "_meta": {
                "latestReportTemplateId": template_id,
                "latestReportTemplateUpdatedAt": now,
            },
        },
    )
    if not updated:
        raise LookupError("Project not found")

    return {
        "schemaVersion": "realytics-report-template-upsert-result-v1",
        "projectId": project_id,
        "template": template,
        "project": updated,
    }


def _normalize_sections(sections: Any) -> List[Dict[str, Any]]:
    if not isinstance(sections, list) or not sections:
        return [
            {
                "sectionKey": section_key,
                "title": section_key.replace("_", " ").title(),
                "enabled": True,
                "order": index,
                "sourceAgents": [],
                "templateText": "",
            }
            for index, section_key in enumerate(DEFAULT_PHASE11_SECTIONS, start=1)
        ]

    normalized = []
    for index, section in enumerate(sections, start=1):
        if isinstance(section, str):
            normalized.append(
                {
                    "sectionKey": section,
                    "title": section.replace("_", " ").title(),
                    "enabled": True,
                    "order": index,
                    "sourceAgents": [],
                    "templateText": "",
                }
            )
            continue
        if isinstance(section, dict):
            section_key = section.get("sectionKey") or section.get("key") or f"section_{index}"
            normalized.append(
                {
                    "sectionKey": section_key,
                    "title": section.get("title") or str(section_key).replace("_", " ").title(),
                    "enabled": section.get("enabled", True),
                    "order": section.get("order") or index,
                    "sourceAgents": section.get("sourceAgents") or section.get("source_agents") or [],
                    "templateText": section.get("templateText") or section.get("template_text") or "",
                    "requiredEvidence": section.get("requiredEvidence") or section.get("required_evidence") or [],
                }
            )
    return normalized
