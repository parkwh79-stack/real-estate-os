"""Compare a project's archived workbook mapping with reviewed working inputs."""

from copy import deepcopy
import math
import re
from typing import Any

from core.schedule import schedule_month_indices
from use_cases.cash_flow import build_cash_flow_request_use_case, execute_cash_flow_request_use_case
from use_cases.feasibility_workbook_llm import FEASIBILITY_INPUT_KEYS, feasibility_review_state
from use_cases.workbook_import_approval import feasibility_study_draft_hash


_SCHEMA = "realytics-workbook-input-reconciliation-v1"
_FIELDS = {
    "total_revenue": "총매출",
    "total_cost": "총사업비",
    "land_cost": "토지비·부대비",
    "construction_cost": "건축공사비",
    "finance_cost": "예산 금융비용",
    "other_cost": "기타 사업비",
    "development_profit": "세전 사업이익",
    "pf_amount": "PF 예산 한도",
}
_METRICS = {
    "totalRevenue": "총매출",
    "totalProjectCost": "총사업비",
    "developmentProfit": "사업이익",
    "pfLimit": "PF 예산 한도",
    "maxMinus": "조달 전 최대 자금부족",
    "totalPfDraw": "예상 PF 인출 합계",
    "endingOperatingCash": "기말 운영계정",
    "endingRepaymentReserve": "기말 상환계정",
}
_BUDGET_METHOD = "shared_working_schedule_budget"


def _registered_source(project: dict, mapped: dict) -> bool:
    draft = project.get("feasibilityStudyV2Draft") or {}
    raw = draft.get("raw_import") or {}
    document = mapped.get("sourceDocument") or {}
    candidate = raw.get("candidateImport") or {}
    metadata = project.get("_meta") or {}
    digest = document.get("originalFileHash")
    draft_hash = candidate.get("draftHash")
    import_id = candidate.get("importId")
    project_id = project.get("id") or (project.get("project") or {}).get("id")
    if not (
        project_id is not None
        and str(draft.get("project_id")) == str(project_id)
        and isinstance(digest, str)
        and re.fullmatch(r"[a-f0-9]{64}", digest)
        and document.get("archiveId")
        and isinstance(draft_hash, str)
        and import_id
        and import_id == metadata.get("latestFeasibilityWorkbookCandidateId")
        and candidate.get("status") == "candidate"
    ):
        return False
    recorded = any(
        row.get("importId") == import_id and row.get("draftHash") == draft_hash
        and digest in (row.get("sourceDocumentHashes") or [])
        and row.get("status") == "candidate"
        for row in metadata.get("feasibilityWorkbookCandidates") or []
        if isinstance(row, dict)
    )
    archived = any(
        row.get("archiveId") == document["archiveId"] and row.get("originalFileHash") == digest
        and row.get("feasibilityStudyV2DraftHash") == draft_hash
        and row.get("objectVersion") == document.get("objectVersion")
        and row.get("documentType") == "feasibility_workbook"
        for row in project.get("source_documents") or []
        if isinstance(row, dict)
    )
    if not (recorded and archived):
        return False
    try:
        return feasibility_study_draft_hash(draft) == draft_hash
    except ValueError:
        return False


def _amount(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not -9_007_199_254_740_991 <= value <= 9_007_199_254_740_991 or value != int(value):
        return None
    return int(value)


def _row(key: str, label: str, source: dict, state: dict) -> dict:
    workbook = _amount(source.get("value")) if source.get("normalizedUnit") == "KRW" else None
    working = _amount(state["values"].get(key))
    saved_review = state["fieldReviews"].get(key) or {}
    difference = working - workbook if working is not None and workbook is not None else None
    if workbook is None:
        status = "missing_source_value"
        action = "원본 셀의 금액·단위와 저장값을 확인하세요."
    elif not source.get("applyEligible") or not source.get("sourceCell"):
        status = "source_needs_review"
        action = "원본 셀의 산정 범위와 적용 가능 여부를 확인하세요."
    elif working is None:
        status = "missing_working_input"
        action = "분석에 사용할 금액을 사업수지 검토 입력에 저장하세요."
    elif difference:
        status = "different"
        action = "차액의 근거를 원본 셀 또는 저장한 변경 사유에서 확인하세요."
    else:
        status, action = "matched", None
    return {
        "key": key, "label": label, "unit": "KRW", "workbookValue": workbook,
        "workingValue": working, "differenceWon": difference, "status": status,
        "role": "reference_output" if key == "development_profit" else "budget_input",
        "sourceCell": source.get("sourceCell"), "sourceUnit": source.get("sourceUnit"),
        "calculationState": source.get("calculationState"),
        "workingOrigin": saved_review.get("origin"), "workingReason": saved_review.get("reason"),
        "nextAction": action,
    }


def _budget(project: dict, amounts: dict, schedule: Any) -> dict:
    # Use one planning model for both columns. The original workbook trace and
    # project-specific collection or loan schedules keep their own calculation basis.
    study = {
        "id": project.get("id"), "project": deepcopy(project.get("project") or {}),
        "feasibility_input": {key: amounts[key] for key in FEASIBILITY_INPUT_KEYS},
        "schedule": deepcopy(schedule),
    }
    request = build_cash_flow_request_use_case(study)
    request["sourcesAndUses"]["funding"]["pfLoan"] = amounts["pf_amount"]
    return execute_cash_flow_request_use_case(request)


def _calculation_comparison(project: dict, rows: list[dict]) -> tuple[dict, dict | None]:
    data = {"status": "unavailable", "method": _BUDGET_METHOD, "metrics": [],
            "message": "원본과 작업 입력의 예산 금액을 확인하면 같은 일정으로 비교할 수 있습니다."}
    inputs = [row for row in rows if row["role"] == "budget_input"]
    if any(row["status"] not in {"matched", "different"} for row in inputs):
        return data, None
    detail_keys = ("pf_structure", "cashflow_input", "sales_schedule", "salesCollectionSchedule",
                   "product_mix", "productMix", "sales_assumptions", "cost_assumptions",
                   "canonicalFeasibilityStudyV2", "feasibilityStudyV2")
    if any(project.get(key) for key in detail_keys):
        data["message"] = "상세 대출·상품·수납 입력이 있는 프로젝트는 동일한 예산 정책을 선택한 계산 실행끼리 비교하세요."
        return data, None
    schedule = project.get("schedule") or project.get("project_schedule")
    try:
        if not {"construction_start", "completion"} <= schedule_month_indices(schedule).keys():
            data["message"] = "같은 월차로 다시 계산하려면 저장 검토 입력에 착공일과 준공일이 필요합니다."
            return data, None
        amounts = [{row["key"]: row[column] for row in inputs} for column in ("workbookValue", "workingValue")]
        for values in amounts:
            if any(value < 0 for value in values.values()):
                data["message"] = "수입·사업비·약정 계획액의 부호를 확인하세요."
                return data, None
            cost_parts = ("land_cost", "construction_cost", "finance_cost", "other_cost")
            if sum(values[key] for key in cost_parts) != values["total_cost"]:
                data["message"] = "토지비·공사비·금융비·기타비 합계와 총사업비의 원 단위 차이를 확인하세요."
                return data, None
        baseline, working = (_budget(project, values, schedule) for values in amounts)
    except ValueError:
        data["message"] = "저장한 월차나 예산 금액으로 공통 계획을 계산할 수 없습니다. 착공·준공 및 금액 범위를 다시 확인하세요."
        return data, None

    def metric(result: dict, key: str) -> float:
        summary = result["summary"]
        if key == "developmentProfit":
            return summary["totalRevenue"] - summary["totalProjectCost"]
        return summary[key]

    metrics = []
    for key, label in _METRICS.items():
        left, right = (metric(result, key) for result in (baseline, working))
        metrics.append({
            "key": key, "label": label, "unit": "KRW", "workbookValue": left,
            "workingValue": right, "differenceWon": right - left,
            "status": "matched" if math.isclose(left, right, rel_tol=0, abs_tol=0.000001) else "different",
        })
    data.update({
        "status": "matched" if all(row["status"] == "matched" for row in metrics) else "differences",
        "metrics": metrics,
        "message": "저장한 착공·준공 일정과 공통 예산 배분 기준으로 두 금액을 각각 재계산했습니다.",
        "workingResult": "recalculated_budget",
        "workbookResult": "recalculated_budget_from_mapped_totals",
    })
    periods = working["monthlyCashFlow"]
    return data, {
        "kind": "calculation_month_index", "periodCount": len(periods),
        "firstPeriod": periods[0]["period"], "lastPeriod": periods[-1]["period"],
    }


def _period_comparison(project: dict, working: dict | None) -> dict:
    workbook = (project["feasibilityStudyV2Draft"].get("raw_import") or {}).get("cashFlowPeriodBasis") or {}
    periods = workbook.get("periods") or []
    source = {
        "kind": workbook.get("kind"), "periodCount": len(periods),
        "firstPeriod": periods[0].get("label") if periods else None,
        "lastPeriod": periods[-1].get("label") if periods else None,
    }
    status = "unavailable"
    message = "원본에 대조할 기간 정보가 없습니다."
    if periods:
        status = "working_axis_unconfirmed"
        message = "원본 기간과 예산 계산 월차 사이의 날짜 대응을 확인해야 기간별 차이를 비교할 수 있습니다."
        if working and len(periods) != working["periodCount"]:
            status = "different_axis"
            message = "원본 현금흐름과 공통 예산의 기간 개수가 다릅니다. 기간별 현금흐름 차이는 별도 검토가 필요합니다."
    return {"status": status, "workbook": source, "working": working, "message": message}


def reconcile_workbook_inputs(project: dict) -> dict:
    """Use only the authorized project's registered draft and its saved inputs."""
    report = {"schemaVersion": _SCHEMA, "analysisOnly": True, "reportEligible": False,
              "status": "source_unavailable", "sourceDocument": None, "profile": None, "mappingVersion": None,
              "reviewHash": None, "rows": [], "workbookCalculationEvidence": "saved_values",
              "periodComparison": {"status": "unavailable", "workbook": None, "working": None,
                                   "message": "검토할 원본 기간이 없습니다."},
              "calculationComparison": {"status": "unavailable", "method": _BUDGET_METHOD, "metrics": [],
                                        "message": "보관한 상봉 일반분양 원본을 사업수지 검토 화면에서 선택하세요."}}
    state = feasibility_review_state(project)
    mapping = state.get("workbookFinancialReview")
    if not mapping or mapping.get("profile") != "sangbong-sale-feasibility" or not _registered_source(project, mapping):
        return report
    rows = [_row(key, label, mapping["sourceFields"].get(key) or {}, state) for key, label in _FIELDS.items()]
    row_states = {row["status"] for row in rows}
    status = "incomplete"
    if row_states <= {"different", "matched"}:
        status = "differences" if "different" in row_states else "matched"
    comparison, working_axis = _calculation_comparison(project, rows)
    report.update(status=status, sourceDocument=mapping["sourceDocument"], profile=mapping["profile"],
                  mappingVersion=mapping["schemaVersion"], reviewHash=state["reviewHash"], rows=rows,
                  periodComparison=_period_comparison(project, working_axis), calculationComparison=comparison)
    return report
