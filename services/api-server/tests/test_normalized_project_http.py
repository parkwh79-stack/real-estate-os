"""Exercise real HTTP routes with no project in the compatibility dictionary.

DB transport and the parser are injected; calculation, request scope and atomic
storage run unchanged. Restricted PostgreSQL/RLS is checked in the live suite.
"""
from contextlib import contextmanager
from copy import deepcopy
from fastapi import HTTPException
from fastapi.testclient import TestClient
import main
import storage
from routers import projects
from db import postgres
from db.repositories import calculation_run_repository
from db.repositories import deal_state_repository
from test_request_project_scope import Authority
from test_normalized_calculation_writes import Connection
from test_cash_flow_dependencies import request_data


def test_upload_calculate_get_without_compatibility_project(monkeypatch):
    authority = Authority('tenant-a')
    original_update = authority.update
    def update(key, data, expected_state_version=None):
        return original_update(key, {**deepcopy(authority.row), **data}, expected_state_version)
    authority.update = update
    for module in (main, projects, storage):
        monkeypatch.setattr(module, 'storage_backend', lambda: 'postgres')
    monkeypatch.setattr(projects, 'PROJECTS_DB', {})
    monkeypatch.setattr(projects, 'PostgresProjectRepository', lambda **kw: authority)
    identity = lambda *_args, **_kw: {'user': {'tenantId':'tenant-a', 'userId':'reviewer'}}
    for module in (main, projects):
        monkeypatch.setattr(module, 'identity_context_from_request', identity)
        monkeypatch.setattr(module, 'enforce_project_tenant_scope', lambda *_: None)
    monkeypatch.setattr(projects, 'authorize_request', lambda *_: None)
    monkeypatch.setattr(projects, 'ai_context_from_request', lambda *_args, **_kw: {})
    monkeypatch.setattr(main, 'record_user_activity_log', lambda row: row)
    class Activity:
        def append_api_request(self, event):
            return event['eventId']
    monkeypatch.setattr(main, 'normalized_activity_repository', lambda **kw: Activity())
    async def parse(pid, file, context):
        assert await file.read() == b'uploaded evidence'
        return {'source_documents':[{'archiveId':'source-1','documentType':'business_plan'}],
                'feasibility_input':{'total_revenue':200, 'total_cost':80}}
    monkeypatch.setattr(projects, 'parse_uploaded_business_plan', parse)
    monkeypatch.setattr(projects, '_maybe_auto_reanalyze_on_upload', lambda *_: None)
    monkeypatch.setattr(storage, '_load_json_state', lambda: (_ for _ in ()).throw(AssertionError('JSON authority used')))
    connections=[]
    @contextmanager
    def transaction(tenant):
        assert tenant == 'tenant-a'
        conn=Connection()
        connections.append(conn)
        yield conn
    repository = calculation_run_repository.PostgresCalculationRunRepository
    monkeypatch.setattr(calculation_run_repository, 'PostgresCalculationRunRepository',
        lambda **kw: repository(**{**kw, 'transaction_factory':transaction}))
    client=TestClient(main.app)
    uploaded=client.post('/projects/p/upload', files={'file':('plan.txt',b'uploaded evidence','text/plain')})
    assert uploaded.status_code == 200, uploaded.text
    calculated=client.post('/projects/p/cash-flow/run', json=request_data())
    assert calculated.status_code == 200, calculated.text
    assert calculated.json()['dependentExecutionStatus'] == 'completed'
    assert calculated.json()['summary']['totalRevenue'] == 200
    reloaded=client.get('/projects/p')
    assert reloaded.status_code == 200
    assert reloaded.json()['source_documents'][0]['archiveId'] == 'source-1'
    assert reloaded.json()['_meta']['stateVersion'] == 2
    assert len(connections) == 5
    assert all(conn.writes[:2] == ['run','outbox'] for conn in connections)
    assert projects.PROJECTS_DB == {}


def test_workspace_context_reads_normalized_data_without_compatibility_state(monkeypatch):
    authority = Authority("tenant-a")
    authority.row.update({
        "project": {"id": "p", "name": "Saved project"},
        "source_documents": [{"archiveId": "doc-1", "documentType": "business_plan"}],
        "landLedger": {"parcels": [{"pnu": "1111010100100010000"}]},
    })
    original_project = deepcopy(authority.row)
    for module in (main, projects, storage):
        monkeypatch.setattr(module, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(projects, "PostgresProjectRepository", lambda **kw: authority)
    identity = lambda *_args, **_kw: {"user": {"tenantId": "tenant-a", "userId": "reviewer"}}
    for module in (main, projects):
        monkeypatch.setattr(module, "identity_context_from_request", identity)
        monkeypatch.setattr(module, "enforce_project_tenant_scope", lambda *_: None)

    def forbid_legacy(*_args, **_kw):
        raise AssertionError("A normalized workspace read used legacy state")

    monkeypatch.setattr(storage, "_load_json_state", forbid_legacy)
    monkeypatch.setattr(projects, "_persist_shared_deal_state_graph", forbid_legacy)
    monkeypatch.setattr(projects, "record_user_activity_log", forbid_legacy)
    calls = []

    def authorize(_request, permission):
        assert permission == "project:read"
        if _request.headers.get("x-test-deny") == "true":
            raise HTTPException(status_code=403, detail="denied")

    class DealState:
        def __init__(self, *, tenant_id, **_kw):
            assert tenant_id == "tenant-a"

        def list_records(self, project_id, *, record_type, **_kw):
            calls.append("facts")
            assert (project_id, record_type) == ("p", "parsed_fact")
            return [{"recordId": "fact-1", "recordType": "parsed_fact", "domain": "land"}]

    def list_artifacts(table, *, where, limit):
        calls.append("artifacts")
        assert (table, where, limit) == ("chief_pod_artifacts", {"project_id": "p"}, 100)
        return []

    class Activity:
        def append_api_request(self, event):
            return event["eventId"]

    monkeypatch.setattr(projects, "authorize_request", authorize)
    monkeypatch.setattr(deal_state_repository, "PostgresDealStateRepository", DealState)
    monkeypatch.setattr(postgres, "list_typed_jsonb_records", list_artifacts)
    monkeypatch.setattr(main, "normalized_activity_repository", lambda **_kw: Activity())
    client = TestClient(main.app)
    route = "/projects/p/workspace/context?page=input_package"
    assert client.get(route, headers={"x-test-deny": "true"}).status_code == 403
    assert client.get("/projects/outside/workspace/context").status_code == 404
    assert calls == []

    response = client.get(route)
    assert response.status_code == 200, response.text
    context = response.json()
    assert context["contextCompleteness"] == "partial"
    assert context["projectMemory"]["projectName"] == "Saved project"
    assert context["projectMemory"]["sourceDocumentCount"] == 1
    assert context["pageData"]["sourceDocuments"][0]["archiveId"] == "doc-1"
    assert context["pageData"]["parsedFactCount"] == 1
    assert context["pageData"]["landLedgerSummary"]["pnuCount"] == 1
    assert "landEvidenceSummary" not in context["pageData"]
    assert "missingInputRegister" not in context["pageData"]
    assert "dealStateGraph" not in context
    assert calls == ["facts", "artifacts"]
    assert authority.row == original_project

    monkeypatch.setattr(projects, "PostgresLegalReviewProjectionRepository", forbid_legacy)
    monkeypatch.setattr(projects, "list_land_evidence_records", lambda project_id: [])
    land = client.get("/projects/p/workspace/context?page=land").json()
    assert land["pageData"]["landLedger"]["parcels"][0]["pnu"] == "1111010100100010000"
    assert "landLegalReview" not in land["pageData"]
    assert land["pageData"]["landEvidenceRecords"] == []
    assert "landEvidence" not in land["unavailableSections"]

    def unavailable(_project_id):
        raise OSError("unavailable")

    monkeypatch.setattr(projects, "list_land_evidence_records", unavailable)
    degraded = client.get("/projects/p/workspace/context?page=land")
    assert degraded.status_code == 200
    assert degraded.json()["pageData"]["landLedger"] == land["pageData"]["landLedger"]
    assert "landEvidence" in degraded.json()["unavailableSections"]


def test_normalized_map_reads_parcels_without_legacy_state_or_lookup_writes(monkeypatch):
    authority = Authority("tenant-a")
    first, second = "1111010100100010000", "1111010100100020000"
    point = {"type": "Point", "coordinates": [126.97, 37.56]}
    boundary = {"type": "Polygon", "coordinates": [[
        [126.97, 37.56], [126.98, 37.56], [126.97, 37.57], [126.97, 37.56],
    ]]}
    authority.row["land_ledger"] = {"parcels": [
        {"lotNumber": "1", "pnu": first},
        {"lotNumber": "2", "pnu": second, "geometry": point},
    ]}
    saved = deepcopy(authority.row)
    for module in (main, projects, storage):
        monkeypatch.setattr(module, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(projects, "PROJECTS_DB", {})
    monkeypatch.setattr(projects, "PostgresProjectRepository", lambda **_: authority)
    monkeypatch.setattr(main, "enforce_project_tenant_scope", lambda *_: None)
    identity = {"authenticated": True, "user": {"tenantId": "tenant-a", "userId": "reviewer"}}
    for module in (main, projects):
        monkeypatch.setattr(module, "identity_context_from_request", lambda *_args, **_kwargs: identity)

    class DealState:
        def __init__(self, *, tenant_id, **_kw):
            assert tenant_id == "tenant-a"

        def list_records(self, project_id, *, record_type, **_kw):
            assert (project_id, record_type) == ("p", "land_evidence")
            return []

    class Activity:
        def append_api_request(self, event):
            return event["eventId"]

    def forbid_legacy(*_args, **_kw):
        raise AssertionError("Map reads must not touch legacy state or write project data")

    monkeypatch.setattr(storage, "_load_json_state", forbid_legacy)
    monkeypatch.setattr(projects, "attach_active_restructuring_cures", forbid_legacy)
    monkeypatch.setattr(authority, "update", forbid_legacy)
    monkeypatch.setattr(deal_state_repository, "PostgresDealStateRepository", DealState)
    monkeypatch.setattr(main, "normalized_activity_repository", lambda **_kw: Activity())
    monkeypatch.setattr(projects, "vworld_configured", lambda: False)
    looked_up = []

    def fetch(pnu, *, timeout):
        assert 0 < timeout <= 4.0
        looked_up.append(pnu)
        return boundary, {"status": "resolved"}

    monkeypatch.setattr(projects, "fetch_parcel_geometry_by_pnu", fetch)
    client = TestClient(main.app)
    route = "/projects/p/map?resolve_geometry=true&geometry_limit=1"
    response = client.get(route)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["geometryResolution"]["status"] == "not_configured"
    assert payload["summary"]["boundaryParcelCount"] == 0
    assert payload["summary"]["pointParcelCount"] == 1
    assert looked_up == []

    monkeypatch.setattr(projects, "vworld_configured", lambda: True)
    assert client.get(f"{route}&focus_pnu=1111010100100030000").json()["geometryResolution"]["attemptedCount"] == 0
    assert client.get("/projects/outside/map").status_code == 404
    response = client.get(f"{route}&focus_pnu={second}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert looked_up == [second]
    assert payload["geometryResolution"]["status"] == "resolved"
    assert payload["summary"]["boundaryParcelCount"] == 1
    assert payload["summary"]["pointParcelCount"] == 0
    assert [row["geometry"] for row in payload["parcelGeoJson"]["features"]] == [None, boundary]
    assert authority.row == saved


def test_normalized_parcel_resolution_and_legal_run_keep_same_tenant_evidence(monkeypatch):
    from integrations import vworld
    from use_cases import legal_pod

    authority = Authority("tenant-a")
    authority.row.update({
        "project": {"id": "p", "type": "officetel"},
        "land": {"address": "경기도 용인시 기흥구 상갈동 13", "area_sqm": 1000},
        "land_ledger": {"parcels": [{"lotNumber": "13", "areaSqm": 1000}]},
    })
    original_update = authority.update

    def update(key, data, expected_state_version=None):
        return original_update(key, {**deepcopy(authority.row), **data}, expected_state_version)

    authority.update = update
    for module in (main, projects, storage, legal_pod):
        monkeypatch.setattr(module, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(projects, "PROJECTS_DB", {})
    monkeypatch.setattr(projects, "PostgresProjectRepository", lambda **_kwargs: authority)
    identity = {"authenticated": True, "user": {"tenantId": "tenant-a", "userId": "reviewer"}}
    for module in (main, projects):
        monkeypatch.setattr(module, "identity_context_from_request", lambda *_args, **_kwargs: identity)
    monkeypatch.setattr(projects, "authorize_request", lambda *_args: identity)
    monkeypatch.setattr(projects, "_stored_chief_evidence_options", lambda _pid, _data, options: options)
    monkeypatch.setattr(projects, "record_sensitive_project_event", lambda *_args, **_kwargs: None)

    def forbid_compatibility(*_args, **_kwargs):
        raise AssertionError("Normalized Legal Pod touched the compatibility store")

    monkeypatch.setattr(storage, "_load_json_state", forbid_compatibility)
    monkeypatch.setattr(projects, "upsert_legal_review_record", forbid_compatibility)
    monkeypatch.setattr(legal_pod, "upsert_legal_review_record", forbid_compatibility)
    monkeypatch.setattr(legal_pod, "upsert_permit_review_record", forbid_compatibility)
    monkeypatch.setattr(projects, "postgres_configured", lambda: True)
    monkeypatch.setattr(projects, "PostgresLegalTenantOverlayRepository", lambda **_kwargs: object())
    monkeypatch.setattr(legal_pod, "resolve_project_legal_context", lambda *_args, **_kwargs: {
        "lawContext": {"connected": False, "references": []},
        "legalIntelligence": {"evidenceStatus": "unverified", "references": []},
    })

    pnu = "4146310300100130000"
    monkeypatch.setattr(vworld, "resolve_legal_dong_code", lambda _addr: pnu[:10])
    monkeypatch.setattr(vworld, "fetch_parcel_by_pnu", lambda value: (
        {"pnu": value, "jibun": "13", "jiga": "1500000"}, {"status": "resolved"},
    ))
    deal_rows = {}

    class DealState:
        def __init__(self, *, tenant_id, **_kwargs):
            assert tenant_id == "tenant-a"

        def list_records(self, project_id, *, record_type=None, **_kwargs):
            assert project_id == "p"
            return [row for row in deal_rows.values() if record_type in (None, row["recordType"])]

        def upsert_records(self, project_id, rows):
            assert project_id == "p"
            for row in rows:
                assert row["tenantId"] == "tenant-a"
                deal_rows[row["recordId"]] = row
            return rows

    class Activity:
        def append_api_request(self, event):
            return event["eventId"]

    captured = {}

    class Projection:
        def __init__(self, *, tenant_id, project_id):
            assert (tenant_id, project_id) == ("tenant-a", "p")

        def append_legal_pod_run(self, *, legal, permit, actor_id):
            assert actor_id == "reviewer"
            assert permit.analysis_only
            captured["legal"] = legal
            return {
                "legalReview": {"reviewVersionId": "legal-1", "reviewVersion": 1, "projectionSha256": "sha256:" + "a" * 64},
                "permitReview": {"reviewVersionId": "permit-1", "reviewVersion": 1, "projectionSha256": "sha256:" + "b" * 64},
            }

    monkeypatch.setattr(deal_state_repository, "PostgresDealStateRepository", DealState)
    monkeypatch.setattr(main, "normalized_activity_repository", lambda **_kwargs: Activity())
    monkeypatch.setattr(projects, "PostgresLegalReviewProjectionRepository", Projection)
    client = TestClient(main.app)

    resolved = client.post("/projects/p/land/parcels/resolve")
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["results"][0]["pnu"] == pnu
    assert authority.row["land_ledger"]["parcels"][0]["pnu"] == pnu
    evidence = deal_rows[f"land_evidence:pnu_resolution:{pnu}"]["landEvidenceRecord"]
    assert evidence["verificationStatus"] == "resolved"

    stored_inputs = deepcopy(authority.row)
    before_preview = deepcopy(deal_rows)
    preview = client.post("/projects/p/legal-pod/preview")
    assert preview.status_code == 200, preview.text
    assert "no-store" in preview.headers["Cache-Control"]
    preview_body = preview.json()
    assert preview_body["previewOnly"] is True
    assert preview_body["legalReview"]["developabilityAnalysis"]["heightReview"]["parcelReviews"][0]["pnu"] == pnu
    assert not captured
    assert deal_rows == before_preview
    assert authority.row == stored_inputs

    summary_url = "/projects/p/legal-pod/preview-summary"
    summary_request = {"sections": ["permit"], "expectedPreviewHash": "a" * 64}
    assert client.post(summary_url, json=summary_request).status_code == 409
    assert deal_rows == before_preview
    summary_request["expectedPreviewHash"] = preview_body["previewHash"]
    saved = client.post(summary_url, json=summary_request)
    assert saved.status_code == 200, saved.text
    row = saved.json()
    assert row["reportEligible"] is False
    assert set(row["summary"]) == {"permit"}
    assert row["summary"]["permit"]["useClassification"] == "officetel"
    assert deal_rows == before_preview
    assert authority.row["landLegalPreviewSummaries"] == [row]
    stored_summary_state = deepcopy(authority.row)
    assert client.post(summary_url, json=summary_request).json()["recordId"] == row["recordId"]
    assert authority.row == stored_summary_state
    assert client.get(summary_url).json()["items"][0]["recordId"] == row["recordId"]
    assert authority.row["land_ledger"] == stored_inputs["land_ledger"]
    assert not captured

    response = client.post("/projects/p/legal-pod/run", json={"autoEnrichLand": False})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["normalizedProjectionPersistence"]["persisted"] is True
    parcels = result["legalReview"]["parcelLegalReviews"]
    assert parcels[0]["pnu"] == pnu
    assert parcels[0]["evidenceIds"] == [f"pnu_resolution:{pnu}", f"official_land_price:{pnu}"]
    assert captured["legal"].display["parcelLegalReviews"] == parcels
    assert authority.row["legalReview"]["reviewId"] == result["legalReview"]["reviewId"]
    assert projects.PROJECTS_DB == {}
