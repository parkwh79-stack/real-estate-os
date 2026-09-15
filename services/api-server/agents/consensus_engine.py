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
import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from agents.agent_policy import system_prompt_for

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


class LLMCoordinator:
    """LLM-controlled coordinator: the model sequences conflicts and writes the
    rationale, but never produces or alters numbers — patches still come from the
    deterministic evidence-priority rules. Any failure (no key, bad output) falls
    back to deterministic ordering, so the loop always runs.
    """

    name = "llm"
    _SYSTEM = (
        "You are a PF credit-committee coordinator. Given cross-agent reconciliation "
        "conflicts, decide the ORDER to resolve them and give a one-line Korean rationale "
        "per conflict. You must NOT compute, invent, or change any numbers — the engine "
        "applies fixed evidence-priority rules. Respond with JSON only: "
        '{"order": ["conflictKey", ...], "rationale": {"conflictKey": "..."}, "note": "..."}.'
    )

    def __init__(self, ai_context: Dict[str, Any], connection: Optional[Any] = None):
        self.ai_context = ai_context
        self._connection = connection
        self._rationale: Dict[str, str] = {}
        self._fallback = DeterministicCoordinator()

    def _conn(self):
        if self._connection is None:
            from agents.ai_connection import AIConnection

            self._connection = AIConnection()
        return self._connection

    def order_conflicts(self, conflicts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        self._rationale = {}
        keys = [c.get("key") for c in conflicts]
        by_key = {c.get("key"): c for c in conflicts}
        prompt = json.dumps(
            {
                "conflicts": [
                    {
                        "key": c.get("key"),
                        "label": c.get("label"),
                        "status": c.get("status"),
                        "leftValue": c.get("leftValue"),
                        "rightValue": c.get("rightValue"),
                    }
                    for c in conflicts
                ]
            },
            ensure_ascii=False,
        )
        try:
            result = self._conn().extract_json(
                prompt,
                system=system_prompt_for("consensus", base=self._SYSTEM),
                ai_context=self.ai_context,
            )
            data = result.data if result and result.error is None else None
            if not isinstance(data, dict):
                raise ValueError("no coordinator response")
            raw_order = list(dict.fromkeys(
                k for k in (data.get("order") or []) if isinstance(k, str) and k in by_key
            ))
            # keep it a strict permutation: append any conflicts the model omitted
            ordered_keys = raw_order + [k for k in keys if k not in raw_order]
            rationale = data.get("rationale") or {}
            if isinstance(rationale, dict):
                self._rationale = {k: str(v) for k, v in rationale.items() if k in by_key}
            return [by_key[k] for k in ordered_keys]
        except Exception:
            return self._fallback.order_conflicts(conflicts)

    def rationale(self, resolution: Dict[str, Any]) -> str:
        return self._rationale.get(resolution.get("key")) or resolution.get("rule") or "Adopted by evidence-priority rule."


def build_consensus_coordinator(ai_context: Optional[Dict[str, Any]] = None, connection: Optional[Any] = None):
    """Return an LLM coordinator when an AI is available, else the deterministic one."""
    if ai_context:
        return LLMCoordinator(ai_context, connection=connection)
    return DeterministicCoordinator()


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
