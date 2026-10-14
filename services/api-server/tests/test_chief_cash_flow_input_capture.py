from copy import deepcopy

import pytest

import use_cases.chief_deal_orchestration as chief
import agent_platform.domain_pod_runtime as pod_runtime
from agents.cash_flow_engine import run_cash_flow_engine
from agents.cash_flow_mapper import build_cash_flow_run_request_from_feasibility_study
from storage import list_calculation_run_records, upsert_calculation_run_record
from use_cases.approved_workbook_authority import GOVERNED_WORKBOOK_CORE_FIELDS
from use_cases.cash_flow_comparison import compare_cash_flow_runs, run_issues
from use_cases.cash_flow_dependencies import financial_input_hash
from use_cases.cash_flow import build_cash_flow_request_use_case
from use_cases.chief_pod_records import (
    build_pod_handoffs,
    calculation_records_for_pod,
    pod_input_snapshot,
    stable_digest,
)
from use_cases.development_feasibility import analyze_development_feasibility_use_case


def project(*, construction=400):
    return {
        "id": "chief-cf-input-capture",
        "project": {"name": "검토 입력 추적"},
        "feasibility_input": {
            "total_revenue": 1000, "total_cost": construction + 200,
            "construction_cost": construction, "land_cost": 100,
            "finance_cost": 50, "other_cost": 50,
        },
        "product_mix": [{"type": "apartment", "units": 10, "totalRevenue": 1000}],
        "schedule": {"construction_start": "2026-01-01", "completion": "2026-03-01"},
        "pf_structure": [{"tranche": "Senior", "priority": 1, "amount": 900}],
        "chief_orchestration_state": {"runId": "chief-parent-run"},
    }


def execute(monkeypatch, data):
    # Exercise the real deterministic child runtime and calculation; the other
    # workflow steps are outside this request-capture regression.
    feasibility = analyze_development_feasibility_use_case(data)
    monkeypatch.setattr(chief, "_run_domain_workflow", lambda *_: {
        "run_id": "feasibility-domain-run", "workflow": "feasibility",
        "status": "completed", "artifacts": {"developerFeasibility": feasibility},
    })
    inputs = []
    child_runtime = chief.run_domain_agent_callable_with_contract

    def traced_child(**kwargs):
        inputs.append(deepcopy(kwargs["input_snapshot"]))
        return child_runtime(**kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(chief, "run_domain_agent_callable_with_contract", traced_child)
        artifact = chief._execute_pod_artifact(data["id"], "feasibility_cashflow", data, {})
    return artifact, inputs


def records(data, artifact, *, artifact_key="chief-artifact"):
    return calculation_records_for_pod(
        project_id=data["id"], pod_key="feasibility_cashflow", artifact_key=artifact_key,
        artifact=artifact, input_snapshot=pod_input_snapshot("feasibility_cashflow", data),
        project_data=data, run_id=data["chief_orchestration_state"]["runId"],
    )


def approved_study():
    return {
        "project_name": "승인된 사업수지",
        "metrics": {"total_revenue": 2000, "total_cost": 800},
        "monthly_cash_flow": [{"month_index": 0, "period": "M0", "cash_out": {"other_cost": 800},
                               "source_locator": {"sheetName": "사업수지", "cell": "B10"}}],
        "schedule": [
            {"key": "construction_start", "label": "착공", "planned_date": "2026-01-01"},
            {"key": "completion", "label": "준공", "planned_date": "2026-03-01"},
        ],
        "input_references": {"approved_input_snapshot_id": "approved-snapshot-1", "approved_input_snapshot_hash": "a" * 64},
        "raw_import": {
            "canonicalStatus": "approved",
            "parserV2Bridge": {"status": "approved", "approvedInputSnapshotId": "approved-snapshot-1",
                              "approvedInputSnapshotHash": "a" * 64, "approvedFields": list(GOVERNED_WORKBOOK_CORE_FIELDS)},
        },
    }


def test_chief_stores_executed_request_hash_and_reopens_comparable_run(monkeypatch):
    data = project()
    artifact, child_inputs = execute(monkeypatch, data)
    request = deepcopy(artifact["cashFlowRunRequest"])
    assert request["requestType"] == "cash_flow_run_request_v1"
    assert "sourcesAndUses" in request and "productMix" in request
    assert "monthlyFinancing" not in request
    assert child_inputs == [request]
    replay = run_cash_flow_engine(request)
    assert replay["summary"] == artifact["cashFlowRunResult"]["summary"]
    assert replay["monthlyCashFlow"] == artifact["cashFlowRunResult"]["monthlyCashFlow"]

    rows = records(data, artifact)
    development, cash_flow = rows
    assert development["engineKey"] == "development_feasibility"
    assert "feasibility_input" in development["inputSnapshot"]
    assert cash_flow["engineKey"] == "cash_flow_engine"
    digest = financial_input_hash(request)
    assert cash_flow["inputSnapshot"] == {**request, "financialInputHash": digest}
    assert cash_flow["financialInputHash"] == digest
    assert cash_flow["sourceLocator"] == {
        "sourceType": "chief_pod_artifact", "artifactKey": "chief-artifact",
        "podKey": "feasibility_cashflow", "engineKey": "cash_flow_engine",
        "runId": "chief-parent-run",
    }
    assert run_issues(cash_flow) == []
    upsert_calculation_run_record(data["id"], cash_flow)

    changed = project(construction=500)
    second, _ = execute(monkeypatch, changed)
    other = records(changed, second, artifact_key="changed-artifact")[1]
    upsert_calculation_run_record(data["id"], other)
    persisted = {row["calculationRunId"]: row for row in list_calculation_run_records(data["id"])}
    comparison = compare_cash_flow_runs(persisted[cash_flow["calculationRunId"]], persisted[other["calculationRunId"]])
    assert comparison["status"] == "ready"
    assert comparison["baseline"]["financialInputHash"] == digest
    assert any(row["path"].startswith("sourcesAndUses.cost") for row in comparison["inputChanges"])
    assert comparison["businessInputsChanged"] is True
    assert next(row for row in comparison["metrics"] if row["key"] == "totalProjectCost")["delta"] == 100

    handoff = build_pod_handoffs(
        project_id=data["id"], pod_key="feasibility_cashflow", artifact_key="chief-artifact",
        downstream_pods=["pf_credit"], artifact=artifact,
        calculation_run_ids=[row["calculationRunId"] for row in rows],
        input_hash="pod-input-hash", output_hash=stable_digest(artifact), run_id="chief-parent-run",
    )[0]
    assert handoff["runId"] == cash_flow["sourceLocator"]["runId"]
    assert handoff["payload"]["sourceArtifactKey"] == cash_flow["artifactRef"]["artifactKey"]
    assert cash_flow["calculationRunId"] in handoff["payload"]["calculationRunIds"]

    data["feasibility_input"]["total_revenue"] = 9999
    artifact["cashFlowRunRequest"]["sourcesAndUses"]["revenue"]["totalSalesRevenue"] = 9999
    assert cash_flow["inputSnapshot"]["sourcesAndUses"]["revenue"]["totalSalesRevenue"] == 1000
    assert cash_flow["financialInputHash"] == digest


def test_chief_default_uses_approved_canonical_and_does_not_enable_monthly(monkeypatch):
    data = project()
    canonical = approved_study()
    data["canonicalFeasibilityStudyV2"] = deepcopy(canonical)
    expected = build_cash_flow_run_request_from_feasibility_study(canonical)
    artifact, child_inputs = execute(monkeypatch, data)
    assert artifact["cashFlowRunRequest"] == expected
    assert child_inputs == [expected]
    assert artifact["cashFlowRunResult"]["summary"]["totalRevenue"] == 2000
    assert artifact["cashFlowRunResult"]["summary"]["totalProjectCost"] == 800
    assert "monthlyFinancing" not in artifact["cashFlowRunResult"]
    assert data["canonicalFeasibilityStudyV2"] == canonical
    assert records(data, artifact)[1]["inputSnapshot"] == {
        **expected, "financialInputHash": financial_input_hash(expected),
    }


def test_record_builder_rejects_untraceable_cf_result_instead_of_guessing_input():
    data = project()
    with pytest.raises(ValueError, match="실행 입력"):
        records(data, {"cashFlowRunResult": {"status": "completed"}})


def test_cf_request_capture_does_not_change_other_pod_record_contract():
    data = project()
    snapshot = pod_input_snapshot("pf_credit", data)
    rows = calculation_records_for_pod(
        project_id=data["id"], pod_key="pf_credit", artifact_key="pf-artifact",
        artifact={"pfStructure": {"seniorLimit": 900}}, input_snapshot=snapshot, project_data=data,
    )
    assert len(rows) == 1 and rows[0]["engineKey"] == "pf_structure"
    assert rows[0]["inputSnapshot"] == snapshot
    assert "financialInputHash" not in rows[0]


def test_cf_contract_version_reexecutes_old_artifact_and_changed_financial_input(monkeypatch):
    data = project()
    snapshot = pod_input_snapshot("feasibility_cashflow", data)
    request = build_cash_flow_request_use_case(data)
    legacy_artifact = {"artifactType": "feasibility_cashflow_pod_result",
                       "cashFlowRunResult": run_cash_flow_engine(request)}
    manifest = pod_runtime._manifest
    with monkeypatch.context() as scoped:
        scoped.setattr(pod_runtime, "_manifest", lambda key: manifest(key).model_copy(update={"version": "1.1.0"}))
        legacy, old_runtime = pod_runtime.run_domain_pod_with_contract(
            project_id=data["id"], pod_key="feasibility_cashflow", input_snapshot=snapshot,
            project_data=data, options={}, executor=lambda: deepcopy(legacy_artifact),
        )
    assert old_runtime["status"] == "completed" and "cashFlowRunRequest" not in legacy
    executions = []

    def actual_executor():
        executions.append(True)
        actual_request = build_cash_flow_request_use_case(data)
        return {"artifactType": "feasibility_cashflow_pod_result",
                "cashFlowRunRequest": deepcopy(actual_request),
                "cashFlowRunResult": run_cash_flow_engine(actual_request)}

    current, current_runtime = pod_runtime.run_domain_pod_with_contract(
        project_id=data["id"], pod_key="feasibility_cashflow", input_snapshot=snapshot,
        project_data=data, options={}, executor=actual_executor,
    )
    assert current_runtime["status"] == "completed" and current_runtime["replayed"] is False
    assert current_runtime["agentRunId"] != old_runtime["agentRunId"]
    assert current["cashFlowRunRequest"] == request and executions == [True]
    _, exact_replay = pod_runtime.run_domain_pod_with_contract(
        project_id=data["id"], pod_key="feasibility_cashflow", input_snapshot=snapshot,
        project_data=data, options={}, executor=actual_executor,
    )
    assert exact_replay["replayed"] is True and executions == [True]

    data["canonicalFeasibilityStudyV2"] = approved_study()
    changed_snapshot = pod_input_snapshot("feasibility_cashflow", data)
    assert changed_snapshot != snapshot
    changed, changed_runtime = pod_runtime.run_domain_pod_with_contract(
        project_id=data["id"], pod_key="feasibility_cashflow", input_snapshot=changed_snapshot,
        project_data=data, options={}, executor=actual_executor,
    )
    assert changed_runtime["replayed"] is False and executions == [True, True]
    assert changed["cashFlowRunResult"]["summary"]["totalRevenue"] == 2000
    assert current["cashFlowRunResult"]["summary"]["totalRevenue"] == 1000
    assert manifest("feasibility_cashflow").version == "1.3.0"
    assert manifest("pf_credit").version == "1.1.0"


@pytest.mark.parametrize("field", [
    "cost_assumptions", "sales_assumptions", "cashflow_input", "sales_schedule", "salesCollectionSchedule",
    "architectural_plan", "land", "regulation", "logistics",
])
def test_cf_pod_hash_covers_existing_calculation_dependencies(field):
    data = project()
    before = pod_input_snapshot("feasibility_cashflow", data)
    data[field] = {"changed": True}
    after = pod_input_snapshot("feasibility_cashflow", data)
    assert stable_digest(before) != stable_digest(after)


def test_cf_pod_binds_consumed_parsing_source_without_other_project_metadata():
    data = project()
    data["_meta"] = {"parsing_source": "first-workbook", "updated_at": "before", "api_key": "test-secret-excluded"}
    first = pod_input_snapshot("feasibility_cashflow", data)
    assert analyze_development_feasibility_use_case(data)["dataQuality"]["source"] == "first-workbook"
    assert first["parsingSource"] == "first-workbook" and "_meta" not in first
    data["_meta"]["updated_at"] = "after"
    assert pod_input_snapshot("feasibility_cashflow", data) == first
    data["_meta"]["parsing_source"] = "second-workbook"
    second = pod_input_snapshot("feasibility_cashflow", data)
    assert analyze_development_feasibility_use_case(data)["dataQuality"]["source"] == "second-workbook"
    assert stable_digest(first) != stable_digest(second)
