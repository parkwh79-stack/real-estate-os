"""Hosted glossary reads use normalized rows without seeding compatibility state."""

from contextlib import contextmanager
from types import SimpleNamespace
import unicodedata

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from db.repositories.glossary_repository import GlossaryAuthorityUnavailable, PostgresGlossaryRepository
from routers import glossary
from use_cases import glossary as use_case


@pytest.fixture
def hosted_glossary(monkeypatch):
    observed = []
    rows = {
        "terms": [{"termId": "revenue", "preferredTerm": "총분양수입", "displayNameKo": "총분양수입",
                   "canonicalField": "feasibility_input.total_revenue", "reviewStatus": "approved",
                   "category": "feasibility", "aliases": ["분양수입"]}],
        "candidates": [],
    }

    class Connection:
        def execute(self, sql, parameters):
            observed.append((sql, parameters))
            assert sql.startswith("SELECT "), "A glossary read must not write or seed"
            kind = "candidates" if "glossary_term_candidates" in sql else "terms"
            result = rows[kind]
            if isinstance(result, Exception):
                raise result
            return SimpleNamespace(fetchall=lambda: result if kind == "candidates" else [(item,) for item in result])

    @contextmanager
    def transaction(tenant_id):
        assert tenant_id == "tenant-a"
        yield Connection()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Compatibility glossary state was accessed")

    monkeypatch.setattr(glossary, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(glossary, "current_project_scope", lambda: SimpleNamespace(
        tenant_id="tenant-a", get=lambda project_id: {"id": project_id} if project_id == "p" else None,
    ))
    monkeypatch.setattr(glossary, "PostgresGlossaryRepository", lambda **kwargs: PostgresGlossaryRepository(
        **kwargs, transaction_factory=transaction,
    ))
    for name in ("ensure_seed_glossary_terms", "list_glossary_term_records",
                 "list_glossary_term_candidate_records", "get_glossary_term_record"):
        monkeypatch.setattr(use_case, name, forbidden)
    monkeypatch.setattr(glossary, "record_user_activity_log", forbidden)

    def authorize(request, permission):
        if request.headers.get("x-test-deny"):
            raise HTTPException(status_code=403, detail="denied")
        assert permission in {"project:read", "project:write"}
        return {"authenticated": True, "user": {"tenantId": "tenant-a", "userId": "member"}}

    monkeypatch.setattr(glossary, "authorize_request", authorize)
    app = FastAPI()
    app.include_router(glossary.router)
    return TestClient(app), rows, observed


def test_hosted_term_reads_resolve_and_field_map_use_normalized_glossary(hosted_glossary):
    client, _rows, observed = hosted_glossary
    surface = unicodedata.normalize("NFD", "총분양수입")
    response = client.get("/glossary/terms", params={
        "q": surface, "canonical_field": "feasibility_input.total_revenue", "review_status": "approved",
    })
    assert response.status_code == 200, response.text
    assert response.json()["terms"][0]["termId"] == "revenue"
    assert observed[0][1] == ["feasibility_input.total_revenue", "approved"]
    assert all("realytics.glossary_terms" in sql for sql, _ in observed)
    detail = client.get("/glossary/terms/revenue")
    assert detail.status_code == 200, detail.text
    assert detail.json()["term"]["termId"] == "revenue"
    resolved = client.get("/glossary/resolve", params={"term": "분양수입"})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["matched"] is True
    mapped = client.get("/glossary/canonical-field-map")
    assert mapped.status_code == 200, mapped.text
    assert mapped.json()["termCount"] == 1
    assert "feasibility_input.total_revenue" in mapped.json()["fields"]


def test_hosted_empty_terms_are_confirmed_empty_without_get_seeding(hosted_glossary):
    client, rows, observed = hosted_glossary
    rows["terms"] = []
    response = client.get("/glossary/terms")
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 0
    assert response.json()["terms"] == []
    assert len(observed) == 1


def test_hosted_candidates_require_tenant_and_project_scope(hosted_glossary):
    client, rows, observed = hosted_glossary
    rows["candidates"] = [("tenant-a", "p", {
        "candidateId": "candidate-a", "projectId": "p", "surface": "검토할 표현",
        "status": "pending_review", "sourceField": "business_plan",
    })]
    response = client.get("/glossary/candidates", params={"project_id": "p", "status": "pending_review", "q": "표현"})
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    assert response.json()["candidates"][0]["tenantId"] == "tenant-a"
    assert "tenant_id = %s" in observed[0][0]
    assert "project_id = %s" in observed[0][0]
    assert observed[0][1] == ["tenant-a", "pending_review", "p"]
    assert client.get("/glossary/candidates", params={"project_id": "outside"}).status_code == 404
    assert len(observed) == 1


def test_missing_candidate_select_is_unavailable_instead_of_fake_empty(hosted_glossary):
    client, rows, _observed = hosted_glossary
    rows["candidates"] = PermissionError("secret database diagnostic")
    response = client.get("/glossary/candidates")
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["error"] == "glossary_read_authority_unavailable"
    assert response.json()["detail"]["authority"] == "candidates"
    assert "secret" not in response.text
    assert "candidates" not in response.json()


@pytest.mark.parametrize("tenant,payload", [
    ("tenant-b", {}),
    ("tenant-a", {"tenantId": "tenant-b"}),
    ("tenant-a", {"projectId": "outside"}),
])
def test_candidate_response_cannot_cross_scope(hosted_glossary, tenant, payload):
    client, rows, _observed = hosted_glossary
    rows["candidates"] = [(tenant, "p", payload)]
    response = client.get("/glossary/candidates", params={"project_id": "p"})
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["authority"] == "candidate_scope"


def test_hosted_writes_and_reads_preserve_authorization(hosted_glossary):
    client, _rows, observed = hosted_glossary
    assert client.get("/glossary/terms", headers={"x-test-deny": "1"}).status_code == 403
    assert client.get("/glossary/candidates", headers={"x-test-deny": "1"}).status_code == 403
    assert client.post("/glossary/terms", json={}, headers={"x-test-deny": "1"}).status_code == 403
    for path in ("/terms", "/normalize", "/standard-pack/import", "/candidates/capture", "/candidates/c/review"):
        response = client.post("/glossary" + path, json={})
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["error"] == "glossary_write_authority_unavailable"
    assert observed == []


def test_missing_request_scope_does_not_fallback_to_legacy(hosted_glossary, monkeypatch):
    client, _rows, observed = hosted_glossary
    monkeypatch.setattr(glossary, "current_project_scope", lambda: None)
    response = client.get("/glossary/terms")
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["authority"] == "request_scope"
    assert observed == []


def test_repository_requires_verified_tenant():
    with pytest.raises(ValueError):
        PostgresGlossaryRepository(tenant_id=" ")