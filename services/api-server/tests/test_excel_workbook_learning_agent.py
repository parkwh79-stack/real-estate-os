from pathlib import Path

from openpyxl import Workbook
from openpyxl.workbook.defined_name import DefinedName

from agents.excel_workbook_learning_agent import ExcelWorkbookLearningAgent
from agents.feasibility_workbook_validator import (
    LEGACY_BONRI_WORKBOOK_PROFILE,
    REALYTICS_FORMULA_WORKBOOK_PROFILE,
)


def _build_legacy_like_workbook(path: Path) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name in LEGACY_BONRI_WORKBOOK_PROFILE["required_sheets"]:
        workbook.create_sheet(sheet_name)

    business = workbook[LEGACY_BONRI_WORKBOOK_PROFILE["key_cells"]["total_cost"][0]]
    business["C3"] = "Sample mixed-use project"
    business["F3"] = "Daegu"
    business["D11"] = 165596965.01
    business["D72"] = 151841565.31
    business["D74"] = "=D11-D72"

    cash_flow = workbook[LEGACY_BONRI_WORKBOOK_PROFILE["required_sheets"][5]]
    cash_flow["E48"] = 19500
    cash_flow["E49"] = 12000
    cash_flow["E50"] = 15000
    cash_flow["E51"] = 43500

    workbook.save(path)


def _build_realytics_export_like_workbook(path: Path) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name in REALYTICS_FORMULA_WORKBOOK_PROFILE["required_sheets"]:
        workbook.create_sheet(sheet_name)

    assumptions = workbook[REALYTICS_FORMULA_WORKBOOK_PROFILE["required_sheets"][1]]
    summary = workbook[REALYTICS_FORMULA_WORKBOOK_PROFILE["required_sheets"][4]]
    cash_flow = workbook[REALYTICS_FORMULA_WORKBOOK_PROFILE["required_sheets"][6]]
    assumptions["C6"] = 1655.97
    assumptions["C14"] = 900
    summary["C13"] = 1518.42
    cash_flow["C22"] = -450

    named_ranges = {
        "fs_total_revenue": f"'{assumptions.title}'!$C$6",
        "fs_pf_loan": f"'{assumptions.title}'!$C$14",
        "fs_total_project_cost": f"'{summary.title}'!$C$13",
        "cf_max_minus": f"'{cash_flow.title}'!$C$22",
    }
    for name, ref in named_ranges.items():
        workbook.defined_names.add(DefinedName(name, attr_text=ref))

    workbook.save(path)


def test_excel_workbook_learning_agent_profiles_legacy_mapping_candidates(tmp_path):
    workbook_path = tmp_path / "legacy-like.xlsx"
    _build_legacy_like_workbook(workbook_path)

    result = ExcelWorkbookLearningAgent().analyze(
        workbook_path,
        source_document={"sourceDocumentId": "src-1", "originalFileName": "legacy-like.xlsx"},
    )

    assert result["analysisType"] == "realytics-excel-workbook-learning-v1"
    assert result["status"] == "candidate_generated"
    assert result["templateCandidates"][0]["templateId"] == "legacy-bonri-mixed-use-pf-feasibility"
    assert result["templateCandidates"][0]["similarityScore"] >= 0.75
    assert any(candidate["schemaPath"] == "metrics.totalRevenue" for candidate in result["mappingCandidates"])
    assert all(candidate["decisionMode"] == "llm_first_validation_gate" for candidate in result["mappingCandidates"])
    assert all(candidate["sourceLocator"]["locator"] for candidate in result["mappingCandidates"])
    assert result["llmJudgementPlan"]["mode"] == "llm_first_autonomous_review"
    assert result["automationDecision"]["decision"] in {"llm_review_required", "auto_promote_shadow_mode"}
    assert result["continuousLearningPlan"]["learningMode"] == "continuous_llm_guided_template_improvement"


def test_excel_workbook_learning_agent_uses_realytics_named_ranges(tmp_path):
    workbook_path = tmp_path / "realytics-like.xlsx"
    _build_realytics_export_like_workbook(workbook_path)

    result = ExcelWorkbookLearningAgent().analyze(workbook_path)

    assert result["templateCandidates"][0]["templateId"] == "realytics-formula-cash-flow-export"
    assert any(
        candidate["schemaPath"] == "metrics.totalProjectCost"
        and candidate["sourceLocator"]["namedRange"] == "fs_total_project_cost"
        for candidate in result["mappingCandidates"]
    )


def test_excel_workbook_learning_agent_keeps_unknown_workbook_in_review(tmp_path):
    workbook_path = tmp_path / "unknown.xlsx"
    workbook = Workbook()
    workbook.active.title = "Random"
    workbook.active["A1"] = "hello"
    workbook.save(workbook_path)

    result = ExcelWorkbookLearningAgent().analyze(workbook_path)

    assert result["status"] == "profiled"
    assert result["templateCandidates"] == []
    assert result["mappingCandidates"] == []
    assert "No known workbook template" in result["warnings"][0]
