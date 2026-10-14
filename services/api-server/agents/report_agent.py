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
from schemas.scenario import ScenarioSetRequest
from agents.scenario_engine_agent import ScenarioEngineAgent


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
    return f"{number:,.0f}원" if number is not None else "미확인"


def _percent_text(value: Any) -> str:
    number = _number(value)
    return f"{number * 100:g}%" if number is not None else "미확인"


class ReportAgent:
    def __init__(self):
        pass

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
                    return f"{value:g}개월"
                if any(word in lower for word in ("cost", "revenue", "amount", "price", "loan", "funding")):
                    return _amount_text(value)
                return f"{value:,g}"
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
        order = [{"index": index, "key": key, "title": title} for index, (key, title) in enumerate((
            ("comparisonSummary", "저장된 계산 비교"), ("assumptions", "계산 방식과 변경 조건"),
            ("limitations", "검토 한계와 미확인 사항"), ("sources", "원본과 저장 기준")), 1)]
        report = {"template": "cash-flow-comparison-review-v1", "reportType": "cash-flow-comparison-review",
            "title": "현금흐름 비교 검토 초안", "reportEligible": False, "displayUnit": "원", "sectionOrder": order, "sections": sections,
            "qualityChecks": {"actualSectionCount": 4, "expectedSectionCount": 4, "isComplete": True},
            "cashFlowComparisonSnapshot": deepcopy(snapshot), "cashFlowComparisonSnapshotHash": snapshot_hash}
        lines = ["# 현금흐름 비교 검토 초안", "", f"대상 사업: {project_name}", "", "> 검토용 초안 — 최종 승인·대출조건 확정용 보고서가 아닙니다.", "",
            "## 1. 저장된 계산 비교", "", "금액은 원 단위이며 표시값은 반올림했습니다.", "",
            "| 업무 지표 | 기준안 | 변경안 | 차이 |", "| --- | ---: | ---: | ---: |", *metric_lines, "",
            "## 2. 계산 방식과 변경 조건", "", f"기준안: {policy_text(comparison['baseline'])}", f"기준안 검토 이유: {review_reason(baseline)}",
            f"변경안: {policy_text(comparison['comparison'])}", f"변경안 검토 이유: {review_reason(changed)}", ""]
        lines.extend(f"- {row['확인 항목']}: {row['변경 전']} → {row['변경 후']}" for row in changes)
        if not changes:
            lines.append("업무 입력 변경 없음")
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

        # Build ScenarioSetRequest on the fly using project inputs to get rich sensitivity grids
        scenario_result = None
        try:
            raw_mix = input_data.get("product_mix") or input_data.get("productMix") or []
            product_mix = []
            for item in raw_mix:
                if isinstance(item, dict):
                    product_mix.append({
                        "type": item.get("type") or item.get("name") or "A",
                        "units": int(item.get("units") or item.get("unit_count") or 0),
                        "avgUnitPrice": float(item.get("avgUnitPrice") or item.get("avg_unit_price") or 0.0),
                        "totalRevenue": float(item.get("totalRevenue") or item.get("total_revenue") or 0.0)
                    })
            pf_structure = []
            for idx, item in enumerate(tranches):
                if isinstance(item, dict):
                    pf_structure.append({
                        "tranche": item.get("tranche") or item.get("name") or f"Tr.{idx+1}",
                        "amount": float(item.get("amount") or 0.0),
                        "priority": normalize_tranche_priority(item.get("priority"), idx + 1),
                        "interestRate": float(item.get("interest_rate") or item.get("interestRate") or 0.0),
                        "feeRate": float(item.get("fee_rate") or item.get("feeRate") or 0.0)
                    })
            if product_mix and pf_structure:
                req = ScenarioSetRequest(
                    scenarioSetId="auto-report-set",
                    scenarioSetName="Auto Generated Report Scenario Set",
                    productMix=product_mix,
                    pfStructure=pf_structure,
                )
                scenario_result = ScenarioEngineAgent().run_scenario_set(req)
        except Exception:
            scenario_result = None

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
            "summaryCashFlow": self._structured_cashflow(input_data, total_revenue, total_cost, scenario_result),
            "borrowerAnalysis": self._structured_party(input_data.get("developer_info", {}), "시행사"),
            "contractorAnalysis": self._structured_party(input_data.get("contractor_info", {}), "시공사"),
            "marketAnalysis": self._structured_market(input_data),
            "businessFeasibility": {
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
            "qualityChecks": self._quality_checks(sections),
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
        """
        Generates a professional 20-section PF Review Report based on realistic sample data.
        """
        project = input_data.get("project", {})
        covenants = input_data.get("covenants", {})
        tranches = input_data.get("pf_structure", [])
        feasibility = input_data.get("feasibility_input", {})

        # Build ScenarioSetRequest on the fly using project inputs to get rich sensitivity grids
        scenario_result = None
        try:
            raw_mix = input_data.get("product_mix") or input_data.get("productMix") or []
            product_mix = []
            for item in raw_mix:
                if isinstance(item, dict):
                    product_mix.append({
                        "type": item.get("type") or item.get("name") or "A",
                        "units": int(item.get("units") or item.get("unit_count") or 0),
                        "avgUnitPrice": float(item.get("avgUnitPrice") or item.get("avg_unit_price") or 0.0),
                        "totalRevenue": float(item.get("totalRevenue") or item.get("total_revenue") or 0.0)
                    })
            pf_structure = []
            for idx, item in enumerate(tranches):
                if isinstance(item, dict):
                    pf_structure.append({
                        "tranche": item.get("tranche") or item.get("name") or f"Tr.{idx+1}",
                        "amount": float(item.get("amount") or 0.0),
                        "priority": normalize_tranche_priority(item.get("priority"), idx + 1),
                        "interestRate": float(item.get("interest_rate") or item.get("interestRate") or 0.0),
                        "feeRate": float(item.get("fee_rate") or item.get("feeRate") or 0.0)
                    })
            if product_mix and pf_structure:
                req = ScenarioSetRequest(
                    scenarioSetId="auto-report-set",
                    scenarioSetName="Auto Generated Report Scenario Set",
                    productMix=product_mix,
                    pfStructure=pf_structure,
                )
                scenario_result = ScenarioEngineAgent().run_scenario_set(req)
        except Exception:
            scenario_result = None

        md = self._build_cover(project)
        md += self._build_financial_terms(input_data)
        md += self._build_claim_preservation(covenants)
        md += self._build_discount_triggers(covenants)
        md += self._build_financing_structure(input_data)
        md += self._build_use_of_funds(input_data)
        md += self._build_land_acquisition(input_data)
        md += self._build_project_schedule(input_data)
        md += self._build_financial_structure_diagram(input_data)
        md += self._build_sales_schedule(input_data)
        md += self._build_revenue_distribution(covenants, scenario_result)
        md += self._build_cashflow_summary(input_data, scenario_result)
        md += self._build_borrower_analysis(input_data)
        md += self._build_contractor_analysis(input_data)
        md += self._build_marketability_analysis(input_data)
        md += self._build_feasibility_analysis(input_data)
        md += self._build_exit_strategy(input_data, scenario_result)
        md += self._build_drawdown_conditions(covenants, scenario_result)
        md += self._build_eod(covenants)
        md += self._build_fund_execution_order(covenants)
        
        return md

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

    def _structured_cashflow(self, data: Dict[str, Any], total_revenue: float, total_cost: float, scenario_result: Any = None) -> Dict[str, Any]:
        has_inputs = total_revenue is not None and total_cost is not None
        profit = total_revenue - total_cost if has_inputs else None
        stressed_revenue = total_revenue * 0.9 if total_revenue is not None else None
        rows = [
            {"scenario": "기준", "cashIn": total_revenue, "cashOut": total_cost, "netCashFlow": profit},
            {"scenario": "분양수입 10% 하락", "cashIn": stressed_revenue, "cashOut": total_cost, "netCashFlow": stressed_revenue - total_cost if has_inputs else None},
        ]
        if scenario_result:
            for w in scenario_result.waterfallScenarios:
                if w.scenarioId in {"sale_70_disc_0", "sale_50_disc_10"}:
                    label = "분양률 70% 시나리오" if "70" in w.scenarioId else "스트레스 분양률 50% (할인 10%)"
                    rows.append({
                        "scenario": label,
                        "cashIn": w.cashAvailable,
                        "cashOut": total_cost,
                        "netCashFlow": w.cashAvailable - total_cost if total_cost is not None else None
                    })
        return {
            "status": "calculated" if has_inputs else "not_assessable",
            "assumption": "분양률 기본 및 스트레스 시나리오 기준 요약 현금흐름입니다.",
            "rows": rows,
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

    def _build_cover(self, project: Dict[str, Any]) -> str:
        name = project.get("name", "Unknown Project")
        date = project.get("review_date", "TBD")
        fi = project.get("financial_institution", "Our Institution")
        
        return f"""# PF 심사보고서 (Draft)

| 항목 | 상세 내용 |
|:---|:---|
| **사업명** | {name} |
| **심의일** | {date} |
| **금융기관** | {fi} |
| **검토 의견** | 본건은 {name} 개발사업을 위한 PF 대출 건으로, 제반 조건 및 사업성 검토 결과 대출 실행이 적정할 것으로 판단됨. |

---
"""

    def _build_financial_terms(self, data: Dict[str, Any]) -> str:
        tranches = data.get("pf_structure", [])
        if not tranches:
            return "## 1. 주요 금융조건 요약표\n데이터 없음.\n\n"
            
        md = "## 1. 주요 금융조건 요약표\n\n"
        md += "| Tranche | 대출금액 | 금리 | 수수료 | All-in Cost | 우선순위 |\n"
        md += "|:---|---:|:---:|:---:|:---:|:---:|\n"
        for t in tranches:
            interest_rate = self._rate(t, "interest_rate", "interestRate")
            fee_rate = self._rate(t, "fee_rate", "feeRate")
            all_in_cost = self._all_in_cost(t)
            md += f"| {t.get('tranche')} | {_amount_text(t.get('amount'))} | {_percent_text(interest_rate)} | {_percent_text(fee_rate)} | {_percent_text(all_in_cost)} | {t.get('priority')}순위 |\n"
        return md + "\n"

    def _build_claim_preservation(self, covenants: Dict[str, Any]) -> str:
        claims = covenants.get("claim_preservation", [])
        md = "## 2. 주요 채권보전방안\n\n"
        if not claims:
            md += "- 채권보전 방안과 약정 증빙 미확인\n"
        else:
            for claim in claims:
                md += f"- **{claim}**\n"
        return md + "\n"

    def _build_discount_triggers(self, covenants: Dict[str, Any]) -> str:
        triggers = covenants.get("discount_triggers", [])
        md = "## 3. Discount Sale Trigger Conditions\n\n"
        if not triggers:
            md += "- No discount trigger supplied.\n"
        else:
            md += "| Month | Target sale rate | Discount rate | Note |\n|:---:|:---:|:---:|:---|\n"
            for t in triggers:
                target_rate = t.get("target_rate")
                if target_rate is None:
                    target_rate = t.get("targetSalesRate")
                discount = t.get("discount")
                if discount is None:
                    discount = t.get("allowedDiscountRate")
                md += f"| {t.get('month')} | {(target_rate or 0)*100}% | {(discount or 0)*100}% | Execute if target is missed |\n"
        return md + "\n"
    def _build_financing_structure(self, data: Dict[str, Any]) -> str:
        pf = _sum_amounts(data.get("pf_structure", []))
        rev = _number(data.get("feasibility_input", {}).get("total_revenue"))
        ltv = pf / rev if pf is not None and rev and rev > 0 else None
        
        md = "## 4. 자금조달 구조\n\n"
        md += f"- **총 조달금액(PF)**: {_amount_text(pf)}\n"
        md += f"- **총 분양수입(예상)**: {_amount_text(rev)}\n"
        md += f"- **LTV (분양수입 대비)**: {_percent_text(ltv)}\n"
        return md + "\n"

    def _build_use_of_funds(self, data: Dict[str, Any]) -> str:
        uses = data.get("use_of_funds", [])
        md = "## 5. 자금 사용 용도\n\n"
        if not uses:
            md += "데이터 없음.\n"
        else:
            md += "| 자금 집행 항목 | 금액(원) | 비고 |\n|:---|---:|:---|\n"
            for u in uses:
                amount = u.get("amount")
                amount_str = f"{amount:,}" if isinstance(amount, (int, float)) else "-"
                md += f"| {u.get('item')} | {amount_str} | 사업계획 기준 |\n"
        return md + "\n"

    def _build_land_acquisition(self, data: Dict[str, Any]) -> str:
        lands = data.get("land_acquisition", [])
        md = "## 6. 사업부지 확보 현황\n\n"
        if not lands:
            md += "데이터 없음.\n"
        else:
            md += "| 소유자/필지 | 지분율 | 면적(㎡) | 제한물권 및 특이사항 |\n|:---:|:---:|:---:|:---|\n"
            for l in lands:
                # Parsed 토지조서 parcels may lack owner/share and use camelCase
                # areaSqm; format defensively so a real ledger never breaks the report.
                owner = l.get("owner") or l.get("addressFragment") or l.get("lotNumber") or "-"
                share = l.get("share")
                share_str = f"{share * 100:g}%" if isinstance(share, (int, float)) else "-"
                area = l.get("area_sqm")
                if area is None:
                    area = l.get("areaSqm")
                area_str = f"{area:,.1f}" if isinstance(area, (int, float)) else "-"
                mortgage = l.get("mortgage") or l.get("landCategory") or "-"
                md += f"| {owner} | {share_str} | {area_str} | {mortgage} |\n"
        return md + "\n"

    def _build_project_schedule(self, data: Dict[str, Any]) -> str:
        s = schedule_event_map(data.get("schedule"))
        md = "## 7. 사업추진 일정\n\n"
        md += f"- **토지매입 완료**: {s.get('land_purchase', 'TBD')}\n"
        md += f"- **사업인허가 완료**: {s.get('permit_approval', 'TBD')}\n"
        md += f"- **착공 및 분양**: {s.get('construction_start', 'TBD')}\n"
        md += f"- **준공 및 입주**: {s.get('completion', 'TBD')}\n"
        return md + "\n"

    def _build_financial_structure_diagram(self, data: Dict[str, Any]) -> str:
        dev_name = data.get("developer_info", {}).get("name", "시행사")
        con_name = data.get("contractor_info", {}).get("name", "시공사")
        fi_name = data.get("project", {}).get("financial_institution", "대주단")
        
        md = "## 8. 금융구조도\n\n"
        md += "```mermaid\n"
        md += "graph TD;\n"
        md += f"  Lender[\"{fi_name} (대주단)\"] -- \"PF 대출금\" --> Trust[\"신탁사 (자금관리)\"];\n"
        md += f"  Trust -- \"공사비 지급\" --> Contractor[\"{con_name}\"];\n"
        md += f"  Contractor -- \"책임준공 확약\" --> Lender;\n"
        md += f"  Dev[\"{dev_name}\"] -- \"사업권/수익권\" --> Trust;\n"
        md += f"  Buyer[\"수분양자\"] -- \"분양대금\" --> Trust;\n"
        md += "```\n\n"
        return md

    def _build_sales_schedule(self, data: Dict[str, Any]) -> str:
        s = data.get("sales_schedule", {})
        md = "## 9. 분양대금 납입 일정\n\n"
        md += f"- **계약금**: {_percent_text(s.get('contract_deposit'))} (계약 시)\n"
        md += f"- **중도금**: {_percent_text(s.get('middle_payment'))} (회차별 분할)\n"
        md += f"- **잔금**: {_percent_text(s.get('balance'))} (입주 시)\n"
        return md + "\n"

    def _build_revenue_distribution(self, covenants: Dict[str, Any], scenario_result: Any = None) -> str:
        md = "## 10. 분양수입금 배분 기준\n\n"
        if scenario_result:
            md += f"{scenario_result.reportFragments.waterfallSummary}\n"
        else:
            md += f"{covenants.get('revenue_distribution', '대주단 협의 기준에 따름')}\n"
        return md + "\n"

    def _build_cashflow_summary(self, data: Dict[str, Any], scenario_result: Any = None) -> str:
        md = "## 11. 요약 현금흐름표\n\n"
        if scenario_result:
            md += "### 분양률/할인 시나리오별 자금회수 및 현금유입\n\n"
            md += "| 시나리오 ID | 분양률 | 할인율 | 회수 자금 | 리스크 경고 |\n"
            md += "|:---|:---|:---|:---|:---|\n"
            for w in scenario_result.waterfallScenarios:
                md += f"| {w.scenarioId} | {w.saleRate*100}% | {w.discountRate*100}% | {w.cashAvailable:,}원 | {'경고 발생' if w.totalShortfall > 0 else '정상 회수'} |\n"
        else:
            md += "현금흐름 시나리오 근거가 미확인 상태이며, 자금 부족액과 대출한도 충족 여부를 확정할 수 없습니다.\n"
        return md + "\n"

    def _build_borrower_analysis(self, data: Dict[str, Any]) -> str:
        dev = data.get("developer_info", {})
        md = "## 12. 차주(시행사) 분석\n\n"
        md += f"- **시행사명**: {dev.get('name', 'N/A')}\n"
        md += f"- **신용등급**: {dev.get('credit_rating', 'N/A')}\n"
        md += f"- **주요 재무현황**: {dev.get('financials', 'N/A')}\n"
        return md + "\n"

    def _build_contractor_analysis(self, data: Dict[str, Any]) -> str:
        con = data.get("contractor_info", {})
        md = "## 13. 시공사 분석\n\n"
        md += f"- **시공사명**: {con.get('name', 'N/A')}\n"
        md += f"- **신용등급**: {con.get('credit_rating', 'N/A')}\n"
        md += f"- **수주잔고**: {con.get('order_backlog', 'N/A')}\n"
        md += f"- **재무 건전성**: {con.get('financials', 'N/A')}\n"
        return md + "\n"

    def _build_marketability_analysis(self, data: Dict[str, Any]) -> str:
        md = "## 14. 분양성 분석\n\n"
        market = self._structured_market(data)
        md += f"- **비교 공급 근거**: {market['competitors']}\n"
        md += f"- **분양가 검토 입력**: {market['priceReasonableness']}\n"
        md += "비교사례와 흡수율 증빙 검토 전 목표 분양률 달성을 확정할 수 없습니다.\n"
        return md + "\n"

    def _build_feasibility_analysis(self, data: Dict[str, Any]) -> str:
        feasibility = data.get("feasibility_input", {})
        revenue = _number(feasibility.get("total_revenue"))
        cost = _number(feasibility.get("total_cost"))
        profit = revenue - cost if revenue is not None and cost is not None else None
        margin = profit / revenue if profit is not None and revenue else None
        md = "## 15. 사업성 분석 결과\n\n"
        md += f"- **예상 개발이익**: {_amount_text(profit)}\n"
        md += f"- **수익률(Margin)**: {_percent_text(margin)}\n"
        return md + "\n"

    def _build_exit_strategy(self, data: Dict[str, Any], scenario_result: Any = None) -> str:
        md = "## 16. Exit 전략 및 상환 재원\n\n"
        md += "분양수입금 상환, 할인분양 및 담보대출 대환을 검토할 수 있으며, 실제 상환재원과 대환 약정의 확보 여부는 별도 증빙 확인이 필요합니다.\n"
        if scenario_result:
            md += f"\n**[시나리오 스트레스 테스트 분석]**\n{scenario_result.reportFragments.trancheRecoverySummary}\n"
        return md + "\n"

    def _build_drawdown_conditions(self, covenants: Dict[str, Any], scenario_result: Any = None) -> str:
        conds = list(covenants.get("drawdown_conditions", []))
        if scenario_result:
            conds.append(scenario_result.reportFragments.approvalCondition)
        md = "## 17. 대출금 인출 선행조건\n\n"
        if not conds:
            md += "- 인허가 완료 및 신탁계약 체결 등\n"
        else:
            for c in conds:
                md += f"- {c}\n"
        return md + "\n"

    def _build_eod(self, covenants: Dict[str, Any]) -> str:
        eods = covenants.get("eod", [])
        md = "## 18. 기한이익상실(EOD) 사유\n\n"
        if not eods:
            md += "- 이자 미납, 책임준공 기한 초과 등\n"
        else:
            for e in eods:
                md += f"- {e}\n"
        return md + "\n"

    def _build_fund_execution_order(self, covenants: Dict[str, Any]) -> str:
        orders = covenants.get("execution_order", [])
        md = "## 19. 자금집행 순서\n\n"
        if not orders:
            md += "1. 필수사업비 2. 대출이자 3. 대출원금 순\n"
        else:
            for i, o in enumerate(orders):
                md += f"{o}\n"
        md += "\n## 20. 변제 충당 및 기타\n\n상기 순서에 의거하여 분양수입금을 배분하며, 대주단 동의 없는 자금 인출은 엄격히 제한함.\n"
        return md + "\n"
