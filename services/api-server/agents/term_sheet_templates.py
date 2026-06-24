from __future__ import annotations

"""Stage-specific term sheet (거래조건서) templates.

Learned from real Korean real-estate PF term sheets (브릿지론/토지담보대출 and
본PF). Each stage defines an ordered list of (sectionKey, label) rows matching
the 구분 | 내용 layout banks actually file, plus standard boilerplate clauses
that are largely deal-invariant (책임준공, 준수사항, EOD, 자금집행순서). The
CreditDocumentAgent fills each row with project-specific values where available
and falls back to the standard clause text or "[TBD]" — mirroring how real
indicative term sheets are drafted before final negotiation.
"""

TERM_SHEET_STAGE_BRIDGE = "bridge"
TERM_SHEET_STAGE_MAIN_PF = "main_pf"

STAGE_META = {
    TERM_SHEET_STAGE_BRIDGE: {
        "id": TERM_SHEET_STAGE_BRIDGE,
        "name": "브릿지론 (토지담보대출)",
        "description": "토지 계약금·중도금 조달을 위한 단기 토지담보대출. 본PF 조달로 상환(리파이낸싱)되는 단계.",
    },
    TERM_SHEET_STAGE_MAIN_PF: {
        "id": TERM_SHEET_STAGE_MAIN_PF,
        "name": "본PF (본대출)",
        "description": "착공·준공까지의 본PF. 관리형토지신탁·트랜치·책임준공을 갖추고 분양수입금으로 분할상환.",
    },
}

# Ordered (sectionKey, 한글 라벨) rows per stage — the 구분 column of the term sheet.
BRIDGE_SECTIONS = [
    ("borrower", "차주"),
    ("guarantor", "연대보증"),
    ("contractor", "시공사"),
    ("loan_amount", "대출금액"),
    ("fund_purpose", "자금용도"),
    ("ltv", "LTV"),
    ("interest_rate", "대출금리 / 취급수수료"),
    ("rate_method", "금리결정방식"),
    ("interest_payment", "이자지급"),
    ("drawdown_method", "인출방식"),
    ("tenor", "대출만기"),
    ("prepayment", "조기상환 / 조기상환수수료"),
    ("default_interest", "연체이자"),
    ("conditions_precedent", "인출선행조건"),
    ("conditions_concurrent", "인출동시이행조건"),
    ("security", "주요 담보 및 채권보전"),
    ("eod_events", "기한이익상실 사유"),
    ("disposal_waterfall", "담보처분 시 자금집행순위"),
    ("governing_court", "관할 법원"),
]

MAIN_PF_SECTIONS = [
    ("borrower", "차주"),
    ("lenders", "참여대주 / 금융주관사"),
    ("contractor", "시공사"),
    ("trustee", "신탁사 / 대리금융기관"),
    ("loan_amount", "대출금액 및 대출구조"),
    ("fund_purpose", "자금용도"),
    ("tenor", "대출기간"),
    ("interest_rate", "대출금리 / 취급수수료"),
    ("rate_method", "금리결정방식"),
    ("interest_payment", "이자지급"),
    ("drawdown_method", "인출방식"),
    ("repayment", "상환방법"),
    ("prepayment", "조기상환"),
    ("default_interest", "연체이자"),
    ("conditions_precedent", "인출선행조건"),
    ("conditions_subsequent", "인출후행조건"),
    ("covenants", "준수사항"),
    ("security", "주요 담보 및 채권보전"),
    ("responsible_completion", "책임준공"),
    ("discount_sale", "할인분양"),
    ("revenue_distribution", "분양대금 배분비율"),
    ("fund_management", "자금관리 및 통제"),
    ("eod_events", "채무불이행(EOD) 사유"),
    ("eod_consequences", "채무불이행의 결과"),
    ("governing_court", "관할 법원"),
]

# 토지개요/사업개요 rows that precede the 금융조건 in a real term sheet. These are
# filled by the Land Agent (대지위치·지번·공시지가·면적·감정가) and the Feasibility
# Agent (사업규모·사업수지) — the multi-agent division of labour.
OVERVIEW_SECTIONS = [
    ("site_location", "대지위치"),
    ("zoning_use", "지구·용도·규모"),
    ("lot_pnu", "지번(PNU)"),
    ("official_land_price", "공시지가"),
    ("land_area", "대지면적 / 연면적"),
    ("appraisal_collateral", "담보목록 및 감정가"),
    ("business_summary", "사업수지 요약"),
]

STAGE_SECTIONS = {
    TERM_SHEET_STAGE_BRIDGE: OVERVIEW_SECTIONS + BRIDGE_SECTIONS,
    TERM_SHEET_STAGE_MAIN_PF: OVERVIEW_SECTIONS + MAIN_PF_SECTIONS,
}

# Which agent owns each section — recorded as provenance so the term sheet is an
# auditable, multi-agent collaboration rather than one monolithic builder.
SECTION_SOURCE_AGENT = {
    # Land Agent
    "site_location": "LandAgent",
    "zoning_use": "LandAgent",
    "lot_pnu": "LandAgent",
    "official_land_price": "LandAgent",
    "land_area": "LandAgent",
    "appraisal_collateral": "LandAgent",
    "ltv": "LandAgent",
    # Feasibility / Cash Flow
    "business_summary": "DevelopmentFeasibilityAgent",
    "loan_amount": "DevelopmentFeasibilityAgent",
    "fund_purpose": "DevelopmentFeasibilityAgent",
    "tenor": "DevelopmentFeasibilityAgent",
    "repayment": "CashFlowEngine",
    "fund_management": "CashFlowEngine",
    "revenue_distribution": "CashFlowEngine",
    # PF structure / scenario
    "loan_amount_structure": "ScenarioEngine",
    "interest_rate": "ScenarioEngine",
    "rate_method": "ScenarioEngine",
    "interest_payment": "ScenarioEngine",
    "drawdown_method": "ScenarioEngine",
    "prepayment": "ScenarioEngine",
    "default_interest": "ScenarioEngine",
    # Marketability
    "discount_sale": "MarketabilityAgent",
    # Credit / Legal / Risk
    "security": "CreditReviewAgent",
    "responsible_completion": "LegalReviewAgent",
    "conditions_precedent": "CreditReviewAgent",
    "conditions_concurrent": "CreditReviewAgent",
    "conditions_subsequent": "CreditReviewAgent",
    "covenants": "CreditReviewAgent",
    "eod_events": "CreditReviewAgent",
    "eod_consequences": "CreditReviewAgent",
    "disposal_waterfall": "CreditReviewAgent",
    "governing_court": "LegalReviewAgent",
    # Intake
    "borrower": "ProjectIntake",
    "guarantor": "CreditEnhancementAgent",
    "contractor": "ProjectIntake",
    "lenders": "ProjectIntake",
    "trustee": "ProjectIntake",
}

DEFAULT_SECTION_SOURCE_AGENT = "TermSheetAgent"

# Standard, largely deal-invariant clause text distilled from the sample term
# sheets. Used as the value when project data does not supply an override.
TBD = "[TBD]"

BOILERPLATE = {
    "rate_method": "고정금리 또는 변동금리 (대주 선택)",
    "interest_payment_bridge": (
        "매 [3]개월 선취 또는 후취 (대주 선택) · 이자계산: 1년 365일, 실제 경과일수(초일산입/말일불산입) 기준 · "
        "이자유보: [N]개월분 이자유보계좌 적립"
    ),
    "interest_payment_main_pf": (
        "매 [3]개월 선취 · 이자지급일 기준 추가 [3]개월분 예상이자 별도 유보 · 이자계산: 1년 365일, 실제 경과일수 기준"
    ),
    "drawdown_method": "대출금 전액 일시인출",
    "default_interest_bridge": "대출금리 + 3.0%",
    "default_interest_main_pf": "연 19%",
    "prepayment_bridge": (
        "이자지급일에 [10]억원 이상 [1]억원 단위 조기상환 가능(10영업일 전 통지) · "
        "조기상환수수료 상환금액의 [1.0]% · 단, 본PF 조달로 상환 시 조기상환수수료 면제"
    ),
    "prepayment_main_pf": (
        "강제조기상환: 분양대금 배분비율에 따라 매 이자지급일 강제조기상환 · "
        "임의조기상환: 매 이자지급일 [10]억원 이상 각 대주별 [1]억원 단위 · 조기상환수수료 [1.0]%(최초 인출 1년 후 면제)"
    ),
    "repayment_main_pf": "분양수입금에 의한 분할상환 (대출원리금적립계좌를 통해 Tr.A 전액 상환 후 Tr.B 상환)",
    "conditions_precedent_bridge": [
        "대출계약서 및 각종 담보·금융계약 체결",
        "기한이익상실 사유 부존재",
        "대주가 만족할 수준의 감정평가기관 감정평가보고서 제출",
        "대주가 만족할 수준의 법무법인 법률의견서 제출",
        "담보물건 관련 재산세·종부세 등 세금 완납 확인",
        "기타 대주가 합리적으로 요구하는 사항",
    ],
    "conditions_concurrent_bridge": [
        "해당 필지 부지의 담보신탁 및 1순위 우선수익권 제공",
        "본건 대출 자금용도에 따른 사용",
        "준수사항의 이행",
    ],
    "conditions_precedent_main_pf": [
        "전체 사업부지 토지계약 완료",
        "사업계획승인(사업시행인가) 완료",
        "시공사와 공사도급계약 체결",
        "시공사 책임준공 확약(미이행 시 채무인수)",
        "관리형토지신탁 설정 및 Tr.A/Tr.B 대주를 1·2순위 수익자로 설정",
        "본건 대출 관련 제반 금융계약서 체결 완료",
    ],
    "conditions_subsequent_main_pf": [
        "제 약정서류 및 담보조건의 계속적 이행·충족",
        "준공(사용승인) 후 5영업일 이내 화재보험 가입 및 보험금지급청구권 근질권 설정",
        "대주가 합리적으로 필요하다고 요구하는 행위의 이행·서류 제출",
    ],
    "covenants_main_pf": [
        "법령의 준수 및 인허가 유지",
        "사업관련 자료 및 회계장부의 작성·유지",
        "매 분기 자금수지표 및 분양계약자 현황 대리금융기관 제출",
        "준공 이후 대출원리금 전액 상환 전까지 다른 비용 지급 유보",
        "대주 사전동의 없는 사업서류·정관 변경, 자금차입, 담보제공, 출자·보증 금지",
    ],
    "responsible_completion_main_pf": (
        "시공사는 관리형토지신탁계약 및 공사도급계약에 따라 여하한 사유에도 불구하고 대출약정금 인출일로부터 [40]개월 이내에 "
        "본건 건물을 완공하여 감리자 공사완료 확인 및 사용승인을 취득하도록 함. 미이행 시 잔존 대출원리금채무를 차주와 "
        "중첩적으로 채무인수하고 이로 인한 손해를 배상. 책임준공 완료 후에도 잔존 대출원리금이 있는 한 미분양세대에 대한 "
        "유치권 포기 및 담보설정에 협조."
    ),
    "discount_sale_main_pf": (
        "본건 아파트·오피스텔에 대하여 목표분양율 미달 시 할인분양 적용. 목표분양율은 총 매출액 기준, 할인분양율은 "
        "최초 분양가 기준 누적 적용. (구체 트리거·할인율은 분양관리·약정 기준)"
    ),
    "revenue_distribution_main_pf": (
        "준공(사용승인) 전: 분양대금을 배분비율에 따라 대출원리금적립계좌에 우선 적립, 잔여 대금은 사업비로 사용 · "
        "준공 후: 전체 분양대금은 대출원리금 전액 상환 후 사업비 사용"
    ),
    "fund_management_main_pf": (
        "대출원리금 전액 상환일까지 신탁사 자금관리. 자금집행순서: ①제세공과금·부담금·등기비용 → ②대출이자(연체이자 포함)·"
        "금융수수료 → ③분양해지 환불금·미분양관리비·중도금대출이자 등 필수사업비 → ④지급기일 도래 대출원금 → ⑤공사비 → "
        "⑥설계·감리·분양대행 등 사업비 → ⑦시행사 운영비. 계좌: 대출실행/분양대금납부/운영/이자유보/대출원리금적립계좌."
    ),
    "eod_events_common": [
        "원리금 등 금전지급채무를 지급기일에 이행하지 않는 경우",
        "차주 등의 진술·보장사항이 사실과 다르거나 허위로 판명된 경우",
        "약정서·담보계약서상 의무 위반이 치유요구 후 10일 내 치유되지 않는 경우",
        "본건 사업 인허가가 취소되거나 사업 진행이 중단된 경우",
        "차주의 파산·회생절차 신청 또는 개시, 중대한 부정적 변경",
    ],
    "eod_events_main_pf_extra": "시공사 책임준공 확약상 의무가 이행되지 않는 경우",
    "eod_consequences": (
        "대주의 의사결정에 따라 기한의 이익 상실 선언 및 치유기간 부여, 미인출 대출의무 소멸, 원리금·지연배상금·비용 일체의 "
        "즉시 변제 청구. 차주가 정상 이행 가능하다고 대주가 판단하면 서면통지로 기한이익을 소급적 회복 가능."
    ),
    "disposal_waterfall_bridge": (
        "담보처분 시 배분순서: ①처분비용·신탁보수·조세채권 → ②담보취득 등 필수사업비(금융비용 포함) → ③연체이자 → "
        "④이자 → ⑤대출원금"
    ),
    "governing_court": "서울중앙지방법원",
}
