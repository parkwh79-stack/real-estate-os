from copy import deepcopy

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from routers import projects
from schemas.orchestration import AgentHandoffEnvelope
from use_cases import chief_pod_preflight as preflight


PNU = "1111010100100010000"


def project():
    return {
        "project": {"id": "deal", "name": "테스트", "type": "apartment"},
        "source_documents": [{"archiveId": "archive-1", "documentType": "business_plan"}],
        "landLedger": {"parcels": [{"pnu": PNU, "lotNumber": "1"}]},
        "landEvidence": {"records": [{
            "pnu": PNU, "evidenceType": "land_use_planning", "sourcePnuVerified": True,
            "zoning": "일반주거지역", "evidenceId": "land-proof",
        }]},
        "product_mix": [{"name": "공동주택"}],
        "feasibility_input": {"total_revenue": 250, "total_cost": 125},
        "schedule": {"construction_months": 12},
        "pf_structure": [{"amount": 30, "interest_rate": 0}],
    }


def by_pod(result):
    return {row["podKey"]: row for row in result["pods"]}


def by_check(row):
    return {check["key"]: check for check in row["checks"]}


def test_six_pods_share_evidence_and_keep_approval_requirements_local(monkeypatch):
    project_data = project()
    before = deepcopy(project_data)
    mapped = []

    def prepare(working):
        mapped.append(working)
        return {"requestType": "cash_flow_run_request_v1"}

    monkeypatch.setattr(preflight, "build_cash_flow_request_use_case", prepare)
    result = preflight.run_chief_pod_preflight("deal", project_data, workflow_track="pf", allow_public_lookups=False)
    pods = by_pod(result)
    assert list(pods) == ["land_legal", "market_product", "cost_schedule", "feasibility_cashflow", "pf_credit", "public_policy"]
    assert result["analysisOnly"] is True
    assert len(mapped) == 1
    assert pods["feasibility_cashflow"]["status"] == "checked"
    assert pods["pf_credit"]["status"] == "checked"
    assert pods["land_legal"]["status"] == "partial"
    assert by_check(pods["land_legal"])["zoning"]["sourceLocators"] == [{"evidenceId": "land-proof"}]
    assert by_check(pods["public_policy"])["zoning"]["sourceLocators"] == [{"evidenceId": "land-proof"}]
    assert by_check(pods["pf_credit"])["financing"]["status"] == "available"
    assert any(row["itemId"] == "registry_evidence_missing" for row in result["materialRequests"])
    handoffs = [AgentHandoffEnvelope.model_validate(row) for row in result["handoffs"]]
    cashflow_to_credit = next(row for row in handoffs if row.payload["fromPod"] == "feasibility_cashflow" and row.payload["toPod"] == "pf_credit")
    assert cashflow_to_credit.handoff_id in pods["pf_credit"]["receivedHandoffIds"]
    assert all(row.payload["analysisOnly"] for row in handoffs)
    assert project_data == before


@pytest.mark.parametrize("provider_pnu", [PNU, "1111010100100020000"])
def test_only_exact_parcel_response_enables_bounded_market_lookup(monkeypatch, provider_pnu):
    data = project()
    data.pop("landEvidence")
    calls = []
    monkeypatch.setattr(preflight, "vworld_configured", lambda: True)

    def parcel(pnu, *, timeout):
        assert pnu == PNU and timeout == 2.0
        return {"pnu": provider_pnu}, {"status": "resolved"}

    class Connector:
        def configured(self):
            return True

    def fetch(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs["lawd_cd"] == "11110"
        assert kwargs["asset_type"] == "apartment"
        assert len(kwargs["deal_yms"]) == 1 and kwargs["max_records"] == 12
        return [{"officialTradeId": "sample"}], {"status": "ok"}

    monkeypatch.setattr(preflight, "fetch_parcel_by_pnu", parcel)
    monkeypatch.setattr(preflight, "MolitRtmsConnector", Connector)
    monkeypatch.setattr(preflight, "fetch_project_market_comparables", fetch)
    result = preflight.run_chief_pod_preflight("deal", data)
    checks = by_check(by_pod(result)["market_product"])
    requested = {row["itemId"] for row in result["materialRequests"]}
    if provider_pnu == PNU:
        assert len(calls) == 1
        assert checks["market"]["basis"] == "verified_public"
        assert "market_evidence_missing" not in requested
    else:
        assert not calls
        assert checks["market"]["status"] == "missing"
        assert "market_evidence_missing" in requested


def test_existing_parser_candidate_requests_review_and_does_not_invent_a_cost():
    data = project()
    data["feasibility_input"].pop("total_cost")
    data["pf_structure"] = [{"amount": 50}]
    data["parsedFacts"] = [
        {"canonicalField": "feasibility.totalCost", "sourceDocument": {"archiveId": "archive-1"},
         "sourceLocator": {"sheetName": "원장", "cell": "B10"}, "normalizedValue": 200},
        {"canonicalField": "feasibility.totalCost", "sourceDocument": {"archiveId": "another-project"},
         "sourceLocator": {"sheetName": "외부"}, "normalizedValue": 200},
    ]
    result = preflight.run_chief_pod_preflight("deal", data, allow_public_lookups=False)
    requests = {row["itemId"]: row for row in result["materialRequests"]}
    cost = requests["cost_assumption_missing"]
    assert cost["action"] == "review_document"
    assert cost["sourceLocators"] == [{
        "sourceType": "uploaded_document", "archiveId": "archive-1", "documentName": "보관 원본",
        "sheetName": "원장", "cell": "B10",
    }]
    assert cost["purpose"] and cost["podKeys"] == ["cost_schedule", "feasibility_cashflow"]
    assert requests["pf_structure_missing"]["action"] == "provide_material"
    assert by_check(by_pod(result)["cost_schedule"])["cost"]["status"] == "missing"
    assert "total_cost" not in data["feasibility_input"]


@pytest.mark.parametrize("bad_row", ["잘못된 조건", {"amount": 15, "interest_rate": "미정", "rate": 0.04}])
def test_malformed_financing_rows_require_review(bad_row):
    data = project()
    data["pf_structure"].append(bad_row)
    result = preflight.run_chief_pod_preflight("deal", data, allow_public_lookups=False)
    assert by_check(by_pod(result)["pf_credit"])["financing"]["status"] == "needs_review"
    assert "pf_structure_missing" in {row["itemId"] for row in result["materialRequests"]}

def test_no_common_source_requests_one_document_without_skipping_any_pod():
    result = preflight.run_chief_pod_preflight("deal", {}, allow_public_lookups=False)
    assert len(result["pods"]) == 6
    assert [row["itemId"] for row in result["materialRequests"]] == ["source_documents_missing"]
    assert len(result["materialRequests"][0]["podKeys"]) == 6


def test_route_authorizes_scoped_latest_inputs_and_ignores_client_overlay(monkeypatch):
    app = FastAPI()
    app.include_router(projects.router)
    authority = project()
    calls = []

    def authorize(request, permission):
        assert permission == "project:analyze"
        if request.headers.get("x-test-allow") != "yes":
            raise HTTPException(status_code=403, detail="Denied")
        calls.append("authorized")

    def fetch(project_id, request):
        assert project_id == "deal"
        return authority

    def scope(request, value):
        assert value is authority
        if request.headers.get("x-test-other-tenant"):
            raise HTTPException(status_code=403, detail="Denied")
        calls.append("scoped")

    def hydrate(project_id, overlay, *, authoritative_source, include_active_cures):
        assert project_id == "deal" and not overlay and not include_active_cures
        assert authoritative_source is authority
        calls.append("hydrated")
        return authority

    def forbid(*args, **kwargs):
        raise AssertionError("Preview must not persist")

    monkeypatch.setattr(projects, "authorize_request", authorize)
    monkeypatch.setattr(projects, "authoritative_project", fetch)
    monkeypatch.setattr(projects, "enforce_project_tenant_scope", scope)
    monkeypatch.setattr(projects, "_hydrated_project_source", hydrate)
    monkeypatch.setattr(projects, "_persist_shared_deal_state_graph", forbid)
    monkeypatch.setattr(projects, "record_sensitive_project_event", forbid)
    monkeypatch.setattr(preflight, "vworld_configured", lambda: False)

    class Connector:
        def configured(self):
            return False

    monkeypatch.setattr(preflight, "MolitRtmsConnector", Connector)
    client = TestClient(app)
    route = "/projects/deal/chief-orchestration/preflight"
    assert client.post(route, json={}).status_code == 403
    assert client.post(route, headers={"x-test-allow": "yes", "x-test-other-tenant": "yes"}, json={}).status_code == 403
    response = client.post(route, headers={"x-test-allow": "yes"}, json={
        "workflowTrack": "pf", "project_data": {"source_documents": [], "feasibility_input": {"total_cost": 999}},
    })
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["workflowTrack"] == "pf"
    assert not any(item["itemId"] == "source_documents_missing" for item in response.json()["materialRequests"])
    assert calls[-3:] == ["authorized", "scoped", "hydrated"]
