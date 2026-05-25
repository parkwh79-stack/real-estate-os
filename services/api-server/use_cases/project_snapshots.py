from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from db.repositories.project_snapshot_repository import ProjectSnapshotRepository


SIMILAR_CASE_SEARCH_SCHEMA_VERSION = "realytics-similar-case-search-v1"


def list_project_snapshots_use_case(
    snapshot_repository: ProjectSnapshotRepository,
    project_id: str,
) -> List[Dict[str, Any]]:
    """List saved review snapshots for a project."""
    return snapshot_repository.list_metadata(project_id)


def get_project_snapshot_use_case(
    snapshot_repository: ProjectSnapshotRepository,
    project_id: str,
    snapshot_id: str,
) -> Dict[str, Any]:
    """Get one saved project review snapshot."""
    snapshot = snapshot_repository.get(project_id, snapshot_id)
    if not snapshot:
        raise LookupError("Project snapshot not found")
    return snapshot


def search_similar_project_snapshots_use_case(
    snapshot_repository: ProjectSnapshotRepository,
    project_id: str,
    options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Search stored project snapshots for deterministic similar PF review cases."""
    options = options or {}
    limit = int(options.get("limit") or 5)
    include_same_project = bool(options.get("include_same_project", options.get("includeSameProject", False)))
    min_score = float(options.get("min_score") or options.get("minScore") or 0.0)
    snapshot_id = options.get("snapshot_id") or options.get("snapshotId")

    query_snapshot = (
        snapshot_repository.get(project_id, str(snapshot_id))
        if snapshot_id
        else snapshot_repository.latest(project_id)
    )
    if not query_snapshot:
        raise LookupError("Project snapshot not found")

    candidates = []
    for candidate in snapshot_repository.list_all():
        if candidate.get("snapshotId") == query_snapshot.get("snapshotId"):
            continue
        if not include_same_project and candidate.get("projectId") == project_id:
            continue
        scored = _score_snapshot_similarity(query_snapshot, candidate)
        if scored["similarityScore"] >= min_score:
            candidates.append(scored)

    candidates = sorted(candidates, key=lambda item: item["similarityScore"], reverse=True)[:limit]
    query_risks = _risk_index(query_snapshot)
    return {
        "schemaVersion": SIMILAR_CASE_SEARCH_SCHEMA_VERSION,
        "analysisType": "historical_similar_case_search",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "queryProjectId": project_id,
        "querySnapshotId": query_snapshot.get("snapshotId"),
        "queryRunId": query_snapshot.get("runId"),
        "querySeed": query_snapshot.get("caseComparisonSeed", {}),
        "summary": {
            "candidateCount": len(snapshot_repository.list_all()) - 1,
            "returnedCount": len(candidates),
            "queryRiskCount": len(query_risks),
            "topScore": candidates[0]["similarityScore"] if candidates else 0,
        },
        "similarCases": candidates,
        "dataLimitations": _similar_case_limitations(query_snapshot, candidates),
    }


def _score_snapshot_similarity(query: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    query_seed = query.get("caseComparisonSeed") or {}
    candidate_seed = candidate.get("caseComparisonSeed") or {}
    dimensions = [
        _categorical_score("projectType", query_seed.get("projectType"), candidate_seed.get("projectType"), 0.16),
        _location_score(query_seed.get("location"), candidate_seed.get("location"), 0.10),
        _numeric_score("landAreaSqm", query_seed.get("landAreaSqm"), candidate_seed.get("landAreaSqm"), 0.08),
        _numeric_score("totalRevenue", query_seed.get("totalRevenue"), candidate_seed.get("totalRevenue"), 0.10),
        _numeric_score("totalCost", query_seed.get("totalCost"), candidate_seed.get("totalCost"), 0.08),
        _numeric_score("landCost", query_seed.get("landCost"), candidate_seed.get("landCost"), 0.06),
        _numeric_score("pfAmount", query_seed.get("pfAmount"), candidate_seed.get("pfAmount"), 0.12),
        _numeric_score("ltv", query_seed.get("ltv"), candidate_seed.get("ltv"), 0.07, tolerance=0.25),
        _numeric_score("ltc", query_seed.get("ltc"), candidate_seed.get("ltc"), 0.07, tolerance=0.25),
        _numeric_score("exitSaleRate", query_seed.get("exitSaleRate"), candidate_seed.get("exitSaleRate"), 0.08, tolerance=0.25),
        _categorical_score("marketabilityGrade", query_seed.get("marketabilityGrade"), candidate_seed.get("marketabilityGrade"), 0.04),
        _categorical_score("landReadinessStatus", query_seed.get("landReadinessStatus"), candidate_seed.get("landReadinessStatus"), 0.02),
        _categorical_score("reconciliationStatus", query_seed.get("reconciliationStatus"), candidate_seed.get("reconciliationStatus"), 0.02),
    ]
    risk_score = _risk_overlap_score(query, candidate, 0.20)
    dimensions.append(risk_score)

    available_weight = sum(item["weight"] for item in dimensions if item["available"])
    weighted_score = sum(item["score"] * item["weight"] for item in dimensions if item["available"])
    similarity = weighted_score / available_weight if available_weight else 0.0
    return {
        "projectId": candidate.get("projectId"),
        "snapshotId": candidate.get("snapshotId"),
        "runId": candidate.get("runId"),
        "workflow": candidate.get("workflow"),
        "createdAt": candidate.get("createdAt"),
        "similarityScore": round(similarity, 4),
        "scoreBreakdown": dimensions,
        "matchedDimensions": [
            item["name"]
            for item in dimensions
            if item["available"] and item["score"] >= 0.75
        ],
        "caseComparisonSeed": candidate_seed,
        "riskMatches": _risk_matches(query, candidate),
        "decisionOutcome": _decision_outcome(candidate),
        "approvalConditions": _approval_conditions(candidate),
        "handlingSummary": _handling_summary(query, candidate),
    }


def _numeric_score(
    name: str,
    query_value: Any,
    candidate_value: Any,
    weight: float,
    tolerance: float = 0.5,
) -> Dict[str, Any]:
    query_number = _to_float(query_value)
    candidate_number = _to_float(candidate_value)
    if query_number is None or candidate_number is None:
        return _dimension(name, weight, 0.0, False, "missing_numeric_value")
    denominator = max(abs(query_number), abs(candidate_number), 1.0)
    difference_ratio = abs(query_number - candidate_number) / denominator
    score = max(0.0, 1.0 - (difference_ratio / tolerance))
    return _dimension(
        name,
        weight,
        score,
        True,
        "numeric_closeness",
        {"query": query_number, "candidate": candidate_number, "differenceRatio": round(difference_ratio, 4)},
    )


def _categorical_score(name: str, query_value: Any, candidate_value: Any, weight: float) -> Dict[str, Any]:
    if query_value in {None, ""} or candidate_value in {None, ""}:
        return _dimension(name, weight, 0.0, False, "missing_categorical_value")
    score = 1.0 if str(query_value).lower() == str(candidate_value).lower() else 0.0
    return _dimension(
        name,
        weight,
        score,
        True,
        "exact_match" if score else "different_value",
        {"query": query_value, "candidate": candidate_value},
    )


def _location_score(query_location: Any, candidate_location: Any, weight: float) -> Dict[str, Any]:
    if not query_location or not candidate_location:
        return _dimension("location", weight, 0.0, False, "missing_location")
    query_tokens = _location_tokens(str(query_location))
    candidate_tokens = _location_tokens(str(candidate_location))
    if not query_tokens or not candidate_tokens:
        return _dimension("location", weight, 0.0, False, "missing_location_tokens")
    overlap = len(query_tokens.intersection(candidate_tokens))
    union = len(query_tokens.union(candidate_tokens))
    score = overlap / union if union else 0.0
    return _dimension(
        "location",
        weight,
        score,
        True,
        "token_overlap",
        {"query": query_location, "candidate": candidate_location, "matchedTokens": sorted(query_tokens.intersection(candidate_tokens))},
    )


def _risk_overlap_score(query: Dict[str, Any], candidate: Dict[str, Any], weight: float) -> Dict[str, Any]:
    matches = _risk_matches(query, candidate)
    query_risks = _risk_index(query)
    if not query_risks:
        return _dimension("riskOverlap", weight, 0.0, False, "query_has_no_risk_register")
    score = len(matches) / max(len(query_risks), 1)
    return _dimension(
        "riskOverlap",
        weight,
        min(1.0, score),
        True,
        "risk_code_or_category_overlap",
        {"matchedRiskCount": len(matches), "queryRiskCount": len(query_risks)},
    )


def _risk_matches(query: Dict[str, Any], candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
    query_risks = _risk_index(query)
    candidate_risks = _risk_index(candidate)
    matches = []
    for query_key, query_risk in query_risks.items():
        candidate_risk = candidate_risks.get(query_key)
        if not candidate_risk:
            candidate_risk = next(
                (
                    risk
                    for risk in candidate_risks.values()
                    if risk.get("category") == query_risk.get("category")
                    and risk.get("severity") == query_risk.get("severity")
                ),
                None,
            )
        if not candidate_risk:
            continue
        matches.append({
            "queryRiskId": query_risk.get("riskId"),
            "queryCode": query_risk.get("code"),
            "matchedRiskId": candidate_risk.get("riskId"),
            "matchedCode": candidate_risk.get("code"),
            "category": candidate_risk.get("category"),
            "severity": candidate_risk.get("severity"),
            "reviewStatus": candidate_risk.get("reviewStatus"),
            "mitigant": candidate_risk.get("mitigant"),
            "approvalCondition": _condition_for_risk(candidate, candidate_risk.get("riskId")),
            "evidenceGraphLinks": candidate_risk.get("evidenceGraphLinks"),
        })
    return matches


def _risk_index(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    risk_register = (snapshot.get("agentArtifacts") or {}).get("riskRegister") or {}
    risks = risk_register.get("risks") or []
    indexed: Dict[str, Dict[str, Any]] = {}
    for risk in risks:
        if not isinstance(risk, dict):
            continue
        key = str(risk.get("code") or risk.get("riskId") or risk.get("title") or "")
        if key:
            indexed[key] = risk
    return indexed


def _decision_outcome(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    decision = (snapshot.get("agentArtifacts") or {}).get("creditReviewDecision") or {}
    return {
        "decision": decision.get("decision"),
        "decisionLabel": decision.get("decisionLabel"),
        "status": decision.get("status"),
        "confidence": decision.get("confidence"),
    }


def _approval_conditions(snapshot: Dict[str, Any]) -> List[str]:
    credit = (snapshot.get("agentArtifacts") or {}).get("creditReviewDecision") or {}
    risk_register = (snapshot.get("agentArtifacts") or {}).get("riskRegister") or {}
    conditions = []
    if isinstance(credit.get("approvalConditions"), list):
        conditions.extend(str(item) for item in credit.get("approvalConditions") if item)
    if isinstance(risk_register.get("approvalConditions"), list):
        conditions.extend(str(item) for item in risk_register.get("approvalConditions") if item)
    return list(dict.fromkeys(conditions))


def _condition_for_risk(snapshot: Dict[str, Any], risk_id: Any) -> str | None:
    if not risk_id:
        return None
    risk_id_text = str(risk_id)
    for condition in _approval_conditions(snapshot):
        if risk_id_text in condition:
            return condition
    return None


def _handling_summary(query: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    matches = _risk_matches(query, candidate)
    return {
        "matchedRiskCount": len(matches),
        "reviewStatuses": sorted({str(match.get("reviewStatus")) for match in matches if match.get("reviewStatus")}),
        "mitigants": list(dict.fromkeys(str(match.get("mitigant")) for match in matches if match.get("mitigant")))[:5],
        "approvalConditions": _approval_conditions(candidate)[:5],
    }


def _dimension(
    name: str,
    weight: float,
    score: float,
    available: bool,
    method: str,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "name": name,
        "weight": weight,
        "score": round(max(0.0, min(1.0, score)), 4),
        "available": available,
        "method": method,
        "metadata": metadata or {},
    }


def _to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _location_tokens(value: str) -> set[str]:
    cleaned = value.replace(",", " ").replace("-", " ")
    return {token for token in cleaned.split() if len(token) >= 2}


def _similar_case_limitations(query_snapshot: Dict[str, Any], candidates: List[Dict[str, Any]]) -> List[str]:
    limitations = []
    seed = query_snapshot.get("caseComparisonSeed") or {}
    missing_seed_keys = [
        key
        for key in ["projectType", "location", "totalRevenue", "pfAmount", "ltv", "ltc", "exitSaleRate"]
        if seed.get(key) in {None, ""}
    ]
    if missing_seed_keys:
        limitations.append(f"Query snapshot is missing comparison seed fields: {', '.join(missing_seed_keys)}.")
    if not candidates:
        limitations.append("No similar historical snapshot matched the current filters.")
    limitations.append("This baseline search is deterministic over stored snapshots and does not yet use vector embeddings or actual post-closing outcomes.")
    return limitations
