"""Source-document listing and binary download boundary.

The router owns authorization, archive integrity lookup, RFC 5987 filenames,
and download audit. Parsing and classification remain separate use cases.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any, Callable, Dict
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from core.authz import authorize_request, enforce_project_tenant_scope
from use_cases.source_documents import (
    get_source_document_download_use_case,
    get_source_document_view_metadata_use_case,
    list_source_documents_use_case,
)
from use_cases.source_document_viewer import (
    UnsatisfiableByteRange,
    build_workbook_cell_preview,
    parse_single_byte_range,
    render_pdf_page_preview,
)


ProjectLookup = Callable[[str, Request], Dict[str, Any] | None]
ProjectSourceDocumentRepositoryFactory = Callable[[str, Dict[str, Any]], Any]
RepositoryFactory = Callable[[], Any]
AuditRecorder = Callable[..., None]


def _content_disposition(filename: str, *, disposition: str) -> str:
    raw_suffix = Path(filename).suffix.lower()
    suffix = raw_suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", raw_suffix) else ".bin"
    return (
        f'{disposition}; filename="source-document{suffix}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def _is_pdf(download: Dict[str, Any]) -> bool:
    media_type = str(download.get("mediaType") or "").split(";", 1)[0].lower()
    return media_type == "application/pdf" or Path(str(download.get("filename") or "")).suffix.lower() == ".pdf"


def _is_openxml_workbook(download: Dict[str, Any]) -> bool:
    suffix = Path(str(download.get("filename") or "")).suffix.lower()
    return suffix in {".xlsx", ".xlsm"}


def _viewer_media_type(download: Dict[str, Any]) -> str:
    if _is_pdf(download):
        return "application/pdf"
    if _is_openxml_workbook(download):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"


def build_project_source_documents_router(
    *,
    project_lookup: ProjectLookup,
    source_document_repository_factory: ProjectSourceDocumentRepositoryFactory,
    upload_archive_repository_factory: RepositoryFactory,
    audit_recorder: AuditRecorder,
) -> APIRouter:
    router = APIRouter(prefix="/projects", tags=["project-source-documents"])

    def require_project(
        project_id: str,
        request: Request,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        authz = authorize_request(request, "evidence:read")
        project = project_lookup(project_id, request)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        enforce_project_tenant_scope(request, project)
        return authz, project

    def project_source_document_repository(
        project_id: str,
        project: Dict[str, Any],
    ):
        """Bind source metadata reads to the exact authorized project record."""

        return source_document_repository_factory(project_id, project)

    @router.get("/{project_id}/source-documents")
    def list_project_source_documents(project_id: str, request: Request):
        _, project = require_project(project_id, request)
        return list_source_documents_use_case(
            project_source_document_repository(project_id, project),
            project_id,
        )

    def require_download(project_id: str, archive_id: str, request: Request):
        authz, project = require_project(project_id, request)
        download = get_source_document_download_use_case(
            project_source_document_repository(project_id, project),
            upload_archive_repository_factory(),
            project_id,
            archive_id,
        )
        if not download:
            raise HTTPException(status_code=404, detail="Source document not found")
        return authz, download

    def require_view(project_id: str, archive_id: str, request: Request):
        authz, project = require_project(project_id, request)
        archive_repository = upload_archive_repository_factory()
        view = get_source_document_view_metadata_use_case(
            project_source_document_repository(project_id, project),
            archive_repository,
            project_id,
            archive_id,
        )
        if not view:
            raise HTTPException(status_code=404, detail="Source document not found")
        return authz, view, archive_repository

    def require_view_range(
        archive_repository,
        view: Dict[str, Any],
        start: int,
        end: int,
    ) -> bytes:
        try:
            return archive_repository.read_view_range(view, start, end)
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail="Source document integrity changed during read",
            ) from exc

    def require_full_view_content(archive_repository, view: Dict[str, Any]) -> bytes:
        size = int(view.get("fileSizeBytes") or 0)
        if size <= 0:
            raise HTTPException(status_code=422, detail="Source document is empty")
        content = require_view_range(archive_repository, view, 0, size - 1)
        expected_hash = str(view.get("archiveId") or "").lower()
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise HTTPException(
                status_code=409,
                detail="Source document hash changed during read",
            )
        return content

    @router.api_route(
        "/{project_id}/source-documents/{archive_id}/content",
        methods=["GET", "HEAD"],
    )
    def view_project_source_document(project_id: str, archive_id: str, request: Request):
        """Serve an integrity-checked original with one authenticated byte range."""

        authz, view, archive_repository = require_view(project_id, archive_id, request)
        filename = str(view["filename"])
        media_type = _viewer_media_type(view)
        if not (_is_pdf(view) or _is_openxml_workbook(view)):
            raise HTTPException(
                status_code=415,
                detail="Inline evidence viewing supports PDF and OpenXML workbooks only",
            )

        size = int(view.get("fileSizeBytes") or 0)
        etag = str(view.get("archiveETag") or f'"{archive_id.lower()}"')
        requested_range = request.headers.get("range")
        if_range = request.headers.get("if-range")
        effective_range = requested_range if not if_range or if_range.strip() == etag else None
        try:
            byte_range = parse_single_byte_range(effective_range, size)
        except UnsatisfiableByteRange as exc:
            audit_recorder(
                request,
                event_type="source_document_view",
                project_id=project_id,
                metadata={
                    "archiveId": archive_id,
                    "filename": filename,
                    "mediaType": media_type,
                    "fileSizeBytes": size,
                    "responseStatus": 416,
                    "rangeRejected": True,
                    "archiveIntegrityStatus": view.get("archiveIntegrityStatus"),
                    "archiveIntegrityBasis": view.get("archiveIntegrityBasis"),
                    "archiveVersionId": view.get("archiveVersionId"),
                    "archiveETag": view.get("archiveETag"),
                    "archiveBackend": view.get("archiveBackend"),
                    "authorization": authz.get("authorization"),
                },
            )
            return Response(
                status_code=416,
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Range": f"bytes */{size}",
                    "Cache-Control": "private, no-store",
                    "ETag": etag,
                    "X-Content-Type-Options": "nosniff",
                },
                content="Requested byte range is not satisfiable.",
                media_type="text/plain",
            )

        start = byte_range.start if byte_range else 0
        end = byte_range.end if byte_range else max(0, size - 1)
        length = byte_range.length if byte_range else size
        status_code = 206 if byte_range else 200
        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "Content-Disposition": _content_disposition(filename, disposition="inline"),
            "Content-Length": str(length),
            "Cross-Origin-Resource-Policy": "same-origin",
            "ETag": etag,
            "X-Content-Type-Options": "nosniff",
        }
        if byte_range:
            headers["Content-Range"] = byte_range.content_range

        content = b""
        if request.method != "HEAD" and length:
            try:
                content = require_view_range(archive_repository, view, start, end)
            except HTTPException:
                audit_recorder(
                    request,
                    event_type="source_document_view",
                    project_id=project_id,
                    metadata={
                        "archiveId": archive_id,
                        "filename": filename,
                        "mediaType": media_type,
                        "fileSizeBytes": size,
                        "responseStatus": 409,
                        "rangeReadFailed": True,
                        "archiveIntegrityStatus": view.get("archiveIntegrityStatus"),
                        "archiveIntegrityBasis": view.get("archiveIntegrityBasis"),
                        "archiveVersionId": view.get("archiveVersionId"),
                        "archiveETag": view.get("archiveETag"),
                        "archiveBackend": view.get("archiveBackend"),
                        "authorization": authz.get("authorization"),
                    },
                )
                raise

        audit_recorder(
            request,
            event_type="source_document_view",
            project_id=project_id,
            metadata={
                "archiveId": archive_id,
                "filename": filename,
                "mediaType": media_type,
                "fileSizeBytes": size,
                "rangeStart": start if byte_range else None,
                "rangeEnd": end if byte_range else None,
                "responseStatus": status_code,
                "archiveIntegrityStatus": view.get("archiveIntegrityStatus"),
                "archiveIntegrityBasis": view.get("archiveIntegrityBasis"),
                "archiveVersionId": view.get("archiveVersionId"),
                "archiveETag": view.get("archiveETag"),
                "archiveBackend": view.get("archiveBackend"),
                "authorization": authz.get("authorization"),
            },
        )

        if request.method == "HEAD":
            return Response(status_code=status_code, media_type=media_type, headers=headers)
        return Response(
            content=content,
            status_code=status_code,
            media_type=media_type,
            headers=headers,
        )

    @router.get("/{project_id}/source-documents/{archive_id}/pdf/pages/{page_number}.png")
    def preview_project_source_pdf_page(
        project_id: str,
        archive_id: str,
        page_number: int,
        request: Request,
        scale: float = Query(1.5, ge=0.5, le=2.0),
    ):
        authz, view, archive_repository = require_view(project_id, archive_id, request)
        if not _is_pdf(view):
            raise HTTPException(status_code=415, detail="Source document is not a PDF")
        try:
            preview = render_pdf_page_preview(
                require_full_view_content(archive_repository, view),
                page_number=page_number,
                scale=scale,
            )
        except IndexError as exc:
            raise HTTPException(status_code=416, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit_recorder(
            request,
            event_type="source_document_pdf_page_view",
            project_id=project_id,
            metadata={
                "archiveId": archive_id,
                "pageNumber": page_number,
                "pageCount": preview["pageCount"],
                "archiveIntegrityStatus": view.get("archiveIntegrityStatus"),
                "archiveIntegrityBasis": view.get("archiveIntegrityBasis"),
                "archiveVersionId": view.get("archiveVersionId"),
                "archiveETag": view.get("archiveETag"),
                "authorization": authz.get("authorization"),
            },
        )
        return Response(
            content=preview["content"],
            media_type="image/png",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": f'inline; filename="page-{page_number}.png"',
                "Cross-Origin-Resource-Policy": "same-origin",
                "X-Content-Type-Options": "nosniff",
                "X-REALYTICS-Coordinate-Space": preview["coordinateSpace"],
                "X-REALYTICS-Page-Count": str(preview["pageCount"]),
                "X-REALYTICS-Page-Height": str(preview["pageHeight"]),
                "X-REALYTICS-Page-Width": str(preview["pageWidth"]),
            },
        )

    @router.get("/{project_id}/source-documents/{archive_id}/workbook/cells")
    def preview_project_source_workbook_cells(
        project_id: str,
        archive_id: str,
        request: Request,
        sheet_name: str = Query(..., min_length=1, max_length=255),
        cell_reference: str = Query(..., min_length=1, max_length=64),
        context_rows: int = Query(3, ge=0, le=10),
        context_columns: int = Query(3, ge=0, le=10),
    ):
        authz, view, archive_repository = require_view(project_id, archive_id, request)
        if not _is_openxml_workbook(view):
            raise HTTPException(status_code=415, detail="Source document is not an OpenXML workbook")
        try:
            preview = build_workbook_cell_preview(
                require_full_view_content(archive_repository, view),
                sheet_name=sheet_name,
                cell_reference=cell_reference,
                context_rows=context_rows,
                context_columns=context_columns,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit_recorder(
            request,
            event_type="source_document_workbook_cell_view",
            project_id=project_id,
            metadata={
                "archiveId": archive_id,
                "sheetName": preview["sheetName"],
                "focusRange": preview["focusRange"],
                "previewRange": preview["previewRange"],
                "archiveIntegrityStatus": view.get("archiveIntegrityStatus"),
                "archiveIntegrityBasis": view.get("archiveIntegrityBasis"),
                "archiveVersionId": view.get("archiveVersionId"),
                "archiveETag": view.get("archiveETag"),
                "authorization": authz.get("authorization"),
            },
        )
        return preview

    @router.get("/{project_id}/source-documents/{archive_id}/download")
    def download_project_source_document(project_id: str, archive_id: str, request: Request):
        authz, download = require_download(project_id, archive_id, request)

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
                "Content-Disposition": _content_disposition(filename, disposition="attachment")
            },
        )

    return router
