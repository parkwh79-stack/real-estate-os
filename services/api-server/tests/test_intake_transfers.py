from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import hashlib
from io import BytesIO
import json

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
import pytest

from db.object_storage import LocalObjectStorage
from db.repositories.upload_archive_repository import UploadRejectedError
from routers import projects
from use_cases.intake_transfers import IntakeTransferStore, MAX_FILE_BYTES, PART_SIZE_BYTES


SCOPE = {"projectId": "p1", "tenantId": "tenant-a", "principalId": "user-a"}
PDF_BYTES = b"%PDF-1.7\ntest original bytes"


@pytest.fixture
def store(tmp_path):
    return IntakeTransferStore(LocalObjectStorage(tmp_path))


def stage(store, content=PDF_BYTES, *, filename="plan.pdf", digest=None):
    created = store.create(SCOPE, {
        "filename": filename, "contentType": "application/octet-stream",
        "sizeBytes": len(content), "sha256": digest or hashlib.sha256(content).hexdigest(),
    })
    transfer_id = created["transferId"]
    for index in range(created["partCount"]):
        store.put_part(SCOPE, transfer_id, index, content[index * PART_SIZE_BYTES:(index + 1) * PART_SIZE_BYTES])
    return transfer_id


def test_large_file_cross_worker_reassembly_preserves_original_bytes(store):
    content = bytes(range(256)) * (24 * 1024) + b"tail"
    transfer_id = stage(store, content)
    another_worker = IntakeTransferStore(store.storage)
    with another_worker.assembled_file(SCOPE, transfer_id) as (manifest, assembled):
        assert manifest["partCount"] == 3
        assert assembled.read() == content
        assert manifest["sha256"] == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize("field,value", [
    ("filename", "../private.pdf"), ("filename", "bad\x00name"),
    ("contentType", "a\r\nb"), ("sizeBytes", 0), ("sizeBytes", True),
    ("sizeBytes", MAX_FILE_BYTES + 1), ("sha256", "z" * 64),
])
def test_invalid_manifest_rejected_before_storage(store, field, value):
    payload = {"filename": "plan.pdf", "contentType": "application/pdf", "sizeBytes": 3, "sha256": "a" * 64}
    payload[field] = value
    with pytest.raises(HTTPException):
        store.create(SCOPE, payload)
    assert list(store.storage.base_dir.glob("**/*")) == []


def test_part_retry_accepts_same_bytes_but_rejects_changed_content(store):
    transfer_id = stage(store, b"abc")
    assert store.put_part(SCOPE, transfer_id, 0, b"abc")["receivedBytes"] == 3
    with pytest.raises(HTTPException) as conflict:
        store.put_part(SCOPE, transfer_id, 0, b"xyz")
    assert conflict.value.status_code == 409
    with store.assembled_file(SCOPE, transfer_id) as (_, content):
        assert content.read() == b"abc"
    for index, content in [(-1, b"abc"), (1, b"abc"), (0, b"ab"), (0, b"abcd")]:
        with pytest.raises(HTTPException):
            store.put_part(SCOPE, transfer_id, index, content)


def test_missing_part_and_wrong_whole_hash_never_enter_parser(store):
    transfer_id = stage(store, b"abc", digest="a" * 64)
    with pytest.raises(HTTPException) as bad_hash:
        with store.assembled_file(SCOPE, transfer_id):
            pytest.fail("unverified content escaped")
    assert bad_hash.value.detail["error"] == "intake_transfer_integrity_failed"
    store.storage.delete(f"{store._prefix(SCOPE, transfer_id)}/parts/0")
    with pytest.raises(HTTPException) as missing:
        with store.assembled_file(SCOPE, transfer_id):
            pytest.fail("incomplete content escaped")
    assert missing.value.detail["error"] == "intake_transfer_incomplete"


@pytest.mark.parametrize("field", ["tenantId", "projectId", "principalId"])
def test_transfer_scope_is_not_shared(store, field):
    transfer_id = stage(store)
    outsider = {**SCOPE, field: "another"}
    with pytest.raises(HTTPException) as denied:
        store.load(outsider, transfer_id)
    assert denied.value.status_code == 404
    store.cancel(outsider, transfer_id)
    assert store.load(SCOPE, transfer_id)["filename"] == "plan.pdf"


def test_expiry_and_explicit_cancel_cleanup_only_temporary_objects(store):
    transfer_id = stage(store)
    active = "tenants/tenant-a/projects/p1/active/original.pdf"
    store.storage.put(active, b"preserve original")
    manifest_key = f"{store._prefix(SCOPE, transfer_id)}/manifest"
    manifest = store.load(SCOPE, transfer_id)
    manifest["expiresAt"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    store.storage.local_path(manifest_key).write_bytes(store._encode(manifest))
    with pytest.raises(HTTPException) as expired:
        store.load(SCOPE, transfer_id)
    assert expired.value.status_code == 410
    assert not store.storage.exists(manifest_key)
    second_id = stage(store)
    assert store.cancel(SCOPE, second_id)["deleted"]
    assert store.cancel(SCOPE, second_id)["deleted"]
    assert store.storage.get(active) == b"preserve original"


def test_operation_blocks_parallel_processing_and_cancel_then_releases_on_failure(store):
    transfer_id = stage(store)
    with pytest.raises(RuntimeError):
        with store.operation(SCOPE, transfer_id):
            with pytest.raises(HTTPException) as busy:
                with IntakeTransferStore(store.storage).operation(SCOPE, transfer_id):
                    pytest.fail("parallel write escaped")
            assert busy.value.detail["error"] == "intake_transfer_busy"
            with pytest.raises(HTTPException):
                store.cancel(SCOPE, transfer_id)
            raise RuntimeError("parser failed")
    with store.operation(SCOPE, transfer_id):
        pass


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(projects, "_intake_transfer_context", lambda *_: SCOPE)
    monkeypatch.setattr(projects, "intake_transfer_store", lambda: store)
    app = FastAPI()
    app.include_router(projects.router)
    with TestClient(app) as client:
        yield client


def test_route_part_stream_limit_and_content_type(client, store):
    transfer_id = stage(store, b"abc")
    path = f"/projects/p1/intake/transfers/{transfer_id}/parts/0"
    assert client.put(path, content=b"abcd", headers={"content-type": "application/octet-stream"}).status_code == 413
    assert client.put(path, content=b"abc", headers={"content-type": "text/plain"}).status_code == 415
    assert client.put(path, content=b"abc", headers={"content-type": "application/octet-stream"}).status_code == 200


def test_classify_and_process_reuse_verified_bytes_and_cache_response(client, store, monkeypatch):
    content = b"%PDF-1.7\n" + b"source bytes unchanged" * 300000
    transfer_id = stage(store, content)
    seen = []

    async def classify(project_id, file, *, require_archive=False):
        assert require_archive
        seen.append(("classify", await file.read()))
        return {"filename": file.filename, "documentType": "business_plan", "archiveStored": True}

    async def process(project_id, request, file, *args):
        seen.append(("process", await file.read()))
        return {"project": {"id": project_id}}

    monkeypatch.setattr(projects, "_classify_intake_file", classify)
    monkeypatch.setattr(projects, "upload_business_plan", process)
    path = f"/projects/p1/intake/transfers/{transfer_id}"
    classified = client.post(f"{path}/classify", json={})
    assert classified.status_code == 200
    assert classified.json()["files"][0]["transferId"] == transfer_id
    assert client.post(f"{path}/classify", json={}).json() == classified.json()
    processed = client.post(f"{path}/process", json={"documentType": "business_plan"})
    assert processed.status_code == 200
    # A stale lease from a failed cleanup must not hide an accepted receipt.
    with store.operation(SCOPE, transfer_id):
        assert client.post(f"{path}/process", json={"documentType": "business_plan"}).json() == processed.json()
    assert seen == [("classify", content), ("process", content)]
    assert not store.storage.exists(f"{store._prefix(SCOPE, transfer_id)}/parts/0")
    assert client.post(f"{path}/process", json={"documentType": "land_ledger"}).status_code == 409


def test_process_failure_preserves_parts_for_retry(client, store, monkeypatch):
    transfer_id = stage(store)

    async def failed(*_):
        raise HTTPException(status_code=503, detail={"error": "scanner_unavailable"})

    monkeypatch.setattr(projects, "upload_business_plan", failed)
    response = client.post(f"/projects/p1/intake/transfers/{transfer_id}/process", json={"documentType": "business_plan"})
    assert response.status_code == 503
    with store.assembled_file(SCOPE, transfer_id) as (_, content):
        assert content.read() == PDF_BYTES
    assert store.read_result(SCOPE, transfer_id, "process") is None


@pytest.mark.parametrize("filename", ["committee.pdf", "committee.xlsx"])
def test_pdf_bytes_never_reach_a_workbook_parser(client, store, monkeypatch, filename):
    content = b"%PDF-1.7\nexample"
    transfer_id = stage(store, content, filename=filename)
    monkeypatch.setattr(projects, "_project_store", lambda: {"p1": {}})

    direct = client.post(
        "/projects/p1/feasibility-workbook/validate",
        files={"file": (filename, content, "application/octet-stream")},
    )
    transferred = client.post(
        f"/projects/p1/intake/transfers/{transfer_id}/process",
        json={"documentType": "feasibility_workbook"},
    )
    for response in (direct, transferred):
        assert response.status_code == 415
        assert response.json()["detail"]["error"] == "intake_document_format_mismatch"
    with store.assembled_file(SCOPE, transfer_id) as (_, source):
        assert source.read() == content


def test_receipt_storage_failure_blocks_reapplying_already_processed_source(client, store, monkeypatch):
    transfer_id = stage(store)
    calls = []

    async def applied(*_):
        calls.append(True)
        return {"project": {"id": "p1"}}

    def unavailable(*_):
        raise RuntimeError("receipt storage unavailable")

    monkeypatch.setattr(projects, "upload_business_plan", applied)
    monkeypatch.setattr(store, "save_result", unavailable)
    path = f"/projects/p1/intake/transfers/{transfer_id}/process"
    first = client.post(path, json={"documentType": "business_plan"})
    assert first.status_code == 503
    assert first.json()["detail"]["error"] == "intake_transfer_result_uncertain"
    assert client.post(path, json={"documentType": "business_plan"}).status_code == 409
    assert calls == [True]


@pytest.mark.parametrize("result", [
    {"status": "host_llm_required"}, {"validation": {"status": "failed"}},
    {"_meta": {"parsing_source": "parse_failed"}},
    {"project": {"_meta": {"parsing_source": "extraction_unavailable"}}},
    {"upload": {"_meta": {"parsing_source": "host_llm_required"}}},
])
def test_http_success_with_extraction_failure_retains_parts_and_allows_retry(client, store, monkeypatch, result):
    transfer_id = stage(store)

    async def failed(*_):
        return result

    monkeypatch.setattr(projects, "upload_business_plan", failed)
    path = f"/projects/p1/intake/transfers/{transfer_id}/process"
    assert client.post(path, json={"documentType": "business_plan"}).json() == result
    assert store.read_result(SCOPE, transfer_id, "process") is None
    with store.assembled_file(SCOPE, transfer_id) as (_, content):
        assert content.read() == PDF_BYTES


def test_downstream_reanalysis_failure_is_cached_without_reapplying_original(client, store, monkeypatch):
    transfer_id = stage(store)
    calls = []

    async def applied(*_):
        calls.append(True)
        return {"project": {"id": "p1"}, "autoReanalysis": {"triggered": True, "status": "failed"}}

    monkeypatch.setattr(projects, "upload_business_plan", applied)
    path = f"/projects/p1/intake/transfers/{transfer_id}/process"
    first = client.post(path, json={"documentType": "business_plan"})
    assert first.status_code == 200
    assert client.post(path, json={"documentType": "business_plan"}).json() == first.json()
    assert calls == [True]


def test_reviewable_workbook_formula_failure_keeps_draft_and_validation_on_retry(client, store, monkeypatch):
    content = b"PK\x03\x04workbook validator fixture"
    transfer_id = stage(store, content, filename="review.xlsx")
    digest = hashlib.sha256(content).hexdigest()
    calls = []
    draft = {
        "validation": {"status": "failed", "sourceDocumentHash": digest},
        "upload": {"archiveStored": True, "archiveId": digest},
        "feasibilityStudyV2Draft": {"version": "realytics-feasibility-study-v2"},
    }

    async def validate(*_):
        calls.append(True)
        return draft

    monkeypatch.setattr(projects, "validate_project_feasibility_workbook", validate)
    path = f"/projects/p1/intake/transfers/{transfer_id}/process"
    assert client.post(path, json={"documentType": "feasibility_workbook"}).json() == draft
    assert client.post(path, json={"documentType": "feasibility_workbook"}).json() == draft
    assert calls == [True]


def test_long_local_root_and_uuid_scope_can_upload(tmp_path):
    store = IntakeTransferStore(LocalObjectStorage(tmp_path / ("long-local-root-" * 7)))
    scope = {"projectId": "a" * 36, "tenantId": "b" * 36, "principalId": "c" * 36}
    created = store.create(scope, {"filename": "sample.pdf", "sizeBytes": 3, "sha256": hashlib.sha256(b"abc").hexdigest()})
    store.put_part(scope, created["transferId"], 0, b"abc")
    with store.assembled_file(scope, created["transferId"]) as (_, content):
        assert content.read() == b"abc"
    assert store.cancel(scope, created["transferId"])["deleted"]


def test_json_process_without_prior_classification_archives_then_uses_existing_handler(client, store, monkeypatch):
    content = json.dumps([{"pnu": "sample", "source": "original"}]).encode()
    transfer_id = stage(store, content, filename="land.json")
    order = []

    async def classify(project_id, file, *, require_archive=False):
        assert require_archive and await file.read() == content
        order.append("archive-and-scan")
        return {"documentType": "land_evidence", "archiveStored": True}

    def evidence(project_id, request, payload):
        assert payload == {"records": json.loads(content)}
        order.append("existing-handler")
        return {"projectId": project_id, "count": 1}

    monkeypatch.setattr(projects, "_classify_intake_file", classify)
    monkeypatch.setattr(projects, "upsert_project_land_evidence", evidence)
    response = client.post(f"/projects/p1/intake/transfers/{transfer_id}/process", json={"documentType": "land_evidence"})
    assert response.status_code == 200
    assert order == ["archive-and-scan", "existing-handler"]


def test_rejected_scan_never_reaches_workbook_or_json_classifier(monkeypatch):
    class RejectedArchive:
        def archive_upload(self, *_args, **_kwargs):
            raise UploadRejectedError("malware_detected")

    monkeypatch.setattr(projects, "upload_archive_repository", lambda: RejectedArchive())
    monkeypatch.setattr(projects, "_project_tenant_id", lambda _: "tenant-a")
    monkeypatch.setattr(projects, "classify_document", lambda *_args, **_kwargs: pytest.fail("classifier ran before accepted scan"))
    for filename in ("sample.xlsx", "sample.json"):
        file = UploadFile(filename=filename, file=BytesIO(b"untrusted content"))
        with pytest.raises(UploadRejectedError):
            asyncio.run(projects._classify_intake_file("p1", file, require_archive=True))


class _CreateOnlyRemote:
    """Offline GCS contract double; no local paths or mutable overwrite."""
    backend = "gcs"

    def __init__(self):
        self.objects = {}

    def put(self, key, content, **_kwargs):
        if key in self.objects and self.objects[key] != content:
            raise RuntimeError("conditional create failed")
        self.objects[key] = bytes(content)

    def head(self, key):
        return {"size": len(self.objects[key])}

    def get(self, key):
        return self.objects[key]

    def exists(self, key):
        return key in self.objects

    def delete(self, key):
        return self.objects.pop(key, None) is not None

    def delete_prefix(self, prefix):
        keys = [key for key in self.objects if key.startswith(prefix.rstrip("/") + "/")]
        for key in keys:
            self.delete(key)
        return bool(keys)


def test_remote_conditional_create_supports_retry_and_mutual_exclusion():
    remote = _CreateOnlyRemote()
    first, second = IntakeTransferStore(remote), IntakeTransferStore(remote)
    transfer_id = stage(first, b"abc")
    assert second.put_part(SCOPE, transfer_id, 0, b"abc")["receivedBytes"] == 3
    with pytest.raises(HTTPException) as conflict:
        second.put_part(SCOPE, transfer_id, 0, b"xyz")
    assert conflict.value.status_code == 409
    with first.operation(SCOPE, transfer_id):
        with pytest.raises(HTTPException) as busy:
            with second.operation(SCOPE, transfer_id):
                pytest.fail("remote parallel mutation escaped")
        assert busy.value.detail["error"] == "intake_transfer_busy"
    with second.assembled_file(SCOPE, transfer_id) as (_, content):
        assert content.read() == b"abc"
