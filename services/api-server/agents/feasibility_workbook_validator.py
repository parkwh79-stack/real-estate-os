from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from openpyxl import load_workbook
from openpyxl.worksheet.formula import ArrayFormula
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

REALYTICS_FORMULA_WORKBOOK_PROFILE = {
    "profile": "realytics-formula-cash-flow-export",
    "required_sheets": [
        "00_사용안내",
        "01_입력가정",
        "02A_분양수입",
        "02B_수납스케줄",
        "02_사업수지",
        "03A_공정률",
        "03_월별현금흐름",
        "03B_PF인출상환",
        "03C_Tranche상환",
        "03D_Tranche월별상환",
        "04A_할인민감도",
        "04B_할인Trigger",
        "04_검증",
        "05_수식지도",
        "06_샘플대사",
        "07_Import계약",
    ],
    "key_cells": {},
    "required_named_ranges": [
        "realytics_fs_schema_version",
        "realytics_source_workbook",
        "fs_total_revenue",
        "fs_total_project_cost",
        "fs_pf_loan",
        "fs_revenue_scenario",
        "cf_monthly_revenue",
        "cf_cumulative_cash_flow",
        "cf_max_minus",
        "pf_ending_balance",
        "pf_tranche_shortfall_total",
        "trigger_adjusted_revenue_total",
    ],
    "formula_integrity_sheets": [
        "00_사용안내",
        "01_입력가정",
        "02A_분양수입",
        "02B_수납스케줄",
        "02_사업수지",
        "03A_공정률",
        "03_월별현금흐름",
        "03B_PF인출상환",
        "03C_Tranche상환",
        "03D_Tranche월별상환",
        "04A_할인민감도",
        "04B_할인Trigger",
        "04_검증",
        "05_수식지도",
        "06_샘플대사",
        "07_Import계약",
    ],
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

    formula_wb = load_workbook(workbook_path, data_only=False, read_only=False)
    value_wb = load_workbook(workbook_path, data_only=True, read_only=False)
    selected_profile = profile or _detect_profile(formula_wb)

    try:
        sheets = _sheet_profiles(formula_wb)
        required_sheet_checks = _required_sheet_checks(formula_wb.sheetnames, selected_profile["required_sheets"])
        key_cells = _read_key_cells(formula_wb, value_wb, selected_profile["key_cells"])
        required_key_cell_checks = _required_key_cell_checks(key_cells)
        reconciliation_checks = _reconciliation_checks(key_cells, tolerance)
        formula_integrity = _formula_integrity(
            formula_wb,
            value_wb,
            max_error_samples,
            included_sheet_names=selected_profile.get("formula_integrity_sheets"),
        )
        defined_names = _defined_name_profiles(formula_wb)
        named_range_values = _read_named_range_values(
            formula_wb,
            value_wb,
            selected_profile.get("required_named_ranges", []),
        )
        schema_readiness = _schema_readiness(formula_wb, selected_profile)
        status = _status(
            required_sheet_checks,
            required_key_cell_checks,
            reconciliation_checks,
            formula_integrity,
            schema_readiness,
        )

        return {
            "status": status,
            "profile": selected_profile["profile"],
            "fileName": workbook_path.name,
            "sheetCount": len(formula_wb.worksheets),
            "sheets": sheets,
            "requiredSheetChecks": required_sheet_checks,
            "keyCells": key_cells,
            "requiredKeyCellChecks": required_key_cell_checks,
            "reconciliationChecks": reconciliation_checks,
            "formulaIntegrity": formula_integrity,
            "calculationEvidence": {
                "method": "saved_workbook_cache", "recalculated": False,
                "fullCalcOnLoad": getattr(formula_wb.calculation, "fullCalcOnLoad", None),
            },
            "definedNames": defined_names,
            "namedRangeValues": named_range_values,
            "schemaReadiness": schema_readiness,
            "recommendations": _recommendations(status, formula_integrity, schema_readiness, required_key_cell_checks),
        }
    finally:
        formula_wb.close()
        value_wb.close()


def _detect_profile(workbook: Any) -> Dict[str, Any]:
    defined_names = set(workbook.defined_names.keys())
    sheet_names = set(workbook.sheetnames)
    if {"\uc190\uc775", "\uac1c\uc694\uc218\uc785", "cash", "\uc784\ub300\ub8cc"}.issubset(sheet_names):
        return {
            "profile": "yongin-public-rental-feasibility",
            "required_sheets": [],
            "key_cells": {},
            "formula_integrity_sheets": [],
        }
    if "realytics_fs_schema_version" in defined_names or "07_Import계약" in sheet_names:
        return REALYTICS_FORMULA_WORKBOOK_PROFILE
    return LEGACY_BONRI_WORKBOOK_PROFILE


def _formula_value(value: Any) -> Any:
    return value.text if isinstance(value, ArrayFormula) else value


def _sheet_profiles(workbook: Any) -> List[Dict[str, Any]]:
    profiles = []
    for sheet in workbook.worksheets:
        formula_count = 0
        non_empty_count = 0
        for row in sheet.iter_rows():
            for cell in row:
                value = _formula_value(cell.value)
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
        cached_value = _json_safe_value(value_wb[sheet_name][cell_ref].value)
        formula_value = _formula_value(formula_wb[sheet_name][cell_ref].value)
        result[key] = {
            "sheetName": sheet_name,
            "cell": cell_ref,
            "value": cached_value,
            "formula": formula_value,
            "present": True,
            "hasCachedValue": cached_value not in (None, ""),
        }
    return result


def _required_key_cell_checks(key_cells: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    text_keys = {"project_name", "location"}
    positive_keys = {"land_area_py", "total_revenue", "total_revenue_after_vat", "total_cost"}
    checks = []
    for key, record in key_cells.items():
        cached_value = record.get("value")
        present = bool(record.get("present") and record.get("hasCachedValue"))
        numeric_required = key not in text_keys
        numeric_valid = bool(
            isinstance(cached_value, (int, float))
            and not isinstance(cached_value, bool)
            and math.isfinite(float(cached_value))
        ) if numeric_required else True
        positive_valid = bool(float(cached_value) > 0) if key in positive_keys and numeric_valid else key not in positive_keys
        passed = present and numeric_valid and positive_valid
        if not present:
            reason = "missing_cached_value"
        elif not numeric_valid:
            reason = "finite_numeric_value_required"
        elif not positive_valid:
            reason = "positive_value_required"
        else:
            reason = None
        checks.append({
            "key": key,
            "sheetName": record.get("sheetName"),
            "cell": record.get("cell"),
            "passed": passed,
            "numericRequired": numeric_required,
            "reason": reason,
            "severity": "ok" if passed else "blocking",
        })
    return checks


def _reconciliation_checks(key_cells: Dict[str, Dict[str, Any]], tolerance: float) -> List[Dict[str, Any]]:
    if not key_cells:
        return []

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


def _formula_integrity(
    formula_wb: Any,
    value_wb: Any,
    max_error_samples: int,
    included_sheet_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    cached_error_counts: Dict[str, int] = {}
    cached_error_samples: List[Dict[str, Any]] = []
    formula_ref_samples: List[Dict[str, Any]] = []
    formula_ref_count = 0
    formula_count = 0
    array_formula_count = 0
    volatile_formula_counts: Dict[str, int] = {}
    volatile_formula_samples: List[Dict[str, Any]] = []
    external_reference_count = 0
    external_reference_samples: List[Dict[str, Any]] = []
    included_sheet_set = set(included_sheet_names or [])

    for value_sheet in value_wb.worksheets:
        if included_sheet_set and value_sheet.title not in included_sheet_set:
            continue
        formula_sheet: Worksheet = formula_wb[value_sheet.title]
        for row in value_sheet.iter_rows():
            for value_cell in row:
                raw_formula = formula_sheet[value_cell.coordinate].value
                formula = _formula_value(raw_formula)
                if isinstance(raw_formula, ArrayFormula):
                    array_formula_count += 1
                if isinstance(formula, str) and formula.startswith("="):
                    formula_count += 1
                    for function_name in re.findall(r"\b(OFFSET|INDIRECT)\s*\(", formula, flags=re.IGNORECASE):
                        name = function_name.upper()
                        volatile_formula_counts[name] = volatile_formula_counts.get(name, 0) + 1
                        if len(volatile_formula_samples) < max_error_samples:
                            volatile_formula_samples.append({
                                "sheetName": value_sheet.title,
                                "cell": value_cell.coordinate,
                                "function": name,
                                "formula": formula,
                            })
                    if re.search(r"\[[^\]]+\.(?:xlsx|xlsm|xlsb|xls)\]", formula, flags=re.IGNORECASE):
                        external_reference_count += 1
                        if len(external_reference_samples) < max_error_samples:
                            external_reference_samples.append({
                                "sheetName": value_sheet.title,
                                "cell": value_cell.coordinate,
                                "formula": formula,
                            })
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
        "arrayFormulaCount": array_formula_count,
        "cachedErrorCounts": cached_error_counts,
        "formulaRefCount": formula_ref_count,
        "cachedErrorSamples": cached_error_samples,
        "formulaRefSamples": formula_ref_samples,
        "volatileFormulaCounts": volatile_formula_counts,
        "volatileFormulaCount": sum(volatile_formula_counts.values()),
        "volatileFormulaSamples": volatile_formula_samples,
        "externalWorkbookReferenceCount": external_reference_count,
        "externalWorkbookReferenceSamples": external_reference_samples,
        "hasExternalWorkbookReferences": external_reference_count > 0,
        "hasBlockingFormulaErrors": formula_ref_count > 0 or bool(cached_error_counts),
        "scopedSheetNames": sorted(included_sheet_set) if included_sheet_set else "all",
    }


def _defined_name_profiles(workbook: Any) -> List[Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    for name in workbook.defined_names.keys():
        try:
            destinations = [
                {"sheetName": sheet_name, "range": cell_range}
                for sheet_name, cell_range in workbook.defined_names[name].destinations
            ]
        except Exception:
            destinations = []
        profiles.append({"name": str(name), "destinations": destinations})
    return profiles


def _read_named_range_values(
    formula_wb: Any,
    value_wb: Any,
    required_named_ranges: List[str],
) -> Dict[str, Dict[str, Any]]:
    values: Dict[str, Dict[str, Any]] = {}
    for name in required_named_ranges:
        if name not in formula_wb.defined_names:
            values[name] = {
                "present": False,
                "destinations": [],
                "value": None,
                "formula": None,
            }
            continue

        destinations = list(formula_wb.defined_names[name].destinations)
        first_value = None
        first_formula = None
        for sheet_name, cell_range in destinations:
            if sheet_name not in formula_wb.sheetnames or ":" in cell_range:
                continue
            first_value = _json_safe_value(value_wb[sheet_name][cell_range].value)
            first_formula = _formula_value(formula_wb[sheet_name][cell_range].value)
            break

        values[name] = {
            "present": True,
            "destinations": [
                {"sheetName": sheet_name, "range": cell_range}
                for sheet_name, cell_range in destinations
            ],
            "value": first_value,
            "formula": first_formula,
        }
    return values


def _schema_readiness(workbook: Any, profile: Dict[str, Any]) -> Dict[str, Any]:
    sheet_names = set(workbook.sheetnames)
    required_present = all(sheet in sheet_names for sheet in profile["required_sheets"])
    table_names = []
    for sheet in workbook.worksheets:
        table_names.extend(list(sheet.tables.keys()) if hasattr(sheet, "tables") else [])

    required_named_ranges = profile.get("required_named_ranges", [])
    missing_required_named_ranges = [
        name for name in required_named_ranges
        if name not in workbook.defined_names
    ]
    realytics_named_ranges = [
        name for name in workbook.defined_names.keys()
        if str(name).startswith("fs_")
        or str(name).startswith("cf_")
        or str(name).startswith("realytics_")
    ]

    if profile["profile"] == REALYTICS_FORMULA_WORKBOOK_PROFILE["profile"]:
        expected_sections = {
            "site": "01_입력가정" in sheet_names,
            "program": "02A_분양수입" in sheet_names,
            "product_mix": "02A_분양수입" in sheet_names and "02B_수납스케줄" in sheet_names,
            "revenue": "02A_분양수입" in sheet_names and "02_사업수지" in sheet_names,
            "costs": "02_사업수지" in sheet_names,
            "financing": "03B_PF인출상환" in sheet_names and "03C_Tranche상환" in sheet_names,
            "monthly_cash_flow": "03_월별현금흐름" in sheet_names,
            "review": "04_검증" in sheet_names and "07_Import계약" in sheet_names,
        }
        direct_v2_import_ready = required_present and not missing_required_named_ranges
        requires_legacy_adapter = False
        notes = [
            "Workbook exposes REALYTICS named ranges for formula-auditable import.",
            "Full FeasibilityStudyV2 import still requires product-level table parsing and provenance mapping.",
        ]
    else:
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
        direct_v2_import_ready = len(realytics_named_ranges) > 0 and len(table_names) > 0
        requires_legacy_adapter = len(realytics_named_ranges) == 0 or len(table_names) == 0
        notes = [
            "The sample workbook conceptually covers FeasibilityStudyV2 sections.",
            "It does not use REALYTICS fs_* named ranges or Excel tables, so direct v2 import requires a legacy-cell-map adapter.",
        ]

    return {
        "requiredLegacySheetsPresent": required_present,
        "sectionsCovered": expected_sections,
        "realyticsNamedRangeCount": len(realytics_named_ranges),
        "requiredNamedRanges": required_named_ranges,
        "missingRequiredNamedRanges": missing_required_named_ranges,
        "excelTableCount": len(table_names),
        "directV2ImportReady": direct_v2_import_ready,
        "requiresLegacyAdapter": requires_legacy_adapter,
        "notes": notes,
    }


def _status(
    required_sheet_checks: List[Dict[str, Any]],
    required_key_cell_checks: List[Dict[str, Any]],
    reconciliation_checks: List[Dict[str, Any]],
    formula_integrity: Dict[str, Any],
    schema_readiness: Dict[str, Any],
) -> str:
    if any(not check["present"] for check in required_sheet_checks):
        return "failed"
    if any(not check["passed"] for check in required_key_cell_checks):
        return "failed"
    if schema_readiness["missingRequiredNamedRanges"]:
        return "failed"
    if any(not check["passed"] for check in reconciliation_checks):
        return "failed"
    if formula_integrity["hasBlockingFormulaErrors"]:
        return "failed"
    if formula_integrity.get("hasExternalWorkbookReferences"):
        return "failed"
    return "passed"


def _recommendations(
    status: str,
    formula_integrity: Dict[str, Any],
    schema_readiness: Dict[str, Any],
    required_key_cell_checks: List[Dict[str, Any]],
) -> List[str]:
    recommendations = []
    if formula_integrity.get("formulaRefCount", 0) > 0 or formula_integrity["cachedErrorCounts"].get("#REF!", 0) > 0:
        recommendations.append("Resolve #REF! formulas before accepting the workbook as an authoritative feasibility model.")
    other_cached_errors = {
        key: count
        for key, count in formula_integrity.get("cachedErrorCounts", {}).items()
        if key != "#REF!" and count
    }
    if other_cached_errors:
        recommendations.append(
            "Resolve cached Excel errors before canonical approval: "
            + ", ".join(f"{key}={count}" for key, count in sorted(other_cached_errors.items()))
            + "."
        )
    if formula_integrity["cachedErrorCounts"].get("#DIV/0!", 0) > 0:
        recommendations.append("Review #DIV/0! cells and confirm whether they are harmless blank-row artifacts or real calculation errors.")
    if formula_integrity.get("hasExternalWorkbookReferences"):
        recommendations.append("Resolve or explicitly archive external workbook references before canonical approval.")
    if formula_integrity.get("volatileFormulaCount", 0) > 0:
        recommendations.append("Inventory OFFSET/INDIRECT formulas and verify cached values before deterministic migration.")
    if any(not check["passed"] for check in required_key_cell_checks):
        recommendations.append("Populate all required key cells with cached values before import approval.")
    if schema_readiness["missingRequiredNamedRanges"]:
        recommendations.append("Add missing REALYTICS named ranges before accepting this workbook for canonical import.")
    if schema_readiness["requiresLegacyAdapter"]:
        recommendations.append("Build a legacy workbook adapter that maps the current Korean workbook cell addresses into FeasibilityStudyV2.")
    if schema_readiness["directV2ImportReady"]:
        recommendations.append("Workbook exposes the required REALYTICS import contract; next step is field-level FeasibilityStudyV2 mapping and provenance capture.")
    if status == "passed":
        recommendations.append("Workbook is structurally ready for import and agent-side metric comparison.")
    return recommendations


def _json_safe_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
