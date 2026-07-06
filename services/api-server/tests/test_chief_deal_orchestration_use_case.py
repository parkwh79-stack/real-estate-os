from use_cases.chief_deal_orchestration import plan_chief_deal_orchestration_use_case


def test_chief_plan_sparse_project_builds_missing_input_register():
    plan = plan_chief_deal_orchestration_use_case("p1", {"project": {"id": "p1"}})
    state = plan["sharedDealStateGraph"]
    missing = state["missingInputRegister"]

    assert plan["schemaVersion"] == "realytics-chief-deal-orchestration-v1"
    assert len(plan["agentPods"]) == 6
    assert {pod["podKey"] for pod in plan["agentPods"]} == {
        "land_legal",
        "market_product",
        "cost_schedule",
        "feasibility_cashflow",
        "pf_credit",
        "public_policy",
    }
    assert any(item["itemId"] == "pnu_or_land_ledger_missing" for item in missing)
    assert any(item["severity"] == "blocking" for item in missing)
    assert plan["decisionGate"]["decision"] == "HOLD"


def test_chief_plan_tags_source_and_calculated_values():
    plan = plan_chief_deal_orchestration_use_case(
        "p2",
        {
            "project": {"id": "p2", "name": "테스트 PF"},
            "source_documents": [{"documentType": "business_plan", "originalFileName": "business.pdf", "archiveId": "a1"}],
            "land": {"pnu": "1111010100100010000", "officialLandPricePerSqm": 1000000},
            "landEvidence": [{"evidenceType": "official_land_price", "pnu": "1111010100100010000"}],
            "regulation": {"far": 300},
            "legal": {"permitStatus": "pending"},
            "market": {"manualComparables": [{"name": "A"}]},
            "product_mix": [{"type": "apartment", "units": 100}],
            "feasibility_input": {"total_revenue": 1000, "total_cost": 800, "construction_cost": 300},
            "schedule": {"completionMonth": 36},
            "pf_structure": [{"tranche": "Senior", "amount": 500}],
        },
    )
    metrics = {row["key"]: row for row in plan["sharedDealStateGraph"]["metrics"]}

    assert metrics["feasibility.totalRevenue"]["valueType"] == "source"
    assert metrics["pf.totalLoanAmount"]["valueType"] == "calculated"
    assert plan["decisionGate"]["decision"] == "HOLD"
    conflicts = {row["code"]: row for row in plan["sharedDealStateGraph"]["conflicts"]}
    assert "MARKET_PRICE_BASIS_MISSING" in conflicts
    assert len(plan["riskCommittee"]) >= 7
    assert {case["scenario"] for case in plan["scenarioConclusions"]} == {
        "base_case",
        "downside_case",
        "upside_case",
        "kill_case",
    }
    base_case = next(case for case in plan["scenarioConclusions"] if case["scenario"] == "base_case")
    assert base_case["keyMetrics"]["exitSaleRate"] == 0.5
    assert plan["taskGraph"]["workflowTrack"] == "development"
    assert plan["taskGraph"]["recalculationRules"]


def test_chief_task_graph_changes_by_workflow_track():
    project_data = {
        "project": {"id": "p_track", "name": "Track test"},
        "source_documents": [{"documentType": "business_plan", "originalFileName": "business.pdf", "archiveId": "a1"}],
        "land": {"pnu": "1111010100100010000", "officialLandPricePerSqm": 1000000},
        "landEvidence": [{"evidenceType": "official_land_price", "pnu": "1111010100100010000"}],
        "regulation": {"far": 300},
        "legal": {"permitStatus": "pending"},
        "market": {"manualComparables": [{"name": "A"}]},
        "product_mix": [{"type": "apartment", "units": 100}],
        "feasibility_input": {"total_revenue": 1000, "total_cost": 800, "construction_cost": 300},
        "schedule": {"completionMonth": 36},
        "pf_structure": [{"tranche": "Senior", "amount": 500}],
    }

    development = plan_chief_deal_orchestration_use_case(
        "p_track_dev",
        project_data,
        {"workflowTrack": "development"},
    )
    pf = plan_chief_deal_orchestration_use_case(
        "p_track_pf",
        project_data,
        {"workflowTrack": "pf"},
    )
    finance = plan_chief_deal_orchestration_use_case(
        "p_track_finance",
        project_data,
        {"workflowTrack": "finance"},
    )

    assert development["taskGraph"]["workflowTrack"] == "development"
    assert pf["taskGraph"]["workflowTrack"] == "pf"
    assert finance["taskGraph"]["workflowTrack"] == "finance"

    assert development["taskGraph"]["executionStages"] != pf["taskGraph"]["executionStages"]
    assert finance["taskGraph"]["recommendedRunOrder"][:3] == [
        "land_legal",
        "feasibility_cashflow",
        "pf_credit",
    ]

    pf_edges = {(edge["from"], edge["to"]) for edge in pf["taskGraph"]["edges"]}
    assert ("market_product", "pf_credit") in pf_edges

    pf_triggers = {rule["trigger"] for rule in pf["taskGraph"]["recalculationRules"]}
    finance_triggers = {rule["trigger"] for rule in finance["taskGraph"]["recalculationRules"]}
    assert "senior_shortfall_detected" in pf_triggers
    assert "collateral_value_changed" in finance_triggers


def test_run_chief_deal_pod_recalculation_triggers():
    from use_cases.chief_deal_orchestration import run_chief_deal_pod_use_case
    
    project_data = {
        "project": {"id": "p_run", "name": "테스트 PF Run"},
        "source_documents": [{"documentType": "business_plan", "originalFileName": "business.pdf", "archiveId": "a1"}],
        "land": {"pnu": "1111010100100010000", "officialLandPricePerSqm": 1000000},
        "landEvidence": [{"evidenceType": "official_land_price", "pnu": "1111010100100010000"}],
        "regulation": {"far": 300},
        "legal": {"permitStatus": "pending"},
        "market": {"manualComparables": [{"name": "A"}]},
        "product_mix": [{"type": "apartment", "units": 100}],
        "feasibility_input": {"total_revenue": 1000, "total_cost": 800, "construction_cost": 300},
        "schedule": {"completionMonth": 36},
        "pf_structure": [{"tranche": "Senior", "amount": 500}],
    }
    
    persisted_artifacts = {}

    def artifact_sink(record):
        persisted_artifacts[record["artifactKey"]] = record
        return record

    # Run land_legal pod
    plan = run_chief_deal_pod_use_case(
        "p_run",
        "land_legal",
        project_data,
        {"forceTrigger": "legal_product_infeasible"},
        artifact_sink=artifact_sink,
    )
    
    runs = {run["podKey"]: run for run in plan["podRuns"]}
    assert runs["land_legal"]["status"] == "completed"
    assert runs["land_legal"]["artifactKeys"]
    assert runs["market_product"]["status"] == "needs_recalculation"
    assert runs["feasibility_cashflow"]["status"] == "needs_recalculation"
    assert runs["pf_credit"]["status"] == "needs_recalculation"
    
    assert len(runs["market_product"]["recalculationEvents"]) == 1
    assert runs["market_product"]["recalculationEvents"][0]["trigger"] == "legal_product_infeasible"
    assert runs["market_product"]["recalculationEvents"][0]["sourcePod"] == "land_legal"
    
    assert any(event.get("trigger") == "legal_product_infeasible" for event in plan["learningLoopEvents"])
    assert any(event.get("eventType") == "assumption_conflict_review_required" for event in plan["learningLoopEvents"])
    assert any(event.get("eventType") == "risk_committee_human_review_required" for event in plan["learningLoopEvents"])

    state = project_data["chief_orchestration_state"]
    artifact_key = runs["land_legal"]["artifactKeys"][0]
    assert artifact_key in state["artifacts"]
    assert state["artifacts"][artifact_key]["artifactType"] == "land_legal_pod_result"
    assert state["artifacts"][artifact_key]["termSurfaceCount"] >= 1
    assert artifact_key in persisted_artifacts
    assert persisted_artifacts[artifact_key]["result"]["artifactType"] == "land_legal_pod_result"
    assert persisted_artifacts[artifact_key]["termSurfaceMetadata"]
    pnu_surface = next(item for item in persisted_artifacts[artifact_key]["termSurfaceMetadata"] if item["canonicalField"] == "land.pnu")
    assert pnu_surface["rawText"] == "PNU"
    assert pnu_surface["normalizedSurface"] == "pnu"
    assert "textQualityFlags" in pnu_surface


def test_land_pod_runs_with_land_ledger_even_when_land_evidence_is_missing():
    from use_cases.chief_deal_orchestration import (
        plan_chief_deal_orchestration_use_case,
        run_chief_deal_pod_use_case,
    )

    project_data = {
        "project": {"id": "p_land_gap", "name": "토지 증빙 보강 테스트"},
        "source_documents": [{"documentType": "land_ledger", "originalFileName": "land-ledger.xlsx", "archiveId": "l1"}],
        "landLedger": {
            "analysisType": "land_ledger_analysis_v1",
            "summary": {"parcelCount": 2, "totalAreaSqm": 1000},
            "parcels": [
                {"rawLotNumber": "1", "normalizedLotNumber": "1"},
                {"rawLotNumber": "2", "normalizedLotNumber": "2"},
            ],
        },
        "regulation": {"zoning": "제2종일반주거지역"},
    }

    plan = plan_chief_deal_orchestration_use_case("p_land_gap", project_data, {"workflowTrack": "pf"})
    runs = {run["podKey"]: run for run in plan["podRuns"]}

    assert runs["land_legal"]["status"] == "planned"
    assert runs["pf_credit"]["status"] == "blocked"
    assert any(item["itemId"] == "land_evidence_missing" for item in plan["sharedDealStateGraph"]["missingInputRegister"])

    persisted_artifacts = {}
    updated = run_chief_deal_pod_use_case(
        "p_land_gap",
        "land_legal",
        project_data,
        {"workflowTrack": "pf"},
        artifact_sink=lambda record: persisted_artifacts.setdefault(record["artifactKey"], record),
    )
    updated_runs = {run["podKey"]: run for run in updated["podRuns"]}
    artifact_key = updated_runs["land_legal"]["artifactKeys"][0]

    assert updated_runs["land_legal"]["status"] == "completed"
    assert persisted_artifacts[artifact_key]["result"]["artifactType"] == "land_legal_pod_result"
    assert "landAgentReview" in persisted_artifacts[artifact_key]["result"]


def test_run_all_pods_executes_track_order_and_reports_summary():
    from use_cases.chief_deal_orchestration import run_chief_deal_all_pods_use_case

    project_data = {
        "project": {"id": "p_run_all", "name": "Run all PF"},
        "source_documents": [{"documentType": "business_plan", "originalFileName": "business.pdf", "archiveId": "a1"}],
        "land": {"pnu": "1111010100100010000", "officialLandPricePerSqm": 1000000},
        "landEvidence": [{"evidenceType": "official_land_price", "pnu": "1111010100100010000"}],
        "regulation": {"far": 300},
        "legal": {"permitStatus": "pending"},
        "market": {"manualComparables": [{"name": "A", "pricePerPy": 30_000_000}]},
        "product_mix": [{"type": "apartment", "units": 100, "pricePerPy": 31_000_000}],
        "feasibility_input": {"total_revenue": 1000, "total_cost": 800, "construction_cost": 300},
        "schedule": {"completionMonth": 36},
        "pf_structure": [{"tranche": "Senior", "amount": 500}],
    }
    artifacts = {}

    plan = run_chief_deal_all_pods_use_case(
        "p_run_all",
        project_data,
        {"workflowTrack": "pf"},
        artifact_sink=lambda record: artifacts.setdefault(record["artifactKey"], record),
    )

    summary = plan["runAllSummary"]
    assert summary["requestedPodCount"] == 6
    assert summary["completedCount"] >= 5
    assert summary["workflowTrack"] == "pf"
    assert artifacts
    runs = {run["podKey"]: run for run in plan["podRuns"]}
    assert runs["land_legal"]["status"] == "completed"
    assert runs["pf_credit"]["artifactKeys"]
