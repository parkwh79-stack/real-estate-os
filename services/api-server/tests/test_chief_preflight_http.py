from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from routers import projects
from use_cases import chief_preflight_receipts as receipts
from use_cases import chief_preflight_review as review


@pytest.fixture
def boundary(monkeypatch):
    project = {
        "project": {"id": "p", "type": "apartment"},
        "source_documents": [{"archiveId": "source-one"}],
        "feasibility_input": {"total_cost": 100, "total_revenue": 200},
    }
    state = {"actor": "reviewer", "tenant": "tenant-a", "allowed": True, "calls": [], "receipts": {}, "changeDuringRun": False}
    app = FastAPI()
    app.include_router(projects.router)

    def authorize(_request, permission):
        assert permission in {"project:read", "project:analyze"}
        if not state["allowed"]:
            raise HTTPException(status_code=403, detail="Denied")
        return {"authenticated": True, "user": {"userId": state["actor"], "tenantId": state["tenant"]}}

    def fetch(project_id, _request):
        if project_id != "p":
            raise HTTPException(status_code=404, detail="Missing project")
        return deepcopy(project)

    def scoped(_request, _project):
        if state["tenant"] != "tenant-a":
            raise HTTPException(status_code=403, detail="Other tenant")

    def hydrate(_pid, overlay, *, authoritative_source, include_active_cures):
        assert not overlay and not include_active_cures
        return deepcopy(authoritative_source)

    def run(pid, source, *, workflow_track, previous_state, retry_failed):
        state["calls"].append((deepcopy(previous_state), retry_failed))
        revision = review.source_revision(source, workflow_track)
        resume = {
            "runnerVersion": review.RUNNER_VERSION, "sourceRevision": revision,
            "private": {"value": source["feasibility_input"]["total_cost"]},
        }
        if state["changeDuringRun"]:
            project["feasibility_input"]["total_cost"] += 1
        return {
            "schemaVersion": "realytics-chief-pod-preflight-v1", "projectId": pid,
            "workflowTrack": workflow_track, "inputRevision": revision,
            "analysisOnly": True, "pods": [], "materialRequests": [],
            "publicLookups": [], "handoffs": [], "resumeState": resume,
            "reviewLoop": {"status": "converged", "rounds": 1, "pendingPodKeys": [],
                           "executedCount": 1, "reusedCount": int(previous_state is not None)},
        }

    def insert(table, values, payload):
        assert table == "agent_handoffs"
        assert values["tenant_id"] == payload["tenantId"] == state["tenant"]
        assert values["project_id"] == payload["projectId"] == "p"
        assert payload["handoffType"] == "chief_preflight_receipt"
        key = values["handoff_id"]
        if key in state["receipts"]:
            return False
        state["receipts"][key] = deepcopy(payload)
        return True

    def lookup(table, where):
        assert table == "agent_handoffs"
        assert where["project_id"] == "p"
        return deepcopy(state["receipts"].get(where["handoff_id"]))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Preflight saved business values or used a legacy authority")

    monkeypatch.setattr(projects, "authorize_request", authorize)
    monkeypatch.setattr(projects, "authoritative_project", fetch)
    monkeypatch.setattr(projects, "enforce_project_tenant_scope", scoped)
    monkeypatch.setattr(projects, "_hydrated_project_source", hydrate)
    monkeypatch.setattr(projects, "_persist_shared_deal_state_graph", forbidden)
    monkeypatch.setattr(projects, "persist_project", forbidden)
    monkeypatch.setattr(projects, "upsert_agent_handoff_record", forbidden)
    monkeypatch.setattr(receipts, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(receipts.postgres, "insert_typed_jsonb_record", insert)
    monkeypatch.setattr(receipts.postgres, "get_typed_jsonb_record", lookup)
    monkeypatch.setattr(review, "run_chief_preflight_review", run)
    return TestClient(app), state, project


URL = "/projects/p/chief-orchestration/preflight"


def previous(response):
    return {key: response.json()[key] for key in ("receiptId", "resumeState")}


def test_hash_receipt_reuses_verified_browser_state_after_input_changes(boundary):
    client, state, project = boundary
    original = deepcopy(project)
    first = client.post(URL, json={"workflowTrack": "pf", "project_data": {"feasibility_input": {"total_cost": 999}}})
    assert first.status_code == 200, first.text
    assert first.headers["Cache-Control"] == "no-store"
    assert first.json()["resumeState"]["private"]["value"] == 100
    assert project == original
    record = state["receipts"][first.json()["receiptId"]]
    assert set(record["payload"]) == {
        "projectId", "tenantId", "actorId", "workflowTrack", "engineVersion",
        "stateHash", "inputRevision", "createdAt", "expiresAt",
    }
    assert "private" not in json.dumps(record)
    assert "feasibility_input" not in json.dumps(record)
    assert record["payload"]["stateHash"] == receipts.stable_digest(first.json()["resumeState"])
    resumed = client.post(URL, json={"workflowTrack": "pf", "previousReview": previous(first), "retryFailed": True})
    assert resumed.status_code == 200, resumed.text
    assert state["calls"][-1] == (first.json()["resumeState"], True)
    assert resumed.json()["receiptId"] != first.json()["receiptId"]
    project["feasibility_input"]["total_cost"] = 150
    changed = client.post(URL, json={"workflowTrack": "pf", "previousReview": previous(first)})
    assert changed.status_code == 200, changed.text
    assert changed.json()["inputRevision"] != first.json()["inputRevision"]
    assert state["calls"][-1][0] == first.json()["resumeState"]


def test_receipt_rejects_forgery_actor_track_and_expiration_before_executing(boundary):
    client, state, _project = boundary
    first = client.post(URL, json={"workflowTrack": "pf"})
    assert first.status_code == 200
    wrapper = previous(first)
    forged = deepcopy(wrapper)
    forged["resumeState"]["private"]["value"] = 999
    assert client.post(URL, json={"workflowTrack": "pf", "previousReview": forged}).status_code == 409
    state["actor"] = "other-reviewer"
    assert client.post(URL, json={"workflowTrack": "pf", "previousReview": wrapper}).status_code == 409
    state["actor"] = "reviewer"
    assert client.post(URL, json={"workflowTrack": "development", "previousReview": wrapper}).status_code == 409
    record = state["receipts"][wrapper["receiptId"]]
    record["payload"]["createdAt"] = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    record["payload"]["expiresAt"] = (datetime.fromisoformat(record["payload"]["createdAt"]) + receipts.RECEIPT_LIFETIME).isoformat()
    assert client.post(URL, json={"workflowTrack": "pf", "previousReview": wrapper}).status_code == 409
    assert len(state["calls"]) == 1


def test_receipt_identity_and_state_bounds_fail_closed(boundary):
    client, state, _project = boundary
    first = client.post(URL, json={})
    assert first.status_code == 200
    wrapper = previous(first)
    state["tenant"] = "tenant-b"
    assert client.post(URL, json={"previousReview": wrapper}).status_code == 403
    state["tenant"] = "tenant-a"
    record = state["receipts"][wrapper["receiptId"]]
    record["tenantId"] = "tenant-b"
    assert client.post(URL, json={"previousReview": wrapper}).status_code == 409
    oversized = deepcopy(wrapper)
    oversized["resumeState"]["padding"] = "x" * receipts.MAX_STATE_BYTES
    assert client.post(URL, json={"previousReview": oversized}).status_code == 400
    assert client.post(URL, json={"previousReview": {"receiptId": "bad", "resumeState": {}}}).status_code == 400
    assert client.post(URL, json={"retryFailed": "true"}).status_code == 400
    state["allowed"] = False
    assert client.post(URL, json={}).status_code == 403
    assert len(state["calls"]) == 1


def test_mid_review_source_change_does_not_issue_a_stale_receipt(boundary):
    client, state, _project = boundary
    state["changeDuringRun"] = True
    result = client.post(URL, json={"workflowTrack": "pf"})
    assert result.status_code == 409, result.text
    assert state["receipts"] == {}


def test_receipt_id_collision_never_overwrites_an_existing_receipt(boundary, monkeypatch):
    client, state, _project = boundary
    monkeypatch.setattr(receipts, "token_hex", lambda _size: "a" * 64)
    first = client.post(URL, json={})
    assert first.status_code == 200
    saved = deepcopy(state["receipts"])
    assert client.post(URL, json={}).status_code == 409
    assert state["receipts"] == saved


def test_plan_and_review_share_the_same_canonical_input_revision(boundary, monkeypatch):
    client, state, project = boundary
    monkeypatch.setattr(projects, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(projects, "_project_store", lambda: {"p": project})

    def hydrate(_pid, overlay, *, authoritative_source=None, include_active_cures):
        assert not overlay and not include_active_cures
        return deepcopy(authoritative_source or project)

    monkeypatch.setattr(projects, "_hydrated_project_source", hydrate)
    monkeypatch.setattr(projects, "_persist_shared_deal_state_graph", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(projects, "record_sensitive_project_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(projects, "plan_chief_deal_orchestration_use_case", lambda _pid, _source, options: {
        "taskGraph": {"workflowTrack": options["workflowTrack"]},
    })
    plan = client.post("/projects/p/chief-orchestration/plan", json={"workflowTrack": "pf"})
    assert plan.status_code == 200, plan.text
    reviewed = client.post(URL, json={"workflowTrack": "pf"})
    assert reviewed.status_code == 200, reviewed.text
    assert plan.json()["preflightInputRevision"] == reviewed.json()["inputRevision"]


def test_receipts_use_the_existing_scoped_immutable_postgres_insert(monkeypatch):
    from contextlib import contextmanager
    from db.repositories.request_project_repository import project_request_scope
    from test_request_project_scope import Authority

    records = {}
    statements = []

    class Rows:
        def __init__(self, values):
            self.values = values

        def fetchone(self):
            return self.values[0] if self.values else None

        def fetchall(self):
            return self.values

    class Connection:
        def execute(self, sql, params):
            statements.append(sql)
            if sql.startswith("INSERT INTO realytics.agent_handoffs"):
                assert "ON CONFLICT DO NOTHING" in sql
                columns = [value.strip() for value in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
                values = dict(zip(columns, params))
                row = json.loads(values["payload_json"])
                identity = (values["tenant_id"], values["project_id"], values["handoff_id"])
                assert row["tenantId"] == values["tenant_id"]
                if identity in records:
                    return Rows([])
                records[identity] = row
                return Rows([(values["tenant_id"],)])
            assert sql.startswith("SELECT tenant_id, payload_json FROM realytics.agent_handoffs")
            project, receipt, tenant, limit = params
            assert limit == 1
            row = records.get((tenant, project, receipt))
            return Rows([(tenant, deepcopy(row))] if row else [])

    @contextmanager
    def transaction(tenant):
        assert tenant in {"tenant-a", "tenant-b"}
        yield Connection()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Receipt opened an unscoped connection or applied a migration")

    monkeypatch.setattr(receipts, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(receipts.postgres, "connect", forbidden)
    monkeypatch.setattr(receipts.postgres, "ensure_schema", forbidden)
    context = {
        "project_id": "p", "tenant_id": "tenant-a", "actor_id": "reviewer",
        "workflow_track": "pf", "engine_version": review.RUNNER_VERSION, "input_revision": "a" * 64,
    }
    transient = {
        "runnerVersion": review.RUNNER_VERSION, "sourceRevision": "a" * 64,
        "private": {"publicTransactions": [{"amount": 987654321}]},
    }
    with project_request_scope(lambda: Authority("tenant-a"), transaction_factory=transaction):
        receipt_id = receipts.issue_review_receipt(transient, **context)
        wrapper = {"receiptId": receipt_id, "resumeState": transient}
        assert receipts.verify_previous_review(wrapper, **{**context, "input_revision": "b" * 64}) == transient
    assert len(records) == 1
    assert "publicTransactions" not in json.dumps(list(records.values()))
    assert "987654321" not in json.dumps(list(records.values()))
    with project_request_scope(lambda: Authority("tenant-b"), transaction_factory=transaction):
        with pytest.raises(receipts.PreflightReceiptError):
            receipts.verify_previous_review(wrapper, **{**context, "tenant_id": "tenant-b"})
    assert all("app_state" not in sql for sql in statements)


def test_large_browser_review_is_compressed_and_reused_only_after_receipt_verification(boundary):
    _client, state, _project = boundary
    source = {"runnerVersion": review.RUNNER_VERSION, "sourceRevision": "a" * 64,
              "workflowTrack": "pf", "outputs": [{"source": "공개 원본 조회", "content": "검토 자료 " * 1000}] * 100}
    packed = receipts.pack_review_state(source)
    assert packed["encoding"] == "zlib-base64-v1"
    assert len(json.dumps(packed).encode()) < receipts.MAX_STATE_BYTES
    context = {"project_id": "p", "tenant_id": "tenant-a", "actor_id": "reviewer", "workflow_track": "pf",
               "engine_version": review.RUNNER_VERSION, "input_revision": "a" * 64}
    receipt_id = receipts.issue_review_receipt(packed, **context)
    assert receipts.verify_previous_review({"receiptId": receipt_id, "resumeState": packed}, **context) == source
    assert "공개 원본 조회" not in json.dumps(state["receipts"], ensure_ascii=False)
    changed = {**packed, "data": packed["data"][:-4] + "AAAA"}
    with pytest.raises(receipts.PreflightReceiptError):
        receipts.verify_previous_review({"receiptId": receipt_id, "resumeState": changed}, **context)
