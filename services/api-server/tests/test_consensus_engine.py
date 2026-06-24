from __future__ import annotations

from agents.consensus_engine import ConsensusEngine


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
