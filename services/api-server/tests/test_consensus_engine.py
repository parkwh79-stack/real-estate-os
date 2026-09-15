from __future__ import annotations

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
