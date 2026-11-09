import use_cases.chief_deal_orchestration as cdo
import pytest
from copy import deepcopy

from core.financial_input_validation import FinancialInputError
from use_cases.chief_pod_records import pod_input_snapshot
from use_cases.development_feasibility import (
    analyze_development_feasibility_use_case,
    development_feasibility_input_basis,
)


def test_cost_schedule_artifact_reads_correct_agent_keys(monkeypatch):
    """cost_schedule pod must read costs/dataQuality.warnings (the keys the
    DevelopmentFeasibilityAgent actually emits), not the old empty costBreakdown/
    dataQualityWarnings keys. Regression for the orchestrator key-mismatch bug."""
    monkeypatch.setattr(
        cdo,
        "analyze_development_feasibility_use_case",
        lambda project_data, options: {
            "costs": {"totalCost": 120, "constructionCost": 80, "costRatio": {"land": 0.2}},
            "dataQuality": {"missingFields": ["feasibility_input.total_revenue"], "warnings": ["feasibility_input.total_revenue 자료 보강 필요"]},
            "downstreamEngineReadiness": {"blockingFields": ["total_revenue"], "canRunCashFlow": False},
        },
    )
    project_data = {"schedule": {"completion": "2027-06"}}
    artifact = cdo._execute_pod_artifact("p1", "cost_schedule", project_data, {})

    assert artifact["artifactType"] == "cost_schedule_pod_result"
    assert artifact["costBreakdown"]["totalCost"] == 120  # was always {} before the fix
    assert artifact["dataQualityWarnings"] == ["feasibility_input.total_revenue 자료 보강 필요"]
    assert artifact["dataQualityMissingFields"] == ["feasibility_input.total_revenue"]
    assert artifact["engineReadiness"]["canRunCashFlow"] is False


def test_cost_schedule_rejects_empty_agent_result(monkeypatch):
    monkeypatch.setattr(cdo, "analyze_development_feasibility_use_case", lambda project_data, options: {})
    project_data = {}
    with pytest.raises(RuntimeError, match=r"DevelopmentFeasibilityAgent runtime did not complete successfully \(failed\)"):
        cdo._execute_pod_artifact("p1", "cost_schedule", project_data, {})
    assert project_data == {}


def test_cost_basis_keeps_effective_inputs_and_canonical_source_without_runtime_state():
    canonical = {"metrics": {"total_cost": 800}, "raw_import": {
        "canonicalStatus": "approved", "approval": {"approvedInputSnapshotId": "existing-snapshot"},
        "monthlyCostSource": {"sheetName": "Cost", "cell": "B10"},
    }}
    data = {
        "project": {"id": "cost-basis", "type": "apartment"},
        "land": {"area_sqm": 100}, "regulation": {"applied_far": 100},
        "feasibility_input": {"total_revenue": 1000},
        "cost_assumptions": {"construction_cost_per_sqm": 5},
        "schedule": {"construction_start": "M0", "completion": "M2"},
        "canonicalFeasibilityStudyV2": deepcopy(canonical),
        "_meta": {"parsing_source": {"archiveId": "existing-archive"}, "updated_at": "old"},
        "chief_orchestration_state": {"artifacts": {"old": {"outputHash": "old"}}},
        "costSchedule": {"costBreakdown": {"totalCost": 123}},
    }
    overrides = {"cost_assumptions": {"construction_cost_per_sqm": 6},
                 "ai_context": {"api_key": "test-secret-not-in-financial-input"}}
    original = deepcopy((data, overrides))
    basis = development_feasibility_input_basis(data, overrides)
    assert basis["cost_assumptions"] == {"construction_cost_per_sqm": 6}
    assert basis["canonicalFeasibilityStudyV2"] == canonical
    assert basis["_meta"] == {"parsing_source": {"archiveId": "existing-archive"}}
    assert "costSchedule" not in basis and "chief_orchestration_state" not in basis and "ai_context" not in basis
    assert pod_input_snapshot("cost_schedule", data, options=overrides)["developmentFeasibilityInput"] == basis
    result = analyze_development_feasibility_use_case(basis)
    assert result["costs"]["constructionCost"] == 600
    assert (data, overrides) == original
    data["canonicalFeasibilityStudyV2"]["raw_import"]["approval"]["approvedInputSnapshotId"] = "changed"
    assert basis["canonicalFeasibilityStudyV2"] == canonical


def test_cost_basis_does_not_drop_nonfinite_consumed_financial_input():
    basis = development_feasibility_input_basis({"feasibility_input": {"construction_cost": float("nan")}})
    with pytest.raises(FinancialInputError, match="must be finite"):
        analyze_development_feasibility_use_case(basis)


def test_cost_review_with_missing_total_cost_keeps_unknown_stress_profit():
    result = cdo._stressed_case_metrics(
        {"totalRevenue": 1000, "totalCost": None},
        revenue_factor=0.9, cost_factor=1.1, sale_rate_factor=0.85,
    )
    assert result["totalRevenue"] == 900
    assert result["totalCost"] is None
    assert result["developmentProfit"] is None and result["profitMargin"] is None
