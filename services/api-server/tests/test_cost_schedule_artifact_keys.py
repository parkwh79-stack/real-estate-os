import use_cases.chief_deal_orchestration as cdo


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


def test_cost_schedule_artifact_empty_when_agent_returns_nothing(monkeypatch):
    monkeypatch.setattr(cdo, "analyze_development_feasibility_use_case", lambda project_data, options: {})
    artifact = cdo._execute_pod_artifact("p1", "cost_schedule", {}, {})
    assert artifact["costBreakdown"] == {}
    assert artifact["dataQualityWarnings"] == []
