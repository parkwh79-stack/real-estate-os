from copy import deepcopy

import pytest

from agents.cash_flow_engine import run_cash_flow_engine
from core.cash_flow_periods import PERIOD_BASIS_VERSION, require_stress_calendar_period
from core.financial_input_validation import FinancialInputError
from core.reviewed_monthly_financing import MONTHLY_FINANCING_POLICY
from use_cases.cash_flow_comparison import compare_cash_flow_runs, content_hash
from use_cases.cash_flow_dependencies import dependent_inputs, financial_input_hash, scale_cash_flow_request


def _basis():
    return {
        "schemaVersion": PERIOD_BASIS_VERSION, "kind": "source_sequence",
        "periods": [
            {"sourcePeriodIndex": index, "label": label, "periodKind": kind,
             "sourceMonthNumber": month,
             "sourceLocator": {"sourceDocument": "검토 사업수지.xlsx", "sheetName": "2.현금흐름", "cell": cell}}
            for index, label, kind, month, cell in (
                (0, "토지계약", "event", None, "F4"),
                (1, "토지잔금", "event", None, "G4"),
                (2, "1", "monthly", 1, "H4"),
            )
        ],
        "scheduleBindings": {"construction_start": {"sourcePeriodIndex": 2, "sourceMonthNumber": 1}},
        "warnings": ["사전 사건의 날짜와 경과기간이 미확인입니다."],
    }


def _request(*, source_sequence=True, monthly=False):
    request = {
        "project": {"projectId": "period-review", "projectName": "기간 검토"},
        "sourcesAndUses": {
            "revenue": {"totalSalesRevenue": 300},
            "cost": {"landCost": 100, "directConstructionCost": 0, "financialCost": 9,
                     "otherCost": 0, "totalProjectCost": 109},
            "funding": {"pfLoan": 200},
        },
        "schedule": {"construction_start": {"plannedDate": "2026-01-01", "monthIndex": 0},
                     "completion": {"plannedDate": "2026-03-01", "monthIndex": 2}},
        "productMix": [{"totalSalesRevenue": 300, "collectionSteps": [
            {"stage": "balance", "monthlyCollectionAmounts": [0, 0, 300]},
        ]}],
        "costSchedule": {"rows": [{"monthIndex": 0, "landCost": 100}, {"monthIndex": 1, "financialCost": 9}]},
        "pfStructure": [{"trancheId": "senior", "tranche": "선순위", "name": "senior", "amount": 200,
                         "interestRate": .12, "feeRate": .01, "maturityMonths": 2,
                         "repaymentPriority": 1, "repaymentSource": "repayment_reserve"}],
    }
    if source_sequence:
        request["periodBasis"] = _basis()
    if monthly:
        request["monthlyFinancing"] = dict(MONTHLY_FINANCING_POLICY)
    return request


def _run(request, identity):
    result = run_cash_flow_engine(request)
    digest = financial_input_hash(request)
    return {"calculationRunId": identity, "engineKey": "cash_flow_engine", "engineVersion": "v1",
            "status": "completed", "financialInputHash": digest,
            "inputSnapshot": deepcopy(request), "resultSnapshot": result}


def test_source_sequence_preserves_budget_money_and_all_input_periods_without_inventing_months():
    request = _request()
    original = deepcopy(request)
    result = run_cash_flow_engine(request)
    calendar_request = deepcopy(request)
    del calendar_request["periodBasis"]
    calendar_result = run_cash_flow_engine(calendar_request)

    assert request == original
    assert result["summary"] == calendar_result["summary"]
    assert result["salesCollectionSchedule"] == calendar_result["salesCollectionSchedule"]
    for source_row, calendar_row in zip(result["monthlyCashFlow"], calendar_result["monthlyCashFlow"], strict=True):
        assert {key: value for key, value in source_row.items() if key != "period"} == {
            key: value for key, value in calendar_row.items() if key != "period"
        }
    assert [row["period"] for row in result["monthlyCashFlow"]] == ["원본 1: 토지계약", "원본 2: 토지잔금", "원본 3: 1"]
    assert result["periodBasis"] == {**original["periodBasis"], "calculationPeriodCount": 3,
        "firstCalculationPeriodIndex": 0, "lastCalculationPeriodIndex": 2,
        "interestCalculation": {"status": "not_calculated", "reason": "source_period_duration_unconfirmed",
            "firstChargedMonthIndex": None, "lastChargedMonthIndex": None, "chargedPeriodCount": 0}}
    assert result["periodBasis"] is not request["periodBasis"]
    result["periodBasis"]["periods"][0]["label"] = "변경된 표시"
    assert request == original
    assert result["periodBasis"]["interestCalculation"]["status"] == "not_calculated"
    assert result["periodBasis"]["interestCalculation"]["chargedPeriodCount"] == 0
    assert "interestCalculation" not in result
    assert result["engineVersion"] == "cash_flow_engine-v2"
    assert result["scheduleProvenance"]["reportEligible"] is False
    assert result["dataStatus"] == "partial"


def test_unconfirmed_source_events_reject_monthly_interest_before_any_financial_formula(monkeypatch):
    request = _request(monthly=True)
    original = deepcopy(request)
    monkeypatch.setattr("agents.cash_flow_engine.ReviewedMonthlyFinancing", lambda *_: pytest.fail("unconfirmed duration must be rejected first"))
    with pytest.raises(FinancialInputError) as caught:
        run_cash_flow_engine(request)
    assert caught.value.code == "monthly_financing_period_unconfirmed"
    message = str(caught.value)
    for text in ("토지계약", "토지잔금", "검토 사업수지.xlsx", "2.현금흐름!F4", "2.현금흐름!G4", "예산 현금흐름"):
        assert text in message
    assert request == original


def test_calendar_monthly_policy_keeps_existing_interest_formula_and_records_actual_charged_months():
    request = _request(source_sequence=False, monthly=True)
    original = deepcopy(request)
    result = run_cash_flow_engine(request)
    assert request == original
    assert result["monthlyFinancing"]["calculatedInterest"] == pytest.approx(2.0502)
    assert result["monthlyFinancing"]["calculatedFees"] == 2
    assert result["monthlyFinancing"]["totalPrincipalRepaid"] == pytest.approx(103.02)
    assert result["periodBasis"] == {"schemaVersion": PERIOD_BASIS_VERSION, "kind": "calendar_monthly",
        "monthIndexOrigin": "project_month_zero", "calculationPeriodCount": 3,
        "firstCalculationPeriodIndex": 0, "lastCalculationPeriodIndex": 2,
        "interestCalculation": {"status": "calculated", "method": "opening_balance_monthly_12",
            "firstChargedMonthIndex": 1, "lastChargedMonthIndex": 2, "chargedPeriodCount": 2}}
    assert result["periodBasis"]["interestCalculation"] == {"status": "calculated", "method": "opening_balance_monthly_12",
        "firstChargedMonthIndex": 1, "lastChargedMonthIndex": 2, "chargedPeriodCount": 2}
    assert "interestCalculation" not in result


def test_zero_rate_records_no_charged_interest_period_without_inventing_zero_length_loan():
    request = _request(source_sequence=False, monthly=True)
    request["pfStructure"][0].update(interestRate=0, feeRate=0)
    result = run_cash_flow_engine(request)
    assert result["monthlyFinancing"]["totalDraw"] == 100
    assert result["monthlyFinancing"]["totalPrincipalRepaid"] == 100
    assert result["periodBasis"]["interestCalculation"] == {"status": "calculated", "method": "opening_balance_monthly_12",
        "firstChargedMonthIndex": None, "lastChargedMonthIndex": None, "chargedPeriodCount": 0}


def test_source_comparison_keeps_total_metrics_without_synthesizing_calendar_base():
    baseline = _run(_request(), "baseline")
    changed = _run(_request(), "changed")
    original = deepcopy([baseline, changed])
    result = compare_cash_flow_runs(baseline, changed)
    assert [baseline, changed] == original
    assert result["status"] == "partial"
    assert result["reasons"] == ["source_period_duration_unconfirmed"]
    for side in ("baseline", "comparison"):
        assert result[side]["period"] == {"basis": "source_sequence", "axis": "source_sequence", "baseMonth": None,
            "firstMonthIndex": None, "lastMonthIndex": None, "firstPeriodIndex": 0, "lastPeriodIndex": 2,
            "rowCount": 3, "valid": True}
    assert result["metrics"]
    assert all(item["delta"] == 0 for item in result["metrics"] if item["status"] == "available")


def test_source_and_calendar_comparison_is_partial_but_keeps_project_totals():
    result = compare_cash_flow_runs(_run(_request(), "source"), _run(_request(source_sequence=False), "calendar"))
    assert result["status"] == "partial"
    assert set(result["reasons"]) == {"period_mismatch", "source_period_duration_unconfirmed"}
    assert result["baseline"]["period"]["baseMonth"] is None
    assert result["comparison"]["period"]["baseMonth"] == "2026-01"
    assert next(item for item in result["metrics"] if item["key"] == "totalProjectCost")["delta"] == 0


def test_independent_pf_input_keeps_source_evidence_but_stress_does_not_use_row_count_as_months():
    request = _request()
    result = run_cash_flow_engine(request)
    original = deepcopy([request, result])
    scenario, stress = dependent_inputs(request, result, "parent")
    assert [request, result] == original
    assert scenario["parentCalculationRunId"] == "parent"
    assert scenario["baseCashFlowRequest"]["periodBasis"] == request["periodBasis"]
    assert scenario["pfStructure"][0]["amount"] == 200
    assert scenario["productMix"][0]["totalRevenue"] == 300
    assert stress["schedule"] == {"months": None, "salesEndMonth": None}
    assert stress["periodBasis"] == request["periodBasis"]
    with pytest.raises(FinancialInputError) as caught:
        require_stress_calendar_period(stress)
    assert caught.value.code == "cash_flow_stress_period_unconfirmed"
    assert "원본 행 수를 사업 개월로 대신 넣지 않습니다" in str(caught.value)


def test_existing_relative_month_budget_and_stress_stay_compatible_and_do_not_rewrite_saved_hashes():
    request = _request(source_sequence=False)
    run = _run(request, "old")
    digest = content_hash(run)
    scenario, stress = dependent_inputs(request, run["resultSnapshot"], "old")
    require_stress_calendar_period(stress)
    assert stress["schedule"] == {"months": 2, "salesEndMonth": 2}
    assert "periodBasis" not in run["resultSnapshot"]
    assert content_hash(run) == digest
    comparison = compare_cash_flow_runs(run, {**deepcopy(run), "calculationRunId": "copy"})
    assert comparison["status"] == "ready"
    assert comparison["reasons"] == []


def test_direct_bound_stress_cannot_reinterpret_source_rows_as_monthly_irr():
    request = _request()
    original = deepcopy(request)
    with pytest.raises(FinancialInputError) as caught:
        require_stress_calendar_period({"baseCashFlowRequest": request, "schedule": {"months": 2}})
    assert caught.value.code == "cash_flow_stress_period_unconfirmed"
    with pytest.raises(FinancialInputError) as caught:
        scale_cash_flow_request(request, revenue=300, construction=0, finance=9)
    assert caught.value.code == "cash_flow_stress_period_unconfirmed"
    assert request == original


@pytest.mark.parametrize("change", [
    lambda basis: basis.update(schemaVersion="unrecognized"),
    lambda basis: basis.update(kind="monthly?"),
    lambda basis: basis.update(periods=[]),
    lambda basis: basis["periods"].append(deepcopy(basis["periods"][0])),
    lambda basis: basis["periods"][0].update(sourcePeriodIndex=True),
    lambda basis: basis["periods"][0].update(sourceMonthNumber=0),
])
def test_invalid_explicit_basis_is_not_silently_treated_as_monthly(change):
    request = _request()
    change(request["periodBasis"])
    original = deepcopy(request)
    with pytest.raises(FinancialInputError) as caught:
        run_cash_flow_engine(request)
    assert caught.value.code == "invalid_cash_flow_period_basis"
    assert request == original


def test_calculation_rows_beyond_supplied_source_periods_are_marked_unmatched_without_fake_labels():
    request = _request()
    request["schedule"]["completion"] = {"plannedDate": "2026-04-01", "monthIndex": 3}
    result = run_cash_flow_engine(request)
    assert result["monthlyCashFlow"][3]["period"] == "원본 대응 미확인: 계산 순서 4"
    assert result["periodBasis"]["calculationPeriodCount"] == 4
    assert len(result["periodBasis"]["periods"]) == 3


@pytest.mark.parametrize("source", ["row_locator", "section_locator", "source_type", "cost_schedule_alias"])
def test_resubmitted_old_workbook_event_request_needs_duration_review_only_when_executed(source):
    request = _request(source_sequence=False, monthly=True)
    row = request["costSchedule"]["rows"][0]
    row["period"] = "토지계약"
    locator = {"sourceDocument": "이전 원본.xlsx", "sheetName": "2.현금흐름", "cell": "F35:F119"}
    if source == "row_locator":
        row["sourceLocator"] = locator
    elif source == "section_locator":
        request["costSchedule"]["sourceLocator"] = locator
    elif source == "source_type":
        request["costSchedule"]["sourceType"] = "workbook_schedule_candidate"
    else:
        row["source_locator"] = locator
        request["cost_schedule"] = request.pop("costSchedule")
    original = deepcopy(request)
    with pytest.raises(FinancialInputError) as caught:
        run_cash_flow_engine(request)
    assert caught.value.code == "monthly_financing_period_unconfirmed"
    assert "토지계약" in str(caught.value)
    if source != "source_type":
        assert "이전 원본.xlsx / 2.현금흐름!F35:F119" in str(caught.value)
    assert request == original
    assert "periodBasis" not in request

    # The historical budget request remains executable and unchanged. Merely
    # reading its summary must not retroactively synthesize a source basis.
    budget = deepcopy(request)
    del budget["monthlyFinancing"]
    historical = _run(budget, "historical-budget")
    digest = content_hash(historical)
    summary = compare_cash_flow_runs(historical, {**deepcopy(historical), "calculationRunId": "copy"})
    assert summary["status"] == "ready"
    assert "periodBasis" not in historical["inputSnapshot"]
    assert "periodBasis" not in historical["resultSnapshot"]
    assert content_hash(historical) == digest


@pytest.mark.parametrize("case", ["user_event_name", "monthly_excel_label", "explicit_calendar_assumption", "unrelated_metadata"])
def test_legacy_guard_keeps_explicit_months_and_user_calendar_assumptions(case):
    request = _request(source_sequence=False, monthly=True)
    row = request["costSchedule"]["rows"][0]
    if case == "user_event_name":
        row.update(period="토지계약", sourceLocator={"sourceType": "user_assumption"})
    elif case == "monthly_excel_label":
        row.update(period="M0", sourceLocator={"sourceType": "excel_range", "sheetName": "CF", "cell": "F35:F119"})
    elif case == "explicit_calendar_assumption":
        row.update(period="토지계약", sourceLocator={"sourceType": "excel_range", "sheetName": "CF", "cell": "F35:F119"})
        request["periodBasis"] = {"schemaVersion": PERIOD_BASIS_VERSION, "kind": "calendar_monthly"}
    else:
        request["sourceMetadata"] = {"note": "토지계약 workbook"}
    original = deepcopy(request)
    result = run_cash_flow_engine(request)
    assert result["monthlyFinancing"]["calculatedInterest"] == pytest.approx(2.0502)
    assert request == original
