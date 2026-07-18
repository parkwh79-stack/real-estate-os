"""Source-document listing and binary download boundary.

The router owns authorization, archive integrity lookup, RFC 5987 filenames,
and download audit. Parsing and classification remain separate use cases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from core.authz import authorize_request
from use_cases.source_documents import (
    get_source_document_download_use_case,
    list_source_documents_use_case,
)


ProjectLookup = Callable[[str], Dict[str, Any] | None]
RepositoryFactory = Callable[[], Any]
AuditRecorder = Callable[..., None]


def build_project_source_documents_router(
    *,
    project_lookup: ProjectLookup,
    source_document_repository_factory: RepositoryFactory,
    upload_archive_repository_factory: RepositoryFactory,
    audit_recorder: AuditRecorder,
) -> APIRouter:
    router = APIRouter(prefix="/projects", tags=["project-source-documents"])

    def require_project(project_id: str) -> Dict[str, Any]:
        project = project_lookup(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    @router.get("/{project_id}/source-documents")
    def list_project_source_documents(project_id: str, request: Request):
        require_project(project_id)
        authorize_request(request, "evidence:read")
        return list_source_documents_use_case(source_document_repository_factory(), project_id)

    @router.get("/{project_id}/source-documents/{archive_id}/download")
    def download_project_source_document(project_id: str, archive_id: str, request: Request):
        require_project(project_id)
        authz = authorize_request(request, "evidence:read")
        download = get_source_document_download_use_case(
            source_document_repository_factory(),
            upload_archive_repository_factory(),
            project_id,
            archive_id,
        )
        if not download:
            raise HTTPException(status_code=404, detail="Source document not found")

        audit_recorder(
            request,
            event_type="source_document_download",
            project_id=project_id,
            metadata={
                "archiveId": archive_id,
                "filename": download.get("filename"),
                "mediaType": download.get("mediaType"),
                "fileSizeBytes": download.get("fileSizeBytes"),
                "archiveIntegrityStatus": download.get("archiveIntegrityStatus"),
                "archiveBackend": download.get("archiveBackend"),
                "authorization": authz.get("authorization"),
            },
        )

        if download.get("path"):
            return FileResponse(
                download["path"],
                media_type=download["mediaType"],
                filename=download["filename"],
            )
        filename = str(download["filename"])
        return Response(
            content=download.get("content") or b"",
            media_type=download["mediaType"],
            headers={
                "Content-Disposition": (
                    f'attachment; filename="source-document{Path(filename).suffix}"; '
                    f"filename*=UTF-8''{quote(filename)}"
                )
            },
        )

    return router
