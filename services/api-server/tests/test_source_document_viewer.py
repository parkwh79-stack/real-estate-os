from __future__ import annotations

from io import BytesIO
import zipfile
from xml.etree import ElementTree

import fitz
import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.authz import identity_context_from_request
from db.malware_scanner import MalwareScanResult
from db.object_storage import LocalObjectStorage, S3ObjectStorage, sha256_bytes
from db.repositories.source_document_repository import SourceDocumentRepository
from db.repositories.upload_archive_repository import UploadArchiveRepository
from main import app as api_app
from routers import projects
from routers.project_source_documents import (
    _content_disposition,
    build_project_source_documents_router,
)
from storage import create_user_session, upsert_user


class _Scanner:
    def scan(self, content, *, filename=None, content_type=None):
        from datetime import datetime, timezone

        del content, filename, content_type
        return MalwareScanResult(
            status="clean",
            engine="test-scanner",
            scanned_at=datetime.now(timezone.utc),
        )


class _RangeOnlyStorage:
    backend = "s3"

    def __init__(self):
        self.objects = {}
        self.metadata = {}
        self.content_types = {}
        self.range_calls = []
        self.full_get_calls = 0

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
        del retention_until, retention_mode
        self.objects[key] = bytes(content)
        self.metadata[key] = dict(metadata or {})
        self.content_types[key] = content_type
        return {
            "backend": self.backend,
            "key": key,
            "size": len(content),
            "sha256": sha256_bytes(content),
            "contentType": content_type,
        }

    def get(self, key):
        self.full_get_calls += 1
        raise AssertionError(f"full GET must not serve a partial range: {key}")

    def head(self, key):
        content = self.objects[key]
        return {
            "backend": self.backend,
            "key": key,
            "size": len(content),
            "contentType": self.content_types[key],
            "metadata": self.metadata[key],
            "etag": '"range-etag"',
            "versionId": "version-1",
        }

    def get_range(self, key, start, end, *, version_id=None, if_match=None):
        self.range_calls.append((key, start, end, version_id, if_match))
        return self.objects[key][start : end + 1]

    def exists(self, key):
        return key in self.objects

    def delete(self, key):
        existed = key in self.objects
        self.objects.pop(key, None)
        self.metadata.pop(key, None)
        self.content_types.pop(key, None)
        return existed

    def delete_prefix(self, prefix):
        keys = [key for key in self.objects if key.startswith(prefix)]
        for key in keys:
            self.delete(key)
        return bool(keys)

    def local_path(self, key):
        del key
        return None


def _client(
    tmp_path,
    content: bytes,
    filename: str,
    content_type: str,
    *,
    object_storage=None,
):
    archive_repository = UploadArchiveRepository(
        api_server_root=tmp_path,
        archive_root=tmp_path / "uploads",
        object_storage=object_storage,
        malware_scanner=_Scanner(),
    )
    metadata = archive_repository.archive_upload(
        "project-a",
        content,
        filename,
        content_type,
        tenant_id="tenant-a",
    )
    document = {
        **metadata,
        "originalFileName": filename,
        "contentType": content_type,
    }
    audit_events = []
    app = FastAPI()
    app.include_router(
        build_project_source_documents_router(
            project_lookup=lambda project_id, _request: {
                "id": project_id,
                "_meta": {"tenantId": "tenant-a"},
                "source_documents": [document],
            }
            if project_id == "project-a"
            else None,
            source_document_repository_factory=lambda project_id, project: (
                SourceDocumentRepository({project_id: project}, lambda: None)
            ),
            upload_archive_repository_factory=lambda: archive_repository,
            audit_recorder=lambda *args, **kwargs: audit_events.append(kwargs),
        )
    )
    return TestClient(app), metadata["archiveId"], audit_events


def test_normalized_only_project_uses_request_tenant_authority_without_legacy_fallback(
    monkeypatch,
):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "development")
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_ENABLE_LOCAL_AUTH", "true")
    monkeypatch.setenv("REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY", "claims")
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    monkeypatch.setenv("REALYTICS_TENANT_ISOLATION_MODE", "strict")

    owner_tenant = "source-normalized-owner-tenant"
    outsider_tenant = "source-normalized-outsider-tenant"
    owner = upsert_user(
        {
            "userId": "source-normalized-owner",
            "tenantId": owner_tenant,
            "role": "reviewer",
            "status": "active",
        }
    )
    outsider = upsert_user(
        {
            "userId": "source-normalized-outsider",
            "tenantId": outsider_tenant,
            "role": "reviewer",
            "status": "active",
        }
    )
    owner_session = create_user_session(
        {"userId": owner["userId"], "provider": "local-test"}
    )["sessionId"]
    outsider_session = create_user_session(
        {"userId": outsider["userId"], "provider": "local-test"}
    )["sessionId"]

    project_id = "normalized-source-only-project"
    archive_id = "a" * 64
    normalized_project = {
        "id": project_id,
        "project": {"id": project_id, "name": "Normalized source project"},
        "_meta": {"tenantId": owner_tenant, "stateVersion": 7},
        "source_documents": [
            {
                "archiveId": archive_id,
                "originalFileName": "normalized-evidence.pdf",
                "contentType": "application/pdf",
            }
        ],
    }
    projects.PROJECTS_DB.pop(project_id, None)

    class _TenantProjectAuthority:
        def __init__(self, tenant_id: str):
            self.tenant_id = tenant_id

        def get(self, candidate_project_id: str):
            if self.tenant_id == owner_tenant and candidate_project_id == project_id:
                return normalized_project
            return None

    def request_project_repository(request=None):
        assert request is not None
        context = identity_context_from_request(request)
        tenant_id = str((context.get("user") or {}).get("tenantId") or "")
        return _TenantProjectAuthority(tenant_id)

    monkeypatch.setattr(projects, "project_repository", request_project_repository)
    client = TestClient(api_app)

    owner_response = client.get(
        f"/projects/{project_id}/source-documents",
        headers={"x-realytics-session-id": owner_session},
    )
    outsider_response = client.get(
        f"/projects/{project_id}/source-documents",
        headers={"x-realytics-session-id": outsider_session},
    )

    assert project_id not in projects.PROJECTS_DB
    assert owner_response.status_code == 200
    assert owner_response.json()[0]["archiveId"] == archive_id
    assert outsider_response.status_code == 404
    assert outsider_response.json()["detail"] == "Project not found"


def test_authenticated_source_content_supports_single_byte_ranges(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "permissive")
    content = b"0123456789abcdefghij"
    client, archive_id, audit_events = _client(
        tmp_path,
        content,
        "evidence.pdf",
        "application/pdf",
    )
    url = f"/projects/project-a/source-documents/{archive_id}/content"

    partial = client.get(url, headers={"Range": "bytes=3-7"})
    assert partial.status_code == 206
    assert partial.content == b"34567"
    assert partial.headers["accept-ranges"] == "bytes"
    assert partial.headers["content-range"] == f"bytes 3-7/{len(content)}"
    assert partial.headers["content-length"] == "5"
    assert partial.headers["content-disposition"].startswith("inline;")
    assert partial.headers["etag"].startswith('"local-')

    suffix = client.get(url, headers={"Range": "bytes=-4"})
    assert suffix.status_code == 206
    assert suffix.content == b"ghij"

    matching_if_range = client.get(
        url,
        headers={"Range": "bytes=0-1", "If-Range": partial.headers["etag"]},
    )
    assert matching_if_range.status_code == 206
    assert matching_if_range.content == b"01"

    head = client.head(url, headers={"Range": "bytes=0-3"})
    assert head.status_code == 206
    assert head.content == b""
    assert head.headers["content-range"] == f"bytes 0-3/{len(content)}"
    assert head.headers["content-length"] == "4"

    stale_if_range = client.get(
        url,
        headers={"Range": "bytes=0-1", "If-Range": '"stale"'},
    )
    assert stale_if_range.status_code == 200
    assert stale_if_range.content == content
    assert audit_events[0]["event_type"] == "source_document_view"
    assert audit_events[0]["metadata"]["rangeStart"] == 3
    assert audit_events[0]["metadata"]["rangeEnd"] == 7


def test_remote_partial_view_uses_native_range_without_full_object_get(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "permissive")
    storage = _RangeOnlyStorage()
    content = b"0123456789abcdefghij"
    client, archive_id, _ = _client(
        tmp_path,
        content,
        "evidence.pdf",
        "application/pdf",
        object_storage=storage,
    )

    response = client.get(
        f"/projects/project-a/source-documents/{archive_id}/content",
        headers={"Range": "bytes=3-7"},
    )

    assert response.status_code == 206
    assert response.content == b"34567"
    assert storage.full_get_calls == 0
    assert len(storage.range_calls) == 1
    _, start, end, version_id, if_match = storage.range_calls[0]
    assert (start, end, version_id, if_match) == (
        3,
        7,
        "version-1",
        '"range-etag"',
    )


def test_remote_partial_view_rejects_unbound_sha256_head_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "permissive")
    storage = _RangeOnlyStorage()
    client, archive_id, _ = _client(
        tmp_path,
        b"0123456789",
        "evidence.pdf",
        "application/pdf",
        object_storage=storage,
    )
    active_key = next(key for key in storage.objects if "/active/" in key)
    storage.metadata[active_key]["sha256"] = "f" * 64

    response = client.get(
        f"/projects/project-a/source-documents/{archive_id}/content",
        headers={"Range": "bytes=0-1"},
    )

    assert response.status_code == 404
    assert storage.range_calls == []


def test_full_pdf_preview_rehashes_version_bound_range_before_render(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "permissive")
    storage = _RangeOnlyStorage()
    client, archive_id, audit_events = _client(
        tmp_path,
        b"%PDF-original",
        "evidence.pdf",
        "application/pdf",
        object_storage=storage,
    )
    active_key = next(key for key in storage.objects if "/active/" in key)
    storage.objects[active_key] = b"%PDF-tampered"

    response = client.get(
        f"/projects/project-a/source-documents/{archive_id}/pdf/pages/1.png"
    )

    assert response.status_code == 409
    assert len(storage.range_calls) == 1
    _, start, end, version_id, if_match = storage.range_calls[0]
    assert (start, end, version_id, if_match) == (
        0,
        len(b"%PDF-original") - 1,
        "version-1",
        '"range-etag"',
    )
    assert not any(
        event["event_type"] == "source_document_pdf_page_view"
        for event in audit_events
    )


def test_source_content_rejects_ambiguous_or_out_of_bounds_ranges(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "permissive")
    client, archive_id, _ = _client(
        tmp_path,
        b"source-bytes",
        "evidence.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    url = f"/projects/project-a/source-documents/{archive_id}/content"

    multipart = client.get(url, headers={"Range": "bytes=0-1,3-4"})
    assert multipart.status_code == 416
    assert multipart.headers["content-range"] == "bytes */12"

    outside = client.get(url, headers={"Range": "bytes=99-"})
    assert outside.status_code == 416
    assert outside.headers["content-range"] == "bytes */12"

    invalid_archive = client.get(
        "/projects/project-a/source-documents/not-a-sha256/content"
    )
    assert invalid_archive.status_code == 404


def test_local_object_storage_head_and_range_use_actual_object_size(tmp_path):
    storage = LocalObjectStorage(tmp_path)
    storage.put("tenant/project/object.pdf", b"0123456789")
    head = storage.head("tenant/project/object.pdf")

    assert head["size"] == 10
    assert head["etag"].startswith('"local-')
    assert storage.get_range(
        "tenant/project/object.pdf",
        3,
        6,
        if_match=head["etag"],
    ) == b"3456"


def test_s3_object_storage_binds_native_range_to_head_version_and_etag():
    class _Body:
        def read(self):
            return b"3456"

    class _Client:
        def __init__(self):
            self.get_kwargs = None

        def head_object(self, **kwargs):
            assert kwargs == {"Bucket": "evidence", "Key": "prefix/object.pdf"}
            return {
                "ContentLength": 10,
                "ContentType": "application/pdf",
                "Metadata": {"sha256": "a" * 64},
                "ETag": '"etag-1"',
                "VersionId": "version-1",
            }

        def get_object(self, **kwargs):
            self.get_kwargs = kwargs
            return {"Body": _Body(), "ContentRange": "bytes 3-6/10"}

    storage = S3ObjectStorage("evidence", "prefix")
    client = _Client()
    storage._client = client

    head = storage.head("object.pdf")
    content = storage.get_range(
        "object.pdf",
        3,
        6,
        version_id=head["versionId"],
        if_match=head["etag"],
    )

    assert content == b"3456"
    assert client.get_kwargs == {
        "Bucket": "evidence",
        "Key": "prefix/object.pdf",
        "Range": "bytes=3-6",
        "VersionId": "version-1",
        "IfMatch": '"etag-1"',
    }


def test_inline_filename_header_uses_safe_ascii_fallback_and_encoded_utf8():
    disposition = _content_disposition(
        '사업계획서.pdf"\r\nX-Injected: true',
        disposition="inline",
    )
    assert disposition.startswith('inline; filename="source-document.bin";')
    assert "\r" not in disposition and "\n" not in disposition
    assert "%0D%0A" in disposition


def test_pdf_page_preview_returns_real_page_geometry_and_png(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "permissive")
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=500)
    page.insert_text((20, 40), "REALYTICS evidence")
    content = pdf.tobytes()
    pdf.close()
    client, archive_id, audit_events = _client(
        tmp_path,
        content,
        "evidence.pdf",
        "application/pdf",
    )

    response = client.get(
        f"/projects/project-a/source-documents/{archive_id}/pdf/pages/1.png"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG")
    assert float(response.headers["x-realytics-page-width"]) == 300.0
    assert float(response.headers["x-realytics-page-height"]) == 500.0
    assert response.headers["x-realytics-coordinate-space"] == "pdf_points_top_left"
    assert audit_events[-1]["event_type"] == "source_document_pdf_page_view"

    outside = client.get(
        f"/projects/project-a/source-documents/{archive_id}/pdf/pages/2.png"
    )
    assert outside.status_code == 416


def test_workbook_preview_reads_bounded_real_cells_and_marks_focus(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "permissive")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "사업수지"
    sheet["B2"] = "총사업비"
    sheet["C2"] = 125000000
    sheet["C3"] = "=C2*2"
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    client, archive_id, audit_events = _client(
        tmp_path,
        buffer.getvalue(),
        "feasibility.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    response = client.get(
        f"/projects/project-a/source-documents/{archive_id}/workbook/cells",
        params={
            "sheet_name": "사업수지",
            "cell_reference": "C2:C3",
            "context_rows": 0,
            "context_columns": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "realytics-workbook-evidence-preview-v1"
    assert body["sheetName"] == "사업수지"
    assert body["focusRange"] == "C2:C3"
    cells = [cell for row in body["rows"] for cell in row["cells"]]
    assert next(cell for cell in cells if cell["coordinate"] == "C2")["displayValue"] == 125000000
    formula = next(cell for cell in cells if cell["coordinate"] == "C3")
    assert formula["formula"] == "=C2*2"
    assert formula["focused"] is True
    assert body["formulaPolicy"] == "display_only_never_executed"
    assert audit_events[-1]["event_type"] == "source_document_workbook_cell_view"

    missing_sheet = client.get(
        f"/projects/project-a/source-documents/{archive_id}/workbook/cells",
        params={"sheet_name": "없는 시트", "cell_reference": "A1"},
    )
    assert missing_sheet.status_code == 404


@pytest.mark.parametrize("array_formula", [False, True], ids=["ordinary", "array"])
@pytest.mark.parametrize("cached_value", [0, None, "#DIV/0!", 73.5], ids=["zero", "missing", "error", "stale"])
def test_workbook_preview_preserves_formula_text_and_saved_cache(
    tmp_path, monkeypatch, array_formula, cached_value
):
    from openpyxl.worksheet.formula import ArrayFormula

    monkeypatch.setenv("REALYTICS_RBAC_MODE", "permissive")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "근거"
    sheet["A1"], sheet["A2"] = 10, 20
    formula = "=SUM(A1:A2)"
    sheet["B1"] = ArrayFormula(ref="B1", text=formula) if array_formula else formula
    original = BytesIO()
    workbook.save(original)
    workbook.close()

    # openpyxl cannot calculate or write caches; supply a real OOXML cache in
    # this in-memory fixture. Even a stale cache must be shown without evaluation.
    content = BytesIO()
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(content, "w") as target:
        for member in source.infolist():
            data = source.read(member.filename)
            if member.filename == "xl/worksheets/sheet1.xml":
                tree = ElementTree.fromstring(data)
                namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                cell = tree.find(f".//{{{namespace}}}c[@r='B1']")
                cached = cell.find(f"{{{namespace}}}v")
                if cached is not None:
                    cell.remove(cached)
                if cached_value is not None:
                    ElementTree.SubElement(cell, f"{{{namespace}}}v").text = str(cached_value)
                    cell.set("t", "e" if isinstance(cached_value, str) else "n")
                data = ElementTree.tostring(tree, encoding="utf-8", xml_declaration=True)
            target.writestr(member, data)

    client, archive_id, _ = _client(
        tmp_path, content.getvalue(), "formula.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response = client.get(
        f"/projects/project-a/source-documents/{archive_id}/workbook/cells",
        params={"sheet_name": "근거", "cell_reference": "B1", "context_rows": 0, "context_columns": 0},
    )

    assert response.status_code == 200
    body = response.json()
    cell = next(cell for row in body["rows"] for cell in row["cells"] if cell["coordinate"] == "B1")
    assert cell["formula"] == formula
    assert cell["displayValue"] == cached_value
    assert cell["rawValue"] == formula
    assert cell["cachedValue"] == cached_value
    assert cell["valueState"] == ("formula_cache_missing" if cached_value is None else "formula_cache_error" if isinstance(cached_value, str) else "formula_cached")
    assert cell["numberFormat"] == "General"
    assert cell["dataType"] == "f"
    assert cell["focused"] is True
    assert body["formulaPolicy"] == "display_only_never_executed"


def test_workbook_preview_rejects_high_expansion_zip_before_openpyxl(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "permissive")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", b"0" * (2 * 1024 * 1024))
    client, archive_id, audit_events = _client(
        tmp_path,
        buffer.getvalue(),
        "hostile.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    response = client.get(
        f"/projects/project-a/source-documents/{archive_id}/workbook/cells",
        params={"sheet_name": "Sheet1", "cell_reference": "A1"},
    )

    assert response.status_code == 422
    assert "ARCHIVE_EXPANSION_LIMIT_EXCEEDED" in response.json()["detail"]
    assert not any(
        event["event_type"] == "source_document_workbook_cell_view"
        for event in audit_events
    )
