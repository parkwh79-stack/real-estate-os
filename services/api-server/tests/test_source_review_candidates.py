import hashlib
from io import BytesIO

from openpyxl import Workbook
import pytest

from parsing_platform.adapters import ArchivedDocument
from parsing_platform.runtime_modules import build_parser_v2_runtime_orchestrator
from parsing_platform.source_review_candidates import PROFILE_VERSION, _normalized, source_review_profile
from test_parser_v2_pipeline import MemoryRecords
from use_cases.parser_v2_pipeline import run_structural_parsing_use_case
from use_cases.parser_v2_records import ParserV2RecordStore


def document():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "원본"
    for row in [
        ["사업명", "검토할 실제 프로젝트"],
        ["대지면적(㎡)", 1234],
        ["총사업비(백만원)", 750],
        ["총분양수입", 999],
        ["토지비(원)", "=B3"],
        ["PNU", "1111010100100010000"],
        ["소재지", "서울특별시 서초구 서초동 1303-34"],
    ]:
        sheet.append(row)
    sheet["F12"] = "희소 셀 검증"
    stream = BytesIO()
    workbook.save(stream)
    content = stream.getvalue()
    return ArchivedDocument(
        tenant_id="tenant-1", project_id="project-1", archive_id=hashlib.sha256(content).hexdigest(),
        filename="review.xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        content=content,
    )


def test_archive_profile_emits_real_cell_candidates_without_approval_or_formula_guess():
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)
    original = document()
    kwargs = {
        "document": original, "actor_id": "reviewer", "profile": source_review_profile(project_id="project-1"),
        "orchestrator": build_parser_v2_runtime_orchestrator(), "record_store": store,
        "idempotency_key": "source-review-v1:" + original.archive_id,
    }
    result = run_structural_parsing_use_case(**kwargs)
    assert result["parsingRun"]["executionStatus"] in {"completed", "completed_with_warnings"}, result
    candidates = store.list_candidates("tenant-1", "project-1")
    by_field = {item["canonicalField"]: item for item in candidates}
    assert by_field["project.name"]["normalizedValue"] == "검토할 실제 프로젝트"
    assert by_field["land.area_sqm"]["normalizedValue"] == 1234
    assert by_field["cost.total_project_cost"]["normalizedValue"] == 750000000
    assert by_field["cost.total_project_cost"]["validationStatus"] == "blocked"
    assert "FACT_BASIS_REQUIRED" in by_field["cost.total_project_cost"]["validationCodes"]
    assert "cost.land_cost" not in by_field
    assert "revenue.total_sales_revenue" not in by_field
    for candidate in candidates:
        assert candidate["approvalStatus"] != "approved"
        for locator in candidate["evidence"]:
            assert locator["fileSha256"] == original.sha256
            assert locator["sheetName"] == "원본"
            assert locator["cellReference"]
    repeated = run_structural_parsing_use_case(**kwargs)
    assert repeated["replayed"] is True
    assert len(store.list_candidates("tenant-1", "project-1")) == len(candidates)


def test_units_and_malformed_values_are_not_guessed():
    assert _normalized("총사업비", 100) is None
    assert _normalized("총사업비(억원)", "100 백만원") is None
    assert _normalized("대지면적(원)", 123) is None
    assert _normalized("PNU", "1111010100100010000") == ("land.pnu", "1111010100100010000", None, "parcel:1111010100100010000")
    assert _normalized("소재지", "파일 이름에서 추정한 주소") is None

def test_native_pdf_candidates_keep_exact_page_bbox_and_stop_before_later_pages():
    import fitz

    pdf = fitz.open()
    for index in range(21):
        page = pdf.new_page()
        if index == 0:
            page.insert_text((60, 70), "PNU: 1111010100100010000")
            page.insert_text((60, 100), "대지면적(㎡): 1234", fontname="korea")
        if index == 20:
            page.insert_text((60, 70), "PNU: 1111010100100020000")
    content = pdf.tobytes()
    pdf.close()
    original = ArchivedDocument(
        tenant_id="tenant-1", project_id="project-1", archive_id=hashlib.sha256(content).hexdigest(),
        filename="review.pdf", content_type="application/pdf", content=content,
    )
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)
    result = run_structural_parsing_use_case(
        document=original, actor_id="reader", profile=source_review_profile(project_id="project-1"),
        orchestrator=build_parser_v2_runtime_orchestrator(), record_store=store,
    )
    assert result["parsingRun"]["executionStatus"] == "completed", result
    candidates = store.list_candidates("tenant-1", "project-1")
    pnus = [item for item in candidates if item["canonicalField"] == "land.pnu"]
    assert len(pnus) == 1
    assert pnus[0]["normalizedValue"] == "1111010100100010000"
    for candidate in candidates:
        assert candidate["evidence"][0]["pageNumber"] == 1
        assert len(candidate["evidence"][0]["bbox"]) == 4
        assert candidate["evidence"][0]["rawTextSha256"]
    output = result["orchestration"]["results"]["source_review_candidates"]["output"]
    assert output["summary"]["coverage"] == "partial"


def test_reserved_profile_executes_existing_archive_through_http(monkeypatch):
    from test_project_parser_v2_router import _client

    original = document()
    client, archive_id, _projects, _audits = _client(
        monkeypatch, orchestrator_factory=None, primary_content=original.content,
        primary_filename="review.xlsx",
        primary_media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    payload = {
        "archiveId": archive_id, "profileId": "realytics.source_review_candidates", "profileVersion": PROFILE_VERSION,
        "idempotencyKey": "source-review-v1-" + archive_id,
    }
    first = client.post("/projects/p-1/parsing-v2/runs", json=payload)
    assert first.status_code == 201, first.text
    assert first.json()["candidateCollection"]["candidateCount"] >= 4
    again = client.post("/projects/p-1/parsing-v2/runs", json=payload)
    assert again.status_code == 201, again.text
    assert again.json()["replayed"] is True
    assert again.json()["parsingRun"]["parsingRunId"] == first.json()["parsingRun"]["parsingRunId"]

def test_native_split_labels_inline_notes_and_explicit_dual_units():
    from parsing_platform.source_review_candidates import _pairs

    locator = {"pageNumber": 6, "bbox": [395, 357, 618, 374], "evidenceId": "source"}
    rows = list(_pairs([{
        "kind": "pdf_text_block", "text": "대\n지\n면\n적\n605평(2,000㎡)", "sourceLocator": locator,
    }]))
    assert any(_normalized(label, raw) == ("land.area_sqm", 2000, "sqm", None) for label, raw, _ in rows)
    assert _normalized("· 연면적 ", "28,714㎡ (8,685평)") == ("program.gross_floor_area_sqm", 28714, "sqm", None)
    assert _normalized("위치", "서울시서초구서초동1303-34 (백암빌딩)")[0] == "land.address"
    assert _normalized("연면적", "8,316평") is None

def test_source_review_profile_binds_its_scan_budget_before_serializing_large_workbooks(monkeypatch):
    import parsing_platform.source_review_candidates as source_review
    monkeypatch.setattr(source_review, "MAX_CHUNKS", 2)
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)
    result = run_structural_parsing_use_case(document=document(), actor_id="reviewer",
        profile=source_review_profile(project_id="project-1"), orchestrator=build_parser_v2_runtime_orchestrator(), record_store=store)
    assert result["parsingRun"]["executionStatus"] in {"completed", "completed_with_warnings"}
    artifact = result["parsingRun"]["orchestrationArtifact"]["results"]["structural_extraction"]["output"]["artifact"]
    assert artifact["chunkCount"] == 2
    assert artifact["complete"] is False
    assert artifact["metadata"]["chunkLimit"] == 2
    assert artifact["metadata"]["coverage"] == "partial"
    assert "ADAPTER_CHUNK_LIMIT_REACHED" in artifact["warnings"]
    assert len(store.list_candidates("tenant-1", "project-1")) == 1


def bridge_document(*, title="■ [검증]브릿지론 현황", source_unit="억원", formula_principal=False):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "bridge"
    sheet["A1"] = title
    sheet["F2"] = "(단위: " + source_unit + ")" if source_unit else None
    for column, label in enumerate(["구분", "대출금", "대주", "만기", "금리", "연대보증인", "담보"], 1):
        sheet.cell(3, column, label)
    amounts = [50, 50, 50, 50, 50, 40, 40, 200, 50, 50, 115]
    for index, amount in enumerate(amounts, 4):
        sheet.cell(index, 2, "=25+25" if formula_principal and index == 4 else amount)
        sheet.cell(index, 3, "검증 대주 " + str(index))
    for row, label, rate in [(4, "Tr.A", "변동 7.70 %"), (11, "Tr.B", "변동 8.20%"),
                             (12, "Tr.C", "변동 9.00%"), (14, "Tr.D", "변동 9.50%")]:
        sheet.cell(row, 1, label)
        sheet.cell(row, 5, rate)
    sheet["D4"] = "2025-06-17"
    sheet.merge_cells("D4:D14")
    sheet.merge_cells("E4:E10")
    sheet.merge_cells("E12:E13")
    sheet["B17"] = "=SUM(B4:B14)"
    stream = BytesIO()
    workbook.save(stream)
    content = stream.getvalue()
    return ArchivedDocument(
        tenant_id="tenant-1", project_id="project-1", archive_id=hashlib.sha256(content).hexdigest(),
        filename="bridge.xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        content=content,
    )


def extract_review_candidates(original):
    memory = MemoryRecords()
    store = ParserV2RecordStore(upsert_fn=memory.upsert, list_fn=memory.list)
    result = run_structural_parsing_use_case(document=original, actor_id="reviewer",
        profile=source_review_profile(project_id="project-1"),
        orchestrator=build_parser_v2_runtime_orchestrator(), record_store=store)
    assert result["parsingRun"]["executionStatus"] in {"completed", "completed_with_warnings"}, result
    return result, store.list_candidates("tenant-1", "project-1")


def test_bridge_financial_table_is_source_bound_review_only_and_not_future_pf():
    original = bridge_document()
    original_hash = original.sha256
    result, candidates = extract_review_candidates(original)
    assert len(candidates) == 31
    amounts = [item for item in candidates if item["fieldKey"] == "financing.tranche.loan_amount"]
    assert len(amounts) == 11
    assert sum(item["normalizedValue"] for item in amounts) == 74_500_000_000
    assert all(item["validationStatus"] == "blocked" for item in amounts)
    assert all(item["validationCodes"] == ["FACT_BASIS_REQUIRED"] for item in amounts)
    assert {item["fieldKey"] for item in candidates} == {
        "financing.tranche.loan_amount", "financing.tranche.name", "financing.tranche.interest_rate",
        "financing.tranche.interest_rate_type", "financing.tranche.maturity_date",
    }
    assert len([item for item in candidates if item["fieldKey"] == "financing.tranche.maturity_date"]) == 1
    rates = [item for item in candidates if item["fieldKey"] == "financing.tranche.interest_rate"]
    assert [item["normalizedValue"] for item in rates] == [0.077, 0.082, 0.09, 0.095]
    assert all(item["validationStatus"] == "passed" for item in candidates if item not in amounts)
    for candidate in candidates:
        assert candidate["approvalStatus"] != "approved"
        assert candidate["reviewStatus"] == "pending"
        assert candidate["metadata"]["referenceOnly"] is True
        assert candidate["metadata"]["sourceFinancialContext"] == "current_bridge_snapshot"
        assert candidate["metadata"]["sourceAsOfStatus"] == "unconfirmed"
        assert "basis" not in candidate["metadata"]
        assert not candidate.get("asOfDate")
        for locator in candidate["evidence"]:
            assert locator["fileSha256"] == original_hash
            assert locator["sheetName"] == "bridge"
            assert locator["cellReference"]
            assert "workbookFormula" not in locator
    summary = result["orchestration"]["results"]["source_review_candidates"]["output"]["summary"]
    assert summary["formulaControls"][0]["sourceLocator"]["cellReference"] == "B17"
    assert summary["formulaControls"][0]["formulaSha256"] == hashlib.sha256(b"=SUM(B4:B14)").hexdigest()
    assert "formula" not in summary["formulaControls"][0]
    assert summary["formulaControls"][0]["approvalEligible"] is False
    assert hashlib.sha256(original.content).hexdigest() == original_hash


@pytest.mark.parametrize("kwargs", [{"title": "일반 대출 현황"}, {"source_unit": ""}, {"source_unit": "달러"}])
def test_bridge_table_requires_its_actual_title_headers_and_explicit_currency_unit(kwargs):
    _result, candidates = extract_review_candidates(bridge_document(**kwargs))
    assert candidates == []


def test_bridge_formula_principal_and_aggregate_cache_are_never_promoted():
    _result, candidates = extract_review_candidates(bridge_document(formula_principal=True))
    amounts = [item for item in candidates if item["fieldKey"] == "financing.tranche.loan_amount"]
    assert len(amounts) == 10
    assert all(item["evidence"][-1]["cellReference"] not in {"B4", "B17"} for item in amounts)


@pytest.mark.parametrize("target,version", [("realytics-feasibility-study-v2", "2.0"),
                                           ("realytics-cash-flow-run-request-v1", "1.0")])
def test_even_a_reviewed_bridge_snapshot_has_no_future_pf_or_cashflow_projection(target, version):
    from parsing_platform.field_registry import project_approved_inputs
    from test_parser_v2_field_registry import _approved_snapshot, _financing_basis

    snapshot = _approved_snapshot([{
        "fieldKey": "financing.tranche.loan_amount", "normalizedValue": 5_000_000_000,
        "unit": "krw", "entityId": "source-bridge:literal-row-4",
        "metadata": {"sourceFinancialContext": "current_bridge_snapshot", "referenceOnly": True,
                     **_financing_basis(financingBalanceBasis="outstanding")},
    }])
    with pytest.raises(ValueError, match="no registered projection"):
        project_approved_inputs(target_schema_id=target, target_schema_version=version, approved_snapshot=snapshot)
