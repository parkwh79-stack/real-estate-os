from __future__ import annotations

from copy import deepcopy

import pytest

from agents.ai_connection import AIExtractionResult
from agents.consensus_engine import (
    ConsensusEngine,
    DeterministicCoordinator,
    LLMCoordinator,
    build_consensus_coordinator,
)


def _make_reconcile_fn(artifacts, *, include_readiness=True):
    """Recompute conflicts from the (possibly patched) working project_data."""
    market_rev = artifacts["marketabilityAnalysis"]["projectSummary"]["totalSalesRevenue"]
    land_area = artifacts["landAgentReview"]["summary"]["totalAreaSqm"]

    def reconcile(pd, _art):
        checks = []

        def numeric(key, label, left, right):
            if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
                status = "warning"
                ratio = None
            else:
                denom = max(abs(left), abs(right), 1.0)
                ratio = abs(right - left) / denom
                status = "blocking" if ratio >= 0.15 else "warning" if ratio >= 0.05 else "clear"
            checks.append({"key": key, "label": label, "status": status, "severity": status,
                           "leftValue": left, "rightValue": right, "diffRatio": ratio})

        numeric("revenue_feasibility_vs_marketability", "매출 정합",
                (pd.get("feasibility_input") or {}).get("total_revenue"), market_rev)
        numeric("land_area_project_vs_land_agent", "면적 정합",
                (pd.get("land") or {}).get("area_sqm"), land_area)
        if include_readiness:
            checks.append({"key": "official_price_evidence_ready", "label": "공시지가 증빙",
                           "status": "warning", "severity": "warning", "ready": False})
        return {"checks": checks, "status": "needs_review"}

    return reconcile


def _artifacts(market_complete=True):
    return {
        "marketabilityAnalysis": {
            "dataStatus": "complete" if market_complete else "insufficient",
            "score": 78 if market_complete else None,
            "projectSummary": {"totalSalesRevenue": 100_000_000_000},
        },
        "landAgentReview": {
            "summary": {"totalAreaSqm": 9688.0},
            "evidenceReadiness": {"pnuReady": True, "officialPriceReady": True},
        },
    }


def test_consensus_converges_numeric_conflicts_and_keeps_evidence_gaps():
    artifacts = _artifacts(market_complete=True)
    project_data = {"feasibility_input": {"total_revenue": 130_000_000_000}, "land": {"area_sqm": 8000.0}}
    result = ConsensusEngine().reach_consensus(project_data, artifacts, _make_reconcile_fn(artifacts))

    assert result["status"] == "partial"  # readiness gap remains
    assert result["coordinator"] == "deterministic"
    # marketability evidence wins -> feasibility revenue patched to market revenue
    assert result["agreedProjectData"]["feasibility_input"]["total_revenue"] == 100_000_000_000
    # land evidence wins -> project area patched to land-agent area
    assert result["agreedProjectData"]["land"]["area_sqm"] == 9688.0
    resolved_keys = {r["key"] for r in result["resolutions"]}
    assert {"revenue_feasibility_vs_marketability", "land_area_project_vs_land_agent"} <= resolved_keys
    # the evidence-required conflict is surfaced, never silently cleared
    assert any(u["key"] == "official_price_evidence_ready" for u in result["unresolved"])


def test_consensus_fully_converges_without_evidence_gaps():
    artifacts = _artifacts(market_complete=True)
    project_data = {"feasibility_input": {"total_revenue": 130_000_000_000}, "land": {"area_sqm": 8000.0}}
    result = ConsensusEngine().reach_consensus(
        project_data, artifacts, _make_reconcile_fn(artifacts, include_readiness=False)
    )
    assert result["status"] == "converged"
    assert result["summary"]["unresolvedCount"] == 0


def test_no_market_evidence_adopts_conservative_revenue():
    artifacts = _artifacts(market_complete=False)
    project_data = {"feasibility_input": {"total_revenue": 130_000_000_000}, "land": {"area_sqm": 9688.0}}
    result = ConsensusEngine().reach_consensus(
        project_data, artifacts, _make_reconcile_fn(artifacts, include_readiness=False)
    )
    revenue_res = next(r for r in result["resolutions"] if r["key"] == "revenue_feasibility_vs_marketability")
    assert revenue_res["adoptedSource"] == "conservative"
    # conservative = lower of feasibility (130bn) and market (100bn)
    assert result["agreedProjectData"]["feasibility_input"]["total_revenue"] == 100_000_000_000


def test_land_area_not_overridden_without_land_evidence():
    artifacts = _artifacts(market_complete=True)
    artifacts["landAgentReview"]["evidenceReadiness"] = {"pnuReady": False, "officialPriceReady": False}
    project_data = {"feasibility_input": {"total_revenue": 100_000_000_000}, "land": {"area_sqm": 8000.0}}
    result = ConsensusEngine().reach_consensus(
        project_data, artifacts, _make_reconcile_fn(artifacts, include_readiness=False)
    )
    # land area stays (no evidence to override) and remains an unresolved conflict
    assert result["agreedProjectData"]["land"]["area_sqm"] == 8000.0
    assert any(u["key"] == "land_area_project_vs_land_agent" for u in result["unresolved"])
    assert result["status"] == "partial"


class _FakeConn:
    def __init__(self, data=None, error=None):
        self._data = data
        self._error = error
        self.calls = 0

    def extract_json(self, prompt, *, system, ai_context):
        self.calls += 1
        return AIExtractionResult(data=self._data, meta={}, error=self._error)


def _conflicts():
    return [
        {"key": "land_area_project_vs_land_agent", "label": "면적", "status": "warning"},
        {"key": "revenue_feasibility_vs_marketability", "label": "매출", "status": "blocking"},
    ]


def test_build_coordinator_falls_back_to_deterministic_without_ai():
    assert isinstance(build_consensus_coordinator(None), DeterministicCoordinator)
    assert build_consensus_coordinator({"provider": "openai"}).name == "llm"


def test_llm_coordinator_orders_and_explains():
    conn = _FakeConn(data={
        "order": ["revenue_feasibility_vs_marketability", "land_area_project_vs_land_agent"],
        "rationale": {"revenue_feasibility_vs_marketability": "매출 충돌이 여신에 가장 큰 영향"},
    })
    coord = LLMCoordinator({"provider": "openai", "model": "gpt-4o"}, connection=conn)
    ordered = coord.order_conflicts(_conflicts())
    assert [c["key"] for c in ordered][0] == "revenue_feasibility_vs_marketability"
    assert conn.calls == 1
    assert coord.rationale({"key": "revenue_feasibility_vs_marketability", "rule": "r"}) == "매출 충돌이 여신에 가장 큰 영향"
    # unseen key falls back to its rule text
    assert coord.rationale({"key": "land_area_project_vs_land_agent", "rule": "rule-text"}) == "rule-text"


def test_llm_coordinator_falls_back_on_error():
    coord = LLMCoordinator({"provider": "openai"}, connection=_FakeConn(error="no key"))
    ordered = coord.order_conflicts(_conflicts())
    # deterministic fallback: blocking before warning
    assert [c["key"] for c in ordered][0] == "revenue_feasibility_vs_marketability"


def test_llm_coordinator_omitted_keys_are_appended():
    conn = _FakeConn(data={"order": ["revenue_feasibility_vs_marketability"]})  # omits land
    coord = LLMCoordinator({"provider": "openai"}, connection=conn)
    ordered = coord.order_conflicts(_conflicts())
    assert {c["key"] for c in ordered} == {
        "revenue_feasibility_vs_marketability", "land_area_project_vs_land_agent"
    }


def test_llm_coordinator_order_is_a_permutation_and_failed_round_clears_rationale():
    key = "revenue_feasibility_vs_marketability"
    conn = _FakeConn(data={"order": [key, key], "rationale": {key: "old rationale"}})
    coord = LLMCoordinator({"provider": "test"}, connection=conn)
    ordered = coord.order_conflicts(_conflicts())
    assert len(ordered) == len(_conflicts())
    assert len({item["key"] for item in ordered}) == len(ordered)
    conn._error = "offline failure"
    coord.order_conflicts(_conflicts())
    assert coord.rationale({"key": key, "rule": "current rule"}) == "current rule"


def test_consensus_runs_with_llm_coordinator():
    artifacts = _artifacts(market_complete=True)
    project_data = {"feasibility_input": {"total_revenue": 130_000_000_000}, "land": {"area_sqm": 9688.0}}
    coord = LLMCoordinator({"provider": "openai"}, connection=_FakeConn(data={"order": [], "rationale": {}}))
    result = ConsensusEngine().reach_consensus(
        project_data, artifacts, _make_reconcile_fn(artifacts, include_readiness=False), coordinator=coord
    )
    assert result["coordinator"] == "llm"
    assert result["status"] == "converged"


def test_fresh_domain_cycle_updates_artifacts_after_each_accepted_patch_without_mutating_callers():
    project = {"land": {"area_sqm": 80}, "feasibility_input": {"total_revenue": 1300}}
    artifacts = _artifacts()
    original_project, original_artifacts = deepcopy(project), deepcopy(artifacts)
    executions = []

    def recalculate(working, current):
        area, revenue = working["land"]["area_sqm"], working["feasibility_input"]["total_revenue"]
        # Each deterministic test domain consumes this same current input. A
        # changed land area causes a second market/revenue reconciliation.
        for agent in ("land", "market", "developer", "cashflow", "pf"):
            executions.append((agent, area, revenue))
            current[agent] = {"inputArea": area, "inputRevenue": revenue}
        current["landAgentReview"] = {
            "summary": {"totalAreaSqm": 100}, "evidenceReadiness": {"pnuReady": True},
        }
        current["marketabilityAnalysis"] = {
            "dataStatus": "complete", "projectSummary": {"totalSalesRevenue": area * 10},
        }
        return _make_reconcile_fn(current, include_readiness=False)(working, current)

    result = ConsensusEngine().reach_consensus(project, artifacts, recalculate)

    assert result["status"] == "converged"
    assert result["stopReason"] == "converged"
    assert result["agreedProjectData"]["feasibility_input"]["total_revenue"] == 1000
    assert result["agreedProjectData"]["land"]["area_sqm"] == 100
    assert len(executions) == 15
    assert {row[0] for row in executions} == {"land", "market", "developer", "cashflow", "pf"}
    assert [row for row in executions if row[0] == "pf"] == [
        ("pf", 80, 1300), ("pf", 100.0, 800.0), ("pf", 100.0, 1000.0),
    ]
    assert result["finalArtifacts"]["developer"] == {"inputArea": 100.0, "inputRevenue": 1000.0}
    assert result["finalReconciliation"]["checks"][0]["status"] == "clear"
    assert result["summary"]["evaluationCount"] == 3
    assert [row["round"] for row in result["evaluationLog"]] == [0, 1, 2]
    assert all(row["recalculationPerformed"] for row in result["roundLog"])
    assert result["roundLog"][0]["inputHashAfter"] == result["roundLog"][1]["inputHashBefore"]
    assert len({row["outputHash"] for row in result["evaluationLog"]}) == 3
    assert project == original_project
    assert artifacts == original_artifacts


def test_oscillating_fresh_agent_outputs_stop_without_claiming_consensus():
    project = {"feasibility_input": {"total_revenue": 200}}
    calls = []

    def recalculate(working, artifacts):
        revenue = working["feasibility_input"]["total_revenue"]
        next_revenue = 100 if revenue == 200 else 200
        calls.append(revenue)
        artifacts["marketabilityAnalysis"] = {"dataStatus": "complete"}
        return {"checks": [{
            "key": "revenue_feasibility_vs_marketability", "status": "blocking",
            "leftValue": revenue, "rightValue": next_revenue,
        }]}

    result = ConsensusEngine().reach_consensus(project, {}, recalculate)
    assert calls == [200, 100.0, 200.0]
    assert result["stopReason"] == "oscillation"
    assert result["status"] == "partial"
    assert result["summary"]["converged"] is False
    assert result["unresolved"][0]["key"] == "revenue_feasibility_vs_marketability"


def test_nonconverging_fresh_cycle_stops_at_the_round_budget():
    calls = []

    def recalculate(working, artifacts):
        revenue = working["feasibility_input"]["total_revenue"]
        calls.append(revenue)
        artifacts["marketabilityAnalysis"] = {"dataStatus": "complete", "nextRevenue": revenue - 1}
        return {"checks": [{
            "key": "revenue_feasibility_vs_marketability", "status": "blocking",
            "leftValue": revenue, "rightValue": revenue - 1,
        }]}

    result = ConsensusEngine().reach_consensus({"feasibility_input": {"total_revenue": 200}}, {}, recalculate)
    assert calls == [200, 199.0, 198.0, 197.0]
    assert result["rounds"] == result["maxRounds"] == 3
    assert result["stopReason"] == "round_limit"
    assert result["status"] == "partial"
    assert result["summary"]["evaluationCount"] == 4


def test_unchanged_patch_is_not_a_resolution_and_does_not_spin():
    artifacts = {"marketabilityAnalysis": {"dataStatus": "complete"}}
    calls = []

    def stale_check(working, current):
        calls.append(True)
        return {"checks": [{
            "key": "revenue_feasibility_vs_marketability", "status": "blocking",
            "leftValue": 200, "rightValue": 100,
        }]}

    result = ConsensusEngine().reach_consensus({"feasibility_input": {"total_revenue": 100}}, artifacts, stale_check)
    assert calls == [True]
    assert result["status"] == "partial"
    assert result["stopReason"] == "no_progress"
    assert result["resolutions"] == []
    assert result["patches"] == []
    assert result["roundLog"][0]["recalculationPerformed"] is False


def test_callback_failure_does_not_mutate_original_artifacts_or_return_converged():
    project = {"land": {"area_sqm": 80}}
    artifacts = {"land": {"ready": True}}
    original_project, original_artifacts = deepcopy(project), deepcopy(artifacts)

    def failed_cycle(working, current):
        current["land"]["ready"] = False
        raise RuntimeError("PF recalculation failed")

    with pytest.raises(RuntimeError, match="PF recalculation failed"):
        ConsensusEngine().reach_consensus(project, artifacts, failed_cycle)
    assert project == original_project
    assert artifacts == original_artifacts


@pytest.mark.parametrize("invalid", [{}, {"checks": None}, {"checks": [{}]},
                                   {"checks": [{"key": "x", "status": "unknown"}]},
                                   {"checks": [{"key": "x", "status": "clear"}, {"key": "x", "status": "warning"}]},
                                   {"status": "failed", "checks": []}])
def test_invalid_or_failed_recalculation_cannot_be_treated_as_converged(invalid):
    with pytest.raises(ValueError):
        ConsensusEngine().reach_consensus({}, {}, lambda *_: invalid)


def test_callback_cannot_silently_patch_its_input_snapshot():
    project = {"land": {"area_sqm": 80}}

    def mutate_input(working, artifacts):
        working["land"]["area_sqm"] = 100
        return {"checks": []}

    with pytest.raises(ValueError, match="must not mutate its input"):
        ConsensusEngine().reach_consensus(project, {}, mutate_input)
    assert project == {"land": {"area_sqm": 80}}


@pytest.mark.parametrize("max_rounds", [-1, 0, 4, 100, True, "3", 1.5])
def test_round_limit_rejects_invalid_or_unbounded_configuration(max_rounds):
    with pytest.raises(ValueError, match="between 1 and 3"):
        ConsensusEngine().reach_consensus({}, {}, lambda *_: {"checks": []}, max_rounds=max_rounds)


def test_audit_hash_ignores_generated_bookkeeping_but_preserves_source_identity():
    counter = {"value": 0}

    def callback(source_id):
        def recalculate(working, artifacts):
            counter["value"] += 1
            artifacts["landAgentReview"] = {
                "generatedAt": str(counter["value"]), "runId": str(counter["value"]),
                "sourceLocator": {"evidenceId": source_id, "asOfDate": "2026-11-06"},
            }
            return {"checks": [], "generatedAt": str(counter["value"])}
        return recalculate

    first = ConsensusEngine().reach_consensus({}, {}, callback("source-a"))
    repeated = ConsensusEngine().reach_consensus({}, {}, callback("source-a"))
    changed_source = ConsensusEngine().reach_consensus({}, {}, callback("source-b"))
    assert first["evaluationLog"][0]["outputHash"] == repeated["evaluationLog"][0]["outputHash"]
    assert first["evaluationLog"][0]["outputHash"] != changed_source["evaluationLog"][0]["outputHash"]


def test_coordinator_can_only_order_conflicts_and_explain_not_change_values():
    class FaultyCoordinator:
        name = "test"

        def order_conflicts(self, conflicts):
            conflicts[0]["rightValue"] = 1
            return conflicts

        def rationale(self, resolution):
            resolution["patch"]["value"] = 999
            return "Ordering only"

    artifacts = _artifacts()
    project = {"land": {"area_sqm": 9688.0}, "feasibility_input": {"total_revenue": 130_000_000_000}}
    result = ConsensusEngine().reach_consensus(
        project, artifacts, _make_reconcile_fn(artifacts, include_readiness=False),
        coordinator=FaultyCoordinator(),
    )
    assert result["status"] == "converged"
    assert result["agreedProjectData"]["feasibility_input"]["total_revenue"] == 100_000_000_000
    assert result["patches"][0]["value"] == 100_000_000_000
