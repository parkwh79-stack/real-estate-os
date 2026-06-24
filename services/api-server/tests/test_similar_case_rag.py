from __future__ import annotations

from use_cases.similar_case_rag import retrieve_similar_cases_rag


def _snap(sid, project_type, location, grade, outcome, risks):
    return {
        "snapshotId": sid,
        "projectId": "p-" + sid,
        "runId": "r-" + sid,
        "createdAt": "2026-01-01",
        "canonicalProject": {"project": {"name": sid + " 사업"}},
        "caseComparisonSeed": {"projectType": project_type, "location": location, "marketabilityGrade": grade,
                                "ltv": 0.6, "exitSaleRate": 0.6},
        "historicalOutcome": {"outcomeStatus": outcome},
        "agentArtifacts": {"riskRegister": {"risks": [{"code": c} for c in risks]}},
    }


def _corpus():
    return [
        _snap("s1", "mixed_use", "대구 수성구", "B", "repaid", ["UNSOLD_LTV_BREACH"]),
        _snap("s2", "logistics", "춘천", "C", "loss_realized", ["NO_COMPARABLE_DATA"]),
        _snap("s3", "mixed_use", "대구 달서구", "B", "repaid", ["UNSOLD_LTV_BREACH"]),
    ]


def test_retrieve_by_free_text_returns_cited_hits():
    res = retrieve_similar_cases_rag(_corpus(), query="대구 mixed_use UNSOLD_LTV_BREACH", k=2)
    assert res["status"] == "ok"
    assert res["backend"] == "lexical"
    assert res["count"] >= 1
    top_ids = [h["snapshotId"] for h in res["hits"]]
    assert "s1" in top_ids or "s3" in top_ids  # 대구 mixed_use cases rank top
    assert res["hits"][0]["citation"]["projectId"].startswith("p-")
    assert "historicalOutcome" in res["hits"][0]


def test_query_snapshot_is_excluded_from_results():
    res = retrieve_similar_cases_rag(_corpus(), query_snapshot_id="s1", k=5)
    assert res["status"] == "ok"
    assert all(h["snapshotId"] != "s1" for h in res["hits"])


def test_no_query_returns_empty():
    res = retrieve_similar_cases_rag(_corpus(), k=5)
    assert res["status"] == "no_query"
    assert res["hits"] == []
