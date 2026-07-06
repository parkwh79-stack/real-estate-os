from __future__ import annotations

"""CalculationRun payload adapters for the scenario/stress engines.

The registry already persists feasibility/cashflow/pf/return runs; this module
extends it to Waterfall, Unsold LTV, Tranche Recovery, and (discount) Stress
runs — the calculations behind the pilot blockers SENIOR_SHORTFALL and
UNSOLD_LTV_BREACH — so every blocker links back to a durable
``calculationRunId`` instead of a transient response body.

Each adapter derives ONLY deterministic summaries from the engine dump (counts,
min/max of already-computed rows) and routes the engine's own risk flags to the
run that produced them. Nothing is recomputed or invented here.
"""

from typing import Any, Dict, List

SCENARIO_ENGINE_VERSION = "calculation_scenario_engine_v1"
STRESS_ENGINE_VERSION = "stress_test_v1"

# Engine-key -> risk-flag code prefixes emitted by ScenarioEngineAgent.
_FLAG_ROUTING: Dict[str, tuple] = {
    "waterfall": ("WATERFALL_SHORTFALL", "SENIOR_SHORTFALL", "MEZZANINE_SHORTFALL", "STRESS_SENIOR_SHORTFALL"),
    "unsold_ltv": ("UNSOLD_LTV_BREACH", "UNSOLD_LTV_HIGH"),
    "tranche_recovery": ("TRIGGER_REQUIRED", "RECOVERY", "TRANCHE"),
}


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _flags_for(engine_key: str, flags: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    prefixes = _FLAG_ROUTING.get(engine_key, ())
    return [
        flag
        for flag in flags
        if isinstance(flag, dict) and str(flag.get("code") or "").startswith(prefixes)
    ]


def scenario_engine_run_payloads(
    dump: Dict[str, Any],
    *,
    engines: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """Build per-engine CalculationRun payloads from one ScenarioSetResult dump.

    Returns [{engineKey, engineVersion, result, artifactRef}] for the requested
    engines (default: all three).
    """
    engines = engines or ["waterfall", "unsold_ltv", "tranche_recovery"]
    flags = [f for f in (dump.get("riskFlags") or []) if isinstance(f, dict)]
    scenario_set_id = dump.get("scenarioSetId")
    payloads: List[Dict[str, Any]] = []

    if "waterfall" in engines:
        rows = [r for r in (dump.get("waterfallScenarios") or []) if isinstance(r, dict)]
        engine_flags = _flags_for("waterfall", flags)
        payloads.append(
            {
                "engineKey": "waterfall",
                "engineVersion": SCENARIO_ENGINE_VERSION,
                "result": {
                    "summary": {
                        "scenarioCount": len(rows),
                        "shortfallFlagCount": len(engine_flags),
                    },
                    "riskFlags": engine_flags,
                },
                "artifactRef": {"type": "scenario_set", "scenarioSetId": scenario_set_id, "section": "waterfallScenarios"},
            }
        )

    if "unsold_ltv" in engines:
        matrix = dump.get("unsoldLtvMatrix") or {}
        rows = [r for r in (matrix.get("rows") or []) if isinstance(r, dict)]
        ltvs = [v for v in (_num(r.get("unsoldLtv")) for r in rows) if v is not None]
        engine_flags = _flags_for("unsold_ltv", flags)
        summary: Dict[str, Any] = {
            "rowCount": len(rows),
            "breachFlagCount": len([f for f in engine_flags if f.get("code") == "UNSOLD_LTV_BREACH"]),
        }
        if ltvs:
            summary["maxUnsoldLtv"] = max(ltvs)
            summary["minUnsoldLtv"] = min(ltvs)
        pf_amount = _num(matrix.get("pfAmount"))
        if pf_amount is not None:
            summary["pfAmount"] = pf_amount
        payloads.append(
            {
                "engineKey": "unsold_ltv",
                "engineVersion": SCENARIO_ENGINE_VERSION,
                "result": {"summary": summary, "riskFlags": engine_flags},
                "artifactRef": {"type": "scenario_set", "scenarioSetId": scenario_set_id, "section": "unsoldLtvMatrix"},
            }
        )

    if "tranche_recovery" in engines:
        rows = [r for r in (dump.get("trancheRecoveryMatrix") or []) if isinstance(r, dict)]
        pools = [v for v in (_num(r.get("recoveryPool")) for r in rows) if v is not None]
        engine_flags = _flags_for("tranche_recovery", flags)
        summary = {"scenarioCount": len(rows), "flagCount": len(engine_flags)}
        if pools:
            summary["minRecoveryPool"] = min(pools)
            summary["maxRecoveryPool"] = max(pools)
        payloads.append(
            {
                "engineKey": "tranche_recovery",
                "engineVersion": SCENARIO_ENGINE_VERSION,
                "result": {"summary": summary, "riskFlags": engine_flags},
                "artifactRef": {"type": "scenario_set", "scenarioSetId": scenario_set_id, "section": "trancheRecoveryMatrix"},
            }
        )

    return payloads


def stress_test_run_payload(dump: Dict[str, Any]) -> Dict[str, Any]:
    """CalculationRun payload for a StressTestResult dump.

    Covers the discount-stress stages (severe 10% / tail 20% 할인) — the run's
    riskIssues carry STRESS_SENIOR_SHORTFALL / UNSOLD_LTV_BREACH style flags.
    """
    scenarios = [s for s in (dump.get("scenarios") or []) if isinstance(s, dict)]
    flags = [f for f in (dump.get("riskFlags") or []) if isinstance(f, dict)]
    discount_stages = [s for s in scenarios if (_num(s.get("discountRate")) or 0) > 0]
    summary: Dict[str, Any] = {
        "scenarioCount": len(scenarios),
        "discountStressStageCount": len(discount_stages),
        "riskFlagCount": len(flags),
    }
    hurdle = _num(dump.get("hurdleRate"))
    if hurdle is not None:
        summary["hurdleRate"] = hurdle
    return {
        "engineKey": "stress_test",
        "engineVersion": STRESS_ENGINE_VERSION,
        "result": {"summary": summary, "riskFlags": flags},
        "artifactRef": {
            "type": "stress_test",
            "worstCaseKey": dump.get("worstCaseKey"),
            "seniorBreakScenarioKey": dump.get("seniorBreakScenarioKey"),
            "firstLossScenarioKey": dump.get("firstLossScenarioKey"),
        },
    }
