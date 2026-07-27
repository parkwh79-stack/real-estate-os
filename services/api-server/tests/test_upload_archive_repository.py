from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from db.repositories.source_document_repository import SourceDocumentRepository
from db.repositories.upload_archive_repository import UploadArchiveRepository
from db.malware_scanner import MalwareScanResult
from db.repositories.upload_archive_repository import UploadRejectedError
from routers.project_source_documents import build_project_source_documents_router
from use_cases.source_documents import (
    archive_source_document_use_case,
    get_source_document_download_use_case,
)


class InMemorySourceDocumentRepository:
    def __init__(self, document, project_id="p1"):
        self.document = document
        self.project_id = project_id

    def find_by_archive_id(self, project_id, archive_id):
        if project_id == self.project_id and self.document.get("archiveId") == archive_id:
            return self.document
        return None


class InMemoryObjectStorage:
    backend = "s3"

    def __init__(self):
        self.objects = {}
        self.puts = []

    def put(
        self,
        key,
        content,
        *,
        content_type=None,
        metadata=None,
        retention_until=None,
        retention_mode=None,
    ):
        from db.object_storage import sha256_bytes

        self.objects[key] = bytes(content)
        self.puts.append((key, dict(metadata or {})))
        return {
            "backend": self.backend,
            "key": key,
            "size": len(content),
            "sha256": sha256_bytes(content),
            "contentType": content_type,
            "retentionUntil": retention_until.isoformat() if retention_until else None,
            "retentionMode": retention_mode,
        }

    def get(self, key):
        return self.objects[key]

    def exists(self, key):
        return key in self.objects

    def delete_prefix(self, prefix):
        keys = [key for key in self.objects if key == prefix or key.startswith(f"{prefix}/")]
        for key in keys:
            del self.objects[key]
        return bool(keys)

    def delete(self, key):
        return self.objects.pop(key, None) is not None

    def local_path(self, key):
        return None


class _Scanner:
    def __init__(self, status="clean"):
        self.status = status

    def scan(self, content, *, filename=None, content_type=None):
        from datetime import datetime, timezone

        del content, filename, content_type
        return MalwareScanResult(
            status=self.status,
            engine="test-scanner",
            scanned_at=datetime.now(timezone.utc),
            signature="EICAR-Test" if self.status == "infected" else None,
        )


def test_upload_archive_repository_archives_and_resolves_download(tmp_path):
    repository = UploadArchiveRepository(
        api_server_root=tmp_path,
        archive_root=tmp_path / "runtime" / "uploads",
    )

    archive_meta = archive_source_document_use_case(
        repository,
        "p1",
        b"business-plan",
        "business-plan.pdf",
        "application/pdf",
    )

    assert archive_meta["archiveStored"] is True
    assert archive_meta["archiveIntegrityStatus"] == "verified"
    assert archive_meta["archiveHashAlgorithm"] == "sha256"
    assert archive_meta["archivePath"].endswith(".pdf")
    assert archive_meta["downloadUrl"] == f"/projects/p1/source-documents/{archive_meta['archiveId']}/download"

    document = {
        **archive_meta,
        "archiveId": archive_meta["archiveId"],
        "originalFileName": "business-plan.pdf",
        "contentType": "application/pdf",
    }
    download = get_source_document_download_use_case(
        InMemorySourceDocumentRepository(document),
        repository,
        "p1",
        archive_meta["archiveId"],
    )

    assert download is not None
    assert download["filename"] == "business-plan.pdf"
    assert download["mediaType"] == "application/pdf"
    assert download["archiveIntegrityStatus"] == "verified"
    assert download["path"].read_bytes() == b"business-plan"


def test_upload_archive_repository_blocks_hash_mismatch(tmp_path):
    repository = UploadArchiveRepository(
        api_server_root=tmp_path,
        archive_root=tmp_path / "runtime" / "uploads",
    )
    archive_meta = repository.archive_upload("p1", b"original", "source.xlsx", "application/vnd.ms-excel")
    document = {
        **archive_meta,
        "archiveId": archive_meta["archiveId"],
        "originalFileName": "source.xlsx",
        "contentType": "application/vnd.ms-excel",
    }
    archive_path = tmp_path / archive_meta["archivePath"]
    archive_path.write_bytes(b"tampered")

    download = get_source_document_download_use_case(
        InMemorySourceDocumentRepository(document),
        repository,
        "p1",
        archive_meta["archiveId"],
    )

    assert download is None


def test_upload_archive_repository_streams_verified_remote_object(tmp_path):
    object_storage = InMemoryObjectStorage()
    repository = UploadArchiveRepository(
        api_server_root=tmp_path,
        object_storage=object_storage,
        malware_scanner=_Scanner(),
    )
    archive_meta = repository.archive_upload(
        "p-remote",
        b"remote-business-plan",
        "business-plan.pdf",
        "application/pdf",
    )
    document = {
        **archive_meta,
        "originalFileName": "business-plan.pdf",
        "contentType": "application/pdf",
    }

    download = get_source_document_download_use_case(
        InMemorySourceDocumentRepository(document, project_id="p-remote"),
        repository,
        "p-remote",
        archive_meta["archiveId"],
    )

    assert download is not None
    assert download["path"] is None
    assert download["content"] == b"remote-business-plan"
    assert download["archiveBackend"] == "s3"
    assert download["archiveIntegrityStatus"] == "verified"
    assert archive_meta["malwareScanStatus"] == "clean"
    assert archive_meta["storageLifecycleStatus"] == "active"
    assert archive_meta["reportEligible"] is True
    assert "/quarantine/" in object_storage.puts[0][0]
    assert "/active/" in object_storage.puts[1][0]
    assert object_storage.puts[0][0] not in object_storage.objects


def test_upload_archive_repository_rejects_cross_project_object_key(tmp_path):
    object_storage = InMemoryObjectStorage()
    repository = UploadArchiveRepository(
        api_server_root=tmp_path,
        object_storage=object_storage,
        malware_scanner=_Scanner(),
    )
    archive_meta = repository.archive_upload(
        "project-a", b"same-tenant-secret", "plan.pdf", "application/pdf"
    )

    resolved = repository.resolve_download_metadata(
        "project-b",
        {
            "archiveId": archive_meta["archiveId"],
            "archiveObjectKey": archive_meta["archiveObjectKey"],
            "originalFileName": "plan.pdf",
        },
    )

    assert resolved is None


def test_infected_upload_remains_quarantined_and_is_never_promoted(tmp_path):
    object_storage = InMemoryObjectStorage()
    repository = UploadArchiveRepository(
        api_server_root=tmp_path,
        object_storage=object_storage,
        malware_scanner=_Scanner("infected"),
    )

    with pytest.raises(UploadRejectedError) as error:
        repository.archive_upload(
            "project-a",
            b"EICAR-like-test-bytes",
            "plan.pdf",
            "application/pdf",
            tenant_id="tenant-a",
        )

    assert error.value.code == "malware_detected"
    assert len(object_storage.puts) == 1
    assert "/quarantine/" in object_storage.puts[0][0]
    assert all("/active/" not in key for key in object_storage.objects)


def test_remote_download_uses_utf8_content_disposition_and_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "permissive")
    object_storage = InMemoryObjectStorage()
    archive_repository = UploadArchiveRepository(
        api_server_root=tmp_path,
        object_storage=object_storage,
        malware_scanner=_Scanner(),
    )
    archive_meta = archive_repository.archive_upload(
        "p-korean",
        b"source-bytes",
        "사업계획서.pdf",
        "application/pdf",
    )
    document = {
        **archive_meta,
        "originalFileName": "사업계획서.pdf",
        "contentType": "application/pdf",
    }
    audit_events = []
    app = FastAPI()
    app.include_router(
        build_project_source_documents_router(
            project_lookup=lambda project_id, _request: (
                {"id": project_id, "source_documents": [document]}
                if project_id == "p-korean"
                else None
            ),
            source_document_repository_factory=lambda project_id, project: (
                SourceDocumentRepository({project_id: project}, lambda: None)
            ),
            upload_archive_repository_factory=lambda: archive_repository,
            audit_recorder=lambda *args, **kwargs: audit_events.append(kwargs),
        )
    )

    response = TestClient(app).get(
        f"/projects/p-korean/source-documents/{archive_meta['archiveId']}/download"
    )

    assert response.status_code == 200
    assert response.content == b"source-bytes"
    disposition = response.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
    assert "%EC%82%AC%EC%97%85%EA%B3%84%ED%9A%8D%EC%84%9C.pdf" in disposition
    assert audit_events[0]["metadata"]["archiveBackend"] == "s3"
