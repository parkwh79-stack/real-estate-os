from __future__ import annotations

import copy
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel

from agents.feasibility_workbook_validator import validate_feasibility_workbook
from agents.legacy_bonri_workbook_profile import legacy_bonri_registry_context
from domains.feasibility.numeric import excel_round_int, to_decimal
from schemas.feasibility_study import FeasibilityStudyV2


KRW_PER_THOUSAND = 1_000
KRW_PER_MILLION = 1_000_000
SQM_PER_PY = 3.305785
GOVERNED_FACT_BASIS_CONTRACT_VERSION = (
    "realytics-workbook-governed-fact-basis-v1"
)
_GOVERNED_MONETARY_FIELD_KEYS = (
    "revenue.total_sales_revenue",
    "cost.total_project_cost",
    "financing.pf_amount",
)
_CONTROLLED_REALYTICS_SCHEMA_VERSION = "realytics-fs-xlsx-v1-draft"

# These are versioned workbook-profile meanings, not generic defaults.
# - Bonri is eligible only when the workbook itself states ``VAT 별도``.
# - REALYTICS is eligible only for the exact controlled schema version whose
#   import contract defines total revenue, summed project cost, and requested
#   PF / tranche principal.
_PROFILE_GOVERNED_FACT_BASIS = {
    "legacy-bonri-mixed-use-pf-feasibility": {
        "revenue.total_sales_revenue": {
            "vatTreatment": "excluded",
            "amountScope": "gross",
            "allocationBasis": "direct",
            "currency": "KRW",
        },
        "cost.total_project_cost": {
            "vatTreatment": "excluded",
            "amountScope": "gross",
            "allocationBasis": "mixed",
            "allocationMethod": "sum_of_profile_cost_categories",
            "currency": "KRW",
        },
        "financing.pf_amount": {
            "vatTreatment": "not_applicable",
            "amountScope": "not_applicable",
            "allocationBasis": "not_applicable",
            "financingBalanceBasis": "commitment",
            "currency": "KRW",
        },
    },
    "realytics-formula-cash-flow-export": {
        "revenue.total_sales_revenue": {
            "vatTreatment": "excluded",
            "amountScope": "gross",
            "allocationBasis": "direct",
            "currency": "KRW",
        },
        "cost.total_project_cost": {
            "vatTreatment": "excluded",
            "amountScope": "gross",
            "allocationBasis": "mixed",
            "allocationMethod": "sum_of_profile_cost_categories",
            "currency": "KRW",
        },
        "financing.pf_amount": {
            "vatTreatment": "not_applicable",
            "amountScope": "not_applicable",
            "allocationBasis": "not_applicable",
            "financingBalanceBasis": "commitment",
            "currency": "KRW",
        },
    },
}

# Bonri cash-flow rows are not sign-coded transactions. Operating receipts,
# financing transfers, operating costs, debt repayment, and equity recovery
# must remain distinct or the Cash Flow Engine will incorrectly refinance
# principal repayment and sponsor distributions as project costs.
LEGACY_CF_ROW_KIND = {
    **{row: "cash_in" for row in (35, 36, 41, 46, 54)},
    **{row: "pf_draw" for row in (48, 49, 50, 51, 53)},
    52: "other_financing_inflow",
    55: "equity_contribution",
    **{row: "cash_out" for row in (*range(58, 69), *range(70, 93), *range(97, 108), 109, 110, *range(112, 120))},
    **{row: "loan_repayment" for row in (*range(93, 97), 108)},
    111: "equity_recovery",
}
LEGACY_CF_OPPOSITE_KIND = {
    "cash_in": "cash_out",
    "cash_out": "cash_in",
    "pf_draw": "loan_repayment",
    "loan_repayment": "pf_draw",
    "equity_contribution": "equity_recovery",
    "equity_recovery": "equity_contribution",
    "other_financing_inflow": "other_financing_outflow",
    "other_financing_outflow": "other_financing_inflow",
}


def import_feasibility_study_from_workbook(
    workbook_path: str | Path,
    *,
    project_id: Optional[str] = None,
    source_document: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a FeasibilityStudyV2 draft from a validated workbook.

    The importer is deliberately conservative. It maps stable sample-informed cells
    and REALYTICS named ranges, records provenance, and leaves unsupported fields in
    raw_import instead of inventing values.
    """
    workbook_path = Path(workbook_path)
    validation = validate_feasibility_workbook(workbook_path)
    profile = validation.get("profile")

    if profile == "realytics-formula-cash-flow-export":
        draft = _import_realytics_formula_export(workbook_path, validation, project_id, source_document)
    elif profile in {"sangbong-sale-feasibility", "sangbong-private-rental-feasibility"}:
        from agents.sangbong_workbook_profile import import_sangbong_workbook

        draft = import_sangbong_workbook(workbook_path, validation, project_id, source_document)
    elif profile == "yongin-public-rental-feasibility":
        draft = _import_yongin_public_rental(workbook_path, validation, project_id, source_document)
    elif profile == "legacy-bonri-mixed-use-pf-feasibility":
        draft = _import_legacy_bonri(workbook_path, validation, project_id, source_document)
    else:
        draft = _minimal_draft(project_id, validation, source_document)

    return FeasibilityStudyV2.model_validate(draft).model_dump(mode="json")


def _import_yongin_public_rental(workbook_path: Path, validation: Dict[str, Any], project_id: Optional[str], source_document: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministic adapter for the 손익/개요수입/cash/임대료 workbook family."""
    workbook = load_workbook(workbook_path, data_only=True, read_only=False)
    try:
        profit, overview, cash, _rent = [workbook[name] for name in workbook.sheetnames[:4]]
        source_name = _source_name(workbook_path, source_document)
        total_revenue = (_to_float(profit["I24"].value) or 0) * KRW_PER_THOUSAND
        total_cost = (_to_float(profit["I81"].value) or 0) * KRW_PER_THOUSAND
        development_profit = (_to_float(profit["I82"].value) or 0) * KRW_PER_THOUSAND
        pf_amount = (_to_float(cash["D34"].value) or 0) * KRW_PER_THOUSAND
        land_area = _to_float(overview["D2"].value)
        gross_area = _to_float(overview["D15"].value)
        unit_count = _to_float(overview["B26"].value)
        product_mix = []
        for row in range(18, 26):
            units = _to_float(overview.cell(row, 2).value)
            if not units:
                continue
            product_mix.append({
                "key": f"apartment_type_{row - 17}",
                "asset_type": "apartment",
                "product_name": f"Apartment type {row - 17}",
                "unit_count": units,
                "exclusive_area_sqm": _to_float(overview.cell(row, 3).value),
                "contract_area_sqm": _to_float(overview.cell(row, 5).value),
                "provenance": _provenance(source_name, "xlsx", overview.title, f"A{row}:M{row}", None),
            })
        # TODO-SDD: this profile needs a reviewed transaction-row map. Whole-column
        # summation double-counts subtotals, balances and financing transfers.
        monthly_cash_flow = []
        start_match = re.search(r"착공\s*(\d{4})년\s*(\d{1,2})월", str(profit["A3"].value or ""))
        schedule = []
        if start_match and 1 <= int(start_match[2]) <= 12:
            schedule.append({"key": "construction_start", "label": "construction start",
                "planned_date": f"{start_match[1]}-{int(start_match[2]):02d}",
                "provenance": _provenance(source_name, "xlsx", profit.title, "A3", None)})
        metrics = _metrics(total_revenue, total_cost, development_profit, pf_amount, None)
        # These require a verified timing, collateral or repayment basis. Revenue
        # is not collateral value; total cost minus PF is not peak equity need.
        for key in ("required_equity", "max_minus", "ltv", "exit_sale_rate", "break_even_sale_rate"):
            metrics[key] = None
        raw_import = {
            "profile": validation.get("profile"),
            "validationStatus": validation.get("status"),
            "sourceWorkbook": source_name,
            "sourceDocument": source_document or {},
            "unitScale": {profit.title: "KRW_THOUSAND", cash.title: "KRW_THOUSAND"},
            "unresolvedFields": ["monthly_cash_flow", "max_minus", "required_equity", "ltv", "exit_sale_rate", "break_even_sale_rate"],
            "mappedCells": {
                "grossFloorArea": f"{overview.title}!D15",
                "totalRevenue": f"{profit.title}!I24",
                "totalCost": f"{profit.title}!I81",
                "developmentProfit": f"{profit.title}!I82",
                "pfAmount": f"{cash.title}!D34",
                "productMix": f"{overview.title}!A18:M25",
            },
            "limitations": [
                "Product-level rental conversion and legal approval conditions remain subject to review.",
                "Monthly transactions, peak funding, equity, collateral LTV and exit repayment require reviewed source mappings."
            ],
        }
        _attach_profile_governed_fact_basis(raw_import)
        return {
            "project_id": project_id,
            "project_name": _cell_value(profit, "A1"),
            "asset_type": "mixed_use",
            "site": {"land_area_sqm": _value("land_area_sqm", "land area", land_area, "SQM", overview.title, "D2", source_name), "zoning": _cell_value(overview, "D8")},
            "program": {"gross_floor_area_sqm": _value("gross_floor_area_sqm", "gross floor area", gross_area, "SQM", overview.title, "D15", source_name), "unit_count": _value("unit_count", "unit count", unit_count, "COUNT", overview.title, "B26", source_name)},
            "product_mix": product_mix,
            "revenue_items": [_line_item("total_revenue", "Total revenue", "sales_and_rental_revenue", total_revenue, profit.title, "I24", source_name)],
            "cost_items": [_line_item("total_cost", "Total project cost", "total_project_cost", total_cost, profit.title, "I81", source_name)],
            "financing": [{"tranche_id": "pf", "name": "PF", "priority": "senior", "amount": pf_amount, "excel_row_id": f"{cash.title}!D34"}],
            "schedule": schedule,
            "monthly_cash_flow": monthly_cash_flow,
            "metrics": metrics,
            "raw_import": raw_import,
        }
    finally:
        workbook.close()


def _import_legacy_bonri(
    workbook_path: Path,
    validation: Dict[str, Any],
    project_id: Optional[str],
    source_document: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    workbook = load_workbook(workbook_path, data_only=True, read_only=True)
    try:
        business = workbook["사업수지"]
        input_sheet = workbook["입력"]
        sales = workbook["분양"]
        cash_flow = workbook["2.현금흐름"]

        source_name = _source_name(workbook_path, source_document)
        project_name = _cell_value(business, "C3")
        address = _cell_value(business, "F3")
        land_area_sqm = _to_float(_cell_value(input_sheet, "E8"))
        gross_floor_area_sqm = _to_float(_cell_value(input_sheet, "F19"))
        saleable_area_sqm = _to_float(_cell_value(input_sheet, "M8"))
        contract_area_sqm = _py_to_sqm(_to_float(_cell_value(business, "E6")))
        unit_count = _to_float(_cell_value(sales, "I21")) + _to_float(_cell_value(sales, "I30")) + _commercial_unit_count(sales)
        design_far_raw = _to_float(_cell_value(input_sheet, "E22"))
        design_far = design_far_raw * 100 if design_far_raw is not None and design_far_raw < 100 else design_far_raw

        total_revenue = _krw_from_thousand(_cell_value(business, "D11"))
        total_cost = _krw_from_thousand(_cell_value(business, "D72"))
        development_profit = _krw_from_thousand(_cell_value(business, "D74"))
        pf_amount = _pf_amount_from_cash_flow(cash_flow)
        financing, financing_review = _legacy_financing(cash_flow, input_sheet, source_name)
        schedule = _legacy_schedule(input_sheet, source_name)

        cost_items = [
            _line_item("land_cost", "대지비", "land", _krw_from_thousand(_cell_value(business, "D26")), "사업수지", "D26", source_name),
            _line_item("construction_cost", "공사비", "construction", _krw_from_thousand(_cell_value(business, "D18")), "사업수지", "D18", source_name),
            _line_item("service_cost", "용역비", "indirect", _krw_from_thousand(_cell_value(business, "D33")), "사업수지", "D33", source_name),
            _line_item("sales_marketing_cost", "분양제경비", "sales_marketing", _krw_from_thousand(_cell_value(business, "D41")), "사업수지", "D41", source_name),
            _line_item("levy_and_charges", "각종부담금", "tax_and_dues", _krw_from_thousand(_cell_value(business, "D52")), "사업수지", "D52", source_name),
            _line_item("finance_cost", "금융비용", "finance", _krw_from_thousand(_cell_value(business, "D62")), "사업수지", "D62", source_name),
            _line_item("other_cost", "기타사업비", "other", _krw_from_thousand(_cell_value(business, "D71")), "사업수지", "D71", source_name),
        ]
        raw_import = {
            "profile": validation.get("profile"),
            "sampleProfile": legacy_bonri_registry_context(source_name),
            "validationStatus": validation.get("status"),
            "sourceWorkbook": source_name,
            "unitScale": {
                "사업수지": "KRW_THOUSAND",
                "2.현금흐름": "KRW_MILLION",
            },
            "sourceDocument": source_document or {},
            "financingTermReview": financing_review,
            "cashFlowPeriodBasis": _legacy_cash_flow_period_basis(cash_flow, source_name, schedule),
            "designFar": _value("design_far", "설계 용적률", design_far, "PERCENT", "입력", "E22", source_name),
            # These are source budget scopes, not additional cost line items.
            # Keeping the purchase component out of cost_items avoids counting
            # it twice alongside the inclusive land-cost subtotal.
            "landCostBreakdown": {
                "purchaseBudget": _value("land_purchase_budget", "순수 토지 매입예산", _krw_from_thousand(_cell_value(business, "D19")), "KRW", "사업수지", "D19", source_name),
                "inclusiveBudget": _value("land_inclusive_budget", "부대비 포함 대지비", _krw_from_thousand(_cell_value(business, "D26")), "KRW", "사업수지", "D26", source_name),
            },
            "mappedCells": {
                "grossFloorArea": "입력!F19",
                "totalRevenue": "사업수지!D11",
                "totalCost": "사업수지!D72",
                "developmentProfit": "사업수지!D74",
                "pfAmount": "2.현금흐름!E48:E51",
                "productMix": "분양!21,30,40 and 입력!M5:M8",
            },
            "limitations": [
                "Legacy sample workbook has no REALYTICS named ranges; mapping uses a fixed Bonri-style cell profile.",
                "Workbook rates and fees are review candidates, not verified loan terms. Legal priority, maturity basis, floating rates and repayment conditions require the actual agreement.",
            ],
        }
        _attach_profile_governed_fact_basis(
            raw_import,
            legacy_vat_statement=_cell_value(business, "A2"),
        )

        return {
            "project_id": project_id,
            "project_name": project_name,
            "asset_type": "mixed_use",
            "site": {
                "address": address,
                "land_area_sqm": _value("land_area_sqm", "대지면적", land_area_sqm, "SQM", "입력", "E8", source_name),
                "zoning": _cell_value(input_sheet, "E6"),
            },
            "program": {
                "gross_floor_area_sqm": _value("gross_floor_area_sqm", "연면적", gross_floor_area_sqm, "SQM", "입력", "F19", source_name),
                "saleable_area_sqm": _value("saleable_area_sqm", "전용면적 합계", saleable_area_sqm, "SQM", "입력", "M8", source_name),
                "contract_area_sqm": _value("contract_area_sqm", "계약면적", contract_area_sqm, "SQM", "사업수지", "E6", source_name),
                "unit_count": _value("unit_count", "세대/호실수", unit_count, "COUNT", "분양", "I21,I30,I32:I33", source_name),
            },
            "product_mix": _legacy_product_mix(sales, input_sheet, source_name),
            "revenue_items": [
                _line_item("sales_revenue", "분양수입", "sales_revenue", total_revenue, "사업수지", "D11", source_name)
            ],
            "cost_items": cost_items,
            "financing": financing,
            "schedule": schedule,
            "scenarios": _default_scenarios(),
            "monthly_cash_flow": _legacy_monthly_cash_flow(cash_flow, source_name),
            "metrics": _metrics(total_revenue, total_cost, development_profit, pf_amount, _max_minus_from_cash_flow(cash_flow)),
            "raw_import": raw_import,
        }
    finally:
        workbook.close()


def _import_realytics_formula_export(
    workbook_path: Path,
    validation: Dict[str, Any],
    project_id: Optional[str],
    source_document: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    named = validation.get("namedRangeValues", {})
    source_name = _source_name(workbook_path, source_document)
    workbook = load_workbook(workbook_path, data_only=True, read_only=True)

    try:
        schema_version = (
            named.get("realytics_fs_schema_version") or {}
        ).get("value")
        total_revenue = _to_float((named.get("fs_total_revenue") or {}).get("value"))
        total_cost = _to_float((named.get("fs_total_project_cost") or {}).get("value"))
        pf_amount = _to_float((named.get("fs_pf_loan") or {}).get("value"))
        if total_revenue and total_revenue < 10_000_000:
            total_revenue *= 100_000_000
        if total_cost and total_cost < 10_000_000:
            total_cost *= 100_000_000
        if pf_amount and pf_amount < 10_000_000:
            pf_amount *= 100_000_000

        product_monthly_scenarios = _realytics_product_monthly_sales_rate_scenarios(
            workbook,
            source_name,
            total_revenue,
        )
        limitations = []
        if not product_monthly_scenarios:
            limitations.append(
                "Product-level monthly sales-rate scenario rows were not found; only core metrics and named ranges were imported."
            )

        development_profit = total_revenue - total_cost if total_revenue and total_cost else None
        raw_import = {
            "profile": validation.get("profile"),
            "validationStatus": validation.get("status"),
            "sourceWorkbook": source_name,
            "schemaVersion": schema_version,
            "namedRangeValues": named,
            "mappedCells": {
                key: value
                for key, value in {
                    "totalRevenue": _named_range_anchor(
                        named, "fs_total_revenue"
                    ),
                    "totalCost": _named_range_anchor(
                        named, "fs_total_project_cost"
                    ),
                    "pfAmount": _named_range_anchor(named, "fs_pf_loan"),
                }.items()
                if value is not None
            },
            "productMonthlySalesRateScenarios": product_monthly_scenarios,
            "sourceDocument": source_document or {},
            "limitations": limitations,
        }
        _attach_profile_governed_fact_basis(
            raw_import,
            schema_version=str(schema_version or ""),
        )
        draft = {
            "project_id": project_id,
            "project_name": None,
            "asset_type": "mixed_use",
            "revenue_items": [
                _line_item("sales_revenue", "분양수입", "sales_revenue", total_revenue, "01_입력가정", "C6", source_name, "fs_total_revenue")
            ],
            "cost_items": [
                _line_item("total_project_cost", "총사업비", "total_project_cost", total_cost, "02_사업수지", "C13", source_name, "fs_total_project_cost")
            ],
            "financing": [
                {
                    "tranche_id": "pf_loan",
                    "name": "PF Loan",
                    "priority": "senior",
                    "amount": pf_amount,
                    "repayment_source": "Sales proceeds",
                    "excel_row_id": "fs_pf_loan",
                }
            ] if pf_amount else [],
            "metrics": _metrics(total_revenue, total_cost, development_profit, pf_amount, _to_float((named.get("cf_max_minus") or {}).get("value"))),
            "raw_import": raw_import,
        }
        return draft
    finally:
        workbook.close()


def _legacy_product_mix(sales: Any, input_sheet: Any, source_name: str) -> List[Dict[str, Any]]:
    rows = [
        {
            "key": "apartment",
            "asset_type": "apartment",
            "product_name": "아파트",
            "unit_count": _to_float(_cell_value(sales, "I21")),
            "exclusive_area_sqm": _to_float(_cell_value(input_sheet, "M5")),
            "saleable_area_sqm": _py_to_sqm(_to_float(_cell_value(sales, "C21"))),
            "contract_area_sqm": _py_to_sqm(_to_float(_cell_value(sales, "E21"))),
            "revenue": _krw_from_thousand(_cell_value(sales, "J21")),
            "excel_row_id": "분양!21",
            "provenance": _provenance(source_name, "xlsx", "분양", "J21", "아파트 소계"),
        },
        {
            "key": "officetel",
            "asset_type": "officetel",
            "product_name": "오피스텔",
            "unit_count": _to_float(_cell_value(sales, "I30")),
            "exclusive_area_sqm": _to_float(_cell_value(input_sheet, "M6")),
            "saleable_area_sqm": _py_to_sqm(_to_float(_cell_value(sales, "C30"))),
            "contract_area_sqm": _py_to_sqm(_to_float(_cell_value(sales, "E30"))),
            "revenue": _krw_from_thousand(_cell_value(sales, "J30")),
            "excel_row_id": "분양!30",
            "provenance": _provenance(source_name, "xlsx", "분양", "J30", "오피스텔 소계"),
        },
        {
            "key": "commercial",
            "asset_type": "commercial",
            "product_name": "근린생활시설",
            "unit_count": _commercial_unit_count(sales),
            "exclusive_area_sqm": _to_float(_cell_value(input_sheet, "M7")),
            "saleable_area_sqm": _py_to_sqm(_to_float(_cell_value(sales, "C40"))),
            "contract_area_sqm": _py_to_sqm(_to_float(_cell_value(sales, "E40"))),
            "revenue": _krw_from_thousand(_cell_value(sales, "J40")),
            "excel_row_id": "분양!40",
            "provenance": _provenance(source_name, "xlsx", "분양", "J40", "상가 소계"),
        },
    ]
    for row in rows:
        area_py = (row.get("contract_area_sqm") or 0) / SQM_PER_PY
        revenue = row.get("revenue") or 0
        row["avg_sale_price_per_py"] = revenue / area_py if area_py else None
        row["avg_sale_price_per_sqm"] = revenue / (row.get("contract_area_sqm") or 0) if row.get("contract_area_sqm") else None
    return rows


def _legacy_financing(cash_flow: Any, input_sheet: Any, source_name: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    tranche_rows = [
        ("tr_a", "Tr.A", "senior", "E48"),
        ("tr_b", "Tr.B", "mezzanine", "E49"),
        ("tr_c", "Tr.C", "junior", "E50"),
        ("tr_d", "Tr.D", "other", "E51"),
    ]
    result, reviews = [], []
    for index, (tranche_id, name, priority, cell) in enumerate(tranche_rows):
        column = "DEFG"[index]
        header_matches = str(_cell_value(input_sheet, f"{column}129") or "").strip() == name
        amount_unit_matches = "백만원" in str(_cell_value(input_sheet, "C130") or "").replace(" ", "")
        fields = {}
        for key, row, unit in (("amount", 130, "KRW_MILLION"), ("interest_rate", 131, "RATIO"), ("fee_rate", 132, "RATIO")):
            source = input_sheet[f"{column}{row}"]
            raw = source.value
            number = _to_float(raw)
            valid = number is not None and math.isfinite(number) and number >= 0
            context_matches = header_matches and (amount_unit_matches if key == "amount" else "%" in str(source.number_format or ""))
            if key != "amount":
                valid = valid and number <= 1
            valid = valid and context_matches
            fields[key] = {
                "value": (_krw_from_million(number) if key == "amount" else number) if valid else None,
                "rawValue": str(raw) if isinstance(raw, float) and not math.isfinite(raw) else raw if isinstance(raw, (str, int, float, bool)) or raw is None else str(raw),
                "sourceCell": f"입력!{column}{row}", "sourceUnit": unit,
                "status": "read" if valid else "missing" if raw is None else "needs_review",
            }
        amount = _krw_from_million(_cell_value(cash_flow, cell))
        input_amount = fields["amount"]["value"]
        reconciled = amount is not None and input_amount is not None and amount == input_amount
        reviews.append({
            "trancheId": tranche_id, "name": name, "fields": fields,
            "cashFlowAmount": amount, "cashFlowSourceCell": f"2.현금흐름!{cell}",
            "principalReconciled": reconciled,
            "priorityBasis": "legacy_profile_assumption", "repaymentBasis": "legacy_profile_assumption",
            "unconfirmedTerms": ["법적 상환순위", "만기와 기산일", "고정·변동금리 조건", "수수료 부과 기준", "인출·상환 조건"],
        })
        # Preserve an explicitly zero commitment; a blank tranche is not zero.
        if amount or (reconciled and input_amount == 0):
            result.append({
                "tranche_id": tranche_id,
                "name": name,
                "priority": priority,
                "amount": amount,
                "interest_rate": fields["interest_rate"]["value"] if reconciled else None,
                "fee_rate": fields["fee_rate"]["value"] if reconciled else None,
                "repayment_source": "분양수입금",
                "excel_row_id": f"2.현금흐름!{cell}",
            })
    equity = _krw_from_million(_cell_value(cash_flow, "E55"))
    if equity:
        result.append({
            "tranche_id": "equity",
            "name": "시행자투자자금",
            "priority": "equity",
            "amount": equity,
            "excel_row_id": "2.현금흐름!E55",
        })
    return result, {
        "schemaVersion": "bonri-workbook-financing-review-v1",
        "sourceWorkbook": source_name, "status": "needs_review", "approvalStatus": "not_requested",
        "terms": reviews,
    }


def _legacy_schedule(input_sheet: Any, source_name: str) -> List[Dict[str, Any]]:
    start_date, start_cell = _schedule_date_with_source(input_sheet, "E25", "D25")
    completion_date, completion_cell = _schedule_date_with_source(input_sheet, "E26", "D26")
    duration = _to_float(_cell_value(input_sheet, "E24"))
    if duration is None:
        duration = _to_float(_cell_value(input_sheet, "D24"))
    return [
        {
            "key": "construction_start",
            "label": "착공일",
            "planned_date": start_date,
            "month_index": 1,
            "excel": {"sheet_name": "입력", "cell": start_cell},
            "provenance": _provenance(source_name, "xlsx", "입력", start_cell, None),
        },
        {
            "key": "completion",
            "label": "준공일",
            "planned_date": completion_date,
            "month_index": int(duration) if duration else None,
            "excel": {"sheet_name": "입력", "cell": completion_cell},
            "provenance": _provenance(source_name, "xlsx", "입력", completion_cell, None),
        },
    ]


def _schedule_date_with_source(
    input_sheet: Any,
    value_cell: str,
    legacy_cell: str,
) -> tuple[Optional[str], str]:
    """Read a schedule date from the value column, then a legacy inline cell.

    Bonri-style workbooks place the labels in column D and their values in
    column E. Older derived fixtures stored the dates directly in column D.
    Only a value that can be normalized as a date is accepted, so labels such
    as ``▶착공일:`` can never flow into downstream month calculations.
    """
    for cell in (value_cell, legacy_cell):
        normalized = _date_value(_cell_value(input_sheet, cell))
        if normalized is not None:
            return normalized, cell
    return None, value_cell


def _metrics(
    total_revenue: Optional[float],
    total_cost: Optional[float],
    development_profit: Optional[float],
    pf_amount: Optional[float],
    max_minus: Optional[float],
) -> Dict[str, Any]:
    total_revenue = total_revenue or 0
    total_cost = total_cost or 0
    development_profit = development_profit if development_profit is not None else total_revenue - total_cost
    return {
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "development_profit": development_profit,
        "profit_margin": development_profit / total_revenue if total_revenue else None,
        "profit_on_cost": development_profit / total_cost if total_cost else None,
        "break_even_sale_rate": total_cost / total_revenue if total_revenue else None,
        "required_equity": max(0, total_cost - (pf_amount or 0)) if total_cost else None,
        "max_minus": max_minus,
        "ltv": (pf_amount or 0) / total_revenue if total_revenue else None,
        "ltc": (pf_amount or 0) / total_cost if total_cost else None,
        "exit_sale_rate": (pf_amount or 0) / total_revenue if total_revenue else None,
    }


_LEGACY_CF_EVENT_LABELS = frozenset({"토지계약", "교평등", "사업승인", "분양"})


def _legacy_cash_flow_period_basis(
    cash_flow: Any, source_name: str, schedule: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Keep source-column order separate from the workbook's monthly clock.

    Four named events precede month 1 in the original sample. Their column
    positions carry no evidence of elapsed time. Row 2 owns monthly numbers;
    row 4 is only the display label and can contain ``준공+1`` instead of 43.
    """
    start_col = 6
    end_col = _last_period_column(cash_flow, start_col=start_col, header_row=4)
    headers = list(cash_flow.iter_rows(
        min_row=2, max_row=4, min_col=start_col, max_col=end_col, values_only=True,
    )) if end_col is not None else []
    month_headers = headers[0] if headers else ()
    labels = headers[2] if len(headers) > 2 else ()
    periods = []
    for offset, label in enumerate(labels):
        raw_month = month_headers[offset] if offset < len(month_headers) else None
        source_month = _source_month_number(raw_month)
        display_label = "" if label is None else str(label)
        is_event = display_label.strip() in _LEGACY_CF_EVENT_LABELS and raw_month in (None, "")
        column = _column_name(start_col + offset)
        periods.append({
            "sourcePeriodIndex": offset,
            "label": display_label,
            "periodKind": "event" if is_event else "monthly" if source_month is not None else "unknown",
            "sourceMonthNumber": source_month,
            "sourceLocator": {"sourceName": source_name, "sheet": "2.현금흐름",
                              "cell": f"{column}4", "monthCell": f"{column}2"},
        })
    warnings = []
    monthly_rows = [row for row in periods if row["periodKind"] != "event"]
    source_months = [row["sourceMonthNumber"] for row in monthly_rows]
    monthly_sequence_valid = bool(monthly_rows) and source_months == list(range(1, len(monthly_rows) + 1))
    if not monthly_sequence_valid:
        warnings.append("원본 월 헤더가 누락·중복·불연속되어 일정과 현금흐름 기간의 대응을 확인할 수 없습니다. 원본 2행 월 번호를 확인해 주세요.")
        for row in monthly_rows:
            row["periodKind"] = "unknown"
    # Events embedded within the monthly sequence are not evidence of a valid
    # prefix. Keep their original labels and numbers, but do not bind schedules.
    first_monthly = next((index for index, row in enumerate(periods) if row["periodKind"] != "event"), len(periods))
    if any(row["periodKind"] == "event" for row in periods[first_monthly:]):
        monthly_sequence_valid = False
        warnings.append("월별 기간 사이에 사건 열이 있어 원본 기간 순서를 확인해야 합니다.")
        for row in periods[first_monthly:]:
            row["periodKind"] = "unknown"
    if any(row["periodKind"] == "event" for row in periods):
        warnings.append("사전 사건의 실제 날짜와 경과 기간은 미확인입니다. 원본 열 순번을 한 달씩의 기간으로 계산하지 않습니다.")
    by_month = {row["sourceMonthNumber"]: row["sourcePeriodIndex"] for row in periods
                if row["periodKind"] == "monthly"} if monthly_sequence_valid else {}
    bindings = {}
    for event in schedule:
        source_month = _source_month_number(event.get("month_index"))
        source_index = by_month.get(source_month)
        bindings[event["key"]] = {
            "sourcePeriodIndex": source_index,
            "sourceMonthNumber": source_month if source_index is not None else None,
        }
    return {"schemaVersion": "cash-flow-period-basis-v1", "kind": "source_sequence",
            "periods": periods, "scheduleBindings": bindings, "warnings": warnings}


def _source_month_number(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return int(value) if re.fullmatch(r"[1-9]\d*", value) else None
    if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 and int(value) == value:
        return int(value)
    return None


def _legacy_monthly_cash_flow(cash_flow: Any, source_name: str) -> List[Dict[str, Any]]:
    """Normalize the source-backed legacy transaction rows into KRW.

    The source workbook stores four pre-month events plus 60 monthly periods in
    F:BQ, followed by non-period total/check columns. The last period is derived
    from row 4 instead of a hard-coded worksheet maximum. Rows 120:158 are
    subtotals, account balances, or assumptions and are not re-imported as
    transactions.
    """
    rows: List[Dict[str, Any]] = []
    start_col = 6
    end_col = _last_period_column(cash_flow, start_col=start_col, header_row=4)
    if end_col is None:
        return rows
    start_row, end_row = 35, min(int(getattr(cash_flow, "max_row", 119) or 119), 119)
    # ``iter_rows`` is important here: repeated random access on an
    # openpyxl read-only worksheet is disproportionately slow for legacy
    # workbooks with large shared-string tables.
    period_row = next(cash_flow.iter_rows(min_row=4, max_row=4, min_col=start_col, max_col=end_col, values_only=True), ())
    source_rows = list(cash_flow.iter_rows(min_row=start_row, max_row=end_row, min_col=2, max_col=end_col, values_only=True))
    current_cash_row = next(cash_flow.iter_rows(min_row=122, max_row=122, min_col=start_col, max_col=end_col, values_only=True), ())
    carry_cash_row = next(cash_flow.iter_rows(min_row=123, max_row=123, min_col=start_col, max_col=end_col, values_only=True), ())
    financing_adjustment_row = next(cash_flow.iter_rows(min_row=129, max_row=129, min_col=start_col, max_col=end_col, values_only=True), ())
    cumulative_cash_row = next(cash_flow.iter_rows(min_row=130, max_row=130, min_col=start_col, max_col=end_col, values_only=True), ())
    prior_source_cumulative_krw: Optional[int] = None
    for col in range(start_col, end_col + 1):
        month_index = col - start_col
        period_offset = col - start_col
        period = period_row[period_offset] if period_offset < len(period_row) else None
        period = period or f"M{month_index}"
        cash_in: Dict[str, float] = {}
        cash_out: Dict[str, float] = {}
        pf_draw: Dict[str, float] = {}
        loan_repayment: Dict[str, float] = {}
        equity_contribution: Dict[str, float] = {}
        equity_recovery: Dict[str, float] = {}
        other_financing_inflow: Dict[str, float] = {}
        other_financing_outflow: Dict[str, float] = {}
        buckets = {
            "cash_in": cash_in,
            "cash_out": cash_out,
            "pf_draw": pf_draw,
            "loan_repayment": loan_repayment,
            "equity_contribution": equity_contribution,
            "equity_recovery": equity_recovery,
            "other_financing_inflow": other_financing_inflow,
            "other_financing_outflow": other_financing_outflow,
        }
        for row_offset, values in enumerate(source_rows):
            row = start_row + row_offset
            kind = LEGACY_CF_ROW_KIND.get(row)
            if kind is None:
                continue
            # values begins at column B; the month value is offset by one
            # because J is the tenth worksheet column.
            value_offset = col - 2
            value = _to_float(values[value_offset]) if value_offset < len(values) else None
            if value is None or value == 0:
                continue
            label = next((item for item in values[:3] if item not in (None, "", 0)), f"row_{row}")
            key = f"row_{row}_{str(label).strip()}"
            normalized_krw = excel_round_int(to_decimal(value) * KRW_PER_MILLION)
            target_kind = kind if normalized_krw >= 0 else LEGACY_CF_OPPOSITE_KIND[kind]
            target = buckets[target_kind]
            target[key] = abs(normalized_krw)
        total_in = (
            sum(cash_in.values())
            + sum(pf_draw.values())
            + sum(equity_contribution.values())
            + sum(other_financing_inflow.values())
        )
        total_out = (
            sum(cash_out.values())
            + sum(loan_repayment.values())
            + sum(equity_recovery.values())
            + sum(other_financing_outflow.values())
        )
        net_cash_flow = total_in - total_out
        source_current = _to_float(current_cash_row[period_offset]) if period_offset < len(current_cash_row) else None
        source_carry = _to_float(carry_cash_row[period_offset]) if period_offset < len(carry_cash_row) else None
        source_financing_adjustment = _to_float(financing_adjustment_row[period_offset]) if period_offset < len(financing_adjustment_row) else None
        source_cumulative = _to_float(cumulative_cash_row[period_offset]) if period_offset < len(cumulative_cash_row) else None
        expected_current_krw = excel_round_int(to_decimal(source_current) * KRW_PER_MILLION) if source_current is not None else None
        source_carry_krw = excel_round_int(to_decimal(source_carry) * KRW_PER_MILLION) if source_carry is not None else None
        source_financing_adjustment_krw = excel_round_int(to_decimal(source_financing_adjustment) * KRW_PER_MILLION) if source_financing_adjustment is not None else None
        source_cumulative_krw = excel_round_int(to_decimal(source_cumulative) * KRW_PER_MILLION) if source_cumulative is not None else None
        expected_cumulative_krw = (
            source_carry_krw + expected_current_krw + (source_financing_adjustment_krw or 0)
            if source_carry_krw is not None and expected_current_krw is not None
            else None
        )
        reset_detected = bool(
            prior_source_cumulative_krw not in (None, 0)
            and source_carry_krw == 0
        )
        rows.append({
            "month_index": month_index,
            "period": str(period),
            "cash_in": cash_in,
            "cash_out": cash_out,
            "pf_draw": pf_draw,
            "loan_repayment": loan_repayment,
            "equity_contribution": equity_contribution,
            "equity_recovery": equity_recovery,
            "other_financing_inflow": other_financing_inflow,
            "other_financing_outflow": other_financing_outflow,
            "net_cash_flow": net_cash_flow,
            "cumulative_cash_flow": source_cumulative_krw,
            "source_locator": {
                "sourceType": "excel_range",
                "sourceDocument": source_name,
                "sheetName": "2.현금흐름",
                "cell": f"{_column_name(col)}{start_row}:{_column_name(col)}{end_row}",
                "rowRange": f"B{start_row}:{_column_name(end_col)}{end_row}",
                "sourceUnit": "KRW_MILLION",
                "normalizedUnit": "KRW",
                "normalizationFactor": KRW_PER_MILLION,
                "transactionRowPolicy": "legacy_bonri_explicit_rows_v1",
                "currentCashCheck": {
                    "cell": f"{_column_name(col)}122",
                    "expectedKrw": expected_current_krw,
                    "actualKrw": net_cash_flow,
                    "differenceKrw": net_cash_flow - expected_current_krw if expected_current_krw is not None else None,
                    "passed": abs(net_cash_flow - expected_current_krw) <= 1 if expected_current_krw is not None else False,
                },
                "legacyCumulativeCashFlowCheck": {
                    "cell": f"{_column_name(col)}130",
                    "carryCell": f"{_column_name(col)}123",
                    "carryKrw": source_carry_krw,
                    "currentKrw": expected_current_krw,
                    "financingAdjustmentCell": f"{_column_name(col)}129",
                    "financingAdjustmentKrw": source_financing_adjustment_krw,
                    "expectedKrw": expected_cumulative_krw,
                    "actualKrw": source_cumulative_krw,
                    "differenceKrw": source_cumulative_krw - expected_cumulative_krw if source_cumulative_krw is not None and expected_cumulative_krw is not None else None,
                    "passed": abs(source_cumulative_krw - expected_cumulative_krw) <= 1 if source_cumulative_krw is not None and expected_cumulative_krw is not None else False,
                    "carryResetDetected": reset_detected,
                },
            },
        })
        prior_source_cumulative_krw = source_cumulative_krw
    return rows


def _last_period_column(cash_flow: Any, *, start_col: int, header_row: int) -> Optional[int]:
    max_col = int(getattr(cash_flow, "max_column", start_col) or start_col)
    header_values = next(
        cash_flow.iter_rows(
            min_row=header_row,
            max_row=header_row,
            min_col=start_col,
            max_col=max_col,
            values_only=True,
        ),
        (),
    )
    populated_offsets = [
        offset for offset, value in enumerate(header_values)
        if value not in (None, "")
    ]
    return start_col + max(populated_offsets) if populated_offsets else None


def _default_scenarios() -> List[Dict[str, Any]]:
    return [
        {"scenario_id": "base", "label": "Base", "sales_price_factor": 1.0, "construction_cost_factor": 1.0, "sales_rate": 1.0},
        {"scenario_id": "sales_minus_10pct", "label": "Sales -10%", "sales_price_factor": 0.9, "construction_cost_factor": 1.0, "sales_rate": 0.9},
        {"scenario_id": "combined_downside", "label": "Sales -10%, Cost +5%", "sales_price_factor": 0.9, "construction_cost_factor": 1.05, "sales_rate": 0.85},
    ]


def _realytics_product_monthly_sales_rate_scenarios(
    workbook: Any,
    source_name: str,
    total_revenue_krw: Optional[float],
) -> List[Dict[str, Any]]:
    resolved_sheet_name = _find_product_collection_sheet(workbook)
    if resolved_sheet_name:
        sheet_name = resolved_sheet_name
    else:
        return []
    sheet_name = "02B_수납스케줄"
    # Keep the resolved workbook sheet; the legacy literal above may be mojibake.
    sheet_name = resolved_sheet_name
    if sheet_name not in workbook.sheetnames:
        return []

    sheet = workbook[sheet_name]
    product_groups: Dict[str, Dict[str, Any]] = {}
    total_monthly_amounts = [0.0 for _ in range(45)]
    for row_index in range(4, sheet.max_row + 1):
        product_type = _cell_value(sheet, f"A{row_index}")
        stage = _cell_value(sheet, f"B{row_index}")
        due_month = _to_float(_cell_value(sheet, f"C{row_index}"))
        collection_rate = _to_float(_cell_value(sheet, f"D{row_index}"))
        product_revenue = _to_float(_cell_value(sheet, f"E{row_index}"))
        stage_amount = _to_float(_cell_value(sheet, f"F{row_index}"))
        if not product_type or str(product_type).strip() == "합계":
            continue
        if not stage or stage_amount is None:
            continue

        key = str(product_type)
        group = product_groups.setdefault(
            key,
            {
                "productType": key,
                "productRevenue": _eok_to_krw(product_revenue),
                "collectionSteps": [],
                "monthlyCollectionAmounts": [0.0 for _ in range(45)],
            },
        )
        step_monthly_amounts = []
        for month_index in range(45):
            column = _column_name(7 + month_index)
            amount = _to_float(_cell_value(sheet, f"{column}{row_index}")) or 0.0
            step_monthly_amounts.append(_eok_to_krw(amount) or 0.0)
            group["monthlyCollectionAmounts"][month_index] += _eok_to_krw(amount) or 0.0
            total_monthly_amounts[month_index] += _eok_to_krw(amount) or 0.0

        group["collectionSteps"].append(
            {
                "stage": str(stage),
                "dueMonth": int(due_month) if due_month is not None else None,
                "collectionRate": collection_rate,
                "stageAmount": _eok_to_krw(stage_amount),
                "monthlyCollectionAmounts": step_monthly_amounts,
                "provenance": _provenance(source_name, "xlsx", sheet_name, f"A{row_index}:AY{row_index}", str(stage)),
            }
        )

    if not product_groups:
        return []

    denominator = total_revenue_krw or sum(total_monthly_amounts)
    product_scenarios = []
    for group in product_groups.values():
        product_revenue = group.get("productRevenue") or sum(group["monthlyCollectionAmounts"])
        group["monthlyCollectionRates"] = [
            amount / product_revenue if product_revenue else 0.0
            for amount in group["monthlyCollectionAmounts"]
        ]
        group["cumulativeCollectionRates"] = _cumulative_rates(group["monthlyCollectionRates"])
        product_scenarios.append(group)

    return [
        {
            "scenarioId": "base_product_collection_schedule",
            "label": "Base product-level monthly collection schedule",
            "unit": "KRW",
            "monthCount": 45,
            "sourceSheet": sheet_name,
            "sourceWorkbook": source_name,
            "totalMonthlyCollectionAmounts": total_monthly_amounts,
            "totalMonthlyCollectionRates": [
                amount / denominator if denominator else 0.0
                for amount in total_monthly_amounts
            ],
            "totalCumulativeCollectionRates": _cumulative_rates([
                amount / denominator if denominator else 0.0
                for amount in total_monthly_amounts
            ]),
            "products": product_scenarios,
            "sourceLocator": {
                "sourceType": "excel_range",
                "sheetName": sheet_name,
                "cell": f"A4:AY{sheet.max_row}",
                "fieldName": "raw_import.productMonthlySalesRateScenarios",
                "confidence": 0.95,
            },
        }
    ]


def _find_product_collection_sheet(workbook: Any) -> Optional[str]:
    """Resolve product collection sheets without relying on corrupted UI text."""
    for sheet_name in workbook.sheetnames:
        normalized = str(sheet_name).strip().casefold().replace(" ", "")
        if normalized.startswith("02b") and any(
            token in normalized for token in ("수납", "납입", "collection", "schedule")
        ):
            return sheet_name
    for sheet_name in workbook.sheetnames:
        if str(sheet_name).strip().casefold().replace(" ", "").startswith("02b"):
            return sheet_name
    return None


def _cumulative_rates(values: List[float]) -> List[float]:
    total = 0.0
    result = []
    for value in values:
        total += value
        result.append(total)
    return result


def _eok_to_krw(value: Optional[float]) -> Optional[float]:
    return value * 100_000_000 if value is not None else None


def _attach_profile_governed_fact_basis(
    raw_import: Dict[str, Any],
    *,
    schema_version: str = "",
    legacy_vat_statement: Any = None,
) -> None:
    """Attach only basis attested by a controlled workbook profile.

    Unknown profiles and the Yongin public-rental profile intentionally remain
    ineligible.  In particular, the Yongin workbook mixes sale and rental
    columns but does not state one VAT treatment or whether ``cash!D34`` is a
    commitment, drawn balance, or outstanding balance.
    """

    profile = str(raw_import.get("profile") or "")
    evidence: List[Dict[str, Any]]
    if profile == "legacy-bonri-mixed-use-pf-feasibility":
        rendered_statement = str(legacy_vat_statement or "").strip()
        normalized_statement = rendered_statement.casefold().replace(" ", "")
        if "vat" not in normalized_statement or "\ubcc4\ub3c4" not in normalized_statement:
            _set_incomplete_basis_coverage(
                raw_import,
                reason=(
                    "legacy Bonri basis requires an explicit workbook VAT "
                    "separate statement"
                ),
                missing_evidence=["사업수지!A2 VAT 별도"],
            )
            return
        evidence = [
            {
                "evidenceType": "workbook_cell",
                "sheetName": "사업수지",
                "cellReference": "A2",
                "observedText": rendered_statement,
                "attests": [
                    "revenue.total_sales_revenue.vatTreatment",
                    "cost.total_project_cost.vatTreatment",
                ],
            },
            {
                "evidenceType": "versioned_profile_formula",
                "profileContract": "legacy-bonri-mixed-use-pf-feasibility-v3",
                "cellReferences": [
                    "사업수지!D11",
                    "사업수지!D72",
                    "2.현금흐름!E48:E51",
                ],
                "attests": [
                    "gross project totals",
                    "mixed cost-category sum",
                    "planned PF tranche principal commitment",
                ],
            },
        ]
    elif profile == "realytics-formula-cash-flow-export":
        if schema_version != _CONTROLLED_REALYTICS_SCHEMA_VERSION:
            _set_incomplete_basis_coverage(
                raw_import,
                reason=(
                    "REALYTICS monetary basis is attested only for the exact "
                    "controlled workbook schema version"
                ),
                missing_evidence=[
                    f"schemaVersion={_CONTROLLED_REALYTICS_SCHEMA_VERSION}"
                ],
            )
            return
        evidence = [
            {
                "evidenceType": "versioned_profile_contract",
                "schemaVersion": schema_version,
                "namedRanges": [
                    "fs_total_revenue",
                    "fs_total_project_cost",
                    "fs_pf_loan",
                ],
                "attests": [
                    "VAT-excluded total sales revenue",
                    "summed gross project cost categories",
                    "requested PF / tranche principal commitment",
                ],
            }
        ]
    else:
        _set_incomplete_basis_coverage(
            raw_import,
            reason="the detected workbook profile has no governed monetary basis contract",
            missing_evidence=[
                "vatTreatment",
                "amountScope",
                "allocationBasis",
                "financingBalanceBasis",
            ],
        )
        return

    basis = _PROFILE_GOVERNED_FACT_BASIS[profile]
    raw_import["governedFactBasis"] = copy.deepcopy(basis)
    raw_import["governedFactBasisAttestation"] = {
        "schemaVersion": GOVERNED_FACT_BASIS_CONTRACT_VERSION,
        "profile": profile,
        "status": "attested",
        "evidence": evidence,
    }
    raw_import["governedFactBasisCoverage"] = {
        "schemaVersion": GOVERNED_FACT_BASIS_CONTRACT_VERSION,
        "status": "complete",
        "fieldKeys": list(_GOVERNED_MONETARY_FIELD_KEYS),
        "approvalEligible": True,
    }


def _set_incomplete_basis_coverage(
    raw_import: Dict[str, Any],
    *,
    reason: str,
    missing_evidence: List[str],
) -> None:
    raw_import.pop("governedFactBasis", None)
    raw_import.pop("governedFactBasisAttestation", None)
    raw_import["governedFactBasisCoverage"] = {
        "schemaVersion": GOVERNED_FACT_BASIS_CONTRACT_VERSION,
        "status": "insufficient_data",
        "fieldKeys": list(_GOVERNED_MONETARY_FIELD_KEYS),
        "reason": reason,
        "missingEvidence": list(missing_evidence),
        "analysisOnly": True,
        "reportEligible": False,
        "approvalEligible": False,
    }


def _named_range_anchor(
    named_range_values: Dict[str, Any],
    name: str,
) -> Optional[str]:
    item = named_range_values.get(name)
    if not isinstance(item, dict):
        return None
    destinations = item.get("destinations")
    if not isinstance(destinations, list) or len(destinations) != 1:
        return None
    destination = destinations[0]
    if not isinstance(destination, dict):
        return None
    sheet_name = str(destination.get("sheetName") or "").strip()
    cell_range = str(destination.get("range") or "").replace("$", "").strip()
    if not sheet_name or not cell_range:
        return None
    return f"'{sheet_name}'!{cell_range}"


def _minimal_draft(
    project_id: Optional[str],
    validation: Dict[str, Any],
    source_document: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    raw_import = {
        "profile": validation.get("profile"),
        "validationStatus": validation.get("status"),
        "sourceDocument": source_document or {},
        "limitations": ["No workbook importer profile is available for this file."],
    }
    _attach_profile_governed_fact_basis(raw_import)
    return {
        "project_id": project_id,
        "asset_type": "other",
        "metrics": {},
        "raw_import": raw_import,
    }


def _line_item(
    key: str,
    label: str,
    category: str,
    amount: Optional[float],
    sheet_name: str,
    cell: str,
    source_name: str,
    named_range: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "category": category,
        "amount": amount,
        "unit": "KRW",
        "excel": {"sheet_name": sheet_name, "cell": cell, "named_range": named_range},
        "provenance": _provenance(source_name, "xlsx", sheet_name, cell, None, named_range),
    }


def _value(
    key: str,
    label: str,
    value: Optional[float],
    unit: str,
    sheet_name: str,
    cell: str,
    source_name: str,
) -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "excel": {"sheet_name": sheet_name, "cell": cell},
        "provenance": _provenance(source_name, "xlsx", sheet_name, cell, None),
    }


def _provenance(
    source_name: str,
    source_type: str,
    sheet_name: str,
    cell: str,
    raw_text: Optional[str],
    named_range: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "source_document": source_name,
        "source_type": source_type,
        "sheet_name": sheet_name,
        "cell": cell,
        "named_range": named_range,
        "raw_text": raw_text,
        "confidence": 0.9,
    }


def _pf_amount_from_cash_flow(cash_flow: Any) -> float:
    return sum(_krw_from_million(_cell_value(cash_flow, cell)) or 0 for cell in ["E48", "E49", "E50", "E51"])


def _max_minus_from_cash_flow(cash_flow: Any) -> Optional[float]:
    values = []
    end_col = _last_period_column(cash_flow, start_col=6, header_row=4)
    if end_col is None:
        return None
    for row in [128, 130]:
        for col in range(6, end_col + 1):
            value = _to_float(cash_flow.cell(row, col).value)
            if value is not None:
                values.append(value * KRW_PER_MILLION)
    return min(values) if values else None


def _commercial_unit_count(sales: Any) -> float:
    return sum(_to_float(_cell_value(sales, cell)) or 0 for cell in ["I32", "I33"])


def _source_name(workbook_path: Path, source_document: Optional[Dict[str, Any]]) -> str:
    return (
        (source_document or {}).get("originalFileName")
        or (source_document or {}).get("fileName")
        or workbook_path.name
    )


def _cell_value(sheet: Any, cell: str) -> Any:
    return sheet[cell].value


def _column_name(column_index: int) -> str:
    dividend = column_index
    name = ""
    while dividend > 0:
        modulo = (dividend - 1) % 26
        name = chr(65 + modulo) + name
        dividend = (dividend - modulo) // 26
    return name


def _to_float(value: Any) -> Optional[float]:
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


def _krw_from_thousand(value: Any) -> Optional[int]:
    raw = _to_float(value)
    return excel_round_int(to_decimal(raw) * KRW_PER_THOUSAND) if raw is not None else None


def _krw_from_million(value: Any) -> Optional[int]:
    raw = _to_float(value)
    return excel_round_int(to_decimal(raw) * KRW_PER_MILLION) if raw is not None else None


def _py_to_sqm(value: Any) -> Optional[float]:
    raw = _to_float(value)
    return raw * SQM_PER_PY if raw is not None else None


def _date_value(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            converted = from_excel(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if isinstance(converted, datetime):
            return converted.date().isoformat()
        if isinstance(converted, date):
            return converted.isoformat()
        return None

    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        pass
    for pattern in ("%Y/%m/%d", "%Y.%m.%d", "%Y년 %m월 %d일", "%Y년%m월%d일"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    for pattern in ("%Y-%m", "%Y/%m", "%Y.%m", "%Y년 %m월", "%Y년%m월"):
        try:
            return datetime.strptime(text, pattern).strftime("%Y-%m")
        except ValueError:
            continue
    return None
