from __future__ import annotations

from typing import Any, Dict, List


def archive_source_document_use_case(
    upload_archive_repository,
    project_id: str,
    content: bytes,
    original_file_name: str | None,
    content_type: str | None,
    tenant_id: str | None = None,
) -> Dict[str, Any]:
    return upload_archive_repository.archive_upload(
        project_id,
        content,
        original_file_name,
        content_type,
        tenant_id=tenant_id,
    )


def list_source_documents_use_case(source_document_repository, project_id: str) -> List[Dict[str, Any]]:
    return source_document_repository.list(project_id)


def save_source_document_metadata_use_case(
    source_document_repository,
    project_id: str,
    upload_meta: Dict[str, Any],
) -> Dict[str, Any] | None:
    return source_document_repository.save_upload_metadata(project_id, upload_meta)


def find_source_document_by_archive_id_use_case(
    source_document_repository,
    project_id: str,
    archive_id: str,
) -> Dict[str, Any] | None:
    return source_document_repository.find_by_archive_id(project_id, archive_id)


def get_source_document_download_use_case(
    source_document_repository,
    upload_archive_repository,
    project_id: str,
    archive_id: str,
) -> Dict[str, Any] | None:
    if not upload_archive_repository.is_valid_archive_id(archive_id):
        return None
    document = source_document_repository.find_by_archive_id(project_id, archive_id)
    if not document:
        return None
    download_metadata = upload_archive_repository.resolve_download_metadata(project_id, document)
    if not download_metadata:
        return None
    return {
        "document": document,
        **download_metadata,
    }


def delete_project_upload_archive_use_case(upload_archive_repository, project_id: str) -> bool:
    return upload_archive_repository.delete_project_archive(project_id)
