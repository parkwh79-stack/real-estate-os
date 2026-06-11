export const FINANCIAL_TERMS = {
  inputPackage: "사업자료·증빙 입력",
  orchestration: "AI 통합심사",
  approvalReadiness: "승인 가능성 점검",
  landEvidence: "토지권원·증빙",
  feasibility: "사업수지",
  cashFlow: "월별 현금흐름",
  pfStructure: "PF 금융구조",
  riskRegister: "리스크·승인조건",
  report: "PF 심사보고서",
  audit: "감사이력",
  snapshot: "심사 기준본",
  snapshotDiff: "심사 기준본 비교",
  similarCases: "과거 유사 심사",
  historicalOutcome: "사후성과",
  handoff: "에이전트 인계사항",
  creditDecision: "여신심사 의견",
  qualityGate: "자료 정합성 점검",
  evidenceGraph: "근거 연결도",
} as const;

export type FinancialTermKey = keyof typeof FINANCIAL_TERMS;

export function financeTerm(key: FinancialTermKey) {
  return FINANCIAL_TERMS[key];
}

const MACHINE_TERM_LABELS: Record<string, string> = {
  input_package: FINANCIAL_TERMS.inputPackage,
  orchestration: FINANCIAL_TERMS.orchestration,
  approval_readiness: FINANCIAL_TERMS.approvalReadiness,
  approval_readiness_gate: FINANCIAL_TERMS.approvalReadiness,
  land_evidence: FINANCIAL_TERMS.landEvidence,
  landAgentReview: FINANCIAL_TERMS.landEvidence,
  developerFeasibility: FINANCIAL_TERMS.feasibility,
  feasibility: FINANCIAL_TERMS.feasibility,
  cash_flow: FINANCIAL_TERMS.cashFlow,
  scenariosEvaluation: "시나리오·회수가능성",
  pfStructure: FINANCIAL_TERMS.pfStructure,
  riskRegister: FINANCIAL_TERMS.riskRegister,
  phase11Report: FINANCIAL_TERMS.report,
  evidenceGraph: FINANCIAL_TERMS.evidenceGraph,
  creditReviewDecision: FINANCIAL_TERMS.creditDecision,
  handoff: FINANCIAL_TERMS.handoff,
  dataQualityReview: FINANCIAL_TERMS.qualityGate,
  marketabilityAnalysis: "분양성 검토",
  marketEvidenceReview: "시장근거 검토",
  legalPermitReview: "법무·인허가 검토",
  qualityCheck: "보고서 완성도 점검",
  source_locator: "근거 위치",
  source_document: "원천문서",
  report_section: "보고서 섹션",
  agent_artifact: "에이전트 산출물",
  claim: "판단·계산값",
  risk: "리스크 항목",
};

export function translateFinancialTerm(value?: string | null) {
  if (!value) return "-";
  return MACHINE_TERM_LABELS[value] ?? MACHINE_TERM_LABELS[value.replaceAll("-", "_")] ?? value;
}

export function readinessLabel(pass?: boolean) {
  if (pass === true) return "승인 검토 가능";
  if (pass === false) return "보완 후 재심 필요";
  return "미실행";
}

export function reviewStatusLabel(status?: string | null) {
  const labels: Record<string, string> = {
    approved: "승인",
    waived: "유보 승인",
    resolved: "해소",
    needs_revision: "보완 요청",
    rejected: "반려",
    open: "미검토",
    pending: "대기",
    blocked: "차단",
  };
  return labels[status ?? ""] ?? status ?? "미검토";
}

export function severityLabel(severity?: string | null) {
  const labels: Record<string, string> = {
    critical: "중대",
    high: "높음",
    medium: "보통",
    low: "낮음",
    warning: "주의",
    info: "참고",
  };
  return labels[severity ?? ""] ?? severity ?? "-";
}
