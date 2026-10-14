import copy
import hashlib
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.workbook.defined_name import DefinedName

from agents.feasibility_workbook_importer import import_feasibility_study_from_workbook
from agents.feasibility_workbook_validator import validate_feasibility_workbook
from use_cases.parser_v2_records import ParserV2RecordStore
from use_cases.workbook_parser_v2_bridge import (
    WorkbookParserV2BridgeBlocked,
    bridge_approved_workbook_to_parser_v2,
)


def _build_sample_like_workbook(path: Path, *, broken_ref: bool = False) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name in [
        "입력",
        "분양",
        "제경비",
        "사업수지",
        "1.수지총괄(대외용)",
        "2.현금흐름",
        "검토서",
    ]:
        workbook.create_sheet(sheet_name)

    business = workbook["사업수지"]
    business["C3"] = "Sample Project"
    business["F3"] = "Sample Location"
    business["J3"] = 1000
    business["D8"] = 1000
    business["D11"] = 1000
    business["D18"] = 400
    business["D26"] = 200
    business["D33"] = 100
    business["D41"] = 50
    business["D52"] = 30
    business["D62"] = 20
    business["D71"] = 10
    business["D72"] = 810
    business["D74"] = 190

    workbook["1.수지총괄(대외용)"]["E51"] = 1000
    workbook["검토서"]["E35"] = 190
    if broken_ref:
        workbook["검토서"]["C30"] = "=#REF!*1000"

    workbook.save(path)


def test_validate_feasibility_workbook_passes_core_reconciliations(tmp_path):
    workbook_path = tmp_path / "sample.xlsx"
    _build_sample_like_workbook(workbook_path)

    result = validate_feasibility_workbook(workbook_path)

    assert result["status"] == "passed"
    assert all(check["passed"] for check in result["reconciliationChecks"])
    assert result["schemaReadiness"]["requiresLegacyAdapter"] is True


def test_validate_feasibility_workbook_fails_on_ref_formula(tmp_path):
    workbook_path = tmp_path / "sample-broken.xlsx"
    _build_sample_like_workbook(workbook_path, broken_ref=True)

    result = validate_feasibility_workbook(workbook_path)

    assert result["status"] == "failed"
    assert result["formulaIntegrity"]["formulaRefCount"] == 1
    assert "Resolve #REF!" in result["recommendations"][0]


def test_validate_feasibility_workbook_fails_blank_required_key_cells(tmp_path):
    workbook_path = tmp_path / "blank-required.xlsx"
    _build_sample_like_workbook(workbook_path)
    workbook = load_workbook(workbook_path)
    workbook["사업수지"]["D11"] = None
    workbook.save(workbook_path)
    workbook.close()

    result = validate_feasibility_workbook(workbook_path)

    assert result["status"] == "failed"
    check = next(item for item in result["requiredKeyCellChecks"] if item["key"] == "total_revenue_after_vat")
    assert check["passed"] is False
    assert check["severity"] == "blocking"


def test_validate_feasibility_workbook_rejects_non_numeric_required_cells(tmp_path):
    workbook_path = tmp_path / "non-numeric-required.xlsx"
    _build_sample_like_workbook(workbook_path)
    workbook = load_workbook(workbook_path)
    business = workbook["사업수지"]
    for cell in ("J3", "D8", "D11", "D18", "D26", "D33", "D41", "D52", "D62", "D71", "D72", "D74"):
        business[cell] = "N/A"
    workbook["1.수지총괄(대외용)"]["E51"] = "N/A"
    workbook["검토서"]["E35"] = "N/A"
    workbook.save(workbook_path)
    workbook.close()

    result = validate_feasibility_workbook(workbook_path)

    assert result["status"] == "failed"
    numeric_checks = [check for check in result["requiredKeyCellChecks"] if check["numericRequired"]]
    assert numeric_checks
    assert all(check["passed"] is False for check in numeric_checks)
    assert {check["reason"] for check in numeric_checks} == {"finite_numeric_value_required"}


def test_validate_feasibility_workbook_blocks_cached_excel_errors(tmp_path):
    workbook_path = tmp_path / "cached-error.xlsx"
    _build_sample_like_workbook(workbook_path)
    workbook = load_workbook(workbook_path)
    workbook["사업수지"]["D11"] = "#DIV/0!"
    workbook.save(workbook_path)
    workbook.close()

    result = validate_feasibility_workbook(workbook_path)

    assert result["status"] == "failed"
    assert result["formulaIntegrity"]["cachedErrorCounts"]["#DIV/0!"] == 1
    assert result["formulaIntegrity"]["hasBlockingFormulaErrors"] is True


def _build_realytics_formula_export_workbook(
    path: Path,
    *,
    omit_named_range: str | None = None,
) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name in [
        "00_사용안내",
        "01_입력가정",
        "02A_분양수입",
        "02B_수납스케줄",
        "02_사업수지",
        "03A_공정률",
        "03_월별현금흐름",
        "03B_PF인출상환",
        "03C_Tranche상환",
        "03D_Tranche월별상환",
        "04A_할인민감도",
        "04B_할인Trigger",
        "04_검증",
        "05_수식지도",
        "06_샘플대사",
        "07_Import계약",
    ]:
        workbook.create_sheet(sheet_name)

    workbook["07_Import계약"]["B23"] = "realytics-fs-xlsx-v1-draft"
    workbook["01_입력가정"]["C6"] = 1655.97
    workbook["01_입력가정"]["C14"] = 900
    workbook["01_입력가정"]["C22"] = "Base"
    workbook["01_입력가정"]["C23"] = "sample.xlsx"
    workbook["02_사업수지"]["C13"] = 1518.42
    workbook["03_월별현금흐름"]["C22"] = -450
    workbook["03B_PF인출상환"]["C11"] = 0
    workbook["03C_Tranche상환"]["L9"] = 0
    workbook["04B_할인Trigger"]["C16"] = 1490
    collection = workbook["02B_수납스케줄"]
    collection["A3"] = "상품유형"
    collection["B3"] = "납입단계"
    collection["C3"] = "수납월"
    collection["D3"] = "납입비율"
    collection["E3"] = "상품별 분양수입"
    collection["F3"] = "단계별 수납액"
    for index in range(45):
        collection.cell(3, 7 + index).value = f"M{index + 1}"
    collection["A4"] = "아파트"
    collection["B4"] = "계약금"
    collection["C4"] = 10
    collection["D4"] = 0.1
    collection["E4"] = 1267.3
    collection["F4"] = 126.73
    collection.cell(4, 16).value = 126.73
    collection["A5"] = "아파트"
    collection["B5"] = "잔금"
    collection["C5"] = 42
    collection["D5"] = 0.3
    collection["E5"] = 1267.3
    collection["F5"] = 380.19
    collection.cell(5, 48).value = 380.19
    collection["A6"] = "오피스텔"
    collection["B6"] = "계약금"
    collection["C6"] = 10
    collection["D6"] = 0.1
    collection["E6"] = 245.91
    collection["F6"] = 24.591
    collection.cell(6, 16).value = 24.591
    collection["A7"] = "합계"

    named_ranges = {
        "realytics_fs_schema_version": "'07_Import계약'!$B$23",
        "realytics_source_workbook": "'01_입력가정'!$C$23",
        "fs_total_revenue": "'01_입력가정'!$C$6",
        "fs_total_project_cost": "'02_사업수지'!$C$13",
        "fs_pf_loan": "'01_입력가정'!$C$14",
        "fs_revenue_scenario": "'01_입력가정'!$C$22",
        "cf_monthly_revenue": "'03_월별현금흐름'!$D$7:$AV$7",
        "cf_cumulative_cash_flow": "'03_월별현금흐름'!$D$21:$AV$21",
        "cf_max_minus": "'03_월별현금흐름'!$C$22",
        "pf_ending_balance": "'03B_PF인출상환'!$C$11",
        "pf_tranche_shortfall_total": "'03C_Tranche상환'!$L$9",
        "trigger_adjusted_revenue_total": "'04B_할인Trigger'!$C$16",
    }
    for name, ref in named_ranges.items():
        if name == omit_named_range:
            continue
        workbook.defined_names.add(DefinedName(name, attr_text=ref))

    workbook.save(path)


def test_validate_realytics_formula_export_workbook_is_import_ready(tmp_path):
    workbook_path = tmp_path / "realytics-export.xlsx"
    _build_realytics_formula_export_workbook(workbook_path)

    result = validate_feasibility_workbook(workbook_path)

    assert result["status"] == "passed"
    assert result["profile"] == "realytics-formula-cash-flow-export"
    assert result["schemaReadiness"]["directV2ImportReady"] is True
    assert result["schemaReadiness"]["requiresLegacyAdapter"] is False
    assert result["schemaReadiness"]["missingRequiredNamedRanges"] == []
    assert result["namedRangeValues"]["realytics_fs_schema_version"]["value"] == "realytics-fs-xlsx-v1-draft"


def test_validate_realytics_formula_export_workbook_fails_missing_named_range(tmp_path):
    workbook_path = tmp_path / "realytics-export-missing.xlsx"
    _build_realytics_formula_export_workbook(workbook_path, omit_named_range="fs_total_revenue")

    result = validate_feasibility_workbook(workbook_path)

    assert result["status"] == "failed"
    assert "fs_total_revenue" in result["schemaReadiness"]["missingRequiredNamedRanges"]


def _build_bonri_import_workbook(path: Path, *, legacy_schedule_layout: bool = False) -> None:
    _build_sample_like_workbook(path)
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name in [
        "입력",
        "분양",
        "제경비",
        "사업수지",
        "1.수지총괄(대외용)",
        "2.현금흐름",
        "검토서",
    ]:
        workbook.create_sheet(sheet_name)

    input_sheet = workbook["입력"]
    input_sheet["D2"] = "본리동 주상복합 신축공사"
    input_sheet["E5"] = "대구광역시 달서구 본리동"
    input_sheet["E6"] = "중심상업지역"
    input_sheet["E8"] = 4691.9
    input_sheet["F8"] = 1419.29975
    input_sheet["E22"] = 8.574381720430107
    if legacy_schedule_layout:
        input_sheet["D24"] = 42
        input_sheet["E24"] = "invalid-duration"
        input_sheet["D25"] = "2020-05-01"
        input_sheet["E25"] = "invalid-start-date"
        input_sheet["D26"] = "2023-10-01"
        input_sheet["E26"] = "invalid-completion-date"
    else:
        input_sheet["D24"] = "사업기간:"
        input_sheet["E24"] = 42
        input_sheet["D25"] = "▶착공일:"
        input_sheet["E25"] = "2020-05-01"
        input_sheet["D26"] = "▶준공일:"
        input_sheet["E26"] = "2023-10-01"
    input_sheet["F19"] = 54919.35
    input_sheet["M5"] = 19535.9714
    input_sheet["M6"] = 6457.036
    input_sheet["M7"] = 2022.02
    input_sheet["M8"] = 28015.0274

    sales = workbook["분양"]
    sales["C21"] = 7948.583526
    sales["E21"] = 11639.8615025
    sales["I21"] = 230
    sales["J21"] = 126730000
    sales["C30"] = 2850.3266385
    sales["E30"] = 4145.2085015
    sales["I30"] = 76
    sales["J30"] = 24591098.909090906
    sales["I32"] = 1
    sales["I33"] = 1
    sales["C40"] = 611.66105
    sales["E40"] = 828.0331895
    sales["J40"] = 14275866.102681817

    business = workbook["사업수지"]
    business["A2"] = "(단위 : 천원, VAT 별도)"
    business["C3"] = "본리동 주상복합 신축공사"
    business["F3"] = "대구광역시 달서구 본리동"
    business["E6"] = 16613.103375
    business["J3"] = 1419.29975
    business["D8"] = 165596965.01177272
    business["D11"] = 165596965.01177272
    business["D18"] = 72036344.5125
    business["D19"] = 42000000
    business["D26"] = 46032000
    business["D33"] = 3561168
    business["D41"] = 6318199.465631289
    business["D52"] = 2653950.4620283353
    business["D62"] = 16298073.468263712
    business["D71"] = 4941829.4056805
    business["D72"] = 151841565.31410384
    business["D74"] = 13755399.69766888

    workbook["1.수지총괄(대외용)"]["E51"] = 165596965.01177272
    workbook["검토서"]["E35"] = 13755399.69766888

    cash_flow = workbook["2.현금흐름"]
    cash_flow["E48"] = 50000
    cash_flow["E55"] = 5000
    cash_flow["F4"] = "토지계약"
    cash_flow["F55"] = 5000
    cash_flow["F58"] = 5267.49
    cash_flow["F122"] = -267.49
    cash_flow["F130"] = -267.49

    workbook.save(path)


def test_import_legacy_bonri_workbook_to_feasibility_study_v2(tmp_path):
    workbook_path = tmp_path / "bonri-import.xlsx"
    _build_bonri_import_workbook(workbook_path)

    study = import_feasibility_study_from_workbook(workbook_path, project_id="p1")

    assert study["version"] == "realytics-feasibility-study-v2"
    assert study["project_name"] == "본리동 주상복합 신축공사"
    assert study["asset_type"] == "mixed_use"
    assert study["site"]["land_area_sqm"]["value"] == 4691.9
    assert study["site"]["legal_far"] is None
    assert study["raw_import"]["designFar"]["value"] == pytest.approx(857.4381720430107)
    assert study["raw_import"]["designFar"]["excel"]["cell"] == "E22"
    assert "site.legal_far" not in study["raw_import"]["sampleProfile"]["fieldLocators"]
    assert study["raw_import"]["landCostBreakdown"]["purchaseBudget"]["value"] == 42000000000
    assert study["raw_import"]["landCostBreakdown"]["inclusiveBudget"]["value"] == 46032000000
    assert len([row for row in study["cost_items"] if row["category"] == "land"]) == 1
    assert len(study["product_mix"]) == 3
    assert study["product_mix"][0]["asset_type"] == "apartment"
    assert abs(study["metrics"]["total_revenue"] - 165596965011.7727) < 1
    assert abs(study["metrics"]["total_cost"] - 151841565314.10385) < 1
    assert abs(study["metrics"]["ltc"] - (50000000000 / 151841565314.10385)) < 0.000001
    assert study["raw_import"]["unitScale"]["사업수지"] == "KRW_THOUSAND"
    assert study["raw_import"]["governedFactBasis"] == {
        "revenue.total_sales_revenue": {
            "vatTreatment": "excluded",
            "amountScope": "gross",
            "allocationBasis": "direct",
            "currency": "KRW",
        },
        "cost.total_project_cost": {
            "vatTreatment": "excluded",
            "amountScope": "gross",
            "allocationBasis": "mixed",
            "allocationMethod": "sum_of_profile_cost_categories",
            "currency": "KRW",
        },
        "financing.pf_amount": {
            "vatTreatment": "not_applicable",
            "amountScope": "not_applicable",
            "allocationBasis": "not_applicable",
            "financingBalanceBasis": "commitment",
            "currency": "KRW",
        },
    }
    assert (
        study["raw_import"]["governedFactBasisAttestation"]["status"]
        == "attested"
    )
    assert (
        study["raw_import"]["governedFactBasisCoverage"]["approvalEligible"]
        is True
    )
    assert study["schedule"][0]["planned_date"] == "2020-05-01"
    assert study["schedule"][0]["excel"]["cell"] == "E25"
    assert study["schedule"][0]["provenance"]["cell"] == "E25"
    assert study["schedule"][1]["planned_date"] == "2023-10-01"
    assert study["schedule"][1]["month_index"] == 42
    assert study["schedule"][1]["excel"]["cell"] == "E26"
    assert study["schedule"][1]["provenance"]["cell"] == "E26"
    first_event = study["monthly_cash_flow"][0]
    assert first_event["period"] == "토지계약"
    assert first_event["net_cash_flow"] == -267_490_000
    assert first_event["source_locator"]["sourceUnit"] == "KRW_MILLION"
    assert first_event["source_locator"]["normalizedUnit"] == "KRW"
    assert first_event["source_locator"]["currentCashCheck"]["differenceKrw"] == 0
    assert not any("row_120" in key or "row_130" in key for key in first_event["cash_in"] | first_event["cash_out"])


def test_import_legacy_bonri_workbook_falls_back_to_inline_schedule_dates(tmp_path):
    workbook_path = tmp_path / "bonri-import-legacy-schedule.xlsx"
    _build_bonri_import_workbook(workbook_path, legacy_schedule_layout=True)

    study = import_feasibility_study_from_workbook(workbook_path, project_id="p-legacy")

    assert study["schedule"][0]["planned_date"] == "2020-05-01"
    assert study["schedule"][0]["excel"]["cell"] == "D25"
    assert study["schedule"][0]["provenance"]["cell"] == "D25"
    assert study["schedule"][1]["planned_date"] == "2023-10-01"
    assert study["schedule"][1]["month_index"] == 42
    assert study["schedule"][1]["excel"]["cell"] == "D26"
    assert study["schedule"][1]["provenance"]["cell"] == "D26"


def test_import_realytics_formula_export_workbook_to_feasibility_study_v2(tmp_path):
    workbook_path = tmp_path / "realytics-export.xlsx"
    _build_realytics_formula_export_workbook(workbook_path)

    study = import_feasibility_study_from_workbook(workbook_path, project_id="p2")

    assert study["project_id"] == "p2"
    assert study["metrics"]["total_revenue"] == 165597000000
    assert study["metrics"]["total_cost"] is not None
    assert study["raw_import"]["schemaVersion"] == "realytics-fs-xlsx-v1-draft"
    assert (
        study["raw_import"]["governedFactBasis"][
            "revenue.total_sales_revenue"
        ]["vatTreatment"]
        == "excluded"
    )
    assert (
        study["raw_import"]["governedFactBasis"][
            "financing.pf_amount"
        ]["financingBalanceBasis"]
        == "commitment"
    )
    assert study["raw_import"]["mappedCells"] == {
        "totalRevenue": "'01_입력가정'!C6",
        "totalCost": "'02_사업수지'!C13",
        "pfAmount": "'01_입력가정'!C14",
    }
    scenarios = study["raw_import"]["productMonthlySalesRateScenarios"]
    assert scenarios[0]["scenarioId"] == "base_product_collection_schedule"
    assert scenarios[0]["sourceLocator"]["sheetName"] == "02B_수납스케줄"
    assert scenarios[0]["products"][0]["productType"] == "아파트"
    assert scenarios[0]["products"][0]["collectionSteps"][0]["dueMonth"] == 10
    assert scenarios[0]["products"][0]["monthlyCollectionAmounts"][9] == 12673000000


def _build_yongin_public_rental_workbook(path: Path) -> None:
    workbook = Workbook()
    profit = workbook.active
    profit.title = "손익"
    overview = workbook.create_sheet("개요수입")
    cash = workbook.create_sheet("cash")
    workbook.create_sheet("임대료")

    profit["A1"] = "Yongin public rental"
    profit["I24"] = 850_000
    profit["I81"] = 780_000
    profit["I82"] = 70_000
    overview["D2"] = 10_000
    overview["D8"] = "public rental district"
    overview["D15"] = 20_000
    overview["B26"] = 100
    cash["D34"] = 350_000
    workbook.save(path)


def test_yongin_profile_does_not_infer_unattested_monetary_basis(tmp_path):
    workbook_path = tmp_path / "yongin-public-rental.xlsx"
    _build_yongin_public_rental_workbook(workbook_path)

    study = import_feasibility_study_from_workbook(
        workbook_path,
        project_id="p-yongin",
    )

    raw_import = study["raw_import"]
    assert raw_import["profile"] == "yongin-public-rental-feasibility"
    assert "governedFactBasis" not in raw_import
    assert raw_import["governedFactBasisCoverage"]["status"] == (
        "insufficient_data"
    )
    assert raw_import["governedFactBasisCoverage"]["approvalEligible"] is False
    assert raw_import["governedFactBasisCoverage"]["reportEligible"] is False
    assert "vatTreatment" in raw_import["governedFactBasisCoverage"][
        "missingEvidence"
    ]


def test_yongin_does_not_sum_dates_subtotals_or_invent_peak_funding(tmp_path):
    path = tmp_path / "yongin.xlsx"
    _build_yongin_public_rental_workbook(path)
    from openpyxl import load_workbook
    workbook = load_workbook(path)
    workbook['cash']['E1'] = 2027
    workbook['cash']['E8'] = 1000
    workbook['cash']['E9'] = 1000  # a subtotal is not another transaction
    workbook.save(path)
    workbook.close()
    study = import_feasibility_study_from_workbook(path)
    assert study['metrics']['total_revenue'] == 850_000_000
    assert study['monthly_cash_flow'] == []
    assert study['schedule'] == []
    for key in ('max_minus', 'required_equity', 'ltv', 'exit_sale_rate'):
        assert study['metrics'][key] is None
    workbook = load_workbook(path)
    workbook['손익']['A3'] = '아파트 착공 2030년8월 / 공기 40개월'
    workbook.save(path)
    workbook.close()
    assert import_feasibility_study_from_workbook(path)['schedule'][0]['planned_date'] == '2030-08'


class _ParserRecordMemory:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, dict]] = {}

    def upsert(self, project_id: str, record: dict) -> dict:
        saved = copy.deepcopy(record)
        saved.setdefault("projectId", project_id)
        saved.setdefault("updatedAt", saved.get("createdAt"))
        self.rows.setdefault(project_id, {})[saved["recordId"]] = saved
        return copy.deepcopy(saved)

    def list(self, project_id: str, **filters) -> list[dict]:
        records = list(self.rows.get(project_id, {}).values())
        record_type = filters.get("record_type")
        if record_type:
            records = [
                item
                for item in records
                if item.get("recordType") == record_type
            ]
        return copy.deepcopy(records[: int(filters.get("limit") or 1000)])


def test_controlled_realytics_import_without_gfa_blocks_parser_v2_approval(
    tmp_path,
):
    workbook_path = tmp_path / "realytics-approved.xlsx"
    _build_realytics_formula_export_workbook(workbook_path)
    source_hash = hashlib.sha256(workbook_path.read_bytes()).hexdigest()
    source_document = {
        "archiveId": source_hash,
        "originalFileHash": source_hash,
        "sha256": source_hash,
        "archiveStored": True,
        "archiveIntegrityStatus": "verified",
        "archiveObjectKey": f"p-approved/{source_hash}.xlsx",
        "documentType": "feasibility_workbook",
        "validationStatus": "passed",
        "originalFileName": workbook_path.name,
        "contentType": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        "fileSizeBytes": workbook_path.stat().st_size,
    }
    study = import_feasibility_study_from_workbook(
        workbook_path,
        project_id="p-approved",
        source_document=source_document,
    )
    study["raw_import"]["canonicalStatus"] = "approved"
    study["raw_import"]["candidateImport"] = {
        "importId": "realytics-import-approved",
        "recordedBy": "workbook-preparer",
        "recordedAt": "2026-07-17T00:00:00Z",
        "status": "reviewed",
        "draftHash": "e" * 64,
        "sourceDocumentHashes": [source_hash],
    }
    study["raw_import"]["approval"] = {
        "importId": "realytics-import-approved",
        "candidateImportId": "realytics-import-approved",
        "preparedBy": "workbook-preparer",
        "approvedBy": "credit-officer",
        "approvedAt": "2026-07-18T00:00:00Z",
        "sourceDocument": source_document,
    }
    backend = _ParserRecordMemory()
    store = ParserV2RecordStore(
        upsert_fn=backend.upsert,
        list_fn=backend.list,
    )

    with pytest.raises(
        WorkbookParserV2BridgeBlocked,
        match="all four governed workbook core facts",
    ) as raised:
        bridge_approved_workbook_to_parser_v2(
            record_store=store,
            tenant_id="tenant-approved",
            project_id="p-approved",
            import_id="realytics-import-approved",
            canonical_study=study,
            source_document=source_document,
            approved_by="credit-officer",
        )

    assert {
        (item["fieldKey"], item["status"])
        for item in raised.value.validation_issues
    } == {("program.gross_floor_area_sqm", "missing_value")}
    assert backend.rows == {}
