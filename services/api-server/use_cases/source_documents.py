from __future__ import annotations

from typing import Any, Dict, List


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
