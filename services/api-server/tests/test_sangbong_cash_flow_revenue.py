from copy import deepcopy

import pytest

from agents.cash_flow_mapper import build_cash_flow_run_request_from_feasibility_study, captured_study_financial_binding
from agents.cash_flow_engine import run_cash_flow_engine
from use_cases.cash_flow import build_cash_flow_request_use_case


def _request(items=None, total=999):
    study = {"project_name": "상봉 수익 매핑 검토", "metrics": {"total_revenue": total}}
    if items is not None:
        study["revenue_items"] = [
            {"key": key, "label": key, "category": category, "amount": amount}
            for key, category, amount in items
        ]
    original = deepcopy(study)
    request = build_cash_flow_run_request_from_feasibility_study(study)
    assert study == original
    return request


def test_sales_components_are_summed_once_without_adding_declared_total():
    request = _request([
        ("private", "sales_revenue", 180),
        ("public", "sales", 30),
        ("commercial", "sales_revenue", 20),
    ], total=230)
    assert request["sourcesAndUses"]["revenue"]["totalSalesRevenue"] == 230


def test_rental_income_is_separate_from_conversion_sales_and_deposits():
    request = _request([
        ("conversion", "sales_revenue", 288),
        ("public", "sales_revenue", 7),
        ("commercial", "sales_revenue", 14),
        ("rent", "rental_revenue", 37),
        ("deposit", "lease_deposit", 54),
    ], total=346)
    assert request["sourcesAndUses"]["revenue"] == {
        "totalSalesRevenue": 309, "otherRevenue": 37, "leaseDeposit": 54,
        "forwardSaleProceeds": 0, "refinancingProceeds": 0,
    }


def test_explicit_zero_sales_do_not_revert_to_declared_total():
    request = _request([("unsold", "sales_revenue", 0)], total=999)
    assert request["sourcesAndUses"]["revenue"]["totalSalesRevenue"] == 0
    assert "total_sales_revenue_missing" not in request["mappingDiagnostics"]["warnings"]


@pytest.mark.parametrize("category,bucket", [
    ("sales_revenue", "totalSalesRevenue"), ("rental_revenue", "otherRevenue"),
    ("lease_deposit", "leaseDeposit"), ("forward_sale", "forwardSaleProceeds"),
    ("refinancing", "refinancingProceeds"),
])
def test_incomplete_components_are_unknown_instead_of_silent_partial_sum(category, bucket):
    request = _request([("known", category, 10), ("missing", category, None)])
    assert request["sourcesAndUses"]["revenue"][bucket] is None
    assert request["mappingDiagnostics"]["missingRevenueItems"] == [
        {"key": "missing", "category": category, "bucket": bucket},
    ]
    assert "revenue_line_items_incomplete" in request["mappingDiagnostics"]["warnings"]


def test_declared_sales_fallback_is_used_when_no_sales_line_items_exist():
    assert _request(total=321)["sourcesAndUses"]["revenue"]["totalSalesRevenue"] == 321
    assert _request([("rent", "rental_revenue", 7)], total=321)["sourcesAndUses"]["revenue"]["totalSalesRevenue"] == 321


def _captured_study():
    # Income and cost are intentionally on a different basis from the business
    # headline; deposits and PF are financing, never extra project revenue.
    study = {"project_id": "3", "project_name": "원본 계획", "metrics": {"total_revenue": 100, "total_cost": 80},
             "revenue_items": [{"key": "sales", "label": "sales", "category": "sales_revenue", "amount": 100}],
             "financing": [{"tranche_id": "pf", "name": "initial", "priority": "senior", "amount": 60}],
             "monthly_cash_flow": [
                 {"month_index": 0, "period": "2025-01", "cash_in": {"refund": 5}, "cash_out": {"build": 80},
                  "pf_draw": {"pf": 60}, "equity_contribution": {"equity": 20},
                  "other_financing_inflow": {"deposit": 10}, "net_cash_flow": 15, "cumulative_cash_flow": 15},
                 {"month_index": 1, "period": "2025-02", "cash_in": {"net_sale": 90}, "cash_out": {},
                  "loan_repayment": {"pf": 60}, "equity_recovery": {"equity": 20},
                  "net_cash_flow": 10, "cumulative_cash_flow": 25},
             ], "raw_import": {"profile": "sangbong-sale-feasibility", "sourceWorkbook": "source.xlsx",
                 "sourceDocument": {"originalFileHash": "a" * 64},
                 "cashFlowPeriodBasis": {"schemaVersion": "cash-flow-period-basis-v1", "kind": "calendar_monthly",
                     "periods": [{"sourcePeriodIndex": i, "label": date, "periodKind": "monthly"}
                                 for i, date in enumerate(("2025-01", "2025-02"))]},
                 "cashFlowReview": {"transactionMappingComplete": True, "calendarVerified": True, "periodControlsMatched": True,
                     "periodChecks": [{"sourcePeriodIndex": i, "passed": True, "cumulativePassed": True,
                         "sourceNetKrw": net, "sourceBalanceKrw": cumulative, "roundingBoundKrw": 0,
                         "cumulativeRoundingBoundKrw": 0} for i, (net, cumulative) in enumerate(((15, 15), (10, 25)))],
                     "sourceControls": [{"passed": False, "differenceKrwBeforeRounding": 1}]}}}
    for i, row in enumerate(study["monthly_cash_flow"]):
        row["source_locator"] = {"complete": True, "sheetName": "2. 캐쉬_분양률100%", "cell": f"{i}:transactions",
                                 "transactionRowPolicy": "sangbong-cash-flow-map-v1"}
    study["raw_import"]["sourceFinancialInputHash"] = captured_study_financial_binding(study)
    return study


def _registered_project(study=None):
    from use_cases.workbook_import_approval import feasibility_study_draft_hash
    study = study or _captured_study()
    draft_hash = feasibility_study_draft_hash(study)
    study["raw_import"]["candidateImport"] = {"importId": "import-3", "status": "candidate", "draftHash": draft_hash}
    return {"id": "3", "name": "운영 프로젝트", "feasibilityStudyV2Draft": study,
            "_meta": {"latestFeasibilityWorkbookCandidateId": "import-3", "feasibilityWorkbookCandidates": [
                {"importId": "import-3", "status": "candidate", "draftHash": draft_hash, "sourceDocumentHashes": ["a" * 64]}]},
            "source_documents": [{"documentType": "feasibility_workbook", "originalFileHash": "a" * 64,
                                  "feasibilityStudyV2DraftHash": draft_hash}]}


def test_source_plan_preserves_financing_and_business_basis_without_pricing_products():
    request = build_cash_flow_request_use_case(_registered_project())
    original = deepcopy(request)
    result = run_cash_flow_engine(request)
    assert request == original
    assert result["sourceBasis"] == "captured_workbook_cash_flow"
    assert result["analysisOnly"] is True and result["reportEligible"] is False
    assert result["summary"]["totalRevenue"] == 95
    assert result["summary"]["businessDeclaredTotals"]["total_revenue"] == 100
    assert result["summary"]["totalOtherFinancingInflow"] == 10
    assert result["summary"]["totalPfDraw"] == result["summary"]["totalPrincipalRepaid"] == 60
    assert result["summary"]["endingCash"] == 25
    assert result["summary"]["maxMinus"] == -75
    assert result["summary"]["requiredPfLimit"] is None
    assert result["summary"]["peakUnfinancedOperatingRequirement"] == 75
    assert result["monthlyCashFlow"][1]["equityRecovery"] == {"equity": 20}
    assert "source_declared_total_differs_from_monthly_source_cells" in result["scheduleProvenance"]["costSchedule"]["warnings"]


@pytest.mark.parametrize("section,key", [("sourcesAndUses", "revenue"), ("productMix", None), ("schedule", None)])
def test_source_plan_cannot_replay_after_mapped_financial_input_changes(section, key):
    request = build_cash_flow_run_request_from_feasibility_study(_captured_study())
    if section == "sourcesAndUses":
        request[section][key]["totalSalesRevenue"] += 1
    elif section == "productMix":
        request[section][0]["totalSalesRevenue"] = 101
    else:
        request[section]["completion"] = {"monthIndex": 12}
    with pytest.raises(ValueError, match="입력이 원본 현금흐름 이후 변경"):
        run_cash_flow_engine(request)


def test_source_plan_cannot_rebind_modified_study_before_request_mapping():
    study = _captured_study()
    study["financing"][0]["amount"] = 500
    request = build_cash_flow_run_request_from_feasibility_study(study)
    assert "capturedWorkbookCashFlow" not in request
    with pytest.raises(ValueError, match="원본 이후 변경된 입력"):
        run_cash_flow_engine(request)


def test_source_transaction_mutation_fails_arithmetic_and_cache_controls():
    request = build_cash_flow_run_request_from_feasibility_study(_captured_study())
    request["capturedWorkbookCashFlow"]["rows"][0]["cash_in"]["refund"] += 1
    with pytest.raises(ValueError, match="보관된 원본 거래"):
        run_cash_flow_engine(request)


@pytest.mark.parametrize("key,value", [
    ("originalFileHash", "b" * 64), ("businessDeclaredTotals", {"total_revenue": 999}),
    ("sourceControls", [{"passed": True}]), ("periodChecks", []),
])
def test_source_capture_content_is_bound_including_provenance_and_controls(key, value):
    request = build_cash_flow_run_request_from_feasibility_study(_captured_study())
    request["capturedWorkbookCashFlow"][key] = value
    with pytest.raises(ValueError, match="보관된 원본 거래"):
        run_cash_flow_engine(request)


def test_incomplete_revenue_cannot_be_calculated_as_zero():
    request = _request([("sales", "sales_revenue", None)])
    with pytest.raises(ValueError, match="미확인 금액"):
        run_cash_flow_engine(request)


def test_external_payload_cannot_self_attest_captured_source():
    forged = build_cash_flow_run_request_from_feasibility_study(_captured_study())
    with pytest.raises(ValueError, match="서버에 등록된 동일 원본"):
        build_cash_flow_request_use_case({"id": "3"}, forged)


def test_exact_registered_server_request_may_be_replayed_as_candidate():
    project = _registered_project()
    request = build_cash_flow_request_use_case(project)
    assert build_cash_flow_request_use_case(project, deepcopy(request)) == request
    assert request["authorityStatus"] == "candidate" and request["reportEligible"] is False


@pytest.mark.parametrize("mutation", ["draft", "archive_hash", "registration", "source_draft_hash"])
def test_invalid_server_registration_does_not_fall_back_to_healthy_zero(mutation):
    project = _registered_project()
    if mutation == "draft":
        project["feasibilityStudyV2Draft"]["metrics"]["total_revenue"] = 900
    elif mutation == "archive_hash":
        project["source_documents"][0]["originalFileHash"] = "b" * 64
    elif mutation == "source_draft_hash":
        project["source_documents"][0]["feasibilityStudyV2DraftHash"] = "b" * 64
    else:
        project["_meta"]["feasibilityWorkbookCandidates"] = []
    with pytest.raises(ValueError, match="원본 후보의 등록 기록"):
        build_cash_flow_request_use_case(project)


def test_working_input_changes_do_not_reuse_older_registered_source_plan():
    project = _registered_project()
    original_request = build_cash_flow_request_use_case(project)
    project["feasibility_input"] = {"total_revenue": 200, "total_cost": 100}
    request = build_cash_flow_request_use_case(project)
    assert "capturedWorkbookCashFlow" not in request
    assert request["sourcesAndUses"]["revenue"]["totalSalesRevenue"] == 200
    with pytest.raises(ValueError, match="서버에 등록된 동일 원본"):
        build_cash_flow_request_use_case(project, original_request)
