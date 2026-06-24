from __future__ import annotations

"""Similar-case RAG over reviewed project snapshots (institutional memory).

Complements the deterministic snapshot similarity search with semantic/lexical
retrieval: it indexes each reviewed case's comparison seed, risk codes, and
actual outcome into a retrieval index and returns the most relevant prior cases
for a free-text question or a query snapshot — each hit carrying its citation
(snapshotId / projectId / runId). It returns evidence to ground a reviewer's
judgement; it never produces figures or a credit decision.
"""

from typing import Any, Dict, List, Optional

from agents.retrieval import build_retrieval_index

SIMILAR_CASE_RAG_SCHEMA_VERSION = "realytics-similar-case-rag-v1"


def _seed_text(snapshot: Dict[str, Any]) -> str:
    seed = snapshot.get("caseComparisonSeed") or {}
    outcome = snapshot.get("historicalOutcome") or {}
    canonical = (snapshot.get("canonicalProject") or {}).get("project") or {}
    risk = snapshot.get("agentArtifacts", {}).get("riskRegister") or {}
    risk_codes = [
        str(r.get("code"))
        for r in (risk.get("risks") or [])
        if isinstance(r, dict) and r.get("code")
    ]
    parts = [
        canonical.get("name"),
        seed.get("projectType"),
        seed.get("location"),
        f"marketability {seed.get('marketabilityGrade')}",
        f"ltv {seed.get('ltv')}",
        f"exit {seed.get('exitSaleRate')}",
        f"outcome {outcome.get('outcomeStatus')}",
        " ".join(risk_codes),
    ]
    return " ".join(str(p) for p in parts if p not in (None, "", "None"))


def _snapshot_document(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": snapshot.get("snapshotId"),
        "text": _seed_text(snapshot),
        "metadata": {
            "snapshotId": snapshot.get("snapshotId"),
            "projectId": snapshot.get("projectId"),
            "runId": snapshot.get("runId"),
            "createdAt": snapshot.get("createdAt"),
            "caseComparisonSeed": snapshot.get("caseComparisonSeed") or {},
            "historicalOutcome": snapshot.get("historicalOutcome") or {},
        },
    }


def retrieve_similar_cases_rag(
    snapshots: List[Dict[str, Any]],
    *,
    query: Optional[str] = None,
    query_snapshot_id: Optional[str] = None,
    k: int = 5,
    index: Optional[Any] = None,
) -> Dict[str, Any]:
    """Retrieve prior reviewed cases relevant to a query or a query snapshot."""
    query_snapshot = None
    corpus: List[Dict[str, Any]] = []
    for snapshot in snapshots:
        if query_snapshot_id and snapshot.get("snapshotId") == query_snapshot_id:
            query_snapshot = snapshot
            continue  # don't retrieve the query case itself
        corpus.append(snapshot)

    resolved_query = query
    if not resolved_query and query_snapshot is not None:
        resolved_query = _seed_text(query_snapshot)

    if not resolved_query:
        return {
            "schemaVersion": SIMILAR_CASE_RAG_SCHEMA_VERSION,
            "status": "no_query",
            "hits": [],
            "count": 0,
        }

    index = index or build_retrieval_index()
    index.add([_snapshot_document(s) for s in corpus])
    raw_hits = index.search(resolved_query, k=k)

    hits = [
        {
            "snapshotId": hit["document"]["metadata"]["snapshotId"],
            "projectId": hit["document"]["metadata"]["projectId"],
            "runId": hit["document"]["metadata"]["runId"],
            "score": hit["score"],
            "citation": {
                "snapshotId": hit["document"]["metadata"]["snapshotId"],
                "projectId": hit["document"]["metadata"]["projectId"],
                "runId": hit["document"]["metadata"]["runId"],
                "createdAt": hit["document"]["metadata"]["createdAt"],
            },
            "caseComparisonSeed": hit["document"]["metadata"]["caseComparisonSeed"],
            "historicalOutcome": hit["document"]["metadata"]["historicalOutcome"],
        }
        for hit in raw_hits
    ]
    return {
        "schemaVersion": SIMILAR_CASE_RAG_SCHEMA_VERSION,
        "status": "ok",
        "backend": getattr(index, "backend", "lexical"),
        "query": resolved_query,
        "querySnapshotId": query_snapshot_id,
        "count": len(hits),
        "hits": hits,
    }
