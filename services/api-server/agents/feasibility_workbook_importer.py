from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from openpyxl import load_workbook

from agents.feasibility_workbook_validator import validate_feasibility_workbook
from agents.legacy_bonri_workbook_profile import legacy_bonri_registry_context
from schemas.feasibility_study import FeasibilityStudyV2


KRW_PER_THOUSAND = 1_000
KRW_PER_MILLION = 1_000_000
SQM_PER_PY = 3.305785


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
        monthly_cash_flow = []
        for column in range(5, cash.max_column + 1):
            period = cash.cell(1, column).value
            if period is None:
                continue
            net = sum(_to_float(cash.cell(row, column).value) or 0 for row in range(1, cash.max_row + 1))
            monthly_cash_flow.append({
                "month_index": len(monthly_cash_flow),
                "period": str(period),
                "cash_in": {},
                "cash_out": {},
                "net_cash_flow": net * KRW_PER_THOUSAND,
                "source_locator": {"sourceType": "excel_column", "sourceDocument": source_name, "sheetName": cash.title, "cell": f"{_column_name(column)}1:{_column_name(column)}{cash.max_row}"},
            })
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
            "schedule": [{"key": "construction_start", "label": "construction start", "planned_date": "2027-04", "provenance": _provenance(source_name, "xlsx", profit.title, "A3", None)}],
            "monthly_cash_flow": monthly_cash_flow,
            "metrics": _metrics(total_revenue, total_cost, development_profit, pf_amount, min((row.get("net_cash_flow") or 0) for row in monthly_cash_flow) if monthly_cash_flow else None),
            "raw_import": {"profile": validation.get("profile"), "validationStatus": validation.get("status"), "sourceWorkbook": source_name, "sourceDocument": source_document or {}, "mappedCells": {"totalRevenue": f"{profit.title}!I24", "totalCost": f"{profit.title}!I81", "developmentProfit": f"{profit.title}!I82", "pfAmount": f"{cash.title}!D34", "productMix": f"{overview.title}!A18:M25"}, "limitations": ["Product-level rental conversion and legal approval conditions remain subject to review."]},
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
        legal_far_raw = _to_float(_cell_value(input_sheet, "E22"))
        legal_far = legal_far_raw * 100 if legal_far_raw is not None and legal_far_raw < 100 else legal_far_raw

        total_revenue = _krw_from_thousand(_cell_value(business, "D11"))
        total_cost = _krw_from_thousand(_cell_value(business, "D72"))
        development_profit = _krw_from_thousand(_cell_value(business, "D74"))
        pf_amount = _pf_amount_from_cash_flow(cash_flow)

        cost_items = [
            _line_item("land_cost", "대지비", "land", _krw_from_thousand(_cell_value(business, "D26")), "사업수지", "D26", source_name),
            _line_item("construction_cost", "공사비", "construction", _krw_from_thousand(_cell_value(business, "D18")), "사업수지", "D18", source_name),
            _line_item("service_cost", "용역비", "indirect", _krw_from_thousand(_cell_value(business, "D33")), "사업수지", "D33", source_name),
            _line_item("sales_marketing_cost", "분양제경비", "sales_marketing", _krw_from_thousand(_cell_value(business, "D41")), "사업수지", "D41", source_name),
            _line_item("levy_and_charges", "각종부담금", "tax_and_dues", _krw_from_thousand(_cell_value(business, "D52")), "사업수지", "D52", source_name),
            _line_item("finance_cost", "금융비용", "finance", _krw_from_thousand(_cell_value(business, "D62")), "사업수지", "D62", source_name),
            _line_item("other_cost", "기타사업비", "other", _krw_from_thousand(_cell_value(business, "D71")), "사업수지", "D71", source_name),
        ]

        return {
            "project_id": project_id,
            "project_name": project_name,
            "asset_type": "mixed_use",
            "site": {
                "address": address,
                "land_area_sqm": _value("land_area_sqm", "대지면적", land_area_sqm, "SQM", "입력", "E8", source_name),
                "zoning": _cell_value(input_sheet, "E6"),
                "legal_far": _value("legal_far", "용적률", legal_far, "PERCENT", "입력", "E22", source_name),
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
            "financing": _legacy_financing(cash_flow),
            "schedule": _legacy_schedule(input_sheet, source_name),
            "scenarios": _default_scenarios(),
            "monthly_cash_flow": _legacy_monthly_cash_flow(cash_flow, source_name),
            "metrics": _metrics(total_revenue, total_cost, development_profit, pf_amount, _max_minus_from_cash_flow(cash_flow)),
            "raw_import": {
                "profile": validation.get("profile"),
                "sampleProfile": legacy_bonri_registry_context(source_name),
                "validationStatus": validation.get("status"),
                "sourceWorkbook": source_name,
                "unitScale": {
                    "사업수지": "KRW_THOUSAND",
                    "2.현금흐름": "KRW_MILLION",
                },
                "sourceDocument": source_document or {},
                "mappedCells": {
                    "totalRevenue": "사업수지!D11",
                    "totalCost": "사업수지!D72",
                    "developmentProfit": "사업수지!D74",
                    "pfAmount": "2.현금흐름!E48:E51",
                    "productMix": "분양!21,30,40 and 입력!M5:M8",
                },
                "limitations": [
                    "Legacy sample workbook has no REALYTICS named ranges; mapping uses a fixed Bonri-style cell profile.",
                    "PF tranche terms beyond principal are not fully available in the workbook and require PF review documents.",
                ],
            },
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
            "raw_import": {
                "profile": validation.get("profile"),
                "validationStatus": validation.get("status"),
                "sourceWorkbook": source_name,
                "schemaVersion": (named.get("realytics_fs_schema_version") or {}).get("value"),
                "namedRangeValues": named,
                "productMonthlySalesRateScenarios": product_monthly_scenarios,
                "sourceDocument": source_document or {},
                "limitations": limitations,
            },
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


def _legacy_financing(cash_flow: Any) -> List[Dict[str, Any]]:
    tranche_rows = [
        ("tr_a", "Tr.A", "senior", "E48"),
        ("tr_b", "Tr.B", "middle", "E49"),
        ("tr_c", "Tr.C", "junior", "E50"),
        ("tr_d", "Tr.D", "other", "E51"),
    ]
    result = []
    for tranche_id, name, priority, cell in tranche_rows:
        amount = _krw_from_million(_cell_value(cash_flow, cell))
        if amount:
            result.append({
                "tranche_id": tranche_id,
                "name": name,
                "priority": priority,
                "amount": amount,
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
    return result


def _legacy_schedule(input_sheet: Any, source_name: str) -> List[Dict[str, Any]]:
    start_date = _date_value(_cell_value(input_sheet, "D25"))
    completion_date = _date_value(_cell_value(input_sheet, "D26"))
    duration = _to_float(_cell_value(input_sheet, "D24"))
    return [
        {
            "key": "construction_start",
            "label": "착공일",
            "planned_date": start_date,
            "month_index": 1,
            "excel": {"sheet_name": "입력", "cell": "D25"},
            "provenance": _provenance(source_name, "xlsx", "입력", "D25", None),
        },
        {
            "key": "completion",
            "label": "준공일",
            "planned_date": completion_date,
            "month_index": int(duration) if duration else None,
            "excel": {"sheet_name": "입력", "cell": "D26"},
            "provenance": _provenance(source_name, "xlsx", "입력", "D26", None),
        },
    ]


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


def _legacy_monthly_cash_flow(cash_flow: Any, source_name: str) -> List[Dict[str, Any]]:
    """Map the legacy monthly CF block without inventing missing semantics.

    The legacy sheet is row-oriented (line items in rows 100:158 and periods
    in columns J:CH). We preserve the complete row matrix in each canonical
    period's ``cashIn``/``cashOut`` buckets and keep the exact Excel range as
    provenance. A later workbook adapter can classify individual rows into
    named buckets without losing this imported source data.
    """
    rows: List[Dict[str, Any]] = []
    start_col, end_col = 10, min(int(getattr(cash_flow, "max_column", 86) or 86), 86)
    start_row, end_row = 100, min(int(getattr(cash_flow, "max_row", 158) or 158), 158)
    # ``iter_rows`` is important here: repeated random access on an
    # openpyxl read-only worksheet is disproportionately slow for legacy
    # workbooks with large shared-string tables.
    period_row = next(cash_flow.iter_rows(min_row=4, max_row=4, min_col=start_col, max_col=end_col, values_only=True), ())
    source_rows = list(cash_flow.iter_rows(min_row=start_row, max_row=end_row, min_col=2, max_col=end_col, values_only=True))
    for col in range(start_col, end_col + 1):
        month_index = col - start_col
        period_offset = col - start_col
        period = period_row[period_offset] if period_offset < len(period_row) else None
        period = period or f"M{month_index}"
        cash_in: Dict[str, float] = {}
        cash_out: Dict[str, float] = {}
        for row_offset, values in enumerate(source_rows):
            row = start_row + row_offset
            # values begins at column B; the month value is offset by one
            # because J is the tenth worksheet column.
            value_offset = col - 2
            value = _to_float(values[value_offset]) if value_offset < len(values) else None
            if value is None:
                continue
            label = values[0] or (values[1] if len(values) > 1 else None) or f"row_{row}"
            key = f"row_{row}_{str(label).strip()}"
            # Preserve sign and original line item. Classification is only a
            # display aid; the source row remains authoritative.
            target = cash_in if value >= 0 else cash_out
            target[key] = value
        total_in = sum(cash_in.values())
        total_out = sum(abs(v) for v in cash_out.values())
        rows.append({
            "month_index": month_index,
            "period": str(period),
            "cash_in": cash_in,
            "cash_out": cash_out,
            "net_cash_flow": total_in - total_out,
            "source_locator": {
                "sourceType": "excel_range",
                "sourceDocument": source_name,
                "sheetName": "2.현금흐름",
                "cell": f"{_column_name(col)}{start_row}:{_column_name(col)}{end_row}",
                "rowRange": f"B{start_row}:CH{end_row}",
            },
        })
    return rows


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


def _minimal_draft(
    project_id: Optional[str],
    validation: Dict[str, Any],
    source_document: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "project_id": project_id,
        "asset_type": "other",
        "metrics": {},
        "raw_import": {
            "profile": validation.get("profile"),
            "validationStatus": validation.get("status"),
            "sourceDocument": source_document or {},
            "limitations": ["No workbook importer profile is available for this file."],
        },
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
    for row in [128, 130]:
        for col in range(6, cash_flow.max_column + 1):
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


def _krw_from_thousand(value: Any) -> Optional[float]:
    raw = _to_float(value)
    return raw * KRW_PER_THOUSAND if raw is not None else None


def _krw_from_million(value: Any) -> Optional[float]:
    raw = _to_float(value)
    return raw * KRW_PER_MILLION if raw is not None else None


def _py_to_sqm(value: Any) -> Optional[float]:
    raw = _to_float(value)
    return raw * SQM_PER_PY if raw is not None else None


def _date_value(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value) if value else None
