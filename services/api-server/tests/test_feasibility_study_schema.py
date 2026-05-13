import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas.feasibility_study import (
    FEASIBILITY_STUDY_SCHEMA_VERSION,
    FEASIBILITY_WORKBOOK_VERSION,
    FeasibilityStudyV2,
    default_workbook_mapping,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "fixtures"
    / "feasibility_study_v2_schema_example.json"
)


def test_feasibility_study_v2_fixture_validates():
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    study = FeasibilityStudyV2.model_validate(data)

    assert study.version == FEASIBILITY_STUDY_SCHEMA_VERSION
    assert study.workbook.workbook_version == FEASIBILITY_WORKBOOK_VERSION
    assert study.workbook.formula_policy == "preserve_excel_formulas"
    assert study.site.land_area_sqm.excel.named_range == "fs_land_area_sqm"
    assert study.program.gross_floor_area_sqm.formula.formula_type == "excel_formula"
    assert len(study.product_mix) == 2
    assert study.metrics.development_profit == pytest.approx(42_480_000_000)


def test_default_workbook_mapping_has_required_formula_sheets_and_named_ranges():
    mapping = default_workbook_mapping()
    roles = {sheet.role for sheet in mapping.sheets}

    assert mapping.workbook_version == FEASIBILITY_WORKBOOK_VERSION
    assert {"inputs", "product_mix", "costs", "financing", "monthly_cf", "summary", "checks"} <= roles
    assert mapping.named_ranges["metrics.total_revenue"] == "fs_total_revenue"
    assert mapping.named_ranges["metrics.exit_sale_rate"] == "fs_exit_sale_rate"


def test_feasibility_study_v2_default_includes_workbook_mapping():
    study = FeasibilityStudyV2(project_name="Blank Template")

    assert study.workbook.workbook_version == FEASIBILITY_WORKBOOK_VERSION
    assert study.workbook.named_ranges["project_name"] == "fs_project_name"
    assert any(sheet.sheet_name == "99_Checks" for sheet in study.workbook.sheets)


def test_feasibility_study_v2_rejects_unknown_fields():
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    data["unexpected"] = True

    with pytest.raises(ValidationError):
        FeasibilityStudyV2.model_validate(data)
