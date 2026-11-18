from copy import deepcopy

import pytest

from agents.scenario_engine_agent import ScenarioEngineAgent
from agent_platform import domain_pod_runtime as pod_runtime
from schemas.scenario import ScenarioSetRequest
from storage import list_calculation_run_records, upsert_calculation_run_record
from use_cases.chief_deal_orchestration import _build_scenario_request, _execute_pod_artifact
from use_cases.chief_pod_records import calculation_records_for_pod, pod_input_snapshot, stable_digest
from use_cases.calculation_run_capture import scenario_engine_run_payloads
from use_cases.pf_structure import build_pf_structure_input, execute_pf_structure_input


def project():
    return {
        "project": {"id": "pf-input-capture", "name": "합성 PF 입력 검증"},
        "feasibility_input": {"total_revenue": 1000, "total_cost": 800, "finance_cost": 30},
        "pf_structure": [{"tranche": "Senior", "amount": 600, "priority": 1, "interestRate": .05, "feeRate": .01}],
        "product_mix": [{"type": "Apartment", "units": 10, "totalRevenue": 1000}],
        "stressTest": {"scenarioCount": 99, "worstCaseKey": "old-unrelated-result"},
    }


def records(data, artifact):
    return calculation_records_for_pod(project_id="pf-input-capture", pod_key="pf_credit", artifact_key="pf-artifact",
        artifact=artifact, input_snapshot=pod_input_snapshot("pf_credit", data), project_data=data,
        run_id="chief-pf-run", agent_run_id="pf-runtime-run")


def test_pf_pod_saves_actual_requests_replays_scenarios_and_excludes_stale_stress():
    data = project()
    options = {"finance_cost": 0, "net_operating_income": 80, "debt_service": 40,
               "scenarioDrivers": {"saleRates": [.5, 1], "discountRates": [0, .2]}}
    artifact = _execute_pod_artifact("pf-input-capture", "pf_credit", data, options)
    rows = records(data, artifact)
    assert {row["engineKey"] for row in rows} == {"pf_structure", "waterfall", "unsold_ltv", "tranche_recovery"}
    assert artifact["stressExecutionStatus"] == "not_executed_in_this_pod"
    for row in rows:
        assert row["sourceLocator"]["runId"] == "chief-pf-run"
        assert row["sourceLocator"]["agentRunId"] == "pf-runtime-run"
        assert row["inputDigest"]["capturePolicy"] == "effective_request_v1"
        if row["engineKey"] == "pf_structure":
            assert row["inputSnapshot"]["finance_cost"] == 0
            assert row["resultSnapshot"]["dscr"] == 2
            assert execute_pf_structure_input(row["inputSnapshot"])["metrics"] == row["resultSnapshot"]["metrics"]
        else:
            assert row["inputSnapshot"]["scenarioDrivers"]["discountRates"] == [0, .2]
            assert row["inputSnapshot"]["waterfallRule"]["includeFees"] is True
            replay = ScenarioEngineAgent().run_scenario_set(ScenarioSetRequest(**row["inputSnapshot"])).model_dump()
            assert scenario_engine_run_payloads(replay, engines=[row["engineKey"]])[0]["result"] == row["resultSnapshot"]
        upsert_calculation_run_record("pf-input-capture", row)
    before = deepcopy(rows)
    data["feasibility_input"]["total_revenue"] = 5000
    artifact["pfStructureInput"]["total_revenue"] = 9999
    artifact["pfStructure"]["ltv"] = 99
    assert rows == before
    persisted = {r["calculationRunId"]: r for r in list_calculation_run_records("pf-input-capture")}
    for row in before:
        assert persisted[row["calculationRunId"]]["inputSnapshot"] == row["inputSnapshot"]
        assert persisted[row["calculationRunId"]]["resultSnapshot"] == row["resultSnapshot"]


@pytest.mark.parametrize("field,value", [("feasibility_input", {"total_revenue": 900}),
    ("product_mix", [{"type": "Apartment", "units": 3, "totalRevenue": 300}]),
    ("credit_enhancements", [{"type": "responsible_completion", "present": True}])])
def test_pf_pod_cache_tracks_consumed_project_changes(field, value):
    data = project()
    before = pod_input_snapshot("pf_credit", data)
    data[field] = value
    assert stable_digest(pod_input_snapshot("pf_credit", data)) != stable_digest(before)


def test_pf_pod_cache_and_scenario_request_track_changed_driver_options():
    data = project()
    first = {"scenarioDrivers": {"saleRates": [.5], "discountRates": [0]}}
    second = {"scenarioDrivers": {"saleRates": [.8], "discountRates": [.1]}}
    assert stable_digest(pod_input_snapshot("pf_credit", data, options=first)) != stable_digest(pod_input_snapshot("pf_credit", data, options=second))
    a = _execute_pod_artifact("pf-input-capture", "pf_credit", deepcopy(data), first)
    b = _execute_pod_artifact("pf-input-capture", "pf_credit", deepcopy(data), second)
    assert a["scenariosEvaluation"]["waterfallScenarios"] != b["scenariosEvaluation"]["waterfallScenarios"]
    assert b["scenarioRunRequest"]["scenarioDrivers"]["saleRates"] == [.8]


@pytest.mark.parametrize("artifact", [{"pfStructure": {"ltv": .5}}, {"scenariosEvaluation": {"scenarioSetId": "old"}}, {"stressTest": {"scenarioCount": 1}}])
def test_unbound_legacy_artifact_cannot_create_new_calculation_records(artifact):
    with pytest.raises(ValueError, match="실행 입력"):
        records(project(), artifact)


def test_explicit_zero_rates_and_removed_lists_are_not_restored_from_project():
    data = project()
    for overrides in ({"tranches": []}, {"productMix": []}):
        assert _build_scenario_request("p", data, overrides) is None
    request = _build_scenario_request("p", data, {"tranches": [{"tranche": "A", "amount": 100,
        "interestRate": 0, "interest_rate": .08, "feeRate": 0, "fee_rate": .01}]})
    assert request.pfStructure[0].interestRate == request.pfStructure[0].feeRate == 0


@pytest.mark.parametrize("overrides", [
    {"productMix": [{"units": 0, "unitCount": 10, "totalRevenue": 1000}]},
    {"productMix": [{"units": 10, "totalRevenue": 0, "revenue": 1000}]},
    {"tranches": [{"amount": 0, "loanAmount": 600}]},
])
def test_zero_scenario_inputs_do_not_revive_stale_aliases_or_project_totals(overrides):
    data = project()
    data["architectural_plan"] = {"unit_count": 10}
    assert _build_scenario_request("p", data, overrides) is None


def test_legacy_pf_structure_alias_and_explicit_empty_canonical_list():
    data = project()
    data["pfStructure"] = data.pop("pf_structure")
    assert build_pf_structure_input(data)["tranches"] == data["pfStructure"]
    data["pf_structure"] = []
    assert build_pf_structure_input(data)["tranches"] == []
    assert _build_scenario_request("p", data, {}) is None
    data.pop("pf_structure")
    data["pfStructure"] = {"ltv": .6, "metrics": {}}
    assert build_pf_structure_input(data)["tranches"] == []


def test_insufficient_current_scenario_does_not_feed_old_scenario_to_credit_review():
    data = project()
    previous = {"scenarioSetId": "historical", "riskFlags": [{"code": "OLD_RESULT", "severity": "critical"}]}
    data["scenariosEvaluation"] = deepcopy(previous)
    artifact = _execute_pod_artifact("no-current-scenario", "pf_credit", data, {"productMix": []})
    assert artifact["scenarioExecutionStatus"] == "insufficient_input"
    assert artifact["executionStatus"] == "completed_with_review"
    assert artifact["dataLimitations"]
    assert artifact["scenariosEvaluation"] is None
    assert "scenariosEvaluation" not in artifact["standardArtifacts"]
    assert "OLD_RESULT" not in str(artifact["riskRegister"])
    assert data["scenariosEvaluation"] == previous
    assert [row["engineKey"] for row in records(data, artifact)] == ["pf_structure"]


def test_pf_runtime_invalidates_old_artifacts_and_replays_only_unchanged_inputs(monkeypatch):
    data = project()
    snapshot = pod_input_snapshot("pf_credit", data)
    manifest = pod_runtime._manifest

    def execute(payload, executor):
        return pod_runtime.run_domain_pod_with_contract(project_id="pf-cache-capture", pod_key="pf_credit",
            input_snapshot=payload, project_data=data, options={}, executor=executor)

    with monkeypatch.context() as scoped:
        scoped.setattr(pod_runtime, "_manifest", lambda key: manifest(key).model_copy(update={"version": "1.1.0"}))
        _, legacy = execute(snapshot, lambda: {"pfStructure": {"ltv": .6}})
    calls = []

    def actual():
        calls.append(True)
        return _execute_pod_artifact("pf-cache-capture", "pf_credit", data, {})

    captured, current = execute(snapshot, actual)
    assert current["status"] == "completed" and current["replayed"] is False
    assert current["agentRunId"] != legacy["agentRunId"]
    assert captured["pfStructureInput"]["total_revenue"] == 1000
    _, repeated = execute(snapshot, actual)
    assert repeated["replayed"] is True and calls == [True]
    data["feasibility_input"]["total_revenue"] = 1200
    changed, rerun = execute(pod_input_snapshot("pf_credit", data), actual)
    assert rerun["replayed"] is False and calls == [True, True]
    assert changed["pfStructureInput"]["total_revenue"] == 1200
    assert changed["pfStructure"]["ltv"] != captured["pfStructure"]["ltv"]
