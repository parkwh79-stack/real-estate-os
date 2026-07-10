from pathlib import Path

import pytest

from agents.feasibility_workbook_importer import import_feasibility_study_from_workbook
from agents.feasibility_workbook_validator import validate_feasibility_workbook


SAMPLE_DIR = Path(__file__).resolve().parents[4] / "sample"


@pytest.mark.skipif(not list(SAMPLE_DIR.glob("*.xlsx")), reason="workspace sample workbook is not available in CI")
def test_real_bonri_sample_import_uses_formula_profile_and_cell_provenance():
    workbook_path = next(SAMPLE_DIR.glob("*.xlsx"))
    validation = validate_feasibility_workbook(workbook_path)
    assert validation["profile"] == "legacy-bonri-mixed-use-pf-feasibility"
    assert validation["status"] in {"valid", "warning"}

    study = import_feasibility_study_from_workbook(workbook_path, project_id="sample-bonri")
    assert study["metrics"]["total_revenue"] > 0
    assert study["metrics"]["total_cost"] > 0
    assert study["raw_import"]["sampleProfile"]["fieldLocators"]["revenue.total"]["cell"] == "D11"
    assert study["raw_import"]["sampleProfile"]["fieldLocators"]["cashflow.monthly_cash_flow"]["range"] == "J100:CH158"
    assert len(study["monthly_cash_flow"]) > 0
    assert study["monthly_cash_flow"][0]["source_locator"]["sheetName"] == "2.현금흐름"
