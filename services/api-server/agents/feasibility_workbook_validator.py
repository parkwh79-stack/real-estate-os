from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet


LEGACY_BONRI_WORKBOOK_PROFILE = {
    "profile": "legacy-bonri-mixed-use-pf-feasibility",
    "required_sheets": [
        "입력",
        "분양",
        "제경비",
        "사업수지",
        "1.수지총괄(대외용)",
        "2.현금흐름",
        "검토서",
    ],
    "key_cells": {
        "project_name": ("사업수지", "C3"),
        "location": ("사업수지", "F3"),
        "land_area_py": ("사업수지", "J3"),
        "total_revenue": ("사업수지", "D8"),
        "total_revenue_after_vat": ("사업수지", "D11"),
        "construction_cost_subtotal": ("사업수지", "D18"),
        "land_cost_subtotal": ("사업수지", "D26"),
        "service_cost_subtotal": ("사업수지", "D33"),
        "sales_expense_subtotal": ("사업수지", "D41"),
        "levy_subtotal": ("사업수지", "D52"),
        "finance_cost_subtotal": ("사업수지", "D62"),
        "other_cost_subtotal": ("사업수지", "D71"),
        "total_cost": ("사업수지", "D72"),
        "development_profit": ("사업수지", "D74"),
        "external_total_revenue": ("1.수지총괄(대외용)", "E51"),
        "review_normal_profit": ("검토서", "E35"),
    },
}


def validate_feasibility_workbook(
    workbook_path: str | Path,
    *,
    profile: Optional[Dict[str, Any]] = None,
    tolerance: float = 1.0,
    max_error_samples: int = 50,
) -> Dict[str, Any]:
    """Inspect and validate a formula-driven feasibility workbook.

    This is intentionally read-only. It validates workbook structure, formula integrity,
    key metric reconciliations, and whether the file is ready for direct v2 import or
    needs a legacy workbook adapter.
    """
    workbook_path = Path(workbook_path)
    selected_profile = profile or LEGACY_BONRI_WORKBOOK_PROFILE

    formula_wb = load_workbook(workbook_path, data_only=False, read_only=False)
    value_wb = load_workbook(workbook_path, data_only=True, read_only=False)

    sheets = _sheet_profiles(formula_wb)
    required_sheet_checks = _required_sheet_checks(formula_wb.sheetnames, selected_profile["required_sheets"])
    key_cells = _read_key_cells(formula_wb, value_wb, selected_profile["key_cells"])
    reconciliation_checks = _reconciliation_checks(key_cells, tolerance)
    formula_integrity = _formula_integrity(formula_wb, value_wb, max_error_samples)
    schema_readiness = _schema_readiness(formula_wb, selected_profile)
    status = _status(required_sheet_checks, reconciliation_checks, formula_integrity)

    return {
        "status": status,
        "profile": selected_profile["profile"],
        "fileName": workbook_path.name,
        "sheetCount": len(formula_wb.worksheets),
        "sheets": sheets,
        "requiredSheetChecks": required_sheet_checks,
        "keyCells": key_cells,
        "reconciliationChecks": reconciliation_checks,
        "formulaIntegrity": formula_integrity,
        "schemaReadiness": schema_readiness,
        "recommendations": _recommendations(status, formula_integrity, schema_readiness),
    }


def _sheet_profiles(workbook: Any) -> List[Dict[str, Any]]:
    profiles = []
    for sheet in workbook.worksheets:
        formula_count = 0
        non_empty_count = 0
        for row in sheet.iter_rows():
            for cell in row:
                value = cell.value
                if value is not None:
                    non_empty_count += 1
                if isinstance(value, str) and value.startswith("="):
                    formula_count += 1
        profiles.append({
            "sheetName": sheet.title,
            "dimension": sheet.calculate_dimension(),
            "maxRow": sheet.max_row,
            "maxColumn": sheet.max_column,
            "nonEmptyCellCount": non_empty_count,
            "formulaCount": formula_count,
            "mergedRangeCount": len(sheet.merged_cells.ranges),
            "tableNames": list(sheet.tables.keys()) if hasattr(sheet, "tables") else [],
        })
    return profiles


def _required_sheet_checks(sheet_names: List[str], required_sheets: List[str]) -> List[Dict[str, Any]]:
    return [
        {
            "sheetName": sheet_name,
            "present": sheet_name in sheet_names,
            "severity": "blocking" if sheet_name not in sheet_names else "ok",
        }
        for sheet_name in required_sheets
    ]


def _read_key_cells(
    formula_wb: Any,
    value_wb: Any,
    key_cell_map: Dict[str, tuple[str, str]],
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for key, (sheet_name, cell_ref) in key_cell_map.items():
        if sheet_name not in formula_wb.sheetnames:
            result[key] = {
                "sheetName": sheet_name,
                "cell": cell_ref,
                "value": None,
                "formula": None,
                "present": False,
            }
            continue
        result[key] = {
            "sheetName": sheet_name,
            "cell": cell_ref,
            "value": _json_safe_value(value_wb[sheet_name][cell_ref].value),
            "formula": formula_wb[sheet_name][cell_ref].value,
            "present": True,
        }
    return result


def _reconciliation_checks(key_cells: Dict[str, Dict[str, Any]], tolerance: float) -> List[Dict[str, Any]]:
    def value(key: str) -> float:
        raw = key_cells.get(key, {}).get("value")
        return float(raw) if isinstance(raw, (int, float)) else 0.0

    checks = []
    total_revenue = value("total_revenue_after_vat")
    total_cost = value("total_cost")
    development_profit = value("development_profit")
    cost_parts = [
        "construction_cost_subtotal",
        "land_cost_subtotal",
        "service_cost_subtotal",
        "sales_expense_subtotal",
        "levy_subtotal",
        "finance_cost_subtotal",
        "other_cost_subtotal",
    ]
    checks.append(_numeric_check(
        "development_profit_equals_revenue_minus_total_cost",
        development_profit,
        total_revenue - total_cost,
        tolerance,
    ))
    checks.append(_numeric_check(
        "total_cost_equals_cost_subtotals",
        total_cost,
        sum(value(key) for key in cost_parts),
        tolerance,
    ))
    checks.append(_numeric_check(
        "external_summary_revenue_matches_business_plan",
        value("external_total_revenue"),
        value("total_revenue"),
        tolerance,
    ))
    checks.append(_numeric_check(
        "review_normal_profit_matches_business_plan",
        value("review_normal_profit"),
        development_profit,
        tolerance,
    ))
    return checks


def _numeric_check(name: str, lhs: float, rhs: float, tolerance: float) -> Dict[str, Any]:
    diff = lhs - rhs
    return {
        "name": name,
        "lhs": lhs,
        "rhs": rhs,
        "diff": diff,
        "tolerance": tolerance,
        "passed": abs(diff) <= tolerance,
        "severity": "blocking" if abs(diff) > tolerance else "ok",
    }


def _formula_integrity(formula_wb: Any, value_wb: Any, max_error_samples: int) -> Dict[str, Any]:
    cached_error_counts: Dict[str, int] = {}
    cached_error_samples: List[Dict[str, Any]] = []
    formula_ref_samples: List[Dict[str, Any]] = []
    formula_ref_count = 0
    formula_count = 0

    for value_sheet in value_wb.worksheets:
        formula_sheet: Worksheet = formula_wb[value_sheet.title]
        for row in value_sheet.iter_rows():
            for value_cell in row:
                formula = formula_sheet[value_cell.coordinate].value
                if isinstance(formula, str) and formula.startswith("="):
                    formula_count += 1
                if isinstance(value_cell.value, str) and value_cell.value.startswith("#"):
                    cached_error_counts[value_cell.value] = cached_error_counts.get(value_cell.value, 0) + 1
                    if len(cached_error_samples) < max_error_samples:
                        cached_error_samples.append({
                            "sheetName": value_sheet.title,
                            "cell": value_cell.coordinate,
                            "value": value_cell.value,
                            "formula": formula,
                        })
                if isinstance(formula, str) and "#REF!" in formula:
                    formula_ref_count += 1
                    if len(formula_ref_samples) < max_error_samples:
                        formula_ref_samples.append({
                            "sheetName": value_sheet.title,
                            "cell": value_cell.coordinate,
                            "formula": formula,
                        })

    return {
        "formulaCount": formula_count,
        "cachedErrorCounts": cached_error_counts,
        "formulaRefCount": formula_ref_count,
        "cachedErrorSamples": cached_error_samples,
        "formulaRefSamples": formula_ref_samples,
        "hasBlockingFormulaErrors": formula_ref_count > 0 or cached_error_counts.get("#REF!", 0) > 0,
    }


def _schema_readiness(workbook: Any, profile: Dict[str, Any]) -> Dict[str, Any]:
    sheet_names = set(workbook.sheetnames)
    required_present = all(sheet in sheet_names for sheet in profile["required_sheets"])
    table_names = []
    for sheet in workbook.worksheets:
        table_names.extend(list(sheet.tables.keys()) if hasattr(sheet, "tables") else [])

    realytics_named_ranges = [
        name for name in workbook.defined_names.keys()
        if str(name).startswith("fs_")
    ]
    expected_sections = {
        "site": "입력" in sheet_names,
        "program": "입력" in sheet_names,
        "product_mix": "분양" in sheet_names,
        "revenue": "사업수지" in sheet_names and "분양" in sheet_names,
        "costs": "사업수지" in sheet_names and "제경비" in sheet_names,
        "financing": "2.현금흐름" in sheet_names,
        "monthly_cash_flow": "2.현금흐름" in sheet_names,
        "review": "검토서" in sheet_names,
    }

    return {
        "requiredLegacySheetsPresent": required_present,
        "sectionsCovered": expected_sections,
        "realyticsNamedRangeCount": len(realytics_named_ranges),
        "excelTableCount": len(table_names),
        "directV2ImportReady": len(realytics_named_ranges) > 0 and len(table_names) > 0,
        "requiresLegacyAdapter": len(realytics_named_ranges) == 0 or len(table_names) == 0,
        "notes": [
            "The sample workbook conceptually covers FeasibilityStudyV2 sections.",
            "It does not use REALYTICS fs_* named ranges or Excel tables, so direct v2 import requires a legacy-cell-map adapter.",
        ],
    }


def _status(
    required_sheet_checks: List[Dict[str, Any]],
    reconciliation_checks: List[Dict[str, Any]],
    formula_integrity: Dict[str, Any],
) -> str:
    if any(not check["present"] for check in required_sheet_checks):
        return "failed"
    if any(not check["passed"] for check in reconciliation_checks):
        return "failed"
    if formula_integrity["hasBlockingFormulaErrors"]:
        return "warning"
    return "passed"


def _recommendations(
    status: str,
    formula_integrity: Dict[str, Any],
    schema_readiness: Dict[str, Any],
) -> List[str]:
    recommendations = []
    if formula_integrity["hasBlockingFormulaErrors"]:
        recommendations.append("Resolve #REF! formulas before accepting the workbook as an authoritative feasibility model.")
    if formula_integrity["cachedErrorCounts"].get("#DIV/0!", 0) > 0:
        recommendations.append("Review #DIV/0! cells and confirm whether they are harmless blank-row artifacts or real calculation errors.")
    if schema_readiness["requiresLegacyAdapter"]:
        recommendations.append("Build a legacy workbook adapter that maps the current Korean workbook cell addresses into FeasibilityStudyV2.")
    if status == "passed":
        recommendations.append("Workbook is structurally ready for import and agent-side metric comparison.")
    return recommendations


def _json_safe_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
