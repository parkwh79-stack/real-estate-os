from datetime import date
from io import BytesIO
import random
import zipfile

import openpyxl
import pytest

from storage import create_user_session, upsert_user
from test_source_document_viewer import _client, _RangeOnlyStorage
from use_cases.source_document_viewer import build_workbook_cell_preview, build_workbook_sheet_preview


def _book():
    book = openpyxl.Workbook()
    hidden = book.active
    hidden.title, hidden.sheet_state = "숨김 안내", "hidden"
    hidden["A1"] = "숨김 시트 원문"
    sheet = book.create_sheet("브리지 원본")
    sheet["E5"], sheet["F6"], sheet["G7"] = "대출 현황", "(단위: 억원)", 0
    sheet["H8"] = date(2025, 6, 17)
    sheet["H8"].number_format = "yyyy-mm-dd"
    sheet["G8"] = "=SUM(G7:G7)"
    sheet["G8"].number_format = "#,##0"
    book.create_sheet("빈 시트")
    formatted = book.create_sheet("서식 범위가 큰 시트")
    formatted["A1"] = "원본"
    formatted["XFD1048576"].number_format = "0.00"
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def _url(archive_id, endpoint="sheets"):
    return f"/projects/project-a/source-documents/{archive_id}/workbook/{endpoint}"


def test_original_preview_needs_no_parser_candidates_and_preserves_units_dates_and_missing_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "permissive")
    storage = _RangeOnlyStorage()
    client, archive_id, audits = _client(tmp_path, _book(), "bridge.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", object_storage=storage)
    # This project contains only the archive metadata, no Parser document,
    # candidate, approved input, or source locator records.
    metadata = client.get(_url(archive_id))
    assert metadata.status_code == 200
    source = metadata.json()
    assert source["archiveId"] == source["sourceSha256"] == archive_id
    assert source["archiveVersionId"] == "version-1"
    assert source["archiveETag"] == '"range-etag"'
    assert source["archiveIntegrityStatus"] == "verified"
    assert source["analysisOnly"] is True and source["approvalEligible"] is False
    assert metadata.headers["cache-control"] == "private, no-store"
    assert source["defaultSheetName"] == "브리지 원본" and source["defaultRange"] == "E5:H8"
    assert [sheet["sheetName"] for sheet in source["sheets"]] == ["숨김 안내", "브리지 원본", "빈 시트", "서식 범위가 큰 시트"]
    large = source["sheets"][-1]
    assert large["declaredRowCount"] == 1048576 and large["declaredColumnCount"] == 16384
    assert large["rowCount"] == 10000 and large["columnCount"] == 512 and large["dimensionsTruncated"]
    assert source["truncated"] is True
    response = client.get(_url(archive_id, "cells"), params={"sheet_name": source["defaultSheetName"], "cell_reference": source["defaultRange"], "context_rows": 0, "context_columns": 0})
    assert response.status_code == 200
    preview = response.json()
    cells = {cell["coordinate"]: cell for row in preview["rows"] for cell in row["cells"]}
    assert cells["F6"]["displayValue"] == "(단위: 억원)"
    assert cells["G7"]["rawValue"] == cells["G7"]["displayValue"] == 0
    assert cells["G7"]["valueState"] == "literal"
    assert cells["H8"]["displayValue"] == "2025-06-17T00:00:00"
    assert cells["H8"]["numberFormat"] == "yyyy-mm-dd"
    assert cells["G8"]["formula"] == cells["G8"]["rawValue"] == "=SUM(G7:G7)"
    assert cells["G8"]["cachedValue"] is None and cells["G8"]["valueState"] == "formula_cache_missing"
    assert cells["G8"]["numberFormat"] == "#,##0"
    assert preview["sourceSha256"] == archive_id and preview["approvalEligible"] is False
    assert storage.full_get_calls == 0
    assert all(call[3:] == ("version-1", '"range-etag"') for call in storage.range_calls)
    assert [event["event_type"] for event in audits] == ["source_document_workbook_sheet_view", "source_document_workbook_cell_view"]


@pytest.mark.parametrize("endpoint", ["sheets", "cells"])
def test_workbook_original_preview_requires_evidence_permission_before_any_source_read(tmp_path, monkeypatch, endpoint):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    storage = _RangeOnlyStorage()
    client, archive_id, _ = _client(tmp_path, _book(), "bridge.xlsx", "application/octet-stream", object_storage=storage)
    params = {"sheet_name": "브리지 원본", "cell_reference": "E5", "context_rows": 0, "context_columns": 0} if endpoint == "cells" else {}
    denied = client.get(_url(archive_id, endpoint), params=params)
    assert denied.status_code == 403
    assert denied.json()["detail"]["requiredPermission"] == "evidence:read"
    assert storage.range_calls == []


def test_original_preview_enforces_project_tenant_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    monkeypatch.setenv("REALYTICS_TENANT_ISOLATION_MODE", "strict")
    client, archive_id, _ = _client(tmp_path, _book(), "bridge.xlsx", "application/octet-stream")
    for tenant, expected in [("tenant-a", 200), ("tenant-b", 403)]:
        user_id = f"workbook-original-{tenant}"
        upsert_user({"userId": user_id, "role": "analyst", "tenantId": tenant, "status": "active"})
        session = create_user_session({"userId": user_id, "provider": "local-test"})["sessionId"]
        response = client.get(_url(archive_id), headers={"x-realytics-session-id": session})
        assert response.status_code == expected
        if expected == 403:
            assert response.json()["detail"]["error"] == "tenant_scope_mismatch"


@pytest.mark.parametrize("fault,status", [("content_hash", 409), ("generation", 409), ("head_hash", 404), ("quarantine", 404)])
def test_original_preview_cannot_read_changed_or_quarantined_source(tmp_path, monkeypatch, fault, status):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "permissive")
    storage = _RangeOnlyStorage()
    client, archive_id, audits = _client(tmp_path, _book(), "bridge.xlsx", "application/octet-stream", object_storage=storage)
    active_key = next(key for key in storage.objects if "/active/" in key)
    if fault == "content_hash":
        stored = storage.objects[active_key]
        storage.objects[active_key] = bytes([stored[0] ^ 1]) + stored[1:]
    elif fault == "generation":
        def changed(*_args, **_kwargs):
            raise RuntimeError("generation changed after HEAD")
        storage.get_range = changed
    elif fault == "head_hash":
        storage.metadata[active_key]["sha256"] = "f" * 64
    else:
        storage.metadata[active_key]["lifecycle-status"] = "quarantine"
    response = client.get(_url(archive_id))
    assert response.status_code == status
    assert not audits


@pytest.mark.parametrize("reference", ["A:T", "1:20", "A1:U20", "A10001", "SS1", "D4:A1"])
def test_unbounded_or_oversized_cell_requests_are_rejected(reference):
    with pytest.raises(ValueError):
        build_workbook_cell_preview(_book(), sheet_name="브리지 원본", cell_reference=reference, context_rows=0, context_columns=0)


def test_sparse_sheets_do_not_invent_a_default_source_location():
    book = openpyxl.Workbook()
    book.active["A500"] = "탐색 범위 밖 원본"
    output = BytesIO()
    book.save(output)
    book.close()
    preview = build_workbook_sheet_preview(output.getvalue())
    assert preview["defaultRange"] is None
    assert any("200행" in warning for warning in preview["warnings"])
    window = build_workbook_cell_preview(output.getvalue(), sheet_name="Sheet", cell_reference="A500", context_rows=0, context_columns=0)
    assert window["rows"][0]["cells"][0]["displayValue"] == "탐색 범위 밖 원본"


def test_preview_clips_long_text_and_response_size_without_changing_source():
    randomizer = random.Random(713)
    alphabet = [chr(0x1F300 + index) for index in range(100)]
    book = openpyxl.Workbook()
    for row in book.active.iter_rows(min_row=1, max_row=20, min_col=1, max_col=20):
        for cell in row:
            cell.value = "".join(randomizer.choices(alphabet, k=1400))
    output = BytesIO()
    book.save(output)
    book.close()
    content = output.getvalue()
    preview = build_workbook_cell_preview(content, sheet_name="Sheet", cell_reference="A1:T20", context_rows=0, context_columns=0)
    import json
    assert len(json.dumps(preview, ensure_ascii=False).encode()) < 3 * 1024 * 1024
    assert preview["truncated"] and any("잘렸습니다" in warning for warning in preview["warnings"])
    assert all(cell["valueTruncated"] for row in preview["rows"] for cell in row["cells"])
    assert output.getvalue() == content


def test_sheet_list_blocks_macros_before_opening_and_does_not_resolve_external_links(monkeypatch):
    original = _book()
    with zipfile.ZipFile(BytesIO(original)) as source, zipfile.ZipFile(output := BytesIO(), "w") as target:
        for entry in source.infolist():
            target.writestr(entry, source.read(entry.filename))
        target.writestr("xl/vbaProject.bin", b"never execute")
    def must_not_open(*_args, **_kwargs):
        raise AssertionError("macro container must not reach the reader")
    monkeypatch.setattr(openpyxl, "load_workbook", must_not_open)
    with pytest.raises(ValueError, match="VBA_PROJECT_PRESENT"):
        build_workbook_sheet_preview(output.getvalue())


def test_final_response_bound_also_covers_large_numeric_values(monkeypatch):
    import use_cases.source_document_viewer as viewer
    # The limit is a final encoded JSON bound, including non-string values.
    monkeypatch.setattr(viewer, "MAX_WORKBOOK_PREVIEW_BYTES", 20)
    with pytest.raises(ValueError, match="select a smaller range"):
        build_workbook_cell_preview(_book(), sheet_name="브리지 원본", cell_reference="G7", context_rows=0, context_columns=0)
