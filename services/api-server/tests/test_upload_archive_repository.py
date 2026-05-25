from __future__ import annotations

from db.repositories.upload_archive_repository import UploadArchiveRepository
from use_cases.source_documents import (
    archive_source_document_use_case,
    get_source_document_download_use_case,
)


class InMemorySourceDocumentRepository:
    def __init__(self, document):
        self.document = document

    def find_by_archive_id(self, project_id, archive_id):
        if project_id == "p1" and self.document.get("archiveId") == archive_id:
            return self.document
        return None


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
