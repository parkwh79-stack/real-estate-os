from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

from core.calculations import calculate_pf_metrics
from core.text_normalization import build_term_surface_record
from agents.orchestration_agent import OrchestrationAgent
from agents.credit_review_agent import CreditReviewAgent
from agents.market_evidence_agent import MarketEvidenceAgent
from agents.market_product_depth_agent import MarketProductDepthAgent
from agents.risk_register_agent import RiskRegisterAgent
from agents.scenario_engine_agent import ScenarioEngineAgent
from agents.pod_ai_reviewer_agent import PodAIReviewerAgent
from schemas.scenario import ScenarioSetRequest
from schemas.orchestration import (
    AgentPodPlan,
    AgentPodRun,
    ChiefDealOrchestrationPlan,
    DealStateValue,
    DecisionGateResult,
    MissingInputRegisterItem,
    RiskCommitteeOpinion,
    ScenarioConclusion,
    SharedDealStateGraph,
)
from use_cases.cash_flow import run_cash_flow_use_case
from use_cases.covenant_eod import evaluate_covenant_eod_use_case
from use_cases.developability_score import score_developability_use_case
from use_cases.development_feasibility import analyze_development_feasibility_use_case
from use_cases.product_planning import compare_product_planning_use_case
from use_cases.land_ledger_import import enrich_land_ledger_use_case
from use_cases.legal_review import review_legal_and_permits_use_case
from use_cases.legal_pod import run_legal_pod_use_case
from use_cases.marketability_review import analyze_marketability_use_case
from use_cases.track_workflows import get_track_workflow_definition, normalize_workflow_track
from use_cases.development_process import get_development_process

# An LLM opinion alone must never move a committee stance (AGENTS.md §1). We read
# the review's `deterministicFindings` (deterministic provenance guaranteed) and
# gate by SEVERITY, excluding only advisory categories.
_ADVISORY_REVIEW_CATEGORIES = {"guardrail", "info", "note"}


def _load_pod_ai_review_findings(project_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Latest deterministic-grounded blocking/high Pod AI Review findings per pod.

    Best-effort: any read/shape error yields {} so the committee still renders.
    Only the newest review per pod contributes (stale reviews are ignored)."""
    try:
        from storage import list_pod_ai_review_records

        reviews = list_pod_ai_review_records(str(project_id), limit=200)
    except Exception:
        return {}
    latest_by_pod: Dict[str, Dict[str, Any]] = {}
    for review in reviews:
        pod_key = str(review.get("podKey") or "")
        if not pod_key:
            continue
        current = latest_by_pod.get(pod_key)
        if current is None or str(review.get("updatedAt") or "") >= str(current.get("updatedAt") or ""):
            latest_by_pod[pod_key] = review
    findings_by_pod: Dict[str, List[Dict[str, Any]]] = {}
    for pod_key, review in latest_by_pod.items():
        if str(review.get("reviewStatus") or "").lower() in {"approved", "waived", "resolved"}:
            continue
        source = review.get("deterministicFindings")
        if not isinstance(source, list):
            source = review.get("findings") or []
        relevant = [
            finding
            for finding in source
            if isinstance(finding, dict)
            and str(finding.get("severity") or "").lower() in {"blocking", "high"}
            and str(finding.get("category") or "").lower() not in _ADVISORY_REVIEW_CATEGORIES
        ]
        if relevant:
            findings_by_pod[pod_key] = relevant
    return findings_by_pod


def _cross_agent_reconciliation(project_data: Dict[str, Any], missing: List[MissingInputRegisterItem]) -> List[Dict[str, Any]]:
    """Deterministic cross-agent consistency checks for the AI Deal Room."""
    conflicts = list(_conflicts(project_data))
    feasibility = project_data.get("feasibility_input") or {}
    marketability = project_data.get("marketabilityAnalysis") or project_data.get("marketability") or {}
    market_pricing = marketability.get("pricing") if isinstance(marketability, dict) else {}
    land_ledger = project_data.get("landLedger") or project_data.get("land_ledger") or {}
    land = project_data.get("land") or {}

    def add(code: str, category: str, severity: str, message: str, source_agents: List[str], target_agents: List[str], values: Dict[str, Any] | None = None) -> None:
        if any(row.get("code") == code for row in conflicts):
            return
        conflicts.append(
            {
                "code": code,
                "category": category,
                "severity": severity,
                "message": message,
                "sourceAgents": source_agents,
                "targetAgents": target_agents,
                "values": values or {},
                "status": "needs_review",
            }
        )

    total_revenue = _number(feasibility.get("total_revenue") or feasibility.get("sales_revenue"))
    comparable_median = _number((market_pricing or {}).get("comparableMedianPricePerPy"))
    proposed_price = _number((market_pricing or {}).get("proposedPricePerPy"))
    if total_revenue and not (comparable_median or proposed_price):
        add(
            "MARKET_PRICE_BASIS_MISSING",
            "market",
            "high",
            "사업수지상 매출은 있으나 분양가 또는 비교사례 가격 기준이 부족합니다.",
            ["Feasibility-Cashflow Pod"],
            ["Market-Product Pod", "PF-Credit Pod"],
            {"totalRevenue": total_revenue},
        )

    market_evidence_review = project_data.get("marketEvidenceReview") or project_data.get("market_evidence_review") or {}
    if isinstance(market_evidence_review, dict):
        review_status = str(market_evidence_review.get("status") or "").lower()
        issues = market_evidence_review.get("issues") if isinstance(market_evidence_review.get("issues"), list) else []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            code = str(issue.get("code") or "DOCUMENT_MARKET_EVIDENCE_NEEDS_REVIEW")
            severity = "high" if str(issue.get("severity") or "").lower() in {"warning", "error", "high", "blocking"} else "medium"
            add(
                code,
                "market",
                severity,
                str(issue.get("message") or "Uploaded business plan / IM market evidence requires reconciliation before marketability scoring."),
                ["ParserAgent", "Market Evidence Agent", "Market-Product Pod"],
                ["Marketability Analysis Agent", "Feasibility-Cashflow Pod", "PF-Credit Pod"],
                {
                    "marketEvidenceStatus": market_evidence_review.get("status"),
                    "issue": issue,
                    "evidenceSummary": market_evidence_review.get("evidenceSummary") or {},
                },
            )
        if review_status in {"needs_review", "needs_evidence", "blocking", "blocked"} and not issues:
            add(
                "DOCUMENT_MARKET_EVIDENCE_RECONCILIATION_OPEN",
                "market",
                "high",
                "Uploaded business plan / IM market evidence exists, but reconciliation is still open.",
                ["ParserAgent", "Market Evidence Agent"],
                ["Marketability Analysis Agent", "Feasibility-Cashflow Pod"],
                {"marketEvidenceStatus": market_evidence_review.get("status")},
            )

    for issue in _parsed_fact_reconciliation_issues(project_data):
        add(
            str(issue.get("code") or "PARSED_FACT_RECONCILIATION_ISSUE"),
            str(issue.get("category") or "data_quality"),
            str(issue.get("severity") or "medium"),
            str(issue.get("message") or "Parsed fact requires review before downstream pod execution."),
            ["ParserAgent", "DataQualityAgent"],
            list(issue.get("targetAgents") or ["Chief Deal Orchestrator"]),
            {
                "parsedFacts": issue.get("parsedFacts") or [],
                "canonicalField": issue.get("canonicalField"),
                "sourceLocators": issue.get("sourceLocators") or [],
            },
        )

    parcel_area = _number((land_ledger.get("summary") or {}).get("totalAreaSqm")) if isinstance(land_ledger, dict) else None
    project_area = _number(land.get("areaSqm") or land.get("landAreaSqm") or land.get("area_sqm"))
    if parcel_area and project_area and project_area > 0:
        variance = abs(parcel_area - project_area) / project_area
        if variance > 0.01:
            add(
                "LAND_AREA_RECONCILIATION_GAP",
                "land",
                "high",
                "토지조서 면적과 프로젝트 대지면적이 1% 이상 차이납니다.",
                ["Land-Legal Pod"],
                ["Feasibility-Cashflow Pod", "PF-Credit Pod"],
                {"landLedgerAreaSqm": parcel_area, "projectLandAreaSqm": project_area, "variance": variance},
            )

    missing_ids = {item.item_id for item in missing}
    if "land_evidence_missing" in missing_ids:
        add(
            "LAND_EVIDENCE_GATE_OPEN",
            "legal",
            "blocking",
            "공시지가, 지적특성, 등기/제한물권 증빙이 완성되지 않아 PF 승인 게이트가 열려 있습니다.",
            ["Land-Legal Pod"],
            ["PF-Credit Pod", "AI Risk Committee"],
        )
    if "pf_structure_missing" in missing_ids and total_revenue:
        add(
            "PF_STRUCTURE_NOT_LINKED",
            "pf",
            "high",
            "사업수지 매출은 있으나 PF Tranche 구조가 연결되지 않았습니다.",
            ["Feasibility-Cashflow Pod"],
            ["PF-Credit Pod"],
        )
    return conflicts


def _scenario_conclusions(project_data: Dict[str, Any], missing: List[MissingInputRegisterItem], conflicts: List[Dict[str, Any]]) -> List[ScenarioConclusion]:
    base = _base_case_metrics(project_data)
    flags = _scenario_flags(base, missing, conflicts)
    return [
        _build_case("base_case", base, flags, "현재 확보된 자료 기준의 기본 Case입니다."),
        _build_case(
            "downside_case",
            _stressed_case_metrics(base, revenue_factor=0.90, cost_factor=1.10, sale_rate_factor=0.85),
            [*flags, "DOWNSIDE_REVENUE_10_DOWN", "DOWNSIDE_COST_10_UP"],
            "분양가·매각가 10% 하락, 비용 10% 상승, 분양/회수 지연을 반영한 보수 Case입니다.",
        ),
        _build_case(
            "upside_case",
            _stressed_case_metrics(base, revenue_factor=1.05, cost_factor=0.97, sale_rate_factor=1.05),
            [flag for flag in flags if flag in {"DATA_INCOMPLETE", "LAND_EVIDENCE_GATE_OPEN"}],
            "시장가격 지지와 비용 절감이 확인될 경우의 개선 Case입니다.",
        ),
        _build_case(
            "kill_case",
            _stressed_case_metrics(base, revenue_factor=0.80, cost_factor=1.15, sale_rate_factor=0.65),
            [*flags, "KILL_REVENUE_20_DOWN", "KILL_COST_15_UP", "KILL_SALE_RATE_STRESS"],
            "토지·법률·시장·PF 회수 조건이 동시에 악화되는 중단 검토 Case입니다.",
        ),
    ]


def _base_case_metrics(project_data: Dict[str, Any]) -> Dict[str, Any]:
    feasibility = project_data.get("feasibility_input") or {}
    expected = project_data.get("expected_results") or {}
    total_revenue = _number(feasibility.get("total_revenue") or feasibility.get("sales_revenue"))
    total_cost = _number(feasibility.get("total_cost"))
    pf_amount = _total_pf_amount(project_data)
    development_profit = None
    profit_margin = _number(expected.get("profit_margin"))
    if total_revenue is not None and total_cost is not None:
        development_profit = total_revenue - total_cost
        if total_revenue and profit_margin is None:
            profit_margin = development_profit / total_revenue
    exit_sale_rate = pf_amount / total_revenue if pf_amount is not None and total_revenue else None
    return {
        "totalRevenue": total_revenue,
        "totalCost": total_cost,
        "developmentProfit": development_profit,
        "profitMargin": profit_margin,
        "pfAmount": pf_amount,
        "exitSaleRate": exit_sale_rate,
        "saleRateAssumption": 1.0,
    }


def _stressed_case_metrics(base: Dict[str, Any], *, revenue_factor: float, cost_factor: float, sale_rate_factor: float) -> Dict[str, Any]:
    revenue = _mul(base.get("totalRevenue"), revenue_factor)
    cost = _mul(base.get("totalCost"), cost_factor)
    pf_amount = base.get("pfAmount")
    profit = revenue - cost if revenue is not None and cost is not None else None
    margin = profit / revenue if revenue else None
    exit_sale_rate = pf_amount / revenue if pf_amount is not None and revenue else None
    senior_shortfall = max(0.0, (pf_amount or 0.0) - (revenue or 0.0)) if pf_amount is not None and revenue is not None else None
    return {
        "totalRevenue": revenue,
        "totalCost": cost,
        "developmentProfit": profit,
        "profitMargin": margin,
        "pfAmount": pf_amount,
        "exitSaleRate": exit_sale_rate,
        "saleRateAssumption": sale_rate_factor,
        "seniorShortfall": senior_shortfall,
    }


def _build_case(scenario: str, metrics: Dict[str, Any], risk_flags: List[str], summary: str) -> ScenarioConclusion:
    clean_flags = list(dict.fromkeys([flag for flag in risk_flags if flag]))
    exit_sale_rate = metrics.get("exitSaleRate")
    senior_shortfall = metrics.get("seniorShortfall") or 0
    if exit_sale_rate is not None and exit_sale_rate > 0.7:
        clean_flags.append("EXIT_SALE_RATE_HIGH")
    if senior_shortfall > 0:
        clean_flags.append("SENIOR_SHORTFALL")
    implication = "추가 검토 가능"
    if "SENIOR_SHORTFALL" in clean_flags or scenario == "kill_case":
        implication = "보완 또는 중단 검토 필요"
    elif any(flag.endswith("MISSING") or flag.endswith("GATE_OPEN") for flag in clean_flags):
        implication = "자료 보강 후 판단"
    elif "EXIT_SALE_RATE_HIGH" in clean_flags:
        implication = "PF 구조 또는 상환조건 보완 필요"
    return ScenarioConclusion(
        scenario=scenario,  # type: ignore[arg-type]
        summary=summary,
        keyMetrics=metrics,
        riskFlags=list(dict.fromkeys(clean_flags)),
        decisionImplication=implication,
    )


def _scenario_flags(base: Dict[str, Any], missing: List[MissingInputRegisterItem], conflicts: List[Dict[str, Any]]) -> List[str]:
    flags: List[str] = []
    if base.get("totalRevenue") is None or base.get("totalCost") is None:
        flags.append("DATA_INCOMPLETE")
    if base.get("pfAmount") is None:
        flags.append("PF_STRUCTURE_MISSING")
    for item in missing:
        if item.severity == "blocking":
            flags.append(item.item_id.upper())
    for conflict in conflicts:
        if conflict.get("severity") in {"blocking", "high"}:
            flags.append(str(conflict.get("code") or "RECONCILIATION_ISSUE"))
    return flags


# Which Chief pods each committee reviewer is accountable for — used to route
# that pod's Pod AI Review findings into the reviewer's concerns/stance.
_REVIEWER_POD_MAP: Dict[str, tuple] = {
    "devils_advocate": ("land_legal", "market_product", "cost_schedule", "feasibility_cashflow", "pf_credit", "public_policy"),
    "credit_reviewer": ("pf_credit", "feasibility_cashflow"),
    "legal_compliance_reviewer": ("land_legal", "public_policy"),
    "market_stress_tester": ("market_product",),
    "construction_risk_reviewer": ("cost_schedule", "feasibility_cashflow"),
    "lender_persona_reviewer": ("pf_credit", "feasibility_cashflow"),
    "ic_judge": ("land_legal", "market_product", "cost_schedule", "feasibility_cashflow", "pf_credit", "public_policy"),
}

# Committee stance ranking so an escalation never downgrades a stronger stance.
_IMPACT_RANK = {"supports_pass": 0, "supports_conditional_pass": 1, "supports_hold": 2, "supports_fail": 3}


def _risk_committee_opinions(
    project_data: Dict[str, Any],
    missing: List[MissingInputRegisterItem],
    conflicts: List[Dict[str, Any]],
    scenarios: List[ScenarioConclusion],
    pod_findings: Dict[str, List[Dict[str, Any]]] | None = None,
) -> List[RiskCommitteeOpinion]:
    blocking = [item.description for item in missing if item.severity == "blocking"]
    high_conflicts = [row for row in conflicts if row.get("severity") in {"blocking", "high"}]
    scenario_flags = {flag for scenario in scenarios for flag in scenario.risk_flags}
    base = next((scenario for scenario in scenarios if scenario.scenario == "base_case"), None)
    base_metrics = base.key_metrics if base else {}
    exit_sale_rate = _number(base_metrics.get("exitSaleRate"))
    profit_margin = _number(base_metrics.get("profitMargin"))
    pod_findings = pod_findings or {}

    def _reviewer_pod_findings(role: str) -> List[tuple]:
        """(podKey, finding) for the pods this reviewer is accountable for."""
        return [
            (pod_key, finding)
            for pod_key in _REVIEWER_POD_MAP.get(role, ())
            for finding in pod_findings.get(pod_key, [])
        ]

    def opinion(role: str, text: str, impact: str, concerns: List[str], actions: List[str], confidence: float = 0.78) -> RiskCommitteeOpinion:
        # Fold this reviewer's Pod AI Review findings into concerns + stance.
        relevant = _reviewer_pod_findings(role)
        if relevant:
            concerns = list(concerns)
            actions = list(actions)
            has_blocking = any(str(f.get("severity")).lower() == "blocking" for _, f in relevant)
            for pod_key, finding in relevant[:6]:
                msg = str(finding.get("message") or finding.get("category") or "").strip()
                if msg:
                    concerns.append(f"[{pod_key}] {msg[:100]}")
                act = str(finding.get("recommendedAction") or "").strip()
                if act:
                    actions.append(f"[{pod_key}] {act[:100]}")
            # A blocking deterministic finding escalates the stance to at least HOLD
            # (never downgrades an existing FAIL). High-only findings add concerns only.
            if has_blocking:
                target = "supports_hold"
                if _IMPACT_RANK.get(target, 0) > _IMPACT_RANK.get(impact, 0):
                    impact = target
        return RiskCommitteeOpinion(
            reviewerRole=role,  # type: ignore[arg-type]
            opinion=text,
            decisionImpact=impact,  # type: ignore[arg-type]
            keyConcerns=concerns[:8],
            requiredActions=actions[:8],
            confidence=confidence,
        )

    return [
        opinion(
            "devils_advocate",
            "현재 자료와 수치 충돌 기준으로 가장 취약한 가정은 토지·시장·PF 회수 근거의 미완성 여부입니다.",
            "supports_hold" if blocking or high_conflicts else "supports_conditional_pass",
            [str(row.get("code")) for row in high_conflicts] + list(scenario_flags),
            ["자료 출처와 기준값을 확정하고, 충돌 항목은 Assumption Override 또는 재계산으로 해소"],
        ),
        opinion(
            "credit_reviewer",
            "대주단 관점에서는 Exit 분양률, 선순위 회수 가능성, 상환계좌 배분조건이 핵심입니다.",
            "supports_fail" if "SENIOR_SHORTFALL" in scenario_flags else ("supports_hold" if blocking else "supports_conditional_pass"),
            ["SENIOR_SHORTFALL"] if "SENIOR_SHORTFALL" in scenario_flags else (["EXIT_SALE_RATE_HIGH"] if exit_sale_rate and exit_sale_rate > 0.7 else []),
            ["Senior 감액, Equity/DSRA 증액, 분양수입금 Cash Sweep 강화, 추가 담보 또는 선매입 근거 확보"],
        ),
        opinion(
            "legal_compliance_reviewer",
            "법무·인허가 관점에서는 PNU, 공시지가, 등기/제한물권, 인출 선행조건 증빙이 확정되어야 합니다.",
            "supports_hold" if any(item.category in {"land", "legal"} and item.severity == "blocking" for item in missing) else "supports_conditional_pass",
            [item.item_id for item in missing if item.category in {"land", "legal"}],
            ["필지별 공시지가·지적특성·등기부 증빙 첨부", "제한물권 말소 및 신탁등기 조건을 CP로 반영"],
        ),
        opinion(
            "market_stress_tester",
            "시장성 관점에서는 비교사례와 제안 분양가 기준이 부족하면 사업수지 매출을 확정값으로 사용할 수 없습니다.",
            "supports_hold" if "MARKET_PRICE_BASIS_MISSING" in scenario_flags else "supports_conditional_pass",
            ["MARKET_PRICE_BASIS_MISSING"] if "MARKET_PRICE_BASIS_MISSING" in scenario_flags else [],
            ["실거래가, 분양사례, 임대료, 미분양 통계 또는 수동 비교사례를 Evidence Vault에 저장"],
        ),
        opinion(
            "construction_risk_reviewer",
            "공사비와 일정이 흔들리면 금융비용과 Max Minus가 재계산되어 PF 조건도 바뀝니다.",
            "supports_hold" if any(item.category in {"cost", "schedule"} and item.severity in {"blocking", "high"} for item in missing) else "supports_conditional_pass",
            [item.item_id for item in missing if item.category in {"cost", "schedule"}],
            ["공사비 내역, 공정표, 책임준공 조건, 공사비 유보율을 연결"],
        ),
        opinion(
            "lender_persona_reviewer",
            "은행·증권·캐피탈 관점 모두 원금보존과 증빙 추적성이 약하면 조건부 승인도 제한됩니다.",
            "supports_hold" if blocking else "supports_conditional_pass",
            [item.item_id for item in missing if item.severity in {"blocking", "high"}],
            ["금융기관별 LTV/LTC/Exit 기준과 승인조건 템플릿을 적용"],
        ),
        opinion(
            "ic_judge",
            "투자심의 관점에서는 Base/Downside/Upside/Kill Case가 모두 수치와 증빙으로 연결되어야 합니다.",
            "supports_fail" if "SENIOR_SHORTFALL" in scenario_flags else ("supports_hold" if blocking or high_conflicts else "supports_conditional_pass"),
            [str(row.get("code")) for row in high_conflicts] + [item.item_id for item in missing if item.severity == "blocking"],
            ["Human Review에서 기준값 승인, waiver, 보완자료 요청, 재실행 여부를 결정"],
            confidence=0.82 if profit_margin is not None else 0.72,
        ),
    ]


def _decision_gate(
    missing: List[MissingInputRegisterItem],
    conflicts: List[Dict[str, Any]],
    risk_committee: List[RiskCommitteeOpinion],
    scenarios: List[ScenarioConclusion],
) -> DecisionGateResult:
    blocking = [item.description for item in missing if item.severity == "blocking"]
    high = [item.description for item in missing if item.severity == "high"]
    blocking_conflicts = [str(row.get("message") or row.get("code")) for row in conflicts if row.get("severity") == "blocking"]
    high_conflicts = [str(row.get("message") or row.get("code")) for row in conflicts if row.get("severity") == "high"]
    impacts = {opinion.decision_impact for opinion in risk_committee}
    flags = {flag for scenario in scenarios for flag in scenario.risk_flags}

    if "supports_fail" in impacts or "SENIOR_SHORTFALL" in flags:
        return DecisionGateResult(
            decision="FAIL",
            rationale="AI Risk Committee가 선순위 회수 부족 또는 중대한 구조 리스크를 확인했습니다.",
            requiredSupplements=[*blocking, *blocking_conflicts, *high_conflicts][:8],
            conditionsPrecedent=[*blocking, *blocking_conflicts][:6],
            mitigationActions=["Senior 감액", "Equity/DSRA 증액", "추가 담보", "선매입/Bulk Sale/Refinancing 근거 확보", "상환계좌 배분 강화"],
            humanReviewRequired=True,
        )
    if blocking or blocking_conflicts or "supports_hold" in impacts:
        return DecisionGateResult(
            decision="HOLD",
            rationale="필수 증빙 또는 수치 정합성 문제가 남아 있어 최종 승인 판단을 보류합니다.",
            requiredSupplements=[*blocking, *blocking_conflicts, *high_conflicts][:8],
            conditionsPrecedent=[*blocking, *blocking_conflicts][:6],
            mitigationActions=["부족자료 업로드", "기준값 승인/override", "관련 Pod 재실행"],
            humanReviewRequired=True,
        )
    if high or high_conflicts:
        return DecisionGateResult(
            decision="CONDITIONAL_PASS",
            rationale="핵심 분석은 진행 가능하나 고위험 자료 보강과 승인조건 반영이 필요합니다.",
            requiredSupplements=[*high, *high_conflicts][:8],
            conditionsPrecedent=[*high, *high_conflicts][:4],
            conditionsSubsequent=["보강자료 수령 후 Risk Register 재검토", "보고서 Evidence Graph 업데이트"],
            humanReviewRequired=True,
        )
    return DecisionGateResult(
        decision="PASS",
        rationale="현재 Deal Room 기준으로 필수 자료와 Agent 간 정합성이 확보되어 다음 Pod 분석을 진행할 수 있습니다.",
        humanReviewRequired=True,
    )


def _human_learning_loop_events(
    project_id: str,
    missing: List[MissingInputRegisterItem],
    conflicts: List[Dict[str, Any]],
    risk_committee: List[RiskCommitteeOpinion],
) -> List[Dict[str, Any]]:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    events: List[Dict[str, Any]] = []
    for item in missing:
        if item.severity in {"blocking", "high"}:
            events.append(
                {
                    "eventId": f"learning:{project_id}:missing:{item.item_id}",
                    "eventType": "missing_input_review_required",
                    "createdAt": now,
                    "source": "ChiefDealOrchestrator",
                    "target": "HumanExpertReview",
                    "status": "open",
                    "itemId": item.item_id,
                    "category": item.category,
                    "severity": item.severity,
                    "recommendedAction": "자료 업로드, waiver, 또는 기준값 승인 후 관련 Pod를 재실행합니다.",
                }
            )
    for conflict in conflicts:
        if conflict.get("severity") in {"blocking", "high"}:
            events.append(
                {
                    "eventId": f"learning:{project_id}:conflict:{conflict.get('code')}",
                    "eventType": "assumption_conflict_review_required",
                    "createdAt": now,
                    "source": "CrossAgentReconciliation",
                    "target": "HumanExpertReview",
                    "status": "open",
                    "code": conflict.get("code"),
                    "category": conflict.get("category"),
                    "severity": conflict.get("severity"),
                    "recommendedAction": "기준값을 승인하거나 원천자료를 보강하고 Assumption Book에 반영합니다.",
                }
            )
    if any(opinion.decision_impact in {"supports_fail", "supports_hold"} for opinion in risk_committee):
        events.append(
            {
                "eventId": f"learning:{project_id}:committee:review",
                "eventType": "risk_committee_human_review_required",
                "createdAt": now,
                "source": "AIRiskCommittee",
                "target": "HumanExpertReview",
                "status": "open",
                "recommendedAction": "심사역이 Risk Committee 의견을 승인, 반려, waiver 또는 보완요청으로 처리합니다.",
            }
        )
    return events


def _merge_learning_loop_events(existing: List[Dict[str, Any]], generated: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for event in [*(existing or []), *generated]:
        event_id = event.get("eventId") or event.get("id")
        if event_id:
            merged[str(event_id)] = event
    return list(merged.values())


def _total_pf_amount(project_data: Dict[str, Any]) -> float | None:
    raw_tranches = project_data.get("pf_structure") or project_data.get("pfStructure") or []
    if isinstance(raw_tranches, list) and raw_tranches:
        total = sum(
            _number(item.get("amount") or item.get("loanAmount") or item.get("principal")) or 0.0
            for item in raw_tranches
            if isinstance(item, dict)
        )
        return total if total > 0 else None
    feasibility = project_data.get("feasibility_input") or {}
    return _number(feasibility.get("pf_amount") or feasibility.get("pfLoan"))


def _mul(value: Any, factor: float) -> float | None:
    number = _number(value)
    return number * factor if number is not None else None


def plan_chief_deal_orchestration_use_case(
    project_id: str,
    project_data: Dict[str, Any],
    options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build the first Chief Deal Orchestrator plan.

    This use case does not run pods or invent missing conclusions. It converts
    current project state into an AI Deal Room contract: shared state, missing
    input register, pod plan, task graph, preliminary decision gate, and report
    outputs that can be produced after human-gated review.
    """
    options = options or {}
    analysis_purpose = str(options.get("analysisPurpose") or project_data.get("analysisPurpose") or "deal_orchestration")
    workflow_track = _workflow_track(project_data, options)
    missing = _missing_inputs(project_data)
    conflicts = _cross_agent_reconciliation(project_data, missing)
    state = SharedDealStateGraph(
        projectId=project_id,
        projectMemory=_project_memory(project_data),
        evidenceVault=_evidence_vault(project_data),
        assumptionBook=_assumption_book(project_data),
        metrics=_metrics(project_data),
        risks=_risks(project_data),
        conflicts=conflicts,
        missingInputRegister=missing,
    )
    pods = _agent_pods(missing, workflow_track)
    
    # Load stored chief orchestration state from project_data
    stored_state = project_data.get("chief_orchestration_state") or {}
    stored_pod_runs = stored_state.get("podRuns") or {}
    stored_learning_loop_events = stored_state.get("learningLoopEvents") or []

    pod_runs = []
    for pod in pods:
        stored_run = stored_pod_runs.get(pod.pod_key)
        if stored_run:
            status = stored_run.get("status") or "planned"
            if _pod_blocked(pod, missing) and status not in ("completed", "failed"):
                status = "blocked"
            pod_runs.append(
                AgentPodRun(
                    podKey=pod.pod_key,
                    status=status,
                    progressPercent=stored_run.get("progressPercent", 0),
                    artifactKeys=stored_run.get("artifactKeys") or [],
                    podAiReviewIds=stored_run.get("podAiReviewIds") or [],
                    handoffIds=stored_run.get("handoffIds") or [],
                    recalculationEvents=stored_run.get("recalculationEvents") or [],
                )
            )
        else:
            pod_runs.append(
                AgentPodRun(
                    podKey=pod.pod_key,
                    status="blocked" if _pod_blocked(pod, missing) else "planned",
                    progressPercent=0,
                )
            )

    scenario_conclusions = _scenario_conclusions(project_data, missing, conflicts)
    pod_findings = _load_pod_ai_review_findings(project_id)
    risk_committee = _risk_committee_opinions(project_data, missing, conflicts, scenario_conclusions, pod_findings)
    decision_gate = _decision_gate(missing, conflicts, risk_committee, scenario_conclusions)
    learning_loop_events = _merge_learning_loop_events(
        stored_learning_loop_events,
        _human_learning_loop_events(project_id, missing, conflicts, risk_committee),
    )
    plan = ChiefDealOrchestrationPlan(
        projectId=project_id,
        analysisPurpose=analysis_purpose,
        sharedDealStateGraph=state,
        taskGraph=_task_graph(pods, workflow_track),
        agentPods=pods,
        podRuns=pod_runs,
        scenarioConclusions=scenario_conclusions,
        riskCommittee=risk_committee,
        decisionGate=decision_gate,
        reportStudioOutputs=[
            "토지검토서",
            "사업성 검토서",
            "사업수지표",
            "월별 Cash Flow",
            "PF 심사보고서",
            "IC Memo",
            "Term Sheet",
            "Covenants Sheet",
            "Monitoring Sheet",
        ],
        learningLoopEvents=learning_loop_events,
    )
    return plan.model_dump(by_alias=True)


def run_chief_deal_pod_use_case(
    project_id: str,
    pod_key: str,
    project_data: Dict[str, Any],
    options: Dict[str, Any] | None = None,
    *,
    artifact_sink: Callable[[Dict[str, Any]], Dict[str, Any]] | None = None,
    review_sink: Callable[[Dict[str, Any]], Dict[str, Any]] | None = None,
    event_sink: Callable[[Dict[str, Any]], None] | None = None,
) -> Dict[str, Any]:
    """Execute a single agent pod, update its state, run the Event Bus triggers,
    and persist the updated orchestration state and derived metrics into the project.

    ``event_sink`` receives every recalculation event for durable audit
    persistence (the in-project state copy alone is not queryable history).
    """
    from datetime import datetime, timezone

    options = options or {}
    workflow_track = _workflow_track(project_data, options)
    missing = _missing_inputs(project_data)
    pods = _agent_pods(missing, workflow_track)

    # 1. Find the target pod plan
    target_pod = next((p for p in pods if p.pod_key == pod_key), None)
    if not target_pod:
        raise ValueError(f"Pod {pod_key} not found")

    # 2. Check if the pod is blocked — but first let the orchestrator try to
    # self-serve the missing inputs from parsed data + public APIs (VWorld PNU/
    # 공시지가, MOLIT 실거래가) instead of immediately asking for more uploads.
    supplement_log: List[Dict[str, Any]] = []
    if _pod_blocked(target_pod, missing):
        from use_cases.chief_pod_auto_supplement import auto_supplement_pod_inputs

        supplement_log = auto_supplement_pod_inputs(project_id, project_data)
        missing = _missing_inputs(project_data)
        pods = _agent_pods(missing, workflow_track)
        target_pod = next((p for p in pods if p.pod_key == pod_key), target_pod)
        if _pod_blocked(target_pod, missing):
            still_needed = [
                item.description
                for item in missing
                if pod_key in item.required_for or "all_pods" in item.required_for
            ]
            tried = "; ".join(
                f"{a.get('itemId')}: {a.get('status')}" for a in supplement_log
            ) or "자동 보강 가능 항목 없음"
            raise ValueError(
                f"Pod {pod_key} is blocked due to missing inputs. "
                f"자동 보강 시도: {tried}. 남은 필요 자료: {'; '.join(still_needed[:4])}"
            )

    # 3. Load or initialize the orchestration state
    state = project_data.get("chief_orchestration_state") or {}
    pod_runs = state.setdefault("podRuns", {})
    learning_loop_events = state.setdefault("learningLoopEvents", [])
    artifacts = state.setdefault("artifacts", {})

    # 4. Execute the real domain use case for the pod and persist its artifact.
    now = datetime.now(timezone.utc).isoformat()
    artifact_key = f"artifact:{pod_key}:{now}"
    artifact = _execute_pod_artifact(project_id, pod_key, project_data, options)
    term_surface_metadata = _pod_term_surface_metadata(pod_key, artifact)
    artifact_record = {
        "artifactKey": artifact_key,
        "podKey": pod_key,
        "createdAt": now,
        "schemaVersion": "realytics-chief-pod-artifact-v1",
        "termSurfaceMetadata": term_surface_metadata,
        "result": artifact,
    }
    persisted_artifact = artifact_sink(artifact_record) if artifact_sink else artifact_record
    pod_ai_review_ref: Dict[str, Any] | None = None
    if pod_key in {"land_legal", "market_product", "pf_credit", "cost_schedule", "feasibility_cashflow", "public_policy"}:
        pod_ai_review = PodAIReviewerAgent().review_pod(
            project_id=project_id,
            pod_key=pod_key,
            source_artifact=persisted_artifact,
            source_artifact_key=artifact_key,
            project_data=project_data,
            options=options,
        )
        persisted_review = review_sink(pod_ai_review) if review_sink else pod_ai_review
        pod_ai_review_ref = {
            "reviewId": persisted_review.get("reviewId"),
            "reviewStatus": persisted_review.get("reviewStatus"),
            "reviewMode": persisted_review.get("reviewMode"),
            "agentId": persisted_review.get("agentId"),
            "confidence": persisted_review.get("confidence"),
        }
        persisted_artifact["podAiReviewId"] = persisted_review.get("reviewId")
        persisted_artifact["podAiReview"] = pod_ai_review_ref
        if artifact_sink is not None:
            persisted_artifact = artifact_sink(persisted_artifact)
    artifacts[artifact_key] = {
        "artifactKey": artifact_key,
        "podKey": pod_key,
        "artifactType": artifact.get("artifactType"),
        "createdAt": persisted_artifact.get("createdAt") or now,
        "termSurfaceCount": len(term_surface_metadata),
        "schemaVersion": "realytics-chief-pod-artifact-ref-v1",
    }
    if pod_ai_review_ref:
        artifacts[artifact_key]["podAiReview"] = pod_ai_review_ref
        artifacts[artifact_key]["podAiReviewId"] = pod_ai_review_ref.get("reviewId")
    pod_runs[pod_key] = {
        "podKey": pod_key,
        "status": "completed",
        "progressPercent": 100,
        "artifactKeys": [artifact_key],
        "podAiReviewIds": [pod_ai_review_ref["reviewId"]] if pod_ai_review_ref and pod_ai_review_ref.get("reviewId") else [],
        "handoffIds": [f"handoff:{pod_key}:{now}"],
        "recalculationEvents": pod_runs.get(pod_key, {}).get("recalculationEvents") or [],
    }
            
    # 5. Event Bus: Evaluate Recalculation Rules
    triggers = []
    if pod_key == "cost_schedule":
        triggers.append("finance_cost_changed")
    elif pod_key == "market_product" and options.get("forceTrigger") == "market_price_unsupported":
        triggers.append("market_price_unsupported")
    elif pod_key == "land_legal" and options.get("forceTrigger") == "legal_product_infeasible":
        triggers.append("legal_product_infeasible")
    elif pod_key == "feasibility_cashflow" and options.get("forceTrigger") == "max_minus_exceeds_pf_limit":
        triggers.append("max_minus_exceeds_pf_limit")

    if options.get("trigger"):
        triggers.append(options["trigger"])

    recalc_rules = _task_graph(pods, workflow_track)["recalculationRules"]
    for trigger in triggers:
        rule = next((r for r in recalc_rules if r["trigger"] == trigger), None)
        if rule:
            target_pods = rule["rerun"]
            event = {
                "eventId": f"event:{trigger}:{now}",
                "eventType": "recalculation_triggered",
                "trigger": trigger,
                "sourcePod": pod_key,
                "timestamp": now,
                "targetPods": target_pods,
            }
            learning_loop_events.append(event)
            if event_sink is not None:
                try:
                    event_sink(event)
                except Exception:
                    pass  # durable audit is best-effort; never break the pod run
            for target_key in target_pods:
                target_run = pod_runs.setdefault(target_key, {
                    "podKey": target_key,
                    "status": "planned",
                    "progressPercent": 0,
                    "artifactKeys": [],
                    "handoffIds": [],
                    "recalculationEvents": [],
                })
                target_run["status"] = "needs_recalculation"
                # Avoid duplicates
                if not any(e["eventId"] == event["eventId"] for e in target_run.setdefault("recalculationEvents", [])):
                    target_run["recalculationEvents"].append(event)

    project_data["chief_orchestration_state"] = state
    plan = plan_chief_deal_orchestration_use_case(project_id, project_data, options)
    if supplement_log:
        plan["autoSupplements"] = supplement_log
    return plan


def run_chief_deal_all_pods_use_case(
    project_id: str,
    project_data: Dict[str, Any],
    options: Dict[str, Any] | None = None,
    *,
    artifact_sink: Callable[[Dict[str, Any]], Dict[str, Any]] | None = None,
    review_sink: Callable[[Dict[str, Any]], Dict[str, Any]] | None = None,
    event_sink: Callable[[Dict[str, Any]], None] | None = None,
    max_recalculation_rounds: int = 2,
) -> Dict[str, Any]:
    """Execute the current track's pod graph in recommended order.

    This is still deterministic orchestration, not an approval decision. Each
    pod gets the same blocked-pod auto-supplement behavior as single-pod
    execution. If a pod remains blocked after legitimate public/API sources are
    attempted, the run continues and the final plan explicitly reports the
    blocked pod and reason.

    After the main pass, pods that the Event Bus marked ``needs_recalculation``
    are re-executed automatically (bounded by ``max_recalculation_rounds``) so a
    trigger like finance_cost_changed actually propagates instead of leaving
    stale downstream numbers.
    """
    from datetime import datetime, timezone

    options = options or {}
    initial = plan_chief_deal_orchestration_use_case(project_id, project_data, options)
    order = list((initial.get("taskGraph") or {}).get("recommendedRunOrder") or [])
    if not order:
        order = [pod.get("podKey") for pod in initial.get("agentPods") or [] if pod.get("podKey")]

    summary_rows: List[Dict[str, Any]] = []
    auto_supplements: List[Dict[str, Any]] = []
    last_plan = initial
    for pod_key in order:
        if not pod_key:
            continue
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            last_plan = run_chief_deal_pod_use_case(
                project_id,
                str(pod_key),
                project_data,
                options,
                artifact_sink=artifact_sink,
                review_sink=review_sink,
                event_sink=event_sink,
            )
            auto_supplements.extend(last_plan.get("autoSupplements") or [])
            run = next((item for item in last_plan.get("podRuns", []) if item.get("podKey") == pod_key), {})
            summary_rows.append(
                {
                    "podKey": pod_key,
                    "status": run.get("status") or "completed",
                    "startedAt": started_at,
                    "completedAt": datetime.now(timezone.utc).isoformat(),
                    "artifactKeys": run.get("artifactKeys") or [],
                }
            )
        except ValueError as exc:
            state = project_data.setdefault("chief_orchestration_state", {})
            pod_runs = state.setdefault("podRuns", {})
            pod_runs[str(pod_key)] = {
                "podKey": pod_key,
                "status": "blocked",
                "progressPercent": 0,
                "artifactKeys": [],
                "handoffIds": [],
                "recalculationEvents": [],
                "error": str(exc),
                "blockedAt": datetime.now(timezone.utc).isoformat(),
            }
            summary_rows.append(
                {
                    "podKey": pod_key,
                    "status": "blocked",
                    "startedAt": started_at,
                    "completedAt": datetime.now(timezone.utc).isoformat(),
                    "error": str(exc),
                }
            )
            # Re-plan so the UI sees the blocked state and updated missing-input
            # register, then keep moving to expose all remaining blockers.
            last_plan = plan_chief_deal_orchestration_use_case(project_id, project_data, options)

    # --- Recalculation cascade -------------------------------------------------
    # Event Bus triggers mark downstream pods needs_recalculation; propagate by
    # re-running them (bounded rounds; a pod re-runs at most once per round).
    recalculation_rows: List[Dict[str, Any]] = []
    for round_index in range(1, max(0, max_recalculation_rounds) + 1):
        state = project_data.get("chief_orchestration_state") or {}
        pod_runs = state.get("podRuns") or {}
        pending = [
            key
            for key in order
            if key and (pod_runs.get(str(key)) or {}).get("status") == "needs_recalculation"
        ]
        if not pending:
            break
        for pod_key in pending:
            triggering = [
                str(event.get("trigger"))
                for event in (pod_runs.get(str(pod_key)) or {}).get("recalculationEvents") or []
            ]
            started_at = datetime.now(timezone.utc).isoformat()
            try:
                last_plan = run_chief_deal_pod_use_case(
                    project_id,
                    str(pod_key),
                    project_data,
                    options,
                    artifact_sink=artifact_sink,
                    review_sink=review_sink,
                    event_sink=event_sink,
                )
                recalculation_rows.append(
                    {
                        "podKey": pod_key,
                        "round": round_index,
                        "status": "recalculated",
                        "triggers": triggering[:5],
                        "startedAt": started_at,
                        "completedAt": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except ValueError as exc:
                recalculation_rows.append(
                    {
                        "podKey": pod_key,
                        "round": round_index,
                        "status": "blocked",
                        "triggers": triggering[:5],
                        "error": str(exc),
                    }
                )

    final_plan = plan_chief_deal_orchestration_use_case(project_id, project_data, options)
    if auto_supplements:
        # Keep order and de-duplicate repeated attempts caused by multiple pods
        # sharing the same missing input.
        seen = set()
        unique_supplements = []
        for item in auto_supplements:
            key = (item.get("itemId"), item.get("status"), str(item.get("values") or item.get("reason") or ""))
            if key in seen:
                continue
            seen.add(key)
            unique_supplements.append(item)
        final_plan["autoSupplements"] = unique_supplements
    completed_count = len([row for row in summary_rows if row.get("status") == "completed"])
    blocked_count = len([row for row in summary_rows if row.get("status") == "blocked"])
    final_plan["runAllSummary"] = {
        "schemaVersion": "realytics-chief-pod-run-all-summary-v1",
        "projectId": project_id,
        "workflowTrack": (final_plan.get("taskGraph") or {}).get("workflowTrack"),
        "requestedPodCount": len(order),
        "completedCount": completed_count,
        "blockedCount": blocked_count,
        "recalculatedCount": len([r for r in recalculation_rows if r.get("status") == "recalculated"]),
        "recalculationRows": recalculation_rows,
        "status": "completed_with_blockers" if blocked_count else "completed",
        "rows": summary_rows,
    }
    return final_plan


def run_chief_deal_pod_with_cascade_use_case(
    project_id: str,
    pod_key: str,
    project_data: Dict[str, Any],
    options: Dict[str, Any] | None = None,
    *,
    artifact_sink: Callable[[Dict[str, Any]], Dict[str, Any]] | None = None,
    review_sink: Callable[[Dict[str, Any]], Dict[str, Any]] | None = None,
    event_sink: Callable[[Dict[str, Any]], None] | None = None,
    max_rounds: int = 2,
) -> Dict[str, Any]:
    """Run one Pod and automatically consume its recalculation events.

    The normal single-Pod API remains backward-compatible. This explicit
    cascade path is used by the UI when a reviewer asks for an autonomous
    downstream run, so a cost or market update does not leave dependent Pods
    permanently in ``needs_recalculation``.
    """
    options = dict(options or {})
    options.pop("autoCascade", None)
    plan = run_chief_deal_pod_use_case(
        project_id,
        pod_key,
        project_data,
        options,
        artifact_sink=artifact_sink,
        review_sink=review_sink,
        event_sink=event_sink,
    )
    rows: List[Dict[str, Any]] = []
    for round_index in range(1, max(0, max_rounds) + 1):
        state = project_data.get("chief_orchestration_state") or {}
        pod_runs = state.get("podRuns") or {}
        order = list((plan.get("taskGraph") or {}).get("recommendedRunOrder") or [])
        pending = [
            key for key in order
            if key and (pod_runs.get(str(key)) or {}).get("status") == "needs_recalculation"
        ]
        if not pending:
            break
        for target_key in pending:
            started_at = datetime.now(timezone.utc).isoformat()
            triggers = [
                str(event.get("trigger"))
                for event in (pod_runs.get(str(target_key)) or {}).get("recalculationEvents") or []
            ]
            try:
                plan = run_chief_deal_pod_use_case(
                    project_id,
                    str(target_key),
                    project_data,
                    options,
                    artifact_sink=artifact_sink,
                    review_sink=review_sink,
                    event_sink=event_sink,
                )
                rows.append({
                    "podKey": target_key,
                    "round": round_index,
                    "status": "recalculated",
                    "triggers": triggers[:5],
                    "startedAt": started_at,
                    "completedAt": datetime.now(timezone.utc).isoformat(),
                })
            except ValueError as exc:
                rows.append({
                    "podKey": target_key,
                    "round": round_index,
                    "status": "blocked",
                    "triggers": triggers[:5],
                    "error": str(exc),
                })
                break
    plan = plan_chief_deal_orchestration_use_case(project_id, project_data, options)
    plan["autoCascadeSummary"] = {
        "schemaVersion": "realytics-chief-pod-auto-cascade-v1",
        "sourcePod": pod_key,
        "recalculatedCount": len([row for row in rows if row.get("status") == "recalculated"]),
        "blockedCount": len([row for row in rows if row.get("status") == "blocked"]),
        "rows": rows,
    }
    return plan


def _truthy(value: Any) -> bool:
    if value in (None, "", 0, 0.0):
        return False
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _execute_pod_artifact(
    project_id: str,
    pod_key: str,
    project_data: Dict[str, Any],
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """Run the best available domain agent/use case for a Chief Orchestrator pod.

    The early Deal Room implementation used synthetic field updates. This helper
    keeps the pod orchestration deterministic while ensuring each button stores a
    real artifact produced by existing domain logic wherever available.
    """
    if pod_key == "land_legal":
        workflow_run = _run_domain_workflow(project_id, "land_review", project_data, options)
        workflow_artifacts = workflow_run.get("artifacts") or {}
        artifact: Dict[str, Any] = {
            "artifactType": "land_legal_pod_result",
            "domainWorkflow": _workflow_summary(workflow_run),
            "standardArtifacts": workflow_artifacts,
            "legalPermitReview": run_legal_pod_use_case(project_id, project_data, options)["legalReview"],
            "developabilityScore": score_developability_use_case(project_data),
        }
        # Keep the legal review as a first-class, auditable result.  The pod is
        # the execution unit, but LegalReviewAgent remains visible to the Deal
        # Room and downstream Risk Register instead of being hidden in a blob.
        artifact["legalReviewExecution"] = {
            "agent": "LegalReviewAgent",
            "executed": True,
            "mode": "preliminary_pf_legal_permit_check",
            "source": "Land-Legal Pod",
        }
        if workflow_artifacts.get("landAgentReview"):
            artifact["landAgentReview"] = workflow_artifacts["landAgentReview"]
            project_data["landAgentReview"] = workflow_artifacts["landAgentReview"]
        land_ledger = project_data.get("landLedger") or project_data.get("land_ledger")
        if isinstance(land_ledger, dict):
            try:
                artifact["landLedgerEnrichment"] = enrich_land_ledger_use_case(
                    land_ledger,
                    {
                        "mode": "chief_orchestrator_pod",
                        **(options.get("landLedgerOptions") or {}),
                    },
                )
                project_data["landLedger"] = artifact["landLedgerEnrichment"]
                artifact.setdefault("standardArtifacts", {})["landLedgerEnrichment"] = artifact["landLedgerEnrichment"]
            except Exception as exc:  # pragma: no cover - defensive incomplete-upload handling
                artifact["landLedgerEnrichmentError"] = {
                    "status": "needs_review",
                    "reason": "land_ledger_structure_incomplete",
                    "message": str(exc),
                    "nextAction": "토지조서 원본을 재파싱하거나 필지별 owners/payment/area 필드를 보강하십시오.",
                }
        project_data["legal"] = artifact["legalPermitReview"]
        project_data["legalPermitReview"] = artifact["legalPermitReview"]
        project_data["developabilityScore"] = artifact["developabilityScore"]
        return artifact

    if pod_key == "market_product":
        workflow_run = _run_domain_workflow(project_id, "marketability", project_data, options)
        workflow_artifacts = workflow_run.get("artifacts") or {}
        market_evidence_review = workflow_artifacts.get("marketEvidenceReview")
        if not isinstance(market_evidence_review, dict):
            market_evidence_review = MarketEvidenceAgent().review(project_data)
            workflow_artifacts["marketEvidenceReview"] = market_evidence_review
        document_market_facts = _document_market_fact_summary(project_data, market_evidence_review)
        document_market_reconciliation = _document_market_reconciliation_summary(market_evidence_review)
        artifact = {
            "artifactType": "market_product_pod_result",
            "domainWorkflow": _workflow_summary(workflow_run),
            "standardArtifacts": workflow_artifacts,
            "documentParsedMarketFacts": document_market_facts,
            "documentMarketReconciliation": document_market_reconciliation,
            "marketEvidenceReview": market_evidence_review,
            "marketabilityAnalysis": workflow_artifacts.get("marketabilityAnalysis") or analyze_marketability_use_case(project_data, options),
        }
        if workflow_artifacts.get("liveMarketComparables"):
            artifact["liveMarketComparables"] = workflow_artifacts["liveMarketComparables"]
            project_data["liveMarketComparables"] = workflow_artifacts["liveMarketComparables"]
        project_data["documentParsedMarketFacts"] = document_market_facts
        project_data["documentMarketReconciliation"] = document_market_reconciliation
        project_data["marketEvidenceReview"] = market_evidence_review
        project_data["marketability"] = artifact["marketabilityAnalysis"]
        project_data["marketabilityAnalysis"] = artifact["marketabilityAnalysis"]
        try:  # deterministic product-planning program per asset type (reviewed by the pod AI reviewer)
            artifact["productPlanning"] = compare_product_planning_use_case(project_data)
            project_data["productPlanning"] = artifact["productPlanning"]
        except Exception:  # never let product planning break the market pod
            pass
        try:  # market depth: 제공주체 분류·비교, 상품별 분양성 score, 흡수율, AI 추가작업 판단
            artifact["marketProductDepth"] = MarketProductDepthAgent().analyze(
                project_data, artifact["marketabilityAnalysis"], options=options,
            )
            project_data["marketProductDepth"] = artifact["marketProductDepth"]
        except Exception:  # never let depth analysis break the market pod
            pass
        return artifact

    if pod_key == "cost_schedule":
        feasibility = analyze_development_feasibility_use_case(project_data, options)
        # DevelopmentFeasibilityAgent returns the cost split under "costs" and its
        # quality warnings nested under "dataQuality.warnings" — read those keys
        # (previously read costBreakdown/dataQualityWarnings, which the agent never
        # emits, so both were always empty in the pod artifact).
        data_quality = feasibility.get("dataQuality") or {}
        artifact = {
            "artifactType": "cost_schedule_pod_result",
            "costBreakdown": feasibility.get("costs") or feasibility.get("costBreakdown") or {},
            "schedule": project_data.get("schedule") or project_data.get("project_schedule") or {},
            "dataQualityWarnings": data_quality.get("warnings") or feasibility.get("dataQualityWarnings") or [],
            "dataQualityMissingFields": data_quality.get("missingFields") or [],
            "engineReadiness": feasibility.get("downstreamEngineReadiness") or feasibility.get("engineReadiness") or {},
        }
        project_data.setdefault("costSchedule", artifact)
        return artifact

    if pod_key == "feasibility_cashflow":
        workflow_run = _run_domain_workflow(project_id, "feasibility", project_data, options)
        workflow_artifacts = workflow_run.get("artifacts") or {}
        feasibility = analyze_development_feasibility_use_case(project_data, options)
        artifact = {
            "artifactType": "feasibility_cashflow_pod_result",
            "domainWorkflow": _workflow_summary(workflow_run),
            "standardArtifacts": workflow_artifacts,
            "developerFeasibility": workflow_artifacts.get("developerFeasibility") or feasibility,
            "returnMetrics": workflow_artifacts.get("returnMetrics"),
            "feasibility": workflow_artifacts.get("feasibility"),
        }
        try:
            artifact["cashFlowRunResult"] = run_cash_flow_use_case(project_data, feasibility)
        except Exception as exc:  # pragma: no cover - defensive artifact, not a hard pod failure
            artifact["cashFlowRunError"] = {
                "message": str(exc),
                "reason": "cash_flow_engine_input_not_ready",
            }
        project_data["developerFeasibility"] = artifact["developerFeasibility"]
        if artifact.get("returnMetrics"):
            project_data["returnMetrics"] = artifact["returnMetrics"]
        if artifact.get("feasibility"):
            project_data["feasibility"] = artifact["feasibility"]
        if isinstance(artifact.get("cashFlowRunResult"), dict):
            project_data["cashFlowRunResult"] = artifact["cashFlowRunResult"]
        _apply_feasibility_summary_to_project(project_data, feasibility)
        return artifact

    if pod_key == "pf_credit":
        workflow_run = _run_domain_workflow(project_id, "pf_structure_review", project_data, options)
        workflow_artifacts = workflow_run.get("artifacts") or {}
        standard_artifacts = _current_standard_artifacts(project_data)
        standard_artifacts.update({k: v for k, v in workflow_artifacts.items() if v is not None})
        pf_structure = workflow_artifacts.get("pfStructure") or _calculate_pf_structure_artifact(project_data, options)
        standard_artifacts["pfStructure"] = pf_structure
        scenario_result = _run_pf_scenario_if_ready(project_id, project_data, options)
        if scenario_result:
            standard_artifacts["scenariosEvaluation"] = scenario_result
            project_data["scenariosEvaluation"] = scenario_result
        risk_register = RiskRegisterAgent().build(project_data, standard_artifacts)
        standard_artifacts["riskRegister"] = risk_register
        credit_review = CreditReviewAgent().decide(project_data, standard_artifacts)
        standard_artifacts["creditReviewDecision"] = credit_review
        artifact = {
            "artifactType": "pf_credit_pod_result",
            "domainWorkflow": _workflow_summary(workflow_run),
            "standardArtifacts": standard_artifacts,
            "pfStructure": pf_structure,
            "scenariosEvaluation": scenario_result,
            "riskRegister": risk_register,
            "creditReviewDecision": credit_review,
        }
        try:  # deterministic covenant/EOD definitions (reviewed by the pod AI reviewer)
            artifact["covenantEod"] = evaluate_covenant_eod_use_case(project_data)
            project_data["covenantEod"] = artifact["covenantEod"]
        except Exception:  # never let covenant derivation break the credit pod
            pass
        project_data["pfStructureAnalysis"] = artifact["pfStructure"]
        project_data["pfStructure"] = artifact["pfStructure"]
        project_data["riskRegister"] = risk_register
        project_data["creditReviewDecision"] = credit_review
        return artifact

    if pod_key == "public_policy":
        artifact = {
            "artifactType": "public_policy_pod_result",
            "developabilityScore": score_developability_use_case(project_data),
            "policyNotes": [
                "Public-policy pod currently reuses developability and regulation evidence until dedicated policy simulation agents are implemented.",
            ],
        }
        project_data["publicPolicyReview"] = artifact
        return artifact

    raise ValueError(f"Pod {pod_key} not found")


def _run_domain_workflow(
    project_id: str,
    workflow: str,
    project_data: Dict[str, Any],
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """Run an existing domain workflow and return its standard artifacts."""
    run = OrchestrationAgent().run_workflow(project_id, project_data, workflow, options)
    for key, value in (run.get("artifacts") or {}).items():
        if value is not None:
            project_data[key] = value
    return run


def _workflow_summary(run: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "runId": run.get("run_id"),
        "workflow": run.get("workflow"),
        "status": run.get("status"),
        "stepCount": len(run.get("steps") or []),
        "artifactKeys": sorted((run.get("artifacts") or {}).keys()),
        "errorCount": len(run.get("errors") or []),
    }


def _document_market_evidence_payload(project_data: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("document_market_evidence", "documentMarketEvidence", "market_evidence", "marketEvidence"):
        value = project_data.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _document_market_fact_summary(project_data: Dict[str, Any], market_evidence_review: Dict[str, Any] | None = None) -> Dict[str, Any]:
    document_evidence = _document_market_evidence_payload(project_data)
    evidence_summary = (market_evidence_review or {}).get("evidenceSummary") if isinstance(market_evidence_review, dict) else {}
    evidence_summary = evidence_summary if isinstance(evidence_summary, dict) else {}
    source_document = document_evidence.get("sourceDocument") if isinstance(document_evidence.get("sourceDocument"), dict) else {}
    product_mix = document_evidence.get("productMix") or document_evidence.get("product_mix") or []
    fact_count = 0
    for key in (
        "productMix",
        "product_mix",
        "comparables",
        "manualComparables",
        "manual_comparables",
        "transactionComparables",
        "transaction_comparables",
        "presaleComparables",
        "presale_comparables",
        "listingComparables",
        "listing_comparables",
        "rentComparables",
        "rent_comparables",
    ):
        fact_count += _list_count(document_evidence.get(key))
    if document_evidence.get("proposedPricePerPy") not in (None, ""):
        fact_count += 1
    if document_evidence.get("demandLevel"):
        fact_count += 1
    if document_evidence.get("upcomingSupplyUnits") not in (None, ""):
        fact_count += 1

    return {
        "schemaVersion": "realytics-document-market-facts-v1",
        "hasDocumentMarketEvidence": bool(document_evidence),
        "sourceDocument": source_document,
        "parserVersion": document_evidence.get("parserVersion"),
        "productMixCount": _list_count(product_mix),
        "saleComparableCount": _sale_comparable_count(document_evidence),
        "rentComparableCount": _list_count(document_evidence.get("rentComparables") or document_evidence.get("rent_comparables")),
        "sourceMetadataCount": evidence_summary.get("sourceMetadataCount"),
        "evidenceRecordCount": evidence_summary.get("evidenceRecordCount"),
        "sourceLocatorCount": evidence_summary.get("sourceLocatorCount"),
        "fieldLocatorCoverage": evidence_summary.get("fieldLocatorCoverage"),
        "proposedPricePerPy": document_evidence.get("proposedPricePerPy") or document_evidence.get("proposed_price_per_py"),
        "demandLevel": document_evidence.get("demandLevel") or document_evidence.get("demand_level"),
        "upcomingSupplyUnits": document_evidence.get("upcomingSupplyUnits") or document_evidence.get("upcoming_supply_units"),
        "reconciliationStatus": (market_evidence_review or {}).get("status") if isinstance(market_evidence_review, dict) else None,
        "issueCount": len((market_evidence_review or {}).get("issues") or []) if isinstance(market_evidence_review, dict) else 0,
        "factCount": fact_count,
    }


def _document_market_reconciliation_summary(market_evidence_review: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(market_evidence_review, dict):
        return {
            "schemaVersion": "realytics-document-market-reconciliation-v1",
            "status": "not_run",
            "issues": [],
            "pricingChecks": [],
            "rentChecks": [],
            "evidenceSummary": {},
        }
    return {
        "schemaVersion": "realytics-document-market-reconciliation-v1",
        "status": market_evidence_review.get("status"),
        "evidenceSummary": market_evidence_review.get("evidenceSummary") or {},
        "pricingChecks": market_evidence_review.get("pricingChecks") or [],
        "rentChecks": market_evidence_review.get("rentChecks") or [],
        "issues": market_evidence_review.get("issues") or [],
        "agentMessages": market_evidence_review.get("agentMessages") or {},
    }


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _sale_comparable_count(document_evidence: Dict[str, Any]) -> int:
    return sum(
        _list_count(document_evidence.get(key))
        for key in (
            "comparables",
            "manualComparables",
            "manual_comparables",
            "transactionComparables",
            "transaction_comparables",
            "presaleComparables",
            "presale_comparables",
            "listingComparables",
            "listing_comparables",
        )
    )


def _pod_term_surface_metadata(pod_key: str, artifact: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Attach a compact controlled-vocabulary boundary to persisted pod artifacts.

    This does not replace deep Evidence Graph nodes. It gives DB readers and
    downstream Pods a stable first pass over which canonical terms the artifact
    is expected to affect.
    """
    pod_terms: Dict[str, List[tuple[str, str, str]]] = {
        "land_legal": [
            ("PNU", "pnu", "land.pnu"),
            ("대지면적", "land_area_sqm", "land.area_sqm"),
            ("개별공시지가", "official_land_price", "landEvidence.officialLandPrice.pricePerSqm"),
            ("제한물권", "encumbrance", "landEvidence.registry.encumbrances"),
            ("건축허가", "building_permit", "legal.permits.buildingPermit"),
        ],
        "market_product": [
            ("총분양수입", "total_sales_revenue", "feasibility_input.total_revenue"),
            ("분양률", "sales_rate", "marketability.salesRate"),
            ("Exit 분양률", "exit_sales_rate", "scenario.exitSalesRate"),
            ("Cap Rate", "cap_rate", "real_asset.capRate"),
        ],
        "cost_schedule": [
            ("공사비", "construction_cost", "feasibility_input.construction_cost"),
            ("준공", "completion", "schedule.completion"),
            ("공정률", "construction_progress_rate", "schedule.constructionProgressRate"),
        ],
        "feasibility_cashflow": [
            ("총분양수입", "total_sales_revenue", "feasibility_input.total_revenue"),
            ("총사업비", "total_project_cost", "feasibility_input.total_cost"),
            ("Max Minus", "max_minus", "cashFlow.maxMinus"),
            ("LTC", "ltc", "pf_metrics.ltc"),
        ],
        "pf_credit": [
            ("PF 대출금", "pf_total_amount", "pf_structure.amount.sum"),
            ("LTV", "ltv", "pf_metrics.ltv"),
            ("LTC", "ltc", "pf_metrics.ltc"),
            ("DSCR", "dscr", "pf_metrics.dscr"),
            ("선순위 Tranche", "senior_tranche", "pf_structure.tranches.senior.amount"),
            ("Covenant", "covenant", "legal.covenants"),
            ("기한이익상실 사유", "eod", "legal.eodTriggers"),
        ],
        "public_policy": [
            ("용도지역", "zoning", "regulation.zoning"),
            ("용적률", "floor_area_ratio", "regulation.applied_far"),
            ("건폐율", "building_coverage_ratio", "regulation.applied_bcr"),
        ],
    }
    surfaces = [
        build_term_surface_record(
            label,
            source_field=f"chiefPodArtifacts[{pod_key}].controlledTerm",
            source_locator={"sourceType": "chief_pod_artifact", "podKey": pod_key, "artifactType": artifact.get("artifactType")},
            term_id=term_id,
            canonical_field=canonical_field,
            match_type="pod_controlled_term",
            confidence=0.9,
        )
        for label, term_id, canonical_field in pod_terms.get(pod_key, [])
    ]
    artifact_type = artifact.get("artifactType")
    if artifact_type:
        surfaces.append(
            build_term_surface_record(
                artifact_type,
                source_field=f"chiefPodArtifacts[{pod_key}].artifactType",
                source_locator={"sourceType": "chief_pod_artifact", "podKey": pod_key},
                match_type="pod_artifact_type",
                confidence=0.7,
            )
        )
    return surfaces


def _current_standard_artifacts(project_data: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "landAgentReview",
        "marketEvidenceReview",
        "marketabilityAnalysis",
        "developerFeasibility",
        "feasibility",
        "cashFlowRunResult",
        "returnMetrics",
        "pfStructure",
        "legalPermitReview",
        "developabilityScore",
    ]
    return {key: project_data.get(key) for key in keys if project_data.get(key) is not None}


def _run_pf_scenario_if_ready(
    project_id: str,
    project_data: Dict[str, Any],
    options: Dict[str, Any],
) -> Dict[str, Any] | None:
    request = _build_scenario_request(project_id, project_data, options)
    if request is None:
        return None
    return ScenarioEngineAgent().run_scenario_set(request).model_dump(by_alias=True)


def _build_scenario_request(
    project_id: str,
    project_data: Dict[str, Any],
    options: Dict[str, Any],
) -> ScenarioSetRequest | None:
    product_mix = _scenario_product_mix(project_data, options)
    pf_structure = _scenario_pf_structure(project_data, options)
    if not product_mix or not pf_structure:
        return None
    return ScenarioSetRequest.model_validate(
        {
            "scenarioSetId": f"chief-pod-{project_id}-pf-credit",
            "scenarioSetName": "Chief Deal Orchestrator PF-Credit Pod",
            "projectId": project_id,
            "productMix": product_mix,
            "pfStructure": pf_structure,
            "sourceMetadata": {
                "sourceName": "Chief Deal Orchestrator",
                "sourceType": "agent_artifact",
                "sourceUrlOrEvidenceId": f"project:{project_id}:chief_pod:pf_credit",
                "confidence": 0.8,
            },
        }
    )


def _scenario_product_mix(project_data: Dict[str, Any], options: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_items = options.get("productMix") or project_data.get("product_mix") or project_data.get("productMix") or []
    items: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        units = int(_number(item.get("units") or item.get("unitCount") or item.get("count")) or 0)
        total_revenue = _number(item.get("totalRevenue") or item.get("total_revenue") or item.get("revenue")) or 0.0
        avg_unit_price = _number(item.get("avgUnitPrice") or item.get("avg_unit_price") or item.get("unitPrice")) or 0.0
        if not avg_unit_price and units and total_revenue:
            avg_unit_price = total_revenue / units
        if units > 0 and total_revenue > 0:
            items.append(
                {
                    "type": str(item.get("type") or item.get("productType") or f"product_{index}"),
                    "units": units,
                    "avgUnitPrice": avg_unit_price,
                    "totalRevenue": total_revenue,
                }
            )
    if items:
        return items

    feasibility = project_data.get("feasibility_input") or {}
    total_revenue = _number(
        options.get("totalRevenue")
        or options.get("total_revenue")
        or feasibility.get("total_revenue")
        or feasibility.get("sales_revenue")
    ) or 0.0
    units = int(
        _number(
            options.get("units")
            or project_data.get("architectural_plan", {}).get("unit_count")
            or project_data.get("architecturalPlan", {}).get("unitCount")
        )
        or 0
    )
    if total_revenue > 0 and units > 0:
        return [
            {
                "type": "project_total",
                "units": units,
                "avgUnitPrice": total_revenue / units,
                "totalRevenue": total_revenue,
            }
        ]
    return []


def _scenario_pf_structure(project_data: Dict[str, Any], options: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_tranches = options.get("pfStructure") or options.get("tranches") or project_data.get("pf_structure") or project_data.get("pfStructure") or []
    tranches: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_tranches, start=1):
        if not isinstance(item, dict):
            continue
        amount = _number(item.get("amount") or item.get("loanAmount") or item.get("principal")) or 0.0
        if amount <= 0:
            continue
        tranches.append(
            {
                "tranche": str(item.get("tranche") or item.get("name") or f"Tr.{index}"),
                "amount": amount,
                "priority": int(_number(item.get("priority") or item.get("repaymentPriority")) or index),
                "interestRate": _number(item.get("interestRate") or item.get("interest_rate")) or 0.0,
                "feeRate": _number(item.get("feeRate") or item.get("fee_rate")) or 0.0,
            }
        )
    if tranches:
        return tranches

    feasibility = project_data.get("feasibility_input") or {}
    pf_amount = _number(options.get("pfAmount") or feasibility.get("pf_amount") or feasibility.get("pfLoan")) or 0.0
    if pf_amount > 0:
        return [{"tranche": "Senior", "amount": pf_amount, "priority": 1, "interestRate": 0.0, "feeRate": 0.0}]
    return []


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _apply_feasibility_summary_to_project(project_data: Dict[str, Any], feasibility: Dict[str, Any]) -> None:
    """Sync computed feasibility totals back into feasibility_input so downstream
    engines (cash flow, PF) read them. DevelopmentFeasibilityAgent.analyze returns
    the totals under ``revenue``/``costs``/``developerReturns`` (not
    revenueAssumptions/costBreakdown) — reading the wrong keys previously left
    feasibility_input un-synced. Only fills a field when it has a positive value
    and is missing/zero in feasibility_input (never overwrites real input)."""
    feasibility_input = project_data.setdefault("feasibility_input", {})
    revenue = feasibility.get("revenue") or feasibility.get("revenueAssumptions") or {}
    costs = feasibility.get("costs") or feasibility.get("costBreakdown") or {}
    returns = feasibility.get("developerReturns") or feasibility.get("summary") or {}

    def _fill(key: str, value: Any) -> None:
        if value in (None, "") or not isinstance(value, (int, float)) or value <= 0:
            return
        if not feasibility_input.get(key):
            feasibility_input[key] = value

    if isinstance(revenue, dict):
        _fill("total_revenue", revenue.get("totalRevenue") or revenue.get("total_revenue"))
    if isinstance(costs, dict):
        _fill("total_cost", costs.get("totalCost") or costs.get("total_cost"))
        _fill("construction_cost", costs.get("constructionCost") or costs.get("construction_cost"))
        _fill("land_cost", costs.get("landCost") or costs.get("land_cost"))
        _fill("finance_cost", costs.get("financeCost") or costs.get("finance_cost"))
    if isinstance(returns, dict):
        profit_margin = returns.get("profitMargin") or returns.get("developerProfitMargin")
        if profit_margin not in (None, ""):
            project_data.setdefault("expected_results", {})["profit_margin"] = profit_margin


def _calculate_pf_structure_artifact(project_data: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
    feasibility = project_data.get("feasibility_input", {}) or {}
    tranches = options.get("tranches") or project_data.get("pf_structure") or project_data.get("pfStructure") or []
    source_data = {
        "tranches": tranches,
        "total_revenue": options.get("total_revenue", feasibility.get("total_revenue", 0)),
        "total_cost": options.get("total_cost", feasibility.get("total_cost", 0)),
        "finance_cost": options.get("finance_cost", feasibility.get("finance_cost", 0)),
        "other_priority_costs": options.get(
            "other_priority_costs",
            options.get("finance_cost", feasibility.get("finance_cost", 0)),
        ),
        "net_operating_income": options.get("net_operating_income", options.get("noi", 0)),
        "debt_service": options.get("debt_service", 0),
    }
    result = calculate_pf_metrics(source_data)
    metrics = result.get("metrics", {})
    return {
        **result,
        "ltv": (metrics.get("ltv") or {}).get("value"),
        "ltc": (metrics.get("ltc") or {}).get("value"),
        "dscr": (metrics.get("dscr") or {}).get("value"),
        "exit_sale_rate": (metrics.get("exit_sale_rate") or {}).get("value"),
    }


def _documents(project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(project_data.get("source_documents") or project_data.get("sourceDocuments") or [])


def _doc_types(project_data: Dict[str, Any]) -> set[str]:
    return {
        str(doc.get("documentType") or doc.get("document_type") or "")
        for doc in _documents(project_data)
        if doc
    }


def _has_any(project_data: Dict[str, Any], *keys: str) -> bool:
    return any(_truthy(project_data.get(key)) for key in keys)


def _missing_inputs(project_data: Dict[str, Any]) -> List[MissingInputRegisterItem]:
    docs = _doc_types(project_data)
    land = project_data.get("land") or {}
    feasibility = project_data.get("feasibility_input") or project_data.get("feasibilityStudy") or {}
    market = project_data.get("market") or project_data.get("marketEvidenceReview") or {}
    pf_structure = project_data.get("pf_structure") or project_data.get("pfStructure") or []
    schedule = project_data.get("schedule") or project_data.get("project_schedule") or {}
    regulation = project_data.get("regulation") or project_data.get("zoning") or {}
    legal = project_data.get("legal") or project_data.get("legalReview") or {}

    items: List[MissingInputRegisterItem] = []

    def add(
        item_id: str,
        category: str,
        description: str,
        required_for: List[str],
        severity: str,
        sources: List[str],
    ) -> None:
        items.append(
            MissingInputRegisterItem(
                itemId=item_id,
                category=category,  # type: ignore[arg-type]
                description=description,
                requiredFor=required_for,
                severity=severity,  # type: ignore[arg-type]
                acceptableSources=sources,
            )
        )

    if not _documents(project_data):
        add("source_documents_missing", "report", "원본 사업자료가 아직 업로드되지 않았습니다.", ["all_pods"], "blocking", ["사업계획서", "IM", "PDF", "Excel"])
    if not ({"business_plan", "basic_architectural_plan"} & docs or project_data.get("project", {}).get("name")):
        add("project_identity_missing", "feasibility", "사업명, 위치, 상품유형을 확정할 사업개요 자료가 필요합니다.", ["deal_intake", "feasibility_cashflow"], "high", ["사업계획서", "건축 기본계획", "IM/IR"])
    if not (land.get("pnu") or _has_any(project_data, "land_acquisition", "land_ledger", "landLedger")):
        add("pnu_or_land_ledger_missing", "land", "PNU 또는 토지조서가 필요합니다.", ["land_legal", "public_policy"], "blocking", ["토지조서", "토지대장", "VWorld/PNU API"])
    if not (project_data.get("landEvidence") or project_data.get("land_evidence") or land.get("officialLandPricePerSqm")):
        add("land_evidence_missing", "legal", "공시지가, 지적특성, 등기·제한물권 증빙이 필요합니다.", ["pf_credit"], "blocking", ["공시지가 evidence", "등기부", "지적/토지특성 API"])
    if not regulation:
        add("regulation_data_missing", "legal", "용도지역, 지구단위계획, 건축가능성 검토자료가 필요합니다.", ["land_legal", "market_product"], "high", ["토지이용계획", "조례/법규 API", "건축 기본계획"])
    if not legal:
        add("legal_cp_missing", "legal", "인허가, 신탁, 담보, 책임준공 등 법무 선행조건 검토자료가 필요합니다.", ["land_legal", "pf_credit"], "high", ["법률 검토 메모", "대출약정 초안", "신탁계약"])
    if not (market.get("transactionComparables") or market.get("manualComparables") or "market_evidence" in docs):
        add("market_evidence_missing", "market", "실거래, 분양사례, 임대료, 미분양/청약 등 시장근거가 필요합니다.", ["market_product", "pf_credit"], "high", ["국토부 실거래가", "분양사례", "임대료/Rent Roll", "제휴 데이터"])
    if not _has_any(project_data, "product_mix", "productMix"):
        add("product_mix_missing", "product", "상품 구성, 면적, 세대/호실 수, 전용률, 분양면적이 필요합니다.", ["market_product", "feasibility_cashflow"], "high", ["건축개요", "상품구성표", "분양가표"])
    if not _truthy(feasibility.get("total_revenue") or feasibility.get("sales_revenue")):
        add("revenue_assumption_missing", "feasibility", "총수입 또는 상품별 수입 가정이 필요합니다.", ["feasibility_cashflow"], "high", ["사업수지 Excel", "분양가표", "임대료/매각가 자료"])
    if not _truthy(feasibility.get("total_cost") or feasibility.get("construction_cost")):
        add("cost_assumption_missing", "cost", "총사업비, 공사비, 간접비, 금융비용 가정이 필요합니다.", ["cost_schedule", "feasibility_cashflow"], "high", ["사업수지 Excel", "공사비 내역", "견적/도급계약"])
    if not schedule:
        add("schedule_missing", "schedule", "토지잔금, 인허가, 착공, 분양, 준공, 입주 일정이 필요합니다.", ["cost_schedule", "feasibility_cashflow", "pf_credit"], "medium", ["사업일정표", "공정표", "분양일정"])
    if not pf_structure:
        add("pf_structure_missing", "pf", "대출금액, Tranche, 금리, 수수료, 상환순위 등 PF 구조가 필요합니다.", ["pf_credit"], "high", ["PF Term Sheet", "대출조건표"])
    return items


def _project_memory(project_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "project": project_data.get("project") or {},
        "land": project_data.get("land") or {},
        "regulation": project_data.get("regulation") or project_data.get("zoning") or {},
        "productMix": project_data.get("product_mix") or project_data.get("productMix") or [],
        "feasibilityInput": project_data.get("feasibility_input") or {},
        "pfStructure": project_data.get("pf_structure") or project_data.get("pfStructure") or [],
    }


def _evidence_vault(project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []
    for doc in _documents(project_data):
        evidence.append(
            {
                "evidenceType": "source_document",
                "documentType": doc.get("documentType") or doc.get("document_type"),
                "originalFileName": doc.get("originalFileName") or doc.get("fileName"),
                "archiveId": doc.get("archiveId"),
                "hash": doc.get("sha256") or doc.get("fileHash"),
                "sourceLocator": {"type": "uploaded_file", "archiveId": doc.get("archiveId")},
            }
        )
    for record in list(project_data.get("landEvidence") or project_data.get("land_evidence") or []):
        if isinstance(record, dict):
            evidence.append({"evidenceType": record.get("evidenceType") or "land_evidence", **record})
    parsed_facts = _parsed_fact_records(project_data)
    if parsed_facts:
        evidence.append(
            {
                "evidenceType": "parsed_fact_store",
                "recordCount": len(parsed_facts),
                "summary": project_data.get("parsedFactsSummary") or {},
                "sourceLocator": {"type": "deal_state_records", "recordType": "parsed_fact"},
            }
        )
    return evidence


def _assumption_book(project_data: Dict[str, Any]) -> List[DealStateValue]:
    rows: List[DealStateValue] = []
    overrides = project_data.get("assumption_overrides") or project_data.get("assumptionOverrides") or []
    for idx, override in enumerate(overrides if isinstance(overrides, list) else []):
        if not isinstance(override, dict):
            continue
        rows.append(
            DealStateValue(
                key=str(override.get("canonicalField") or override.get("checkKey") or f"override.{idx}"),
                value=override.get("approvedValue"),
                valueType="assumption",
                unit=override.get("unit"),
                confidence=1.0,
                evidenceIds=list(override.get("evidenceIds") or []),
                sourceLocator={"type": "human_override", "overrideId": override.get("overrideId")},
                reviewStatus="approved",
            )
        )
    return rows


def _metrics(project_data: Dict[str, Any]) -> List[DealStateValue]:
    feasibility = project_data.get("feasibility_input") or {}
    expected = project_data.get("expected_results") or {}
    pf_structure = project_data.get("pf_structure") or project_data.get("pfStructure") or []
    metrics: List[DealStateValue] = []

    def metric(key: str, value: Any, value_type: str = "source", unit: str | None = None) -> None:
        if _truthy(value):
            metrics.append(
                DealStateValue(
                    key=key,
                    value=value,
                    valueType=value_type,  # type: ignore[arg-type]
                    unit=unit,
                    confidence=0.8 if value_type == "source" else 0.7,
                )
            )

    metric("feasibility.totalRevenue", feasibility.get("total_revenue") or feasibility.get("sales_revenue"), "source", "KRW")
    metric("feasibility.totalCost", feasibility.get("total_cost"), "source", "KRW")
    metric("feasibility.landCost", feasibility.get("land_cost"), "source", "KRW")
    metric("feasibility.constructionCost", feasibility.get("construction_cost"), "source", "KRW")
    metric("expected.profitMargin", expected.get("profit_margin"), "calculated", "ratio")
    if isinstance(pf_structure, list) and pf_structure:
        total_pf = sum(float(t.get("amount") or 0) for t in pf_structure if isinstance(t, dict))
        metric("pf.totalLoanAmount", total_pf, "calculated", "KRW")
    return metrics


def _risks(project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    risks: List[Dict[str, Any]] = []
    for key in ("riskRegister", "risk_register", "risk_matrix"):
        value = project_data.get(key)
        if isinstance(value, list):
            risks.extend([row for row in value if isinstance(row, dict)])
        elif isinstance(value, dict):
            rows = value.get("rows") or value.get("risks")
            if isinstance(rows, list):
                risks.extend([row for row in rows if isinstance(row, dict)])
    return risks


def _parsed_fact_reconciliation_issues(project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    facts = _parsed_fact_records(project_data)
    if not facts:
        return []
    issues: List[Dict[str, Any]] = []
    facts_by_key: Dict[str, List[Dict[str, Any]]] = {}
    for fact in facts:
        key = str(fact.get("canonicalField") or fact.get("factKey") or fact.get("key") or "")
        if not key:
            continue
        facts_by_key.setdefault(key, []).append(fact)

    for key, rows in facts_by_key.items():
        numeric_rows = [
            (row, _number(row.get("normalizedValue") if row.get("normalizedValue") is not None else row.get("value")))
            for row in rows
        ]
        numeric_rows = [(row, value) for row, value in numeric_rows if value is not None]
        if len(numeric_rows) >= 2:
            values = [value for _, value in numeric_rows]
            denominator = max(abs(max(values)), 1.0)
            variance = (max(values) - min(values)) / denominator
            if variance > 0.01:
                issues.append(
                    {
                        "code": f"PARSED_FACT_VALUE_CONFLICT:{key}",
                        "category": "data_quality",
                        "severity": "high",
                        "message": f"파싱 후보값 {key}이 문서/셀/Agent 결과 사이에서 1% 이상 다릅니다.",
                        "canonicalField": key,
                        "targetAgents": _target_agents_from_facts(rows),
                        "parsedFacts": [_parsed_fact_issue_preview(row) for row, _ in numeric_rows[:6]],
                        "sourceLocators": [row.get("sourceLocator") for row, _ in numeric_rows if row.get("sourceLocator")],
                    }
                )

    for fact in facts:
        key = str(fact.get("canonicalField") or fact.get("factKey") or fact.get("key") or "")
        if not (fact.get("critical") or key in _critical_parsed_fact_fields()):
            continue
        if _parsed_fact_has_specific_locator(fact):
            continue
        issues.append(
            {
                "code": f"PARSED_FACT_SOURCE_LOCATOR_WEAK:{key or fact.get('parserPath')}",
                "category": "evidence",
                "severity": "medium",
                "message": f"핵심 파싱 후보값 {key or fact.get('parserPath')}에 문서 페이지, 엑셀 셀, API row 수준의 근거 위치가 부족합니다.",
                "canonicalField": key,
                "targetAgents": _target_agents_from_facts([fact]),
                "parsedFacts": [_parsed_fact_issue_preview(fact)],
                "sourceLocators": [fact.get("sourceLocator")] if fact.get("sourceLocator") else [],
            }
        )
    return issues


def _parsed_fact_records(project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    records = project_data.get("parsedFacts") or project_data.get("parsed_facts") or []
    if isinstance(records, list):
        return [record for record in records if isinstance(record, dict)]
    package = project_data.get("parsedFactPackage") or project_data.get("parsed_fact_package") or {}
    if isinstance(package, dict) and isinstance(package.get("records"), list):
        return [record for record in package["records"] if isinstance(record, dict)]
    return []


def _target_agents_from_facts(facts: List[Dict[str, Any]]) -> List[str]:
    agents: List[str] = []
    pod_to_agent = {
        "land_legal": "Land-Legal Pod",
        "market_product": "Market-Product Pod",
        "cost_schedule": "Cost-Schedule Pod",
        "feasibility_cashflow": "Feasibility-Cashflow Pod",
        "pf_credit": "PF-Credit Pod",
        "public_policy": "Public-Policy Pod",
        "chief_orchestrator": "Chief Deal Orchestrator",
    }
    for fact in facts:
        for pod_key in fact.get("targetPods") or []:
            label = pod_to_agent.get(str(pod_key), str(pod_key))
            if label not in agents:
                agents.append(label)
    return agents or ["Chief Deal Orchestrator"]


def _parsed_fact_issue_preview(fact: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "recordId": fact.get("recordId"),
        "canonicalField": fact.get("canonicalField") or fact.get("factKey") or fact.get("key"),
        "parserPath": fact.get("parserPath"),
        "value": fact.get("value"),
        "normalizedValue": fact.get("normalizedValue"),
        "sourceLocator": fact.get("sourceLocator"),
        "locatorStatus": fact.get("locatorStatus"),
        "confidence": fact.get("confidence"),
    }


def _parsed_fact_has_specific_locator(fact: Dict[str, Any]) -> bool:
    locator = fact.get("sourceLocator")
    if not isinstance(locator, dict):
        return False
    return any(locator.get(key) for key in ("cell", "apiRowId", "row", "rowIndex", "page", "pageNumber", "evidenceId", "sourceUrlOrEvidenceId", "artifactKey"))


def _critical_parsed_fact_fields() -> set[str]:
    return {
        "land.address",
        "land.pnu",
        "land.area_sqm",
        "landEvidence.officialLandPrice.pricePerSqm",
        "architectural_plan.gross_floor_area_sqm",
        "architectural_plan.unit_count",
        "feasibility_input.total_revenue",
        "feasibility_input.total_cost",
        "feasibility_input.land_cost",
        "feasibility_input.construction_cost",
        "pf_structure.amount.sum",
        "market.proposedPricePerPy",
        "productMix.totalRevenue",
        "productMix.pricePerPy",
        "marketComparable.pricePerPy",
    }


def _conflicts(project_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    quality = project_data.get("dataQuality") or project_data.get("data_quality") or {}
    conflicts = quality.get("conflicts") if isinstance(quality, dict) else None
    return [row for row in conflicts if isinstance(row, dict)] if isinstance(conflicts, list) else []


def _agent_pods(missing: List[MissingInputRegisterItem], workflow_track: str = "development") -> List[AgentPodPlan]:
    pods = [
        AgentPodPlan(
            podKey="land_legal",
            name="Land-Legal Pod",
            objective="필지, 법규, 권리, 인허가, 물리적 제약을 검증합니다.",
            agents=["Land & Parcel Agent", "Zoning / Regulation Agent", "Rights / Encumbrance Agent", "Permit Feasibility Agent", "Site Constraint Agent"],
            requiredInputs=["토지조서", "PNU", "공시지가", "등기부", "법규/인허가 자료"],
            expectedOutputs=["parcel_analysis", "zoning_summary", "title_risk", "permit_risk"],
        ),
        AgentPodPlan(
            podKey="market_product",
            name="Market-Product Pod",
            objective="시장근거, 수요, 경쟁공급, 상품구성, 분양가/임대료를 검증합니다.",
            agents=["Market Comparable Agent", "Demand / Absorption Agent", "Product Concept Agent", "Pricing Agent", "Competition Agent"],
            requiredInputs=["실거래/분양/임대 사례", "상품구성", "분양가표", "경쟁공급 자료"],
            expectedOutputs=["market_comparables", "product_options", "pricing_opinion", "absorption_forecast"],
        ),
        AgentPodPlan(
            podKey="cost_schedule",
            name="Cost-Schedule Pod",
            objective="공사비, 간접비, 일정, VE, 시공사 리스크를 검토합니다.",
            agents=["Construction Cost Agent", "Indirect Cost Agent", "Schedule Agent", "VE / Spec Optimization Agent", "Contractor Risk Agent"],
            requiredInputs=["공사비 내역", "간접비 내역", "사업일정표", "공정표", "시공사 자료"],
            expectedOutputs=["cost_plan", "schedule_plan", "contractor_risk", "ve_options"],
        ),
        AgentPodPlan(
            podKey="feasibility_cashflow",
            name="Feasibility-Cashflow Pod",
            objective="사업수지, 개발이익, 월별 현금흐름, Max Minus, 민감도를 계산합니다.",
            agents=["Revenue Model Agent", "Development Cost Model Agent", "Proforma Agent", "Monthly Cash Flow Agent", "Max Minus / Sensitivity Agent"],
            dependencies=["market_product", "cost_schedule"],
            requiredInputs=["사업수지 Excel", "상품별 수입", "비용표", "일정", "분양대금 납입조건"],
            expectedOutputs=["feasibility_statement", "monthly_cashflow", "max_minus", "sensitivity"],
        ),
        AgentPodPlan(
            podKey="pf_credit",
            name="PF-Credit Pod",
            objective="PF 구조, Tranche, LTV/LTC/DSCR, Exit, Covenant, Waterfall, 대주단 관점을 검토합니다.",
            agents=["PF Structuring Agent", "Tranche Agent", "LTV / LTC / DSCR Agent", "Exit Sales Rate Agent", "Covenant / Waterfall Agent", "Lender Persona Agent"],
            dependencies=["land_legal", "feasibility_cashflow"],
            requiredInputs=["PF Term Sheet", "Cash Flow", "Waterfall", "담보/신용보강 자료"],
            expectedOutputs=["tranche_structure", "pf_metrics", "exit_sale_rate", "covenants", "credit_opinion"],
        ),
        AgentPodPlan(
            podKey="public_policy",
            name="Public-Policy Pod",
            objective="공공성, 도시계획, 기부채납, 정책 시뮬레이션을 검토합니다.",
            agents=["Public Feasibility Agent", "Urban Planning Agent", "Public Contribution Agent", "Policy Simulation Agent"],
            requiredInputs=["도시계획 자료", "공공기여 자료", "정책/공모 지침"],
            expectedOutputs=["public_feasibility", "urban_planning_risk", "policy_simulation"],
        ),
    ]
    return _apply_track_pod_policy(pods, workflow_track)


def _workflow_track(project_data: Dict[str, Any], options: Dict[str, Any]) -> str:
    latest_triage = project_data.get("latestIntakeTriage")
    triage_track = latest_triage.get("recommendedTrack") if isinstance(latest_triage, dict) else None
    return normalize_workflow_track(
        str(
            options.get("workflowTrack")
            or options.get("track")
            or project_data.get("workflowTrack")
            or project_data.get("track")
            or triage_track
            or ""
        )
    )


def _apply_track_pod_policy(pods: List[AgentPodPlan], workflow_track: str) -> List[AgentPodPlan]:
    policy = _track_pod_policies().get(normalize_workflow_track(workflow_track), _track_pod_policies()["development"])
    by_key = {pod.pod_key: pod for pod in pods}

    for pod_key, dependencies in policy.get("dependencies", {}).items():
        pod = by_key.get(pod_key)
        if pod:
            pod.dependencies = [dep for dep in dependencies if dep in by_key and dep != pod_key]

    for pod_key, outputs in policy.get("extraExpectedOutputs", {}).items():
        pod = by_key.get(pod_key)
        if pod:
            pod.expected_outputs = list(dict.fromkeys([*pod.expected_outputs, *outputs]))

    ordered_keys = policy.get("podOrder") or [pod.pod_key for pod in pods]
    return [by_key[key] for key in ordered_keys if key in by_key]


def _track_pod_policies() -> Dict[str, Dict[str, Any]]:
    return {
        "development": {
            "podOrder": ["land_legal", "market_product", "cost_schedule", "feasibility_cashflow", "pf_credit", "public_policy"],
            "dependencies": {
                "market_product": ["land_legal"],
                "cost_schedule": ["land_legal"],
                "feasibility_cashflow": ["market_product", "cost_schedule"],
                "pf_credit": ["land_legal", "feasibility_cashflow"],
                "public_policy": ["land_legal"],
            },
            "extraExpectedOutputs": {
                "feasibility_cashflow": ["developer_return", "development_margin", "cash_in_out_reconciliation"],
                "pf_credit": ["pf_readiness", "preliminary_lender_conditions"],
            },
        },
        "pf": {
            "podOrder": ["land_legal", "market_product", "cost_schedule", "feasibility_cashflow", "pf_credit", "public_policy"],
            "dependencies": {
                "market_product": ["land_legal"],
                "cost_schedule": ["land_legal"],
                "feasibility_cashflow": ["market_product", "cost_schedule"],
                "pf_credit": ["land_legal", "market_product", "feasibility_cashflow"],
                "public_policy": ["land_legal", "pf_credit"],
            },
            "extraExpectedOutputs": {
                "pf_credit": ["approval_readiness_gate", "cure_plan", "conditions_precedent", "conditions_subsequent"],
                "feasibility_cashflow": ["waterfall_recovery", "senior_shortfall_check"],
            },
        },
        "finance": {
            "podOrder": ["land_legal", "feasibility_cashflow", "pf_credit", "market_product", "cost_schedule", "public_policy"],
            "dependencies": {
                "feasibility_cashflow": ["land_legal"],
                "pf_credit": ["land_legal", "feasibility_cashflow"],
                "market_product": ["land_legal"],
                "cost_schedule": ["feasibility_cashflow"],
                "public_policy": ["land_legal"],
            },
            "extraExpectedOutputs": {
                "pf_credit": ["collateral_package", "refinancing_capacity", "dscr_covenant"],
                "feasibility_cashflow": ["debt_service_capacity"],
            },
        },
        "real_asset_finance": {
            "podOrder": ["land_legal", "market_product", "feasibility_cashflow", "pf_credit", "cost_schedule", "public_policy"],
            "dependencies": {
                "market_product": ["land_legal"],
                "feasibility_cashflow": ["market_product"],
                "pf_credit": ["land_legal", "market_product", "feasibility_cashflow"],
                "cost_schedule": ["land_legal"],
                "public_policy": ["land_legal"],
            },
            "extraExpectedOutputs": {
                "market_product": ["noi_cap_rate_opinion", "rent_roll_evidence", "vacancy_risk"],
                "pf_credit": ["asset_finance_terms", "collateral_ltv", "exit_refinancing_path"],
            },
        },
        "reit": {
            "podOrder": ["land_legal", "market_product", "feasibility_cashflow", "pf_credit", "public_policy", "cost_schedule"],
            "dependencies": {
                "market_product": ["land_legal"],
                "feasibility_cashflow": ["market_product"],
                "pf_credit": ["feasibility_cashflow"],
                "public_policy": ["land_legal", "pf_credit"],
                "cost_schedule": ["land_legal"],
            },
            "extraExpectedOutputs": {
                "feasibility_cashflow": ["distribution_capacity", "stress_distribution_ratio"],
                "public_policy": ["reit_compliance_check", "disclosure_risk"],
            },
        },
        "fund": {
            "podOrder": ["market_product", "pf_credit", "feasibility_cashflow", "land_legal", "cost_schedule", "public_policy"],
            "dependencies": {
                "pf_credit": ["market_product"],
                "feasibility_cashflow": ["market_product"],
                "land_legal": ["market_product"],
                "cost_schedule": ["feasibility_cashflow"],
                "public_policy": ["land_legal"],
            },
            "extraExpectedOutputs": {
                "pf_credit": ["fund_waterfall", "investor_recovery", "manager_risk"],
                "feasibility_cashflow": ["fund_return_sensitivity"],
            },
        },
    }


def _track_execution_stages(workflow_track: str) -> List[Dict[str, Any]]:
    stages_by_track = {
        "development": [
            {"stage": 1, "name": "Land and permit feasibility", "pods": ["land_legal", "public_policy"]},
            {"stage": 2, "name": "Product, market, cost plan", "pods": ["market_product", "cost_schedule"]},
            {"stage": 3, "name": "Developer feasibility and PF readiness", "pods": ["feasibility_cashflow", "pf_credit"]},
        ],
        "pf": [
            {"stage": 1, "name": "Credit evidence gate", "pods": ["land_legal", "market_product"]},
            {"stage": 2, "name": "Cash flow, waterfall, recovery", "pods": ["cost_schedule", "feasibility_cashflow"]},
            {"stage": 3, "name": "PF decision and covenants", "pods": ["pf_credit", "public_policy"]},
        ],
        "finance": [
            {"stage": 1, "name": "Collateral and borrower package", "pods": ["land_legal"]},
            {"stage": 2, "name": "Debt service and refinancing capacity", "pods": ["feasibility_cashflow", "pf_credit"]},
            {"stage": 3, "name": "Monitoring and market support", "pods": ["market_product", "cost_schedule", "public_policy"]},
        ],
        "real_asset_finance": [
            {"stage": 1, "name": "Asset due diligence", "pods": ["land_legal", "market_product"]},
            {"stage": 2, "name": "NOI, cap rate, exit finance", "pods": ["feasibility_cashflow", "pf_credit"]},
            {"stage": 3, "name": "Operating risk and monitoring", "pods": ["cost_schedule", "public_policy"]},
        ],
        "reit": [
            {"stage": 1, "name": "Asset pool and valuation evidence", "pods": ["land_legal", "market_product"]},
            {"stage": 2, "name": "Distribution and financing stress", "pods": ["feasibility_cashflow", "pf_credit"]},
            {"stage": 3, "name": "REIT compliance and disclosure", "pods": ["public_policy", "cost_schedule"]},
        ],
        "fund": [
            {"stage": 1, "name": "Investment thesis and asset strategy", "pods": ["market_product", "land_legal"]},
            {"stage": 2, "name": "Fund terms, waterfall, recovery", "pods": ["pf_credit", "feasibility_cashflow"]},
            {"stage": 3, "name": "Risk monitoring and investor reporting", "pods": ["cost_schedule", "public_policy"]},
        ],
    }
    return stages_by_track.get(normalize_workflow_track(workflow_track), stages_by_track["development"])


def _track_recalculation_rules(workflow_track: str) -> List[Dict[str, Any]]:
    base_rules = [
        {"trigger": "finance_cost_changed", "rerun": ["feasibility_cashflow", "pf_credit"]},
        {"trigger": "market_price_unsupported", "rerun": ["market_product", "feasibility_cashflow"]},
        {"trigger": "legal_product_infeasible", "rerun": ["market_product", "feasibility_cashflow", "pf_credit"]},
        {"trigger": "max_minus_exceeds_pf_limit", "rerun": ["pf_credit", "feasibility_cashflow"]},
    ]
    track_rules = {
        "development": [
            {"trigger": "product_program_changed", "rerun": ["market_product", "cost_schedule", "feasibility_cashflow"]},
            {"trigger": "permit_constraint_changed", "rerun": ["land_legal", "market_product", "feasibility_cashflow"]},
        ],
        "pf": [
            {"trigger": "senior_shortfall_detected", "rerun": ["pf_credit", "feasibility_cashflow"]},
            {"trigger": "covenant_breach_detected", "rerun": ["pf_credit", "public_policy"]},
        ],
        "finance": [
            {"trigger": "collateral_value_changed", "rerun": ["pf_credit", "feasibility_cashflow"]},
            {"trigger": "dscr_breach_detected", "rerun": ["pf_credit"]},
        ],
        "real_asset_finance": [
            {"trigger": "noi_changed", "rerun": ["market_product", "feasibility_cashflow", "pf_credit"]},
            {"trigger": "cap_rate_changed", "rerun": ["market_product", "pf_credit"]},
        ],
        "reit": [
            {"trigger": "distribution_capacity_changed", "rerun": ["feasibility_cashflow", "pf_credit", "public_policy"]},
            {"trigger": "reit_compliance_issue", "rerun": ["public_policy", "pf_credit"]},
        ],
        "fund": [
            {"trigger": "investor_waterfall_changed", "rerun": ["pf_credit", "feasibility_cashflow"]},
            {"trigger": "fund_term_changed", "rerun": ["pf_credit", "public_policy"]},
        ],
    }
    return [*base_rules, *track_rules.get(normalize_workflow_track(workflow_track), [])]


def _pod_blocked(pod: AgentPodPlan, missing: List[MissingInputRegisterItem]) -> bool:
    if not missing:
        return False
    for item in missing:
        if item.severity == "blocking" and (pod.pod_key in item.required_for or "all_pods" in item.required_for):
            return True
    return False


def _task_graph(pods: List[AgentPodPlan], workflow_track: str = "development") -> Dict[str, Any]:
    normalized_track = normalize_workflow_track(workflow_track)
    workflow_definition = get_track_workflow_definition(normalized_track)
    stages = _track_execution_stages(normalized_track)
    lifecycle_process = get_development_process()
    stage_by_pod = {
        pod_key: stage
        for stage in stages
        for pod_key in stage.get("pods", [])
    }
    return {
        "workflowTrack": normalized_track,
        "trackWorkflow": workflow_definition,
        "executionStages": stages,
        "lifecycleProcess": lifecycle_process,
        "feedbackLoops": lifecycle_process["feedbackLoops"],
        "recommendedRunOrder": [pod.pod_key for pod in pods],
        "nodes": [
            {
                "id": pod.pod_key,
                "label": pod.name,
                "agents": pod.agents,
                "parallel": pod.can_run_parallel,
                "stage": stage_by_pod.get(pod.pod_key),
            }
            for pod in pods
        ],
        "edges": [
            {"from": dep, "to": pod.pod_key, "type": "dependency"}
            for pod in pods
            for dep in pod.dependencies
        ],
        "recalculationRules": _track_recalculation_rules(normalized_track),
        "aiAgentRoles": _agent_ai_roles_v2(normalized_track),
    }


def _agent_ai_roles_v2(workflow_track: str) -> Dict[str, Any]:
    """Define where an LLM may reason, verify, or draft.

    Financial calculations remain deterministic. LLMs can classify documents,
    extract facts with locators, reconcile assumptions, challenge the deal
    thesis, explain results, and draft report text only when linked evidence or
    explicit assumptions are attached.
    """
    base_roles = {
        "ParserAgent": {
            "llmUse": "required_when_document_is_unstructured",
            "purpose": "업로드 문서에서 사업개요, 토지, 수지, PF 조건을 근거 locator와 함께 추출합니다.",
            "guardrail": "근거 없는 숫자 생성 금지. sourceLocator 없는 값은 확정값으로 쓰지 않습니다.",
        },
        "IntakeTriageAgent": {
            "llmUse": "optional_with_deterministic_fallback",
            "purpose": "문서 분류 결과를 바탕으로 개발, PF, 금융, 실물금융, 리츠, 펀드 트랙을 추천합니다.",
            "guardrail": "트랙 추천 신뢰도는 업무 라우팅 신뢰도이며 승인 가능성으로 해석하지 않습니다.",
        },
        "MarketProductPod": {
            "llmUse": "recommended",
            "purpose": "분양가, 임대료, 비교사례, 흡수율 근거를 해석하고 부족한 시장자료를 요청합니다.",
            "guardrail": "비교사례, 출처, base date 없이 가격 적정성을 확정하지 않습니다.",
        },
        "LandLegalPod": {
            "llmUse": "assistive_only",
            "purpose": "토지조서, 등기, 인허가 증빙의 누락/충돌을 설명하고 보완자료 요청 문구를 생성합니다.",
            "guardrail": "PNU, 공시지가, 등기 권리관계는 API 또는 증빙 기반으로만 판단합니다.",
        },
        "FeasibilityCashflowPod": {
            "llmUse": "extract_and_explain_only",
            "purpose": "임의 양식 사업수지 Excel 항목을 표준 스키마에 매핑하고 계산 결과를 설명합니다.",
            "guardrail": "사업수지, Cash Flow, Max Minus, Waterfall 계산은 deterministic 함수만 사용합니다.",
        },
        "PFCreditPod": {
            "llmUse": "review_and_draft",
            "purpose": "계산 결과를 바탕으로 대주단 관점 리스크, cure, 승인조건 초안을 작성합니다.",
            "guardrail": "Senior shortfall, LTV breach 등 수치 리스크는 LLM 의견만으로 waiver하지 않습니다.",
        },
        "AIRiskCommittee": {
            "llmUse": "recommended",
            "purpose": "Devil's Advocate, Credit Reviewer, Legal/Compliance, Market Stress, Construction Risk, Lender Persona, IC Judge 의견을 생성합니다.",
            "guardrail": "Base, Downside, Upside, Kill Case 수치는 evidence graph와 계산엔진 결과를 기준으로만 판단합니다.",
        },
        "ReportStudio": {
            "llmUse": "recommended",
            "purpose": "PF 심사보고서, IC Memo, Term Sheet 문구 초안을 생성합니다.",
            "guardrail": "보고서 문장은 source/evidence id 또는 assumption id에 연결합니다.",
        },
    }
    track_overrides = {
        "real_asset_finance": {
            "MarketProductPod": {
                "purpose": "임대료, NOI, Cap Rate, 실물 인수 comps를 해석하고 실물금융 검토자료를 요청합니다.",
            },
        },
        "reit": {
            "PFCreditPod": {
                "purpose": "편입자산, 배당재원, 감정가, 운용구조, 공시 리스크를 투자자 보호 관점으로 검토합니다.",
            },
        },
        "fund": {
            "PFCreditPod": {
                "purpose": "펀드 설정, 투자자모집, 대출/에쿼티 구조, 운용/회수 리스크를 검토합니다.",
            },
        },
    }
    roles = {key: dict(value) for key, value in base_roles.items()}
    for key, patch in track_overrides.get(workflow_track, {}).items():
        roles[key] = {**roles.get(key, {}), **patch}
    return {
        "schemaVersion": "realytics-agent-ai-roles-v1",
        "workflowTrack": workflow_track,
        "defaultBrainPolicy": "user_byok_profile_or_gemini_free_fallback",
        "calculationPolicy": "LLM은 계산값을 직접 만들지 않고 deterministic engine 결과를 검증, 설명, 문서화합니다.",
        "roles": roles,
    }


def _agent_ai_roles_v2(workflow_track: str) -> Dict[str, Any]:
    """UTF-8 clean Agent/Pod AI role policy used by the Chief task graph.

    The duplicate definition intentionally overrides the legacy mojibake text
    above without changing the surrounding orchestration logic.
    """
    base_roles = {
        "ParserAgent": {
            "llmUse": "required_when_document_is_unstructured",
            "purpose": "업로드 문서에서 사업개요, 토지, 수지, PF 조건을 근거 locator와 함께 추출합니다.",
            "guardrail": "근거 없는 숫자 생성 금지. sourceLocator 없는 값은 확정값으로 쓰지 않습니다.",
        },
        "IntakeTriageAgent": {
            "llmUse": "optional_with_deterministic_fallback",
            "purpose": "문서 분류 결과를 바탕으로 부동산개발, PF, 금융, 실물금융, 리츠, 펀드 트랙을 추천합니다.",
            "guardrail": "트랙 추천 신뢰도는 업무 라우팅 신뢰도이며 승인 가능성으로 해석하지 않습니다.",
        },
        "LandLegalPod": {
            "llmUse": "assistive_only",
            "purpose": "토지조서, 등기, 인허가 증빙의 누락/충돌을 설명하고 보완자료 요청 문구를 생성합니다.",
            "guardrail": "PNU, 공시지가, 등기 권리관계는 API 또는 증빙 기반으로만 판단합니다.",
        },
        "MarketProductPod": {
            "llmUse": "recommended",
            "purpose": "분양가, 임대료, 비교사례, 흡수율 근거를 해석하고 부족한 시장자료를 요청합니다.",
            "guardrail": "비교사례, 출처, 기준일 없이 가격 적정성을 확정하지 않습니다.",
        },
        "CostSchedulePod": {
            "llmUse": "recommended",
            "purpose": "공사비, 제경비, 공정률, 책임준공, 시공사 리스크 자료의 누락과 재계산 필요성을 설명합니다.",
            "guardrail": "공사비 추정은 증빙 또는 승인된 가정 없이는 확정값으로 쓰지 않습니다.",
        },
        "FeasibilityCashflowPod": {
            "llmUse": "extract_and_explain_only",
            "purpose": "사업수지 Excel 항목을 표준 스키마에 매핑하고 Cash Flow 계산 결과를 설명합니다.",
            "guardrail": "사업수지, Cash Flow, Max Minus, Waterfall 계산은 deterministic engine만 사용합니다.",
        },
        "PFCreditPod": {
            "llmUse": "review_and_draft",
            "purpose": "계산 결과를 바탕으로 대주단 관점 리스크, cure, 승인조건 초안을 작성합니다.",
            "guardrail": "Senior shortfall, LTV breach 같은 수치 리스크는 LLM 의견만으로 waiver하지 않습니다.",
        },
        "AIRiskCommittee": {
            "llmUse": "recommended",
            "purpose": "Devil's Advocate, Credit Reviewer, Legal/Compliance, Market Stress, Construction Risk, Lender Persona, IC Judge 의견을 생성합니다.",
            "guardrail": "Base, Downside, Upside, Kill Case 판단은 evidence graph와 계산엔진 결과를 기준으로만 합니다.",
        },
        "ReportStudio": {
            "llmUse": "recommended",
            "purpose": "PF 심사보고서, IC Memo, Term Sheet 문구 초안을 생성합니다.",
            "guardrail": "보고서 문장은 source/evidence id 또는 assumption id에 연결합니다.",
        },
    }
    track_overrides = {
        "real_asset_finance": {
            "MarketProductPod": {
                "purpose": "임대료, NOI, Cap Rate, 실물 인수 comps를 해석하고 실물금융 검토자료를 요청합니다.",
            },
        },
        "reit": {
            "PFCreditPod": {
                "purpose": "편입자산, 배당재원, 감정가, 운용구조, 공시 리스크를 투자자 보호 관점으로 검토합니다.",
            },
        },
        "fund": {
            "PFCreditPod": {
                "purpose": "펀드 설정, 투자자 모집, 대출/에쿼티 구조, 운용/회수 리스크를 검토합니다.",
            },
        },
    }
    roles = {key: dict(value) for key, value in base_roles.items()}
    for key, patch in track_overrides.get(workflow_track, {}).items():
        roles[key] = {**roles.get(key, {}), **patch}
    return {
        "schemaVersion": "realytics-agent-ai-roles-v1",
        "workflowTrack": workflow_track,
        "defaultBrainPolicy": "user_byok_profile_or_gemini_free_fallback",
        "calculationPolicy": "LLM은 계산값을 직접 만들지 않고 deterministic engine 결과를 검증, 설명, 문서화합니다.",
        "roles": roles,
    }


def _agent_ai_roles(workflow_track: str) -> Dict[str, Any]:
    """Where an LLM is allowed to reason, explain, or draft.

    Calculations remain deterministic. LLMs may classify, extract, reconcile,
    challenge, explain, and draft report text only when evidence/assumptions are
    attached.
    """
    base_roles = {
        "ParserAgent": {
            "llmUse": "required_when_document_is_unstructured",
            "purpose": "업로드 문서에서 사업개요, 토지, 수지, PF 조건을 근거 locator와 함께 추출",
            "guardrail": "근거 없는 숫자 생성 금지. sourceLocator 없는 값은 확정값으로 쓰지 않음.",
        },
        "IntakeTriageAgent": {
            "llmUse": "optional_with_deterministic_fallback",
            "purpose": "문서 분류 결과를 바탕으로 개발/PF/금융/실물금융/리츠/펀드 트랙 추천",
            "guardrail": "트랙 추천 신뢰도는 라우팅 신뢰도이며 승인 가능성으로 해석하지 않음.",
        },
        "MarketProductPod": {
            "llmUse": "recommended",
            "purpose": "분양가, 임대료, 비교사례, 흡수율 근거를 해석하고 부족한 시장자료를 요청",
            "guardrail": "비교사례/출처/base date 없이 가격 적정성 확정 금지.",
        },
        "LandLegalPod": {
            "llmUse": "assistive_only",
            "purpose": "토지조서·등기·인허가 증빙의 누락/충돌 설명과 보완자료 요청 문구 생성",
            "guardrail": "PNU, 공시지가, 등기 권리관계는 API/증빙 기반. LLM 추정 금지.",
        },
        "FeasibilityCashflowPod": {
            "llmUse": "extract_and_explain_only",
            "purpose": "임의 양식 사업수지 Excel의 항목 매핑과 계산 결과 설명",
            "guardrail": "사업수지, Cash Flow, Max Minus, Waterfall 계산은 deterministic 함수만 사용.",
        },
        "PFCreditPod": {
            "llmUse": "review_and_draft",
            "purpose": "계산 결과를 바탕으로 대주단 관점 리스크, cure, 승인조건 초안 작성",
            "guardrail": "Senior shortfall, LTV breach 등 수치 리스크는 LLM 의견으로 waiver하지 않음.",
        },
        "AIRiskCommittee": {
            "llmUse": "recommended",
            "purpose": "Devil's Advocate, Credit Reviewer, Legal/Compliance, Market Stress, Construction Risk, Lender Persona, IC Judge 의견 생성",
            "guardrail": "Base/Downside/Upside/Kill Case 수치와 evidence graph를 기준으로만 판단.",
        },
        "ReportStudio": {
            "llmUse": "recommended",
            "purpose": "PF 심사보고서, IC Memo, Term Sheet 문구 초안 생성",
            "guardrail": "보고서 문장은 source/evidence id 또는 assumption id에 연결.",
        },
    }
    track_overrides = {
        "real_asset_finance": {
            "MarketProductPod": {
                "purpose": "임대료, NOI, Cap Rate, 실물 인수 comps를 해석하고 실물금융 검토자료를 요청",
            },
        },
        "reit": {
            "PFCreditPod": {
                "purpose": "편입자산, 배당재원, 감정가, 운용구조, 공시 리스크를 투자자 보호 관점으로 검토",
            },
        },
        "fund": {
            "PFCreditPod": {
                "purpose": "펀드 설정, 투자대상, 대출/에쿼티 구조, 운용·회수 리스크를 검토",
            },
        },
    }
    roles = {key: dict(value) for key, value in base_roles.items()}
    for key, patch in track_overrides.get(workflow_track, {}).items():
        roles[key] = {**roles.get(key, {}), **patch}
    return {
        "schemaVersion": "realytics-agent-ai-roles-v1",
        "workflowTrack": workflow_track,
        "defaultBrainPolicy": "user_byok_profile_or_gemini_free_fallback",
        "calculationPolicy": "LLM은 계산값을 직접 만들지 않고 deterministic engine의 결과를 검증·설명·문서화한다.",
        "roles": roles,
    }


def _scenario_placeholders(project_data: Dict[str, Any]) -> List[ScenarioConclusion]:
    metrics = {
        row.key: row.value for row in _metrics(project_data)
    }
    return [
        ScenarioConclusion(
            scenario="base_case",
            summary="현재 확보된 자료와 승인된 가정을 기준으로 한 기준 시나리오입니다.",
            keyMetrics=metrics,
            riskFlags=[],
            decisionImplication="Pod 실행과 reconciliation 이후 결정 가능합니다.",
        ),
        ScenarioConclusion(
            scenario="downside_case",
            summary="분양가 하락, 공사비 상승, 금리 상승, 분양 지연을 반영해야 하는 하방 시나리오입니다.",
            keyMetrics={},
            riskFlags=["NEEDS_STRESS_TEST"],
            decisionImplication="Stress Test Agent 실행 전 확정할 수 없습니다.",
        ),
        ScenarioConclusion(
            scenario="upside_case",
            summary="시장가격 지지, 빠른 흡수율, 금융조건 개선이 확인될 때의 상방 시나리오입니다.",
            keyMetrics={},
            riskFlags=["NEEDS_MARKET_EVIDENCE"],
            decisionImplication="시장근거와 승인된 가정이 있어야 사용할 수 있습니다.",
        ),
        ScenarioConclusion(
            scenario="kill_case",
            summary="법규상 불가능, 토지권원 미확보, 선순위 회수불능, 핵심 인허가 실패 등 사업 중단 시나리오입니다.",
            keyMetrics={},
            riskFlags=["NEEDS_RISK_COMMITTEE"],
            decisionImplication="AI Risk Committee와 Human Expert Review에서 판단해야 합니다.",
        ),
    ]


def _preliminary_decision_gate(missing: List[MissingInputRegisterItem]) -> DecisionGateResult:
    blocking = [item.description for item in missing if item.severity == "blocking"]
    high = [item.description for item in missing if item.severity == "high"]
    if blocking:
        return DecisionGateResult(
            decision="HOLD",
            rationale="필수 증빙 또는 토지/법무 자료가 부족하여 최종 분석을 보류합니다.",
            requiredSupplements=blocking[:6],
            conditionsPrecedent=blocking[:6],
            humanReviewRequired=True,
        )
    if high:
        return DecisionGateResult(
            decision="CONDITIONAL_PASS",
            rationale="핵심 자료 일부가 부족하므로 조건부로 Pod 분석을 진행할 수 있습니다.",
            requiredSupplements=high[:6],
            conditionsPrecedent=high[:3],
            humanReviewRequired=True,
        )
    return DecisionGateResult(
        decision="PASS",
        rationale="초기 Deal Room 구성에 필요한 핵심 자료가 확보되어 Pod 분석을 진행할 수 있습니다.",
        humanReviewRequired=True,
    )
