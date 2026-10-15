"""Freeze an explicitly selected cash-flow run into the PF report's CF section."""
from copy import deepcopy

from use_cases.cash_flow_comparison import content_hash, run_issues, run_summary


BASIS_NOTE = (
    "요약 현금흐름표만 아래 저장 계산의 입력·결과를 사용합니다. 다른 절은 보고서 생성 시점의 "
    "프로젝트 자료이므로 같은 계산 기준으로 검증된 것이 아닙니다. 대출 약정 확인·최종 승인을 뜻하지 않습니다."
)


def freeze_cash_flow_basis(run: dict, *, project_id: str, tenant_id: str, selection: dict) -> dict:
    if (run.get("calculationRunId") != selection["calculationRunId"]
            or str(run.get("projectId")) != project_id
            or str(run.get("tenantId") or "legacy") != tenant_id):
        raise LookupError("선택한 현금흐름 계산을 찾을 수 없습니다.")
    if run_issues(run):
        raise ValueError("선택한 계산의 입력·결과를 확인할 수 없습니다. 계산 이력을 다시 확인해 주세요.")
    if (content_hash(run["inputSnapshot"]) != selection["inputHash"]
            or content_hash(run["resultSnapshot"]) != selection["resultHash"]):
        raise ValueError("선택한 계산 기록이 변경되었습니다. 계산 목록을 다시 불러오고 선택해 주세요.")
    return deepcopy(run)


def attach_cash_flow_basis(report: dict, run: dict) -> None:
    """Never deep-merge monthly rows or missing fields with a different run."""
    saved = run["resultSnapshot"]
    info = run_summary(run)
    report["cashFlowCalculationSnapshot"] = deepcopy(run)
    report["cashFlowCalculationSnapshotHash"] = content_hash(run)
    report["reportEligible"] = False
    report["sections"]["summaryCashFlow"] = {
        "status": saved["status"], "assumption": BASIS_NOTE,
        "calculationRunId": run["calculationRunId"],
        "summary": deepcopy(saved["summary"]), "rows": deepcopy(saved["monthlyCashFlow"]),
    }
    report["calculationSources"] = [{
        "적용 범위": "12. 요약 현금흐름표", "계산 기록": run["calculationRunId"],
        "계산 시각": run.get("createdAt") or "미기록",
        "계산 방식": "월별 금융 검토" if info["policyMode"] == "reviewed_monthly_v1" else "금융비용 예산 배분",
        "입력 검증값": info["inputHash"], "결과 검증값": info["resultHash"],
        "검토 한계": BASIS_NOTE,
        "입력 근거와 가정": [{"항목": item["label"], "구분": item["classification"],
            "검토 이유": item["reason"], "원본": item.get("sourceName") or "미기록",
            "원본 위치": item.get("sourceCell") or "미기록"} for item in info["inputReviews"]],
    }]


def verify_cash_flow_basis(report: dict, *, project_id: str, tenant_id: str | None = None) -> None:
    run = report["cashFlowCalculationSnapshot"]
    if (str(run.get("projectId")) != project_id
            or (tenant_id is not None and str(run.get("tenantId") or "legacy") != tenant_id)
            or report.get("cashFlowCalculationSnapshotHash") != content_hash(run)
            or report.get("reportEligible") is not False or run_issues(run)):
        raise ValueError("Invalid frozen cash-flow basis")
    section = report["sections"]["summaryCashFlow"]
    result = run["resultSnapshot"]
    if (section.get("calculationRunId") != run.get("calculationRunId")
            or section.get("summary") != result["summary"]
            or section.get("rows") != result["monthlyCashFlow"]):
        raise ValueError("Report cash-flow values differ from the captured run")
