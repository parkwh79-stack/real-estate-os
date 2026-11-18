from contextlib import contextmanager
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from db.repositories.market_evidence_repository import MarketEvidenceConflict, PostgresMarketEvidenceRepository
from use_cases.evidence_review import list_market_evidence_use_case, review_market_evidence_use_case


class EvidenceDatabase:
    def __init__(self):
        self.rows = {
            ("tenant-a", "p", "run-a:ev"): {
                "projectId": "p", "recordKey": "run-a:ev", "runId": "run-a",
                "value": 123, "evidenceId": "ev", "reviewStatus": "pending_review",
            },
            ("tenant-b", "p", "run-b:ev"): {
                "projectId": "p", "recordKey": "run-b:ev", "runId": "run-b",
                "value": 999, "evidenceId": "ev", "reviewStatus": "pending_review",
            },
        }
        self.scope = None
        self.queries = []

    @contextmanager
    def transaction(self, tenant):
        previous, self.scope = self.scope, tenant
        try:
            yield self
        finally:
            self.scope = previous

    def execute(self, query, params):
        self.queries.append(query)
        if query.strip().startswith("SELECT"):
            tenant, project = params[:2]
            assert tenant == self.scope
            matches = [
                (key[2], deepcopy(value)) for key, value in self.rows.items()
                if key[:2] == (tenant, project) and (len(params) == 2 or key[2] == params[2])
            ]
        else:
            assert query.strip().startswith("UPDATE realytics.market_evidence")
            assert "AND payload_json = %s::jsonb" in query
            payload, tenant, project, key, expected = params
            assert tenant == self.scope
            identity = (tenant, project, key)
            matches = []
            if self.rows.get(identity) == json.loads(expected):
                self.rows[identity] = json.loads(payload)
                matches = [(key,)]

        class Result:
            def fetchone(self):
                return matches[0] if matches else None

            def fetchall(self):
                return matches

        return Result()


def test_normalized_market_evidence_reopens_reviews_and_rejects_lost_updates():
    database = EvidenceDatabase()

    def repository():
        return PostgresMarketEvidenceRepository(tenant_id="tenant-a", transaction_factory=database.transaction)

    first, second = repository(), repository()
    assert len(list_market_evidence_use_case(first, "p", "run-a")) == 1
    assert list_market_evidence_use_case(first, "p", "run-b") == []
    assert first.get("p", "run-b:ev") is None
    stale = second.get("p", "run-a:ev")
    reviewed = review_market_evidence_use_case(
        first, {"recordKey": "run-a:ev", "reviewStatus": "approved", "reviewedBy": "reviewer"},
        "2026-11-05T10:00:00Z", "p",
    )
    reopened = repository().get("p", "run-a:ev")
    assert reopened == reviewed
    assert reopened["value"] == 123
    assert reopened["reviewHistory"][0]["reviewedBy"] == "reviewer"
    stale["reviewStatus"] = "rejected"
    with pytest.raises(MarketEvidenceConflict):
        second.save("p", stale)
    assert repository().get("p", "run-a:ev") == reopened
    assert database.rows[("tenant-b", "p", "run-b:ev")]["reviewStatus"] == "pending_review"


def test_normalized_market_evidence_http_uses_verified_scope_and_actor(monkeypatch):
    import main
    import storage
    from routers import projects
    from test_normalized_project_http import Authority

    database = EvidenceDatabase()
    authority = Authority("tenant-a")
    identity = {"authenticated": True, "user": {"tenantId": "tenant-a", "userId": "verified-reviewer"}}
    for module in (main, projects, storage):
        monkeypatch.setattr(module, "storage_backend", lambda: "postgres")
    for module in (main, projects):
        monkeypatch.setattr(module, "identity_context_from_request", lambda *_args, **_kwargs: identity)
    monkeypatch.setattr(projects, "authorize_request", lambda *_args, **_kwargs: identity)
    monkeypatch.setattr(projects, "PostgresProjectRepository", lambda **_kwargs: authority)
    monkeypatch.setattr(projects, "PostgresMarketEvidenceRepository", lambda *, tenant_id: PostgresMarketEvidenceRepository(
        tenant_id=tenant_id, transaction_factory=database.transaction,
    ))
    monkeypatch.setattr(main, "normalized_activity_repository", lambda **_kwargs: None)
    monkeypatch.setattr(projects, "MARKET_EVIDENCE_DB", {})

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Normalized market evidence touched the compatibility store")

    monkeypatch.setattr(storage, "_load_json_state", forbidden)
    monkeypatch.setattr(projects, "persist_state", forbidden)
    client = TestClient(main.app)
    base = "/projects/p/market-evidence"
    response = client.get(base, params={"run_id": "run-a"})
    assert response.status_code == 200, response.text
    assert response.json()[0]["recordKey"] == "run-a:ev"
    assert client.get(base, params={"run_id": "run-b"}).json() == []
    assert client.get("/projects/foreign/market-evidence").status_code == 404

    reviewed = client.post(base + "/review", json={
        "recordKey": "run-a:ev", "reviewStatus": "approved", "reviewedBy": "spoofed",
    })
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["reviewedBy"] == "verified-reviewer"
    assert client.get(base).json()[0] == reviewed.json()
    assert client.post(base + "/review", json={"recordKey": "run-b:ev", "reviewStatus": "approved"}).status_code == 404
    assert client.post(base + "/review", json={"recordKey": "run-a:ev", "reviewStatus": "unknown"}).status_code == 400
    assert projects.MARKET_EVIDENCE_DB == {}

    def changed(*_args, **_kwargs):
        raise MarketEvidenceConflict("Changed by another reviewer")

    monkeypatch.setattr(PostgresMarketEvidenceRepository, "save", changed)
    assert client.post(base + "/review", json={"recordKey": "run-a:ev", "reviewStatus": "rejected"}).status_code == 409
