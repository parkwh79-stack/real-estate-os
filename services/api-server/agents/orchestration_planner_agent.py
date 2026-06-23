from __future__ import annotations

import datetime
from typing import Any, Dict, List

from schemas.orchestration_plan import (
    OrchestrationPlanResult,
    WorkOrderOutputOption,
    WorkOrderPromptDraft,
    WorkOrderValidationOption,
    WorkflowOption,
)


def _truthy(value: Any) -> bool:
    if value in (None, "", 0, 0.0):
        return False
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


class OrchestrationPlannerAgent:
    """Intent router for REALYTICS orchestration.

    The planner turns uploaded materials into a practical work order: what can
    be analyzed now, what evidence is missing, which outputs are worth creating,
    and what validation conditions should be attached before review.
    """

    analysis_type = "orchestration_plan_v1"

    def plan(self, project_id: str, project_data: Dict[str, Any]) -> Dict[str, Any]:
        feasibility = project_data.get("feasibility_input", {}) or {}

        has_feasibility = bool(
            (_truthy(feasibility.get("total_revenue")) or _truthy(feasibility.get("sales_revenue")))
            and (_truthy(feasibility.get("total_cost")) or _truthy(feasibility.get("construction_cost")))
        )
        has_land = any(
            _truthy(project_data.get(key))
            for key in ("land_ledger", "land_evidence", "landEvidence", "parcels", "land_acquisition")
        )
        has_product_mix = _truthy(project_data.get("product_mix")) or _truthy(project_data.get("productMix"))
        has_pf = _truthy(project_data.get("pf_structure"))
        has_schedule = _truthy(project_data.get("schedule"))

        availability = {
            "feasibilityInputs": has_feasibility,
            "landData": has_land,
            "productMix": has_product_mix,
            "pfStructure": has_pf,
            "schedule": has_schedule,
        }

        workflows: List[WorkflowOption] = [
            WorkflowOption(
                key="land_review",
                nameKo="토지권원 검토",
                description="토지조서, PNU, 개별공시지가, 등기부 권리관계와 토지취득률을 검토합니다.",
                availableNow=has_land,
                agents=["LandAgent"],
                missingData=[] if has_land else ["토지조서 또는 토지 증빙자료"],
                endpoint='POST /projects/{id}/orchestration/run-workflow {"workflow":"land_review"}',
            ),
            WorkflowOption(
                key="feasibility",
                nameKo="사업수지 분석",
                description="총수입, 총사업비, 개발이익, IRR, Equity Multiple, Payback을 계산합니다.",
                availableNow=has_feasibility,
                agents=["DevelopmentFeasibilityAgent", "ReturnMetricsEngine"],
                missingData=[] if has_feasibility else ["총분양수입(total_revenue)", "총사업비(total_cost)"],
                endpoint='POST /projects/{id}/orchestration/run-workflow {"workflow":"feasibility"}',
            ),
            WorkflowOption(
                key="marketability",
                nameKo="분양성·시장성 분석",
                description="상품구성, 비교사례, 분양가 적정성, Exit 분양률, 할인분양 필요성을 검토합니다.",
                availableNow=has_product_mix,
                agents=["MarketEvidenceAgent", "MarketabilityAgent"],
                missingData=[] if has_product_mix else ["상품구성(product_mix) 또는 시장근거 자료"],
                endpoint='POST /projects/{id}/orchestration/run-workflow {"workflow":"marketability"}',
            ),
            WorkflowOption(
                key="returns_stress",
                nameKo="수익지표·Stress Test",
                description="Project IRR, Equity IRR, Equity Multiple과 Base/Mild/Severe/Tail Stress Test를 실행합니다.",
                availableNow=has_feasibility,
                agents=["ReturnMetricsEngine", "StressTestAgent"],
                missingData=[] if has_feasibility else ["사업수지 입력(total_revenue, total_cost, construction_cost)"],
                endpoint='POST /projects/{id}/orchestration/run-workflow {"workflow":"returns_stress"}',
            ),
            WorkflowOption(
                key="pf_structure_review",
                nameKo="PF 구조·신용보강 검토",
                description="LTV, LTC, DSCR, Exit 분양률과 책임준공, 채무인수, Cash Sweep 등 신용보강을 평가합니다.",
                availableNow=has_pf and has_feasibility,
                agents=["PFStructuringCalculator", "CreditEnhancementAgent"],
                missingData=(
                    []
                    if (has_pf and has_feasibility)
                    else ([] if has_pf else ["PF Tranche 구조(pf_structure)"])
                    + ([] if has_feasibility else ["사업수지 입력"])
                ),
                endpoint='POST /projects/{id}/orchestration/run-workflow {"workflow":"pf_structure_review"}',
            ),
            WorkflowOption(
                key="full_pf_review",
                nameKo="전체 PF 심사",
                description="토지, 사업수지, 분양성, 법무, 리스크, 여신심사 의견, Phase 11 보고서를 모두 실행합니다.",
                availableNow=True,
                agents=["OrchestrationAgent(full pipeline)"],
                missingData=self._full_review_gaps(has_feasibility, has_land, has_pf),
                endpoint="POST /projects/{id}/orchestrations/pf-review",
            ),
        ]

        recommended = self._recommend(has_feasibility, has_land, has_product_mix, has_pf)
        for workflow in workflows:
            workflow.recommended = workflow.key == recommended

        available_names = [workflow.nameKo for workflow in workflows if workflow.availableNow]
        result = OrchestrationPlanResult(
            projectId=project_id,
            dataAvailability=availability,
            workflows=workflows,
            outputOptions=self._output_options(recommended),
            validationOptions=self._validation_options(),
            promptDraft=self._prompt_draft(recommended, availability),
            recommendedWorkflow=recommended,
            promptKo=self._build_prompt(available_names, recommended, workflows),
            summary=self._build_summary(availability, recommended),
            createdAt=datetime.datetime.now().isoformat(),
        )
        return result.model_dump()

    @staticmethod
    def _full_review_gaps(has_feasibility: bool, has_land: bool, has_pf: bool) -> List[str]:
        gaps: List[str] = []
        if not has_feasibility:
            gaps.append("사업수지 입력이 부족하여 수익성·현금흐름 평가는 제한적으로 표시됩니다.")
        if not has_land:
            gaps.append("토지 증빙이 부족하여 토지권원 게이트가 미충족으로 표시됩니다.")
        if not has_pf:
            gaps.append("PF 구조가 부족하여 Tranche·Exit 분석은 제한적으로 평가됩니다.")
        return gaps

    @staticmethod
    def _recommend(has_feasibility: bool, has_land: bool, has_product_mix: bool, has_pf: bool) -> str:
        rich = sum([has_feasibility, has_land, has_pf])
        if has_feasibility and (has_land or has_pf) and rich >= 2:
            return "full_pf_review"
        if has_feasibility:
            return "feasibility"
        if has_product_mix:
            return "marketability"
        if has_land:
            return "land_review"
        return "full_pf_review"

    @staticmethod
    def _build_prompt(available_names: List[str], recommended: str, workflows: List[WorkflowOption]) -> str:
        recommended_workflow = next((workflow for workflow in workflows if workflow.key == recommended), None)
        recommended_label = recommended_workflow.nameKo if recommended_workflow else "전체 PF 심사"
        if available_names:
            return (
                f"업로드된 자료로 가능한 작업은 {', '.join(available_names)}입니다. "
                f"현재 추천 작업은 {recommended_label}입니다."
            )
        return "현재 분석 가능한 자료가 부족합니다. 사업계획서, 토지조서, 사업수지 Excel을 먼저 업로드해 주세요."

    @staticmethod
    def _build_summary(availability: Dict[str, bool], recommended: str) -> str:
        label_map = {
            "feasibilityInputs": "사업수지",
            "landData": "토지자료",
            "productMix": "상품구성",
            "pfStructure": "PF 구조",
            "schedule": "일정",
        }
        present = [label_map[key] for key, value in availability.items() if value]
        missing = [label_map[key] for key, value in availability.items() if not value]
        return (
            f"확인된 자료: {', '.join(present) if present else '없음'}. "
            f"부족 자료: {', '.join(missing) if missing else '없음'}. 추천 workflow: {recommended}."
        )

    @staticmethod
    def _output_options(recommended: str) -> List[WorkOrderOutputOption]:
        defaults = {
            "full_pf_review": {"phase11_report", "risk_register", "approval_readiness", "evidence_graph"},
            "feasibility": {"feasibility_excel", "cash_flow_workbook", "validation_sheet"},
            "marketability": {"marketability_memo", "pricing_comparison", "discount_trigger"},
            "land_review": {"land_evidence_package", "legal_checklist"},
            "returns_stress": {"stress_test_summary", "return_metrics"},
            "pf_structure_review": {"pf_term_summary", "credit_enhancement_memo"},
        }.get(recommended, set())
        options = [
            ("phase11_report", "PF 심사보고서", "20개 섹션 Phase 11 보고서 초안을 생성합니다."),
            ("feasibility_excel", "사업수지 Excel", "수식과 named range가 보존되는 사업수지 workbook을 생성합니다."),
            ("cash_flow_workbook", "Cash Flow Workbook", "월별 현금흐름, Max Minus, PF 인출·상환표를 생성합니다."),
            ("risk_register", "리스크 등록부", "Land, Market, Legal, Cash Flow, PF 리스크를 하나의 표로 모읍니다."),
            ("approval_readiness", "승인 가능성 대시보드", "blocking, needs_review, ready 상태를 한 화면에 정리합니다."),
            ("evidence_graph", "근거 연결도", "문서, Excel 셀, API row, Agent 산출물을 연결합니다."),
            ("marketability_memo", "분양성 검토 메모", "분양가, 흡수율, Exit 분양률, 할인분양 Trigger를 정리합니다."),
            ("land_evidence_package", "토지 증빙 패키지", "PNU, 공시지가, 등기부, 토지취득률 증빙을 묶습니다."),
        ]
        return [
            WorkOrderOutputOption(
                key=key,
                nameKo=name,
                description=description,
                defaultSelected=key in defaults,
            )
            for key, name, description in options
        ]

    @staticmethod
    def _validation_options() -> List[WorkOrderValidationOption]:
        return [
            WorkOrderValidationOption(
                key="preserve_original",
                nameKo="원본 파일 보존",
                description="업로드 파일의 원본명, SHA-256, 업로드 사용자, 보관 경로를 저장합니다.",
            ),
            WorkOrderValidationOption(
                key="formula_trace",
                nameKo="Excel 수식 경로 보존",
                description="계산값이 하드코딩되지 않고 수식·named range·셀 참조로 추적되는지 확인합니다.",
            ),
            WorkOrderValidationOption(
                key="sources_uses_balance",
                nameKo="Sources & Uses 균형 검증",
                description="Cash-In과 Cash-Out 합계, PF 조달금액과 사용금액의 대사를 확인합니다.",
            ),
            WorkOrderValidationOption(
                key="cross_document_reconciliation",
                nameKo="문서 간 숫자 대사",
                description="총분양수입, 대지면적, PF 금액, 세대수, VAT 기준의 문서 간 충돌을 찾습니다.",
            ),
            WorkOrderValidationOption(
                key="evidence_required",
                nameKo="근거 위치 필수",
                description="보고서 문장과 리스크가 문서 페이지, Excel 셀, API row, evidence ID를 갖도록 요구합니다.",
            ),
            WorkOrderValidationOption(
                key="no_fabrication",
                nameKo="무근거 값 생성 금지",
                description="공시지가, 등기부, 분양률, 비교사례가 없으면 확정값처럼 표시하지 않습니다.",
            ),
        ]

    @staticmethod
    def _prompt_draft(recommended: str, availability: Dict[str, bool]) -> WorkOrderPromptDraft:
        missing = [key for key, value in availability.items() if not value]
        missing_text = ", ".join(missing) if missing else "없음"
        return WorkOrderPromptDraft(
            body=(
                "다음 부동산 개발/PF 자료를 기준으로 실무형 재무모델과 심사 산출물을 작성하십시오.\n\n"
                f"1. 우선 실행 workflow: {recommended}\n"
                "2. 모든 가정값은 별도 입력 영역에 정리하고, 계산 시트는 원본 입력값을 직접 참조하십시오.\n"
                "3. 총분양수입, 총사업비, PF 금액, 대지면적, 세대수는 문서 간 대사표를 만드십시오.\n"
                "4. 월별 Cash Flow, Max Minus, Tranche별 Waterfall, Exit 분양률, 미분양 LTV를 수식으로 연결하십시오.\n"
                "5. 결과표에는 보고서용 요약, 검증표, source locator, formula map을 포함하십시오.\n"
                "6. 출처가 없는 값은 추정 또는 추가 검증 필요로 표시하고 확정 결론으로 쓰지 마십시오.\n"
                f"7. 현재 부족한 자료 키: {missing_text}."
            )
        )
