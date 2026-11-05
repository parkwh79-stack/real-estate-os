from __future__ import annotations

import asyncio
import copy
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from openpyxl import Workbook

from agents.document_classifier_agent import classify_document
from agents.feasibility_workbook_validator import validate_feasibility_workbook
from agents.land_ledger_agent import LandLedgerAgent
from routers import projects


def workbook_bytes(*, debt=False):
    workbook = Workbook()
    sheet = workbook.active
    if debt:
        sheet.append(["■ [상봉]브릿지론 현황"])
        sheet.append([None, None, None, None, None, "(단위: 억원)"])
        sheet.append(["구분", "대출금", "대주", "만기", "금리", "연대보증인", "담보"])
        sheet.append(["Tr.A", 50, "검증 대주", "2025-06-17", "변동 7.70 %", None, "담보신탁 우선수익권"])
    else:
        sheet.title = "0. 개요입력"
        sheet["B5"] = "대지위치"
        sheet["C5"] = "서울시 중랑구 상봉동 107-6 외 2필지"
        workbook.create_sheet("1. 사업수지")["A1"] = "다른 사업수지 셀 배치"
        workbook.create_sheet("2. 캐쉬_분양률100%")["A1"] = "=SUM(#REF!)"
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def test_debt_title_requires_actual_debt_headers_and_never_claims_land_or_signed_contract():
    name = "1. [상봉]브릿지론 현황(담보제공현황 포함)_250113.xlsx"
    result = classify_document(name, sheet_names=["sheet"],
        sample_text="구분 대출금 대주 만기 금리 연대보증인 담보", detected_format="xlsx")
    assert result["documentType"] == "unknown"
    assert result["processingMode"] == "document_review"
    assert result["processingTypes"] == []
    assert result["needsReview"] is True
    assert "금융구조·대출" in result["topics"]
    assert "processingMode" not in classify_document(name, sample_text="담보", detected_format="xlsx")
    ledger = classify_document("토지조서.xlsx", sample_text="소유자 지번 면적 담보", detected_format="xlsx")
    assert ledger["documentType"] == "land_ledger"
    assert "processingMode" not in ledger


def test_unknown_workbook_layout_is_mapping_required_not_bonri_or_formula_pass(tmp_path):
    path = tmp_path / "sangbong.xlsx"
    path.write_bytes(workbook_bytes())
    result = validate_feasibility_workbook(path)
    assert result["status"] == "needs_mapping"
    assert result["profile"] == "unsupported-workbook-layout"
    assert result["templateSupport"]["status"] == "unsupported_layout"
    assert result["schemaReadiness"]["directV2ImportReady"] is False
    assert result["formulaIntegrity"] == {"status": "not_evaluated"}
    assert result["calculationEvidence"]["recalculated"] is False
    assert result["diagnostics"][0]["code"] == "unsupported_workbook_layout"
    assert [row["sheetName"] for row in result["sheets"]] == ["0. 개요입력", "1. 사업수지", "2. 캐쉬_분양률100%"]


def test_zero_parcel_workbook_is_not_review_ready_and_cannot_replace_existing_ledger(tmp_path, monkeypatch):
    path = tmp_path / "bridge.xlsx"
    path.write_bytes(workbook_bytes(debt=True))
    empty = LandLedgerAgent().analyze_workbook(path)
    assert empty["parcels"] == []
    assert empty["validation"]["status"] == "needs_review"
    assert empty["validation"]["highestSeverity"] == 3
    assert empty["validation"]["issues"][0]["code"] == "no_parcels_detected"
    empty["upload"] = {"archiveId": "a" * 64}
    original = {"landLedger": {"parcels": [{"pnu": "1111010100100010000"}]},
                "land_acquisition": [{"pnu": "1111010100100010000"}]}
    before = copy.deepcopy(original)
    saved = []
    async def parse(*_):
        return empty
    monkeypatch.setattr(projects, "_project_store", lambda: {"p1": original})
    monkeypatch.setattr(projects, "parse_uploaded_land_ledger", parse)
    monkeypatch.setattr(projects, "source_document_repository", lambda: object())
    monkeypatch.setattr(projects, "save_source_document_metadata_use_case", lambda *args: saved.append(args[-1]))
    def no_project_write():
        raise AssertionError("No ledger, land acquisition, enrichment or reanalysis write is allowed")
    monkeypatch.setattr(projects, "project_repository", no_project_write)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(projects.analyze_land_ledger_upload("p1", UploadFile(filename="bridge.xlsx", file=BytesIO())))
    assert caught.value.status_code == 422
    assert caught.value.detail["error"] == "land_ledger_no_parcels"
    assert caught.value.detail["persisted"] is False
    assert caught.value.detail["archiveId"] == "a" * 64
    assert original == before
    assert saved == [empty["upload"]]


def test_unsupported_upload_keeps_archive_diagnostics_without_financial_import(monkeypatch):
    content = workbook_bytes()
    digest = __import__("hashlib").sha256(content).hexdigest()
    monkeypatch.setattr(projects, "upload_archive_repository", lambda: object())
    monkeypatch.setattr(projects, "_project_tenant_id", lambda *_: "tenant-a")
    monkeypatch.setattr(projects, "archive_source_document_use_case", lambda *a, **k:
        {"archiveId": digest, "archiveStored": True, "downloadUrl": "/projects/p1/source-archive/" + digest})
    def no_import(*a, **k):
        raise AssertionError("Unsupported layouts must not invoke a coordinate-based financial importer")
    monkeypatch.setattr(projects, "import_feasibility_study_from_workbook", no_import)
    result = asyncio.run(projects.validate_uploaded_feasibility_workbook("p1",
        UploadFile(filename="sangbong.xlsx", file=BytesIO(content))))
    assert result["validation"]["status"] == "needs_mapping"
    assert result["validation"]["diagnostics"][0]["code"] == "unsupported_workbook_layout"
    assert result["feasibilityStudyV2Draft"] is None
    assert result["sourceReview"]["archiveId"] == digest
    assert result["sourceReview"]["projectId"] == "p1"
    assert result["sourceReview"]["candidateOnly"] is True
    assert result["sourceReview"]["authoritative"] is False


def test_archive_security_failure_cannot_become_candidate_review(monkeypatch):
    monkeypatch.setattr(projects, "upload_archive_repository", lambda: object())
    monkeypatch.setattr(projects, "_project_tenant_id", lambda *_: "tenant-a")
    def denied(*a, **k):
        raise HTTPException(status_code=403, detail={"error": "archive_permission_denied"})
    monkeypatch.setattr(projects, "archive_source_document_use_case", denied)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(projects.validate_uploaded_feasibility_workbook("p1",
            UploadFile(filename="sangbong.xlsx", file=BytesIO(workbook_bytes()))))
    assert caught.value.status_code == 403


def test_corrupt_workbook_is_read_failure_without_source_review(monkeypatch):
    monkeypatch.setattr(projects, "upload_archive_repository", lambda: object())
    monkeypatch.setattr(projects, "_project_tenant_id", lambda *_: "tenant-a")
    monkeypatch.setattr(projects, "archive_source_document_use_case", lambda *a, **k:
        {"archiveId": "a" * 64, "archiveStored": True})
    result = asyncio.run(projects.validate_uploaded_feasibility_workbook("p1",
        UploadFile(filename="bad.xlsx", file=BytesIO(b"PK\x03\x04invalid"))))
    assert result["validation"]["status"] == "parse_failed"
    assert result["validation"]["errorCode"] == "workbook_read_or_import_failed"
    assert "sourceReview" not in result
    assert result["feasibilityStudyV2Draft"] is None
