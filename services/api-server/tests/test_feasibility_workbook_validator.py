from pathlib import Path

from openpyxl import Workbook

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
