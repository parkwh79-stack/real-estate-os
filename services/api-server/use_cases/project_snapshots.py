from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from db.repositories.project_snapshot_repository import ProjectSnapshotRepository


SIMILAR_CASE_SEARCH_SCHEMA_VERSION = "realytics-similar-case-search-v1"
SNAPSHOT_DIFF_SCHEMA_VERSION = "realytics-snapshot-diff-v1"


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


def diff_project_snapshots_use_case(
    snapshot_repository: ProjectSnapshotRepository,
    project_id: str,
    base_snapshot_id: str,
    compare_snapshot_id: str,
) -> Dict[str, Any]:
    """Compare two saved project snapshots for audit and re-review workflows."""
    base_snapshot = snapshot_repository.get(project_id, base_snapshot_id)
    compare_snapshot = snapshot_repository.get(project_id, compare_snapshot_id)
    if not base_snapshot:
        raise LookupError("Base project snapshot not found")
    if not compare_snapshot:
        raise LookupError("Compare project snapshot not found")

    seed_changes = _diff_seed(base_snapshot, compare_snapshot)
    risk_changes = _diff_risks(base_snapshot, compare_snapshot)
    decision_change = _diff_decision(base_snapshot, compare_snapshot)
    artifact_changes = _diff_artifacts(base_snapshot, compare_snapshot)
    report_changes = _diff_report(base_snapshot, compare_snapshot)
    material_changes = _material_changes(seed_changes, risk_changes, decision_change, artifact_changes, report_changes)
    return {
        "schemaVersion": SNAPSHOT_DIFF_SCHEMA_VERSION,
        "analysisType": "project_snapshot_diff",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "projectId": project_id,
        "baseSnapshotId": base_snapshot_id,
        "compareSnapshotId": compare_snapshot_id,
        "baseRunId": base_snapshot.get("runId"),
        "compareRunId": compare_snapshot.get("runId"),
        "summary": {
            "seedChangeCount": len(seed_changes),
            "addedRiskCount": len(risk_changes["added"]),
            "removedRiskCount": len(risk_changes["removed"]),
            "changedRiskCount": len(risk_changes["changed"]),
            "artifactAddedCount": len(artifact_changes["added"]),
            "artifactRemovedCount": len(artifact_changes["removed"]),
            "reportSectionAddedCount": len(report_changes["addedSections"]),
            "reportSectionRemovedCount": len(report_changes["removedSections"]),
            "decisionChanged": decision_change["changed"],
            "materialChangeCount": len(material_changes),
            "requiresReReview": bool(material_changes),
        },
        "seedChanges": seed_changes,
        "riskChanges": risk_changes,
        "decisionChange": decision_change,
        "artifactChanges": artifact_changes,
        "reportChanges": report_changes,
        "materialChanges": material_changes,
        "reReviewRecommendation": _re_review_recommendation(material_changes),
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


def _diff_seed(base: Dict[str, Any], compare: Dict[str, Any]) -> List[Dict[str, Any]]:
    base_seed = base.get("caseComparisonSeed") or {}
    compare_seed = compare.get("caseComparisonSeed") or {}
    keys = sorted(set(base_seed.keys()).union(compare_seed.keys()))
    changes = []
    for key in keys:
        before = base_seed.get(key)
        after = compare_seed.get(key)
        if before == after:
            continue
        change: Dict[str, Any] = {
            "field": key,
            "before": before,
            "after": after,
            "changeType": "changed",
            "material": key in _material_seed_fields(),
        }
        before_number = _to_float(before)
        after_number = _to_float(after)
        if before_number is not None and after_number is not None:
            absolute_change = after_number - before_number
            base_denominator = abs(before_number) or 1.0
            change["absoluteChange"] = absolute_change
            change["percentChange"] = round(absolute_change / base_denominator, 4)
            change["direction"] = "increased" if absolute_change > 0 else "decreased"
            if key in {"pfAmount", "totalRevenue", "totalCost", "landCost"} and abs(change["percentChange"]) >= 0.03:
                change["material"] = True
            if key in {"ltv", "ltc", "exitSaleRate"} and abs(absolute_change) >= 0.03:
                change["material"] = True
        changes.append(change)
    return changes


def _diff_risks(base: Dict[str, Any], compare: Dict[str, Any]) -> Dict[str, Any]:
    base_risks = _risk_index(base)
    compare_risks = _risk_index(compare)
    added = []
    removed = []
    changed = []
    for key, risk in compare_risks.items():
        if key not in base_risks:
            added.append(_risk_summary(risk))
            continue
        before = base_risks[key]
        field_changes = []
        for field in ["severity", "probability", "grade", "score", "reviewStatus", "mitigant", "creditDecisionImpact"]:
            if before.get(field) != risk.get(field):
                field_changes.append({
                    "field": field,
                    "before": before.get(field),
                    "after": risk.get(field),
                })
        if field_changes:
            changed.append({
                "code": key,
                "riskIdBefore": before.get("riskId"),
                "riskIdAfter": risk.get("riskId"),
                "category": risk.get("category") or before.get("category"),
                "fieldChanges": field_changes,
                "material": any(item["field"] in {"severity", "grade", "score", "reviewStatus"} for item in field_changes),
            })
    for key, risk in base_risks.items():
        if key not in compare_risks:
            removed.append(_risk_summary(risk))
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def _diff_decision(base: Dict[str, Any], compare: Dict[str, Any]) -> Dict[str, Any]:
    before = _decision_outcome(base)
    after = _decision_outcome(compare)
    field_changes = []
    for field in ["decision", "decisionLabel", "status", "confidence"]:
        if before.get(field) != after.get(field):
            field_changes.append({
                "field": field,
                "before": before.get(field),
                "after": after.get(field),
            })
    return {
        "changed": bool(field_changes),
        "before": before,
        "after": after,
        "fieldChanges": field_changes,
    }


def _diff_artifacts(base: Dict[str, Any], compare: Dict[str, Any]) -> Dict[str, Any]:
    base_keys = set((base.get("agentArtifacts") or {}).keys())
    compare_keys = set((compare.get("agentArtifacts") or {}).keys())
    return {
        "added": sorted(compare_keys - base_keys),
        "removed": sorted(base_keys - compare_keys),
        "unchanged": sorted(base_keys.intersection(compare_keys)),
    }


def _diff_report(base: Dict[str, Any], compare: Dict[str, Any]) -> Dict[str, Any]:
    base_sections = set(((base.get("report") or {}).get("sectionKeys")) or [])
    compare_sections = set(((compare.get("report") or {}).get("sectionKeys")) or [])
    return {
        "addedSections": sorted(compare_sections - base_sections),
        "removedSections": sorted(base_sections - compare_sections),
        "unchangedSections": sorted(base_sections.intersection(compare_sections)),
    }


def _material_changes(
    seed_changes: List[Dict[str, Any]],
    risk_changes: Dict[str, Any],
    decision_change: Dict[str, Any],
    artifact_changes: Dict[str, Any],
    report_changes: Dict[str, Any],
) -> List[Dict[str, Any]]:
    material = []
    for change in seed_changes:
        if change.get("material"):
            material.append({
                "type": "seed_change",
                "field": change["field"],
                "message": f"{change['field']} changed from {change.get('before')} to {change.get('after')}.",
            })
    for risk in risk_changes["added"]:
        if risk.get("severity") in {"critical", "high"}:
            material.append({
                "type": "risk_added",
                "code": risk.get("code"),
                "message": f"New material risk added: {risk.get('code')}.",
            })
    for risk in risk_changes["changed"]:
        if risk.get("material"):
            material.append({
                "type": "risk_changed",
                "code": risk.get("code"),
                "message": f"Risk changed materially: {risk.get('code')}.",
            })
    if decision_change.get("changed"):
        material.append({
            "type": "credit_decision_changed",
            "message": "Credit Review decision changed between snapshots.",
        })
    for artifact_key in artifact_changes["added"]:
        if artifact_key in {"riskRegister", "creditReviewDecision", "evidenceGraph", "dataQualityReview"}:
            material.append({
                "type": "artifact_added",
                "artifactKey": artifact_key,
                "message": f"Material artifact added: {artifact_key}.",
            })
    if report_changes["addedSections"] or report_changes["removedSections"]:
        material.append({
            "type": "report_section_changed",
            "message": "Report section coverage changed between snapshots.",
        })
    return material


def _re_review_recommendation(material_changes: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not material_changes:
        return {
            "required": False,
            "level": "none",
            "message": "No material change detected; reviewer can archive the diff for audit trail.",
            "recommendedActions": [],
        }
    action_map = {
        "seed_change": "Re-run affected calculations and confirm source assumptions.",
        "risk_added": "Review new Risk Register row and assign approval condition or waiver.",
        "risk_changed": "Re-review changed risk severity/status and update Credit Review memo.",
        "credit_decision_changed": "Require credit officer approval of changed decision.",
        "artifact_added": "Review newly introduced artifact and ensure report sections cite it.",
        "report_section_changed": "Check Phase 11 section completeness and evidence mapping.",
    }
    actions = [action_map.get(item["type"], "Review material change.") for item in material_changes]
    return {
        "required": True,
        "level": "credit_re_review",
        "message": "Material changes were detected; credit reviewer should re-open the affected review gates.",
        "recommendedActions": list(dict.fromkeys(actions)),
    }


def _risk_summary(risk: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "riskId": risk.get("riskId"),
        "code": risk.get("code"),
        "category": risk.get("category"),
        "severity": risk.get("severity"),
        "probability": risk.get("probability"),
        "grade": risk.get("grade"),
        "score": risk.get("score"),
        "reviewStatus": risk.get("reviewStatus"),
        "mitigant": risk.get("mitigant"),
        "evidenceGraphLinks": risk.get("evidenceGraphLinks"),
    }


def _material_seed_fields() -> set[str]:
    return {
        "projectType",
        "landAreaSqm",
        "totalRevenue",
        "totalCost",
        "landCost",
        "pfAmount",
        "ltv",
        "ltc",
        "exitSaleRate",
        "marketabilityGrade",
        "landReadinessStatus",
        "reconciliationStatus",
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
        if not candidate_risk and not query_risk.get("code"):
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
        "dimension": name,
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
