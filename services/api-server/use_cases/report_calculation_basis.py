"""Freeze explicitly selected calculations into the PF report without recalculation."""
from copy import deepcopy
import math

from use_cases.cash_flow_comparison import content_hash, run_issues, run_summary


CALCULATION_SLOTS = {
    "feasibility": ("사업성 요약", ("feasibility", "development_feasibility")),
    "pf": ("PF 지표", ("pf_metrics", "pf_structure")),
    "returns": ("투자 수익률", ("return_metrics",)),
    "waterfall": ("상환순서 시나리오", ("waterfall",)),
    "unsold_ltv": ("미분양 LTV 시나리오", ("unsold_ltv",)),
    "tranche_recovery": ("트랜치 회수 시나리오", ("tranche_recovery",)),
}
CALCULATION_NOTE = (
    "각 항목은 표시된 저장 계산의 입력·결과를 사용합니다. 서로 다른 계산의 입력 정합성이나 실제 약정이 "
    "확인되었다는 뜻은 아닙니다. 미선택 항목은 미확인이며 다른 절은 생성 당시 프로젝트 자료입니다. "
    "분석용 초안으로 최종 승인·발행 대상이 아닙니다."
)
CONNECTED_CALCULATION_NOTE = CALCULATION_NOTE.replace(
    "다른 절은 생성 당시 프로젝트 자료입니다.", "다른 절의 기준은 각 절에 표시합니다.")


def _number_or_missing(value) -> bool:
    return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))


def _has_metrics(result: dict, keys: tuple[str, ...]) -> bool:
    metrics = result.get("metrics")
    return isinstance(metrics, dict) and all(isinstance(metrics.get(key), dict)
        and "value" in metrics[key] and _number_or_missing(metrics[key]["value"]) for key in keys)


def calculation_issue(run: dict, slot: str) -> str | None:
    """Accept captured engine outputs, not legacy overrides or current project fallbacks."""
    try:
        if run.get("engineKey") not in CALCULATION_SLOTS[slot][1] or run.get("status") != "completed":
            return "완료된 해당 종류의 계산이 아닙니다."
        source, result = run["inputSnapshot"], run["resultSnapshot"]
        if not isinstance(source, dict) or not source or not isinstance(result, dict) or not result:
            return "저장된 입력·결과가 없습니다."
        source_hash, result_hash = content_hash(source), content_hash(result)
        pod = (run.get("sourceLocator") or {}).get("sourceType") == "chief_pod_artifact"
        if pod:
            if run.get("inputHash") != source_hash or run.get("outputHash") != result_hash:
                return "Pod 계산의 입력·결과 검증값이 일치하지 않습니다."
        elif (run.get("inputDigest") or {}).get("capturePolicy") != "effective_request_v1":
            return "실제 실행 입력을 보관하지 않은 과거 기록입니다. 다시 계산해 주세요."
        if run["engineKey"] == "development_feasibility":
            valid = (source.get("developmentFeasibilityVersion") == result.get("engineVersion")
                     and bool(source.get("developmentFeasibilityVersion"))
                     and all(isinstance(result.get(key), dict) for key in ("revenue", "costs", "developerReturns", "dataQuality")))
        elif slot == "feasibility":
            valid = isinstance(result.get("inputs"), dict) and all(key in result and _number_or_missing(result[key])
                for key in ("total_revenue", "total_cost", "development_profit", "profit_margin"))
        elif slot == "pf":
            valid = (all(key in source for key in ("tranches", "total_revenue", "total_cost", "other_priority_costs"))
                     and _has_metrics(result, ("sales_value_ltv", "ltc", "dscr", "exit_sale_rate"))
                     and isinstance(result.get("tranches"), list))
        elif slot == "returns":
            valid = (_has_metrics(result, ("projectIrr", "equityIrr", "equityMultiple", "paybackPeriodMonths", "icr"))
                     and isinstance(result.get("inputs"), dict))
        else:
            valid = (all(key in source for key in ("scenarioSetId", "productMix", "pfStructure", "scenarioDrivers", "waterfallRule"))
                     and source["scenarioSetId"] == (run.get("artifactRef") or {}).get("scenarioSetId")
                     and isinstance(result.get("rows"), list) and bool(result["rows"])
                     and all(isinstance(row, dict) for row in result["rows"]) and isinstance(result.get("summary"), dict))
        return None if valid else "입력·결과의 저장 형식을 확인할 수 없습니다."
    except (KeyError, TypeError, ValueError, AttributeError):
        return "입력·결과가 손상되었거나 지원하지 않는 계산입니다."


def calculation_choice(run: dict, slot: str) -> dict:
    issue = calculation_issue(run, slot)
    return {"slot": slot, "label": CALCULATION_SLOTS[slot][0],
            "calculationRunId": run.get("calculationRunId"), "createdAt": run.get("createdAt"),
            "engineKey": run.get("engineKey"), "eligible": issue is None, "issue": issue,
            "inputHash": content_hash(run["inputSnapshot"]) if issue is None else None,
            "resultHash": content_hash(run["resultSnapshot"]) if issue is None else None}


def freeze_calculation_basis(run: dict, *, slot: str, project_id: str, tenant_id: str, selection: dict) -> dict:
    if (run.get("calculationRunId") != selection["calculationRunId"] or str(run.get("projectId")) != project_id
            or str(run.get("tenantId") or "legacy") != tenant_id):
        raise LookupError("선택한 계산을 찾을 수 없습니다.")
    issue = calculation_issue(run, slot)
    if issue:
        raise ValueError(issue)
    if (content_hash(run["inputSnapshot"]) != selection["inputHash"]
            or content_hash(run["resultSnapshot"]) != selection["resultHash"]):
        raise ValueError("선택한 계산 기록이 변경되었습니다. 계산 목록을 다시 불러오세요.")
    return deepcopy(run)


def _calculation_block(runs: dict, slot: str) -> dict:
    run = runs.get(slot)
    if run is None:
        return {"상태": "미확인 — 저장 계산 미선택"}
    result = run["resultSnapshot"]
    # The developer Pod contains additional derived engine runs. Keep them in
    # the JSON snapshot, but do not present them as separately selected results.
    if run["engineKey"] == "development_feasibility":
        result = {key: result[key] for key in ("revenue", "costs", "developerReturns", "dataQuality")}
    return {"계산 기록": run["calculationRunId"], "계산 시각": run.get("createdAt") or "미기록",
            "저장된 결과": deepcopy(result)}


def calculation_sections(runs: dict, *, note: str = CALCULATION_NOTE) -> dict:
    return {
        "businessFeasibility": {"검토 한계": note,
            **{CALCULATION_SLOTS[slot][0]: _calculation_block(runs, slot)
               for slot in ("feasibility", "pf", "returns", "unsold_ltv")}},
        "salesRevenueDistribution": {"검토 한계": note,
            "상환순서 시나리오": _calculation_block(runs, "waterfall")},
        "exitStrategy": {"검토 한계": note,
            "트랜치 회수 시나리오": _calculation_block(runs, "tranche_recovery")},
    }


def attach_calculation_bases(report: dict, runs: dict) -> None:
    report["calculationSnapshots"] = deepcopy(runs)
    report["calculationSnapshotsHash"] = content_hash(runs)
    report["reportEligible"] = False
    report["sections"].update(calculation_sections(runs))
    if "cashFlowCalculationSnapshot" in report:
        # A combined draft also has explicitly selected non-CF sections.
        report["sections"]["summaryCashFlow"]["assumption"] = CALCULATION_NOTE
        for source in report.get("calculationSources", []):
            if source.get("적용 범위") == "12. 요약 현금흐름표":
                source["검토 한계"] = CALCULATION_NOTE
    for slot, run in runs.items():
        report.setdefault("calculationSources", []).append({
            "적용 항목": CALCULATION_SLOTS[slot][0], "계산 기록": run["calculationRunId"],
            "입력 검증값": content_hash(run["inputSnapshot"]), "결과 검증값": content_hash(run["resultSnapshot"]),
            "검토 한계": CALCULATION_NOTE, "원본 위치": deepcopy(run.get("sourceLocator") or {}),
            "증거 기록": deepcopy(run.get("inputEvidenceIds") or []),
            "가정 기록": deepcopy(run.get("assumptionIds") or []),
            "입력 출처": deepcopy(run["inputSnapshot"].get("sourceMetadata") or {}),
        })


def verify_calculation_bases(report: dict, *, project_id: str, tenant_id: str | None = None) -> None:
    runs = report["calculationSnapshots"]
    if (not isinstance(runs, dict) or not runs or set(runs) - CALCULATION_SLOTS.keys()
            or report.get("reportEligible") is not False or report["calculationSnapshotsHash"] != content_hash(runs)):
        raise ValueError("Invalid calculation snapshot bundle")
    for slot, run in runs.items():
        if (str(run.get("projectId")) != project_id
                or (tenant_id is not None and str(run.get("tenantId") or "legacy") != tenant_id)
                or calculation_issue(run, slot)):
            raise ValueError("Invalid calculation scope or payload")
    note = CONNECTED_CALCULATION_NOTE if "financingBasis" in report else CALCULATION_NOTE
    for key, expected in calculation_sections(runs, note=note).items():
        if report["sections"].get(key) != expected:
            raise ValueError("Report values differ from saved calculations")


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
