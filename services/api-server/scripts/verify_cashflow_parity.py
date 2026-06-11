"""Verify cash-flow sheet parity between a sample workbook and an export workbook.

This is a deterministic pilot QA tool. It does not judge whether the model is
financially correct; it verifies whether the exported workbook preserves the
sample workbook's key cash-flow package.
"""

from __future__ import annotations

import argparse
import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet


warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    message=r".*merged range.*comment.*",
)


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
DEFAULT_EXPORT = REPO_ROOT / ".runtime" / "formula-cashflow-export-cashflow-parity.xlsx"
DEFAULT_SAMPLE_DIR = WORKSPACE_ROOT / "sample"
TARGET_SHEETS = ["1.수지총괄(대외용)", "2.현금흐름"]
KEY_CELLS = {
    "1.수지총괄(대외용)": ["A1", "E47", "F47", "L140"],
    "2.현금흐름": ["E47", "F47", "F48", "F55", "F56", "F120", "F122", "F124", "F130", "CH124"],
}


@dataclass
class SheetProfile:
    title: str
    dimension: str
    max_row: int
    max_column: int
    non_empty_cell_count: int
    formula_count: int
    merged_range_count: int
    freeze_panes: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "dimension": self.dimension,
            "maxRow": self.max_row,
            "maxColumn": self.max_column,
            "nonEmptyCellCount": self.non_empty_cell_count,
            "formulaCount": self.formula_count,
            "mergedRangeCount": self.merged_range_count,
            "freezePanes": self.freeze_panes,
        }


def find_default_sample() -> Path:
    matches = sorted(DEFAULT_SAMPLE_DIR.glob("*.xlsx"))
    for path in matches:
        if "PF" in path.name and "사업수지" in path.name:
            return path
    raise FileNotFoundError(f"No Bonri-style PF feasibility workbook found in {DEFAULT_SAMPLE_DIR}")


def sheet_profile(sheet: Worksheet) -> SheetProfile:
    non_empty = 0
    formulas = 0
    for row in sheet.iter_rows():
        for cell in row:
            if cell.value is not None:
                non_empty += 1
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    formulas += 1
    return SheetProfile(
        title=sheet.title,
        dimension=sheet.calculate_dimension(),
        max_row=sheet.max_row,
        max_column=sheet.max_column,
        non_empty_cell_count=non_empty,
        formula_count=formulas,
        merged_range_count=len(sheet.merged_cells.ranges),
        freeze_panes=str(sheet.freeze_panes) if sheet.freeze_panes else None,
    )


def values_equal(left: Any, right: Any, tolerance: float) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= tolerance
    return left == right


def compare_profiles(
    sample_profile: SheetProfile,
    export_profile: SheetProfile,
) -> List[Dict[str, Any]]:
    checks = []
    for field in [
        "dimension",
        "max_row",
        "max_column",
        "non_empty_cell_count",
        "formula_count",
        "merged_range_count",
        "freeze_panes",
    ]:
        left = getattr(sample_profile, field)
        right = getattr(export_profile, field)
        checks.append(
            {
                "check": field,
                "sample": left,
                "export": right,
                "passed": left == right,
            }
        )
    return checks


def compare_key_cells(
    sample_formula_sheet: Worksheet,
    export_formula_sheet: Worksheet,
    sample_value_sheet: Worksheet,
    export_value_sheet: Worksheet,
    cells: Iterable[str],
    tolerance: float,
) -> List[Dict[str, Any]]:
    checks = []
    for coordinate in cells:
        sample_formula = sample_formula_sheet[coordinate].value
        export_formula = export_formula_sheet[coordinate].value
        sample_value = sample_value_sheet[coordinate].value
        export_value = export_value_sheet[coordinate].value
        formula_passed = sample_formula == export_formula
        value_passed = values_equal(sample_value, export_value, tolerance)
        checks.append(
            {
                "cell": coordinate,
                "sampleFormula": sample_formula,
                "exportFormula": export_formula,
                "sampleValue": sample_value,
                "exportValue": export_value,
                "formulaPassed": formula_passed,
                "valuePassed": value_passed,
                "passed": formula_passed and value_passed,
            }
        )
    return checks


def verify_cashflow_parity(
    sample_path: Path,
    export_path: Path,
    *,
    tolerance: float = 0.0001,
    target_sheets: Optional[List[str]] = None,
) -> Dict[str, Any]:
    sample_formula_wb = load_workbook(sample_path, data_only=False, read_only=False)
    export_formula_wb = load_workbook(export_path, data_only=False, read_only=False)
    sample_value_wb = load_workbook(sample_path, data_only=True, read_only=False)
    export_value_wb = load_workbook(export_path, data_only=True, read_only=False)

    sheet_results = []
    selected_target_sheets = target_sheets or TARGET_SHEETS
    for sheet_name in selected_target_sheets:
        if sheet_name not in sample_formula_wb.sheetnames:
            sheet_results.append({"sheetName": sheet_name, "passed": False, "error": "missing_in_sample"})
            continue
        if sheet_name not in export_formula_wb.sheetnames:
            sheet_results.append({"sheetName": sheet_name, "passed": False, "error": "missing_in_export"})
            continue

        sample_profile = sheet_profile(sample_formula_wb[sheet_name])
        export_profile = sheet_profile(export_formula_wb[sheet_name])
        profile_checks = compare_profiles(sample_profile, export_profile)
        key_cell_checks = compare_key_cells(
            sample_formula_wb[sheet_name],
            export_formula_wb[sheet_name],
            sample_value_wb[sheet_name],
            export_value_wb[sheet_name],
            KEY_CELLS.get(sheet_name, []),
            tolerance,
        )
        passed = all(check["passed"] for check in profile_checks) and all(
            check["passed"] for check in key_cell_checks
        )
        sheet_results.append(
            {
                "sheetName": sheet_name,
                "passed": passed,
                "sampleProfile": sample_profile.as_dict(),
                "exportProfile": export_profile.as_dict(),
                "profileChecks": profile_checks,
                "keyCellChecks": key_cell_checks,
            }
        )

    return {
        "status": "passed" if all(result.get("passed") for result in sheet_results) else "failed",
        "schemaVersion": "realytics-cashflow-parity-check-v1",
        "sampleWorkbook": str(sample_path),
        "exportWorkbook": str(export_path),
        "targetSheets": selected_target_sheets,
        "sheetResults": sheet_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Bonri-style cash-flow sheet parity.")
    parser.add_argument("--sample", type=Path, default=None, help="Source sample workbook path.")
    parser.add_argument("--export", type=Path, default=DEFAULT_EXPORT, help="Export workbook path.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path.")
    parser.add_argument("--tolerance", type=float, default=0.0001, help="Numeric value tolerance.")
    parser.add_argument(
        "--all-sample-sheets",
        action="store_true",
        help="Compare every sheet in the sample workbook instead of only the cash-flow parity package.",
    )
    args = parser.parse_args()

    sample_path = args.sample or find_default_sample()
    target_sheets = None
    if args.all_sample_sheets:
        sample_wb = load_workbook(sample_path, data_only=False, read_only=False)
        try:
            target_sheets = list(sample_wb.sheetnames)
        finally:
            sample_wb.close()

    result = verify_cashflow_parity(
        sample_path.resolve(),
        args.export.resolve(),
        tolerance=args.tolerance,
        target_sheets=target_sheets,
    )

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
