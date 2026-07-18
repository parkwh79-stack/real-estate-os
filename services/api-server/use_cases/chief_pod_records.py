from __future__ import annotations

"""Persistence payload builders for Chief Pod execution.

These adapters never calculate financial values. They index deterministic Pod
outputs, preserve their source locators, and create typed communication records.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from uuid import uuid4

from schemas.orchestration import AgentHandoffEnvelope
from use_cases.calculation_run_capture import (
    scenario_engine_run_payloads,
    stress_test_run_payload,
)


_POD_INPUT_KEYS: Dict[str, tuple[str, ...]] = {
    "land_legal": (
        "land", "landLedger", "landEvidence", "regulation", "regulatoryConstraintSnapshot",
        "schedule", "covenants", "legalContext", "source_documents",
    ),
    "market_product": (
        "market", "marketEvidence", "product_mix", "productMix", "land", "landLedger",
        "regulation", "regulatoryConstraintSnapshot", "buildableEnvelope", "source_documents",
    ),
    "cost_schedule": ("feasibility_input", "costs", "schedule", "project_schedule", "source_documents"),
    "feasibility_cashflow": (
        "feasibility_input",
        "feasibilityStudyV2",
        "product_mix",
        "productMix",
        "schedule",
        "pf_structure",
        "marketabilityAnalysis",
        "regulatoryConstraintSnapshot",
        "buildableEnvelope",
    ),
    "pf_credit": (
        "pf_structure",
        "pfStructure",
        "cashFlowRunResult",
        "scenariosEvaluation",
        "marketabilityAnalysis",
        "landEvidence",
        "adoptedRestructuringCures",
        "legalPermitReview",
        "regulatoryConstraintSnapshot",
        "buildableEnvelope",
    ),
    "public_policy": ("land", "landLedger", "legalContext", "developabilityScore", "source_documents"),
}


def stable_digest(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def pod_input_snapshot(
    pod_key: str,
    project_data: Dict[str, Any],
    dependency_artifacts: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    snapshot = {
        key: project_data.get(key)
        for key in _POD_INPUT_KEYS.get(pod_key, ())
        if project_data.get(key) is not None
    }
    snapshot["dependencyArtifacts"] = dependency_artifacts or {}
    snapshot["activeAssumptionOverrides"] = project_data.get("activeAssumptionOverrides") or []
    return snapshot


def _iter_numeric_metrics(value: Any, path: str = "result") -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        explicit_locator = value.get("sourceLocator") or value.get("source_locator")
        for key, child in value.items():
            if key in {"sourceLocator", "source_locator", "sourceLocators", "source_locators"}:
                continue
            child_path = f"{path}.{key}"
            if isinstance(child, bool):
                continue
            if isinstance(child, (int, float)):
                yield {
                    "key": child_path,
                    "value": child,
                    "sourceLocator": explicit_locator
                    or {"sourceType": "chief_pod_artifact", "path": child_path},
                }
            elif isinstance(child, (dict, list)):
                yield from _iter_numeric_metrics(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, (dict, list)):
                yield from _iter_numeric_metrics(child, f"{path}[{index}]")


def _risk_issues(value: Any) -> List[Dict[str, Any]]:
    risks: List[Dict[str, Any]] = []
    if not isinstance(value, dict):
        return risks
    for key in ("riskFlags", "risk_flags", "riskIssues", "risk_issues"):
        rows = value.get(key)
        if isinstance(rows, list):
            risks.extend(row for row in rows if isinstance(row, dict))
    return risks


def _evidence_ids(value: Any) -> List[str]:
    found: List[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if key in {"evidenceId", "sourceUrlOrEvidenceId"} and child:
                    found.append(str(child))
                elif key in {"evidenceIds", "inputEvidenceIds"} and isinstance(child, list):
                    found.extend(str(item) for item in child if item)
                elif isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return list(dict.fromkeys(found))


def _assumption_ids(value: Any) -> List[str]:
    found: List[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"assumptionId"} and child:
                found.append(str(child))
            elif key in {"assumptionIds", "inputAssumptionIds"} and isinstance(child, list):
                found.extend(str(item) for item in child if item)
            elif isinstance(child, (dict, list)):
                found.extend(_assumption_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_assumption_ids(child))
    return list(dict.fromkeys(found))


def _generic_calculation_record(
    *,
    project_id: str,
    pod_key: str,
    artifact_key: str,
    engine_key: str,
    result: Dict[str, Any],
    input_snapshot: Dict[str, Any],
    engine_version: str | None = None,
    artifact_ref: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "projectId": project_id,
        "calculationRunId": f"calc:{engine_key}:{uuid4()}",
        "engineKey": engine_key,
        "engineVersion": engine_version or str(result.get("engineVersion") or f"{engine_key}-v1"),
        "status": "completed",
        "inputEvidenceIds": _evidence_ids(input_snapshot),
        "assumptionIds": _assumption_ids(input_snapshot),
        "outputMetrics": list(_iter_numeric_metrics(result)),
        "riskIssues": _risk_issues(result),
        "inputSnapshot": input_snapshot,
        "resultSnapshot": result,
        "artifactRef": artifact_ref
        or {"artifactKey": artifact_key, "podKey": pod_key, "section": engine_key},
        "sourceLocator": {
            "sourceType": "chief_pod_artifact",
            "artifactKey": artifact_key,
            "podKey": pod_key,
            "engineKey": engine_key,
        },
        "inputHash": stable_digest(input_snapshot),
        "outputHash": stable_digest(result),
        "createdAt": now,
        "updatedAt": now,
    }


def calculation_records_for_pod(
    *,
    project_id: str,
    pod_key: str,
    artifact_key: str,
    artifact: Dict[str, Any],
    input_snapshot: Dict[str, Any],
    project_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if pod_key == "feasibility_cashflow":
        feasibility = artifact.get("developerFeasibility")
        if isinstance(feasibility, dict):
            records.append(
                _generic_calculation_record(
                    project_id=project_id,
                    pod_key=pod_key,
                    artifact_key=artifact_key,
                    engine_key="development_feasibility",
                    result=feasibility,
                    input_snapshot=input_snapshot,
                )
            )
        cash_flow = artifact.get("cashFlowRunResult")
        if isinstance(cash_flow, dict):
            records.append(
                _generic_calculation_record(
                    project_id=project_id,
                    pod_key=pod_key,
                    artifact_key=artifact_key,
                    engine_key="cash_flow_engine",
                    result=cash_flow,
                    input_snapshot=input_snapshot,
                )
            )

    if pod_key == "pf_credit":
        pf_structure = artifact.get("pfStructure")
        if isinstance(pf_structure, dict):
            records.append(
                _generic_calculation_record(
                    project_id=project_id,
                    pod_key=pod_key,
                    artifact_key=artifact_key,
                    engine_key="pf_structure",
                    result=pf_structure,
                    input_snapshot=input_snapshot,
                )
            )
        scenario_dump = artifact.get("scenariosEvaluation")
        if isinstance(scenario_dump, dict):
            for payload in scenario_engine_run_payloads(scenario_dump):
                records.append(
                    _generic_calculation_record(
                        project_id=project_id,
                        pod_key=pod_key,
                        artifact_key=artifact_key,
                        engine_key=str(payload["engineKey"]),
                        engine_version=str(payload["engineVersion"]),
                        result=payload["result"],
                        input_snapshot=input_snapshot,
                        artifact_ref={
                            **(payload.get("artifactRef") or {}),
                            "artifactKey": artifact_key,
                            "podKey": pod_key,
                        },
                    )
                )
        stress = project_data.get("stressTest") or artifact.get("stressTest")
        if isinstance(stress, dict):
            payload = stress_test_run_payload(stress)
            records.append(
                _generic_calculation_record(
                    project_id=project_id,
                    pod_key=pod_key,
                    artifact_key=artifact_key,
                    engine_key=str(payload["engineKey"]),
                    engine_version=str(payload["engineVersion"]),
                    result=payload["result"],
                    input_snapshot=input_snapshot,
                    artifact_ref={
                        **(payload.get("artifactRef") or {}),
                        "artifactKey": artifact_key,
                        "podKey": pod_key,
                    },
                )
            )
    return records


def build_pod_handoffs(
    *,
    project_id: str,
    pod_key: str,
    artifact_key: str,
    downstream_pods: Iterable[str],
    artifact: Dict[str, Any],
    calculation_run_ids: Iterable[str],
    input_hash: str,
    output_hash: str,
    run_id: str | None = None,
) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    evidence_ids = _evidence_ids(artifact)
    handoffs: List[Dict[str, Any]] = []
    for target in downstream_pods:
        envelope = AgentHandoffEnvelope(
            handoffId=f"handoff:{pod_key}:{target}:{uuid4()}",
            workflow="chief-deal-orchestration",
            runId=run_id,
            handoffType="pod_artifact_ready",
            fromAgent=pod_key,
            toAgent=str(target),
            status="created",
            payloadSchema="realytics-chief-pod-handoff-payload-v1",
            payload={
                "projectId": project_id,
                "sourceArtifactKey": artifact_key,
                "calculationRunIds": list(calculation_run_ids),
                "inputHash": input_hash,
                "outputHash": output_hash,
            },
            evidenceIds=evidence_ids,
            confidence=1.0,
            requiresHumanReview=False,
            createdAt=now,
            progressImpact={"unblocks": [str(target)]},
        )
        handoffs.append(envelope.model_dump(by_alias=True))
    return handoffs
