"""Compare captured inputs only. Never repair, recalculate or approve a run."""
from copy import deepcopy
from decimal import Decimal
from itertools import combinations
import math

from use_cases.cash_flow_comparison import business_input, content_hash
from use_cases.report_calculation_basis import CALCULATION_SLOTS


VERSION = "report-input-reconciliation-v1"
LIMITATION = (
    "명시된 공통 예산 항목과 같은 계산 계열의 저장 업무입력만 대조합니다. "
    "누락값은 0으로 채우지 않으며 단위를 환산하지 않습니다. 금액 일치만으로 대출조건·시점·"
    "근거 또는 보고서 전체가 일치한다는 뜻은 아닙니다. 차이가 있어도 분석 초안은 저장할 수 있습니다."
)
FIELDS = (
    ("total_revenue", "총매출 입력", "revenue", "totalSalesRevenue"),
    ("total_cost", "총사업비 입력", "cost", "totalProjectCost"),
    ("land_cost", "토지비 입력", "cost", "landCost"),
    ("construction_cost", "직접 공사비 입력", "cost", "directConstructionCost"),
    ("finance_cost", "금융비용 예산 입력", "cost", "financialCost"),
    ("other_cost", "기타 비용 입력", "cost", "otherCost"),
)
STATUS_LABELS = {"different": "차이 있음", "same": "확인한 입력 일치", "missing": "입력 누락",
                 "unit_mismatch": "단위 확인 필요", "insufficient": "대조 대상 부족"}


def _runs(report: dict) -> dict:
    runs = dict(report.get("calculationSnapshots") or {})
    if report.get("cashFlowCalculationSnapshot"):
        runs["cash_flow"] = report["cashFlowCalculationSnapshot"]
    return dict(sorted(runs.items()))


def _label(slot: str) -> str:
    return "현금흐름" if slot == "cash_flow" else CALCULATION_SLOTS[slot][0]


def _budget_cell(slot: str, run: dict, field: tuple) -> dict:
    key, _, group, cf_key = field
    source = run["inputSnapshot"]
    unit = source.get("unit", "KRW")
    currency = source.get("currency", "KRW")
    unit = unit if isinstance(unit, str) else "미기록"
    currency = currency if isinstance(currency, str) else "미기록"
    if slot == "cash_flow":
        value = ((source.get("sourcesAndUses") or {}).get(group) or {}).get(cf_key)
        path = f"sourcesAndUses.{group}.{cf_key}"
    elif run["engineKey"] == "development_feasibility":
        value = (source.get("feasibility_input") or {}).get(key)
        path = f"feasibility_input.{key}"
    else:
        value, path = source.get(key), key
    valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return {"slot": slot, "label": _label(slot), "calculationRunId": run["calculationRunId"],
            "value": value if valid else None, "unit": unit, "currency": currency,
            "unitSource": "captured" if "unit" in source else "engine_contract_krw",
            "sourcePath": path, "status": "present" if valid else "missing"}


def _family(run: dict) -> str:
    engine = run["engineKey"]
    if engine in {"waterfall", "unsold_ltv", "tranche_recovery"}:
        return "scenario"
    if engine in {"pf_metrics", "pf_structure"}:
        return "pf"
    return engine


def build_input_reconciliation(report: dict) -> dict:
    runs = _runs(report)
    budget_runs = {slot: run for slot, run in runs.items() if slot in {"feasibility", "pf", "returns", "cash_flow"}}
    rows = []
    for field in FIELDS:
        cells = [_budget_cell(slot, run, field) for slot, run in budget_runs.items()]
        present = [cell for cell in cells if cell["status"] == "present"]
        units = {(cell["currency"], cell["unit"]) for cell in present}
        different = len({Decimal(str(cell["value"])) for cell in present}) > 1
        # Different known values remain visible even if a third run is missing.
        status = ("insufficient" if len(cells) < 2 else "unit_mismatch" if units - {("KRW", "KRW")}
                  else "different" if len(present) >= 2 and different
                  else "missing" if len(present) != len(cells) else "same")
        rows.append({"key": field[0], "label": field[1], "status": status,
                     "missingCount": len(cells) - len(present), "values": cells})
    pairs = []
    for (left_slot, left), (right_slot, right) in combinations(runs.items(), 2):
        if _family(left) != _family(right):
            continue
        a, b = business_input(left["inputSnapshot"]), business_input(right["inputSnapshot"])
        pairs.append({"leftSlot": left_slot, "rightSlot": right_slot,
                      "leftRunId": left["calculationRunId"], "rightRunId": right["calculationRunId"],
                      "label": f"{_label(left_slot)} / {_label(right_slot)}",
                      "status": "same" if a == b else "different",
                      "leftInputHash": content_hash(left["inputSnapshot"]),
                      "rightInputHash": content_hash(right["inputSnapshot"])})
    difference_count = sum(row["status"] == "different" for row in [*rows, *pairs])
    unresolved_count = sum(row["status"] in {"missing", "unit_mismatch", "insufficient"} or row["missingCount"] > 0 for row in rows)
    comparable_count = sum(row["status"] in {"same", "different"} for row in [*rows, *pairs])
    status = "differences_found" if difference_count else "incomplete" if unresolved_count else "no_difference_in_checked_inputs"
    summary = (f"입력 차이 {difference_count}개 · 추가 확인 {unresolved_count}개" if difference_count
               else "확인 가능한 입력에서 차이는 없으나 추가 확인이 필요합니다." if unresolved_count
               else "대조한 입력에서 차이를 찾지 못했습니다. 전체 정합성 승인은 아닙니다.")
    return {"schemaVersion": VERSION, "status": status, "summary": summary, "limitation": LIMITATION,
            "differenceCount": difference_count, "unresolvedCount": unresolved_count, "comparableCount": comparable_count,
            "runCount": len(runs), "rows": rows, "sameFamilyChecks": pairs,
            "runInputHashes": {slot: content_hash(run["inputSnapshot"]) for slot, run in runs.items()}}


def attach_input_reconciliation(report: dict) -> None:
    if _runs(report):
        report["calculationReconciliation"] = build_input_reconciliation(report)


def verify_input_reconciliation(report: dict) -> None:
    captured = report["calculationReconciliation"]
    if captured.get("schemaVersion") != VERSION or captured != build_input_reconciliation(report):
        raise ValueError("Saved report input reconciliation differs from captured inputs")


def reconciliation_document(reconciliation: dict) -> dict:
    """Business labels for document formats; machine fields stay in the JSON."""
    return {"대조 결과": reconciliation["summary"], "검토 범위와 한계": reconciliation["limitation"],
            "예산 입력 대조": [{"항목": row["label"], "상태": STATUS_LABELS[row["status"]],
                "실행별 입력": [{"분석": cell["label"], "계산 기록": cell["calculationRunId"],
                    "값": deepcopy(cell["value"]) if cell["status"] == "present" else "미기록",
                    "단위": f"{cell['currency']} / {cell['unit']}",
                    "단위 근거": "저장 입력" if cell["unitSource"] == "captured" else "계산 API 기본 원 단위"}
                    for cell in row["values"]]} for row in reconciliation["rows"]],
            "같은 계산 계열의 전체 업무입력": [{"대상": pair["label"], "상태": STATUS_LABELS[pair["status"]],
                "기준 계산": pair["leftRunId"], "대조 계산": pair["rightRunId"]}
                for pair in reconciliation["sameFamilyChecks"]]}
