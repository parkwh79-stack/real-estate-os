"""Sample-informed cell contract for the Bonri-style feasibility workbook.

The workbook is formula-driven and uses Korean sheet names. This profile keeps
the stable business meaning and cell locator together so imported values and
CalculationRun inputs can be audited back to the source workbook.
"""

from __future__ import annotations

from typing import Any, Dict


LEGACY_BONRI_PROFILE_ID = "legacy-bonri-mixed-use-pf-feasibility-v4"

LEGACY_BONRI_CELL_MAP: Dict[str, Dict[str, Any]] = {
    "project.name": {"sheet": "입력", "cell": "D2", "kind": "source"},
    "site.address": {"sheet": "입력", "cell": "E5", "kind": "source"},
    "site.land_area_sqm": {"sheet": "입력", "cell": "E8", "kind": "source"},
    "site.effective_land_area_sqm": {"sheet": "입력", "cell": "E10", "kind": "calculated"},
    "raw_import.designFar": {"sheet": "입력", "cell": "E22", "kind": "calculated"},
    "cost.land_purchase_budget": {"sheet": "사업수지", "cell": "D19", "kind": "source", "unit": "KRW_THOUSAND"},
    "cost.land_inclusive_budget": {"sheet": "사업수지", "cell": "D26", "kind": "calculated", "unit": "KRW_THOUSAND"},
    "program.gross_floor_area_sqm": {"sheet": "입력", "cell": "F19", "kind": "calculated"},
    "revenue.total": {"sheet": "사업수지", "cell": "D11", "kind": "calculated", "unit": "KRW_THOUSAND"},
    "cost.total": {"sheet": "사업수지", "cell": "D72", "kind": "calculated", "unit": "KRW_THOUSAND"},
    "returns.development_profit": {"sheet": "사업수지", "cell": "D74", "kind": "calculated", "unit": "KRW_THOUSAND"},
    "product.apartment.revenue": {"sheet": "분양", "cell": "J21", "kind": "calculated", "unit": "KRW_THOUSAND"},
    "product.officetel.revenue": {"sheet": "분양", "cell": "J30", "kind": "calculated", "unit": "KRW_THOUSAND"},
    "product.commercial.revenue": {"sheet": "분양", "cell": "J40", "kind": "calculated", "unit": "KRW_THOUSAND"},
    "pf.tranche_senior": {"sheet": "2.현금흐름", "cell": "E48", "kind": "source", "unit": "KRW_MILLION"},
    "pf.tranche_mezzanine": {"sheet": "2.현금흐름", "cell": "E49", "kind": "source", "unit": "KRW_MILLION"},
    "pf.tranche_junior": {"sheet": "2.현금흐름", "cell": "E50", "kind": "source", "unit": "KRW_MILLION"},
    "pf.equity": {"sheet": "2.현금흐름", "cell": "E55", "kind": "source", "unit": "KRW_MILLION"},
    "cashflow.monthly_sales_rates": {"sheet": "2.현금흐름", "range": "J8:BQ10", "kind": "calculated"},
    "cashflow.monthly_progress_rates": {"sheet": "2.현금흐름", "range": "J5:BQ7", "kind": "calculated"},
    "cashflow.period_labels": {"sheet": "2.현금흐름", "range": "F4:BQ4", "kind": "source"},
    "cashflow.monthly_cash_flow": {"sheet": "2.현금흐름", "range": "F35:BQ119", "kind": "calculated", "unit": "KRW_MILLION"},
    "cashflow.cumulative_cash_flow": {"sheet": "2.현금흐름", "range": "F130:BQ130", "kind": "calculated", "unit": "KRW_MILLION"},
    "cashflow.construction_progress_curve": {"sheet": "공정율", "range": "A1:BQ76", "kind": "source"},
}


def legacy_bonri_source_locator(field: str, source_name: str | None = None) -> Dict[str, Any]:
    mapping = LEGACY_BONRI_CELL_MAP.get(field, {})
    locator = {
        "sourceType": "excel_cell" if mapping.get("cell") else "excel_range",
        "sourceName": source_name,
        "templateId": LEGACY_BONRI_PROFILE_ID,
        "canonicalField": field,
        **mapping,
    }
    return {key: value for key, value in locator.items() if value not in (None, "")}


def legacy_bonri_registry_context(source_name: str | None = None) -> Dict[str, Any]:
    return {
        "templateId": LEGACY_BONRI_PROFILE_ID,
        "sourceWorkbook": source_name,
        "fieldLocatorCount": len(LEGACY_BONRI_CELL_MAP),
        "fieldLocators": {
            field: legacy_bonri_source_locator(field, source_name)
            for field in LEGACY_BONRI_CELL_MAP
        },
    }
