from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from agents.feasibility_workbook_validator import (
    LEGACY_BONRI_WORKBOOK_PROFILE,
    REALYTICS_FORMULA_WORKBOOK_PROFILE,
)


KNOWN_TEMPLATE_PROFILES: List[Dict[str, Any]] = [
    {
        "templateId": "legacy-bonri-mixed-use-pf-feasibility",
        "templateName": "Legacy Bonri mixed-use PF feasibility workbook",
        "profile": LEGACY_BONRI_WORKBOOK_PROFILE,
        "requiredSheets": LEGACY_BONRI_WORKBOOK_PROFILE["required_sheets"],
        "optionalSheets": [],
        "mappingRules": [
            {
                "schemaPath": "project.projectName",
                "displayName": "Project name",
                "sheetName": LEGACY_BONRI_WORKBOOK_PROFILE["key_cells"]["project_name"][0],
                "cell": LEGACY_BONRI_WORKBOOK_PROFILE["key_cells"]["project_name"][1],
                "valueType": "text",
            },
            {
                "schemaPath": "site.address",
                "displayName": "Project location",
                "sheetName": LEGACY_BONRI_WORKBOOK_PROFILE["key_cells"]["location"][0],
                "cell": LEGACY_BONRI_WORKBOOK_PROFILE["key_cells"]["location"][1],
                "valueType": "text",
            },
            {
                "schemaPath": "metrics.totalRevenue",
                "displayName": "Total sales revenue",
                "sheetName": LEGACY_BONRI_WORKBOOK_PROFILE["key_cells"]["total_revenue_after_vat"][0],
                "cell": LEGACY_BONRI_WORKBOOK_PROFILE["key_cells"]["total_revenue_after_vat"][1],
                "valueType": "krw_thousand",
            },
            {
                "schemaPath": "metrics.totalProjectCost",
                "displayName": "Total project cost",
                "sheetName": LEGACY_BONRI_WORKBOOK_PROFILE["key_cells"]["total_cost"][0],
                "cell": LEGACY_BONRI_WORKBOOK_PROFILE["key_cells"]["total_cost"][1],
                "valueType": "krw_thousand",
            },
            {
                "schemaPath": "metrics.developmentProfit",
                "displayName": "Development profit",
                "sheetName": LEGACY_BONRI_WORKBOOK_PROFILE["key_cells"]["development_profit"][0],
                "cell": LEGACY_BONRI_WORKBOOK_PROFILE["key_cells"]["development_profit"][1],
                "valueType": "krw_thousand",
            },
            {
                "schemaPath": "financing.pfAmount",
                "displayName": "PF principal amount",
                "sheetName": LEGACY_BONRI_WORKBOOK_PROFILE["required_sheets"][5],
                "cell": "E48:E51",
                "valueType": "krw_million_range",
            },
        ],
    },
    {
        "templateId": "realytics-formula-cash-flow-export",
        "templateName": "REALYTICS formula cash-flow export workbook",
        "profile": REALYTICS_FORMULA_WORKBOOK_PROFILE,
        "requiredSheets": REALYTICS_FORMULA_WORKBOOK_PROFILE["required_sheets"],
        "optionalSheets": [],
        "mappingRules": [
            {
                "schemaPath": "metrics.totalRevenue",
                "displayName": "Total sales revenue",
                "namedRange": "fs_total_revenue",
                "valueType": "krw_100m_or_krw",
            },
            {
                "schemaPath": "metrics.totalProjectCost",
                "displayName": "Total project cost",
                "namedRange": "fs_total_project_cost",
                "valueType": "krw_100m_or_krw",
            },
            {
                "schemaPath": "financing.pfAmount",
                "displayName": "PF principal amount",
                "namedRange": "fs_pf_loan",
                "valueType": "krw_100m_or_krw",
            },
            {
                "schemaPath": "metrics.maxMinus",
                "displayName": "Max Minus",
                "namedRange": "cf_max_minus",
                "valueType": "krw_100m_or_krw",
            },
        ],
    },
]

AUTONOMOUS_PROMOTION_THRESHOLDS = {
    "templateSimilarity": 0.92,
    "mappingConfidence": 0.90,
    "sourceLocatorCoverage": 1.0,
    "requiredMappingCoverage": 0.80,
}


class ExcelWorkbookLearningAgent:
    """Profile uploaded feasibility workbooks and create LLM-first mapping decisions.

    This agent does not mutate import code, template rules, or persisted project data.
    It creates deterministic workbook profiles, mapping candidates, LLM judgement
    context, and automation gates before any new parser behavior is registered.
    """

    analysis_type = "realytics-excel-workbook-learning-v1"

    def analyze(
        self,
        workbook_path: str | Path,
        *,
        source_document: Optional[Dict[str, Any]] = None,
        max_preview_cells_per_sheet: int = 25,
    ) -> Dict[str, Any]:
        workbook_path = Path(workbook_path)
        formula_wb = load_workbook(workbook_path, data_only=False, read_only=False)
        value_wb = load_workbook(workbook_path, data_only=True, read_only=False)

        try:
            workbook_profile = _profile_workbook(formula_wb, workbook_path, max_preview_cells_per_sheet)
            template_candidates = _template_candidates(formula_wb, workbook_profile)
            selected_template = template_candidates[0] if template_candidates else None
            mapping_candidates = _mapping_candidates(formula_wb, value_wb, selected_template, source_document)
            validation_plan = _validation_plan(selected_template, mapping_candidates)
            llm_judgement_plan = _llm_judgement_plan(workbook_profile, selected_template, mapping_candidates)
            automation_decision = _automation_decision(selected_template, mapping_candidates, validation_plan)
            warnings = _warnings(template_candidates, mapping_candidates)

            status = "candidate_generated" if mapping_candidates else "needs_review"
            if not template_candidates:
                status = "profiled"

            return {
                "analysisType": self.analysis_type,
                "status": status,
                "fileName": workbook_path.name,
                "sourceDocument": source_document or {},
                "workbookProfile": workbook_profile,
                "templateCandidates": template_candidates,
                "mappingCandidates": mapping_candidates,
                "validationPlan": validation_plan,
                "llmJudgementPlan": llm_judgement_plan,
                "automationDecision": automation_decision,
                "continuousLearningPlan": _continuous_learning_plan(automation_decision),
                "llmPromptContext": _llm_prompt_context(workbook_profile, template_candidates, mapping_candidates),
                "safetyPolicy": _safety_policy(),
                "warnings": warnings,
                "nextActions": _next_actions(status, warnings),
            }
        finally:
            formula_wb.close()
            value_wb.close()


def profile_excel_workbook_for_learning(
    workbook_path: str | Path,
    *,
    source_document: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return ExcelWorkbookLearningAgent().analyze(workbook_path, source_document=source_document)


def _profile_workbook(workbook: Any, workbook_path: Path, max_preview_cells_per_sheet: int) -> Dict[str, Any]:
    sheets = [_profile_sheet(sheet, max_preview_cells_per_sheet) for sheet in workbook.worksheets]
    defined_names = sorted(workbook.defined_names.keys())
    formula_count = sum(sheet["formulaCount"] for sheet in sheets)
    non_empty_count = sum(sheet["nonEmptyCellCount"] for sheet in sheets)

    return {
        "fileName": workbook_path.name,
        "sheetCount": len(workbook.worksheets),
        "sheetNames": list(workbook.sheetnames),
        "definedNames": defined_names,
        "formulaCount": formula_count,
        "nonEmptyCellCount": non_empty_count,
        "formulaDensity": round(formula_count / non_empty_count, 4) if non_empty_count else 0,
        "sheets": sheets,
    }


def _profile_sheet(sheet: Worksheet, max_preview_cells: int) -> Dict[str, Any]:
    formula_count = 0
    non_empty_count = 0
    numeric_count = 0
    text_count = 0
    preview_cells: List[Dict[str, Any]] = []

    for row in sheet.iter_rows():
        for cell in row:
            value = cell.value
            if value is None:
                continue
            non_empty_count += 1
            if isinstance(value, str) and value.startswith("="):
                formula_count += 1
            elif isinstance(value, (int, float)):
                numeric_count += 1
            elif isinstance(value, str):
                text_count += 1
            if len(preview_cells) < max_preview_cells:
                preview_cells.append(
                    {
                        "cell": cell.coordinate,
                        "valuePreview": _value_preview(value),
                        "valueKind": _value_kind(value),
                    }
                )

    return {
        "sheetName": sheet.title,
        "dimension": sheet.calculate_dimension(),
        "maxRow": sheet.max_row,
        "maxColumn": sheet.max_column,
        "nonEmptyCellCount": non_empty_count,
        "formulaCount": formula_count,
        "numericCellCount": numeric_count,
        "textCellCount": text_count,
        "mergedRangeCount": len(sheet.merged_cells.ranges),
        "freezePane": str(sheet.freeze_panes) if sheet.freeze_panes else None,
        "tableNames": list(sheet.tables.keys()) if hasattr(sheet, "tables") else [],
        "previewCells": preview_cells,
    }


def _template_candidates(workbook: Any, workbook_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    sheet_names = set(workbook.sheetnames)
    defined_names = set(workbook.defined_names.keys())
    candidates: List[Dict[str, Any]] = []

    for template in KNOWN_TEMPLATE_PROFILES:
        required = template.get("requiredSheets", [])
        present_required = [name for name in required if name in sheet_names]
        missing_required = [name for name in required if name not in sheet_names]
        required_score = len(present_required) / len(required) if required else 0

        required_named_ranges = template["profile"].get("required_named_ranges", [])
        present_ranges = [name for name in required_named_ranges if name in defined_names]
        named_range_score = len(present_ranges) / len(required_named_ranges) if required_named_ranges else 0

        formula_density = min(float(workbook_profile.get("formulaDensity", 0)) / 0.5, 1.0)
        score = round((required_score * 0.75) + (named_range_score * 0.15) + (formula_density * 0.10), 4)

        if score <= 0:
            continue
        candidates.append(
            {
                "templateId": template["templateId"],
                "templateName": template["templateName"],
                "similarityScore": score,
                "matchedRequiredSheetCount": len(present_required),
                "requiredSheetCount": len(required),
                "missingRequiredSheets": missing_required,
                "matchedRequiredNamedRangeCount": len(present_ranges),
                "requiredNamedRangeCount": len(required_named_ranges),
                "matchReason": _match_reason(score, missing_required, required_named_ranges, present_ranges),
            }
        )

    return sorted(candidates, key=lambda item: item["similarityScore"], reverse=True)


def _mapping_candidates(
    formula_wb: Any,
    value_wb: Any,
    selected_template: Optional[Dict[str, Any]],
    source_document: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not selected_template:
        return []

    template = next(
        item for item in KNOWN_TEMPLATE_PROFILES if item["templateId"] == selected_template["templateId"]
    )
    candidates: List[Dict[str, Any]] = []
    for rule in template.get("mappingRules", []):
        if rule.get("namedRange"):
            candidate = _named_range_candidate(formula_wb, value_wb, rule, source_document)
        else:
            candidate = _cell_candidate(formula_wb, value_wb, rule, source_document)
        if candidate:
            candidate["confidence"] = _candidate_confidence(selected_template, candidate)
            candidate["decisionMode"] = "llm_first_validation_gate"
            candidate["requiresHumanApproval"] = _requires_human_exception_review(candidate)
            candidate["approvalStatus"] = (
                "llm_auto_gate_candidate"
                if not candidate["requiresHumanApproval"]
                else "exception_review_required"
            )
            candidates.append(candidate)
    return candidates


def _cell_candidate(
    formula_wb: Any,
    value_wb: Any,
    rule: Dict[str, Any],
    source_document: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    sheet_name = rule["sheetName"]
    cell_ref = rule["cell"]
    if sheet_name not in formula_wb.sheetnames:
        return None

    formula_sheet = formula_wb[sheet_name]
    value_sheet = value_wb[sheet_name]
    if ":" in cell_ref:
        values = [_json_safe_value(value_sheet[cell].value) for cell in _iter_cell_refs(cell_ref)]
        formulas = [_json_safe_value(formula_sheet[cell].value) for cell in _iter_cell_refs(cell_ref)]
        has_value = any(value is not None for value in values) or any(formula is not None for formula in formulas)
        if not has_value:
            return None
        value: Any = values
        formula: Any = formulas
    else:
        value = _json_safe_value(value_sheet[cell_ref].value)
        formula = _json_safe_value(formula_sheet[cell_ref].value)
        if value is None and formula is None:
            return None

    locator = _excel_locator(sheet_name, cell_ref, source_document)
    return {
        "candidateId": f"mapping::{rule['schemaPath']}::{sheet_name}!{cell_ref}",
        "schemaPath": rule["schemaPath"],
        "displayName": rule["displayName"],
        "valueType": rule["valueType"],
        "value": value,
        "formula": formula,
        "sourceLocator": locator,
        "mappingReason": "Matched a known sample-informed workbook cell profile.",
    }


def _named_range_candidate(
    formula_wb: Any,
    value_wb: Any,
    rule: Dict[str, Any],
    source_document: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    name = rule["namedRange"]
    defined_name = formula_wb.defined_names.get(name)
    if defined_name is None:
        return None

    destinations = list(defined_name.destinations)
    if not destinations:
        return None

    sheet_name, cell_ref = destinations[0]
    if sheet_name not in formula_wb.sheetnames:
        return None

    value = _json_safe_value(value_wb[sheet_name][cell_ref].value)
    formula = _json_safe_value(formula_wb[sheet_name][cell_ref].value)
    if value is None and formula is None:
        return None

    locator = _excel_locator(sheet_name, cell_ref, source_document)
    locator["namedRange"] = name
    return {
        "candidateId": f"mapping::{rule['schemaPath']}::{name}",
        "schemaPath": rule["schemaPath"],
        "displayName": rule["displayName"],
        "valueType": rule["valueType"],
        "value": value,
        "formula": formula,
        "sourceLocator": locator,
        "mappingReason": "Matched a required REALYTICS named range.",
    }


def _validation_plan(
    selected_template: Optional[Dict[str, Any]],
    mapping_candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "status": "ready_for_review" if mapping_candidates else "needs_manual_mapping",
        "selectedTemplateId": (selected_template or {}).get("templateId"),
        "requiredChecks": [
            "Preserve original uploaded workbook and SHA-256 hash.",
            "Run workbook validator before import.",
            "Reconcile total revenue, total cost, development profit, PF amount, and Max Minus.",
            "Require source locator for every accepted mapping.",
            "Require LLM judgement, automated validation, and regression pass before registering a new template rule.",
            "Create a regression fixture before enabling automatic import.",
        ],
        "mappingCandidateCount": len(mapping_candidates),
        "automationThresholds": AUTONOMOUS_PROMOTION_THRESHOLDS,
    }


def _llm_judgement_plan(
    workbook_profile: Dict[str, Any],
    selected_template: Optional[Dict[str, Any]],
    mapping_candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "mode": "llm_first_autonomous_review",
        "status": "llm_judgement_required" if mapping_candidates else "insufficient_candidates",
        "modelRole": "Judge workbook mapping, unit basis, formula intent, and validation sufficiency before parser promotion.",
        "mustEvaluate": [
            "template similarity and missing required sheets",
            "cell or named-range mapping correctness",
            "KRW unit basis and sqm/py basis",
            "formula lineage for total revenue, total cost, PF amount, Max Minus, and cash-flow rows",
            "source locator coverage",
            "whether regression fixture can detect future breakage",
        ],
        "currentSignals": {
            "templateId": (selected_template or {}).get("templateId"),
            "templateSimilarity": (selected_template or {}).get("similarityScore"),
            "mappingCandidateCount": len(mapping_candidates),
            "sourceLocatorCoverage": _source_locator_coverage(mapping_candidates),
            "formulaDensity": workbook_profile.get("formulaDensity"),
        },
        "allowedDecisions": [
            "auto_promote_candidate",
            "auto_promote_shadow_mode",
            "needs_more_evidence",
            "reject_mapping",
        ],
        "requiredOutput": {
            "decision": "auto_promote_candidate | auto_promote_shadow_mode | needs_more_evidence | reject_mapping",
            "confidence": 0.0,
            "reasons": [],
            "requiredRegressionTests": [],
            "acceptedMappingCandidateIds": [],
            "rejectedMappingCandidateIds": [],
        },
    }


def _automation_decision(
    selected_template: Optional[Dict[str, Any]],
    mapping_candidates: List[Dict[str, Any]],
    validation_plan: Dict[str, Any],
) -> Dict[str, Any]:
    if not selected_template or not mapping_candidates:
        return {
            "decision": "needs_more_evidence",
            "canAutoPromote": False,
            "reason": "No matched template or no mapping candidates.",
            "nextMode": "llm_review_required",
        }

    source_locator_coverage = _source_locator_coverage(mapping_candidates)
    required_mapping_coverage = _required_mapping_coverage(selected_template, mapping_candidates)
    min_mapping_confidence = min(candidate.get("confidence", 0) for candidate in mapping_candidates)
    checks = {
        "templateSimilarityPassed": selected_template.get("similarityScore", 0) >= AUTONOMOUS_PROMOTION_THRESHOLDS["templateSimilarity"],
        "mappingConfidencePassed": min_mapping_confidence >= AUTONOMOUS_PROMOTION_THRESHOLDS["mappingConfidence"],
        "sourceLocatorCoveragePassed": source_locator_coverage >= AUTONOMOUS_PROMOTION_THRESHOLDS["sourceLocatorCoverage"],
        "requiredMappingCoveragePassed": required_mapping_coverage >= AUTONOMOUS_PROMOTION_THRESHOLDS["requiredMappingCoverage"],
        "validationPlanReady": validation_plan.get("status") == "ready_for_review",
    }
    can_auto_promote = all(checks.values())
    return {
        "decision": "auto_promote_shadow_mode" if can_auto_promote else "llm_review_required",
        "canAutoPromote": can_auto_promote,
        "nextMode": "shadow_mode_regression" if can_auto_promote else "llm_judgement_then_regression",
        "checks": checks,
        "signals": {
            "templateSimilarity": selected_template.get("similarityScore"),
            "sourceLocatorCoverage": source_locator_coverage,
            "requiredMappingCoverage": required_mapping_coverage,
            "minMappingConfidence": round(min_mapping_confidence, 4),
        },
        "guardrail": (
            "Even when canAutoPromote is true, first production use runs in shadow mode "
            "against validation and regression fixtures before updating canonical project data."
        ),
    }


def _continuous_learning_plan(automation_decision: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "learningMode": "continuous_llm_guided_template_improvement",
        "promotionPath": [
            "llm_judgement",
            "automated_validation",
            "shadow_mode_import",
            "regression_fixture_pass",
            "template_registry_update",
            "monitored_production_import",
        ],
        "feedbackSignals": [
            "validator reconciliation failures",
            "cash-flow parity check failures",
            "Data Quality Agent conflicts",
            "user corrections or approved assumption overrides",
            "historical import error patterns",
            "report/evidence source-locator gaps",
        ],
        "currentRecommendedMode": automation_decision.get("nextMode"),
        "rollbackRequired": True,
    }


def _llm_prompt_context(
    workbook_profile: Dict[str, Any],
    template_candidates: List[Dict[str, Any]],
    mapping_candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "purpose": "Ask an LLM to judge workbook mappings, propose additional mappings, define validation checks, and decide whether the workbook can enter autonomous shadow mode.",
        "canonicalSchemaTargets": [
            "FeasibilityStudyV2.site",
            "FeasibilityStudyV2.program",
            "FeasibilityStudyV2.product_mix",
            "FeasibilityStudyV2.revenue_items",
            "FeasibilityStudyV2.cost_items",
            "FeasibilityStudyV2.financing",
            "FeasibilityStudyV2.schedule",
            "FeasibilityStudyV2.metrics",
        ],
        "workbookProfileSummary": {
            "sheetCount": workbook_profile.get("sheetCount"),
            "sheetNames": workbook_profile.get("sheetNames"),
            "formulaDensity": workbook_profile.get("formulaDensity"),
            "definedNames": workbook_profile.get("definedNames"),
        },
        "topTemplateCandidates": template_candidates[:3],
        "existingMappingCandidates": [
            {
                "schemaPath": candidate["schemaPath"],
                "locator": candidate["sourceLocator"].get("locator"),
                "valueType": candidate["valueType"],
            }
            for candidate in mapping_candidates
        ],
        "instructions": [
            "Act as the primary mapping judge, but do not invent values when a cell is blank or ambiguous.",
            "Prefer named ranges over visual cell positions when both exist.",
            "Return candidate mappings with sheet, cell, value type, confidence, and validation rule.",
            "Flag any mapping that changes unit basis, such as KRW thousand, KRW million, or KRW 100m.",
            "Approve autonomous promotion only when source locators, unit basis, validation checks, and regression tests are sufficient.",
        ],
    }


def _safety_policy() -> Dict[str, Any]:
    return {
        "llmMay": [
            "suggest additional mapping candidates",
            "suggest validation and reconciliation checks",
            "suggest regression fixture coverage",
            "judge whether a mapping can enter autonomous shadow mode",
            "reject ambiguous mappings without waiting for a human reviewer",
        ],
        "llmMustNot": [
            "mutate parser code directly",
            "overwrite canonical project values",
            "fabricate missing source data",
            "expose sensitive account or owner data in logs",
            "skip validation, regression, audit logging, or rollback requirements",
        ],
        "promotionPath": [
            "profiled",
            "candidate_generated",
            "llm_judgement",
            "shadow_mode_import",
            "regression_ready",
            "registered",
            "monitored_production",
        ],
    }


def _warnings(template_candidates: List[Dict[str, Any]], mapping_candidates: List[Dict[str, Any]]) -> List[str]:
    warnings: List[str] = []
    if not template_candidates:
        warnings.append("No known workbook template was matched; LLM judgement must propose or reject a new template mapping.")
    elif template_candidates[0]["similarityScore"] < 0.8:
        warnings.append("Top template similarity is below 0.80; route to LLM judgement and do not auto-import until validation passes.")
    if not mapping_candidates:
        warnings.append("No usable mapping candidates were found from known template rules.")
    if any(candidate.get("sourceLocator", {}).get("locatorPrecision") != "cell" for candidate in mapping_candidates):
        warnings.append("Some candidates are not precise cell-level locators and require additional review.")
    return warnings


def _next_actions(status: str, warnings: List[str]) -> List[str]:
    actions = [
        "Run LLM judgement against mapping candidates and workbook profile.",
        "Run automated workbook validation and reconciliation checks.",
        "Create or update a regression fixture before enabling monitored production import.",
    ]
    if status == "profiled" or warnings:
        actions.insert(0, "Ask the LLM judge to propose additional mappings from the workbook profile and reject ambiguous cells.")
    return actions


def _candidate_confidence(selected_template: Dict[str, Any], candidate: Dict[str, Any]) -> float:
    base = min(float(selected_template.get("similarityScore", 0)), 0.95)
    if candidate.get("sourceLocator", {}).get("locatorPrecision") == "cell":
        base += 0.03
    if candidate.get("value") is not None:
        base += 0.02
    return round(min(base, 0.98), 4)


def _requires_human_exception_review(candidate: Dict[str, Any]) -> bool:
    return (
        candidate.get("confidence", 0) < AUTONOMOUS_PROMOTION_THRESHOLDS["mappingConfidence"]
        or candidate.get("sourceLocator", {}).get("locatorPrecision") not in {"cell", "cell_range"}
        or candidate.get("value") is None
    )


def _source_locator_coverage(mapping_candidates: List[Dict[str, Any]]) -> float:
    if not mapping_candidates:
        return 0.0
    covered = sum(1 for candidate in mapping_candidates if candidate.get("sourceLocator", {}).get("locator"))
    return round(covered / len(mapping_candidates), 4)


def _required_mapping_coverage(
    selected_template: Dict[str, Any],
    mapping_candidates: List[Dict[str, Any]],
) -> float:
    template = next(
        item for item in KNOWN_TEMPLATE_PROFILES if item["templateId"] == selected_template["templateId"]
    )
    required_rules = template.get("mappingRules", [])
    if not required_rules:
        return 0.0
    mapped_paths = {candidate.get("schemaPath") for candidate in mapping_candidates}
    required_paths = {rule.get("schemaPath") for rule in required_rules}
    return round(len(mapped_paths & required_paths) / len(required_paths), 4)


def _match_reason(
    score: float,
    missing_required: List[str],
    required_named_ranges: List[str],
    present_ranges: List[str],
) -> str:
    if not missing_required and (not required_named_ranges or len(required_named_ranges) == len(present_ranges)):
        return "All required sheets and required named ranges are present."
    if score >= 0.8:
        return "Most required workbook structure is present; missing items need reviewer confirmation."
    return "Partial structure match only; use as a learning candidate, not an automatic import template."


def _excel_locator(
    sheet_name: str,
    cell_ref: str,
    source_document: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    locator = {
        "sourceType": "excel_cell",
        "sheetName": sheet_name,
        "cell": cell_ref,
        "locator": f"{sheet_name}!{cell_ref}",
        "locatorPrecision": "cell" if ":" not in cell_ref else "cell_range",
    }
    if source_document:
        for key in ["sourceDocumentId", "archiveId", "originalFileName", "sha256"]:
            if key in source_document:
                locator[key] = source_document[key]
    return locator


def _iter_cell_refs(cell_range: str) -> Iterable[str]:
    start, end = cell_range.split(":", 1)
    start_col = "".join(char for char in start if char.isalpha())
    end_col = "".join(char for char in end if char.isalpha())
    start_row = int("".join(char for char in start if char.isdigit()))
    end_row = int("".join(char for char in end if char.isdigit()))
    if start_col != end_col:
        return []
    return [f"{start_col}{row}" for row in range(start_row, end_row + 1)]


def _value_preview(value: Any) -> Any:
    if isinstance(value, str):
        return value[:120]
    return _json_safe_value(value)


def _value_kind(value: Any) -> str:
    if isinstance(value, str) and value.startswith("="):
        return "formula"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    return type(value).__name__


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
