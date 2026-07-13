"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowRight,
  BookOpenCheck,
  Building2,
  CheckCircle2,
  ClipboardCheck,
  Database,
  FileSearch,
  FileText,
  GitBranch,
  Landmark,
  Loader2,
  Network,
  Radio,
  RefreshCw,
  Scale,
  ShieldAlert,
  Sparkles,
  Workflow,
} from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import {
  getStoredWorkflowTrack,
  getTrackWorkflowDefinition,
  isWorkflowTrack,
  setStoredWorkflowTrack,
  TRACK_LABELS,
  type WorkflowTrack,
} from "../../lib/workflow-track";

type PodKey =
  | "land_legal"
  | "market_product"
  | "cost_schedule"
  | "feasibility_cashflow"
  | "pf_credit"
  | "public_policy";

type PodStatus = "planned" | "running" | "completed" | "blocked" | "failed" | "needs_recalculation";

interface MissingInputRegisterItem {
  itemId: string;
  category: string;
  description: string;
  requiredFor?: string[];
  severity: "info" | "medium" | "high" | "blocking";
  acceptableSources?: string[];
}

interface AgentPodPlan {
  podKey: PodKey;
  name: string;
  objective: string;
  agents: string[];
}

interface AgentPodRun {
  podKey: PodKey;
  status: PodStatus;
  progressPercent: number;
  artifactKeys?: string[];
  podAiReviewIds?: string[];
  handoffIds?: string[];
  recalculationEvents?: Array<Record<string, unknown>>;
}

interface PodAIReviewFinding {
  findingId?: string;
  category?: string;
  severity?: "info" | "medium" | "high" | "blocking";
  message?: string;
  evidenceIds?: string[];
  sourceLocators?: Array<Record<string, unknown>>;
  recommendedAction?: string;
}

interface PodAIReviewHandoff {
  handoffId?: string;
  fromAgent?: string;
  toAgent?: string;
  handoffType?: string;
  payload?: Record<string, unknown>;
  evidenceIds?: string[];
  confidence?: number;
}

interface PodAIReview {
  reviewId: string;
  podKey: PodKey;
  sourceArtifactKey?: string;
  agentId?: string;
  reviewMode?: "llm_evidence_aware_review" | "deterministic_fallback_review";
  reviewStatus?: "pass" | "warning" | "blocking" | "needs_human_review";
  confidence?: number;
  findings?: PodAIReviewFinding[];
  handoffs?: PodAIReviewHandoff[];
  reportFragments?: Record<string, string>;
  documentFacts?: Record<string, unknown>;
  aiBrainUsed?: Record<string, unknown>;
  createdAt?: string;
  updatedAt?: string;
}

interface DealStateValue {
  key: string;
  value?: unknown;
  valueType: "source" | "assumption" | "calculated" | "estimated";
  unit?: string;
  confidence?: number;
}

interface ScenarioConclusion {
  scenario: "base_case" | "downside_case" | "upside_case" | "kill_case";
  summary: string;
  keyMetrics?: Record<string, unknown>;
  riskFlags?: string[];
  decisionImplication: string;
}

interface RiskCommitteeOpinion {
  reviewerRole: string;
  opinion: string;
  decisionImpact: "supports_pass" | "supports_conditional_pass" | "supports_hold" | "supports_fail";
  keyConcerns?: string[];
  requiredActions?: string[];
  evidenceIds?: string[];
  confidence?: number;
}

interface AutoSupplementAttempt {
  itemId: string;
  status: "supplemented" | "needs_user_material" | "fetch_failed" | "blocked_by_dependency";
  source?: string;
  reason?: string;
  fetchedAt?: string;
  values?: Record<string, unknown>;
}

interface RecalculationEvent {
  eventId?: string;
  eventType?: string;
  trigger?: string;
  sourcePod?: string;
  targetPod?: string | null;
  targetPods?: string[];
  status?: string;
  createdAt?: string;
  timestamp?: string;
  recommendedAction?: string;
}

interface ChiefDealOrchestrationPlan {
  schemaVersion: string;
  projectId: string;
  autoSupplements?: AutoSupplementAttempt[];
  runAllSummary?: {
    schemaVersion: string;
    projectId: string;
    workflowTrack?: WorkflowTrack;
    requestedPodCount: number;
    completedCount: number;
    blockedCount: number;
    status: "completed" | "completed_with_blockers";
    rows: Array<{
      podKey: PodKey;
      status: PodStatus;
      startedAt?: string;
      completedAt?: string;
      artifactKeys?: string[];
      error?: string;
    }>;
  };
  analysisPurpose?: string;
  sharedDealStateGraph: {
    evidenceVault?: Array<Record<string, unknown>>;
    assumptionBook?: DealStateValue[];
    metrics?: DealStateValue[];
    risks?: Array<Record<string, unknown>>;
    conflicts?: Array<Record<string, unknown>>;
    missingInputRegister?: MissingInputRegisterItem[];
  };
  taskGraph?: {
    workflowTrack?: WorkflowTrack;
    executionStages?: Array<Record<string, unknown>>;
    recommendedRunOrder?: string[];
    edges?: Array<Record<string, unknown>>;
    recalculationRules?: Array<Record<string, unknown>>;
  };
  agentPods: AgentPodPlan[];
  podRuns: AgentPodRun[];
  scenarioConclusions?: ScenarioConclusion[];
  riskCommittee?: RiskCommitteeOpinion[];
  decisionGate?: {
    decision: "PASS" | "CONDITIONAL_PASS" | "HOLD" | "FAIL";
    rationale: string;
    conditionsPrecedent?: string[];
    conditionsSubsequent?: string[];
    requiredSupplements?: string[];
    mitigationActions?: string[];
    humanReviewRequired?: boolean;
  };
  approvalReadinessGate?: {
    pass?: boolean;
    status?: string;
    blockingIssueCount?: number;
    warningCount?: number;
    blockers?: Array<{ code?: string; message?: string }>;
    requiredActions?: string[];
  };
  reportStudioOutputs?: string[];
  learningLoopEvents?: Array<Record<string, unknown>>;
}

const POD_META: Record<
  PodKey,
  {
    label: string;
    subtitle: string;
    objective: string;
    icon: typeof Building2;
    color: string;
    agents: string[];
  }
> = {
  land_legal: {
    label: "Land-Legal Pod",
    subtitle: "토지·법규·권리·인허가",
    objective: "필지, PNU, 공시지가, 등기·제한물권, 인허가 가능성을 먼저 검증합니다.",
    icon: Scale,
    color: "border-emerald-300 bg-emerald-50 text-emerald-950 dark:border-emerald-800 dark:bg-emerald-950/25 dark:text-emerald-50",
    agents: ["Land & Parcel", "PNU Resolver", "Official Land Price", "Rights / Encumbrance", "Permit Feasibility"],
  },
  market_product: {
    label: "Market-Product Pod",
    subtitle: "시장·상품·가격·흡수율",
    objective: "실거래가, 분양사례, 임대료, 경쟁공급을 바탕으로 가격과 분양성을 검증합니다.",
    icon: FileSearch,
    color: "border-sky-300 bg-sky-50 text-sky-950 dark:border-sky-800 dark:bg-sky-950/25 dark:text-sky-50",
    agents: ["Market Comparable", "Demand / Absorption", "Product Concept", "Pricing", "Competition"],
  },
  cost_schedule: {
    label: "Cost-Schedule Pod",
    subtitle: "공사비·간접비·일정·시공",
    objective: "공사비, 간접비, 인허가·착공·준공 일정과 책임준공 리스크를 검토합니다.",
    icon: ClipboardCheck,
    color: "border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-800 dark:bg-amber-950/25 dark:text-amber-50",
    agents: ["Construction Cost", "Indirect Cost", "Schedule", "VE / Spec Optimization", "Contractor Risk"],
  },
  feasibility_cashflow: {
    label: "Feasibility-Cashflow Pod",
    subtitle: "사업수지·월별 CF·Max Minus",
    objective: "사업수지, 월별 Cash Flow, Max Minus, 민감도와 기준값 충돌을 계산합니다.",
    icon: Workflow,
    color: "border-indigo-300 bg-indigo-50 text-indigo-950 dark:border-indigo-800 dark:bg-indigo-950/25 dark:text-indigo-50",
    agents: ["Revenue Model", "Development Cost Model", "Proforma", "Monthly Cash Flow", "Max Minus / Sensitivity"],
  },
  pf_credit: {
    label: "PF-Credit Pod",
    subtitle: "PF 구조·Tranche·LTV·Covenant",
    objective: "선순위·중순위·후순위 회수 가능성, Waterfall, Exit 분양률, 승인조건을 검토합니다.",
    icon: Landmark,
    color: "border-violet-300 bg-violet-50 text-violet-950 dark:border-violet-800 dark:bg-violet-950/25 dark:text-violet-50",
    agents: ["PF Structuring", "Tranche", "LTV / LTC / DSCR", "Exit Sales Rate", "Covenant / Waterfall", "Lender Persona"],
  },
  public_policy: {
    label: "Public-Policy Pod",
    subtitle: "공공성·도시계획·기부채납·정책",
    objective: "공공기여, 도시계획, 정책 리스크와 공공성 검토가 필요한 경우 보조 분석합니다.",
    icon: BookOpenCheck,
    color: "border-slate-300 bg-slate-50 text-slate-950 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-50",
    agents: ["Public Feasibility", "Urban Planning", "Public Contribution", "Policy Simulation"],
  },
};

const STATUS_LABEL: Record<PodStatus, string> = {
  planned: "대기",
  running: "실행 중",
  completed: "완료",
  blocked: "보강 필요",
  failed: "실패",
  needs_recalculation: "재계산 필요",
};

const STATUS_TONE: Record<PodStatus, string> = {
  planned: "border-slate-200 bg-white text-slate-700 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200",
  running: "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-950/30 dark:text-sky-200",
  completed: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200",
  blocked: "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-200",
  failed: "border-red-200 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-200",
  needs_recalculation: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200",
};

const SCENARIO_LABEL: Record<string, string> = {
  base_case: "Base Case",
  downside_case: "Downside Case",
  upside_case: "Upside Case",
  kill_case: "Kill Case",
};

const SCENARIO_FALLBACK: Record<string, string> = {
  base_case: "현재 확보된 자료와 기준값으로 계산한 기본 시나리오입니다.",
  downside_case: "분양가 하락, 공사비 상승, 분양 지연을 반영한 보수 시나리오입니다.",
  upside_case: "가격 지지와 비용 안정이 확인될 때 가능한 개선 시나리오입니다.",
  kill_case: "토지·시장·금융 조건이 동시에 악화되는 중단 검토 시나리오입니다.",
};

const REVIEWER_LABEL: Record<string, string> = {
  devils_advocate: "Devil's Advocate",
  credit_reviewer: "Credit Reviewer",
  legal_compliance_reviewer: "Legal / Compliance",
  market_stress_tester: "Market Stress Tester",
  construction_risk_reviewer: "Construction Risk",
  lender_persona_reviewer: "Lender Persona",
  ic_judge: "IC Judge",
};

const IMPACT_LABEL: Record<string, string> = {
  supports_pass: "통과 지지",
  supports_conditional_pass: "조건부 통과",
  supports_hold: "보류 권고",
  supports_fail: "부결 권고",
};

const VALUE_TYPE_LABEL: Record<DealStateValue["valueType"], string> = {
  source: "원천값",
  assumption: "가정값",
  calculated: "계산값",
  estimated: "추정값",
};

const REPORT_LABEL: Record<string, string> = {
  land_review_report: "토지검토서",
  feasibility_report: "사업성 검토서",
  proforma: "사업수지표",
  monthly_cash_flow: "월별 Cash Flow",
  pf_review_report: "PF 심사보고서",
  ic_memo: "IC Memo",
  term_sheet: "Term Sheet",
  monitoring_sheet: "Monitoring Sheet",
};

const MISSING_LABEL: Record<string, { title: string; description: string }> = {
  source_documents_missing: {
    title: "원본 심사자료 부족",
    description: "사업계획서, 사업수지 Excel, 토지조서, Term Sheet 등 원자료가 충분하지 않습니다.",
  },
  land_evidence_missing: {
    title: "토지·PNU·공시지가·등기 증빙 부족",
    description: "PNU, 공시지가, 지적특성, 등기·제한물권 증빙이 필요합니다. 가능하면 VWorld/공공데이터로 먼저 자동 보강합니다.",
  },
  market_evidence_missing: {
    title: "시장·분양성·가격 근거 부족",
    description: "실거래가, 분양사례, 임대료, 미분양·청약 통계 또는 수동 비교사례가 필요합니다.",
  },
  pf_structure_missing: {
    title: "PF 구조 미연결",
    description: "Tranche, 금리, 수수료, 상환순위, Waterfall, 승인조건이 필요합니다.",
  },
};

const CONFLICT_LABEL: Record<string, string> = {
  MARKET_PRICE_BASIS_MISSING: "사업수지 매출은 있으나 제안 분양가 또는 비교사례 가격 기준이 부족합니다.",
  LAND_AREA_RECONCILIATION_GAP: "토지조서 면적과 프로젝트 대지면적이 일치하지 않습니다.",
  LAND_EVIDENCE_GATE_OPEN: "공시지가, 지적특성, 등기·제한물권 증빙이 부족해 PF 승인 Gate가 열려 있습니다.",
  PF_STRUCTURE_NOT_LINKED: "사업수지와 PF Tranche 구조가 아직 연결되지 않았습니다.",
};

const TRIGGER_LABEL: Record<string, string> = {
  legal_product_infeasible: "법규와 상품안 충돌",
  market_price_unsupported: "시장가격 근거 부족",
  max_minus_exceeds_pf_limit: "Max Minus가 PF 한도 초과",
  finance_cost_changed: "금융비용 변경",
};

function pickTrackFromClient(): WorkflowTrack | null {
  if (typeof window === "undefined") return null;
  const fromQuery = new URLSearchParams(window.location.search).get("track");
  if (isWorkflowTrack(fromQuery)) {
    setStoredWorkflowTrack(fromQuery);
    return fromQuery;
  }
  return getStoredWorkflowTrack();
}

function hasBrokenText(value: string) {
  const mojibakeMarkers = /[�ìíëêãâäåðñ¼½¾±°]/.test(value);
  const questionRun = (value.match(/\?/g) ?? []).length >= 3;
  return mojibakeMarkers || questionRun;
}

function cleanText(value: unknown, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  const text = typeof value === "string" ? value : asText(value);
  return hasBrokenText(text) ? fallback : text;
}

function severityTone(severity?: string) {
  if (severity === "blocking") return "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-200";
  if (severity === "high") return "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200";
  return "border-slate-200 bg-white text-slate-700 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200";
}

function decisionTone(decision?: string) {
  if (decision === "PASS") return "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-100";
  if (decision === "CONDITIONAL_PASS") return "border-sky-300 bg-sky-50 text-sky-900 dark:border-sky-800 dark:bg-sky-950/30 dark:text-sky-100";
  if (decision === "FAIL") return "border-rose-300 bg-rose-50 text-rose-900 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-100";
  return "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100";
}

function impactTone(impact?: string) {
  if (impact === "supports_fail") return "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-200";
  if (impact === "supports_hold") return "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200";
  if (impact === "supports_pass") return "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200";
  return "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-950/30 dark:text-sky-200";
}

function compactKey(key: string) {
  return key
    .replace("feasibility.", "사업수지.")
    .replace("expected.", "예상.")
    .replace("pf.", "PF.")
    .replace("market.", "시장.")
    .replace("land.", "토지.");
}

function safeNumber(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value !== "number") return String(value);
  if (!Number.isFinite(value)) return "-";
  if (Math.abs(value) >= 100_000_000) return `${(value / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}억원`;
  if (value > 0 && value < 1) return `${(value * 100).toFixed(1)}%`;
  return value.toLocaleString("ko-KR", { maximumFractionDigits: 2 });
}

function asText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string") return value;
  if (typeof value === "number") return safeNumber(value);
  if (Array.isArray(value)) return value.map(asText).join(", ");
  return JSON.stringify(value);
}

function asRecordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asCount(value: unknown) {
  if (value === null || value === undefined || value === "") return "0";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("ko-KR") : cleanText(value, "0");
}

function hasTrigger(podKey: PodKey) {
  return podKey === "land_legal" || podKey === "market_product" || podKey === "feasibility_cashflow";
}

function getTrigger(podKey: PodKey) {
  if (podKey === "land_legal") return "legal_product_infeasible";
  if (podKey === "market_product") return "market_price_unsupported";
  if (podKey === "feasibility_cashflow") return "max_minus_exceeds_pf_limit";
  return undefined;
}

function normalizeReportName(name: string) {
  return REPORT_LABEL[name] ?? name;
}

function isPodKey(value: unknown): value is PodKey {
  return typeof value === "string" && value in POD_META;
}

function eventTargetPods(event: RecalculationEvent): PodKey[] {
  const rawTargets = Array.isArray(event.targetPods)
    ? event.targetPods
    : typeof event.targetPod === "string"
      ? event.targetPod.split(",")
      : [];
  return rawTargets.map((item) => item.trim()).filter(isPodKey);
}

export function ChiefDealRoomMap({ projectId }: { projectId: string }) {
  const [plan, setPlan] = useState<ChiefDealOrchestrationPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [track, setTrack] = useState<WorkflowTrack | null>(null);
  const [executingPodKey, setExecutingPodKey] = useState<PodKey | null>(null);
  const [executingAll, setExecutingAll] = useState(false);
  const [executionProgress, setExecutionProgress] = useState(0);
  const [supplements, setSupplements] = useState<AutoSupplementAttempt[] | null>(null);
  const [recalculationEvents, setRecalculationEvents] = useState<RecalculationEvent[]>([]);
  const [podAiReviews, setPodAiReviews] = useState<PodAIReview[]>([]);
  const [approvalGate, setApprovalGate] = useState<ChiefDealOrchestrationPlan["approvalReadinessGate"] | null>(null);
  const [cureType, setCureType] = useState("additional_collateral");
  const [cureAmount, setCureAmount] = useState("");
  const [cureEvidenceId, setCureEvidenceId] = useState("");
  const [cureEvidenceNote, setCureEvidenceNote] = useState("");
  const [cureSubmitting, setCureSubmitting] = useState(false);

  const loadRecalculationEvents = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/chief-orchestration/recalculation-events?limit=50`, {
        cache: "no-store",
        headers: authHeaders("web"),
      });
      if (!res.ok) return;
      const body = await res.json().catch(() => ({}));
      const records = Array.isArray(body.records) ? body.records : [];
      setRecalculationEvents(records as RecalculationEvent[]);
    } catch {
      setRecalculationEvents([]);
    }
  }, [projectId]);

  const loadPodAiReviews = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/chief-orchestration/pod-ai-reviews?limit=100`, {
        cache: "no-store",
        headers: authHeaders("web"),
      });
      if (!res.ok) return;
      const body = await res.json().catch(() => ({}));
      const records = Array.isArray(body.records) ? body.records : [];
      setPodAiReviews(records as PodAIReview[]);
    } catch {
      setPodAiReviews([]);
    }
  }, [projectId]);

  const loadPlan = useCallback(async () => {
    setLoading(true);
    setError(null);
    const selectedTrack = pickTrackFromClient();
    setTrack(selectedTrack);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/chief-orchestration/plan`, {
        method: "POST",
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders("web"),
        },
        body: JSON.stringify({
          analysisPurpose: "chief_deal_room",
          workflowTrack: selectedTrack,
        }),
      });
      if (!res.ok) throw new Error(`Chief Deal Room 계획을 불러오지 못했습니다. (${res.status})`);
      const nextPlan = (await res.json()) as ChiefDealOrchestrationPlan;
      setPlan(nextPlan);
      setApprovalGate(nextPlan.approvalReadinessGate ?? null);
      await Promise.all([loadRecalculationEvents(), loadPodAiReviews()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chief Deal Room 계획을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId, loadRecalculationEvents, loadPodAiReviews]);

  const applyCureAndRerun = useCallback(async () => {
    const amount = Number(cureAmount.replaceAll(",", ""));
    if (!Number.isFinite(amount) || amount <= 0 || !cureEvidenceId.trim()) {
      setError("Cure 금액과 증빙 ID 또는 출처 URL을 입력해 주세요.");
      return;
    }
    setCureSubmitting(true);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/chief-orchestration/unsold-ltv-cure`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders("web") },
        body: JSON.stringify({
          cureType,
          amount,
          evidence: {
            evidenceId: cureEvidenceId.trim(),
            sourceType: "manual_upload_or_external_evidence",
            note: cureEvidenceNote.trim(),
          },
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(cleanText(body?.detail, "Cure 재계산에 실패했습니다."));
      if (body.run?.artifacts?.approvalReadinessGate) {
        setApprovalGate(body.run.artifacts.approvalReadinessGate);
      } else if (body.approvalReadinessGate) {
        setApprovalGate(body.approvalReadinessGate);
      }
      setCureAmount("");
      setCureEvidenceId("");
      setCureEvidenceNote("");
      setError(null);
      await Promise.all([loadRecalculationEvents(), loadPodAiReviews()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cure 재계산 중 오류가 발생했습니다.");
    } finally {
      setCureSubmitting(false);
    }
  }, [cureAmount, cureEvidenceId, cureEvidenceNote, cureType, loadPodAiReviews, loadRecalculationEvents, projectId]);

  const runPod = useCallback(async (podKey: PodKey, forceTrigger?: string): Promise<boolean> => {
    setExecutingPodKey(podKey);
    setExecutionProgress(5);
    const interval = window.setInterval(() => {
      setExecutionProgress((prev) => (prev >= 90 ? 90 : prev + 15));
    }, 180);

    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/chief-orchestration/run-pod`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders("web"),
        },
        body: JSON.stringify({
          podKey,
          workflowTrack: track ?? getStoredWorkflowTrack(),
          options: forceTrigger ? { forceTrigger } : {},
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(cleanText(body?.detail, "Pod 실행에 실패했습니다."));
      setExecutionProgress(100);
      await new Promise((resolve) => window.setTimeout(resolve, 250));
      const nextPlan = body as ChiefDealOrchestrationPlan;
      setPlan(nextPlan);
      setSupplements(nextPlan.autoSupplements?.length ? nextPlan.autoSupplements : null);
      await Promise.all([loadRecalculationEvents(), loadPodAiReviews()]);
      setError(null);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Pod 실행 중 오류가 발생했습니다.");
      return false;
    } finally {
      window.clearInterval(interval);
      setExecutingPodKey(null);
      setExecutionProgress(0);
    }
  }, [projectId, track, loadRecalculationEvents, loadPodAiReviews]);

  const runAllPods = useCallback(async () => {
    setExecutingAll(true);
    setExecutionProgress(8);
    const interval = window.setInterval(() => {
      setExecutionProgress((prev) => (prev >= 92 ? 92 : prev + 8));
    }, 260);

    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/chief-orchestration/run-all-pods`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders("web"),
        },
        body: JSON.stringify({
          workflowTrack: track ?? getStoredWorkflowTrack(),
          options: {},
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(cleanText(body?.detail, "전체 Pod 실행에 실패했습니다."));
      setExecutionProgress(100);
      await new Promise((resolve) => window.setTimeout(resolve, 250));
      const nextPlan = body as ChiefDealOrchestrationPlan;
      setPlan(nextPlan);
      setSupplements(nextPlan.autoSupplements?.length ? nextPlan.autoSupplements : null);
      await Promise.all([loadRecalculationEvents(), loadPodAiReviews()]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "전체 Pod 실행 중 오류가 발생했습니다.");
    } finally {
      window.clearInterval(interval);
      setExecutingAll(false);
      setExecutionProgress(0);
    }
  }, [projectId, track, loadRecalculationEvents, loadPodAiReviews]);

  useEffect(() => {
    void loadPlan();
  }, [loadPlan]);

  const runsByPod = useMemo(() => {
    const map = new Map<PodKey, AgentPodRun>();
    for (const run of plan?.podRuns ?? []) {
      map.set(run.podKey, run);
    }
    return map;
  }, [plan]);

  const podAiReviewsById = useMemo(() => {
    const map = new Map<string, PodAIReview>();
    for (const review of podAiReviews) {
      map.set(review.reviewId, review);
    }
    return map;
  }, [podAiReviews]);

  const latestReviewByPod = useMemo(() => {
    const map = new Map<PodKey, PodAIReview>();
    for (const review of podAiReviews) {
      const existing = map.get(review.podKey);
      const reviewTime = String(review.updatedAt ?? review.createdAt ?? "");
      const existingTime = String(existing?.updatedAt ?? existing?.createdAt ?? "");
      if (!existing || reviewTime >= existingTime) {
        map.set(review.podKey, review);
      }
    }
    return map;
  }, [podAiReviews]);

  const reviewForPod = useCallback((podKey: PodKey, run?: AgentPodRun) => {
    for (const reviewId of run?.podAiReviewIds ?? []) {
      const review = podAiReviewsById.get(reviewId);
      if (review) return review;
    }
    return latestReviewByPod.get(podKey);
  }, [latestReviewByPod, podAiReviewsById]);

  const currentTrack = track ?? plan?.taskGraph?.workflowTrack ?? "development";
  const workflow = getTrackWorkflowDefinition(currentTrack);
  const missing = plan?.sharedDealStateGraph.missingInputRegister ?? [];
  const conflicts = plan?.sharedDealStateGraph.conflicts ?? [];
  const blockingCount = missing.filter((item) => item.severity === "blocking").length + conflicts.filter((item) => item.severity === "blocking").length;
  const highCount = missing.filter((item) => item.severity === "high").length + conflicts.filter((item) => item.severity === "high").length;
  const evidenceCount = plan?.sharedDealStateGraph.evidenceVault?.length ?? 0;
  const assumptionCount = plan?.sharedDealStateGraph.assumptionBook?.length ?? 0;
  const metricCount = plan?.sharedDealStateGraph.metrics?.length ?? 0;
  const eventCount = recalculationEvents.length || (plan?.learningLoopEvents?.length ?? 0);
  // 분석 전(Pod 미실행) vs 분석 후 구분: Pod가 하나라도 실행됐는지로 판단.
  const analyzed =
    (plan?.podRuns ?? []).some((run) => run.status === "completed" || run.status === "running") ||
    Boolean(plan?.runAllSummary);
  // 파싱 후보값 검증 플래그(PARSED_FACT_*)는 "딜 충돌"이 아니라 "자료 확정 대기"다.
  const parsingConfirmations = conflicts.filter((item) =>
    String((item as Record<string, unknown>).code ?? "").startsWith("PARSED_FACT_"),
  ).length;

  if (loading) {
    return (
      <section className="rounded-lg border border-slate-200 bg-white p-8 text-center shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <Loader2 className="mx-auto h-8 w-8 animate-spin text-sky-600" />
        <p className="mt-3 text-sm font-bold text-slate-700 dark:text-slate-200">Chief Deal Orchestrator가 프로젝트 기억을 읽고 있습니다.</p>
      </section>
    );
  }

  if (!plan) {
    return (
      <section className="rounded-lg border border-rose-200 bg-rose-50 p-6 text-rose-900 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-100">
        <div className="flex items-center gap-2 font-black">
          <AlertTriangle className="h-5 w-5" />
          AI 딜룸 계획 생성 실패
        </div>
        <p className="mt-2 text-sm">{error}</p>
        <button
          type="button"
          onClick={() => void loadPlan()}
          className="mt-4 inline-flex h-9 items-center gap-2 rounded-lg bg-rose-700 px-3 text-sm font-bold text-white hover:bg-rose-800"
        >
          <RefreshCw className="h-4 w-4" />
          다시 생성
        </button>
      </section>
    );
  }

  return (
    <div className="space-y-5">
      {error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-bold text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-100">
          {error}
        </div>
      ) : null}

      {supplements ? <AutoSupplementBanner supplements={supplements} /> : null}

      {!analyzed ? (
        <PreAnalysisNotice parsingConfirmations={parsingConfirmations} missingCount={missing.length} />
      ) : null}

      <ApprovalReadinessPanel
        gate={approvalGate}
        cureType={cureType}
        cureAmount={cureAmount}
        cureEvidenceId={cureEvidenceId}
        cureEvidenceNote={cureEvidenceNote}
        submitting={cureSubmitting}
        onCureTypeChange={setCureType}
        onAmountChange={setCureAmount}
        onEvidenceIdChange={setCureEvidenceId}
        onEvidenceNoteChange={setCureEvidenceNote}
        onSubmit={() => void applyCureAndRerun()}
      />

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <p className="text-xs font-black uppercase tracking-[0.16em] text-sky-600 dark:text-sky-300">REALYTICS Chief Deal Room</p>
            <h1 className="mt-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
              트랙별 Task Graph를 만들고 Agent Pod가 같은 Deal State를 검증합니다
            </h1>
            <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-600 dark:text-slate-300">
              선택 트랙은 <strong>{TRACK_LABELS[currentTrack]}</strong>입니다. 오케스트레이터는 바로 PF 심사 결론으로 가지 않고,
              자료 접수 → 자동 보강 → Pod 실행 → 교차 검증 → Risk Committee → Human Review 순서로 판단합니다.
            </p>
          </div>
          <div className={`rounded-lg border px-4 py-3 text-sm font-bold ${decisionTone(plan.decisionGate?.decision)}`}>
            <p className="text-xs uppercase tracking-wide opacity-70">Decision Gate{analyzed ? "" : " · 분석 전 잠정"}</p>
            <p className="mt-1 text-xl">{plan.decisionGate?.decision ?? "HOLD"}</p>
            <p className="mt-1 max-w-xs text-xs leading-5 opacity-80">
              {analyzed
                ? cleanText(plan.decisionGate?.rationale, "아직 승인 가능성을 확정하기 전입니다. Pod 실행 결과와 blocker를 먼저 확인해야 합니다.")
                : "Pod를 아직 실행하지 않아 자동으로 보류(HOLD) 상태입니다. ‘전체 자동 실행’ 후 실제 판단이 생성됩니다."}
            </p>
          </div>
        </div>

        <div className="mt-5 grid gap-3 md:grid-cols-5">
          <Kpi label="선택 트랙" value={TRACK_LABELS[currentTrack]} />
          <Kpi label={analyzed ? "Blocking 이슈" : "필요 선행조건"} value={`${blockingCount}건`} tone={blockingCount ? (analyzed ? "bad" : "warn") : "good"} />
          <Kpi label={analyzed ? "중요 보강/충돌" : "자료 확정 대기"} value={`${highCount}건`} tone={highCount ? "warn" : "good"} />
          <Kpi label="Deal State 값" value={`${evidenceCount + assumptionCount + metricCount}개`} />
          <Kpi label="검토 대기" value={`${eventCount}건`} tone={eventCount ? "warn" : "neutral"} />
        </div>
      </section>

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950">
        <div className="grid gap-4 xl:grid-cols-[1fr_1.4fr_1fr]">
          <FlowRail title="Deal Intake Router" icon={<Sparkles className="h-5 w-5" />}>
            <p className="text-sm leading-6 text-slate-600 dark:text-slate-300">
              업로드 파일, 주소/PNU, API, 내부 DB를 읽어 프로젝트 유형, 사업단계, 분석목적, 필요한 산출물을 식별합니다.
            </p>
            <TrackWorkflowPanel workflow={workflow} />
            <TaskGraphPanel taskGraph={plan.taskGraph} />
          </FlowRail>

          <div className="space-y-4">
            <FlowRail title="Task Planner & Event Bus" icon={<GitBranch className="h-5 w-5" />}>
              <div className="mb-4 flex flex-col gap-3 rounded-lg border border-sky-200 bg-sky-50 px-3 py-3 dark:border-sky-900 dark:bg-sky-950/30 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-sm font-black text-sky-950 dark:text-sky-100">오케스트레이터 전체 자동 실행</p>
                  <p className="mt-1 text-xs font-semibold leading-5 text-sky-800 dark:text-sky-200">
                    트랙별 Task Graph 순서대로 Pod를 실행합니다. 막히는 Pod는 VWorld, 국토부 실거래가 등 합법적 소스에서 먼저 자동 보강을 시도하고, 그래도 부족한 항목만 blocker로 남깁니다.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void runAllPods()}
                  disabled={executingAll || executingPodKey !== null}
                  className="inline-flex h-9 shrink-0 items-center justify-center gap-2 rounded-lg bg-sky-700 px-3 text-sm font-black text-white transition-colors hover:bg-sky-800 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {executingAll ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                  {executingAll ? `전체 실행 중 ${executionProgress}%` : "전체 자동 실행"}
                </button>
              </div>
              {plan.runAllSummary ? <RunAllSummaryPanel summary={plan.runAllSummary} /> : null}
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {plan.agentPods.map((pod) => {
                  const meta = POD_META[pod.podKey] ?? POD_META.land_legal;
                  const run = runsByPod.get(pod.podKey);
                  const podAiReview = reviewForPod(pod.podKey, run);
                  return (
                    <PodCard
                      key={pod.podKey}
                      pod={pod}
                      run={run}
                      podAiReview={podAiReview}
                      meta={meta}
                      onRun={runPod}
                      executingPodKey={executingPodKey}
                      executionProgress={executionProgress}
                    />
                  );
                })}
              </div>
              {eventCount > 0 ? (
                <EventBusQueuePanel
                  events={recalculationEvents.length ? recalculationEvents : ((plan.learningLoopEvents ?? []) as RecalculationEvent[])}
                  onRunPod={runPod}
                  executingPodKey={executingPodKey}
                />
              ) : null}
            </FlowRail>

            <div className="grid gap-4 lg:grid-cols-2">
              <FlowRail title="Shared Deal State Graph" icon={<Database className="h-5 w-5" />}>
                <StateGraphSummary evidenceCount={evidenceCount} assumptionCount={assumptionCount} metrics={plan.sharedDealStateGraph.metrics ?? []} />
              </FlowRail>

              <FlowRail title="Cross-Agent Reconciliation" icon={<Network className="h-5 w-5" />}>
                <ReconciliationPanel conflicts={conflicts} />
              </FlowRail>
            </div>
          </div>

          <div className="space-y-4">
            <FlowRail title="AI Risk Committee" icon={<ShieldAlert className="h-5 w-5" />}>
              <RiskCommitteePanel opinions={plan.riskCommittee ?? []} />
            </FlowRail>

            <FlowRail title="Report Studio + Human Review" icon={<FileText className="h-5 w-5" />}>
              <div className="flex flex-wrap gap-2">
                {(plan.reportStudioOutputs ?? []).map((output) => (
                  <span key={output} className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-bold text-slate-700 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200">
                    {normalizeReportName(output)}
                  </span>
                ))}
              </div>
              <HumanLearningPanel events={plan.learningLoopEvents ?? []} />
            </FlowRail>
          </div>
        </div>
      </section>

      <div className="grid gap-5 xl:grid-cols-[1.1fr_0.9fr]">
        <MissingInputPanel projectId={projectId} missing={missing} />
        <ScenarioPanel plan={plan} />
      </div>

      <div className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:flex-row sm:items-center sm:justify-between dark:border-slate-800 dark:bg-slate-900">
        <div>
          <p className="text-sm font-black text-slate-950 dark:text-white">다음 단계</p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            전체 자동 실행 후 남은 blocker를 확인하고, 공시지가·등기·시장가격 근거처럼 자동 확보되지 않은 자료만 보강합니다.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href={`/project/${projectId}/input-package`} className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 px-3 text-sm font-bold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800">
            자료 보강 단계
            <ArrowRight className="h-4 w-4" />
          </Link>
          <Link href={`/project/${projectId}/orchestration`} className="inline-flex h-9 items-center gap-2 rounded-lg bg-slate-950 px-3 text-sm font-bold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950">
            기존 실행 콘솔
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </div>
    </div>
  );
}

function ApprovalReadinessPanel({
  gate,
  cureType,
  cureAmount,
  cureEvidenceId,
  cureEvidenceNote,
  submitting,
  onCureTypeChange,
  onAmountChange,
  onEvidenceIdChange,
  onEvidenceNoteChange,
  onSubmit,
}: {
  gate: ChiefDealOrchestrationPlan["approvalReadinessGate"] | null;
  cureType: string;
  cureAmount: string;
  cureEvidenceId: string;
  cureEvidenceNote: string;
  submitting: boolean;
  onCureTypeChange: (value: string) => void;
  onAmountChange: (value: string) => void;
  onEvidenceIdChange: (value: string) => void;
  onEvidenceNoteChange: (value: string) => void;
  onSubmit: () => void;
}) {
  const blockers = gate?.blockers ?? [];
  return (
    <section className="rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/20">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-xs font-black uppercase tracking-wide text-amber-700 dark:text-amber-300">Approval Readiness Gate v2</p>
          <h2 className="mt-1 text-xl font-black text-slate-950 dark:text-white">
            {gate?.pass ? "승인 가능성 검토 통과" : "승인 전 보완이 필요합니다"}
          </h2>
          <p className="mt-1 text-xs leading-5 text-amber-900 dark:text-amber-100">
            차단 리스크 {gate?.blockingIssueCount ?? blockers.length}건 · 경고 {gate?.warningCount ?? 0}건 · cure 선택 후 증빙을 연결하면 PF 재계산과 Gate 재평가가 실행됩니다.
          </p>
        </div>
        <span className="rounded-full border border-amber-300 bg-white px-3 py-1 text-xs font-black text-amber-800 dark:border-amber-700 dark:bg-slate-950 dark:text-amber-200">
          {gate?.status ?? "not_ready"}
        </span>
      </div>
      {blockers.length ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {blockers.slice(0, 5).map((item, index) => (
            <span key={`${item.code ?? "blocker"}-${index}`} className="rounded border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] font-bold text-rose-800 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-200">
              {item.code ?? "승인 차단"}
            </span>
          ))}
        </div>
      ) : null}
      <div className="mt-4 grid gap-2 md:grid-cols-[1.1fr_0.8fr_1.2fr_1.4fr_auto]">
        <select value={cureType} onChange={(event) => onCureTypeChange(event.target.value)} className="h-10 rounded border border-amber-200 bg-white px-2 text-sm font-semibold text-slate-800 dark:border-amber-800 dark:bg-slate-950 dark:text-slate-100">
          <option value="additional_collateral">추가 담보</option>
          <option value="pf_reduction">선순위/PF 감액</option>
          <option value="equity_dsra_injection">Equity·DSRA 증액</option>
          <option value="bulk_sale">Bulk Sale·선매입</option>
          <option value="refinancing">준공 후 Refinancing</option>
          <option value="cash_sweep_strengthening">상환계좌 배분 강화</option>
        </select>
        <input value={cureAmount} onChange={(event) => onAmountChange(event.target.value)} inputMode="numeric" placeholder="금액(원)" className="h-10 rounded border border-amber-200 bg-white px-3 text-sm font-semibold dark:border-amber-800 dark:bg-slate-950 dark:text-white" />
        <input value={cureEvidenceId} onChange={(event) => onEvidenceIdChange(event.target.value)} placeholder="증빙 ID 또는 출처 URL" className="h-10 rounded border border-amber-200 bg-white px-3 text-sm font-semibold dark:border-amber-800 dark:bg-slate-950 dark:text-white" />
        <input value={cureEvidenceNote} onChange={(event) => onEvidenceNoteChange(event.target.value)} placeholder="증빙 설명·페이지·셀 위치" className="h-10 rounded border border-amber-200 bg-white px-3 text-sm font-semibold dark:border-amber-800 dark:bg-slate-950 dark:text-white" />
        <button type="button" onClick={onSubmit} disabled={submitting} className="inline-flex h-10 items-center justify-center gap-2 rounded bg-slate-950 px-3 text-sm font-black text-white disabled:opacity-50 dark:bg-white dark:text-slate-950">
          {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          {submitting ? "재계산 중" : "증빙 저장·재계산"}
        </button>
      </div>
      <p className="mt-2 text-[11px] font-semibold text-amber-800 dark:text-amber-200">현재 증빙 첨부는 원본 파일 자체가 아니라 Evidence ID/출처 locator를 연결합니다. 원본 파일은 자료 업로드 화면에서 먼저 보관해야 합니다.</p>
    </section>
  );
}

function AutoSupplementBanner({ supplements }: { supplements: AutoSupplementAttempt[] }) {
  const supplemented = supplements.filter((item) => item.status === "supplemented");
  const unresolved = supplements.filter((item) => item.status !== "supplemented");
  return (
    <div className="rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 dark:border-sky-900 dark:bg-sky-950/30">
      <p className="text-sm font-black text-sky-900 dark:text-sky-100">
        오케스트레이터가 부족한 자료를 자동 보강했습니다
      </p>
      <div className="mt-2 grid gap-2 lg:grid-cols-2">
        <div className="rounded-md bg-white/70 px-3 py-2 dark:bg-slate-950/50">
          <p className="text-xs font-black text-sky-900 dark:text-sky-100">확보된 항목</p>
          <ul className="mt-1 space-y-1 text-xs font-semibold text-sky-800 dark:text-sky-200">
            {supplemented.length ? supplemented.map((item) => (
              <li key={`${item.itemId}-${item.source}`} className="flex flex-wrap items-center gap-1.5">
                <span className="rounded bg-sky-600 px-1.5 py-0.5 text-[10px] font-black text-white">확보</span>
                <span>{item.source ?? item.itemId}</span>
                {item.values?.pnu ? <code className="rounded bg-white/70 px-1 font-mono text-[10px] dark:bg-slate-900/60">PNU {String(item.values.pnu)}</code> : null}
                {item.values?.officialLandPricePerSqm ? <span>공시지가 {Number(item.values.officialLandPricePerSqm).toLocaleString("ko-KR")}원/㎡</span> : null}
                {item.values?.transactionComparableCount ? <span>실거래가 {String(item.values.transactionComparableCount)}건</span> : null}
              </li>
            )) : <li>이번 실행에서 자동 확보된 신규 항목은 없습니다.</li>}
          </ul>
        </div>
        <div className="rounded-md bg-white/70 px-3 py-2 dark:bg-slate-950/50">
          <p className="text-xs font-black text-sky-900 dark:text-sky-100">남은 항목</p>
          <ul className="mt-1 space-y-1 text-xs font-semibold text-sky-800 dark:text-sky-200">
            {unresolved.length ? unresolved.map((item) => (
              <li key={`${item.itemId}-${item.status}`}>
                <span className="rounded bg-slate-400 px-1.5 py-0.5 text-[10px] font-black text-white">미확보</span>{" "}
                {cleanText(item.reason, MISSING_LABEL[item.itemId]?.title ?? item.itemId)}
              </li>
            )) : <li>자동 보강 이후 남은 미확보 항목이 없습니다.</li>}
          </ul>
        </div>
      </div>
    </div>
  );
}

function Kpi({ label, value, tone = "neutral" }: { label: string; value: string; tone?: "neutral" | "good" | "warn" | "bad" }) {
  const color = {
    neutral: "text-slate-950 dark:text-white",
    good: "text-emerald-700 dark:text-emerald-300",
    warn: "text-amber-700 dark:text-amber-300",
    bad: "text-rose-700 dark:text-rose-300",
  }[tone];
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-700 dark:bg-slate-950">
      <p className="text-xs font-bold text-slate-500">{label}</p>
      <p className={`mt-1 text-lg font-black ${color}`}>{value}</p>
    </div>
  );
}

function PreAnalysisNotice({ parsingConfirmations, missingCount }: { parsingConfirmations: number; missingCount: number }) {
  return (
    <section className="rounded-lg border border-sky-200 bg-sky-50 p-4 dark:border-sky-900 dark:bg-sky-950/30">
      <div className="flex items-start gap-2.5">
        <Sparkles className="mt-0.5 h-5 w-5 shrink-0 text-sky-600 dark:text-sky-300" />
        <div>
          <p className="text-sm font-black text-sky-950 dark:text-sky-100">아직 분석 전 단계입니다 — Pod를 실행하세요</p>
          <p className="mt-1 text-xs font-semibold leading-5 text-sky-800 dark:text-sky-200">
            업로드한 자료는 접수됐지만 아직 Agent Pod를 실행하지 않았습니다. 아래 <strong>전체 자동 실행</strong>을 누르면
            파싱된 후보값이 검증·확정되고 실제 심사 판단(계산·리스크·승인 게이트)이 생성됩니다.
          </p>
          <p className="mt-1.5 text-xs leading-5 text-sky-700/90 dark:text-sky-300/80">
            지금 보이는 {parsingConfirmations > 0 ? <><strong>자료 확정 대기 {parsingConfirmations}건</strong>과 </> : null}
            보강 필요 {missingCount}건 · Decision Gate HOLD · 위원회 보류는{" "}
            <strong>딜의 결함이 아니라 “아직 분석·확정 전”</strong>이라는 신호입니다. (예: 문서에서 값이 여러 번 다르게 나오거나, 값의 정확한 페이지·셀 위치가 아직 안 잡힌 상태)
          </p>
        </div>
      </div>
    </section>
  );
}

function FlowRail({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-950 text-white dark:bg-white dark:text-slate-950">
          {icon}
        </div>
        <h2 className="text-base font-black text-slate-950 dark:text-white">{title}</h2>
      </div>
      <div className="my-3 h-px bg-slate-200 dark:bg-slate-800" />
      {children}
    </div>
  );
}

function TrackWorkflowPanel({ workflow }: { workflow: ReturnType<typeof getTrackWorkflowDefinition> }) {
  return (
    <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-3 text-xs dark:border-slate-700 dark:bg-slate-950">
      <p className="font-black text-slate-800 dark:text-slate-100">{workflow.label} 워크플로우</p>
      <p className="mt-1 leading-5 text-slate-500 dark:text-slate-400">{workflow.decisionQuestion}</p>
      <ol className="mt-2 space-y-1">
        {workflow.steps.slice(0, 4).map((step, index) => (
          <li key={step.key} className="flex gap-2 text-slate-600 dark:text-slate-300">
            <span className="font-mono text-sky-600">{index + 1}</span>
            <span>{step.title}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function TaskGraphPanel({ taskGraph }: { taskGraph?: ChiefDealOrchestrationPlan["taskGraph"] }) {
  const stages = taskGraph?.executionStages ?? [];
  const runOrder = taskGraph?.recommendedRunOrder ?? [];
  return (
    <div className="mt-4 space-y-3">
      <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-3 text-xs dark:border-slate-700 dark:bg-slate-950">
        <p className="font-black text-slate-800 dark:text-slate-100">트랙별 Task Graph</p>
        <p className="mt-1 text-slate-500 dark:text-slate-400">
          {stages.length ? `${stages.length}개 실행 단계, ${runOrder.length}개 권장 실행 Pod` : "아직 실행 단계가 생성되지 않았습니다."}
        </p>
      </div>
      <div className="space-y-2">
        {stages.slice(0, 5).map((stage, index) => (
          <div key={`${asText(stage.stageId ?? stage.name ?? index)}`} className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs dark:border-slate-700 dark:bg-slate-900">
            <p className="font-black text-slate-900 dark:text-white">{cleanText(stage.name ?? stage.stageName, `Stage ${index + 1}`)}</p>
            <p className="mt-1 text-slate-500 dark:text-slate-400">{cleanText(stage.objective ?? stage.description ?? stage.podKey, "Pod 실행 준비")}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function PodCard({
  pod,
  run,
  podAiReview,
  meta,
  onRun,
  executingPodKey,
  executionProgress,
}: {
  pod: AgentPodPlan;
  run?: AgentPodRun;
  podAiReview?: PodAIReview;
  meta: (typeof POD_META)[PodKey];
  onRun: (podKey: PodKey, forceTrigger?: string) => Promise<boolean | void>;
  executingPodKey: PodKey | null;
  executionProgress: number;
}) {
  const Icon = meta.icon;
  const status = run?.status ?? "planned";
  const agents = pod.agents?.length ? pod.agents : meta.agents;
  const isExecuting = executingPodKey === pod.podKey;
  const blocked = status === "blocked";
  return (
    <article className={`flex flex-col justify-between rounded-lg border p-3 ${meta.color}`}>
      <div>
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-white/70 dark:bg-slate-950/50">
              <Icon className="h-4 w-4" />
            </div>
            <div>
              <p className="text-sm font-black">{meta.label}</p>
              <p className="text-[11px] font-semibold opacity-75">{meta.subtitle}</p>
            </div>
          </div>
          <span className={`rounded-full border px-2 py-1 text-[11px] font-black ${STATUS_TONE[status]}`}>
            {STATUS_LABEL[status]}
          </span>
        </div>
        <p className="mt-3 min-h-10 text-xs leading-5 opacity-80">{cleanText(pod.objective, meta.objective)}</p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {agents.slice(0, 6).map((agent, agentIndex) => {
            const shownAgents = Math.min(agents.length, 6);
            const activeIndex = isExecuting
              ? Math.min(shownAgents - 1, Math.floor((executionProgress / 100) * shownAgents))
              : -1;
            const isWorking = isExecuting && agentIndex === activeIndex;
            const isDone = (isExecuting && agentIndex < activeIndex) || status === "completed";
            return (
              <span
                key={agent}
                className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-[10px] font-bold transition-colors ${
                  isWorking
                    ? "bg-sky-600 text-white ring-2 ring-sky-300 dark:ring-sky-700"
                    : isDone
                      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-200"
                      : "bg-white/70 dark:bg-slate-950/50"
                }`}
              >
                {isWorking ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : null}
                {isDone && !isWorking ? <span aria-hidden>✓</span> : null}
                {agent}
                {isWorking ? <span className="opacity-90">작업 중</span> : null}
              </span>
            );
          })}
        </div>
        {podAiReview ? (
          <PodAIReviewPanel review={podAiReview} />
        ) : pod.podKey === "land_legal" && status === "completed" ? (
          <NoPodAIReviewNotice />
        ) : null}
      </div>

      <div className="mt-4 border-t border-slate-200/40 pt-2.5 dark:border-slate-800/40">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10px] font-semibold opacity-75">
            {run?.artifactKeys?.length ? `${run.artifactKeys.length}개 결과물` : "결과물 없음"}
          </span>
          {blocked ? (
            <button
              type="button"
              onClick={() => void onRun(pod.podKey)}
              disabled={executingPodKey !== null}
              title="오케스트레이터가 VWorld PNU·공시지가, 국토부 실거래가 등 가능한 공공/연동 소스로 먼저 자동 보강한 뒤 실행합니다."
              className="rounded bg-rose-600 px-1.5 py-0.5 text-[9px] font-black text-white transition-colors hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              자동 보강 후 실행
            </button>
          ) : isExecuting ? (
            <div className="flex items-center gap-2">
              <Loader2 className="h-3 w-3 animate-spin" />
              <span className="text-[10px] font-bold">{executionProgress}%</span>
            </div>
          ) : (
            <div className="flex gap-1">
              {hasTrigger(pod.podKey) ? (
                <button
                  type="button"
                  onClick={() => void onRun(pod.podKey, getTrigger(pod.podKey))}
                  disabled={executingPodKey !== null}
                  className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[9px] font-black text-amber-800 transition-colors hover:bg-amber-500/30 disabled:cursor-not-allowed disabled:opacity-40 dark:text-amber-200"
                  title="재계산 이벤트를 강제로 발생시키며 실행합니다."
                >
                  이벤트 실행
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => void onRun(pod.podKey)}
                disabled={executingPodKey !== null}
                className="rounded bg-slate-950 px-1.5 py-0.5 text-[9px] font-black text-white transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-100"
              >
                {status === "completed" ? "다시 실행" : "실행"}
              </button>
            </div>
          )}
        </div>
        {isExecuting ? (
          <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
            <div className="h-full bg-sky-500 transition-all duration-300 ease-out" style={{ width: `${executionProgress}%` }} />
          </div>
        ) : null}
      </div>
    </article>
  );
}

const REVIEW_STATUS_LABEL: Record<string, string> = {
  pass: "통과",
  warning: "주의",
  blocking: "차단",
  needs_human_review: "검토 필요",
};

const REVIEW_STATUS_TONE: Record<string, string> = {
  pass: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200",
  warning: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200",
  blocking: "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-200",
  needs_human_review: "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-200",
};

const FINDING_SEVERITY_LABEL: Record<string, string> = {
  info: "정보",
  medium: "주의",
  high: "높음",
  blocking: "차단",
};

function PodAIReviewPanel({ review }: { review: PodAIReview }) {
  const status = review.reviewStatus ?? "needs_human_review";
  const findings = review.findings ?? [];
  const handoffs = review.handoffs ?? [];
  const missingEvidenceFindings = findings.filter((finding) => {
    const category = String(finding.category ?? "").toLowerCase();
    const message = String(finding.message ?? "").toLowerCase();
    return category.includes("missing") || category.includes("evidence") || message.includes("evidence") || message.includes("근거") || message.includes("증빙");
  });
  const nextActions = findings
    .map((finding) => cleanText(finding.recommendedAction, ""))
    .filter((item) => item && item !== "-")
    .slice(0, 2);
  const aiBrain = review.aiBrainUsed ?? {};
  const selectionSource = cleanText(aiBrain.selectionSource ?? aiBrain.selection_source, "기본 AI");
  const model = cleanText(aiBrain.model ?? aiBrain.model_name ?? aiBrain.modelName, review.reviewMode === "deterministic_fallback_review" ? "규칙 기반" : "AI Reviewer");
  const confidence = typeof review.confidence === "number" ? `${Math.round(review.confidence * 100)}%` : "-";
  const documentFacts = review.documentFacts ?? {};
  const showDocumentFacts = Boolean(documentFacts.hasDocumentMarketEvidence || Number(documentFacts.factCount ?? 0) > 0);

  return (
    <section className="mt-3 rounded-lg border border-slate-200/70 bg-white/75 p-2.5 text-[11px] shadow-sm dark:border-slate-700/70 dark:bg-slate-950/45">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex items-center gap-1.5 font-black text-slate-900 dark:text-white">
          <Sparkles className="h-3.5 w-3.5 text-sky-500" />
          AI Review
        </div>
        <span className={`rounded-full border px-2 py-0.5 font-black ${REVIEW_STATUS_TONE[status] ?? REVIEW_STATUS_TONE.needs_human_review}`}>
          {REVIEW_STATUS_LABEL[status] ?? cleanText(status, "검토 필요")}
        </span>
      </div>

      <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
        <div className="rounded-md bg-slate-50 px-2 py-1 font-semibold text-slate-600 dark:bg-slate-900/70 dark:text-slate-300">
          신뢰도 {confidence}
        </div>
        <div className="rounded-md bg-slate-50 px-2 py-1 font-semibold text-slate-600 dark:bg-slate-900/70 dark:text-slate-300">
          Brain {selectionSource} · {model}
        </div>
      </div>

      {showDocumentFacts ? (
        <DocumentMarketFactsPanel facts={documentFacts} />
      ) : null}

      {findings.length ? (
        <div className="mt-2 space-y-1.5">
          <p className="font-black text-slate-800 dark:text-slate-100">왜 막혔나 / 주요 판단</p>
          {findings.slice(0, 3).map((finding, index) => (
            <div key={finding.findingId ?? `${review.reviewId}-finding-${index}`} className="rounded-md border border-slate-200/70 bg-slate-50 px-2 py-1.5 dark:border-slate-700 dark:bg-slate-900/60">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="rounded bg-slate-200 px-1.5 py-0.5 font-black text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                  {FINDING_SEVERITY_LABEL[finding.severity ?? ""] ?? cleanText(finding.severity, "검토")}
                </span>
                <span className="font-bold text-slate-600 dark:text-slate-300">{cleanText(finding.category, "review")}</span>
              </div>
              <p className="mt-1 leading-5 text-slate-700 dark:text-slate-200">{cleanText(finding.message, "근거와 가정 확인이 필요합니다.")}</p>
              {finding.sourceLocators?.length ? (
                <p className="mt-1 truncate font-mono text-[10px] text-slate-500">
                  source: {finding.sourceLocators.map((item) => cleanText(item, "")).filter(Boolean).slice(0, 2).join(", ")}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-2 rounded-md bg-emerald-50 px-2 py-1.5 font-semibold text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200">
          차단 finding은 없습니다. 다만 보고서 확정 전에는 evidence/source locator를 다시 확인하세요.
        </p>
      )}

      {missingEvidenceFindings.length ? (
        <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/35 dark:text-amber-100">
          <div className="inline-flex items-center gap-1 font-black">
            <AlertTriangle className="h-3 w-3" />
            부족 근거 {missingEvidenceFindings.length}건
          </div>
          <p className="mt-1 leading-5">{cleanText(missingEvidenceFindings[0]?.recommendedAction ?? missingEvidenceFindings[0]?.message, "문서 페이지, 엑셀 셀, API row 또는 등기/공시지가 증빙을 연결해야 합니다.")}</p>
        </div>
      ) : null}

      {nextActions.length ? (
        <div className="mt-2 rounded-md border border-sky-200 bg-sky-50 px-2 py-1.5 text-sky-900 dark:border-sky-900 dark:bg-sky-950/35 dark:text-sky-100">
          <p className="font-black">다음 조치</p>
          <ul className="mt-1 space-y-1">
            {nextActions.map((action, index) => (
              <li key={`${review.reviewId}-action-${index}`} className="flex gap-1.5 leading-5">
                <ArrowRight className="mt-0.5 h-3 w-3 shrink-0" />
                <span>{action}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {handoffs.length ? (
        <div className="mt-2 rounded-md bg-slate-50 px-2 py-1.5 dark:bg-slate-900/70">
          <p className="font-black text-slate-800 dark:text-slate-100">Handoff</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {handoffs.slice(0, 3).map((handoff, index) => (
              <span key={handoff.handoffId ?? `${review.reviewId}-handoff-${index}`} className="rounded-full border border-slate-200 bg-white px-2 py-0.5 font-bold text-slate-600 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300">
                {cleanText(handoff.fromAgent, review.agentId ?? "PodAIReviewer")} → {cleanText(handoff.toAgent, "다음 Agent")} · {cleanText(handoff.handoffType, "review")}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function DocumentMarketFactsPanel({ facts }: { facts: Record<string, unknown> }) {
  const sourceDocument = asRecordValue(facts.sourceDocument);
  const fileName = cleanText(
    sourceDocument.originalFileName ?? sourceDocument.fileName ?? sourceDocument.name,
    "업로드 사업계획서 / IM",
  );
  const status = cleanText(facts.reconciliationStatus, "검증 전");
  const issueCount = asCount(facts.issueCount);
  const sourceLocatorCount = asCount(facts.sourceLocatorCount);

  return (
    <div className="mt-2 rounded-md border border-cyan-200 bg-cyan-50 px-2 py-1.5 text-cyan-950 dark:border-cyan-900 dark:bg-cyan-950/30 dark:text-cyan-100">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-black">문서 파싱 근거</p>
        <span className="rounded-full bg-white/80 px-2 py-0.5 text-[10px] font-black dark:bg-slate-950/70">
          검증 상태 {status}
        </span>
      </div>
      <p className="mt-1 truncate font-semibold text-cyan-800 dark:text-cyan-200">{fileName}</p>
      <div className="mt-2 grid grid-cols-2 gap-1.5 md:grid-cols-4">
        <MiniMarketFact label="상품구성" value={`${asCount(facts.productMixCount)}건`} />
        <MiniMarketFact label="매각/분양 사례" value={`${asCount(facts.saleComparableCount)}건`} />
        <MiniMarketFact label="임대 사례" value={`${asCount(facts.rentComparableCount)}건`} />
        <MiniMarketFact label="제안 평당가" value={safeNumber(facts.proposedPricePerPy)} />
      </div>
      <p className="mt-2 text-[10px] font-semibold opacity-75">
        검토 이슈 {issueCount}건 · Source locator {sourceLocatorCount}건
      </p>
    </div>
  );
}

function MiniMarketFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-white/80 px-2 py-1 dark:bg-slate-950/60">
      <p className="text-[10px] font-bold opacity-70">{label}</p>
      <p className="mt-0.5 font-black">{value}</p>
    </div>
  );
}

function NoPodAIReviewNotice() {
  return (
    <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[11px] font-semibold leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/35 dark:text-amber-100">
      Land-Legal Pod 결과는 있으나 AI Review 레코드가 아직 없습니다. Pod를 다시 실행하면 evidence-aware reviewer가 finding, missing evidence, handoff를 저장합니다.
    </div>
  );
}

function RunAllSummaryPanel({ summary }: { summary: NonNullable<ChiefDealOrchestrationPlan["runAllSummary"]> }) {
  const blocked = summary.blockedCount > 0;
  return (
    <div className={`mb-4 rounded-lg border px-3 py-3 text-xs ${
      blocked
        ? "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"
        : "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100"
    }`}>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="font-black">
            {blocked ? "전체 실행 완료 · 남은 blocker 있음" : "전체 실행 완료"}
          </p>
          <p className="mt-1 font-semibold opacity-80">
            완료 {summary.completedCount}개 / 차단 {summary.blockedCount}개 / 요청 {summary.requestedPodCount}개
          </p>
        </div>
        <span className="rounded-full bg-white/70 px-2 py-1 font-black dark:bg-slate-950/60">
          {summary.workflowTrack ? TRACK_LABELS[summary.workflowTrack] : "트랙 미지정"}
        </span>
      </div>
      <div className="mt-3 grid gap-1.5">
        {summary.rows.map((row) => (
          <div key={`${row.podKey}-${row.startedAt ?? ""}`} className="rounded-md bg-white/70 px-2 py-1.5 dark:bg-slate-950/50">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-black">{POD_META[row.podKey]?.label ?? row.podKey}</span>
              <span>{STATUS_LABEL[row.status] ?? row.status}</span>
            </div>
            {row.error ? <p className="mt-1 leading-5 opacity-80">{cleanText(row.error, "자동 보강 후에도 보완자료가 필요합니다.")}</p> : null}
          </div>
        ))}
      </div>
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function EventBusPanel({ events }: { events: ChiefDealOrchestrationPlan["learningLoopEvents"] }) {
  return (
    <div className="mt-4 rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-2 flex items-center gap-2 text-xs font-black uppercase tracking-wider text-slate-500">
        <Radio className="h-3.5 w-3.5 animate-pulse text-rose-500" />
        Event Bus / Learning Loop
      </div>
      <div className="max-h-44 space-y-1.5 overflow-y-auto pr-1">
        {(events ?? []).map((event, index) => (
          <div key={asText(event.eventId ?? index)} className="rounded-r border-l-2 border-amber-500 bg-slate-50/80 py-1 pl-2 text-xs leading-5 dark:bg-slate-950/40">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-slate-400">{event.timestamp || event.createdAt ? new Date(asText(event.timestamp ?? event.createdAt)).toLocaleString("ko-KR") : "-"}</span>
              <span className="font-bold text-slate-800 dark:text-slate-200">{cleanText(event.sourcePod ?? event.source, "Event Bus")}</span>
              <span className="text-slate-500">트리거</span>
              <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[10px] text-amber-700 dark:bg-slate-800 dark:text-amber-300">
                {TRIGGER_LABEL[asText(event.trigger)] ?? cleanText(event.trigger ?? event.eventType, "recalculation_event")}
              </code>
            </div>
            <p className="mt-0.5 text-[11px] font-semibold text-slate-500">
              권장 조치: {cleanText(event.recommendedAction ?? event.targetPods ?? event.target, "관련 Pod 재계산")}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function EventBusQueuePanel({
  events,
  onRunPod,
  executingPodKey,
}: {
  events: RecalculationEvent[];
  onRunPod: (podKey: PodKey, forceTrigger?: string) => Promise<boolean | void>;
  executingPodKey: PodKey | null;
}) {
  return (
    <div className="mt-4 rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-2 flex items-center gap-2 text-xs font-black uppercase tracking-wider text-slate-500">
        <Radio className="h-3.5 w-3.5 animate-pulse text-rose-500" />
        Event Bus / Recalculation Queue
      </div>
      <div className="max-h-52 space-y-1.5 overflow-y-auto pr-1">
        {(events ?? []).map((event, index) => {
          const targetPods = eventTargetPods(event);
          const trigger = asText(event.trigger);
          return (
            <div key={asText(event.eventId ?? index)} className="rounded-r border-l-2 border-amber-500 bg-slate-50/80 py-1.5 pl-2 text-xs leading-5 dark:bg-slate-950/40">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-slate-400">
                  {event.timestamp || event.createdAt ? new Date(asText(event.timestamp ?? event.createdAt)).toLocaleString("ko-KR") : "-"}
                </span>
                <span className="font-bold text-slate-800 dark:text-slate-200">{cleanText(event.sourcePod, "Event Bus")}</span>
                <span className="text-slate-500">trigger</span>
                <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[10px] text-amber-700 dark:bg-slate-800 dark:text-amber-300">
                  {TRIGGER_LABEL[trigger] ?? cleanText(event.trigger ?? event.eventType, "recalculation_event")}
                </code>
                {event.status ? (
                  <span className="rounded-full bg-white px-1.5 py-0.5 text-[10px] font-black text-slate-500 dark:bg-slate-800">
                    {event.status}
                  </span>
                ) : null}
              </div>
              <p className="mt-0.5 text-[11px] font-semibold text-slate-500">
                재계산 대상: {targetPods.length ? targetPods.map((pod) => POD_META[pod]?.label ?? pod).join(" / ") : cleanText(event.targetPods ?? event.targetPod, "대상 Pod 없음")}
              </p>
              {targetPods.length ? (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {targetPods.slice(0, 4).map((podKey) => (
                    <button
                      key={`${asText(event.eventId ?? index)}-${podKey}`}
                      type="button"
                      onClick={() => void onRunPod(podKey)}
                      disabled={executingPodKey !== null}
                      className="rounded bg-amber-600 px-2 py-0.5 text-[10px] font-black text-white transition-colors hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {executingPodKey === podKey ? "실행 중" : `${POD_META[podKey]?.label ?? podKey} 재실행`}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StateGraphSummary({ evidenceCount, assumptionCount, metrics }: { evidenceCount: number; assumptionCount: number; metrics: DealStateValue[] }) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <MiniMetric label="Evidence Vault" value={`${evidenceCount}건`} />
        <MiniMetric label="Assumption Book" value={`${assumptionCount}건`} />
      </div>
      <div className="space-y-2">
        {metrics.slice(0, 5).map((metric) => (
          <div key={metric.key} className="flex items-center justify-between gap-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs dark:border-slate-700 dark:bg-slate-950">
            <div>
              <p className="font-black text-slate-800 dark:text-slate-100">{compactKey(metric.key)}</p>
              <p className="mt-0.5 font-semibold text-slate-500">{VALUE_TYPE_LABEL[metric.valueType]}</p>
            </div>
            <p className="shrink-0 font-black text-slate-950 dark:text-white">{safeNumber(metric.value)}</p>
          </div>
        ))}
        {!metrics.length ? (
          <p className="rounded-md border border-dashed border-slate-300 px-3 py-3 text-xs font-semibold text-slate-500 dark:border-slate-700">
            아직 계산값과 원천 지표가 부족합니다. 사업계획서나 사업수지 Excel을 올리면 State Graph에 입력됩니다.
          </p>
        ) : null}
      </div>
    </div>
  );
}

function ReconciliationPanel({ conflicts }: { conflicts: Array<Record<string, unknown>> }) {
  if (!conflicts.length) {
    return (
      <div className="grid gap-2 text-sm">
        <MiniStep title="Consistency Checker" body="현재 기준으로 큰 숫자 충돌은 발견되지 않았습니다." />
        <MiniStep title="Assumption Conflict Resolver" body="문서, 엑셀, API 값이 충돌하면 기준값 승인 또는 재계산 요청으로 넘깁니다." />
        <MiniStep title="Deal Thesis Synthesizer" body="개발논리, 회수논리, 조건부 승인 논리를 하나로 합성합니다." />
      </div>
    );
  }
  return (
    <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
      {conflicts.slice(0, 8).map((conflict, index) => {
        const code = asText(conflict.code ?? conflict.category ?? "CONFLICT");
        return (
          <div key={asText(conflict.code ?? index)} className={`rounded-md border px-3 py-2 text-xs ${severityTone(asText(conflict.severity))}`}>
            <div className="flex items-start justify-between gap-2">
              <p className="font-black">{code}</p>
              <span className="rounded-full bg-white/70 px-2 py-0.5 text-[10px] font-black dark:bg-slate-950/60">
                {asText(conflict.severity ?? "medium")}
              </span>
            </div>
            <p className="mt-1 leading-5 opacity-85">{CONFLICT_LABEL[code] ?? cleanText(conflict.message ?? conflict.description, "검토가 필요한 교차 검증 이슈입니다.")}</p>
            <p className="mt-1 font-semibold opacity-70">상태: {cleanText(conflict.status, "needs_review")}</p>
          </div>
        );
      })}
    </div>
  );
}

function RiskCommitteePanel({ opinions }: { opinions: RiskCommitteeOpinion[] }) {
  if (!opinions.length) {
    return (
      <div className="grid gap-2 text-sm">
        {Object.entries(REVIEWER_LABEL).map(([key, label]) => (
          <div key={key} className="rounded-md border border-slate-200 bg-white px-3 py-2 font-semibold text-slate-700 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200">
            {label}
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="max-h-96 space-y-2 overflow-y-auto pr-1">
      {opinions.map((opinion) => (
        <div key={opinion.reviewerRole} className={`rounded-md border px-3 py-2 text-xs ${impactTone(opinion.decisionImpact)}`}>
          <div className="flex items-start justify-between gap-2">
            <p className="font-black">{REVIEWER_LABEL[opinion.reviewerRole] ?? opinion.reviewerRole}</p>
            <span className="rounded-full bg-white/70 px-2 py-0.5 text-[10px] font-black dark:bg-slate-950/60">
              {IMPACT_LABEL[opinion.decisionImpact] ?? opinion.decisionImpact}
            </span>
          </div>
          <p className="mt-1 leading-5 opacity-90">{cleanText(opinion.opinion, "추가 검토 의견을 생성해야 합니다.")}</p>
          {opinion.requiredActions?.length ? (
            <p className="mt-2 font-semibold opacity-75">조치: {opinion.requiredActions.slice(0, 2).map((x) => cleanText(x, "보완 필요")).join(" / ")}</p>
          ) : null}
          {typeof opinion.confidence === "number" ? (
            <p className="mt-1 font-mono text-[10px] opacity-60">confidence {(opinion.confidence * 100).toFixed(0)}%</p>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function HumanLearningPanel({ events }: { events: ChiefDealOrchestrationPlan["learningLoopEvents"] }) {
  return (
    <div className="mt-3 rounded-md border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-semibold leading-5 text-indigo-800 dark:border-indigo-900 dark:bg-indigo-950/30 dark:text-indigo-200">
      전문가 검토와 수정사항은 Learning Loop로 들어가 회사 표준, 용어, 수지 항목, 리스크 표현, 승인조건에 반영됩니다.
      <span className="mt-1 block">현재 검토 이벤트: {events?.length ?? 0}건</span>
    </div>
  );
}

function MissingInputPanel({ projectId, missing }: { projectId: string; missing: MissingInputRegisterItem[] }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-black uppercase tracking-wide text-slate-500">Missing Input Register</p>
          <h2 className="mt-1 text-xl font-black text-slate-950 dark:text-white">다음 분석 전에 보강해야 할 자료</h2>
        </div>
        <Link href={`/project/${projectId}/input-package`} className="inline-flex h-9 items-center gap-2 rounded-lg bg-slate-950 px-3 text-sm font-bold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950">
          자료 보강
          <ArrowRight className="h-4 w-4" />
        </Link>
      </div>
      <div className="mt-4 grid gap-2">
        {missing.slice(0, 10).map((item) => {
          const copy = MISSING_LABEL[item.itemId];
          return (
            <div key={item.itemId} className={`rounded-lg border px-3 py-2 ${severityTone(item.severity)}`}>
              <div className="flex flex-col gap-1 md:flex-row md:items-start md:justify-between">
                <div>
                  <p className="text-sm font-black">{copy?.title ?? cleanText(item.description, item.itemId)}</p>
                  <p className="mt-1 text-xs font-semibold opacity-75">{copy?.description ?? cleanText(item.description, "보강자료가 필요합니다.")}</p>
                  <p className="mt-1 text-xs font-semibold opacity-75">필요 Pod: {(item.requiredFor ?? []).join(", ") || "-"}</p>
                </div>
                <span className="shrink-0 rounded-full bg-white/70 px-2 py-1 text-[11px] font-black dark:bg-slate-950/60">
                  {item.severity}
                </span>
              </div>
              {item.acceptableSources?.length ? (
                <p className="mt-2 text-xs font-semibold opacity-75">허용 출처: {item.acceptableSources.map((x) => cleanText(x, "출처")).join(" / ")}</p>
              ) : null}
            </div>
          );
        })}
        {!missing.length ? (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-3 text-sm font-bold text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100">
            현재 기준으로 필수 누락자료가 없습니다.
          </div>
        ) : null}
      </div>
    </section>
  );
}

function ScenarioPanel({ plan }: { plan: ChiefDealOrchestrationPlan }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-xs font-black uppercase tracking-wide text-slate-500">Scenario & Stress Test</p>
      <h2 className="mt-1 text-xl font-black text-slate-950 dark:text-white">최종 결론은 4개 Case로 나눕니다</h2>
      <div className="mt-4 space-y-2">
        {(plan.scenarioConclusions ?? []).map((scenario) => (
          <div key={scenario.scenario} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-3 dark:border-slate-700 dark:bg-slate-950">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-sky-600" />
                <p className="text-sm font-black text-slate-900 dark:text-white">{SCENARIO_LABEL[scenario.scenario] ?? scenario.scenario}</p>
              </div>
              <span className="text-xs font-black text-slate-500">{scenario.riskFlags?.length ?? 0} flags</span>
            </div>
            <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">{cleanText(scenario.summary, SCENARIO_FALLBACK[scenario.scenario])}</p>
            <p className="mt-1 text-xs font-bold text-slate-700 dark:text-slate-200">판단: {cleanText(scenario.decisionImplication, "추가 자료 확보 후 판단해야 합니다.")}</p>
            <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] md:grid-cols-4">
              <MiniMetric label="매출" value={safeNumber(scenario.keyMetrics?.totalRevenue)} />
              <MiniMetric label="총비용" value={safeNumber(scenario.keyMetrics?.totalCost)} />
              <MiniMetric label="이익률" value={safeNumber(scenario.keyMetrics?.profitMargin)} />
              <MiniMetric label="Exit 분양률" value={safeNumber(scenario.keyMetrics?.exitSaleRate)} />
            </div>
          </div>
        ))}
      </div>
      <div className={`mt-4 rounded-lg border px-3 py-3 text-sm ${decisionTone(plan.decisionGate?.decision)}`}>
        <p className="font-black">현재 Gate: {plan.decisionGate?.decision}</p>
        <p className="mt-1 text-xs leading-5 opacity-80">
          {plan.decisionGate?.requiredSupplements?.length
            ? `필요 보강: ${plan.decisionGate.requiredSupplements.slice(0, 3).map((x) => cleanText(x, "보강 필요")).join(" / ")}`
            : cleanText(plan.decisionGate?.rationale, "아직 최종 판단 전입니다.")}
        </p>
        {plan.decisionGate?.mitigationActions?.length ? (
          <p className="mt-1 text-xs leading-5 opacity-80">Cure: {plan.decisionGate.mitigationActions.slice(0, 3).map((x) => cleanText(x, "Cure 필요")).join(" / ")}</p>
        ) : null}
      </div>
    </section>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-950">
      <p className="text-[11px] font-bold text-slate-500">{label}</p>
      <p className="mt-1 text-base font-black text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function MiniStep({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-950">
      <p className="font-black text-slate-900 dark:text-white">{title}</p>
      <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">{body}</p>
    </div>
  );
}
