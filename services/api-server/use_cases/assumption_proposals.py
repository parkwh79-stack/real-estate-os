from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from schemas.assumption_proposal import AssumptionProposal


def build_consensus_assumption_proposals(
    project_id: str,
    run_id: str,
    project_data: Dict[str, Any],
    consensus: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Convert consensus patches into reviewable proposals without mutation."""
    now = datetime.now(timezone.utc).isoformat()
    rationale_by_field = {
        str((item.get("patch") or {}).get("field")): item.get("rationale") or item.get("rule")
        for item in consensus.get("resolutions", [])
        if isinstance(item, dict) and isinstance(item.get("patch"), dict)
    }
    proposals: List[Dict[str, Any]] = []
    for index, patch in enumerate(consensus.get("patches", []), start=1):
        if not isinstance(patch, dict) or not patch.get("field"):
            continue
        field_path = str(patch["field"])
        proposal = AssumptionProposal(
            proposalId=f"proposal-{run_id}-{index}",
            projectId=project_id,
            runId=run_id,
            fieldPath=field_path,
            currentValue=_get_by_path(project_data, field_path),
            proposedValue=patch.get("value"),
            sourceAgent=patch.get("sourceAgent"),
            rationale=rationale_by_field.get(field_path),
            evidenceIds=[str(value) for value in patch.get("evidenceIds", []) if value],
            sourceLocator={
                "sourceType": "agent_artifact",
                "runId": run_id,
                "artifactKey": "consensusResolution",
                "fieldPath": field_path,
            },
            status="pending",
            createdAt=now,
        )
        proposals.append(proposal.model_dump(by_alias=True, mode="json"))
    return proposals


def approve_assumption_proposals(
    project_data: Dict[str, Any],
    proposals: List[Dict[str, Any]],
    proposal_ids: Iterable[str],
    *,
    approved_by: str,
    comment: str = "",
) -> Dict[str, Any]:
    """Apply approved proposals to a new auditable project version."""
    selected = {str(value) for value in proposal_ids if value}
    if not selected:
        raise ValueError("proposalIds must contain at least one proposal")
    matched = [item for item in proposals if str(item.get("proposalId")) in selected]
    if len(matched) != len(selected):
        raise LookupError("One or more assumption proposals were not found")
    invalid = [item.get("proposalId") for item in matched if item.get("status") != "pending"]
    if invalid:
        raise ValueError(f"Only pending proposals can be approved: {', '.join(invalid)}")

    now = datetime.now(timezone.utc).isoformat()
    updated = deepcopy(project_data)
    meta = updated.setdefault("_meta", {})
    previous_version = int(meta.get("projectVersion") or 1)
    new_version = previous_version + 1
    changes = []
    for proposal in matched:
        field_path = str(proposal["fieldPath"])
        previous_value = _get_by_path(updated, field_path)
        _set_by_path(updated, field_path, proposal.get("proposedValue"))
        proposal["status"] = "approved"
        proposal["reviewedAt"] = now
        proposal["reviewedBy"] = approved_by
        proposal["reviewComment"] = comment
        changes.append({
            "proposalId": proposal.get("proposalId"),
            "fieldPath": field_path,
            "previousValue": previous_value,
            "approvedValue": proposal.get("proposedValue"),
            "sourceAgent": proposal.get("sourceAgent"),
        })

    version_record = {
        "version": new_version,
        "baseVersion": previous_version,
        "createdAt": now,
        "createdBy": approved_by,
        "reason": "approved_assumption_proposals",
        "comment": comment,
        "changes": changes,
        "previousStateHash": _state_hash(project_data),
        "stateHash": _state_hash(updated),
    }
    meta["projectVersion"] = new_version
    meta.setdefault("projectVersionHistory", []).append(version_record)
    meta.setdefault("assumptionProposalApprovals", []).append({
        "approvedAt": now,
        "approvedBy": approved_by,
        "proposalIds": sorted(selected),
        "projectVersion": new_version,
        "comment": comment,
    })
    return {
        "project": updated,
        "proposals": proposals,
        "projectVersion": version_record,
    }


def _get_by_path(payload: Dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return deepcopy(current)


def _set_by_path(payload: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = payload
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = deepcopy(value)


def _state_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
