"""Source-bound financing mapping for the two reviewed Sangbong workbook layouts.

The construction facilities, subsequent mortgage and historical bridge terms are
separate phases. This module reads review candidates; it never attests a loan
agreement, legal priority, or approval, and never edits the source workbook.
"""
from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any, Callable

_BUSINESS = "1. 사업수지"
_CASH = "2. 캐쉬_분양률100%"


def _number(value: Any, *, ratio: bool = False, months: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        return False
    return (not ratio or value <= 1) and (not months or value > 0 and int(value) == value)


def map_sangbong_financing(
    formula_wb: Any, value_wb: Any, variant: str, *, read_cell: Callable[..., dict],
) -> dict:
    """Map initial commitments and keep refinancing outside the initial PF sum.

    ``read_cell`` owns unit normalization and formula/cache provenance. Only
    context checks and phase selection are performed here; existing financial
    engines retain numerical authority.
    """
    if variant not in {"sale", "rental"}:
        raise ValueError("Unsupported Sangbong financing variant")
    # The importer owns a single-pass worksheet cache. Context validation must
    # use it too: random indexing in a read-only CF sheet reparses its XML.
    context_records: dict[tuple[str, str], dict] = {}

    def _cell(workbook: Any, sheet: str, coordinate: str) -> Any:
        key = (sheet, coordinate)
        if key not in context_records:
            context_records[key] = deepcopy(read_cell(sheet, coordinate, unit="TEXT"))
        record = context_records[key]
        return (record.get("formula") or record.get("rawValue")) if workbook is formula_wb else record.get("rawValue")

    pf_unit = str(_cell(value_wb, _BUSINESS, "AA4") or "")
    budget_unit = str(_cell(value_wb, _BUSINESS, "AS3") or "")
    raw_pf_unit = _cell(formula_wb, _BUSINESS, "AA4")
    raw_budget_unit = _cell(formula_wb, _BUSINESS, "AS3")
    pf_unit_ok = isinstance(raw_pf_unit, str) and not raw_pf_unit.startswith("=") and "백만원" in "".join(pf_unit.split()) and raw_pf_unit == pf_unit
    budget_unit_ok = isinstance(raw_budget_unit, str) and not raw_budget_unit.startswith("=") and "천원" in "".join(budget_unit.split()) and raw_budget_unit == budget_unit
    amount_header_ok = str(_cell(value_wb, _BUSINESS, "AC5") or "").strip() == "참여금액"
    rate_header_ok = str(_cell(value_wb, _BUSINESS, "AM5") or "").strip() == "금리"
    fee_header_ok = str(_cell(value_wb, _BUSINESS, "AI5") or "").strip() == "취급수수료"
    source_fields: dict[str, dict] = {}

    def read(key: str, sheet: str, coordinate: str, unit: str, *, context_ok: bool = True) -> dict:
        record = deepcopy(read_cell(sheet, coordinate, unit=unit))
        value = record.get("value")
        numeric = unit != "TEXT"
        valid = not numeric or _number(value, ratio=unit == "RATIO", months=unit == "MONTH")
        if not context_ok or not valid:
            record["value"] = None
            record["status"] = "missing" if context_ok and record.get("rawValue") is None else "needs_review"
            record["reviewReason"] = "source_context_or_unit_not_confirmed" if not context_ok else "invalid_or_missing_financing_value"
        source_fields[key] = record
        return record

    maturity = read("maturityMonths", _BUSINESS, "K19", "MONTH")
    operating_months = read("operatingMonths", _BUSINESS, "W19", "MONTH") if variant == "rental" else None
    construction_months = read("constructionMonths", _BUSINESS, "W18", "MONTH")
    allocation = read("repaymentAllocation", _BUSINESS, "AP18", "RATIO")
    operating_allocation = read("operatingAllocation", _BUSINESS, "AR18", "RATIO")
    advisory = read("advisoryFeeRate", _BUSINESS, "AI10", "RATIO")
    terms: list[dict] = []

    def facility(tranche_id: str, row: int, cash_row: int, *, phase: str, fee_cell: str,
                 term: dict | None, expected_label: str) -> dict:
        name = str(_cell(value_wb, _BUSINESS, f"AA{row}") or "").strip()
        label_ok = name == expected_label
        prefix = f"financing.{tranche_id}"
        amount = read(prefix + ".amount", _BUSINESS, f"AC{row}", "KRW_MILLION", context_ok=pf_unit_ok and amount_header_ok and label_ok)
        rate = read(prefix + ".interestRate", _BUSINESS, f"AM{row}", "RATIO", context_ok=label_ok and rate_header_ok)
        fee_label = {"X114": "주택기금취급수수료", "X115": "LOAN-1취급수수료", "X116": "LOAN-2취급수수료"}.get(fee_cell)
        fee_context = fee_header_ok if fee_label is None else "".join(str(_cell(value_wb, _BUSINESS, "E" + fee_cell[1:]) or "").split()) == fee_label
        fee = read(prefix + ".feeRate", _BUSINESS, fee_cell, "RATIO", context_ok=label_ok and fee_context)
        table_fee = read(prefix + ".tableFeeRate", _BUSINESS, f"AI{row}", "RATIO", context_ok=label_ok and fee_header_ok)
        all_in = read(prefix + ".allInRate", _BUSINESS, f"AO{row}", "RATIO", context_ok=label_ok)
        cash_label_ok = str(_cell(value_wb, _CASH, f"D{cash_row}") or "").strip() == expected_label
        cash_amount = read(prefix + ".cashFlowAmount", _CASH, f"G{cash_row}", "KRW_MILLION", context_ok=pf_unit_ok and cash_label_ok)
        reconciled = amount.get("value") is not None and cash_amount.get("value") is not None and amount["value"] == cash_amount["value"]
        fields = {"amount": amount, "interest_rate": rate, "fee_rate": fee, "table_fee_rate": table_fee,
                  "all_in_rate": all_in, "term_months": deepcopy(term)}
        entry = {
            "trancheId": tranche_id, "name": name or expected_label, "phase": phase,
            "fields": fields, "cashFlowAmount": cash_amount.get("value"),
            "cashFlowSourceCell": cash_amount.get("sourceCell"), "principalReconciled": reconciled,
            "priorityBasis": "unconfirmed", "repaymentBasis": "workbook_phase_assumption",
            "contractVerified": False, "approvalStatus": "not_requested",
            "unconfirmedTerms": ["약정 체결", "법적 상환순위", "만기와 기산일", "고정·변동금리 조건", "수수료 부과 기준", "인출·상환 조건"],
        }
        if table_fee.get("value") is not None and fee.get("value") is not None and table_fee["value"] != fee["value"]:
            entry["feeRateConflict"] = True
        terms.append(entry)
        return entry

    if variant == "sale":
        initial = [facility("pf", 7, 116, phase="construction", fee_cell="AI7", term=maturity, expected_label="PF")]
        subsequent: list[dict] = []
    else:
        initial = [
            facility("fund", 7, 108, phase="construction", fee_cell="X114", term=maturity, expected_label="기금대출"),
            facility("loan_tr1", 8, 110, phase="construction", fee_cell="X115", term=maturity, expected_label="LOAN_TR1"),
        ]
        subsequent = [facility("mortgage", 9, 112, phase="refinancing", fee_cell="X116", term=operating_months, expected_label="모기지")]

    initial_amounts = [entry["fields"]["amount"] for entry in initial]
    all_initial_present = all(record.get("value") is not None for record in initial_amounts)
    initial_reconciled = all(entry["principalReconciled"] for entry in initial)
    pf_amount = deepcopy(initial_amounts[0])
    if len(initial_amounts) > 1:
        pf_amount.update({
            "value": sum(record["value"] for record in initial_amounts) if all_initial_present else None,
            "rawValue": [record.get("rawValue") for record in initial_amounts],
            "formula": None,
            "calculationExpression": "+".join(str(record.get("sourceCell")) for record in initial_amounts),
            "sourceCell": ", ".join(str(record.get("sourceCell")) for record in initial_amounts),
            "calculationState": "derived_from_source_candidates", "sourceComponents": deepcopy(initial_amounts),
        })
    if not all_initial_present or not initial_reconciled:
        pf_amount["status"] = "needs_review"
        pf_amount["reviewReason"] = "initial_commitment_and_cash_flow_need_reconciliation"
    source_fields["pfAmount"] = pf_amount

    equity_cell, equity_cash_row = ("BE145", 115) if variant == "sale" else ("BE133", 107)
    equity_label_ok = str(_cell(value_wb, _CASH, f"C{equity_cash_row}") or "").strip() == "EQUITY" and str(_cell(value_wb, _CASH, f"D{equity_cash_row}") or "").strip() == "투입자본금"
    equity = read("equity", _BUSINESS, equity_cell, "KRW_THOUSAND", context_ok=budget_unit_ok and equity_label_ok)
    equity_cash = read("equityCashFlow", _CASH, f"G{equity_cash_row}", "KRW_MILLION", context_ok=pf_unit_ok and equity_label_ok)
    equity_reconciled = equity.get("value") is not None and equity_cash.get("value") is not None and equity["value"] == equity_cash["value"]
    equity["principalReconciled"] = equity_reconciled
    if equity.get("value") is not None and not equity_reconciled:
        equity["status"] = "needs_review"
        equity["reviewReason"] = "equity_budget_and_cash_flow_need_reconciliation"

    financing = []
    for entry in initial:
        fields = entry["fields"]
        if fields["amount"].get("value") is None:
            continue
        financing.append({
            "tranche_id": entry["trancheId"], "name": entry["name"], "priority": "other",
            "amount": fields["amount"]["value"],
            "interest_rate": fields["interest_rate"].get("value") if entry["principalReconciled"] else None,
            "fee_rate": fields["fee_rate"].get("value") if entry["principalReconciled"] and not entry.get("feeRateConflict") else None,
            "term_months": maturity.get("value"),
            "repayment_source": "분양수입금(원본 가정)" if variant == "sale" else "준공 후 대환(원본 가정)",
            "excel_row_id": fields["amount"].get("sourceCell"),
        })
    if equity.get("value") is not None:
        financing.append({"tranche_id": "equity", "name": "시행사 자기자본", "priority": "equity",
                          "amount": equity["value"], "excel_row_id": equity.get("sourceCell")})

    phase_evidence = {}
    if variant == "rental":
        for key, coordinate in {
            "fundInitialDraw": "X108", "constructionLoanInitialDraw": "X110", "mortgageDraw": "BR112",
            "fundRepaymentAtRefinance": "BR220", "constructionLoanRepaymentAtRefinance": "BR221",
            "mortgageRepayment": "GH222", "allPhaseDrawTotal": "G114",
        }.items():
            phase_evidence[key] = read("phase." + key, _CASH, coordinate, "KRW_MILLION", context_ok=pf_unit_ok)
    phase_reconciled = True
    candidate_initial_pf = deepcopy(pf_amount)
    if variant == "rental":
        def initial_draw_matches(key: str, commitment: Any) -> bool:
            draw = phase_evidence[key].get("value")
            return _number(draw) and _number(commitment) and draw <= commitment and (draw > 0 or commitment == 0)

        phase_reconciled = (
            initial_reconciled and subsequent[0]["principalReconciled"]
            and initial_draw_matches("fundInitialDraw", initial_amounts[0].get("value"))
            and initial_draw_matches("constructionLoanInitialDraw", initial_amounts[1].get("value"))
            and phase_evidence["mortgageDraw"].get("value") == subsequent[0]["fields"]["amount"].get("value")
            and phase_evidence["fundRepaymentAtRefinance"].get("value") == initial_amounts[0].get("value")
            and phase_evidence["constructionLoanRepaymentAtRefinance"].get("value") == initial_amounts[1].get("value")
        )
        if not phase_reconciled:
            # Do not label a partial/colliding cash-flow phase as a complete PF
            # amount. Original term candidates remain available for review.
            pf_amount["value"] = None
            pf_amount["status"] = "needs_review"
            pf_amount["reviewReason"] = "construction_and_refinancing_phases_need_reconciliation"
            financing = [row for row in financing if row["priority"] == "equity"]

    historical_bridge = {}
    if variant == "sale":
        for key, coordinate, unit in (("label", "AA14", "TEXT"), ("amount", "AC14", "KRW_MILLION"),
                                      ("interestRate", "AM14", "RATIO"), ("feeRate", "AI14", "RATIO")):
            historical_bridge[key] = read("historicalBridge." + key, _BUSINESS, coordinate, unit, context_ok=pf_unit_ok if unit == "KRW_MILLION" else True)

    review = {
        "schemaVersion": "sangbong-workbook-financing-review-v1", "status": "needs_review",
        "approvalStatus": "not_requested", "analysisOnly": True, "reportEligible": False,
        "terms": terms, "initialFacilityIds": [entry["trancheId"] for entry in initial],
        "initialPrincipalReconciled": initial_reconciled, "equityPrincipalReconciled": equity_reconciled,
        "phaseSeparationReconciled": phase_reconciled,
        "unitChecks": {"facilityUnitCell": f"{_BUSINESS}!AA4", "facilityUnitText": pf_unit,
                       "facilityUnitVerified": pf_unit_ok, "amountHeaderVerified": amount_header_ok, "rateHeaderVerified": rate_header_ok, "feeHeaderVerified": fee_header_ok, "budgetUnitCell": f"{_BUSINESS}!AS3",
                       "budgetUnitText": budget_unit, "budgetUnitVerified": budget_unit_ok},
        "constructionMonths": construction_months, "operatingMonths": operating_months,
        "repaymentAllocation": allocation, "operatingAllocation": operating_allocation,
        "advisoryFeeRate": advisory, "historicalBridgeTerms": historical_bridge,
        "limitations": ["원본 수지의 계획 가정이며 체결된 대출 약정이 아닙니다.",
                        "담보·상환순위와 금리 유형은 미확인입니다. 원본 금리만으로 고정금리 약정으로 판단하지 않습니다.",
                        "자문·보증수수료와 취급수수료는 별도 항목이며 자동 합산하지 않습니다."],
    }
    refinancing = {
        "schemaVersion": "sangbong-refinancing-review-v1", "status": "needs_review" if subsequent else "not_applicable",
        "analysisOnly": True, "reportEligible": False, "approvalStatus": "not_requested",
        "initialPfAmount": deepcopy(pf_amount), "candidateInitialPfAmount": candidate_initial_pf,
        "phaseSeparationReconciled": phase_reconciled, "subsequentFacilities": subsequent,
        "phaseEvidence": phase_evidence, "excludeSubsequentFacilitiesFromInitialPf": True,
        "limitations": ["모기지는 기존 건설 단계 대출을 상환하는 후속 자금입니다. 초기 PF 약정액에 중복 합산하지 않습니다."] if subsequent else [],
    }
    return {"financing": financing, "financingTermReview": review, "schedule": [],
            "pfAmount": pf_amount, "equity": equity, "refinancingReview": refinancing,
            "sourceFields": source_fields}
