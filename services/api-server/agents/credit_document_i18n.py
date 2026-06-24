from __future__ import annotations

"""Korean localization for lender-facing credit documents.

Internal agents emit English codes/messages (UNSOLD_LTV_BREACH,
credit_enhancement_triggered, "Review waterfall, unsold LTV ..."). A Korean
credit officer's 여신심사보고서 / 거래조건서 must read in Korean business language,
so the document layer translates risk codes, severities, senior status, and the
recurring English action phrases. Unmapped text is left as-is (best-effort), so
nothing breaks — only known strings are localized.
"""

from typing import Any, Dict

SEVERITY_KO = {"critical": "심각", "high": "높음", "medium": "중간", "low": "낮음"}

SENIOR_STATUS_KO = {
    "normal": "정상",
    "credit_enhancement_triggered": "신용보강 발동",
    "senior_principal_loss_possible": "원금 일부 손실 가능",
    "not_evaluated": "평가 불가",
}

STATUS_KO = {
    "blocking": "차단",
    "blocking_issue": "차단 이슈",
    "needs_review": "검토 필요",
    "caution": "주의",
    "clear": "양호",
    "high_risk": "고위험",
}

RISK_CODE_KO = {
    "UNSOLD_LTV_BREACH": "미분양 LTV 한도 초과",
    "NO_COMPARABLE_DATA": "비교사례 자료 부족",
    "TRIGGER_REQUIRED": "할인분양 트리거 설정 필요",
    "SENIOR_SHORTFALL": "선순위 원금 부족",
    "STRESS_SENIOR_SHORTFALL": "스트레스 시 선순위 원금 부족",
    "STRESS_SENIOR_PRINCIPAL_LOSS": "스트레스 시 선순위 원금 손실",
    "STRESS_CREDIT_ENHANCEMENT": "스트레스 시 신용보강 발동",
    "DATA_QUALITY_BLOCKING": "데이터 품질 차단",
    "PROPOSED_PRICE_BASIS_MISSING": "제안 분양가 근거 부족",
    "LEGAL_LAND_RIGHTS": "토지 권리관계",
    "PERMIT_SCHEDULE_READINESS": "인허가 일정 준비",
    "COVENANT_AND_LEGAL_PACKAGE_COMPLETENESS": "약정·법률 패키지 완결성",
}

# Longest phrases first so partial overlaps localize correctly.
_PHRASES = [
    ("Review waterfall, unsold LTV, discount-sale trigger, and tranche recovery scenarios.",
     "워터폴·미분양 LTV·할인분양 트리거·트랜치 회수 시나리오 재검토"),
    ("Attach comparable, presale, rent, or uploaded market evidence before final pricing conclusion.",
     "최종 분양가 결론 전 비교·분양·임대 또는 업로드 시장 증빙 첨부"),
    ("Complete land evidence enrichment and closing checklist.",
     "토지 증빙 보강 및 클로징 체크리스트 완료"),
    ("Official land price evidence with base year/source must be attached before PF approval.",
     "공시지가 증빙(기준연도·출처)을 PF 승인 전 첨부"),
    ("Registry and encumbrance evidence must be attached before PF approval.",
     "등기·권리제한 증빙을 PF 승인 전 첨부"),
    ("Official land price coverage is below 100%.", "공시지가 증빙 100% 미만"),
    ("Registry evidence coverage is below 100%.", "등기 증빙 100% 미만"),
    ("Select authoritative source, correct source data, or approve explicit assumption override.",
     "권위 있는 출처 선택·원자료 수정 또는 가정 override 승인"),
    ("Attach PNU, official land price, registry, acquisition, and source evidence.",
     "PNU·공시지가·등기·취득·출처 증빙 첨부"),
    ("Resolve or explicitly approve Data Quality Agent conflicts before final credit approval.",
     "최종 여신 승인 전 데이터 품질 충돌 해소 또는 명시적 승인"),
    ("Resolve cross-agent reconciliation issues or record an approved conservative assumption.",
     "교차 정합 이슈 해소 또는 승인된 보수적 가정 기록"),
    ("Do not proceed to final approval until must-resolve issues are cleared or formally waived.",
     "필수 해소 항목 정리 또는 정식 waiver 전까지 최종 승인 보류"),
    ("Assign owner, mitigation, and closing condition.", "담당자·완화방안·클로징 조건 지정"),
    ("Senior principal loss is possible under stress scenario(s):", "스트레스 시나리오에서 선순위 원금 손실 가능:"),
    ("Interest coverage (ICR) is", "이자보상배율(ICR)"),
    ("Stress base-case Project IRR is", "스트레스 기본 시나리오 Project IRR"),
    ("Credit decision is", "여신 결정:"),
    ("Risk Register status is", "리스크 레지스터 상태:"),
    ("Legal Review status is", "법률 검토 상태:"),
    ("Data Quality status is", "데이터 품질 상태:"),
    ("Cross-agent reconciliation status is", "교차 정합 상태:"),
    ("Marketability grade is", "시장성 등급:"),
    ("PF Exit sale rate is", "PF Exit 분양률:"),
    ("Existing mortgages or encumbrances require release or trust-transfer conditions.",
     "기존 저당·권리제한은 말소 또는 신탁 이전 조건 필요"),
    ("Land rights and encumbrances", "토지 권리·권리제한"),
    ("Korean Law MCP sidecar results were not supplied as law_context.",
     "한국 법령 MCP 결과가 law_context로 제공되지 않음"),
    ("Korean Law MCP evidence", "한국 법령 MCP 증빙"),
    ("Permit schedule readiness", "인허가 일정 준비"),
    ("Covenant and legal package completeness", "약정·법률 패키지 완결성"),
    ("require release or trust-transfer conditions", "말소 또는 신탁 이전 조건 필요"),
    ("Low presale rate", "분양률 저조"),
    ("VAT basis consistency", "부가세 기준 일관성"),
    ("Land evidence limitation", "토지 증빙 한계"),
    ("handoff review(s) remain unresolved.", "건의 핸드오프 검토 미해결"),
    ("material risk review(s) remain unresolved.", "건의 중대 리스크 검토 미해결"),
    ("with score", "· 점수"),
    ("hurdle", "허들"),
]


def localize(text: Any) -> Any:
    """Best-effort English -> Korean for credit-document free text."""
    if not isinstance(text, str) or not text:
        return text
    result = text
    for code, ko in RISK_CODE_KO.items():
        if code in result:
            result = result.replace(code, ko)
    for en, ko in _PHRASES:
        if en in result:
            result = result.replace(en, ko)
    return result


def severity_ko(severity: Any) -> str:
    return SEVERITY_KO.get(str(severity), str(severity) if severity else "—")


def senior_status_ko(status: Any) -> str:
    return SENIOR_STATUS_KO.get(str(status), str(status) if status else "—")


def status_ko(status: Any) -> str:
    return STATUS_KO.get(str(status), str(status) if status else "—")


def risk_title_ko(risk: Dict[str, Any]) -> str:
    code = risk.get("code")
    if code and code in RISK_CODE_KO:
        return RISK_CODE_KO[code]
    return localize(risk.get("title")) or (code or "리스크")
