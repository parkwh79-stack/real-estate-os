from __future__ import annotations

"""Iterative cross-agent consensus loop.

Agents re-analyse until their cross-checked numbers agree. The loop is:
reconcile -> detect conflicts -> resolve each with an evidence-priority RULE
(adopt the evidence-backed side; conservative fallback) -> apply the patch to a
working copy of project_data -> reconcile again -> repeat until converged, only
evidence-required conflicts remain, or max rounds.

A *coordinator* sequences the conflicts, decides whether to continue, and writes
the rationale. The default coordinator is deterministic so the loop runs with no
LLM; an LLM coordinator can plug in to control ordering/convergence/explanation,
but it never invents numbers — patches always come from the deterministic
evidence-priority rules, and the underlying calculations stay deterministic.
"""

import copy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

CONSENSUS_SCHEMA_VERSION = "realytics-consensus-v1"

# Conflicts that require real source evidence (cannot be auto-resolved without
# fabricating data) — surfaced as unresolved, never silently cleared.
EVIDENCE_REQUIRED_KEYS = {
    "official_price_evidence_ready",
    "registry_evidence_ready",
    "market_evidence_ready",
    "pf_metrics_available",
}


def _num(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _set_by_path(data: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


class DeterministicCoordinator:
    """Default coordinator: severity-ordered, rule-derived rationale, no LLM."""

    name = "deterministic"

    def order_conflicts(self, conflicts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        severity_rank = {"blocking": 0, "warning": 1}
        return sorted(conflicts, key=lambda c: (severity_rank.get(c.get("status"), 2), c.get("key") or ""))

    def rationale(self, resolution: Dict[str, Any]) -> str:
        return resolution.get("rule") or "Adopted by evidence-priority rule."


class ConsensusEngine:
    analysis_type = "consensus_resolution"

    def reach_consensus(
        self,
        project_data: Dict[str, Any],
        artifacts: Dict[str, Any],
        reconcile_fn: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
        coordinator: Optional[Any] = None,
        max_rounds: int = 3,
    ) -> Dict[str, Any]:
        coordinator = coordinator or DeterministicCoordinator()
        working = copy.deepcopy(project_data)

        round_log: List[Dict[str, Any]] = []
        resolutions: List[Dict[str, Any]] = []
        patches: List[Dict[str, Any]] = []

        reconciliation = reconcile_fn(working, artifacts)
        for round_index in range(1, max_rounds + 1):
            conflicts = [c for c in (reconciliation.get("checks") or []) if c.get("status") in {"blocking", "warning"}]
            if not conflicts:
                break
            resolvable = [c for c in conflicts if c.get("key") not in EVIDENCE_REQUIRED_KEYS]
            ordered = coordinator.order_conflicts(resolvable)
            applied_this_round: List[Dict[str, Any]] = []
            for conflict in ordered:
                resolution = self._resolve(conflict, artifacts, working)
                if not resolution.get("resolved"):
                    continue
                resolution["rationale"] = coordinator.rationale(resolution)
                resolution["round"] = round_index
                patch = resolution.get("patch")
                if patch:
                    _set_by_path(working, patch["field"], patch["value"])
                    patches.append(patch)
                resolutions.append(resolution)
                applied_this_round.append(resolution)
            round_log.append({
                "round": round_index,
                "coordinator": coordinator.name,
                "conflictCount": len(conflicts),
                "resolvedKeys": [r["key"] for r in applied_this_round],
                "evidenceRequiredKeys": [c.get("key") for c in conflicts if c.get("key") in EVIDENCE_REQUIRED_KEYS],
            })
            if not applied_this_round:
                break  # only evidence-required conflicts remain; stop to avoid spinning
            reconciliation = reconcile_fn(working, artifacts)

        final_conflicts = [c for c in (reconciliation.get("checks") or []) if c.get("status") in {"blocking", "warning"}]
        unresolved = [
            {
                "key": c.get("key"),
                "label": c.get("label"),
                "status": c.get("status"),
                "reason": "Requires source evidence or human review (not auto-resolvable)."
                if c.get("key") in EVIDENCE_REQUIRED_KEYS
                else "Could not be reconciled by evidence-priority rules.",
            }
            for c in final_conflicts
        ]
        status = "converged" if not unresolved else "partial"
        return {
            "schemaVersion": CONSENSUS_SCHEMA_VERSION,
            "analysisType": self.analysis_type,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "coordinator": coordinator.name,
            "rounds": len(round_log),
            "roundLog": round_log,
            "resolutions": resolutions,
            "patches": patches,
            "agreedProjectData": working,
            "unresolved": unresolved,
            "summary": {
                "resolvedCount": len(resolutions),
                "unresolvedCount": len(unresolved),
                "converged": status == "converged",
            },
        }

    # ------------------------------------------------------------------ rules
    def _resolve(self, conflict: Dict[str, Any], artifacts: Dict[str, Any], working: Dict[str, Any]) -> Dict[str, Any]:
        key = conflict.get("key")
        if key == "revenue_feasibility_vs_marketability":
            return self._resolve_revenue(conflict, artifacts)
        if key == "land_area_project_vs_land_agent":
            return self._resolve_land_area(conflict, artifacts)
        return {"key": key, "resolved": False}

    def _resolve_revenue(self, conflict: Dict[str, Any], artifacts: Dict[str, Any]) -> Dict[str, Any]:
        feasibility_rev = _num(conflict.get("leftValue"))
        market_rev = _num(conflict.get("rightValue"))
        if feasibility_rev is None or market_rev is None:
            return {"key": conflict["key"], "resolved": False}
        marketability = artifacts.get("marketabilityAnalysis") or {}
        has_market_evidence = (
            marketability.get("dataStatus") == "complete"
            or bool(marketability.get("saleComparables") or marketability.get("records"))
            or _num(marketability.get("score")) is not None
        )
        if has_market_evidence:
            adopted, source, rule = market_rev, "marketability", (
                "증거 기반 마켓 적정매출이 사업수지 가정 매출보다 우선 — 마켓 매출 채택."
            )
        else:
            adopted, source = min(feasibility_rev, market_rev), "conservative"
            rule = "마켓 증거 부족 — 보수적(낮은) 매출을 합의값으로 채택."
        return {
            "key": conflict["key"],
            "label": conflict.get("label"),
            "resolved": True,
            "adoptedSource": source,
            "adoptedValue": adopted,
            "rule": rule,
            "patch": {"field": "feasibility_input.total_revenue", "value": adopted, "sourceAgent": "MarketabilityAgent"},
        }

    def _resolve_land_area(self, conflict: Dict[str, Any], artifacts: Dict[str, Any]) -> Dict[str, Any]:
        project_area = _num(conflict.get("leftValue"))
        land_area = _num(conflict.get("rightValue"))
        if land_area is None:
            return {"key": conflict["key"], "resolved": False}
        land_review = artifacts.get("landAgentReview") or {}
        readiness = land_review.get("evidenceReadiness") or {}
        land_backed = bool(
            readiness.get("pnuReady") or readiness.get("officialPriceReady") or readiness.get("registryEvidenceReady")
        )
        if not land_backed:
            # No land evidence to override the project's stated area.
            return {"key": conflict["key"], "resolved": False}
        return {
            "key": conflict["key"],
            "label": conflict.get("label"),
            "resolved": True,
            "adoptedSource": "landAgent",
            "adoptedValue": land_area,
            "rule": "증거 기반 토지 에이전트 필지면적이 우선 — 사업 대지면적을 토지 에이전트 값으로 정합.",
            "patch": {"field": "land.area_sqm", "value": land_area, "sourceAgent": "LandAgent"},
        }
