"""Versioned, source-preserving mapping of the Sangbong feasibility workbooks.

This adapter extracts a review draft. Saved Excel formula values are observable
source data, not proof that Excel has been recalculated or that a loan is agreed.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any

from openpyxl import load_workbook

from domains.feasibility.numeric import excel_round_int

VERSION = "sangbong-financial-mapping-v1"
OVERVIEW = "0. 개요입력"
BUSINESS = "1. 사업수지"
CASH_FLOW = "2. 캐쉬_분양률100%"
PROFILES = {"sale": "sangbong-sale-feasibility", "rental": "sangbong-private-rental-feasibility"}
_COST_GROUPS = {
    "sale": [(75, "land", "토지비 및 부대비"), (77, "construction", "건축공사비"),
             (84, "indirect", "공사 부대비"), (92, "indirect", "설계·감리·용역"),
             (104, "sales_marketing", "분양·홍보비"), (113, "other", "시행·관리·예비비"),
             (122, "tax_and_dues", "제세공과금"), (145, "finance", "금융비용")],
    "rental": [(58, "land", "토지비 및 부대비"), (60, "construction", "건축공사비"),
               (67, "indirect", "공사 부대비"), (75, "indirect", "설계·감리·용역"),
               (86, "sales_marketing", "분양·홍보·예비비"), (94, "other", "시행·관리비"),
               (102, "other", "임대운영비"), (110, "tax_and_dues", "제세공과금"),
               (133, "finance", "금융비용")],
}
_SCALE = {"KRW": Decimal(1), "KRW_THOUSAND": Decimal(1000), "KRW_MILLION": Decimal(1000000),
          "KRW_HUNDRED_MILLION": Decimal(100000000)}


def _text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _formula(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith("="):
        return value
    candidate = getattr(value, "text", None)
    return candidate if isinstance(candidate, str) else None


def _safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return _formula(value)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None or isinstance(value, (date, datetime)):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def detect_sangbong_profile(workbook: Any) -> dict | None:
    """Require the workbook's cell semantics, never its filename or project name."""
    required = [OVERVIEW, BUSINESS, CASH_FLOW, "분양대금수납계획", "산출기초", "08. 제세금"]
    if not set(required).issubset(workbook.sheetnames):
        return None
    overview, business = workbook[OVERVIEW], workbook[BUSINESS]
    if not (_text(overview["B5"].value) == "대지위치"
            and _text(overview["B7"].value) == "대지면적"
            and _text(business["K22"].value) == "금액"
            and "천원" in _text(business["AS3"].value)
            and "백만원" in _text(business["AA4"].value)):
        return None
    marker = _text(business["E23"].value)
    if marker == "아파트분양가" and _text(business["E69"].value) == "토지비" and _text(business["E123"].value) == "PF이자(TRA)":
        variant = "sale"
    elif marker == "아파트임대보증금" and "임대조건" in workbook.sheetnames and _text(business["E52"].value) == "토지비" and _text(business["E112"].value) == "LOAN-TR1대출이자":
        variant = "rental"
        required.append("임대조건")
    else:
        return None
    return {"profile": PROFILES[variant], "family": "sangbong", "variant": variant,
            "required_sheets": required, "key_cells": {}, "mappingVersion": VERSION}


def _reader(formula_wb: Any, value_wb: Any):
    # Read each sparse sheet once. Random reads on a ReadOnlyWorksheet repeatedly
    # parse its XML and made even tiny cell maps unnecessarily expensive.
    index: dict[tuple[str, str], tuple[Any, Any, str]] = {}
    loaded: set[str] = set()

    def load_sheet(name):
        if name in loaded:
            return
        loaded.add(name)
        if name not in formula_wb.sheetnames or name not in value_wb.sheetnames:
            return
        for formulas, values in zip(formula_wb[name].iter_rows(), value_wb[name].iter_rows()):
            for fc, vc in zip(formulas, values):
                if fc.value is not None:
                    index[(name, fc.coordinate)] = (fc.value, vc.value, fc.number_format)

    for name in (OVERVIEW, BUSINESS):
        load_sheet(name)

    def read_cell(sheet_name: str, cell: str, unit: str = "TEXT") -> dict:
        load_sheet(sheet_name)
        raw, cached, number_format = index.get((sheet_name, cell), (None, None, "General"))
        formula = _formula(raw)
        observed = cached if formula else raw
        value = _safe(observed)
        status = "missing" if observed is None else "read"
        rounding = None
        if unit in _SCALE or unit in {"RATIO", "MONTH", "COUNT", "SQM", "PERCENT"}:
            number = _decimal(observed)
            if number is None:
                value, status = None, "missing" if observed is None else "needs_review"
            elif unit in _SCALE:
                scaled = number * _SCALE[unit]
                value = excel_round_int(scaled)
                if scaled != Decimal(value):
                    rounding = {"policy": "nearest_won_half_up", "unroundedKrw": str(scaled),
                                "reason": "원 단위 반올림; 원본 저장값과 환산 전 단위 보존"}
            else:
                value = float(number)
        elif unit == "DATE" and not isinstance(observed, (date, datetime)):
            value, status = None, "missing" if observed is None else "needs_review"
        return {"value": value, "rawValue": _safe(observed), "formula": formula,
                "sourceCell": f"'{sheet_name}'!{cell}", "sourceUnit": unit,
                "normalizedUnit": "KRW" if unit in _SCALE else unit,
                "status": status, "calculationState": "saved_formula_cache" if formula and observed is not None else "unavailable" if formula else "literal",
                "numberFormat": number_format, **({"rounding": rounding} if rounding else {})}

    return read_cell


def _source_sum(read, cells: list[str], label: str) -> dict:
    parts = [read(BUSINESS, cell, "KRW_THOUSAND") for cell in cells]
    available = all(_decimal(part["rawValue"]) is not None for part in parts)
    raw_total = sum((_decimal(part["rawValue"]) for part in parts), Decimal(0)) if available else None
    return {"label": label, "value": excel_round_int(raw_total * 1000) if raw_total is not None else None,
            "rawValue": str(raw_total) if raw_total is not None else None,
            "sourceCell": ", ".join(part["sourceCell"] for part in parts),
            "sourceUnit": "KRW_THOUSAND", "normalizedUnit": "KRW", "formula": None,
            "calculationExpression": "SUM(" + ",".join(cells) + ")",
            "status": "read" if available else "missing", "calculationState": "derived_from_source_values",
            "components": parts, "roundingPolicy": "nearest_won_half_up"}


def _provenance(name: str, sheet: str, cell: str, raw: Any = None) -> dict:
    return {"source_document": name, "source_type": "xlsx", "sheet_name": sheet,
            "cell": cell, "raw_text": str(raw) if raw is not None else None}


def _line_item(key: str, label: str, category: str, record: dict, source_name: str) -> dict:
    cell = record["sourceCell"].split("!", 1)[-1]
    return {"key": key, "label": label, "category": category, "amount": record["value"], "unit": "KRW",
            "excel": {"sheet_name": BUSINESS, "cell": cell},
            "formula": {"formula": record.get("formula"), "formula_type": "excel_formula" if record.get("formula") else "input"},
            "provenance": _provenance(source_name, BUSINESS, cell, record.get("rawValue"))}


def _rental_revenue_basis(read) -> dict:
    # The source conversion price subtracts deposits already collected. Rebuild
    # gross consideration from its declared units/prices, not by treating a
    # refundable deposit as an additional sales line item.
    cells = {cell: read(BUSINESS, cell, "COUNT" if cell.startswith("U") else "KRW_THOUSAND")
             for cell in ("U33", "AB33", "U34", "AB34", "K25", "K29", "K35", "K32", "K43", "K50", "K51")}
    available = all(_decimal(record["rawValue"]) is not None for record in cells.values())
    result = {"policy": "gross_conversion_consideration_plus_rents", "sourceFields": cells,
              "status": "needs_review", "grossConversionKrw": None, "differenceKrw": None,
              "message": "임대보증금은 별도 금융 유입이며 분양전환 대금에서 정산됩니다. 총매출에 보증금을 다시 더하지 않습니다."}
    if available:
        value = {key: _decimal(record["rawValue"]) for key, record in cells.items()}
        gross = value["U33"] * value["AB33"] + value["U34"] * value["AB34"]
        offset_difference = (gross - value["K35"] - value["K25"] - value["K29"]) * 1000
        total = gross + value["K32"] + value["K43"] + value["K50"]
        total_difference = (total - value["K51"]) * 1000
        formulas_match = all(_text(cells[cell]["formula"]) == expected for cell, expected in (
            ("K35", "=SUM(K33:N34)"), ("K51", "=SUM(K50,K43,K36)")))
        # Pin the actual net-price semantics as well as the observed totals.
        formulas_match = formulas_match and all(_text(read(BUSINESS, cell)["formula"]) == expected for cell, expected in (
            ("X33", "=AB33-(X26+(K23/U23))"), ("X34", "=AB34-(X27+(K24/U24))")))
        result.update(status="matched" if abs(offset_difference) <= 1 and abs(total_difference) <= 1 and formulas_match else "needs_review",
                      grossConversionKrw=excel_round_int(gross * 1000),
                      grossConversionRawThousand=str(gross), totalRevenueKrw=excel_round_int(total * 1000),
                      differenceKrw=float(total_difference), depositOffsetDifferenceKrw=float(offset_difference),
                      sourceFormulaSemanticsMatch=formulas_match)
    return result


def _product_revenue(read, variant: str, row: int, units: float) -> dict:
    """Keep each product's declared source consideration separate from rent."""
    source_cells = []
    amount = None
    if row < 30 and variant == "sale":
        base_cell, balcony_cell = f"K{row - 2}", f"K{row + 4}"
        base, balcony, vat = [read(BUSINESS, cell, unit) for cell, unit in (
            (base_cell, "KRW_THOUSAND"), (balcony_cell, "KRW_THOUSAND"), ("Q34", "RATIO"))]
        source_cells = [base["sourceCell"], balcony["sourceCell"], vat["sourceCell"]]
        numbers = [_decimal(item["rawValue"]) for item in (base, balcony, vat)]
        declared_units = read(BUSINESS, f"U{row - 2}", "COUNT")["value"]
        if all(value is not None for value in numbers) and declared_units == units:
            amount = excel_round_int((numbers[0] + numbers[1] * (1 + numbers[2])) * 1000)
    elif row < 30:
        price_cell = "AB33" if row in (25, 26) else "AB34"
        price = read(BUSINESS, price_cell, "KRW_THOUSAND")
        source_cells = [price["sourceCell"], f"'{OVERVIEW}'!C{row}"]
        if _decimal(price["rawValue"]) is not None:
            amount = excel_round_int(_decimal(price["rawValue"]) * Decimal(str(units)) * 1000)
    else:
        cell = (f"K{row + 10}" if variant == "sale" else f"K{row}") if row in (37, 38) else ("K67" if variant == "sale" else "K50")
        record = read(BUSINESS, cell, "KRW_THOUSAND")
        source_cells = [record["sourceCell"]]
        amount = record["value"]
    return {"value": amount, "sourceCells": source_cells, "status": "read" if amount is not None else "needs_review",
            "basis": "source_sale_consideration_net_vat" if variant == "sale" else "source_gross_conversion_or_sale_consideration",
            "requiresHumanReview": True, "calculationEvidence": "source_values_not_native_excel_recalculation"}


def map_sangbong_workbook(formula_wb: Any, value_wb: Any, profile: dict, *, source_name: str, include_cash_flow: bool = True) -> dict:
    """Build source records and canonical *draft* sections for this family."""
    from agents.sangbong_workbook_financing import map_sangbong_financing
    from agents.sangbong_workbook_cash_flow import map_sangbong_cash_flow

    read = _reader(formula_wb, value_wb)
    variant = profile["variant"]
    revenue_cell, cost_cell, profit_cell = ("K68", "K146", "K147") if variant == "sale" else ("K51", "K134", "K135")
    groups = _COST_GROUPS[variant]
    source_fields = {
        "total_revenue": {**read(BUSINESS, revenue_cell, "KRW_THOUSAND"), "label": "원본 총매출"},
        "total_cost": {**read(BUSINESS, cost_cell, "KRW_THOUSAND"), "label": "원본 사업비 합계"},
        "development_profit": {**read(BUSINESS, profit_cell, "KRW_THOUSAND"), "label": "원본 경상이익(세전)"},
    }
    for key, category, label in (("land_cost", "land", "토지비·부대비"), ("construction_cost", "construction", "건축공사비"), ("finance_cost", "finance", "금융비용")):
        row = next(row for row, group, _ in groups if group == category)
        source_fields[key] = {**read(BUSINESS, f"K{row}", "KRW_THOUSAND"), "label": label}
    source_fields["other_cost"] = _source_sum(read, [f"K{row}" for row, group, _ in groups if group not in {"land", "construction", "finance"}], "기타 사업비 합계")
    financing = map_sangbong_financing(formula_wb, value_wb, variant, read_cell=read)
    financing["financingTermReview"]["sourceWorkbook"] = source_name
    source_fields["pf_amount"] = {**financing["pfAmount"], "label": "초기 PF 약정 계획액"}
    for record in source_fields.values():
        record.setdefault("applyEligible", record["value"] is not None)
        record.setdefault("amountBasis", "source_declared_budget")
    rental_basis = _rental_revenue_basis(read) if variant == "rental" else None
    if rental_basis:
        source_fields["total_revenue"].update(label="총매출·임대수익(분양전환 정산 기준)",
            applyEligible=rental_basis["status"] == "matched", amountBasis=rental_basis["policy"])
        if rental_basis["status"] != "matched":
            source_fields["development_profit"]["applyEligible"] = False

    cost_items = [_line_item(f"cost_{row}", label, group, read(BUSINESS, f"K{row}", "KRW_THOUSAND"), source_name) for row, group, label in groups]
    if variant == "sale":
        revenue_items = [_line_item(key, label, category, read(BUSINESS, cell, "KRW_THOUSAND"), source_name)
                         for key, label, category, cell in (("private_housing", "공동주택·발코니", "sales_revenue", "K46"),
                         ("public_housing", "공공임대주택 매각", "sales_revenue", "K53"), ("commercial", "근린시설 매각", "sales_revenue", "K67"))]
    else:
        gross_record = {"value": rental_basis["grossConversionKrw"], "rawValue": rental_basis.get("grossConversionRawThousand"),
                        "sourceCell": f"'{BUSINESS}'!U33,AB33,U34,AB34", "formula": "=U33*AB33+U34*AB34"}
        gross_item = _line_item("gross_conversion", "분양전환 총대금(보증금 정산 전)", "sales_revenue", gross_record, source_name)
        gross_item["formula"]["formula_type"] = "derived"
        revenue_items = [gross_item, *[_line_item(key, label, category, read(BUSINESS, cell, "KRW_THOUSAND"), source_name)
                         for key, label, category, cell in (("rents", "임대료", "rental_revenue", "K32"),
                         ("public_housing", "장기전세주택 매각", "sales_revenue", "K43"), ("commercial", "근린시설 매각", "sales_revenue", "K50"))]]

    product_mix = []
    product_revenue_mappings = {}
    for row in [*range(25, 30), *range(37, 41), 58]:
        units = read(OVERVIEW, f"C{row}", "COUNT")["value"]
        if units is None or units <= 0:
            continue
        private = row < 30
        commercial = row >= 58
        label = read(OVERVIEW, f"B{row}")["value"]
        product_revenue = _product_revenue(read, variant, row, units)
        product_revenue_mappings[f"source_product_{row}"] = product_revenue
        product_mix.append({"key": f"source_product_{row}", "asset_type": "commercial" if commercial else "apartment",
            "product_name": str(label) if commercial else ("민간주택 " if private else "공공임대주택 ") + str(read(OVERVIEW, f"D{row}", "SQM")["value"]) + "㎡",
            "unit_count": units, "exclusive_area_sqm": read(OVERVIEW, f"D{row}", "SQM")["value"],
            "saleable_area_sqm": read(OVERVIEW, f"F{row}", "SQM")["value"] if not commercial else read(OVERVIEW, f"D{row}", "SQM")["value"],
            "contract_area_sqm": read(OVERVIEW, f"H{row}", "SQM")["value"],
            "revenue": product_revenue["value"],
            "excel_row_id": f"{OVERVIEW}!{row}", "provenance": _provenance(source_name, OVERVIEW, f"B{row}:H{row}")})

    cash = (map_sangbong_cash_flow(formula_wb[CASH_FLOW], value_wb[CASH_FLOW], variant, source_name)
            if include_cash_flow else {"monthly_cash_flow": [], "cashFlowPeriodBasis": {}, "cashFlowReview": {}})
    source_schedule = []
    if cash["cashFlowPeriodBasis"].get("kind") == "calendar_monthly":
        for period in cash["cashFlowPeriodBasis"].get("periods", []):
            event = str(period.get("sourceEvent") or "").strip()
            event_key = {"PF실행/착공": "construction_start", "준공": "completion", "입주": "occupancy", "분양전환": "conversion_sale"}.get(event)
            if event_key:
                locator = period["sourceLocator"]
                source_schedule.append({"key": event_key, "label": event, "planned_date": period["label"] + "-01",
                    "month_index": period["sourcePeriodIndex"], "excel": {"sheet_name": CASH_FLOW, "cell": locator["cell"]},
                    "provenance": _provenance(source_name, CASH_FLOW, locator["cell"], event)})
    unit_parts = [read(OVERVIEW, cell, "COUNT")["value"] for cell in ("C30", "C41", "C58")]
    unit_total = sum(unit_parts) if all(part is not None for part in unit_parts) else None
    mapped_units = sum(item["unit_count"] for item in product_mix)
    product_unit_check = {"mappedUnits": mapped_units, "sourceUnits": unit_total,
                          "matched": unit_total is not None and mapped_units == unit_total}
    area_totals = {}
    for key, column, label in (("saleable_area_sqm", "D", "전용면적 합계"), ("contract_area_sqm", "H", "계약면적 합계")):
        parts = [read(OVERVIEW, f"{column}{row}", "SQM")["value"] for row in (30, 41, 58)]
        area_totals[key] = {"key": key, "label": label, "value": sum(parts) if all(part is not None for part in parts) else None,
                           "unit": "SQM", "excel": {"sheet_name": OVERVIEW, "cell": f"{column}30,{column}41,{column}58"},
                           "provenance": _provenance(source_name, OVERVIEW, f"{column}30,{column}41,{column}58")}
    def value_record(key, label, cell, unit):
        record = read(OVERVIEW, cell, unit)
        return {"key": key, "label": label, "value": record["value"], "unit": unit,
                "excel": {"sheet_name": OVERVIEW, "cell": cell}, "provenance": _provenance(source_name, OVERVIEW, cell, record["rawValue"])}

    revenue, cost, profit = (source_fields[key]["value"] for key in ("total_revenue", "total_cost", "development_profit"))
    pf_amount = source_fields["pf_amount"]["value"]
    metrics = {"total_revenue": revenue, "total_cost": cost, "development_profit": profit,
               "profit_margin": profit / revenue if profit is not None and revenue else None,
               "profit_on_cost": profit / cost if profit is not None and cost else None,
               "ltc": pf_amount / cost if pf_amount is not None and cost else None,
               "required_equity": None, "max_minus": None, "ltv": None, "exit_sale_rate": None, "break_even_sale_rate": None}
    limitations = ["수식 값은 원본에 저장된 계산값이며 Excel 전체 재계산과 정식 승인을 완료한 값이 아닙니다.",
                  "대출 조건은 사업계획 가정입니다. 실제 약정·법적 순위·금리 변동 조건은 별도 확인해야 합니다.",
                  "기부채납 전 원대지와 실대지, 설계 용적률과 법정 용적률을 구분합니다.",
                  "원본 현금흐름의 조달 후 잔액은 최소 필요자금이나 Max Minus로 대체하지 않습니다."]
    limitations.append("원본의 대출 만기·공사기간 가정과 현금흐름 달력의 실제 간격은 별도 기준입니다. 단계 월차를 실제 경과 월로 치환하거나 만기를 자동 수정하지 않습니다.")
    reported = {"sourceRevenue": deepcopy(source_fields["total_revenue"]), "sourceCost": deepcopy(source_fields["total_cost"]),
                "sourceProfit": deepcopy(source_fields["development_profit"])}
    if variant == "sale":
        reported.update(nonDeductibleVat=read(BUSINESS, "K148", "KRW_THOUSAND"), afterVatProfit=read(BUSINESS, "K149", "KRW_THOUSAND"))
        limitations.append("원본 경상이익과 부가세 불공제 후 매출이익은 별도 기준으로 표시합니다.")
    else:
        reported.update(initialDeposits=read(BUSINESS, "K25", "KRW_THOUSAND"), depositIncreases=read(BUSINESS, "K29", "KRW_THOUSAND"), netConversionReceipts=read(BUSINESS, "K35", "KRW_THOUSAND"))
        limitations.append(rental_basis["message"])
    cell_records = []
    end_row = 149 if variant == "sale" else 135
    for row in range(23, end_row + 1):
        record = read(BUSINESS, f"K{row}", "KRW_THOUSAND")
        label = read(BUSINESS, f"E{row}")["value"] or read(BUSINESS, f"H{row}")["value"] or read(BUSINESS, f"B{row}")["value"]
        if record["rawValue"] is not None or label:
            cell_records.append({**record, "label": label or f"원본 {row}행", "includedInCanonicalTotals": False})
    result = {
        "project_name": read(OVERVIEW, "C1")["value"], "asset_type": "mixed_use",
        "site": {"address": read(OVERVIEW, "C5")["value"], "zoning": read(OVERVIEW, "C6")["value"],
                 "land_area_sqm": value_record("land_area_sqm", "원대지면적", "D7", "SQM")},
        "program": {"gross_floor_area_sqm": value_record("gross_floor_area_sqm", "연면적", "D15", "SQM"),
                    **area_totals, "unit_count": {"key": "unit_count", "label": "공급 세대·호실수", "value": unit_total,
                    "unit": "COUNT", "provenance": _provenance(source_name, OVERVIEW, "C30,C41,C58")}},
        "product_mix": product_mix, "revenue_items": revenue_items, "cost_items": cost_items,
        "financing": financing["financing"], "schedule": source_schedule,
        "scenarios": [], "monthly_cash_flow": cash["monthly_cash_flow"], "metrics": metrics,
        "raw_import": {"profile": profile["profile"], "mappingVersion": VERSION, "sourceWorkbook": source_name,
            "unitScale": {BUSINESS: "KRW_THOUSAND", CASH_FLOW: "KRW_MILLION"},
            "financialMapping": {"schemaVersion": VERSION, "variant": variant, "status": "needs_review",
                "sourceFields": source_fields, "reportedTotals": reported, "limitations": limitations},
            "cellRecords": cell_records, "rentalRevenueBasis": rental_basis, "productRevenueMappings": product_revenue_mappings,
            "financingTermReview": financing["financingTermReview"], "refinancingReview": financing.get("refinancingReview"),
            "cashFlowPeriodBasis": cash["cashFlowPeriodBasis"], "cashFlowReview": cash["cashFlowReview"],
            "originalSiteArea": read(OVERVIEW, "D7", "SQM"), "netSiteArea": read(OVERVIEW, "D11", "SQM"),
            "programBasis": {"unitCountSourceCells": ["C30", "C41", "C58"],
                "unitCountComplete": all(part is not None for part in unit_parts),
                "productUnitCheck": product_unit_check,
                "excludedPublicFacilities": [{"label": read(OVERVIEW, f"B{row}")["value"],
                    "units": read(OVERVIEW, f"C{row}", "COUNT"),
                    "exclusiveArea": read(OVERVIEW, f"D{row}", "SQM"),
                    "contractArea": read(OVERVIEW, f"H{row}", "SQM")} for row in (59, 60)],
                "reason": "원본 공공업무시설은 별도 검토하며 분양 가능한 상업시설 수량·면적에 합산하지 않습니다."},
            "designFar": read(OVERVIEW, "C17", "RATIO"), "limitations": limitations,
            "unresolvedFields": ["required_equity", "max_minus", "ltv", "exit_sale_rate", "break_even_sale_rate"],
            "canonicalStatus": "candidate", "analysisOnly": True, "reportEligible": False},
    }
    from agents.cash_flow_mapper import captured_study_financial_binding
    result["raw_import"]["sourceFinancialInputHash"] = captured_study_financial_binding(result)
    return result


def validate_sangbong_workbook(formula_wb, value_wb, profile, *, formula_integrity: dict, file_name: str, tolerance: float = 1.0) -> dict:
    mapping = map_sangbong_workbook(formula_wb, value_wb, profile, source_name=file_name, include_cash_flow=False)
    fields = mapping["raw_import"]["financialMapping"]["sourceFields"]
    def check(name, actual, expected, tolerance_krw=1):
        available = actual is not None and expected is not None
        difference = actual - expected if available else None
        return {"name": name, "lhs": actual, "rhs": expected, "diff": difference,
                "tolerance": tolerance_krw, "unit": "KRW", "passed": available and abs(difference) <= tolerance_krw,
                "severity": "ok" if available and abs(difference) <= tolerance_krw else "blocking"}
    numeric = {key: record["value"] for key, record in fields.items()}
    costs = [item["amount"] for item in mapping["cost_items"]]
    revenues = [item["amount"] for item in mapping["revenue_items"]]
    # Each component was rounded to won; the sum's maximum rounding interval is
    # stated explicitly and never called a new cost buffer or a source correction.
    checks = [check("source_profit_equals_revenue_minus_cost", numeric["development_profit"],
                    numeric["total_revenue"] - numeric["total_cost"] if numeric["total_revenue"] is not None and numeric["total_cost"] is not None else None),
              check("cost_categories_match_source_total", sum(costs) if all(v is not None for v in costs) else None,
                    numeric["total_cost"], max(1, len(costs) // 2 + 1)),
              check("revenue_categories_match_source_total", sum(revenues) if all(v is not None for v in revenues) else None,
                    numeric["total_revenue"], max(1, len(revenues) // 2 + 1))]
    basis = mapping["raw_import"].get("rentalRevenueBasis")
    if basis:
        checks.append({"name": "rental_deposit_conversion_offset", "passed": basis["status"] == "matched",
                       "severity": "ok" if basis["status"] == "matched" else "blocking", "details": basis})
    unit_check = mapping["raw_import"]["programBasis"]["productUnitCheck"]
    checks.append({"name": "product_units_match_source_total", "passed": unit_check["matched"],
                   "severity": "ok" if unit_check["matched"] else "blocking", "details": unit_check})
    product_revenues = [item["revenue"] for item in mapping["product_mix"]]
    product_total = sum(product_revenues) if all(value is not None for value in product_revenues) else None
    if basis and product_total is not None:
        rent = next(item["amount"] for item in mapping["revenue_items"] if item["key"] == "rents")
        product_total = product_total + rent if rent is not None else None
    checks.append(check("product_consideration_matches_source_total", product_total,
                        numeric["total_revenue"], max(1, len(product_revenues) // 2 + 1)))
    key_checks = [{"key": key, "passed": record["value"] is not None, "sheetName": BUSINESS,
                   "cell": record["sourceCell"], "severity": "ok" if record["value"] is not None else "blocking"} for key, record in fields.items()]
    return {"status": "needs_review", "profile": profile["profile"], "fileName": file_name,
            "sheetCount": len(formula_wb.worksheets),
            "sheets": [{"sheetName": sheet.title, "maxRow": sheet.max_row, "maxColumn": sheet.max_column} for sheet in formula_wb.worksheets],
            "requiredSheetChecks": [{"sheetName": name, "present": name in formula_wb.sheetnames} for name in profile["required_sheets"]],
            "keyCells": {key: {**record, "sheetName": BUSINESS, "cell": record["sourceCell"]} for key, record in fields.items()},
            "requiredKeyCellChecks": key_checks, "reconciliationChecks": checks, "formulaIntegrity": formula_integrity,
            "calculationEvidence": {"method": "saved_workbook_cache", "recalculated": False},
            "templateSupport": {"status": "supported_review_mapping", "mappingVersion": VERSION, "variant": profile["variant"]},
            "schemaReadiness": {"directV2ImportReady": False, "requiresExplicitMapping": False, "requiresLegacyAdapter": False,
                                "missingRequiredNamedRanges": [], "sectionsCovered": {key: True for key in ("site", "program", "product_mix", "revenue", "costs", "financing", "monthly_cash_flow", "review")},
                                "notes": ["전용 셀 매핑으로 검토 초안을 추출합니다. 수식 재계산·확정 입력 승인은 별도 절차입니다."]},
            "sourceFinancialReview": mapping["raw_import"]["financialMapping"],
            "recommendations": ["추출값·단위·원본 셀을 확인하고 검토 입력으로 저장하세요.", "원본 수식 오류와 재계산 상태는 정식 승인 전에 확인해야 합니다."]}


def import_sangbong_workbook(path, validation, project_id, source_document):
    formula_wb = load_workbook(path, data_only=False, read_only=True)
    value_wb = load_workbook(path, data_only=True, read_only=True)
    try:
        profile = detect_sangbong_profile(formula_wb)
        if not profile or profile["profile"] != validation.get("profile"):
            raise ValueError("상봉 양식과 검증 프로필이 일치하지 않습니다.")
        source_name = (source_document or {}).get("originalFileName") or path.name
        draft = map_sangbong_workbook(formula_wb, value_wb, profile, source_name=source_name)
        draft["project_id"] = project_id
        draft["raw_import"].update(sourceDocument=deepcopy(source_document or {}), validationStatus=validation["status"])
        from agents.cash_flow_mapper import captured_study_financial_binding
        draft["raw_import"]["sourceFinancialInputHash"] = captured_study_financial_binding(draft)
        return draft
    finally:
        formula_wb.close()
        value_wb.close()
