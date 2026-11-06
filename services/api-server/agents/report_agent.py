from typing import Dict, Any, List
from copy import deepcopy
from math import isfinite

from core.calculations import (
    calculate_exit_sale_rate,
    calculate_sales_value_ltv,
    normalize_tranche_priority,
)
from core.schedule import schedule_event_map
from schemas.report import PHASE_11_SECTION_ORDER, PHASE_11_TEMPLATE
from schemas.scenario import ScenarioSetResult
from agents.report_export_agent import ReportExportAgent


def _looks_corrupted_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    markers = ["?좎", "?먭", "?섏", "?쒓", "?뱀", "?꾩", "?ㅽ", "梨", "遺꾩", "湲", "諛", "媛"]
    return any(marker in value for marker in markers)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", ""))
        return number if isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _sum_amounts(tranches: List[Dict[str, Any]]) -> float | None:
    amounts = [_number(item.get("amount")) for item in tranches]
    return sum(amounts) if amounts and all(amount is not None for amount in amounts) else None


def _amount_text(value: Any) -> str:
    number = _number(value)
    return f"{ReportExportAgent.whole_number_text(number)}원" if number is not None else "미확인"


def _percent_text(value: Any) -> str:
    number = _number(value)
    return ReportExportAgent.presentation_value(number, "interest_rate") if number is not None else "미확인"


class ReportAgent:
    def __init__(self):
        pass

    @staticmethod
    def _saved_scenario(data: Dict[str, Any]):
        value = data.get("scenariosEvaluation")
        if not isinstance(value, dict):
            return None
        try:
            return ScenarioSetResult.model_validate(value)
        except ValueError:
            return None

    def generate_cash_flow_comparison_review(self, snapshot: Dict[str, Any], snapshot_hash: str) -> Dict[str, Any]:
        """Describe two frozen calculations; never calculate from today's project."""
        comparison = snapshot["comparison"]
        baseline, changed = snapshot["baselineRun"], snapshot["comparisonRun"]

        def policy_text(info):
            if info.get("policyMode") == "reviewed_monthly_v1":
                return "월초 잔액 이자·최초 인출 약정수수료·M0 기준 만기 일시상환"
            return "저장된 금융비용 예산 배분(월별 이자·원금상환 잔액 미산정)"

        def review_reason(run):
            review = (run.get("inputSnapshot") or {}).get("reviewedMonthlyFinancingReview")
            value = review.get("reason") if isinstance(review, dict) else None
            return value.strip() if isinstance(value, str) and value.strip() else "미기록"

        def value_text(value, path):
            if value is None:
                return "미입력"
            if isinstance(value, bool):
                return "적용" if value else "미적용"
            if isinstance(value, (dict, list)):
                return "세부 조건 변경(고정 원본 기록 참조)"
            if isinstance(value, (int, float)):
                lower = path.lower()
                if "interest" in lower or "fee" in lower or "rate" in lower:
                    return _percent_text(value)
                if "month" in lower or "maturity" in lower:
                    return f"{ReportExportAgent.whole_number_text(value)}개월"
                if any(word in lower for word in ("cost", "revenue", "amount", "price", "loan", "funding")):
                    return _amount_text(value)
                return ReportExportAgent.whole_number_text(value)
            return {"senior": "선순위", "mezzanine": "중순위", "junior": "후순위", "equity": "자기자본",
                "bridge": "브리지 대출", "other": "기타", "repayment_reserve": "분양대금 상환계정", "sales_proceeds": "분양대금"}.get(str(value), str(value))

        metric_rows, metric_lines, missing = [], [], []
        for metric in comparison["metrics"]:
            label = metric["label"]
            before, after, delta = (_amount_text(metric.get(key)) for key in ("baselineValue", "comparisonValue", "delta"))
            metric_rows.append({"업무 지표": label, "기준안": before, "변경안": after, "차이": delta})
            metric_lines.append(f"| {label} | {before} | {after} | {delta} |")
            if metric.get("status") != "available":
                missing.append(f"{label}: 저장된 계산에 양쪽 값을 비교할 근거가 없습니다. 기존 예산 배분은 잔여 대출을 산정하지 않습니다."
                    if metric.get("key") == "outstandingDebt" else f"{label}: 저장된 계산에 해당 값 또는 산출 근거가 없습니다.")
        changes = [{"확인 항목": row["label"], "변경 전": value_text(row.get("before"), row["path"]),
                    "변경 후": value_text(row.get("after"), row["path"])} for row in comparison.get("inputChanges", [])[:100]]
        caveats = ["검토용 초안이며 최종 승인·대출조건 확정·투자의사결정용 보고서가 아닙니다."]
        if "period_mismatch" in comparison.get("reasons", []):
            caveats.append("두 계산의 기간 또는 기준월이 달라 사업 전체 집계만 비교합니다. 월별 수치를 같은 달로 정렬하지 않았습니다.")
        if "financing_policy_mismatch" in comparison.get("reasons", []):
            caveats.append("금융비용 계산 방식이 다릅니다. 입력 조건과 계산 방식을 함께 검토해야 합니다.")
        if comparison.get("sourceChanged"):
            caveats.append("두 계산의 원본 또는 근거가 다릅니다. 원본 위치와 저장 시점의 차이를 확인하세요.")
        if comparison.get("inputChangesTruncated") or len(comparison.get("inputChanges", [])) > 100:
            caveats.append("본문에는 변경 항목 중 앞 100개만 표시합니다. 전체 비교와 원시 기록은 고정 스냅샷에 보존했습니다.")
        if any((comparison.get(side) or {}).get("policyMode") == "reviewed_monthly_v1" for side in ("baseline", "comparison")):
            caveats.append("월별 검토 계산은 비회전 대출·만기 상환계정 원금상환만 지원합니다. 사용자 지정 자기자본 투입표·개별 인출표·분할 원금상환·이자 자본화·연체가산·변동금리는 지원하지 않습니다.")

        sources = []
        for label, run, info in (("기준안", baseline, comparison["baseline"]), ("변경안", changed, comparison["comparison"])):
            meta = (run.get("inputSnapshot") or {}).get("sourceMetadata") or {}
            locator = run.get("sourceLocator") or {}
            meta = meta if isinstance(meta, dict) else {}
            locator = locator if isinstance(locator, dict) else {}
            name = meta.get("sourceName")
            name = name if isinstance(name, str) and name.strip() else "원본 문서명 미확인"
            name = {"FeasibilityStudyV2": "사업수지 검토 입력", "CashFlowRunRequest": "현금흐름 계산 입력"}.get(name, name)
            positions = [str(locator[key]) for key in ("documentName", "fileName", "sheetName", "sheet_name", "cell", "page") if locator.get(key) is not None]
            sources.append({"구분": label, "계산 기록": run["calculationRunId"], "저장 시각": run.get("createdAt") or "미확인",
                "원본": str(name), "원본 위치": " · ".join(positions) or "문서 위치 미확인(저장된 계산 기록 기준)",
                "입력 검증값(앞 12자리)": info["inputHash"][:12], "결과 검증값(앞 12자리)": info["resultHash"][:12]})
        project = (baseline.get("inputSnapshot") or {}).get("project")
        project_name = project.get("projectName") if isinstance(project, dict) else None
        project_name = project_name if isinstance(project_name, str) and project_name.strip() else f"프로젝트 {snapshot['projectId']}"
        sections = {
            "comparisonSummary": {"대상 사업": project_name, "고정 시각": snapshot["capturedAt"], "금액 표시": "원 단위, 표시값은 반올림", "주요 지표": metric_rows},
            "assumptions": {"기준안 계산 방식": policy_text(comparison["baseline"]), "변경안 계산 방식": policy_text(comparison["comparison"]),
                "기준안 검토 이유": review_reason(baseline), "변경안 검토 이유": review_reason(changed),
                "확인할 입력 변경": changes or ["업무 입력 변경 없음"]},
            "limitations": {"검토 한계": caveats, "미확인 지표": missing or ["제시된 비교 지표는 양쪽 저장값으로 확인했습니다."]},
            "sources": {"저장된 원본": sources, "고정 검증값(앞 16자리)": snapshot_hash[:16],
                "보존 범위": "두 계산의 입력·결과·출처 원시 기록은 이 초안에 고정 보존했습니다. 이후 입력이나 선택 변경으로 이 초안의 수치는 바뀌지 않습니다."},
        }
        reserve_lines = []
        reserve = comparison.get("reserveReview")
        if isinstance(reserve, dict):
            basis_points = round(reserve["ratePercent"] * 100)
            rate_label = f"{basis_points // 100}%" if basis_points % 100 == 0 else f"{basis_points}bp (100bp = 1%)"
            reserve_values = {"적용 예비비율": rate_label}
            for key, label in (
                ("baselineIncludedReserveWon", "기준안에 이미 포함된 예비비"),
                ("comparisonIncludedReserveWon", "변경안에 이미 포함된 예비비"),
                ("baselineCostExcludingReserveWon", "기준안 예비비 제외 사업비"),
                ("comparisonCostExcludingReserveWon", "변경안 예비비 제외 사업비"),
                ("targetReserveWon", "검토 예비비"),
                ("reviewBudgetWon", "예비비 반영 검토 예산"),
                ("costIncreaseWon", "비교 사업비 증가액"),
                ("absorbedWon", "예비비 충당 가능액"),
                ("remainingWon", "남은 예비비"),
                ("excessWon", "예비비 초과액"),
            ):
                reserve_values[label] = _amount_text(reserve[key])
            sections["comparisonSummary"]["사업비 예비비 검토"] = reserve_values
            sections["assumptions"]["예비비 검토 이유"] = reserve["reason"]
            caveats.append("예비비는 비교 예산에만 적용한 사용자 가정입니다. 현금흐름과 승인 사업비는 각 저장 계산을 따릅니다.")
            reserve_lines = ["### 사업비 예비비 검토", "",
                             *[f"- {label} {value}" for label, value in reserve_values.items()],
                             f"- 검토 이유 {' '.join(reserve['reason'].split())}", ""]
        input_reviews = [{"계산": side, "항목": row["label"], "구분": row["classification"],
                          "검토 이유": row["reason"], "원본 위치": " · ".join(str(part) for part in (row.get("sourceName"), row.get("sourceCell")) if part) or "원본 근거 미지정"}
                         for side, key in (("기준안", "baseline"), ("변경안", "comparison"))
                         for row in comparison[key].get("inputReviews", [])]
        if input_reviews:
            sections["assumptions"]["입력 근거와 사용자 가정"] = input_reviews
        order = [{"index": index, "key": key, "title": title} for index, (key, title) in enumerate((
            ("comparisonSummary", "저장된 계산 비교"), ("assumptions", "계산 방식과 변경 조건"),
            ("limitations", "검토 한계와 미확인 사항"), ("sources", "원본과 저장 기준")), 1)]
        report = {"template": "cash-flow-comparison-review-v1", "reportType": "cash-flow-comparison-review",
            "title": "현금흐름 비교 검토 초안", "reportEligible": False, "displayUnit": "원", "sectionOrder": order, "sections": sections,
            "qualityChecks": {"actualSectionCount": 4, "expectedSectionCount": 4, "isComplete": True},
            "cashFlowComparisonSnapshot": deepcopy(snapshot), "cashFlowComparisonSnapshotHash": snapshot_hash}
        lines = ["# 현금흐름 비교 검토 초안", "", f"대상 사업: {project_name}", "", "> 검토용 초안 — 최종 승인·대출조건 확정용 보고서가 아닙니다.", "", ReportExportAgent.DISPLAY_NOTE, "",
            "## 1. 저장된 계산 비교", "", "금액은 원 단위이며 표시값은 반올림했습니다.", "",
            "| 업무 지표 | 기준안 | 변경안 | 차이 |", "| --- | ---: | ---: | ---: |", *metric_lines, "", *reserve_lines,
            "## 2. 계산 방식과 변경 조건", "", f"기준안: {policy_text(comparison['baseline'])}", f"기준안 검토 이유: {review_reason(baseline)}",
            f"변경안: {policy_text(comparison['comparison'])}", f"변경안 검토 이유: {review_reason(changed)}", ""]
        lines.extend(f"- {row['확인 항목']}: {row['변경 전']} → {row['변경 후']}" for row in changes)
        if not changes:
            lines.append("업무 입력 변경 없음")
        if input_reviews:
            lines.extend(["", "입력 근거와 사용자 가정(계산 당시 저장값):"])
            lines.extend(f"- {row['계산']} {row['항목']} · {row['구분']} · {row['검토 이유']} · {row['원본 위치']}" for row in input_reviews)
        lines.extend(["", "## 3. 검토 한계와 미확인 사항", "", *[f"- {item}" for item in caveats + missing], "", "## 4. 원본과 저장 기준", ""])
        lines.extend(f"- {row['구분']}: {row['계산 기록']} · {row['원본']} · {row['원본 위치']} · 저장 {row['저장 시각']}" for row in sources)
        lines.extend(["", f"고정 시각: {snapshot['capturedAt']}", f"고정 검증값(앞 16자리): {snapshot_hash[:16]}", "", sections["sources"]["보존 범위"]])
        return {"structuredReport": report, "reportMarkdown": "\n".join(lines)}

    def generate_structured_report(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Builds the canonical Phase 11 20-section PF review report payload.
        The payload is intentionally deterministic so UI, exports, and tests can share it.
        """
        project = input_data.get("project", {})
        land = input_data.get("land", {})
        feasibility = input_data.get("feasibility_input", {})
        covenants = input_data.get("covenants", {})
        tranches = input_data.get("pf_structure", [])

        # Deck headline engines injected by the orchestration run (may be absent
        # when the report is generated outside a full PF review run).
        return_metrics = input_data.get("returnMetrics") or {}
        stress_test = input_data.get("stressTest") or {}
        credit_enhancement = input_data.get("creditEnhancement") or {}
        developability = input_data.get("developabilityScore") or {}
        rm_metrics = return_metrics.get("metrics", {}) if isinstance(return_metrics, dict) else {}
        stress_scenarios = stress_test.get("scenarios", []) if isinstance(stress_test, dict) else []
        stress_scenarios = [s for s in stress_scenarios if isinstance(s, dict)] if isinstance(stress_scenarios, list) else []
        base_stress = next((s for s in stress_scenarios if s.get("key") == "base"), None)

        def _metric_value(block: Dict[str, Any], key: str):
            entry = block.get(key, {}) if isinstance(block, dict) else {}
            return _number(entry.get("value")) if isinstance(entry, dict) else None

        total_pf = _sum_amounts(tranches)
        total_revenue = _number(feasibility.get("total_revenue"))
        total_cost = _number(feasibility.get("total_cost"))
        finance_cost = _number(feasibility.get("finance_cost"))
        development_profit = total_revenue - total_cost if total_revenue is not None and total_cost is not None else None
        profit_margin = development_profit / total_revenue if development_profit is not None and total_revenue else None
        sales_ltv = calculate_sales_value_ltv(total_pf, total_revenue) if total_pf is not None and total_revenue and total_revenue > 0 else None
        exit_rate = calculate_exit_sale_rate(total_pf, finance_cost, total_revenue) if total_pf is not None and finance_cost is not None and total_revenue and total_revenue > 0 else None

        # Rendering reads the captured result and never runs a scenario engine.
        scenario_result = self._saved_scenario(input_data)

        precedent_conditions = list(covenants.get("drawdown_conditions", []))
        if scenario_result:
            precedent_conditions.append(scenario_result.reportFragments.approvalCondition)

        sections = {
            "cover": self._structured_cover(project),
            "financialTerms": self._structured_financial_terms(tranches, total_pf),
            "claimPreservation": {
                "items": self._as_label_items(covenants.get("claim_preservation", [])),
                "summary": "신탁, 책임준공, 보증 및 covenant를 통한 복합 채권보전 구조입니다.",
                "creditEnhancement": {
                    "enhancementGrade": credit_enhancement.get("enhancementGrade"),
                    "baseSeniorLtv": credit_enhancement.get("baseSeniorLtv"),
                    "adjustedSeniorLtv": credit_enhancement.get("adjustedSeniorLtv"),
                    "totalSeniorLtvDeltaPp": credit_enhancement.get("totalSeniorLtvDeltaPp"),
                    "activeCount": credit_enhancement.get("activeCount"),
                    "missingCriticalMeasures": credit_enhancement.get("missingCriticalMeasures"),
                    "reportParagraph": credit_enhancement.get("reportParagraph"),
                } if credit_enhancement else None,
            },
            "discountTrigger": {
                "triggers": [
                    {
                        "month": t.get("month"),
                        "targetRate": t.get("target_rate", 0),
                        "discountRate": t.get("discount", 0),
                        "condition": "목표분양률 미달 시 할인분양 실행",
                    }
                    for t in covenants.get("discount_triggers", [])
                ],
            },
            "fundingStructure": self._structured_funding(feasibility, total_pf),
            "useOfFunds": {
                "unit": "KRW",
                "items": [
                    {
                        "item": u.get("item", "자료 미확인"),
                        "amount": _number(u.get("amount")),
                        "amountMillion": self._million(u.get("amount")),
                    }
                    for u in input_data.get("use_of_funds", [])
                ],
            },
            "landSecuring": self._structured_land(input_data, land),
            "projectSchedule": self._structured_schedule(input_data.get("schedule", {})),
            "financialStructureDiagram": self._structured_diagram(input_data),
            "salesPaymentSchedule": self._structured_sales_schedule(input_data.get("sales_schedule", {})),
            "salesRevenueDistribution": {
                "policy": scenario_result.reportFragments.waterfallSummary if scenario_result else covenants.get("revenue_distribution", "대주단 협의 기준에 따름"),
                "waterfall": [w.model_dump() for w in scenario_result.waterfallScenarios] if scenario_result else covenants.get("execution_order", []),
            },
            "summaryCashFlow": self._structured_cashflow(input_data),
            "borrowerAnalysis": self._structured_party(input_data.get("developer_info", {}), "시행사"),
            "contractorAnalysis": self._structured_party(input_data.get("contractor_info", {}), "시공사"),
            "marketAnalysis": self._structured_market(input_data),
            "businessFeasibility": {
                "scenarioBasis": "저장된 시나리오 결과입니다. 현재 입력과의 일치 여부는 계산 이력에서 확인해야 합니다."
                    if scenario_result else "사용할 수 있는 저장 시나리오가 없습니다.",
                "metrics": {
                    "salesValueLtv": sales_ltv,
                    "ltv": sales_ltv,
                    "ltvBasis": "gross_sales_value",
                    "exitSalesRate": exit_rate,
                    "profitMargin": profit_margin,
                    "developmentProfit": development_profit,
                    "developmentProfitMillion": self._million(development_profit),
                    "projectIrr": (
                        _metric_value(rm_metrics, "projectIrr")
                        if _metric_value(rm_metrics, "projectIrr") is not None
                        else (base_stress.get("projectIrr") if base_stress else None)
                    ),
                    "equityIrr": _metric_value(rm_metrics, "equityIrr"),
                    "equityMultiple": _metric_value(rm_metrics, "equityMultiple"),
                    "paybackMonths": (rm_metrics.get("paybackPeriodMonths", {}) or {}).get("value") if isinstance(rm_metrics, dict) else None,
                    "icr": _metric_value(rm_metrics, "icr"),
                    "unsoldLtvBase": scenario_result.unsoldLtvMatrix.summary["baseCaseLtv"] if scenario_result else None,
                    "unsoldLtvStress": scenario_result.unsoldLtvMatrix.summary["stressCaseLtv"] if scenario_result else None,
                },
                "metricSourceFields": {
                    "developmentProfit": ["feasibility_input.total_revenue", "feasibility_input.total_cost"],
                    "profitMargin": ["feasibility_input.total_revenue", "feasibility_input.total_cost"],
                    "salesValueLtv": ["pf_structure[].amount", "feasibility_input.total_revenue"],
                    "exitSalesRate": ["pf_structure[].amount", "feasibility_input.finance_cost", "feasibility_input.total_revenue"],
                },
                "returnMetrics": return_metrics or None,
                "stressTest": {
                    "scenarios": [
                        {
                            "key": s.get("key"),
                            "name": s.get("name"),
                            "projectIrr": s.get("projectIrr"),
                            "seniorStatus": s.get("seniorStatus"),
                            "statusLabel": s.get("statusLabel"),
                        }
                        for s in stress_scenarios
                    ],
                    "worstCaseKey": stress_test.get("worstCaseKey"),
                    "seniorBreakScenarioKey": stress_test.get("seniorBreakScenarioKey"),
                } if stress_scenarios else None,
                "developabilityScore": {
                    "overallScore": developability.get("overallScore"),
                    "grade": developability.get("grade"),
                    "drivers": developability.get("drivers", []),
                    "gaps": developability.get("gaps", []),
                    "dimensions": {
                        key: {"label": dim.get("label"), "score": dim.get("score"), "available": dim.get("available")}
                        for key, dim in (developability.get("dimensions") or {}).items()
                    },
                    "summary": developability.get("summary"),
                } if developability else None,
                "summary": scenario_result.reportFragments.unsoldLtvSummary if scenario_result else "LTV, Exit 분양률, 수익률을 기준으로 대출 회수 가능성을 점검합니다.",
            },
            "exitStrategy": self._structured_exit_strategy(input_data, covenants, scenario_result),
            "drawdownConditions": {
                "precedent": precedent_conditions,
                "subsequent": covenants.get("subsequent_conditions", ["분양률 및 공정률 정기 보고", "자금집행 증빙 제출"]),
            },
            "eodTriggers": {
                "automatic": covenants.get("automatic_eod", []),
                "optional": covenants.get("eod", []),
            },
            "fundExecutionOrder": {
                "order": covenants.get("execution_order", []),
                "repaymentRule": "분양수입금은 약정된 waterfall에 따라 선순위 비용, 이자, 원금, 후순위 채권 순으로 충당합니다.",
            },
        }

        self._attach_imported_risk_report_sections(sections, input_data.get("riskReportSections") or {})
        self._normalize_korean_report_labels(sections)
        basis_review = input_data.get("cashFlowBasisReview")
        basis_warnings = []
        if isinstance(basis_review, dict):
            if basis_review.get("status") in {"blocking", "needs_review"}:
                warning = "입력 기준 확인 필요: 승인 현금흐름과 작업용 사업수지의 매출·사업비 기준을 확인하세요. 기준을 확인하기 전에는 함께 확정된 계산으로 사용할 수 없습니다."
                basis_warnings.append(warning)
                sections["summaryCashFlow"]["assumption"] += " " + warning
                sections["businessFeasibility"]["summary"] += " " + warning
        quality_checks = self._quality_checks(sections)
        if isinstance(basis_review, dict):
            quality_checks["inputBasisReview"] = deepcopy(basis_review)
            quality_checks["inputBasisConsistent"] = basis_review.get("status") == "clear"
            quality_checks["inputBasisWarnings"] = basis_warnings

        return {
            "template": PHASE_11_TEMPLATE,
            "title": "AI PF 심사보고서",
            "status": "draft",
            "unit": "KRW",
            "displayUnit": "백만원",
            "sectionOrder": [
                {"index": idx + 1, "key": key, "title": title}
                for idx, (key, title) in enumerate(PHASE_11_SECTION_ORDER)
            ],
            "sections": sections,
            "qualityChecks": quality_checks,
        }

    @staticmethod
    def _normalize_korean_report_labels(sections: Dict[str, Any]) -> None:
        claim = sections.get("claimPreservation")
        if isinstance(claim, dict):
            claim["summary"] = "입력된 채권보전 방안이며 약정과 증빙 확인이 필요합니다." if claim.get("items") else "채권보전 방안과 약정 증빙이 미확인 상태입니다."

        discount = sections.get("discountTrigger", {})
        for trigger in discount.get("triggers", []) if isinstance(discount, dict) else []:
            if isinstance(trigger, dict):
                trigger["condition"] = "목표분양률 미달 시 할인분양을 실행합니다."

        use_of_funds = sections.get("useOfFunds", {})
        for item in use_of_funds.get("items", []) if isinstance(use_of_funds, dict) else []:
            if isinstance(item, dict) and _looks_corrupted_text(item.get("item")):
                item["item"] = "용도 미확정"

        distribution = sections.get("salesRevenueDistribution")
        if isinstance(distribution, dict) and _looks_corrupted_text(distribution.get("policy")):
            distribution["policy"] = "대주단 약정 기준에 따른 자금 배분"

        business = sections.get("businessFeasibility")
        if isinstance(business, dict) and _looks_corrupted_text(business.get("summary")):
            business["summary"] = "LTV, Exit 분양률, 수익률을 기준으로 대출 회수 가능성을 평가합니다."

        drawdown = sections.get("drawdownConditions")
        if isinstance(drawdown, dict):
            subsequent = drawdown.get("subsequent")
            if isinstance(subsequent, list) and any(_looks_corrupted_text(value) for value in subsequent):
                drawdown["subsequent"] = ["분양률 및 공정률 정기 보고", "자금집행 증빙 제출"]

        execution = sections.get("fundExecutionOrder")
        if isinstance(execution, dict) and _looks_corrupted_text(execution.get("repaymentRule")):
            execution["repaymentRule"] = (
                "분양수입금은 약정상 waterfall에 따라 선순위 비용, 이자, 원금, 후순위 채권 순으로 충당합니다."
            )

    def generate_report(self, input_data: Dict[str, Any]) -> str:
        from agents.report_export_agent import ReportExportAgent

        return ReportExportAgent().export({"report": self.generate_structured_report(input_data)}, "markdown")["content"]

    def _structured_cover(self, project: Dict[str, Any]) -> Dict[str, Any]:
        name = project.get("name", "자료 미확인")
        return {
            "projectName": name,
            "reviewDate": project.get("review_date", "자료 미확인"),
            "financialInstitution": project.get("financial_institution", "자료 미확인"),
            "reviewSummary": f"{name} 개발사업 PF 대출 건에 대한 조건, 보전, 사업성 및 회수 가능성 검토입니다.",
        }

    def _structured_financial_terms(self, tranches: List[Dict[str, Any]], total_pf: float) -> Dict[str, Any]:
        return {
            "totalAmount": total_pf,
            "totalAmountMillion": self._million(total_pf),
            "tranches": [
                {
                    "name": t.get("tranche", "자료 미확인"),
                    "amount": _number(t.get("amount")),
                    "amountMillion": self._million(t.get("amount")),
                    "interestRate": self._rate(t, "interest_rate", "interestRate"),
                    "feeRate": self._rate(t, "fee_rate", "feeRate"),
                    "allInCost": self._all_in_cost(t),
                    "priority": t.get("priority"),
                }
                for t in tranches
            ],
        }

    def _structured_funding(self, feasibility: Dict[str, Any], total_pf: float) -> Dict[str, Any]:
        total_revenue = _number(feasibility.get("total_revenue"))
        cash_out = [
            ("토지비", _number(feasibility.get("land_cost"))),
            ("공사비", _number(feasibility.get("construction_cost"))),
            ("금융비용", _number(feasibility.get("finance_cost"))),
            ("기타사업비", _number(feasibility.get("other_cost"))),
        ]
        return {
            "cashIn": [
                {"item": "PF 대출", "amount": total_pf, "amountMillion": self._million(total_pf)},
                {"item": "분양수입", "amount": total_revenue, "amountMillion": self._million(total_revenue)},
            ],
            "cashOut": [
                {"item": item, "amount": amount, "amountMillion": self._million(amount)}
                for item, amount in cash_out
            ],
        }

    def _structured_land(self, data: Dict[str, Any], land: Dict[str, Any]) -> Dict[str, Any]:
        parcels = data.get("land_acquisition", [])
        if not parcels and land:
            parcels = [{
                "owner": "자료 미확인",
                "share": None,
                "area_sqm": land.get("area_sqm"),
                "mortgage": "자료 미확인",
            }]
        return {
            "address": land.get("address", "자료 미확인"),
            "pnu": land.get("pnu", "자료 미확인"),
            "parcels": [
                {
                    "owner": p.get("owner", "자료 미확인"),
                    "parcel": p.get("addressFragment") or p.get("lotNumber") or "자료 미확인",
                    "share": _number(p.get("share")),
                    "areaSqm": _number(p.get("area_sqm", p.get("areaSqm"))),
                    "mortgage": p.get("mortgage", "자료 미확인"),
                }
                for p in parcels
            ],
        }

    def _structured_schedule(self, schedule: Dict[str, Any]) -> List[Dict[str, Any]]:
        schedule = schedule_event_map(schedule)
        mapping = [
            ("land_purchase", "토지매입"),
            ("permit_approval", "사업인허가"),
            ("construction_start", "착공"),
            ("presale_start", "분양개시"),
            ("completion", "준공"),
        ]
        return [
            {"step": label, "plannedDate": schedule.get(key, "자료 미확인"), "status": "예정"}
            for key, label in mapping
        ]

    def _structured_diagram(self, data: Dict[str, Any]) -> Dict[str, Any]:
        project = data.get("project", {})
        developer = data.get("developer_info", {})
        contractor = data.get("contractor_info", {})
        return {
            "borrower": developer.get("name", "시행사"),
            "trustee": data.get("trustee", {}).get("name", "신탁사"),
            "contractor": contractor.get("name", "시공사"),
            "lenders": project.get("financial_institution", "대주단"),
            "description": "차주, 신탁사, 시공사, 대주단 및 수분양자 간 자금 흐름과 책임준공/우선수익권 구조를 표시합니다.",
        }

    def _structured_sales_schedule(self, schedule: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"type": "계약금", "ratio": _number(schedule.get("contract_deposit")), "timing": "계약 시"},
            {"type": "중도금", "ratio": _number(schedule.get("middle_payment")), "timing": "공정률 및 약정 회차별"},
            {"type": "잔금", "ratio": _number(schedule.get("balance")), "timing": "입주 지정기간"},
        ]

    def _structured_cashflow(self, data: Dict[str, Any]) -> Dict[str, Any]:
        saved = data.get("cashFlowRunResult")
        if not isinstance(saved, dict):
            return {"status": "not_assessable", "assumption": "저장된 현금흐름 계산이 없습니다. 계산 후 보고서를 다시 생성해 주세요.", "rows": []}
        return {
            "status": saved.get("status", "completed"),
            "assumption": "저장된 현금흐름 결과입니다. 현재 입력과의 일치 여부는 계산 이력에서 확인해야 합니다.",
            "summary": deepcopy(saved.get("summary") or {}),
            "rows": deepcopy(saved.get("monthlyCashFlow") or []),
            **({"periodBasis": deepcopy(saved["periodBasis"])} if "periodBasis" in saved else {}),
        }

    def _structured_party(self, party: Dict[str, Any], role: str) -> Dict[str, Any]:
        return {
            "role": role,
            "name": party.get("name", "자료 미확인"),
            "creditRating": party.get("credit_rating", "자료 미확인"),
            "financials": party.get("financials", "자료 미확인"),
            "orderBacklog": party.get("order_backlog", "자료 미확인"),
        }

    def _structured_market(self, data: Dict[str, Any]) -> Dict[str, Any]:
        land = data.get("land", {})
        project = data.get("project", {})
        return {
            "location": land.get("address", "자료 미확인"),
            "projectType": project.get("type", "자료 미확인"),
            "competitors": data.get("market", {}).get("competitors", "자료 미확인"),
            "priceReasonableness": data.get("market", {}).get("price_reasonableness", "자료 미확인"),
            "summary": "입지, 경쟁 공급, 분양가 적정성을 기준으로 분양성을 점검합니다.",
        }

    def _structured_exit_strategy(self, data: Dict[str, Any], covenants: Dict[str, Any], scenario_result: Any = None) -> Dict[str, Any]:
        fallback = [
            "할인분양 Trigger 실행",
            "미분양 담보대출 또는 대환",
            "시공사 책임준공 및 기타 신용보강 활용",
        ]
        if scenario_result:
            fallback.append(scenario_result.reportFragments.trancheRecoverySummary)
        return {
            "primary": "분양수입금 기반 PF 원리금 상환",
            "fallback": fallback,
            "discountTriggers": covenants.get("discount_triggers", []),
        }

    def _quality_checks(self, sections: Dict[str, Any]) -> Dict[str, Any]:
        missing = []
        for key, title in PHASE_11_SECTION_ORDER:
            if key not in sections:
                missing.append(title)
        return {
            "expectedSectionCount": len(PHASE_11_SECTION_ORDER),
            "actualSectionCount": len(sections),
            "missingSections": missing,
            "isComplete": len(missing) == 0 and len(sections) == len(PHASE_11_SECTION_ORDER),
        }

    def _attach_imported_risk_report_sections(self, sections: Dict[str, Any], imported: Dict[str, Any]) -> None:
        if not isinstance(imported, dict) or not imported:
            return
        paragraphs = imported.get("paragraphs") if isinstance(imported.get("paragraphs"), list) else []
        approval_conditions = imported.get("approvalConditions") if isinstance(imported.get("approvalConditions"), list) else []
        metrics = imported.get("metrics") if isinstance(imported.get("metrics"), dict) else {}
        cash_flow_summary = imported.get("cashFlowSummary") if isinstance(imported.get("cashFlowSummary"), list) else []
        source_map = imported.get("sourceMap") if isinstance(imported.get("sourceMap"), list) else []

        if paragraphs:
            sections.setdefault("summaryCashFlow", {})["importedRiskReportParagraphs"] = paragraphs
            exit_paragraphs = [
                paragraph
                for paragraph in paragraphs
                if "exit" in str(paragraph.get("section") or paragraph.get("category") or "").lower()
                or "Exit" in str(paragraph.get("section") or paragraph.get("category") or "")
            ]
            sections.setdefault("exitStrategy", {})["importedRiskReportParagraphs"] = exit_paragraphs or paragraphs
        if approval_conditions:
            sections.setdefault("drawdownConditions", {})["importedApprovalConditions"] = approval_conditions
        if metrics:
            sections.setdefault("businessFeasibility", {})["importedRiskWorkbookMetrics"] = metrics
        if cash_flow_summary:
            sections.setdefault("summaryCashFlow", {})["importedCashFlowSummary"] = cash_flow_summary
        if source_map:
            sections.setdefault("businessFeasibility", {})["importedSourceMap"] = source_map

    def _as_label_items(self, values: List[Any]) -> List[Dict[str, Any]]:
        return [{"label": str(value), "status": "반영"} for value in values]

    def _million(self, amount: Any) -> float | None:
        number = _number(amount)
        return round(number / 1_000_000, 2) if number is not None else None

    def _rate(self, data: Dict[str, Any], *keys: str, default: float | None = None) -> float | None:
        for key in keys:
            value = data.get(key)
            if value is None:
                continue
            return _number(value)
        return default

    def _all_in_cost(self, tranche: Dict[str, Any]) -> float | None:
        explicit = self._rate(tranche, "all_in_cost", "allInCost")
        if explicit is not None:
            return explicit
        interest = self._rate(tranche, "interest_rate", "interestRate")
        fee = self._rate(tranche, "fee_rate", "feeRate")
        return interest + fee if interest is not None and fee is not None else None
