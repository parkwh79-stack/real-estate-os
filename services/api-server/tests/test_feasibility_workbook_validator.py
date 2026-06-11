from pathlib import Path

from openpyxl import Workbook
from openpyxl.workbook.defined_name import DefinedName

from agents.feasibility_workbook_importer import import_feasibility_study_from_workbook
from agents.feasibility_workbook_validator import validate_feasibility_workbook


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


def test_validate_feasibility_workbook_warns_on_ref_formula(tmp_path):
    workbook_path = tmp_path / "sample-broken.xlsx"
    _build_sample_like_workbook(workbook_path, broken_ref=True)

    result = validate_feasibility_workbook(workbook_path)

    assert result["status"] == "warning"
    assert result["formulaIntegrity"]["formulaRefCount"] == 1
    assert "Resolve #REF!" in result["recommendations"][0]


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


def _build_bonri_import_workbook(path: Path) -> None:
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
    input_sheet["D24"] = 42
    input_sheet["D25"] = "2020-05-01"
    input_sheet["D26"] = "2023-10-01"
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
    business["C3"] = "본리동 주상복합 신축공사"
    business["F3"] = "대구광역시 달서구 본리동"
    business["E6"] = 16613.103375
    business["J3"] = 1419.29975
    business["D8"] = 165596965.01177272
    business["D11"] = 165596965.01177272
    business["D18"] = 72036344.5125
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
    assert len(study["product_mix"]) == 3
    assert study["product_mix"][0]["asset_type"] == "apartment"
    assert abs(study["metrics"]["total_revenue"] - 165596965011.7727) < 1
    assert abs(study["metrics"]["total_cost"] - 151841565314.10385) < 1
    assert abs(study["metrics"]["ltc"] - (50000000000 / 151841565314.10385)) < 0.000001
    assert study["raw_import"]["unitScale"]["사업수지"] == "KRW_THOUSAND"


def test_import_realytics_formula_export_workbook_to_feasibility_study_v2(tmp_path):
    workbook_path = tmp_path / "realytics-export.xlsx"
    _build_realytics_formula_export_workbook(workbook_path)

    study = import_feasibility_study_from_workbook(workbook_path, project_id="p2")

    assert study["project_id"] == "p2"
    assert study["metrics"]["total_revenue"] == 165597000000
    assert study["metrics"]["total_cost"] is not None
    assert study["raw_import"]["schemaVersion"] == "realytics-fs-xlsx-v1-draft"
