from copy import deepcopy

import pytest

from agents.cash_flow_mapper import build_cash_flow_run_request_from_feasibility_study
from agents.cash_flow_engine import run_cash_flow_engine
from use_cases.approved_workbook_authority import GOVERNED_WORKBOOK_CORE_FIELDS
from use_cases.cash_flow import build_cash_flow_request_use_case, run_cash_flow_use_case


def _approved_snapshot_binding() -> dict:
    snapshot_id = "approved-snapshot-1"
    snapshot_hash = "a" * 64
    return {
        "input_references": {
            "approved_input_snapshot_id": snapshot_id,
            "approved_input_snapshot_hash": snapshot_hash,
        },
        "raw_import": {
            "canonicalStatus": "approved",
            "parserV2Bridge": {
                "status": "approved",
                "approvedInputSnapshotId": snapshot_id,
                "approvedInputSnapshotHash": snapshot_hash,
                "approvedFields": list(GOVERNED_WORKBOOK_CORE_FIELDS),
            },
        },
    }


def test_equity_is_a_funding_source_not_pf_draw_capacity():
    request = build_cash_flow_run_request_from_feasibility_study({
        "project_name": "PF source classification",
        "financing": [
            {"tranche_id": "senior", "name": "Tr.A", "priority": "senior", "amount": 50_000_000_000},
            {"tranche_id": "mezz", "name": "Tr.B", "priority": "mezzanine", "amount": 10_000_000_000},
            {"tranche_id": "equity", "name": "Equity", "priority": "equity", "amount": 5_000_000_000},
        ],
    })

    assert request["sourcesAndUses"]["funding"]["pfLoan"] == 60_000_000_000
    assert request["sourcesAndUses"]["funding"]["equity"] == 5_000_000_000
    assert [row["name"] for row in request["pfStructure"]] == ["senior", "mezzanine"]
    assert sum(row["amount"] for row in request["pfStructure"]) == 60_000_000_000


def test_legacy_middle_priority_is_canonicalized_before_mapper_output():
    request = build_cash_flow_run_request_from_feasibility_study({
        "project_name": "Legacy tranche alias",
        "financing": [{
            "tranche_id": "tr-b",
            "name": "Tr.B",
            "priority": "middle",
            "amount": 10_000_000_000,
        }],
    })

    assert request["pfStructure"][0]["name"] == "mezzanine"
    assert request["pfStructure"][0]["repaymentPriority"] == 2


def test_cash_flow_horizon_extends_to_last_approved_cost_event():
    request = build_cash_flow_run_request_from_feasibility_study({
        "project_name": "Long collection tail",
        "schedule": [
            {"key": "construction_start", "label": "start", "planned_date": "2026-01"},
            {"key": "completion", "label": "completion", "planned_date": "2026-04"},
        ],
        "monthly_cash_flow": [{
            "month_index": 80,
            "period": "tail-event",
            "cash_out": {"construction_cost": 100},
            "source_locator": {"sheetName": "MonthlyCF", "cell": "CH58"},
        }],
        "metrics": {"total_cost": 100},
        "raw_import": {"canonicalStatus": "approved"},
    })

    result = run_cash_flow_engine(request)

    assert len(result["monthlyCashFlow"]) == 81
    assert result["monthlyCashFlow"][80]["cashOut"]["constructionCost"] == 100


def test_cash_flow_horizon_rejects_unbounded_month_index():
    request = build_cash_flow_run_request_from_feasibility_study({
        "project_name": "Unbounded tail",
        "monthly_cash_flow": [{
            "month_index": 601,
            "period": "invalid-tail",
            "cash_out": {"construction_cost": 100},
        }],
        "metrics": {"total_cost": 100},
        "raw_import": {"canonicalStatus": "approved"},
    })

    with pytest.raises(ValueError, match="supported maximum of 600 months"):
        run_cash_flow_engine(request)


def test_partial_override_cannot_be_silently_ignored_for_approved_workbook():
    project = {
        "canonicalFeasibilityStudyV2": {
            "project_name": "Approved source",
            "metrics": {"total_revenue": 100, "total_cost": 80},
            **_approved_snapshot_binding(),
        }
    }

    with pytest.raises(ValueError, match="partial cash-flow overrides are not allowed"):
        build_cash_flow_request_use_case(project, {"delayMonths": 3})


def test_canonical_status_without_snapshot_binding_is_not_authoritative():
    project = {
        "canonicalFeasibilityStudyV2": {
            "project_name": "Unbound source",
            "metrics": {"total_revenue": 100, "total_cost": 80},
            "raw_import": {"canonicalStatus": "approved"},
        }
    }

    request = build_cash_flow_request_use_case(project)

    assert request["sourcesAndUses"]["revenue"]["totalSalesRevenue"] == 0
    assert request["sourcesAndUses"]["cost"]["totalProjectCost"] == 0


def test_approved_canonical_financing_wins_over_incomplete_working_amount():
    project = {
        "pf_structure": [{"tranche_id": "incomplete", "name": "Working draft", "priority": "senior", "amount": None}],
        "canonicalFeasibilityStudyV2": {
            "project_name": "Approved financing",
            "financing": [{"tranche_id": "approved", "name": "Approved senior", "priority": "senior", "amount": 80}],
            **_approved_snapshot_binding(),
        },
    }

    request = build_cash_flow_request_use_case(project)
    result = run_cash_flow_use_case(project)

    assert request["pfStructure"][0]["amount"] == 80
    assert result["summary"]["pfLimit"] == 80


def test_complete_direct_candidate_is_allowed_with_incomplete_working_amount():
    project = {"id": "scenario-project", "pf_structure": [{"tranche_id": "incomplete", "name": "Working draft", "priority": "senior", "amount": None}]}
    direct_request = {
        "requestType": "cash_flow_run_request_v1",
        "sourcesAndUses": {"cost": {}, "funding": {"pfLoan": 80}},
        "pfStructure": [{"name": "scenario-senior", "amount": 80}],
    }

    request = build_cash_flow_request_use_case(project, direct_request)
    result = run_cash_flow_use_case(project, request)

    assert request["authorityStatus"] == "candidate"
    assert request["reportEligible"] is False
    assert result["summary"]["pfLimit"] == 80


@pytest.mark.parametrize("wrapper_key", [None, "cashFlowRunRequest", "cashFlowRunRequestDraft"])
def test_direct_request_is_project_bound_and_cannot_self_approve(wrapper_key):
    project = {
        "id": "route-project",
        "project": {"id": "route-project", "name": "Route project"},
    }
    direct_request = {
        "requestType": "cash_flow_run_request_v1",
        "projectId": "top-level-spoof",
        "project_id": "top-level-snake-spoof",
        "project": {
            "id": "nested-id-spoof",
            "project_id": "nested-snake-spoof",
            "projectId": "nested-spoof",
            "projectName": "Spoofed project",
        },
        "sourcesAndUses": {
            "revenue": {"totalSalesRevenue": 1_000},
            "cost": {
                "landCost": 0,
                "directConstructionCost": 600,
                "financialCost": 0,
                "otherCost": 0,
                "totalProjectCost": 600,
            },
            "funding": {"pfLoan": 600},
        },
        "productMix": [{
            "productId": "caller-product",
            "type": "apartment",
            "totalSalesRevenue": 1_000,
            "collectionSteps": [{
                "stage": "contract",
                "dueMonth": 0,
                "collectionRate": 1.0,
                "monthlyCollectionAmounts": [1_000],
            }],
            "collectionSourceLocator": {"sheetName": "Caller", "cell": "B2"},
        }],
        "pfStructure": [{"name": "senior", "amount": 600, "drawPriority": 1}],
        "costSchedule": {
            "approvalStatus": "approved",
            "approval": {"approvedBy": "caller"},
            "reportEligible": True,
            "sourceType": "approved_excel_schedule",
            "sourceLocator": {"sheetName": "Caller", "cell": "C2"},
            "rows": [{"monthIndex": 0, "constructionCost": 600}],
        },
        "collectionAllocationRules": {
            "approvalStatus": "approved",
            "approval": {"approvedBy": "caller"},
            "reportEligible": True,
            "sourceType": "approved_workbook_rule",
            "sourceLocator": {"sheetName": "Caller", "cell": "D2"},
            "rules": [{"contractAndInterimToOperating": 0.75}],
        },
        "sourceMetadata": {
            "approvalStatus": "approved",
            "approval": {"approvedBy": "caller", "approvalDecisionId": "forged"},
            "reportEligible": True,
            "sourceType": "approved_workbook",
        },
        "mappingDiagnostics": {"warnings": []},
    }
    input_data = direct_request if wrapper_key is None else {wrapper_key: direct_request}
    original_input = deepcopy(input_data)

    request = build_cash_flow_request_use_case(project, input_data)

    assert input_data == original_input
    assert request is not direct_request
    assert request["project"]["projectId"] == "route-project"
    assert request["project"]["projectName"] == "Route project"
    assert "id" not in request["project"]
    assert "project_id" not in request["project"]
    assert request["projectId"] == "route-project"
    assert request["project_id"] == "route-project"
    assert request["authorityStatus"] == "candidate"
    assert request["reportEligible"] is False

    for section_name in ("sourceMetadata", "costSchedule", "collectionAllocationRules"):
        section = request[section_name]
        assert section["approvalStatus"] == "unapproved"
        assert section["approval"] == {}
        assert section["authorityStatus"] == "candidate"
        assert section["reportEligible"] is False
        assert section["sourceType"] == "external_request_candidate"

    result = run_cash_flow_use_case(project, request)

    assert result["summary"]["projectId"] == "route-project"
    assert result["dataStatus"] == "partial"
    assert result["scheduleProvenance"]["costSchedule"]["reportEligible"] is False
    assert result["scheduleProvenance"]["salesCollection"]["reportEligible"] is False
    assert result["scheduleProvenance"]["collectionAllocation"]["reportEligible"] is False
    assert result["scheduleProvenance"]["reportEligible"] is False
    assert result["reportFragments"]["reportEligible"] is False


def test_legacy_debt_repayment_and_equity_recovery_are_not_project_costs():
    request = build_cash_flow_run_request_from_feasibility_study({
        "project_name": "Transfer separation",
        "monthly_cash_flow": [{
            "month_index": 0,
            "period": "M0",
            "cash_out": {
                "row_58_순공사비": 100,
                "row_93_대출금상환(Tr.A)": 50,
                "row_111_시행사투자자금회수": 20,
            },
        }],
        "metrics": {"total_cost": 100},
        "raw_import": {"canonicalStatus": "approved"},
    })

    row = request["costSchedule"]["rows"][0]
    assert row["constructionCost"] == 100
    assert sum(row[key] for key in ("landCost", "constructionCost", "financialCost", "otherCost")) == 100
    excluded = request["costSchedule"]["excludedFinancingTransfers"]
    assert {item["sourceKey"] for item in excluded} == {
        "row_93_대출금상환(Tr.A)",
        "row_111_시행사투자자금회수",
    }


@pytest.mark.parametrize("charge_label", [
    "학교용지부담금",
    "학교용지분담금",
    "row_185_학교 용지 부담금",
    "row_64_학교 용지 분담금",
    "row_64_예술장식품비",
    "row_58_건축물 미술작품 설치비",
])
def test_statutory_charge_uses_other_cost_and_preserves_source_reconciliation(charge_label):
    locator = {"sheetName": "MonthlyCF", "cell": "M58:M185"}
    original_outflows = {"학교용지 매입비": 300, charge_label: 25, "row_58_순공사비": 100}
    request = build_cash_flow_run_request_from_feasibility_study({
        "project_name": "학교용지 비용 분류",
        "cost_items": [
            {"key": "land_cost", "label": "학교용지 매입비", "category": "land", "amount": 300},
            {"key": "statutory_cost", "label": charge_label, "category": "tax_and_dues", "amount": 25},
            {"key": "construction_cost", "label": "순공사비", "category": "construction", "amount": 100},
        ],
        "monthly_cash_flow": [{
            "month_index": 0,
            "period": "M0",
            "cash_out": original_outflows,
            "source_locator": locator,
        }],
        "metrics": {"total_cost": 425},
        "raw_import": {"canonicalStatus": "approved"},
    })

    row = request["costSchedule"]["rows"][0]
    assert row["landCost"] == 300
    assert row["constructionCost"] == 100
    assert row["otherCost"] == 25
    assert row["sourceBreakdown"] == original_outflows
    assert row["sourceLocator"] == locator
    assert sum(row[key] for key in ("landCost", "constructionCost", "financialCost", "otherCost")) == 425
    assert request["sourcesAndUses"]["cost"]["totalProjectCost"] == 425

    result = run_cash_flow_engine(request)
    assert result["monthlyCashFlow"][0]["cashOut"]["landCost"] == 300
    assert result["monthlyCashFlow"][0]["cashOut"]["otherCost"] == 25
    assert result["scheduleProvenance"]["costSchedule"]["sourceLocator"] == locator
    assert result["scheduleProvenance"]["costSchedule"]["warnings"] == []
