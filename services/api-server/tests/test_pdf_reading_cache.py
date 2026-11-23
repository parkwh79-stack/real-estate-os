from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import fitz
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agents.parser_agent import ParserAgent
from db.malware_scanner import MalwareScanResult
from db.object_storage import LocalObjectStorage
from db.repositories.source_document_repository import SourceDocumentRepository
from db.repositories.upload_archive_repository import UploadArchiveRepository
from routers import project_source_documents as routes
from storage import create_user_session, upsert_user
from use_cases.pdf_reading_cache import MAX_READING_RESPONSE_BYTES, PdfReadingCache


class _CleanScanner:
    def scan(self, *_args, **_kwargs):
        return MalwareScanResult(status="clean", engine="test-scanner", scanned_at=datetime.now(timezone.utc))


class _VersionedStorage:
    backend = "gcs"
    retention_days = 3
    retention_mode = "GOVERNANCE"

    def __init__(self):
        self.objects, self.metadata, self.versions = {}, {}, {}
        self.range_calls = []
        self.fail_derived_writes = False

    def put(self, key, content, **kwargs):
        if self.fail_derived_writes and key.startswith("derived/"):
            raise RuntimeError("cache unavailable")
        if key in self.objects and self.objects[key] != content:
            raise RuntimeError("conditional create conflict")
        self.objects[key] = bytes(content)
        self.metadata[key] = dict(kwargs.get("metadata") or {})
        self.versions.setdefault(key, "1")
        return {"backend": "gcs", "key": key, "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(), "versionId": self.versions[key]}

    def get(self, key):
        raise AssertionError("original/cache reads must pin a version with get_range")

    def head(self, key):
        return {"size": len(self.objects[key]), "versionId": self.versions[key],
                "etag": f'"generation-{self.versions[key]}"', "metadata": self.metadata[key],
                "objectLockMode": "GOVERNANCE", "objectLockRetainUntil": "2099-01-01T00:00:00Z",
                "serverSideEncryption": "google-managed"}

    def get_range(self, key, start, end, *, version_id=None, if_match=None):
        assert version_id == self.versions[key]
        assert if_match == self.head(key)["etag"]
        self.range_calls.append((key, start, end, version_id))
        return self.objects[key][start:end + 1]

    def exists(self, key):
        return key in self.objects

    def delete(self, key):
        existed = key in self.objects
        self.objects.pop(key, None)
        self.metadata.pop(key, None)
        self.versions.pop(key, None)
        return existed

    def delete_prefix(self, prefix):
        keys = [key for key in self.objects if key.startswith(prefix + "/")]
        for key in keys:
            self.delete(key)
        return bool(keys)

    def local_path(self, _key):
        return None


def _pdf_bytes():
    with fitz.open() as document:
        page = document.new_page(width=300, height=400)
        page.insert_text((30, 50), "Verified project source native text for reading.")
        page = document.new_page(width=300, height=400)
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100), False)
        pixmap.clear_with(128)
        page.insert_image(page.rect, stream=pixmap.tobytes("png"))
        return document.tobytes()


@pytest.fixture
def reading(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "identity.sqlite3"))
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    monkeypatch.setenv("REALYTICS_TENANT_ISOLATION_MODE", "strict")
    monkeypatch.setattr(UploadArchiveRepository, "hardened_storage_required", classmethod(lambda cls: True))
    identities = {}
    for name, role, tenant in (("owner", "analyst", "tenant-a"), ("reviewer", "reviewer", "tenant-a"),
                               ("viewer", "viewer", "tenant-a"), ("foreign", "admin", "tenant-b")):
        user_id = f"pdf-reading-{name}"
        upsert_user({"userId": user_id, "tenantId": tenant, "role": role})
        session = create_user_session({"userId": user_id, "provider": "local-test"})
        identities[name] = {"x-realytics-session-id": session["sessionId"]}
    storage = _VersionedStorage()
    archive = UploadArchiveRepository(object_storage=storage, malware_scanner=_CleanScanner())
    content = _pdf_bytes()
    upload = archive.archive_upload("p1", content, "source.pdf", "application/pdf", tenant_id="tenant-a")
    document = {**upload, "originalFileName": "source.pdf", "contentType": "application/pdf",
                "documentVersionId": "document-version-source-1"}
    project = {"id": "p1", "_meta": {"tenantId": "tenant-a"}, "source_documents": [document],
               "feasibility": {"approvedLoan": 100}, "approvedInputSnapshot": {"version": 1}}
    events = []
    app = FastAPI()
    app.include_router(routes.build_project_source_documents_router(
        project_lookup=lambda pid, request: project if pid == "p1" else None,
        source_document_repository_factory=lambda pid, record: SourceDocumentRepository({pid: record}, lambda: None),
        upload_archive_repository_factory=lambda: archive,
        audit_recorder=lambda *args, **kwargs: events.append(kwargs),
    ))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, identities=identities, storage=storage, archive=archive,
                              document=document, project=project, events=events, content=content,
                              path=f"/projects/p1/source-documents/{upload['archiveId']}/reading")


def _post(reading, pages=(1,), identity="owner"):
    return reading.client.post(reading.path, json={"pageNumbers": list(pages)}, headers=reading.identities[identity])


def test_native_and_unavailable_ocr_are_separate_and_get_never_runs_ocr(reading, monkeypatch):
    before = deepcopy(reading.project)
    response = _post(reading, (1, 2))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pages"][0]["sourceKind"] == "native"
    assert body["pages"][0]["status"] == "completed"
    assert body["pages"][1]["sourceKind"] == "ocr"
    assert body["pages"][1]["status"] == "unavailable"
    assert body["storedPages"] == [1] and body["unstoredPages"] == [2]
    assert body["pendingOcrPages"] == [2]
    assert body["analysisOnly"] and not body["factPromotionAllowed"] and not body["reportEligible"]
    assert body["sourceVerification"] == {
        "hashAlgorithm": "sha256", "hashVerified": True, "archiveVersionId": "1",
        "documentVersionId": "document-version-source-1", "bindingMode": "object_generation_etag",
    }
    assert "objectKey" not in body and "tenantId" not in body
    assert all(page["reviewRequired"] and not page["approvalEligible"] for page in body["pages"])
    assert reading.project == before
    assert len([call for call in reading.storage.range_calls if "/active/" in call[0]]) == 1
    assert len(response.content) < MAX_READING_RESPONSE_BYTES
    assert response.headers["cache-control"] == "private, no-store"
    monkeypatch.setattr(ParserAgent, "read_evidence_pages", staticmethod(lambda *_args, **_kwargs: pytest.fail("GET must not run the reader")))
    cached = reading.client.get(reading.path + "/pages/1", headers=reading.identities["owner"])
    assert cached.status_code == 200 and cached.json()["pages"][0]["text"] == body["pages"][0]["text"]
    missing = reading.client.get(reading.path + "/pages/2", headers=reading.identities["owner"])
    assert missing.status_code == 200 and missing.json()["status"] == "not_generated"
    assert missing.json()["fileSha256"] == reading.document["archiveId"]
    assert missing.json()["coverageAssessed"] is False
    assert all("text" not in event["metadata"] for event in reading.events)


def test_post_reuses_complete_reading_without_rerunning_engine(reading, monkeypatch):
    first = _post(reading)
    assert first.status_code == 200
    monkeypatch.setattr(ParserAgent, "read_evidence_pages", staticmethod(lambda *_args, **_kwargs: pytest.fail("complete cache should be reused")))
    assert _post(reading).json()["pages"] == first.json()["pages"]


@pytest.mark.parametrize("identity", ["viewer", "foreign"])
def test_reading_permissions_and_tenant_precede_storage(reading, identity, monkeypatch):
    monkeypatch.setattr(reading.storage, "head", lambda *_: pytest.fail("unauthorized access reached object metadata"))
    assert _post(reading, identity=identity).status_code == 403
    assert reading.client.get(reading.path + "/pages/1", headers=reading.identities[identity]).status_code == 403


def test_evidence_reader_role_can_read_without_getting_project_write_permission(reading):
    assert _post(reading, identity="reviewer").status_code == 200
    assert reading.events[-1]["metadata"]["authorization"]["permission"] == "evidence:read"


def test_cached_reading_is_not_served_after_original_scan_status_is_revoked(reading):
    assert _post(reading).status_code == 200
    reading.document["malwareScanStatus"] = "infected"
    assert reading.client.get(reading.path + "/pages/1", headers=reading.identities["owner"]).status_code == 404


def test_original_hash_tamper_blocks_cached_result_before_reading(reading):
    assert _post(reading).status_code == 200
    key = reading.document["archiveObjectKey"]
    original = reading.storage.objects[key]
    reading.storage.objects[key] = original[:-1] + bytes([original[-1] ^ 1])
    response = reading.client.get(reading.path + "/pages/1", headers=reading.identities["owner"])
    assert response.status_code == 409
    assert "hash changed" in response.json()["detail"]


def test_generation_and_reader_fingerprint_changes_invalidate_cache(reading, monkeypatch):
    assert _post(reading).status_code == 200
    key = reading.document["archiveObjectKey"]
    reading.storage.versions[key] = "2"
    response = reading.client.get(reading.path + "/pages/1", headers=reading.identities["owner"])
    assert response.json()["status"] == "not_generated"
    assert response.json()["objectVersion"] == "2"
    reading.storage.versions[key] = "1"
    monkeypatch.setattr(routes, "get_local_pdf_reading_fingerprint", lambda: "f" * 64)
    changed = reading.client.get(reading.path + "/pages/1", headers=reading.identities["owner"])
    assert changed.json()["status"] == "not_generated"


@pytest.mark.parametrize("pages", [[], [1, 1], [0], [True], [1, 2, 3, 4], [3]])
def test_invalid_or_outside_page_selection_is_explicit(reading, pages):
    response = _post(reading, pages)
    assert response.status_code == 422
    assert not any(key.startswith("derived/") for key in reading.storage.objects)


def test_cache_failure_does_not_discard_verified_native_reading(reading):
    reading.storage.fail_derived_writes = True
    response = _post(reading)
    assert response.status_code == 200
    assert response.json()["pages"][0]["status"] == "completed"
    assert response.json()["storedPages"] == [] and response.json()["unstoredPages"] == [1]
    assert response.json()["cacheWarnings"][0]["errorCode"] == "PDF_READING_CACHE_WRITE_FAILED"


def test_oversized_reader_response_is_rejected_without_silent_clipping(reading, monkeypatch):
    actual_reader = ParserAgent.read_evidence_pages

    def oversized(document, *, page_numbers):
        result = actual_reader(document, page_numbers=page_numbers)
        text = "가" * (MAX_READING_RESPONSE_BYTES // 3 + 1)
        result["pages"][0].update({"text": text, "textSha256": hashlib.sha256(text.encode()).hexdigest()})
        return result

    monkeypatch.setattr(ParserAgent, "read_evidence_pages", staticmethod(oversized))
    response = _post(reading)
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "pdf_reading_response_too_large"
    assert not any(key.startswith("derived/") for key in reading.storage.objects)


def test_expired_cache_is_not_returned_and_only_derived_object_is_deleted(reading):
    assert _post(reading).status_code == 200
    key = next(key for key in reading.storage.objects if key.startswith("derived/"))
    saved = json.loads(reading.storage.objects[key])
    saved["expiresAt"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    content = json.dumps(saved).encode()
    reading.storage.objects[key] = content
    reading.storage.metadata[key]["sha256"] = hashlib.sha256(content).hexdigest()
    response = reading.client.get(reading.path + "/pages/1", headers=reading.identities["owner"])
    assert response.json()["status"] == "not_generated"
    assert key not in reading.storage.objects
    assert reading.storage.objects[reading.document["archiveObjectKey"]] == reading.content


def test_corrupt_cached_text_is_not_shown_and_explicit_post_can_still_read_original(reading):
    assert _post(reading).status_code == 200
    key = next(key for key in reading.storage.objects if key.startswith("derived/"))
    saved = json.loads(reading.storage.objects[key])
    saved["reading"]["pages"][0]["text"] = "tampered cache text"
    content = json.dumps(saved).encode()
    reading.storage.objects[key] = content
    reading.storage.metadata[key]["sha256"] = hashlib.sha256(content).hexdigest()
    cached = reading.client.get(reading.path + "/pages/1", headers=reading.identities["owner"])
    assert cached.status_code == 409
    explicit = _post(reading)
    assert explicit.status_code == 200
    assert "tampered" not in explicit.json()["pages"][0]["text"]
    assert explicit.json()["cacheWarnings"] and explicit.json()["unstoredPages"] == [1]


def test_authoritative_reader_output_cannot_enter_derived_reading_cache(reading, monkeypatch):
    actual_reader = ParserAgent.read_evidence_pages

    def invalid(document, *, page_numbers):
        result = actual_reader(document, page_numbers=page_numbers)
        result["reportEligible"] = True
        return result

    monkeypatch.setattr(ParserAgent, "read_evidence_pages", staticmethod(invalid))
    response = _post(reading)
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "pdf_reading_authority_invalid"
    assert not any(key.startswith("derived/") for key in reading.storage.objects)


def test_long_windows_local_cache_path_uses_actual_local_binding(tmp_path):
    storage = LocalObjectStorage(tmp_path / ("long-reading-root-" * 4))
    view = {"archiveId": "a" * 64, "archiveTenantId": "tenant-" + "b" * 36,
            "archiveBackend": "local", "archiveETag": '"local-real-etag"',
            "archiveObjectKey": "original/file.pdf", "filename": "source.pdf", "document": {}}
    cache = PdfReadingCache(storage, project_id="project-" + "c" * 36, view=view, reader_fingerprint="d" * 64)
    assert cache.binding["objectVersion"] == 'local-etag:"local-real-etag"'
    assert cache.load(1) is None
    assert cache.not_generated(1)["status"] == "not_generated"
    reading = {**cache.not_generated(1), "pageCount": 1, "pages": [{
        "pageNumber": 1, "status": "completed", "sourceKind": "native", "text": "local reading",
        "textSha256": hashlib.sha256(b"local reading").hexdigest(), "blocks": [],
        "width": 300, "height": 400, "coordinateSpace": "pdf_points_top_left",
    }]}
    reading.pop("status")
    assert cache.save(reading, 1)["pages"][0]["text"] == "local reading"
    assert cache.load(1)["pages"][0]["text"] == "local reading"
