from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from openpyxl import load_workbook


RISK_REPORT_WORKBOOK_SCHEMA_VERSION = "realytics-risk-report-xlsx-v1"
RISK_REPORT_IMPORTER_VERSION = "risk-report-workbook-importer-v1"

REQUIRED_NAMED_RANGES = [
    "risk_schema_version",
    "risk_project_id",
    "risk_project_name",
    "risk_total_revenue",
    "risk_total_project_cost",
    "risk_pf_loan",
    "risk_base_exit_sale_rate",
    "risk_junior_exit_sale_rate",
    "risk_control_table",
]


def import_risk_report_workbook(
    workbook_path: str | Path,
    *,
    project_id: Optional[str] = None,
    source_document: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Parse a compact risk-report workbook into a server import draft.

    This importer is intentionally source-locator heavy. The compact workbook is
    report-facing, so every parsed risk row and paragraph keeps its sheet/cell
    locator for Risk Register and report-section audit trails.
    """
    workbook_path = Path(workbook_path)
    formula_wb = load_workbook(workbook_path, data_only=False, read_only=False)
    value_wb = load_workbook(workbook_path, data_only=True, read_only=False)
    import_id = f"risk-report-import-{uuid4()}"
    try:
        named = _read_named_ranges(formula_wb, value_wb, REQUIRED_NAMED_RANGES)
        schema_version = _named_value(named, "risk_schema_version") or _read_contract_schema(value_wb)
        validation = _validation(formula_wb, named, schema_version)
        contract = _parse_import_contract(value_wb, named)
        metrics = _parse_metrics(value_wb, named)
        risk_register = _parse_risk_register(value_wb, named, project_id, import_id)
        report_sections = _parse_report_sections(value_wb)
        cash_flow_summary = _parse_cash_flow_summary(value_wb)
        source_map = _parse_source_map(value_wb)

        return {
            "schemaVersion": RISK_REPORT_WORKBOOK_SCHEMA_VERSION,
            "analysisType": "risk_report_workbook_import_draft",
            "importerVersion": RISK_REPORT_IMPORTER_VERSION,
            "importId": import_id,
            "projectId": project_id or _named_value(named, "risk_project_id"),
            "sourceDocument": source_document or {"fileName": workbook_path.name},
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "validation": validation,
            "contract": contract,
            "metrics": metrics,
            "riskRegisterDraft": risk_register,
            "reportSectionsDraft": report_sections,
            "cashFlowSummary": cash_flow_summary,
            "sourceMap": source_map,
            "namedRangeValues": named,
            "approvalPolicy": {
                "requiresReviewerApproval": True,
                "canonicalWriteAllowedWhen": "validation.importReady == true and reviewer approval is supplied",
                "nonFabricationRule": (
                    "Imported rows may be used for Risk Register/report drafting only. "
                    "Missing PNU, official land price, registry, or legal evidence is not fabricated."
                ),
            },
        }
    finally:
        formula_wb.close()
        value_wb.close()


def _read_named_ranges(
    formula_wb: Any,
    value_wb: Any,
    names: List[str],
) -> Dict[str, Dict[str, Any]]:
    values: Dict[str, Dict[str, Any]] = {}
    for name in names:
        if name not in formula_wb.defined_names:
            values[name] = {"present": False, "destinations": [], "value": None, "formula": None}
            continue
        destinations = list(formula_wb.defined_names[name].destinations)
        first_value = None
        first_formula = None
        for sheet_name, cell_range in destinations:
            if sheet_name not in formula_wb.sheetnames or ":" in cell_range:
                continue
            first_value = _json_safe(value_wb[sheet_name][cell_range].value)
            first_formula = _json_safe(formula_wb[sheet_name][cell_range].value)
            break
        values[name] = {
            "present": True,
            "destinations": [{"sheetName": sheet_name, "range": cell_range} for sheet_name, cell_range in destinations],
            "value": first_value,
            "formula": first_formula,
        }
    return values


def _validation(workbook: Any, named: Dict[str, Dict[str, Any]], schema_version: Any) -> Dict[str, Any]:
    missing_named_ranges = [name for name in REQUIRED_NAMED_RANGES if not named.get(name, {}).get("present")]
    required_sheet_roles = {
        "report_addendum": bool(workbook.sheetnames),
        "risk_controls": bool(_first_destination(named, "risk_control_table")),
        "import_contract": bool(_first_destination(named, "risk_schema_version")),
    }
    wrong_schema = schema_version != RISK_REPORT_WORKBOOK_SCHEMA_VERSION
    import_ready = not missing_named_ranges and not wrong_schema and all(required_sheet_roles.values())
    return {
        "status": "passed" if import_ready else "failed",
        "schemaVersion": schema_version,
        "expectedSchemaVersion": RISK_REPORT_WORKBOOK_SCHEMA_VERSION,
        "importReady": import_ready,
        "missingRequiredNamedRanges": missing_named_ranges,
        "requiredSheetRoles": required_sheet_roles,
        "sheetNames": workbook.sheetnames,
        "recommendations": [] if import_ready else _recommendations(missing_named_ranges, wrong_schema),
    }


def _recommendations(missing_named_ranges: List[str], wrong_schema: bool) -> List[str]:
    recommendations: List[str] = []
    if wrong_schema:
        recommendations.append("Upload a workbook with schema_version realytics-risk-report-xlsx-v1.")
    if missing_named_ranges:
        recommendations.append("Restore missing risk_* named ranges before importing into Risk Register.")
    return recommendations


def _parse_import_contract(value_wb: Any, named: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    destination = _first_destination(named, "risk_schema_version")
    if not destination:
        return {}
    sheet_name = destination["sheetName"]
    sheet = value_wb[sheet_name]
    result: Dict[str, Any] = {}
    for row in sheet.iter_rows(min_row=2, values_only=True):
        key = row[0] if row else None
        if key:
            result[str(key)] = _json_safe(row[1] if len(row) > 1 else None)
    return result


def _parse_metrics(value_wb: Any, named: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    metric_keys = {
        "totalRevenue": "risk_total_revenue",
        "totalProjectCost": "risk_total_project_cost",
        "pfLoan": "risk_pf_loan",
        "baseExitSaleRate": "risk_base_exit_sale_rate",
        "juniorExitSaleRate": "risk_junior_exit_sale_rate",
    }
    metrics: Dict[str, Any] = {}
    source_locators = []
    for key, named_range in metric_keys.items():
        value = _named_value(named, named_range)
        destination = _first_destination(named, named_range)
        metrics[key] = _to_number(value)
        if destination:
            source_locators.append(_source_locator(destination["sheetName"], destination["range"], key, "excel_named_range"))
    metrics["sourceLocators"] = source_locators
    return metrics


def _parse_risk_register(
    value_wb: Any,
    named: Dict[str, Dict[str, Any]],
    project_id: Optional[str],
    import_id: str,
) -> Dict[str, Any]:
    destination = _first_destination(named, "risk_control_table")
    if not destination:
        return _empty_risk_register(project_id, import_id)
    sheet = value_wb[destination["sheetName"]]
    rows = sheet[destination["range"]]
    data_rows = rows[1:] if rows else []
    risks: List[Dict[str, Any]] = []
    for row in data_rows:
        values = [_json_safe(cell.value) for cell in row]
        if not values or values[0] in (None, ""):
            continue
        try:
            row_no = int(values[0])
        except (TypeError, ValueError):
            row_no = len(risks) + 1
        category = _text(values, 1)
        item = _text(values, 2)
        level = _text(values, 3)
        owner = _text(values, 4)
        evidence = _text(values, 5)
        source_sheet = _text(values, 6)
        source_locator = _text(values, 7)
        report_section = _text(values, 8)
        report_use = _text(values, 9)
        severity = _severity(level)
        risk_id = f"RR-XLSX-{row_no:03d}"
        risk_code = f"RISK_REPORT_XLSX_{row_no:03d}"
        source_locators = [
            _source_locator(
                destination["sheetName"],
                row[0].row,
                risk_id,
                "excel_row",
                note="Risk control row imported from risk-report-xlsx-v1.",
            )
        ]
        if source_locator:
            source_locators.append({
                "locatorId": f"{risk_id}:source-evidence",
                "sourceType": "excel_cell" if "!" in source_locator else "excel_or_manual_locator",
                "source": source_sheet or "risk_report_workbook",
                "path": source_locator,
                "label": "Risk source locator",
                "locatorPrecision": "cell_or_range" if "!" in source_locator else "declared_locator",
                "note": "Workbook-declared source locator for this risk row.",
            })
        risks.append({
            "riskId": risk_id,
            "sourceAgent": "RiskReportWorkbookImporter",
            "sourceArtifact": "riskReportWorkbook",
            "category": category or "Imported Risk",
            "code": risk_code,
            "title": item or f"Imported risk row {row_no}",
            "description": report_use or item or "Imported compact risk report row.",
            "severity": severity,
            "probability": "medium",
            "score": _severity_score(severity) * 3,
            "grade": "Critical" if severity == "critical" else "High" if severity == "high" else "Medium",
            "financialImpact": None,
            "mitigant": evidence or "Review imported risk evidence and approval condition.",
            "evidenceIds": [],
            "sourceLocators": source_locators,
            "sourceDocumentIds": [],
            "evidenceGraphLinks": {
                "riskNodeId": None,
                "sourceArtifactNodeId": None,
                "evidenceNodeIds": [],
                "claimNodeIds": [],
                "sourceDocumentNodeIds": [],
                "sourceLocatorNodeIds": [],
            },
            "ownerAgent": owner or "reviewer",
            "requiresHumanReview": severity in {"critical", "high"},
            "status": "open",
            "reviewStatus": "pending_review" if severity in {"critical", "high"} else "not_required",
            "reviewedBy": None,
            "reviewedAt": None,
            "reviewComment": None,
            "reviewHistory": [],
            "creditDecisionImpact": "blocks_approval_until_reviewed" if severity == "critical" else "approval_condition_candidate",
            "reportSection": report_section,
            "requiredEvidence": evidence,
        })
    critical_count = sum(1 for risk in risks if risk["severity"] == "critical")
    high_count = sum(1 for risk in risks if risk["severity"] == "high")
    return {
        "schemaVersion": "realytics-risk-register-v1",
        "analysisType": "risk_register_import",
        "registerId": f"{import_id}:risk-register",
        "projectId": project_id,
        "runId": import_id,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "imported_pending_review" if risks else "empty",
        "summary": {
            "riskCount": len(risks),
            "criticalCount": critical_count,
            "highCount": high_count,
            "humanReviewRequiredCount": sum(1 for risk in risks if risk["requiresHumanReview"]),
        },
        "risks": risks,
        "topRisks": risks[:10],
        "approvalConditions": [
            {
                "riskId": risk["riskId"],
                "condition": risk.get("mitigant"),
                "owner": risk.get("ownerAgent"),
                "sourceLocator": (risk.get("sourceLocators") or [{}])[0],
            }
            for risk in risks
            if risk["requiresHumanReview"]
        ],
        "creditReviewInputs": {
            "recommendedDecisionBand": "human_review_required",
            "humanReviewRequiredCount": sum(1 for risk in risks if risk["requiresHumanReview"]),
            "approvalConditions": [],
        },
        "reviewSummary": {
            "pendingReviewCount": sum(1 for risk in risks if risk.get("reviewStatus") == "pending_review"),
            "approvedCount": 0,
            "waivedCount": 0,
            "resolvedCount": 0,
        },
    }


def _empty_risk_register(project_id: Optional[str], import_id: str) -> Dict[str, Any]:
    return {
        "schemaVersion": "realytics-risk-register-v1",
        "analysisType": "risk_register_import",
        "registerId": f"{import_id}:risk-register",
        "projectId": project_id,
        "runId": import_id,
        "status": "empty",
        "risks": [],
        "topRisks": [],
        "approvalConditions": [],
        "reviewSummary": {"pendingReviewCount": 0},
    }


def _parse_report_sections(value_wb: Any) -> Dict[str, Any]:
    sheet = value_wb.worksheets[0] if value_wb.worksheets else None
    if not sheet:
        return {"paragraphs": [], "approvalConditions": []}
    paragraphs: List[Dict[str, Any]] = []
    approval_conditions: List[Dict[str, Any]] = []
    for row in sheet.iter_rows(min_row=1):
        values = [_json_safe(cell.value) for cell in row[:3]]
        if len(values) < 3:
            continue
        third = str(values[2] or "").strip()
        if len(third) < 20:
            continue
        first = str(values[0] or "").strip()
        second = str(values[1] or "").strip()
        locator = f"{sheet.title}!{row[0].coordinate}:{row[2].coordinate}"
        record = {
            "section": first,
            "category": second,
            "text": third,
            "sourceLocator": _source_locator(sheet.title, locator, first or second or "report_text", "excel_range"),
        }
        if _looks_like_condition(first, second):
            approval_conditions.append(record)
        else:
            paragraphs.append(record)
    return {
        "sourceSheet": sheet.title,
        "paragraphs": paragraphs,
        "approvalConditions": approval_conditions,
    }


def _parse_cash_flow_summary(value_wb: Any) -> List[Dict[str, Any]]:
    sheet = _find_sheet(value_wb, ["현금흐름", "cash", "cf"])
    if not sheet:
        return []
    rows = []
    for row in sheet.iter_rows(min_row=2, values_only=False):
        label = _json_safe(row[0].value) if row else None
        if not label:
            continue
        rows.append({
            "label": label,
            "cashIn": _to_number(_json_safe(row[1].value)) if len(row) > 1 else None,
            "cashOut": _to_number(_json_safe(row[2].value)) if len(row) > 2 else None,
            "netCashFlow": _to_number(_json_safe(row[3].value)) if len(row) > 3 else None,
            "cumulativeCashFlow": _to_number(_json_safe(row[4].value)) if len(row) > 4 else None,
            "sourceLocator": _source_locator(sheet.title, row[0].row, str(label), "excel_row"),
        })
    return rows


def _parse_source_map(value_wb: Any) -> List[Dict[str, Any]]:
    sheet = _find_sheet(value_wb, ["근거", "source", "mapping"])
    if not sheet:
        return []
    rows = []
    for row in sheet.iter_rows(min_row=2, values_only=False):
        field = _json_safe(row[0].value) if row else None
        if not field:
            continue
        rows.append({
            "field": field,
            "source": _json_safe(row[1].value) if len(row) > 1 else None,
            "locator": _json_safe(row[2].value) if len(row) > 2 else None,
            "precision": _json_safe(row[3].value) if len(row) > 3 else None,
            "note": _json_safe(row[4].value) if len(row) > 4 else None,
            "sourceLocator": _source_locator(sheet.title, row[0].row, str(field), "excel_row"),
        })
    return rows


def _read_contract_schema(workbook: Any) -> Optional[str]:
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(min_row=1, max_col=3, values_only=True):
            if str(row[0] or "").strip() == "schema_version":
                return str(row[1]) if row[1] is not None else None
    return None


def _find_sheet(workbook: Any, keywords: List[str]) -> Any | None:
    lowered = [(sheet.title, sheet.title.lower()) for sheet in workbook.worksheets]
    for keyword in keywords:
        keyword_lower = keyword.lower()
        for title, lowered_title in lowered:
            if keyword_lower in lowered_title:
                return workbook[title]
    return None


def _named_value(named: Dict[str, Dict[str, Any]], name: str) -> Any:
    return (named.get(name) or {}).get("value")


def _first_destination(named: Dict[str, Dict[str, Any]], name: str) -> Dict[str, Any] | None:
    destinations = (named.get(name) or {}).get("destinations") or []
    return destinations[0] if destinations else None


def _source_locator(sheet_name: str, cell_or_row: Any, label: str, source_type: str, note: str | None = None) -> Dict[str, Any]:
    if isinstance(cell_or_row, int):
        path = f"{sheet_name}!{cell_or_row}:{cell_or_row}"
        precision = "row"
    else:
        path = f"{sheet_name}!{cell_or_row}" if "!" not in str(cell_or_row) else str(cell_or_row)
        precision = "cell_or_range"
    return {
        "locatorId": f"{sheet_name}:{cell_or_row}:{_slug(label)}",
        "sourceType": source_type,
        "source": sheet_name,
        "path": path,
        "label": label,
        "locatorPrecision": precision,
        "note": note,
    }


def _text(values: List[Any], index: int) -> str:
    return str(values[index]).strip() if len(values) > index and values[index] is not None else ""


def _severity(value: str) -> str:
    text = (value or "").lower()
    if any(token in text for token in ["중대", "critical", "block", "以묐"]):
        return "critical"
    if any(token in text for token in ["높", "high", "?믪"]):
        return "high"
    if any(token in text for token in ["낮", "low"]):
        return "low"
    return "medium"


def _severity_score(severity: str) -> int:
    return {"critical": 5, "blocking": 5, "high": 4, "medium": 3, "low": 2}.get(severity, 3)


def _looks_like_condition(first: str, second: str) -> bool:
    text = f"{first} {second}".lower()
    return any(token in text for token in ["condition", "조건", "approval", "승인", "뱀씤"])


def _to_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9가-힣_-]+", "-", str(value or "item")).strip("-")[:80] or "item"
