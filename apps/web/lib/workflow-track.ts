export type WorkflowTrack = "development" | "pf" | "finance" | "real_asset_finance" | "reit" | "fund";

export const WORKFLOW_TRACK_STORAGE_KEY = "realytics.workflow.track.v1";

export const TRACK_LABELS: Record<WorkflowTrack, string> = {
  development: "부동산 개발",
  pf: "부동산 PF",
  finance: "부동산 금융",
  real_asset_finance: "부동산 실물금융",
  reit: "리츠",
  fund: "부동산 펀드",
};

export interface TrackWorkflowStep {
  key: string;
  title: string;
  purpose: string;
  primaryAgents: string[];
  requiredMaterials: string[];
}

export interface TrackWorkflowDefinition {
  track: WorkflowTrack;
  label: string;
  intent: string;
  decisionQuestion: string;
  steps: TrackWorkflowStep[];
}

function step(
  key: string,
  title: string,
  purpose: string,
  primaryAgents: string[],
  requiredMaterials: string[],
): TrackWorkflowStep {
  return { key, title, purpose, primaryAgents, requiredMaterials };
}

export const TRACK_WORKFLOW_DEFINITIONS: Record<WorkflowTrack, TrackWorkflowDefinition> = {
  development: {
    track: "development",
    label: TRACK_LABELS.development,
    intent: "토지, 인허가, 상품기획, 사업수지, 분양성, 현금흐름을 순서대로 검증해 개발 착수 가능성을 판단합니다.",
    decisionQuestion: "이 토지와 계획으로 개발사업을 착수할 수 있는가?",
    steps: [
      step("land_and_pnu", "토지·PNU·권원 확인", "주소, PNU, 공시지가, 토지취득률과 제한물권을 먼저 확정합니다.", ["LandAgent", "PNUResolverAgent", "OfficialLandPriceAgent", "RegistryEvidenceAgent"], ["토지조서", "주소 또는 PNU", "공시지가", "등기부 또는 권리관계 증빙"]),
      step("permit_regulation", "인허가·법규 검토", "용도지역, 건축가능 규모, 인허가 일정과 개발 제한사항을 검토합니다.", ["RegulationAgent", "LegalReviewAgent"], ["건축 기본계획", "인허가 일정표", "용도지역·지구 자료"]),
      step("product_planning", "상품기획·개발구상", "가능한 개발상품, 연면적 프로그램, unit mix, 주차계획을 비교합니다.", ["ProductPlanningAgent", "MarketEvidenceAgent"], ["건축개요", "상품구성 초안", "시장·수요 근거"]),
      step("feasibility", "사업수지·개발이익 분석", "수입, 토지비, 공사비, 금융비용, 개발이익과 민감도를 계산합니다.", ["DevelopmentFeasibilityAgent", "FeasibilityWorkbookValidator"], ["사업수지 Excel", "상품구성", "공사비·간접비 가정"]),
      step("marketability", "분양성·매각성 검증", "제안 분양가, 비교사례, 흡수율, 할인분양 필요성을 검토합니다.", ["MarketEvidenceAgent", "MarketabilityAnalysisAgent"], ["분양가표", "실거래·분양사례", "임대료 또는 매각사례"]),
      step("cash_flow", "월별 Cash Flow·Max Minus", "월별 자금소요, PF 필요 한도, 부족자금 발생 시점을 계산합니다.", ["CashFlowEngine", "CalculationScenarioEngine"], ["공정·분양 일정", "분양대금 납입조건", "지출 우선순위"]),
      step("development_validation", "검증·Human Review", "사업수지 합계, Cash-In/Out, 수식, 출처, 기준값을 검토자 승인 항목으로 분리합니다.", ["ValidationAgent", "DataQualityAgent", "EvidenceGraphAgent"], ["계산 검증표", "source locator", "승인 override"]),
    ],
  },
  pf: {
    track: "pf",
    label: TRACK_LABELS.pf,
    intent: "대주단 관점에서 원금보존, Tranche 회수, 담보·신용보강, 승인조건을 검토합니다.",
    decisionQuestion: "이 PF는 어떤 조건을 붙이면 승인 가능한가, 아니면 부결해야 하는가?",
    steps: [
      step("source_reconciliation", "심사자료 대사", "사업계획서, 사업수지, Term Sheet, 토지자료의 핵심 숫자 충돌을 먼저 잡습니다.", ["ParserAgent", "DataQualityAgent", "EvidenceGraphAgent"], ["사업계획서 또는 IM", "사업수지 Excel", "PF Term Sheet"]),
      step("land_legal_gate", "토지·법무 선행조건", "토지 권원, 공시지가, 등기·제한물권, 신탁·담보 설정 가능성을 확인합니다.", ["LandAgent", "LegalReviewAgent", "RiskRegisterAgent"], ["토지조서", "등기부", "신탁계약 또는 담보자료"]),
      step("market_exit", "분양성·Exit 분양률", "분양가 적정성, 흡수율, 할인분양 Trigger, Exit 분양률을 산정합니다.", ["MarketabilityAnalysisAgent", "PFStructuringAgent"], ["분양가표", "비교사례", "청약·미분양·임대료 자료"]),
      step("waterfall_recovery", "Waterfall·미분양 LTV", "선순위·중순위·후순위 회수율, shortfall, 미분양 LTV를 계산합니다.", ["CashFlowEngine", "CalculationScenarioEngine"], ["PF Tranche 구조", "분양수입 배분규칙", "상환 Waterfall"]),
      step("covenant_eod", "Covenant·EOD 설계", "목표분양률, 공정률, LTV, 책임준공, 할인분양 Trigger와 위반 시 조치 흐름을 설계합니다.", ["CovenantEODAgent", "LegalReviewAgent", "MonitoringAgent"], ["분양률 기준표", "공정표", "대출약정 초안", "책임준공 확약"]),
      step("credit_decision", "리스크 등록부·심사의견", "Blocking risk, cure 시나리오, 승인조건, Phase 11 심사보고서 초안을 생성합니다.", ["RiskRegisterAgent", "CreditReviewAgent", "ReportAgent"], ["Risk Register", "cure evidence", "승인조건 검토 메모"]),
    ],
  },
  finance: {
    track: "finance",
    label: TRACK_LABELS.finance,
    intent: "담보대출, 리파이낸싱, 브릿지, 운영자금의 회수 가능성과 약정조건을 검토합니다.",
    decisionQuestion: "담보와 차주 현금흐름으로 대출 원리금을 회수할 수 있는가?",
    steps: [
      step("collateral_package", "담보 패키지 확인", "담보목적물, 감정가, 선순위 권리, LTV 산정 기준을 정리합니다.", ["CollateralAgent", "RegistryEvidenceAgent"], ["감정평가서", "등기부", "담보목록"]),
      step("borrower_credit", "차주·스폰서 신용 검토", "재무상태, 기존 차입, 보증·연대채무, 상환능력을 검토합니다.", ["CreditReviewAgent", "DataQualityAgent"], ["재무제표", "차입현황", "보증·담보 제공 자료"]),
      step("loan_structure", "대출구조·상환재원", "대출금액, 금리, 만기, 상환재원, 리파이낸싱 가능성을 산정합니다.", ["PFStructuringAgent", "CashFlowEngine"], ["대출조건표", "임대·매각·리파이낸싱 자료"]),
      step("covenant_monitoring", "약정·사후관리 조건", "LTV, DSCR, 이자지급, 담보훼손, 리파이낸싱 실패 시 사후관리 Rule을 설정합니다.", ["CovenantEODAgent", "MonitoringAgent", "AlertAgent"], ["대출약정", "월별 보고자료", "담보평가 갱신 기준"]),
    ],
  },
  real_asset_finance: {
    track: "real_asset_finance",
    label: TRACK_LABELS.real_asset_finance,
    intent: "실물자산 인수·담보·리파이낸싱 관점에서 NOI, Cap Rate, 임대차, 권리관계를 검토합니다.",
    decisionQuestion: "운영수익과 담보가치가 인수금융 또는 실물금융을 지탱하는가?",
    steps: [
      step("asset_due_diligence", "자산 실사·권리관계", "대상자산, 임대차, 등기·권리관계, 매매계약 조건을 확인합니다.", ["RealAssetDueDiligenceAgent", "LegalReviewAgent"], ["매매계약서", "임대차 현황", "등기부"]),
      step("noi_cap_rate", "NOI·Cap Rate 검증", "임대료, 공실률, 운영비, NOI, Cap Rate와 평가가치를 검증합니다.", ["MarketEvidenceAgent", "ValuationAgent"], ["Rent Roll", "운영비 자료", "감정평가·유사사례"]),
      step("acquisition_finance", "인수금융·Exit 검토", "대출한도, DSCR, 리파이낸싱, 매각 Exit 가능성을 검토합니다.", ["CashFlowEngine", "CreditReviewAgent"], ["대출조건", "운영 Cash Flow", "Exit 가정"]),
      step("asset_monitoring", "운영·임대차 모니터링", "임대차 이탈, 공실률, NOI 하락, Cap Rate 변화에 따른 Covenant/EOD 위험을 모니터링합니다.", ["MonitoringAgent", "CovenantEODAgent", "AlertAgent"], ["월별 임대현황", "NOI 실적", "감정가·Cap Rate 업데이트"]),
    ],
  },
  reit: {
    track: "reit",
    label: TRACK_LABELS.reit,
    intent: "리츠 편입자산, 배당재원, 감정평가, 운용규제와 공시 리스크를 검토합니다.",
    decisionQuestion: "편입자산과 운용구조가 리츠 투자자 보호와 배당 안정성을 충족하는가?",
    steps: [
      step("reit_asset_pool", "편입자산·임대차 검토", "편입 예정 자산, 임대차 안정성, 감정가, 담보권을 검토합니다.", ["RealAssetDueDiligenceAgent", "ValuationAgent"], ["편입자산 목록", "임대차계약", "감정평가서"]),
      step("distribution_capacity", "배당재원·운용수지", "NOI, 금융비용, 배당가능이익, 스트레스 배당률을 계산합니다.", ["CashFlowEngine", "ScenarioEngine"], ["운용수지", "차입조건", "배당정책"]),
      step("reit_compliance", "리츠 규제·공시 리스크", "자산요건, 차입한도, 이해상충, 공시·운용위험을 검토합니다.", ["LegalReviewAgent", "RiskRegisterAgent"], ["리츠 구조도", "운용계약", "공시·인가 자료"]),
      step("reit_monitoring", "배당·공시 모니터링", "배당가능이익, 차입한도, 공시기한, 운용위반 가능성을 사후관리합니다.", ["MonitoringAgent", "CovenantEODAgent", "AlertAgent"], ["월별 운용보고", "배당정책", "공시 체크리스트"]),
    ],
  },
  fund: {
    track: "fund",
    label: TRACK_LABELS.fund,
    intent: "부동산 펀드의 투자대상, 투자자 보호, 대출/에쿼티 구조, 운용·회수 리스크를 검토합니다.",
    decisionQuestion: "펀드 투자구조와 회수계획이 투자자 보호와 목표수익률을 충족하는가?",
    steps: [
      step("fund_terms", "펀드 설정·투자조건", "펀드 만기, 투자자, 운용보수, 투자대상과 약정조건을 정리합니다.", ["FundStructuringAgent", "LegalReviewAgent"], ["집합투자규약", "투자제안서", "투자대상 자료"]),
      step("asset_strategy", "투자대상·운용전략", "기초자산, 임대/개발/대출 전략, 레버리지와 담보구조를 검토합니다.", ["MarketEvidenceAgent", "PFStructuringAgent"], ["자산자료", "운용계획", "대출약정·담보자료"]),
      step("investor_recovery", "투자자 회수·리스크", "Waterfall, 배당/분배, 손실흡수, 매각·리파이낸싱 Exit를 검토합니다.", ["CalculationScenarioEngine", "RiskRegisterAgent"], ["Waterfall", "Exit 계획", "민감도 자료"]),
      step("fund_monitoring", "운용·투자자 보고", "투자자 보고, 약정 위반, 운용성과, 회수 지연과 EOD 알림을 관리합니다.", ["MonitoringAgent", "ReportGenerationAgent", "AlertAgent"], ["운용보고서", "투자자 보고 양식", "Covenant 체크리스트"]),
    ],
  },
};

export function normalizeWorkflowTrack(value: string | null | undefined): WorkflowTrack {
  return value && value in TRACK_LABELS ? (value as WorkflowTrack) : "development";
}

export function isWorkflowTrack(value: string | null | undefined): value is WorkflowTrack {
  return Boolean(value && value in TRACK_LABELS);
}

export function getTrackWorkflowDefinition(track: string | null | undefined): TrackWorkflowDefinition {
  return TRACK_WORKFLOW_DEFINITIONS[normalizeWorkflowTrack(track)];
}

export function getStoredWorkflowTrack(): WorkflowTrack | null {
  if (typeof window === "undefined") return null;
  const value = window.localStorage.getItem(WORKFLOW_TRACK_STORAGE_KEY);
  return value && value in TRACK_LABELS ? (value as WorkflowTrack) : null;
}

export function setStoredWorkflowTrack(track: WorkflowTrack) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(WORKFLOW_TRACK_STORAGE_KEY, track);
  window.dispatchEvent(new CustomEvent("realytics-workflow-track-changed", { detail: { track } }));
}
