import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas.feasibility_study import (
    FEASIBILITY_STUDY_SCHEMA_VERSION,
    FEASIBILITY_WORKBOOK_VERSION,
    FinancingTranche,
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


def test_financing_priority_normalizes_middle_legacy_alias() -> None:
    tranche = FinancingTranche.model_validate({
        "tranche_id": "tr-b",
        "name": "Tr.B",
        "priority": "middle",
        "amount": 10_000_000_000,
    })

    assert tranche.priority == "mezzanine"
    assert tranche.model_dump(mode="json")["priority"] == "mezzanine"

    priority_schema = FinancingTranche.model_json_schema()["properties"]["priority"]
    assert priority_schema["enum"] == [
        "senior",
        "mezzanine",
        "junior",
        "equity",
        "bridge",
        "other",
    ]


def test_v2_supports_approved_snapshot_lineage_and_scenario_eligibility():
    study = FeasibilityStudyV2.model_validate({
        "project_name": "Lineage Project",
        "input_references": {
            "approved_input_snapshot_id": "approved-input-1",
            "approved_input_snapshot_hash": "a" * 64,
            "site_parcel_snapshot_id": "parcel-1",
            "regulatory_constraint_snapshot_id": "reg-1",
            "formula_versions": {"revenue.apartment.v1": "1.0.0"},
        },
        "scenarios": [{
            "scenario_id": "base",
            "label": "Base",
            "constraint_snapshot_id": "reg-1",
            "eligibility": "conditional",
            "condition_ids": ["PERMIT_APPROVAL_REQUIRED"],
            "report_grade": False,
        }],
        "formula_traces": [{
            "formula_id": "revenue.apartment.v1",
            "formula_version": "1.0.0",
            "output_path": "metrics.total_revenue",
            "input_paths": ["product_mix[0].revenue"],
            "rounding_policy": "excel_round_krw",
        }],
    })

    assert study.input_references.approved_input_snapshot_id == "approved-input-1"
    assert study.scenarios[0].eligibility == "conditional"
    assert study.scenarios[0].report_grade is False
    assert study.formula_traces[0].rounding_policy == "excel_round_krw"


def test_report_grade_scenario_requires_approved_lineage_and_eligibility():
    with pytest.raises(ValidationError):
        FeasibilityStudyV2.model_validate({
            "scenarios": [{
                "scenario_id": "invalid-report",
                "label": "Invalid report scenario",
                "eligibility": "ineligible",
                "report_grade": True,
            }],
        })

    with pytest.raises(ValidationError):
        FeasibilityStudyV2.model_validate({
            "input_references": {
                "approved_input_snapshot_id": "approved-1",
                "approved_input_snapshot_hash": "not-a-sha256",
            },
        })


def test_report_grade_scenario_accepts_complete_approved_lineage():
    study = FeasibilityStudyV2.model_validate({
        "input_references": {
            "approved_input_snapshot_id": "approved-1",
            "approved_input_snapshot_hash": "b" * 64,
        },
        "scenarios": [{
            "scenario_id": "approved-base",
            "label": "Approved base",
            "constraint_snapshot_id": "reg-1",
            "approval_decision_id": "decision-1",
            "eligibility": "eligible",
            "report_grade": True,
        }],
    })

    assert study.scenarios[0].report_grade is True
