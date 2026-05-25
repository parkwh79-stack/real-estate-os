from db.repositories.project_snapshot_repository import ProjectSnapshotRepository
from use_cases.project_snapshots import search_similar_project_snapshots_use_case


def test_similar_case_search_matches_seed_metrics_and_risk_handling():
    store = {
        "p-query": {
            "snapshot-run-query": _snapshot(
                project_id="p-query",
                run_id="run-query",
                project_type="mixed_use_residential",
                location="대구 달서구 본리동",
                pf_amount=90_000_000_000,
                exit_sale_rate=0.67,
                risk_code="PRICE_PREMIUM_HIGH",
                review_status="pending_review",
            )
        },
        "p-similar": {
            "snapshot-run-similar": _snapshot(
                project_id="p-similar",
                run_id="run-similar",
                project_type="mixed_use_residential",
                location="대구 달서구 본동",
                pf_amount=88_000_000_000,
                exit_sale_rate=0.66,
                risk_code="PRICE_PREMIUM_HIGH",
                review_status="waived",
                decision="conditional_approval",
            )
        },
        "p-other": {
            "snapshot-run-other": _snapshot(
                project_id="p-other",
                run_id="run-other",
                project_type="logistics",
                location="부산 강서구",
                pf_amount=30_000_000_000,
                exit_sale_rate=0.30,
                risk_code="REGISTRY_EVIDENCE_INCOMPLETE",
                review_status="resolved",
                decision="approve",
            )
        },
    }
    repo = ProjectSnapshotRepository(store, lambda: None)

    result = search_similar_project_snapshots_use_case(repo, "p-query", {"limit": 2})

    assert result["schemaVersion"] == "realytics-similar-case-search-v1"
    assert result["querySnapshotId"] == "snapshot-run-query"
    assert result["similarCases"][0]["projectId"] == "p-similar"
    assert result["similarCases"][0]["similarityScore"] > result["similarCases"][1]["similarityScore"]
    assert result["similarCases"][0]["riskMatches"][0]["matchedCode"] == "PRICE_PREMIUM_HIGH"
    assert result["similarCases"][0]["riskMatches"][0]["reviewStatus"] == "waived"
    assert result["similarCases"][0]["decisionOutcome"]["decision"] == "conditional_approval"
    assert result["similarCases"][0]["handlingSummary"]["approvalConditions"]


def _snapshot(
    *,
    project_id,
    run_id,
    project_type,
    location,
    pf_amount,
    exit_sale_rate,
    risk_code,
    review_status,
    decision="conditional_approval",
):
    risk_id = "R-MKT-001" if risk_code == "PRICE_PREMIUM_HIGH" else "R-LAND-001"
    return {
        "schemaVersion": "realytics-project-snapshot-v1",
        "snapshotId": f"snapshot-{run_id}",
        "projectId": project_id,
        "runId": run_id,
        "workflow": "pf_review",
        "status": "completed",
        "createdAt": f"2026-05-2{1 if project_id == 'p-query' else 0}T00:00:00+00:00",
        "caseComparisonSeed": {
            "projectType": project_type,
            "location": location,
            "landAreaSqm": 4_700,
            "totalRevenue": 165_000_000_000,
            "totalCost": 140_000_000_000,
            "landCost": 41_000_000_000,
            "pfAmount": pf_amount,
            "ltv": 0.54,
            "ltc": 0.72,
            "exitSaleRate": exit_sale_rate,
            "marketabilityGrade": "B",
            "landReadinessStatus": "needs_enrichment",
            "reconciliationStatus": "warning",
        },
        "agentArtifacts": {
            "riskRegister": {
                "approvalConditions": [f"{risk_id} approval condition for historical handling"],
                "risks": [
                    {
                        "riskId": risk_id,
                        "code": risk_code,
                        "category": "Market Risk" if risk_id.startswith("R-MKT") else "Land Risk",
                        "severity": "high",
                        "reviewStatus": review_status,
                        "mitigant": "Use discount trigger and require additional evidence.",
                        "evidenceGraphLinks": {"riskNodeId": f"risk:{risk_id}"},
                    }
                ],
            },
            "creditReviewDecision": {
                "decision": decision,
                "decisionLabel": decision,
                "status": "completed",
                "confidence": 0.82,
                "approvalConditions": [f"{risk_id} approval condition for historical handling"],
            },
        },
    }
