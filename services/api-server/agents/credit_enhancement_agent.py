from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from core.financial_input_validation import require_finite_numbers
from schemas.credit_enhancement import (
    CREDIT_ENHANCEMENT_TYPES,
    CreditEnhancementInput,
    CreditEnhancementMeasureResult,
    CreditEnhancementRequest,
    CreditEnhancementResult,
)


_CATALOG: Dict[str, Dict[str, Any]] = {
    "responsible_completion": {
        "nameKo": "책임준공확약",
        "category": "completion",
        "seniorLtvDeltaPp": 5.0,
        "gradeNotch": 0,
        "dscrSupport": 0.0,
        "critical": True,
        "riskMitigated": "공사 미완공 및 지연 리스크를 완화합니다.",
        "requiredEvidence": ["책임준공확약서", "시공사 신용등급", "시공능력평가"],
    },
    "debt_assumption": {
        "nameKo": "채무인수확약",
        "category": "credit",
        "seniorLtvDeltaPp": 0.0,
        "gradeNotch": 2,
        "dscrSupport": 0.0,
        "critical": False,
        "riskMitigated": "책임준공 미이행 등 특정 사유 발생 시 PF 채무를 인수하는 신용보강입니다.",
        "requiredEvidence": ["채무인수확약서", "인수주체 재무제표", "인수한도 및 발동조건"],
    },
    "trust_management": {
        "nameKo": "신탁·분양관리",
        "category": "structure",
        "seniorLtvDeltaPp": 0.0,
        "gradeNotch": 1,
        "dscrSupport": 0.0,
        "critical": True,
        "riskMitigated": "분양수입금과 사업비 집행을 통제하여 자금 유용 리스크를 완화합니다.",
        "requiredEvidence": ["관리형토지신탁계약서", "분양관리신탁 등기", "자금관리 약정"],
    },
    "cash_sweep_reserve": {
        "nameKo": "Cash Sweep / Reserve",
        "category": "liquidity",
        "seniorLtvDeltaPp": 0.0,
        "gradeNotch": 0,
        "dscrSupport": 0.15,
        "critical": False,
        "riskMitigated": "초과 현금의 조기상환과 DSRA 적립으로 유동성 리스크를 완화합니다.",
        "requiredEvidence": ["Cash Sweep 약정", "Reserve Account 약정", "DSRA 적립 기준"],
    },
    "equity_lockup": {
        "nameKo": "Equity Lock-up",
        "category": "sponsor",
        "seniorLtvDeltaPp": 0.0,
        "gradeNotch": 1,
        "dscrSupport": 0.0,
        "critical": False,
        "riskMitigated": "사업 종료 전 시행사 지분 또는 자기자본 회수를 제한하여 이해상충 리스크를 완화합니다.",
        "requiredEvidence": ["Equity Lock-up 약정", "지분구조표"],
    },
    "sponsor_guarantee": {
        "nameKo": "Sponsor Guarantee",
        "category": "sponsor",
        "seniorLtvDeltaPp": 2.0,
        "gradeNotch": 1,
        "dscrSupport": 0.0,
        "critical": False,
        "riskMitigated": "스폰서 또는 모회사의 보증·자금보충으로 최종 회수 안정성을 보강합니다.",
        "requiredEvidence": ["자금보충확약서", "연대보증 또는 한도보증", "모회사 재무제표"],
    },
}


class CreditEnhancementAgent:
    """Evaluate the six PF credit-enhancement measures and quantify their effect."""

    analysis_type = "credit_enhancement_v1"

    def evaluate(self, request: CreditEnhancementRequest) -> CreditEnhancementResult:
        require_finite_numbers(request.model_dump(), "creditEnhancement")
        supplied = {e.type: e for e in request.enhancements if e.type in _CATALOG}

        measures: List[CreditEnhancementMeasureResult] = [
            self._evaluate_measure(measure_type, supplied.get(measure_type))
            for measure_type in CREDIT_ENHANCEMENT_TYPES
        ]

        active = [m for m in measures if m.status == "active"]
        total_ltv_delta = sum(m.seniorLtvDeltaPp for m in active)
        total_notch = sum(m.gradeNotch for m in active)
        total_dscr_support = sum(m.dscrSupport for m in active)

        adjusted_senior_ltv = max(0.0, request.baseSeniorLtv - total_ltv_delta / 100.0)
        adjusted_dscr = (
            round(request.baseDscr + total_dscr_support, 4)
            if request.baseDscr is not None
            else None
        )

        missing_critical = [
            m.type for m in measures if _CATALOG[m.type]["critical"] and m.status != "active"
        ]

        return CreditEnhancementResult(
            analysisType=self.analysis_type,
            baseSeniorLtv=request.baseSeniorLtv,
            adjustedSeniorLtv=round(adjusted_senior_ltv, 4),
            totalSeniorLtvDeltaPp=round(total_ltv_delta, 2),
            totalGradeNotch=total_notch,
            baseDscr=request.baseDscr,
            adjustedDscr=adjusted_dscr,
            activeCount=len(active),
            coverageRatio=round(len(active) / len(CREDIT_ENHANCEMENT_TYPES), 4),
            enhancementGrade=self._enhancement_grade(len(active), missing_critical),
            measures=measures,
            missingCriticalMeasures=missing_critical,
            riskFlags=self._build_risk_flags(missing_critical, measures),
            reportParagraph=self._build_report_paragraph(active, missing_critical, total_ltv_delta, total_notch),
            createdAt=datetime.datetime.now().isoformat(),
        )

    def _evaluate_measure(
        self,
        measure_type: str,
        supplied: Optional[CreditEnhancementInput],
    ) -> CreditEnhancementMeasureResult:
        catalog = _CATALOG[measure_type]
        if supplied is None or not supplied.present:
            return CreditEnhancementMeasureResult(
                type=measure_type,
                nameKo=catalog["nameKo"],
                category=catalog["category"],
                status="missing",
                present=False,
                seniorLtvDeltaPp=0.0,
                gradeNotch=0,
                dscrSupport=0.0,
                riskMitigated=catalog["riskMitigated"],
                effectSummary="미적용 상태로 해당 신용보강 효과는 반영되지 않았습니다.",
                requiredEvidence=list(catalog["requiredEvidence"]),
                recommendation=self._recommend(catalog),
            )

        has_evidence = any(item.strip() for item in supplied.evidenceIds) or any(
            any(value.strip() if isinstance(value, str) else bool(value) for value in locator.values())
            for locator in supplied.sourceLocators
        )
        ltv_delta = supplied.seniorLtvDeltaPp if supplied.seniorLtvDeltaPp is not None else catalog["seniorLtvDeltaPp"]
        notch = supplied.gradeNotch if supplied.gradeNotch is not None else catalog["gradeNotch"]
        dscr_support = supplied.dscrSupport if supplied.dscrSupport is not None else catalog["dscrSupport"]

        if not has_evidence:
            return CreditEnhancementMeasureResult(
                type=measure_type,
                nameKo=catalog["nameKo"],
                category=catalog["category"],
                status="needs_evidence",
                present=True,
                seniorLtvDeltaPp=0.0,
                gradeNotch=0,
                dscrSupport=0.0,
                riskMitigated=catalog["riskMitigated"],
                effectSummary="약정 또는 확약은 입력되었으나 근거 문서가 없어 효과를 반영하지 않았습니다.",
                requiredEvidence=list(catalog["requiredEvidence"]),
                recommendation="근거 문서와 source locator를 첨부하면 신용보강 효과를 반영할 수 있습니다.",
                evidenceIds=list(supplied.evidenceIds),
                sourceLocators=list(supplied.sourceLocators),
            )

        return CreditEnhancementMeasureResult(
            type=measure_type,
            nameKo=catalog["nameKo"],
            category=catalog["category"],
            status="active",
            present=True,
            seniorLtvDeltaPp=round(ltv_delta, 2),
            gradeNotch=int(notch),
            dscrSupport=round(dscr_support, 4),
            riskMitigated=catalog["riskMitigated"],
            effectSummary=self._effect_summary(catalog["nameKo"], ltv_delta, notch, dscr_support),
            requiredEvidence=list(catalog["requiredEvidence"]),
            recommendation=None,
            evidenceIds=list(supplied.evidenceIds),
            sourceLocators=list(supplied.sourceLocators),
        )

    @staticmethod
    def _effect_summary(name_ko: str, ltv_delta: float, notch: int, dscr_support: float) -> str:
        parts = []
        if ltv_delta:
            parts.append(f"Senior LTV {ltv_delta:.0f}%p 인하")
        if notch:
            parts.append(f"심사등급 {notch}단계 보강")
        if dscr_support:
            parts.append(f"DSCR +{dscr_support:.2f} 안정화")
        return f"{name_ko} 적용: " + (", ".join(parts) if parts else "리스크 통제 효과")

    @staticmethod
    def _recommend(catalog: Dict[str, Any]) -> str:
        if catalog["critical"]:
            return f"{catalog['nameKo']}은 핵심 신용보강 항목입니다. 우선 확보를 권고합니다."
        return f"{catalog['nameKo']} 확보 시 {catalog['riskMitigated']}"

    @staticmethod
    def _enhancement_grade(active_count: int, missing_critical: List[str]) -> str:
        if active_count >= 5:
            grade = "A"
        elif active_count >= 4:
            grade = "B"
        elif active_count >= 3:
            grade = "C"
        elif active_count >= 2:
            grade = "D"
        else:
            grade = "E"
        if missing_critical and grade in {"A", "B"}:
            grade = "C"
        return grade

    @staticmethod
    def _build_risk_flags(
        missing_critical: List[str],
        measures: List[CreditEnhancementMeasureResult],
    ) -> List[Dict[str, Any]]:
        flags: List[Dict[str, Any]] = []
        for measure_type in missing_critical:
            flags.append(
                {
                    "code": "CREDIT_ENHANCEMENT_CRITICAL_MISSING",
                    "severity": "high",
                    "measure": measure_type,
                    "message": f"핵심 신용보강 '{_CATALOG[measure_type]['nameKo']}'이 미확보 상태입니다.",
                }
            )
        for measure in measures:
            if measure.status == "needs_evidence":
                flags.append(
                    {
                        "code": "CREDIT_ENHANCEMENT_NEEDS_EVIDENCE",
                        "severity": "caution",
                        "measure": measure.type,
                        "message": f"'{measure.nameKo}' 근거 문서가 없어 효과 미반영 상태입니다.",
                    }
                )
        return flags

    @staticmethod
    def _build_report_paragraph(
        active: List[CreditEnhancementMeasureResult],
        missing_critical: List[str],
        total_ltv_delta: float,
        total_notch: int,
    ) -> str:
        if active:
            applied = ", ".join(m.nameKo for m in active)
            paragraph = (
                f"신용보강 구조 검토 결과 {len(active)}개 수단({applied})이 근거 기반으로 확인되어 "
                f"Senior LTV를 약 {total_ltv_delta:.0f}%p 낮추고 심사등급을 {total_notch}단계 보강하는 효과가 있습니다."
            )
        else:
            paragraph = "현재 근거 기반으로 확인된 신용보강 수단이 없어 대주단 원금보존 안정성이 취약합니다."
        if missing_critical:
            names = ", ".join(_CATALOG[item]["nameKo"] for item in missing_critical)
            paragraph += f" 다만 핵심 신용보강인 {names}이 미확보 상태이므로 승인 전 보완이 필요합니다."
        return paragraph
