from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.legal_intelligence as legal_router


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(
        legal_router,
        "authorize_request",
        lambda request, permission: {"authorization": {"allowed": True}},
    )
    app = FastAPI()
    app.include_router(legal_router.router)
    return TestClient(app)


def test_search_router_forwards_typed_query(monkeypatch):
    captured = {}

    def fake_search(query, **kwargs):
        captured.update({"query": query, **kwargs})
        return {
            "query": query,
            "results": [],
            "authoritativeCount": 0,
            "candidateCount": 0,
        }

    monkeypatch.setattr(legal_router, "search_legal_resources", fake_search)
    response = _client(monkeypatch).get(
        "/legal/search",
        params={
            "q": "건축법",
            "resource_type": "statute",
            "as_of": "2026-07-18",
            "limit": 7,
            "public_query_acknowledged": "true",
        },
    )

    assert response.status_code == 200
    assert captured["query"] == "건축법"
    assert captured["resource_kind"].value == "statute"
    assert captured["as_of"] == date(2026, 7, 18)
    assert captured["limit"] == 7
    assert captured["public_query_acknowledged"] is True


def test_resource_router_returns_canonical_envelope(monkeypatch):
    captured = {}

    def fake_get(resource_id, **kwargs):
        captured.update({"resource_id": resource_id, **kwargs})
        return {
            "resourceId": resource_id,
            "title": "건축법",
            "sourceAuthority": "law_go_kr",
        }

    monkeypatch.setattr(
        legal_router,
        "get_legal_resource",
        fake_get,
    )
    response = _client(monkeypatch).get(
        "/legal/resources/legal-resource%3Abuilding-act",
        params={"provision_limit": 75, "provision_offset": 150},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["resource"]["title"] == "건축법"
    assert "최종 법률의견" in payload["disclaimer"]
    assert captured["provision_limit"] == 75
    assert captured["provision_offset"] == 150


def test_resource_router_never_falls_back_to_unverified_content(monkeypatch):
    monkeypatch.setattr(
        legal_router,
        "get_legal_resource",
        lambda resource_id, **kwargs: None,
    )
    response = _client(monkeypatch).get("/legal/resources/missing")

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "legal_resource_not_found"
