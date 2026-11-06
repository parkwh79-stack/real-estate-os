"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useParams } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Clock3,
  Database,
  FileJson,
  FileText,
  GitBranch,
  Loader2,
  MessageSquareText,
  Play,
  RefreshCw,
  ShieldCheck,
  UserCheck,
  XCircle,
} from "lucide-react";
import { apiBase, authHeaders, getStoredAuthSession } from "../../../../lib/auth-client";
import { OrchestrationToolbar } from "../../../../components/orchestration/orchestration-toolbar";
import { ProjectWorkspaceLink } from "../../../../components/layout/project-workspace-link";

interface RunSummary {
  run_id: string;
  workflow: string;
  status: string;
  started_at: string;
  completed_at?: string;
  step_count: number;
  errors: Array<{ message: string; type?: string }>;
  approval?: {
    approved_at: string;
    approved_by: string;
    comment?: string;
  } | null;
}

interface RunStep {
  key: string;
  title: string;
  status: string;
  completed_at: string;
  output: Record<string, unknown>;
}

interface MarketEvidenceRecord {
  recordKey: string;
  evidenceId: string;
  evidenceType: string;
  sourceName?: string;
  sourceType?: string;
  sourceUrlOrEvidenceId?: string;
  validationStatus?: string;
  reviewStatus?: string;
  reviewedBy?: string;
  reviewComment?: string;
  extractedFields?: Record<string, unknown>;
  relatedCheckKeys?: string[];
  runId?: string;
}

interface AgentProgress {
  schemaVersion?: string;
  agentKey: string;
  agentName: string;
  status: string;
  progressPercent: number;
  completedTasks: string[];
  pendingTasks: string[];
  blockedTasks: string[];
  dependsOn: string[];
  downstreamDependencies: string[];
  lastArtifactKey?: string;
  nextAction?: string;
  reviewRequired?: boolean;
  handoffIds: string[];
  updatedAt?: string;
}

interface AgentHandoff {
  schemaVersion?: string;
  handoffId: string;
  runId?: string;
  workflow?: string;
  handoffType: string;
  fromAgent: string;
  toAgent: string;
  status: string;
  payloadSchema?: string;
  payload: Record<string, unknown>;
  evidenceIds: string[];
  confidence?: number;
  blockingIssues: string[];
  assumptions: string[];
  dataLimitations: string[];
  requiresHumanReview: boolean;
  reviewStatus: string;
  reviewedAt?: string;
  reviewedBy?: string;
  reviewComment?: string;
  reviewHistory?: Array<{
    reviewStatus: string;
    reviewedBy: string;
    reviewedAt: string;
    reviewComment?: string;
  }>;
  createdAt: string;
  progressImpact?: Record<string, unknown>;
}

interface WaterfallScenarioRow {
  rank: number;
  tranche: string;
  priority: number;
  principalClaim: number;
  totalClaim: number;
  paymentReceived: number;
  status: string;
}

interface WaterfallScenario {
  scenarioId: string;
  saleRate: number;
  discountRate: number;
  cashAvailable: number;
  totalClaimAmount: number;
  totalShortfall: number;
  remainingCash: number;
  rows: WaterfallScenarioRow[];
}

interface UnsoldLtvRow {
  saleRate: number;
  discountRate: number;
  unsoldRate: number;
  unsoldLtv: number;
  riskLevel: string;
}

interface UnsoldLtvMatrix {
  rows: UnsoldLtvRow[];
  summary: {
    baseCaseLtv: number;
    stressCaseLtv: number;
    breachCount: number;
  };
}

interface TrancheRecovery {
  tranche: string;
  recoveryRatio: number;
}

interface TrancheRecoveryScenario {
  scenarioId: string;
  saleRate: number;
  discountRate: number;
  recoveryPool: number;
  tranches: TrancheRecovery[];
}

interface ScenarioEvaluation {
  waterfallScenarios?: WaterfallScenario[];
  unsoldLtvMatrix?: UnsoldLtvMatrix;
  trancheRecoveryMatrix?: TrancheRecoveryScenario[];
}

interface RestructuringRecommendation {
  code?: string;
  title?: string;
  description?: string;
  action?: string;
  priority?: string;
  amount?: number;
  amountUnit?: string;
  targetRiskCode?: string;
  riskCodes?: string[];
  sourceRequired?: boolean;
}

interface RestructuringCureSummary {
  schemaVersion?: string;
  recordCount?: number;
  activeCount?: number;
  sourceBackedActiveCount?: number;
  sourceBackedActiveCureCount?: number;
  warning?: string;
  records?: RestructuringCureRecord[];
  sourceBackedRecords?: RestructuringCureRecord[];
}

interface RestructuringPlan {
  analysisType?: string;
  status?: string;
  summary?: string;
  seniorShortfallFix?: Record<string, unknown>;
  unsoldLtvFix?: Record<string, unknown>;
  recommendations?: RestructuringRecommendation[];
  approvalPath?: {
    status?: string;
    remainingBlockers?: string[];
    adoptedCureCount?: number;
    sourceBackedActiveCureCount?: number;
    adoptedCureCodes?: string[];
  };
  adoptedCures?: RestructuringCureSummary;
}

interface RestructuringCureRecord {
  cureId?: string;
  projectId?: string;
  cureType: string;
  status?: string;
  riskCodes?: string[];
  amount?: number;
  amountUnit?: string;
  targetMetric?: string;
  targetValue?: number | string;
  description?: string;
  sourceName?: string;
  sourceType?: string;
  sourceUrlOrEvidenceId?: string;
  sourceLocators?: Array<Record<string, unknown>>;
  evidenceIds?: string[];
  reviewedBy?: string;
  reviewedAt?: string;
  appliesToRunId?: string;
  expiresAt?: string;
  createdAt?: string;
  updatedAt?: string;
}

interface RestructuringCureList {
  records: RestructuringCureRecord[];
}

interface RiskRegisterRisk {
  riskId: string;
  sourceAgent: string;
  sourceArtifact?: string;
  category: string;
  code: string;
  title: string;
  severity: string;
  probability: string;
  grade: string;
  score: number;
  mitigant: string;
  evidenceIds?: string[];
  sourceLocators?: Array<Record<string, unknown>>;
  evidenceGraphLinks?: {
    riskNodeId?: string | null;
    sourceArtifactNodeId?: string | null;
    evidenceNodeIds?: string[];
    claimNodeIds?: string[];
    sourceDocumentNodeIds?: string[];
    sourceLocatorNodeIds?: string[];
  };
  requiresHumanReview: boolean;
  status?: string;
  reviewStatus?: string;
  reviewedBy?: string | null;
  reviewedAt?: string | null;
  reviewComment?: string | null;
  waiverReason?: string | null;
  reviewHistory?: Array<{
    reviewStatus: string;
    reviewedBy: string;
    reviewedAt: string;
    reviewComment?: string;
    waiverReason?: string;
  }>;
}

interface RiskRegister {
  status: string;
  summary: {
    riskCount: number;
    criticalCount: number;
    highCount: number;
    mediumCount: number;
    humanReviewRequiredCount: number;
  };
  reviewSummary?: {
    reviewedCount: number;
    unresolvedReviewCount: number;
    byReviewStatus: Record<string, number>;
  };
  risks: RiskRegisterRisk[];
  topRisks: RiskRegisterRisk[];
  approvalConditions: string[];
}

interface CreditReviewDecision {
  decision: string;
  decisionLabel: string;
  status: string;
  confidence: number;
  summary: {
    riskCount: number;
    criticalRiskCount: number;
    highRiskCount: number;
    legalStatus?: string;
    dataQualityStatus?: string;
    reconciliationStatus?: string;
    marketabilityGrade?: string;
    marketabilityScore?: number;
    ltv?: number;
    ltc?: number;
    exitSaleRate?: number;
  };
  decisionReasons: string[];
  approvalConditions: string[];
  requiredFollowUps: string[];
  reportParagraphs: {
    creditOpinion?: string;
    approvalCondition?: string;
    mitigantSummary?: string;
  };
  humanReview: {
    required: boolean;
    reason: string;
  };
}

interface ApprovalReadinessGateCheck {
  code: string;
  pass: boolean;
  severity: "blocking" | "warning" | string;
  message: string;
}

interface ApprovalReadinessGate {
  schemaVersion?: string;
  analysisType: string;
  pass: boolean;
  status: string;
  gateType: string;
  summary: {
    blockingIssueCount: number;
    warningCount: number;
    unresolvedHandoffReviewCount: number;
    unresolvedRiskReviewCount: number;
    riskRegisterStatus?: string;
    creditDecision?: string;
    legalStatus?: string;
    dataQualityStatus?: string;
    reconciliationStatus?: string;
    landEvidenceReady?: boolean;
    dealStateRecordCount?: number;
    dealStateRiskIssueCount?: number;
    dealStateBlockingRiskCount?: number;
    dealStateEvidenceCount?: number;
    dealStateMetricCount?: number;
    dealStateAssumptionCount?: number;
    dealStateSourceLocatorMissingCount?: number;
  };
  gates: ApprovalReadinessGateCheck[];
  blockers: ApprovalReadinessGateCheck[];
  warnings: ApprovalReadinessGateCheck[];
  requiredActions: string[];
}

interface DataQualityValue {
  source?: string;
  value?: number | string | boolean;
}

interface DataQualityCheck {
  key: string;
  label: string;
  category?: string;
  status: string;
  severity: string;
  message: string;
  metadata?: {
    values?: DataQualityValue[];
    unit?: string;
    sourceLocators?: Array<Record<string, unknown>>;
    [key: string]: unknown;
  } | DataQualityValue[] | Record<string, unknown>;
  approvedAssumptionOverride?: {
    overrideId?: string;
    canonicalField?: string;
    approvedValue?: unknown;
    approvedBy?: string;
    approvedAt?: string;
    reason?: string;
  };
}

interface DataQualityReview {
  status: string;
  summary?: {
    checkCount?: number;
    clearCount?: number;
    overriddenCount?: number;
    warningCount?: number;
    blockingCount?: number;
    missingCount?: number;
  };
  checks?: DataQualityCheck[];
  appliedOverrides?: Array<Record<string, unknown>>;
  blockingIssues?: string[];
  dataLimitations?: string[];
  nextActions?: string[];
}

interface AssumptionOverrideRecord {
  overrideId: string;
  projectId?: string;
  checkKey: string;
  canonicalField: string;
  approvedValue?: unknown;
  unit?: string;
  status?: string;
  effectiveStatus?: string;
  approvedBy?: string;
  approvedAt?: string;
  reason?: string;
  sourceLocators?: Array<Record<string, unknown>>;
  evidenceIds?: string[];
  appliesToRunId?: string;
  statusReason?: string;
  statusChangedBy?: string;
  statusChangedAt?: string;
  expiresAt?: string;
  supersededByOverrideId?: string;
  supersededAt?: string;
  createdAt?: string;
  updatedAt?: string;
}

interface AssumptionOverrideList {
  records: AssumptionOverrideRecord[];
}

interface LandAgentReview {
  analysisType?: string;
  status: string;
  summary?: {
    projectName?: string;
    pnu?: string;
    officialLandPrice?: number;
    officialLandPriceBaseYear?: number;
    parcelCount?: number;
    totalAreaSqm?: number;
    totalPurchaseAmount?: number | null;
    pnuCoverageRatio?: number;
    officialLandPriceCoverageRatio?: number;
    registryEvidenceCoverageRatio?: number;
    acquisitionAreaRatio?: number;
    publicLandDetected?: boolean;
  };
  landAcquisitionStatus?: Record<string, unknown>;
  evidenceReadiness?: {
    pnuReady?: boolean;
    officialPriceReady?: boolean;
    registryEvidenceReady?: boolean;
    acquisitionReady?: boolean;
    sourceDataReady?: boolean;
  };
  blockingIssues?: string[];
  dataLimitations?: string[];
  requiresHumanReview?: boolean;
  confidence?: number;
  handoffReadiness?: string;
  evidenceIds?: string[];
  sourceLocators?: Array<Record<string, unknown>>;
  sourceMetadata?: Record<string, unknown>;
}

interface LandEvidenceRecord {
  recordKey: string;
  evidenceId: string;
  evidenceType: string;
  pnu?: string;
  parcelKey?: string;
  sourceName?: string;
  sourceType?: string;
  sourceUrlOrEvidenceId?: string;
  verificationStatus?: string;
  baseYear?: number | string | null;
  baseDate?: string | null;
  pricePerSqm?: number;
  officialTotalLandValue?: number;
  registeredOwner?: string;
  ownerMatchStatus?: string;
  mortgageExists?: boolean;
  seizureExists?: boolean;
  provisionalRegistrationExists?: boolean;
  easementExists?: boolean;
  trustRegistrationExists?: boolean;
  releaseRequired?: boolean;
  acquisitionAreaRatio?: number;
  acquisitionAmountRatio?: number;
  acquisitionStatus?: string;
  closingStatus?: string;
  reviewedBy?: string;
  reviewedAt?: string;
  updatedAt?: string;
}

interface LandEvidenceList {
  records: LandEvidenceRecord[];
}

interface OrchestrationRun extends RunSummary {
  steps: RunStep[];
  agentProgress?: Record<string, AgentProgress>;
  handoffs?: AgentHandoff[];
  artifacts: {
    developerFeasibility?: {
      summary: string;
      developerReturns: {
        developmentProfit: number;
        profitMargin: number;
        profitOnCost: number;
      };
      pfReadiness: {
        status: string;
        ltvByRevenue: number;
        ltcByCost: number;
        exitSaleRate: number;
      };
      dataQuality: {
        confidence: number;
        warnings: string[];
      };
    };
    marketEvidenceReview?: {
      status: string;
      evidenceSummary: {
        saleComparableCount?: number;
        rentComparableCount?: number;
        evidenceRecordCount?: number;
      };
      issues: Array<{ code: string; message: string; severity: string }>;
    };
    marketabilityAnalysis?: {
      applicable: boolean;
      score?: number;
      grade: string;
      summary: string;
      recommendations: string[];
      riskFlags?: string[];
    };
    legalPermitReview?: {
      status: string;
      mcp: {
        connected: boolean;
        source: string;
      };
      requiredFollowUps: string[];
    };
    qualityCheck?: {
      pass: boolean;
      legalReviewStatus: string;
      missingArtifacts: string[];
      missingReportSections: string[];
    };
    reportRecord?: {
      report_id: string;
      title: string;
      metadata: {
        complete: boolean;
        section_count: number;
      };
    };
    scenariosEvaluation?: ScenarioEvaluation;
    restructuringPlan?: RestructuringPlan;
    riskRegister?: RiskRegister;
    creditReviewDecision?: CreditReviewDecision;
    approvalReadinessGate?: ApprovalReadinessGate;
    dataQualityReview?: DataQualityReview;
    landAgentReview?: LandAgentReview;
  };
}

interface ProjectResponse {
  project?: {
    name?: string;
  };
}

interface ProjectSnapshotSummary {
  snapshotId: string;
  projectId: string;
  runId?: string;
  workflow?: string;
  status?: string;
  createdAt?: string;
  outcomeStatus?: string;
  repaymentCompleted?: boolean | null;
  eodOccurred?: boolean | null;
  lossOccurred?: boolean | null;
  actualSalesRate?: number | null;
  recoveryRate?: number | null;
}

interface HistoricalOutcome {
  schemaVersion?: string;
  outcomeStatus?: string;
  actualSalesRate?: number | null;
  actualDiscountRate?: number | null;
  repaymentCompleted?: boolean | null;
  repaymentCompletionDate?: string | null;
  extensionOccurred?: boolean | null;
  extensionCount?: number | string | null;
  eodOccurred?: boolean | null;
  eodDate?: string | null;
  lossOccurred?: boolean | null;
  lossAmount?: number | string | null;
  recoveryRate?: number | null;
  seniorRecoveryRate?: number | null;
  mezzanineRecoveryRate?: number | null;
  juniorRecoveryRate?: number | null;
  actualExitSource?: string | null;
  outcomeMemo?: string | null;
  sourceMetadata?: Record<string, unknown>;
  sourceLocators?: Array<Record<string, unknown>>;
  outcomeQuality?: {
    status?: string;
    missingFields?: string[];
    hasSourceMetadata?: boolean;
    hasSourceLocators?: boolean;
  };
  updatedBy?: string;
  updatedAt?: string;
}

interface ProjectSnapshotDetail extends ProjectSnapshotSummary {
  historicalOutcome?: HistoricalOutcome | null;
  historicalOutcomeHistory?: HistoricalOutcome[];
}

interface OutcomeFormState {
  actualSalesRate: string;
  actualDiscountRate: string;
  repaymentCompleted: boolean;
  repaymentCompletionDate: string;
  extensionOccurred: boolean;
  extensionCount: string;
  eodOccurred: boolean;
  eodDate: string;
  lossOccurred: boolean;
  lossAmount: string;
  recoveryRate: string;
  seniorRecoveryRate: string;
  mezzanineRecoveryRate: string;
  juniorRecoveryRate: string;
  actualExitSource: string;
  outcomeMemo: string;
  sourceName: string;
  sourceType: string;
  baseDate: string;
  confidence: string;
  sourceLocatorsJson: string;
}

interface SimilarCaseRiskMatch {
  queryRiskId?: string;
  queryRiskCode?: string;
  historicalRiskId?: string;
  historicalRiskCode?: string;
  category?: string;
  severity?: string;
  reviewStatus?: string;
  mitigant?: string;
  approvalCondition?: string;
}

interface SimilarCaseResult {
  projectId?: string;
  snapshotId?: string;
  runId?: string;
  workflow?: string;
  createdAt?: string;
  similarityScore: number;
  matchedDimensions?: string[];
  riskMatches?: SimilarCaseRiskMatch[];
  decisionOutcome?: {
    decision?: string;
    decisionLabel?: string;
    status?: string;
  };
  historicalOutcome?: HistoricalOutcome | null;
  approvalConditions?: string[];
  handlingSummary?: {
    matchedRiskCount?: number;
    reviewedCount?: number;
    waivedCount?: number;
    resolvedCount?: number;
    approvalConditionCount?: number;
    historicalOutcomeStatus?: string;
    repaymentCompleted?: boolean | null;
    eodOccurred?: boolean | null;
    lossOccurred?: boolean | null;
    recoveryRate?: number | null;
  };
  caseComparisonSeed?: Record<string, unknown>;
}

interface SimilarCaseSearchResponse {
  schemaVersion?: string;
  analysisType?: string;
  generatedAt?: string;
  queryProjectId?: string;
  querySnapshotId?: string;
  queryRunId?: string;
  summary?: {
    candidateCount?: number;
    returnedCount?: number;
    queryRiskCount?: number;
    topScore?: number;
  };
  similarCases: SimilarCaseResult[];
  dataLimitations?: string[];
}

interface SnapshotSeedChange {
  field: string;
  before?: unknown;
  after?: unknown;
  changeType?: string;
  material?: boolean;
  absoluteChange?: number;
  percentChange?: number;
  direction?: string;
}

interface SnapshotRiskSummary {
  riskId?: string;
  code?: string;
  category?: string;
  severity?: string;
  probability?: string;
  grade?: string;
  score?: number;
  reviewStatus?: string;
  mitigant?: string;
}

interface SnapshotRiskChange {
  code?: string;
  riskIdBefore?: string;
  riskIdAfter?: string;
  category?: string;
  material?: boolean;
  fieldChanges?: Array<{
    field: string;
    before?: unknown;
    after?: unknown;
  }>;
}

interface SnapshotDiffResponse {
  schemaVersion?: string;
  analysisType?: string;
  generatedAt?: string;
  projectId?: string;
  baseSnapshotId?: string;
  compareSnapshotId?: string;
  baseRunId?: string;
  compareRunId?: string;
  summary: {
    seedChangeCount: number;
    addedRiskCount: number;
    removedRiskCount: number;
    changedRiskCount: number;
    artifactAddedCount: number;
    artifactRemovedCount: number;
    reportSectionAddedCount: number;
    reportSectionRemovedCount: number;
    decisionChanged: boolean;
    materialChangeCount: number;
    requiresReReview: boolean;
  };
  seedChanges: SnapshotSeedChange[];
  riskChanges: {
    added: SnapshotRiskSummary[];
    removed: SnapshotRiskSummary[];
    changed: SnapshotRiskChange[];
  };
  decisionChange: {
    changed: boolean;
    before?: Record<string, unknown>;
    after?: Record<string, unknown>;
    fieldChanges?: Array<{
      field: string;
      before?: unknown;
      after?: unknown;
    }>;
  };
  artifactChanges: {
    added: string[];
    removed: string[];
    unchanged?: string[];
  };
  reportChanges: {
    addedSections: string[];
    removedSections: string[];
    unchangedSections?: string[];
  };
  materialChanges: Array<{
    type: string;
    field?: string;
    code?: string;
    artifactKey?: string;
    message: string;
  }>;
  reReviewRecommendation: {
    required: boolean;
    level: string;
    message: string;
    recommendedActions: string[];
  };
}

function currentSessionUserId(fallback = "web") {
  return getStoredAuthSession()?.user.userId ?? fallback;
}

function fmtPercent(value?: number) {
  if (value === undefined || Number.isNaN(value)) return "-";
  return `${(value * 100).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%`;
}

function fmtEok(value?: number) {
  if (value === undefined || Number.isNaN(value)) return "-";
  return `${(value / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}억원`;
}

function fmtDate(value?: string) {
  if (!value) return "-";
  return new Date(value).toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function shortValue(value: unknown) {
  if (value === undefined || value === null || value === "") return "-";
  if (typeof value === "number") return value.toLocaleString("ko-KR");
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function blankOutcomeForm(): OutcomeFormState {
  return {
    actualSalesRate: "",
    actualDiscountRate: "",
    repaymentCompleted: false,
    repaymentCompletionDate: "",
    extensionOccurred: false,
    extensionCount: "",
    eodOccurred: false,
    eodDate: "",
    lossOccurred: false,
    lossAmount: "",
    recoveryRate: "",
    seniorRecoveryRate: "",
    mezzanineRecoveryRate: "",
    juniorRecoveryRate: "",
    actualExitSource: "",
    outcomeMemo: "",
    sourceName: "",
    sourceType: "manual_review",
    baseDate: "",
    confidence: "",
    sourceLocatorsJson: "[]",
  };
}

function outcomeFormFromSnapshot(snapshot?: ProjectSnapshotDetail | null): OutcomeFormState {
  const outcome = snapshot?.historicalOutcome ?? null;
  const sourceMetadata = outcome?.sourceMetadata ?? {};
  return {
    actualSalesRate: outcome?.actualSalesRate !== undefined && outcome?.actualSalesRate !== null ? String(outcome.actualSalesRate) : "",
    actualDiscountRate: outcome?.actualDiscountRate !== undefined && outcome?.actualDiscountRate !== null ? String(outcome.actualDiscountRate) : "",
    repaymentCompleted: outcome?.repaymentCompleted === true,
    repaymentCompletionDate: String(outcome?.repaymentCompletionDate ?? ""),
    extensionOccurred: outcome?.extensionOccurred === true,
    extensionCount: outcome?.extensionCount !== undefined && outcome?.extensionCount !== null ? String(outcome.extensionCount) : "",
    eodOccurred: outcome?.eodOccurred === true,
    eodDate: String(outcome?.eodDate ?? ""),
    lossOccurred: outcome?.lossOccurred === true,
    lossAmount: outcome?.lossAmount !== undefined && outcome?.lossAmount !== null ? String(outcome.lossAmount) : "",
    recoveryRate: outcome?.recoveryRate !== undefined && outcome?.recoveryRate !== null ? String(outcome.recoveryRate) : "",
    seniorRecoveryRate: outcome?.seniorRecoveryRate !== undefined && outcome?.seniorRecoveryRate !== null ? String(outcome.seniorRecoveryRate) : "",
    mezzanineRecoveryRate: outcome?.mezzanineRecoveryRate !== undefined && outcome?.mezzanineRecoveryRate !== null ? String(outcome.mezzanineRecoveryRate) : "",
    juniorRecoveryRate: outcome?.juniorRecoveryRate !== undefined && outcome?.juniorRecoveryRate !== null ? String(outcome.juniorRecoveryRate) : "",
    actualExitSource: String(outcome?.actualExitSource ?? ""),
    outcomeMemo: String(outcome?.outcomeMemo ?? ""),
    sourceName: String(sourceMetadata.sourceName ?? sourceMetadata.source_name ?? ""),
    sourceType: String(sourceMetadata.sourceType ?? sourceMetadata.source_type ?? "manual_review"),
    baseDate: String(sourceMetadata.baseDate ?? sourceMetadata.base_date ?? ""),
    confidence: sourceMetadata.confidence !== undefined && sourceMetadata.confidence !== null ? String(sourceMetadata.confidence) : "",
    sourceLocatorsJson: JSON.stringify(outcome?.sourceLocators ?? [], null, 2),
  };
}

function numberOrUndefined(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const normalized = Number(trimmed.replaceAll(",", ""));
  return Number.isFinite(normalized) ? normalized : undefined;
}

function rateOrUndefined(value: string) {
  const parsed = numberOrUndefined(value);
  if (parsed === undefined) return undefined;
  return parsed > 1 ? parsed / 100 : parsed;
}

export default function ProjectOrchestrationPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [project, setProject] = useState<ProjectResponse>({});
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState<OrchestrationRun | null>(null);
  const [snapshots, setSnapshots] = useState<ProjectSnapshotSummary[]>([]);
  const [selectedSnapshotId, setSelectedSnapshotId] = useState("");
  const [selectedSnapshot, setSelectedSnapshot] = useState<ProjectSnapshotDetail | null>(null);
  const [similarCaseResult, setSimilarCaseResult] = useState<SimilarCaseSearchResponse | null>(null);
  const [snapshotDiffResult, setSnapshotDiffResult] = useState<SnapshotDiffResponse | null>(null);
  const [marketEvidence, setMarketEvidence] = useState<MarketEvidenceRecord[]>([]);
  const [landEvidenceRecords, setLandEvidenceRecords] = useState<LandEvidenceRecord[]>([]);
  const [assumptionOverrides, setAssumptionOverrides] = useState<AssumptionOverrideRecord[]>([]);
  const [restructuringCures, setRestructuringCures] = useState<RestructuringCureRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [approving, setApproving] = useState(false);
  const [savingOutcome, setSavingOutcome] = useState(false);
  const [searchingSimilarCases, setSearchingSimilarCases] = useState(false);
  const [diffingSnapshots, setDiffingSnapshots] = useState(false);
  const [reviewingKey, setReviewingKey] = useState<string | null>(null);
  const [reviewingHandoffId, setReviewingHandoffId] = useState<string | null>(null);
  const [reviewingRiskId, setReviewingRiskId] = useState<string | null>(null);
  const [savingOverrideKey, setSavingOverrideKey] = useState<string | null>(null);
  const [updatingOverrideId, setUpdatingOverrideId] = useState<string | null>(null);
  const [savingLandEvidence, setSavingLandEvidence] = useState(false);
  const [savingRestructuringCure, setSavingRestructuringCure] = useState(false);
  const [approvalComment, setApprovalComment] = useState("");
  const [evidenceReviewer, setEvidenceReviewer] = useState("market_analyst");
  const [handoffReviewer, setHandoffReviewer] = useState("credit_officer");
  const [riskReviewer, setRiskReviewer] = useState("credit_officer");
  const [outcomeUser, setOutcomeUser] = useState("credit_officer");
  const [outcomeForm, setOutcomeForm] = useState<OutcomeFormState>(() => blankOutcomeForm());
  const [similarLimit, setSimilarLimit] = useState("5");
  const [similarMinScore, setSimilarMinScore] = useState("0");
  const [includeSameProjectCases, setIncludeSameProjectCases] = useState(false);
  const [baseSnapshotId, setBaseSnapshotId] = useState("");
  const [compareSnapshotId, setCompareSnapshotId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadMarketEvidence = useCallback(async (runId?: string) => {
    if (!runId) {
      setMarketEvidence([]);
      return;
    }
    const evidenceRes = await fetch(`${apiBase}/projects/${projectId}/market-evidence?run_id=${encodeURIComponent(runId)}`, {
      cache: "no-store",
      headers: authHeaders("web"),
    });
    if (!evidenceRes.ok) throw new Error("Unable to load market evidence records.");
    setMarketEvidence((await evidenceRes.json()) as MarketEvidenceRecord[]);
  }, [projectId]);

  const loadLandEvidence = useCallback(async () => {
    const evidenceRes = await fetch(`${apiBase}/projects/${projectId}/land-evidence`, {
      cache: "no-store",
      headers: authHeaders("web"),
    });
    if (!evidenceRes.ok) throw new Error("Unable to load land evidence records.");
    const body = (await evidenceRes.json()) as LandEvidenceList;
    setLandEvidenceRecords(body.records ?? []);
  }, [projectId]);

  const loadAssumptionOverrides = useCallback(async () => {
    const overrideRes = await fetch(`${apiBase}/projects/${projectId}/assumption-overrides`, {
      cache: "no-store",
      headers: authHeaders("web"),
    });
    if (!overrideRes.ok) throw new Error("Unable to load assumption override records.");
    const body = (await overrideRes.json()) as AssumptionOverrideList;
    setAssumptionOverrides(body.records ?? []);
  }, [projectId]);

  const loadRestructuringCures = useCallback(async () => {
    const cureRes = await fetch(`${apiBase}/projects/${projectId}/restructuring-cures`, {
      cache: "no-store",
      headers: authHeaders("web"),
    });
    if (!cureRes.ok) throw new Error("Unable to load restructuring cure records.");
    const body = (await cureRes.json()) as RestructuringCureList;
    setRestructuringCures(body.records ?? []);
  }, [projectId]);

  const loadRunDetail = useCallback(async (runId: string) => {
    const detailRes = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${runId}`, {
      cache: "no-store",
      headers: authHeaders("web"),
    });
    if (!detailRes.ok) throw new Error("Unable to load selected orchestration run.");
    return (await detailRes.json()) as OrchestrationRun;
  }, [projectId]);

  const loadSnapshotDetail = useCallback(async (snapshotId: string) => {
    const detailRes = await fetch(`${apiBase}/projects/${projectId}/snapshots/${snapshotId}`, {
      cache: "no-store",
      headers: authHeaders("web"),
    });
    if (!detailRes.ok) throw new Error("Unable to load selected project snapshot.");
    const detail = (await detailRes.json()) as ProjectSnapshotDetail;
    setSelectedSnapshot(detail);
    setSelectedSnapshotId(detail.snapshotId);
    setOutcomeForm(outcomeFormFromSnapshot(detail));
    setSimilarCaseResult(null);
    setSnapshotDiffResult(null);
    setCompareSnapshotId(detail.snapshotId);
    return detail;
  }, [projectId]);

  const loadSnapshots = useCallback(async (preferredSnapshotId?: string) => {
    const snapshotRes = await fetch(`${apiBase}/projects/${projectId}/snapshots`, {
      cache: "no-store",
      headers: authHeaders("web"),
    });
    if (!snapshotRes.ok) throw new Error("Unable to load project snapshots.");
    const snapshotData = (await snapshotRes.json()) as ProjectSnapshotSummary[];
    const sorted = [...snapshotData].sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""));
    setSnapshots(sorted);
    setBaseSnapshotId((current) => current || sorted[1]?.snapshotId || sorted[0]?.snapshotId || "");
    setCompareSnapshotId((current) => current || preferredSnapshotId || sorted[0]?.snapshotId || "");
    const targetSnapshot =
      (preferredSnapshotId ? sorted.find((snapshot) => snapshot.snapshotId === preferredSnapshotId) : null)
      ?? sorted[0];
    if (targetSnapshot) {
      await loadSnapshotDetail(targetSnapshot.snapshotId);
    } else {
      setSelectedSnapshotId("");
      setSelectedSnapshot(null);
      setOutcomeForm(blankOutcomeForm());
    }
  }, [loadSnapshotDetail, projectId]);

  const selectRun = useCallback(async (runId: string) => {
    const detail = await loadRunDetail(runId);
    setSelectedRun(detail);
    await Promise.all([
      loadMarketEvidence(runId),
      loadLandEvidence(),
      loadAssumptionOverrides(),
      loadRestructuringCures(),
    ]);
  }, [loadAssumptionOverrides, loadLandEvidence, loadMarketEvidence, loadRestructuringCures, loadRunDetail]);

  const loadRuns = useCallback(async (preferredRunId?: string) => {
    setError(null);
    const [projectRes, runsRes] = await Promise.all([
      fetch(`${apiBase}/projects/${projectId}`, { cache: "no-store", headers: authHeaders("web") }),
      fetch(`${apiBase}/projects/${projectId}/orchestrations`, { cache: "no-store", headers: authHeaders("web") }),
    ]);

    if (!projectRes.ok) throw new Error("Unable to load project.");
    if (!runsRes.ok) throw new Error("Unable to load orchestration runs.");

    const projectData = (await projectRes.json()) as ProjectResponse;
    const runData = (await runsRes.json()) as RunSummary[];
    setProject(projectData);
    setRuns(runData);

    const targetRun = preferredRunId
      ? runData.find((run) => run.run_id === preferredRunId)
      : [...runData].sort((a, b) => b.started_at.localeCompare(a.started_at))[0];

    if (targetRun) {
      await selectRun(targetRun.run_id);
    } else {
      setSelectedRun(null);
      setMarketEvidence([]);
      await Promise.all([
        loadLandEvidence().catch(() => setLandEvidenceRecords([])),
        loadAssumptionOverrides().catch(() => setAssumptionOverrides([])),
        loadRestructuringCures().catch(() => setRestructuringCures([])),
      ]);
    }
    await loadSnapshots(targetRun ? `snapshot-${targetRun.run_id}` : undefined);
  }, [loadAssumptionOverrides, loadLandEvidence, loadRestructuringCures, loadSnapshots, projectId, selectRun]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    loadRuns()
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : "Unable to load orchestration data.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [loadRuns]);

  const startRun = async () => {
    setStarting(true);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/orchestrations/pf-review`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders("web") },
        body: JSON.stringify({ options: { requested_by: currentSessionUserId("web") } }),
      });
      if (!res.ok) throw new Error("Unable to start PF review run.");
      const run = (await res.json()) as OrchestrationRun;
      setSelectedRun(run);
      await loadMarketEvidence(run.run_id);
      await loadAssumptionOverrides();
      await loadRestructuringCures();
      await loadRuns(run.run_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to start PF review run.");
    } finally {
      setStarting(false);
    }
  };

  const approveRun = async () => {
    if (!selectedRun) return;
    setApproving(true);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${selectedRun.run_id}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders("web") },
        body: JSON.stringify({
          comment: approvalComment.trim(),
        }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Unable to approve run.");
      }
      const approvedRun = (await res.json()) as OrchestrationRun;
      setSelectedRun(approvedRun);
      setRuns((prev) => prev.map((run) => (
        run.run_id === approvedRun.run_id
          ? { ...run, status: approvedRun.status, approval: approvedRun.approval }
          : run
      )));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to approve run.");
    } finally {
      setApproving(false);
    }
  };

  const reviewEvidence = async (recordKey: string, reviewStatus: "approved" | "rejected" | "needs_revision") => {
    setReviewingKey(recordKey);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/market-evidence/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(evidenceReviewer.trim() || "market_analyst") },
        body: JSON.stringify({
          record_key: recordKey,
          review_status: reviewStatus,
          reviewed_by: evidenceReviewer.trim() || "market_analyst",
          comment: `Evidence marked ${reviewStatus} from orchestration UI.`,
        }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Unable to update evidence review status.");
      }
      const updated = (await res.json()) as MarketEvidenceRecord;
      setMarketEvidence((prev) => prev.map((record) => (
        record.recordKey === updated.recordKey ? updated : record
      )));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to update evidence review status.");
    } finally {
      setReviewingKey(null);
    }
  };

  const reviewHandoff = async (handoffId: string, reviewStatus: "approved" | "rejected" | "needs_revision") => {
    if (!selectedRun) return;
    setReviewingHandoffId(handoffId);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${selectedRun.run_id}/handoffs/${handoffId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(handoffReviewer.trim() || "credit_officer") },
        body: JSON.stringify({
          review_status: reviewStatus,
          reviewed_by: handoffReviewer.trim() || "credit_officer",
          comment: `Handoff marked ${reviewStatus} from orchestration UI.`,
        }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Unable to update handoff review status.");
      }
      const updatedRun = (await res.json()) as OrchestrationRun;
      setSelectedRun(updatedRun);
      setRuns((prev) => prev.map((run) => (
        run.run_id === updatedRun.run_id
          ? { ...run, status: updatedRun.status, approval: updatedRun.approval }
          : run
      )));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to update handoff review status.");
    } finally {
      setReviewingHandoffId(null);
    }
  };

  const reviewRisk = async (riskId: string, reviewStatus: "approved" | "waived" | "needs_revision" | "resolved") => {
    if (!selectedRun) return;
    setReviewingRiskId(riskId);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${selectedRun.run_id}/risk-register/${riskId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders(riskReviewer.trim() || "credit_officer") },
        body: JSON.stringify({
          review_status: reviewStatus,
          reviewed_by: riskReviewer.trim() || "credit_officer",
          comment: `Risk row marked ${reviewStatus} from orchestration UI.`,
          waiver_reason: reviewStatus === "waived" ? "Reviewer waiver from orchestration UI." : undefined,
        }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Unable to update risk review status.");
      }
      const updatedRun = (await res.json()) as OrchestrationRun;
      setSelectedRun(updatedRun);
      setRuns((prev) => prev.map((run) => (
        run.run_id === updatedRun.run_id
          ? { ...run, status: updatedRun.status, approval: updatedRun.approval }
          : run
      )));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to update risk review status.");
    } finally {
      setReviewingRiskId(null);
    }
  };

  const saveAssumptionOverride = async (payload: Record<string, unknown>) => {
    if (!selectedRun) return;
    const checkKey = String(payload.checkKey ?? "");
    setSavingOverrideKey(checkKey);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/assumption-overrides`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders(String(payload.approvedBy ?? "credit_officer")),
        },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Unable to save assumption override.");
      }
      setNotice("기준값 승인이 저장되었습니다. 같은 자료로 PF Review를 다시 실행하면 Data Quality Agent가 승인 기준값을 반영합니다.");
      await loadAssumptionOverrides();
      await loadRuns(selectedRun.run_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save assumption override.");
    } finally {
      setSavingOverrideKey(null);
    }
  };

  const updateAssumptionOverrideStatus = async (
    override: AssumptionOverrideRecord,
    status: "revoked" | "expired" | "active",
  ) => {
    setUpdatingOverrideId(override.overrideId);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/assumption-overrides`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders(riskReviewer || "credit_officer"),
        },
        body: JSON.stringify({
          ...override,
          status,
          statusReason:
            status === "revoked"
              ? "Reviewer revoked the approved assumption override from the orchestration UI."
              : status === "expired"
                ? "Reviewer expired the approved assumption override from the orchestration UI."
                : "Reviewer reactivated the approved assumption override from the orchestration UI.",
          statusChangedBy: riskReviewer || "credit_officer",
          statusChangedAt: new Date().toISOString(),
          expiresAt: status === "expired" ? new Date().toISOString() : override.expiresAt,
        }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Unable to update assumption override status.");
      }
      setNotice(`기준값 override가 ${status} 상태로 변경되었습니다. PF Review를 다시 실행하면 변경된 상태가 반영됩니다.`);
      await loadAssumptionOverrides();
      if (selectedRun) {
        await loadRuns(selectedRun.run_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to update assumption override status.");
    } finally {
      setUpdatingOverrideId(null);
    }
  };

  const saveLandAcquisitionEvidence = async (payload: Record<string, unknown>) => {
    setSavingLandEvidence(true);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/land-evidence`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders(String(payload.reviewedBy ?? "land_analyst")),
        },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Unable to save land evidence.");
      }
      setNotice("토지 취득·권원 증빙이 저장되었습니다. PF Review를 다시 실행하면 Land Evidence Gate가 최신 증빙을 반영합니다.");
      await loadLandEvidence();
      if (selectedRun) {
        await loadRuns(selectedRun.run_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save land evidence.");
    } finally {
      setSavingLandEvidence(false);
    }
  };

  const saveRestructuringCure = async (payload: RestructuringCureRecord) => {
    setSavingRestructuringCure(true);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/restructuring-cures`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders(payload.reviewedBy || riskReviewer || "credit_officer"),
        },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Unable to save restructuring cure.");
      }
      setNotice("금융구조 보강안이 저장되었습니다. PF Review를 다시 실행하면 채택 보강안으로 기록되며, 증빙과 계산 결과가 함께 충족될 때 승인 readiness에 반영됩니다.");
      await loadRestructuringCures();
      if (selectedRun) {
        await loadRuns(selectedRun.run_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save restructuring cure.");
    } finally {
      setSavingRestructuringCure(false);
    }
  };

  const saveHistoricalOutcome = async () => {
    if (!selectedSnapshotId) return;
    setSavingOutcome(true);
    setError(null);
    try {
      let sourceLocators: Array<Record<string, unknown>> = [];
      const locatorText = outcomeForm.sourceLocatorsJson.trim();
      if (locatorText) {
        const parsed = JSON.parse(locatorText) as unknown;
        if (!Array.isArray(parsed)) {
          throw new Error("Source locators must be a JSON array.");
        }
        sourceLocators = parsed as Array<Record<string, unknown>>;
      }

      const payload = {
        actualSalesRate: rateOrUndefined(outcomeForm.actualSalesRate),
        actualDiscountRate: rateOrUndefined(outcomeForm.actualDiscountRate),
        repaymentCompleted: outcomeForm.repaymentCompleted,
        repaymentCompletionDate: outcomeForm.repaymentCompletionDate || undefined,
        extensionOccurred: outcomeForm.extensionOccurred,
        extensionCount: numberOrUndefined(outcomeForm.extensionCount),
        eodOccurred: outcomeForm.eodOccurred,
        eodDate: outcomeForm.eodDate || undefined,
        lossOccurred: outcomeForm.lossOccurred,
        lossAmount: numberOrUndefined(outcomeForm.lossAmount),
        recoveryRate: rateOrUndefined(outcomeForm.recoveryRate),
        seniorRecoveryRate: rateOrUndefined(outcomeForm.seniorRecoveryRate),
        mezzanineRecoveryRate: rateOrUndefined(outcomeForm.mezzanineRecoveryRate),
        juniorRecoveryRate: rateOrUndefined(outcomeForm.juniorRecoveryRate),
        actualExitSource: outcomeForm.actualExitSource || undefined,
        outcomeMemo: outcomeForm.outcomeMemo || undefined,
        sourceMetadata: {
          sourceName: outcomeForm.sourceName || "manual outcome review",
          sourceType: outcomeForm.sourceType || "manual_review",
          baseDate: outcomeForm.baseDate || undefined,
          confidence: rateOrUndefined(outcomeForm.confidence),
        },
        sourceLocators,
      };

      const res = await fetch(`${apiBase}/projects/${projectId}/snapshots/${selectedSnapshotId}/outcome`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders(outcomeUser.trim() || "web"),
        },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Unable to save historical outcome.");
      }
      const saved = (await res.json()) as { snapshot: ProjectSnapshotDetail; historicalOutcome: HistoricalOutcome };
      setSelectedSnapshot(saved.snapshot);
      setOutcomeForm(outcomeFormFromSnapshot(saved.snapshot));
      await loadSnapshots(saved.snapshot.snapshotId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save historical outcome.");
    } finally {
      setSavingOutcome(false);
    }
  };

  const searchSimilarCases = async () => {
    if (!selectedSnapshotId) return;
    setSearchingSimilarCases(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/snapshots/similar`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders("web") },
        body: JSON.stringify({
          snapshotId: selectedSnapshotId,
          limit: Math.max(1, Math.min(20, numberOrUndefined(similarLimit) ?? 5)),
          minScore: rateOrUndefined(similarMinScore) ?? 0,
          includeSameProject: includeSameProjectCases,
        }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Unable to search historical similar cases.");
      }
      setSimilarCaseResult((await res.json()) as SimilarCaseSearchResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to search historical similar cases.");
    } finally {
      setSearchingSimilarCases(false);
    }
  };

  const runSnapshotDiff = async () => {
    if (!baseSnapshotId || !compareSnapshotId) return;
    if (baseSnapshotId === compareSnapshotId) {
      setError("Choose two different snapshots for diff.");
      return;
    }
    setDiffingSnapshots(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/snapshots/diff`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders("web") },
        body: JSON.stringify({
          baseSnapshotId,
          compareSnapshotId,
        }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Unable to diff project snapshots.");
      }
      setSnapshotDiffResult((await res.json()) as SnapshotDiffResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to diff project snapshots.");
    } finally {
      setDiffingSnapshots(false);
    }
  };

  const unresolvedHandoffCount = useMemo(() => (
    (selectedRun?.handoffs ?? []).filter((handoff) => (
      handoff.reviewStatus === "rejected"
      || handoff.reviewStatus === "needs_revision"
      || (handoff.requiresHumanReview && handoff.reviewStatus !== "approved")
      || ((handoff.status === "blocked" || handoff.status === "needs_review") && handoff.reviewStatus !== "approved")
    )).length
  ), [selectedRun]);
  const unresolvedRiskCount = useMemo(() => (
    (selectedRun?.artifacts.riskRegister?.risks ?? []).filter((risk) => (
      risk.requiresHumanReview
      && !["approved", "waived", "resolved"].includes(risk.reviewStatus ?? risk.status ?? "open")
    )).length
  ), [selectedRun]);
  const canApprove = (
    (selectedRun?.status === "completed" || selectedRun?.status === "completed_with_warnings")
    && selectedRun?.artifacts.approvalReadinessGate?.pass === true
    && unresolvedHandoffCount === 0
    && unresolvedRiskCount === 0
  );
  const progressItems = useMemo(
    () => Object.values(selectedRun?.agentProgress ?? {}),
    [selectedRun],
  );
  const completedProgressCount = progressItems.filter((item) => item.status === "completed").length;
  const handoffCount = selectedRun?.handoffs?.length ?? 0;

  const approvalReadinessTone = selectedRun?.artifacts.approvalReadinessGate?.pass
    ? "text-emerald-600"
    : selectedRun?.artifacts.approvalReadinessGate
      ? "text-rose-600"
      : "text-slate-500";

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <ProjectWorkspaceLink projectId={projectId} path="chief-orchestration"
            className="inline-flex min-h-9 items-center gap-2 text-sm font-medium text-re-secondary hover:text-re-action">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            AI 딜룸으로 돌아가기
          </ProjectWorkspaceLink>
          <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            AI Orchestration
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Run PF review agents, inspect artifacts, review market evidence, and approve completed runs.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void loadRuns(selectedRun?.run_id)}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          >
            <RefreshCw className="h-4 w-4" />
            Refresh
          </button>
          <button
            type="button"
            onClick={() => void startRun()}
            disabled={starting}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-white dark:text-slate-950"
          >
            {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            Run PF Review
          </button>
        </div>
      </div>

      <OrchestrationToolbar
        projectId={projectId}
        onRefresh={() => void loadRuns(selectedRun?.run_id)}
      />

      {error ? (
        <div className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm font-semibold text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm font-semibold text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300">
          <CheckCircle2 className="h-4 w-4" />
          {notice}
        </div>
      ) : null}

      {loading ? (
        <div className="flex h-56 items-center justify-center rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <Loader2 className="h-6 w-6 animate-spin text-slate-500" />
        </div>
      ) : (
        <>
          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-6">
            <Kpi label="Runs" value={`${runs.length}`} />
            <Kpi label="Current 상태" value={selectedRun?.status ?? "-"} />
            <Kpi label="Agents" value={selectedRun ? `${completedProgressCount}/${progressItems.length}` : "-"} />
            <Kpi label="Handoffs" value={selectedRun ? `${handoffCount}` : "-"} />
            <Kpi label="Open Reviews" value={selectedRun ? `${unresolvedHandoffCount}` : "-"} tone={unresolvedHandoffCount > 0 ? "text-amber-600" : "text-emerald-600"} />
            <Kpi label="Approval Ready" value={selectedRun?.artifacts.approvalReadinessGate?.pass ? "Ready" : selectedRun ? "Blocked" : "-"} tone={approvalReadinessTone} />
          </section>

          <section className="grid gap-4 xl:grid-cols-[360px_1fr]">
            <div className="space-y-3">
              <h3 className="text-lg font-bold text-slate-950 dark:text-white">Run History</h3>
              {runs.length === 0 ? (
                <div className="rounded-lg border border-dashed border-slate-300 p-6 text-sm text-slate-500 dark:border-slate-700">
                  No orchestration runs yet.
                </div>
              ) : (
                runs.map((run) => (
                  <button
                    key={run.run_id}
                    type="button"
                    onClick={() => void selectRun(run.run_id)}
                    className={`w-full rounded-lg border p-4 text-left transition-colors ${
                      selectedRun?.run_id === run.run_id
                        ? "border-slate-950 bg-slate-50 dark:border-white dark:bg-slate-900"
                        : "border-slate-200 bg-white hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-950 dark:hover:bg-slate-900"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <p className="truncate text-sm font-bold text-slate-950 dark:text-white">{run.workflow}</p>
                      <StatusBadge status={run.status} />
                    </div>
                    <p className="mt-2 text-xs text-slate-500">{fmtDate(run.started_at)}</p>
                    <p className="mt-1 text-xs text-slate-500">{run.step_count} steps</p>
                  </button>
                ))
              )}
            </div>

            <div className="space-y-4">
              {selectedRun ? (
                <>
                  <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-xs font-semibold uppercase text-slate-500">Selected Run</p>
                        <h3 className="mt-1 text-lg font-bold text-slate-950 dark:text-white">{selectedRun.run_id}</h3>
                        <p className="mt-1 text-sm text-slate-500">{project.project?.name ?? "Project"}</p>
                      </div>
                      <StatusBadge status={selectedRun.status} />
                    </div>
                    <div className="mt-4 grid gap-3 md:grid-cols-3">
                      <Mini label="Started" value={fmtDate(selectedRun.started_at)} />
                      <Mini label="Completed" value={fmtDate(selectedRun.completed_at)} />
                      <Mini label="Approved By" value={selectedRun.approval ? selectedRun.approval.approved_by : "-"} />
                    </div>
                  </section>

                  <ApprovalPanel
                    approval={selectedRun.approval}
                    approvalReadinessGate={selectedRun.artifacts.approvalReadinessGate}
                    canApprove={canApprove}
                    unresolvedHandoffCount={unresolvedHandoffCount}
                    unresolvedRiskCount={unresolvedRiskCount}
                    status={selectedRun.status}
                    approvalComment={approvalComment}
                    approving={approving}
                    onApprovalCommentChange={setApprovalComment}
                    onApprove={() => void approveRun()}
                  />

                  <ApprovalReadinessDashboard
                    gate={selectedRun.artifacts.approvalReadinessGate}
                    riskRegister={selectedRun.artifacts.riskRegister}
                    creditReviewDecision={selectedRun.artifacts.creditReviewDecision}
                    handoffs={selectedRun.handoffs ?? []}
                  />

                  <RestructuringCurePanel
                    plan={selectedRun.artifacts.restructuringPlan}
                    cures={restructuringCures}
                    runId={selectedRun.run_id}
                    reviewer={riskReviewer}
                    saving={savingRestructuringCure}
                    onReviewerChange={setRiskReviewer}
                    onSave={(payload) => void saveRestructuringCure(payload)}
                  />

                  <LandEvidenceReadinessPanel
                    review={selectedRun.artifacts.landAgentReview}
                    gate={selectedRun.artifacts.approvalReadinessGate}
                    records={landEvidenceRecords}
                    reviewer={riskReviewer}
                    saving={savingLandEvidence}
                    onReviewerChange={setRiskReviewer}
                    onSave={(payload) => void saveLandAcquisitionEvidence(payload)}
                  />

                  {selectedRun.artifacts.dataQualityReview ? (
                    <DataQualityOverridePanel
                      review={selectedRun.artifacts.dataQualityReview}
                      overrides={assumptionOverrides}
                      reviewer={riskReviewer}
                      savingKey={savingOverrideKey}
                      updatingOverrideId={updatingOverrideId}
                      onReviewerChange={setRiskReviewer}
                      onSaveOverride={(payload) => void saveAssumptionOverride(payload)}
                      onUpdateOverrideStatus={(override, status) => void updateAssumptionOverrideStatus(override, status)}
                    />
                  ) : null}

                  <HistoricalOutcomePanel
                    snapshots={snapshots}
                    selectedSnapshot={selectedSnapshot}
                    selectedSnapshotId={selectedSnapshotId}
                    form={outcomeForm}
                    saving={savingOutcome}
                    outcomeUser={outcomeUser}
                    onSelectSnapshot={(snapshotId) => void loadSnapshotDetail(snapshotId)}
                    onFormChange={(patch) => setOutcomeForm((prev) => ({ ...prev, ...patch }))}
                    onOutcomeUserChange={setOutcomeUser}
                    onSave={() => void saveHistoricalOutcome()}
                  />

                  <HistoricalSimilarCasesPanel
                    snapshots={snapshots}
                    selectedSnapshotId={selectedSnapshotId}
                    result={similarCaseResult}
                    searching={searchingSimilarCases}
                    limit={similarLimit}
                    minScore={similarMinScore}
                    includeSameProject={includeSameProjectCases}
                    onSelectSnapshot={(snapshotId) => void loadSnapshotDetail(snapshotId)}
                    onLimitChange={setSimilarLimit}
                    onMinScoreChange={setSimilarMinScore}
                    onIncludeSameProjectChange={setIncludeSameProjectCases}
                    onSearch={() => void searchSimilarCases()}
                  />

                  <SnapshotDiffPanel
                    snapshots={snapshots}
                    baseSnapshotId={baseSnapshotId}
                    compareSnapshotId={compareSnapshotId}
                    result={snapshotDiffResult}
                    diffing={diffingSnapshots}
                    onBaseSnapshotChange={setBaseSnapshotId}
                    onCompareSnapshotChange={setCompareSnapshotId}
                    onDiff={() => void runSnapshotDiff()}
                  />

                  <AgentProgressPanel progressItems={progressItems} />

                  <AgentHandoffPanel
                    handoffs={selectedRun.handoffs ?? []}
                    reviewer={handoffReviewer}
                    reviewingHandoffId={reviewingHandoffId}
                    onReviewerChange={setHandoffReviewer}
                    onReview={(handoffId, status) => void reviewHandoff(handoffId, status)}
                  />

                  {selectedRun.artifacts.riskRegister ? (
                    <RiskRegisterPanel
                      register={selectedRun.artifacts.riskRegister}
                      reviewer={riskReviewer}
                      reviewingRiskId={reviewingRiskId}
                      onReviewerChange={setRiskReviewer}
                      onReview={(riskId, status) => void reviewRisk(riskId, status)}
                    />
                  ) : null}

                  {selectedRun.artifacts.creditReviewDecision ? (
                    <CreditReviewPanel decision={selectedRun.artifacts.creditReviewDecision} />
                  ) : null}

                  {selectedRun.artifacts.scenariosEvaluation ? (
                    <SensitivityMatricesPanel evaluation={selectedRun.artifacts.scenariosEvaluation} />
                  ) : null}

                  <section className="grid gap-4 lg:grid-cols-3">
                    <ArtifactPanel
                      icon={<GitBranch className="h-5 w-5 text-indigo-600" />}
                      title="Feasibility"
                      rows={[
                        ["Development Profit", fmtEok(selectedRun.artifacts.developerFeasibility?.developerReturns.developmentProfit)],
                        ["Profit Margin", fmtPercent(selectedRun.artifacts.developerFeasibility?.developerReturns.profitMargin)],
                        ["PF 상태", selectedRun.artifacts.developerFeasibility?.pfReadiness.status ?? "-"],
                      ]}
                    />
                    <ArtifactPanel
                      icon={<ShieldCheck className="h-5 w-5 text-emerald-600" />}
                      title="Market / Legal"
                      rows={[
                        ["Market Evidence", selectedRun.artifacts.marketEvidenceReview?.status ?? "-"],
                        ["Marketability", selectedRun.artifacts.marketabilityAnalysis?.applicable ? `${selectedRun.artifacts.marketabilityAnalysis.grade} / ${selectedRun.artifacts.marketabilityAnalysis.score}` : "N/A"],
                        ["Legal Review", selectedRun.artifacts.legalPermitReview?.status ?? "-"],
                      ]}
                    />
                    <ArtifactPanel
                      icon={<FileText className="h-5 w-5 text-sky-600" />}
                      title="Report"
                      rows={[
                        ["Report", selectedRun.artifacts.reportRecord?.title ?? "-"],
                        ["Sections", selectedRun.artifacts.reportRecord ? `${selectedRun.artifacts.reportRecord.metadata.section_count}` : "-"],
                        ["Complete", selectedRun.artifacts.reportRecord?.metadata.complete ? "Complete" : "-"],
                      ]}
                    />
                  </section>

                  <MarketEvidencePanel
                    records={marketEvidence}
                    reviewer={evidenceReviewer}
                    reviewingKey={reviewingKey}
                    onReviewerChange={setEvidenceReviewer}
                    onReview={(recordKey, status) => void reviewEvidence(recordKey, status)}
                  />

                  <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                    <h3 className="text-lg font-bold text-slate-950 dark:text-white">Run Steps</h3>
                    <div className="mt-4 space-y-3">
                      {selectedRun.steps.map((step, idx) => (
                        <div key={`${step.key}-${idx}`} className="flex gap-3 rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
                          <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
                            <CheckCircle2 className="h-4 w-4" />
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <p className="font-bold text-slate-950 dark:text-white">{step.title}</p>
                              <p className="text-xs text-slate-500">{fmtDate(step.completed_at)}</p>
                            </div>
                            <p className="mt-1 text-xs text-slate-500">{step.key}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                </>
              ) : (
                <div className="rounded-lg border border-dashed border-slate-300 p-10 text-center text-sm text-slate-500 dark:border-slate-700">
                  Select a run from the left or start a new PF review.
                </div>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function AgentProgressPanel({ progressItems }: { progressItems: AgentProgress[] }) {
  const sorted = [...progressItems].sort((a, b) => a.agentKey.localeCompare(b.agentKey));
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2">
        <Clock3 className="h-5 w-5 text-cyan-600" />
        <div>
          <h3 className="font-bold text-slate-950 dark:text-white">Agent Progress</h3>
          <p className="text-sm text-slate-500">Track completion, dependencies, artifacts, and review gates for each agent step.</p>
        </div>
      </div>

      {sorted.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-slate-300 p-6 text-sm text-slate-500 dark:border-slate-700">
          This run does not include agent progress records yet.
        </div>
      ) : (
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          {sorted.map((item) => (
            <div key={item.agentKey} className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate font-bold text-slate-950 dark:text-white">{item.agentName}</p>
                  <p className="mt-1 text-xs text-slate-500">{item.agentKey}</p>
                </div>
                <StatusBadge status={item.reviewRequired ? "review_required" : item.status} />
              </div>
              <div className="mt-4">
                <div className="flex items-center justify-between text-xs font-semibold text-slate-500">
                  <span>Progress</span>
                  <span>{item.progressPercent}%</span>
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                  <div
                    className="h-full rounded-full bg-cyan-600"
                    style={{ width: `${Math.min(100, Math.max(0, item.progressPercent))}%` }}
                  />
                </div>
              </div>
              <div className="mt-4 grid gap-2 text-xs text-slate-500 md:grid-cols-2">
                <Mini label="Artifact" value={item.lastArtifactKey ?? "-"} />
                <Mini label="Handoffs" value={`${item.handoffIds.length}`} />
              </div>
              <p className="mt-3 text-sm text-slate-600 dark:text-slate-300">{item.nextAction ?? "-"}</p>
              {item.blockedTasks.length > 0 ? (
                <div className="mt-3 rounded-lg bg-rose-50 p-3 text-xs font-semibold text-rose-700 dark:bg-rose-950/30 dark:text-rose-300">
                  {item.blockedTasks.join(" / ")}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function AgentHandoffPanel({
  handoffs,
  reviewer,
  reviewingHandoffId,
  onReviewerChange,
  onReview,
}: {
  handoffs: AgentHandoff[];
  reviewer: string;
  reviewingHandoffId: string | null;
  onReviewerChange: (value: string) => void;
  onReview: (handoffId: string, status: "approved" | "rejected" | "needs_revision") => void;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="flex items-center gap-2">
          <MessageSquareText className="h-5 w-5 text-fuchsia-600" />
          <div>
            <h3 className="font-bold text-slate-950 dark:text-white">Agent Handoffs</h3>
            <p className="text-sm text-slate-500">Typed patches and evidence passed between agents during the selected run.</p>
          </div>
        </div>
        <label className="block min-w-48">
          <span className="text-xs font-semibold text-slate-500">Reviewer</span>
          <input
            value={reviewer}
            onChange={(event) => onReviewerChange(event.target.value)}
            className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
          />
        </label>
      </div>

      {handoffs.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-slate-300 p-6 text-sm text-slate-500 dark:border-slate-700">
          This run does not include typed handoff records yet.
        </div>
      ) : (
        <div className="mt-4 space-y-3">
          {handoffs.map((handoff) => (
            <div key={handoff.handoffId} className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
              <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-bold text-slate-950 dark:text-white">{handoff.handoffType}</p>
                    <StatusBadge status={handoff.reviewStatus ?? handoff.status} />
                    {handoff.requiresHumanReview ? <StatusBadge status="human_review" /> : null}
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
                    <span className="font-semibold">{handoff.fromAgent}</span>
                    <ArrowRight className="h-4 w-4 text-slate-400" />
                    <span className="font-semibold">{handoff.toAgent}</span>
                  </div>
                  <p className="mt-2 text-xs text-slate-500">
                    {handoff.payloadSchema ?? "No payload schema"} / {fmtDate(handoff.createdAt)}
                  </p>
                </div>
                <div className="grid min-w-56 grid-cols-3 gap-2">
                  <Mini label="Confidence" value={handoff.confidence === undefined ? "-" : fmtPercent(handoff.confidence)} />
                  <Mini label="Evidence" value={`${handoff.evidenceIds.length}`} />
                  <Mini label="Blockers" value={`${handoff.blockingIssues.length}`} />
                </div>
              </div>

              <div className="mt-4 grid gap-3 lg:grid-cols-3">
                <HandoffDetail title="Payload Preview" value={handoffPayloadPreview(handoff.payload)} />
                <HandoffDetail title="Data Limitations" value={handoff.dataLimitations.length ? handoff.dataLimitations.join(" / ") : "-"} />
                <HandoffDetail title="Assumptions" value={handoff.assumptions.length ? handoff.assumptions.join(" / ") : "-"} />
              </div>

              <div className="mt-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="text-xs text-slate-500">
                  <span className="font-semibold">Last review:</span>{" "}
                  {handoff.reviewedBy ? `${handoff.reviewedBy} / ${fmtDate(handoff.reviewedAt)}` : "-"}
                  {handoff.reviewComment ? ` / ${handoff.reviewComment}` : ""}
                </div>
                <div className="flex justify-end gap-2">
                  <IconButton
                    label="Approve handoff"
                    disabled={reviewingHandoffId === handoff.handoffId}
                    onClick={() => onReview(handoff.handoffId, "approved")}
                    icon={reviewingHandoffId === handoff.handoffId ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                    tone="approve"
                  />
                  <IconButton
                    label="Needs revision"
                    disabled={reviewingHandoffId === handoff.handoffId}
                    onClick={() => onReview(handoff.handoffId, "needs_revision")}
                    icon={<AlertCircle className="h-4 w-4" />}
                    tone="warn"
                  />
                  <IconButton
                    label="Reject handoff"
                    disabled={reviewingHandoffId === handoff.handoffId}
                    onClick={() => onReview(handoff.handoffId, "rejected")}
                    icon={<XCircle className="h-4 w-4" />}
                    tone="reject"
                  />
                </div>
              </div>

              {handoff.blockingIssues.length > 0 ? (
                <div className="mt-3 rounded-lg bg-amber-50 p-3 text-xs font-semibold text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
                  {handoff.blockingIssues.join(" / ")}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function HandoffDetail({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
      <p className="text-xs font-semibold text-slate-500">{title}</p>
      <p className="mt-2 break-words text-sm font-semibold text-slate-800 dark:text-slate-200">{value}</p>
    </div>
  );
}

function handoffPayloadPreview(payload: Record<string, unknown>) {
  const keys = Object.keys(payload);
  if (keys.length === 0) return "-";
  return keys.slice(0, 5).join(", ");
}

function RiskRegisterPanel({
  register,
  reviewer,
  reviewingRiskId,
  onReviewerChange,
  onReview,
}: {
  register: RiskRegister;
  reviewer: string;
  reviewingRiskId: string | null;
  onReviewerChange: (value: string) => void;
  onReview: (riskId: string, status: "approved" | "waived" | "needs_revision" | "resolved") => void;
}) {
  const risks = (register.risks?.length ? register.risks : register.topRisks) ?? [];
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="flex items-center gap-2">
          <AlertCircle className="h-5 w-5 text-amber-600" />
          <div>
            <h3 className="font-bold text-slate-950 dark:text-white">Risk Register</h3>
            <p className="text-sm text-slate-500">Consolidated risks for credit review and approval-condition drafting.</p>
          </div>
        </div>
        <div className="flex flex-col gap-2 sm:min-w-56">
          <StatusBadge status={register.status} />
          <label className="block">
            <span className="text-xs font-semibold text-slate-500">Risk Reviewer</span>
            <input
              value={reviewer}
              onChange={(event) => onReviewerChange(event.target.value)}
              className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
            />
          </label>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-5">
        <Mini label="Risks" value={`${register.summary.riskCount}`} />
        <Mini label="Critical" value={`${register.summary.criticalCount}`} />
        <Mini label="High" value={`${register.summary.highCount}`} />
        <Mini label="Medium" value={`${register.summary.mediumCount}`} />
        <Mini label="Unresolved" value={`${register.reviewSummary?.unresolvedReviewCount ?? register.summary.humanReviewRequiredCount}`} />
      </div>

      {risks.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-slate-300 p-6 text-sm text-slate-500 dark:border-slate-700">
          No material risk rows were generated for this run.
        </div>
      ) : (
        <div className="mt-4 overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
          <div className="grid grid-cols-[0.7fr_1fr_1.1fr_0.8fr_1.35fr_1.2fr] bg-slate-50 px-4 py-2 text-xs font-bold uppercase text-slate-500 dark:bg-slate-800/70">
            <span>ID</span>
            <span>Source</span>
            <span>Risk</span>
            <span>상태</span>
            <span>Mitigant</span>
            <span className="text-right">Actions</span>
          </div>
          <div className="divide-y divide-slate-200 dark:divide-slate-800">
            {risks.map((risk) => (
              <div key={risk.riskId} className="grid grid-cols-[0.7fr_1fr_1.1fr_0.8fr_1.35fr_1.2fr] gap-3 px-4 py-3 text-sm">
                <div>
                  <p className="font-bold text-slate-950 dark:text-white">{risk.riskId}</p>
                  <p className="mt-1 text-xs text-slate-500">{risk.category}</p>
                </div>
                <div className="min-w-0">
                  <p className="truncate font-semibold text-slate-800 dark:text-slate-200">{risk.sourceAgent}</p>
                  <p className="mt-1 truncate text-xs text-slate-500">{risk.sourceArtifact ?? risk.code}</p>
                  {risk.evidenceGraphLinks?.riskNodeId ? (
                    <p className="mt-1 truncate text-xs font-semibold text-sky-700 dark:text-sky-300">
                      Graph {risk.evidenceGraphLinks.claimNodeIds?.length ?? 0} claims / {risk.evidenceGraphLinks.evidenceNodeIds?.length ?? 0} evidence / {risk.evidenceGraphLinks.sourceLocatorNodeIds?.length ?? 0} locators
                    </p>
                  ) : (
                    <p className="mt-1 truncate text-xs text-slate-400">Graph link pending</p>
                  )}
                </div>
                <div className="min-w-0">
                  <p className="truncate font-bold text-slate-950 dark:text-white">{risk.title}</p>
                  <p className="mt-1 text-xs text-slate-500">
                    {risk.severity} / {risk.probability} / score {risk.score}
                  </p>
                </div>
                <div>
                  <StatusBadge status={risk.grade} />
                  <div className="mt-1">
                    <StatusBadge status={risk.reviewStatus ?? risk.status ?? "open"} />
                  </div>
                  <p className="mt-1 text-xs text-slate-500">{risk.reviewedBy ? `${risk.reviewedBy} / ${fmtDate(risk.reviewedAt ?? undefined)}` : "-"}</p>
                </div>
                <p className="text-xs font-semibold leading-5 text-slate-600 dark:text-slate-300">{risk.mitigant}</p>
                <div className="flex flex-wrap justify-end gap-2">
                  <IconButton
                    label="Approve risk"
                    disabled={reviewingRiskId === risk.riskId}
                    onClick={() => onReview(risk.riskId, "approved")}
                    icon={reviewingRiskId === risk.riskId ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                    tone="approve"
                  />
                  <IconButton
                    label="Waive risk"
                    disabled={reviewingRiskId === risk.riskId}
                    onClick={() => onReview(risk.riskId, "waived")}
                    icon={<ShieldCheck className="h-4 w-4" />}
                    tone="warn"
                  />
                  <IconButton
                    label="Resolve risk"
                    disabled={reviewingRiskId === risk.riskId}
                    onClick={() => onReview(risk.riskId, "resolved")}
                    icon={<CheckCircle2 className="h-4 w-4" />}
                    tone="approve"
                  />
                  <IconButton
                    label="Needs revision"
                    disabled={reviewingRiskId === risk.riskId}
                    onClick={() => onReview(risk.riskId, "needs_revision")}
                    icon={<AlertCircle className="h-4 w-4" />}
                    tone="reject"
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {register.approvalConditions.length > 0 ? (
        <div className="mt-4 rounded-lg bg-amber-50 p-4 dark:bg-amber-950/30">
          <p className="text-xs font-bold uppercase text-amber-700 dark:text-amber-300">Approval Condition Candidates</p>
          <ul className="mt-2 space-y-1 text-sm font-semibold text-amber-900 dark:text-amber-100">
            {register.approvalConditions.slice(0, 5).map((condition) => (
              <li key={condition}>{condition}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function CreditReviewPanel({ decision }: { decision: CreditReviewDecision }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="flex items-center gap-2">
          <UserCheck className="h-5 w-5 text-emerald-600" />
          <div>
            <h3 className="font-bold text-slate-950 dark:text-white">Credit Review Decision</h3>
            <p className="text-sm text-slate-500">Lender-side decision guidance generated from the consolidated risk register.</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={decision.status} />
          <StatusBadge status={decision.decisionLabel} />
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-5">
        <Mini label="Confidence" value={fmtPercent(decision.confidence)} />
        <Mini label="Risks" value={`${decision.summary.riskCount}`} />
        <Mini label="Critical" value={`${decision.summary.criticalRiskCount}`} />
        <Mini label="High" value={`${decision.summary.highRiskCount}`} />
        <Mini label="Human Review" value={decision.humanReview.required ? "Required" : "Not required"} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <div className="rounded-lg bg-slate-50 p-4 dark:bg-slate-800/70">
          <p className="text-xs font-bold uppercase text-slate-500">Decision Reasons</p>
          <ul className="mt-2 space-y-1 text-sm font-semibold text-slate-800 dark:text-slate-200">
            {decision.decisionReasons.slice(0, 5).map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
        <div className="rounded-lg bg-amber-50 p-4 dark:bg-amber-950/30">
          <p className="text-xs font-bold uppercase text-amber-700 dark:text-amber-300">Required Follow-ups</p>
          <ul className="mt-2 space-y-1 text-sm font-semibold text-amber-900 dark:text-amber-100">
            {(decision.requiredFollowUps.length ? decision.requiredFollowUps : ["No mandatory follow-up."]).slice(0, 5).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
        <div className="rounded-lg bg-emerald-50 p-4 dark:bg-emerald-950/30">
          <p className="text-xs font-bold uppercase text-emerald-700 dark:text-emerald-300">Approval Conditions</p>
          <ul className="mt-2 space-y-1 text-sm font-semibold text-emerald-900 dark:text-emerald-100">
            {(decision.approvalConditions.length ? decision.approvalConditions : ["Standard monitoring conditions only."]).slice(0, 5).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      </div>

      {decision.reportParagraphs.creditOpinion ? (
        <div className="mt-4 rounded-lg border border-slate-200 p-4 text-sm font-semibold leading-6 text-slate-700 dark:border-slate-800 dark:text-slate-200">
          {decision.reportParagraphs.creditOpinion}
        </div>
      ) : null}
    </section>
  );
}

function MarketEvidencePanel({
  records,
  reviewer,
  reviewingKey,
  onReviewerChange,
  onReview,
}: {
  records: MarketEvidenceRecord[];
  reviewer: string;
  reviewingKey: string | null;
  onReviewerChange: (value: string) => void;
  onReview: (recordKey: string, status: "approved" | "rejected" | "needs_revision") => void;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="flex items-center gap-2">
          <Database className="h-5 w-5 text-violet-600" />
          <div>
            <h3 className="font-bold text-slate-950 dark:text-white">Market Evidence Review</h3>
            <p className="text-sm text-slate-500">Review persisted evidence before relying on pricing, rent, or absorption conclusions.</p>
          </div>
        </div>
        <label className="block min-w-48">
          <span className="text-xs font-semibold text-slate-500">Reviewer</span>
          <input
            value={reviewer}
            onChange={(event) => onReviewerChange(event.target.value)}
            className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
          />
        </label>
      </div>

      {records.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-slate-300 p-6 text-sm text-slate-500 dark:border-slate-700">
          No market evidence records were persisted for this run.
        </div>
      ) : (
        <div className="mt-4 overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
          <div className="grid grid-cols-[1.1fr_1fr_1fr_1fr_1.3fr] bg-slate-50 px-4 py-2 text-xs font-bold uppercase text-slate-500 dark:bg-slate-800/70">
            <span>Evidence</span>
            <span>Source</span>
            <span>Validation</span>
            <span>Review</span>
            <span className="text-right">Actions</span>
          </div>
          <div className="divide-y divide-slate-200 dark:divide-slate-800">
            {records.map((record) => (
              <div key={record.recordKey} className="grid grid-cols-[1.1fr_1fr_1fr_1fr_1.3fr] gap-3 px-4 py-3 text-sm">
                <div className="min-w-0">
                  <p className="truncate font-bold text-slate-950 dark:text-white">{record.evidenceId}</p>
                  <p className="mt-1 text-xs text-slate-500">{record.evidenceType}</p>
                  <p className="mt-1 truncate text-xs text-slate-500">{record.relatedCheckKeys?.join(", ") ?? "-"}</p>
                </div>
                <div className="min-w-0">
                  <p className="truncate font-semibold text-slate-700 dark:text-slate-200">{record.sourceName ?? "-"}</p>
                  <p className="mt-1 truncate text-xs text-slate-500">{record.sourceUrlOrEvidenceId ?? record.sourceType ?? "-"}</p>
                </div>
                <div>
                  <StatusBadge status={record.validationStatus ?? "not_checked"} />
                  <p className="mt-2 text-xs text-slate-500">
                    {Object.entries(record.extractedFields ?? {}).slice(0, 2).map(([key, value]) => `${key}: ${shortValue(value)}`).join(" / ")}
                  </p>
                </div>
                <div>
                  <StatusBadge status={record.reviewStatus ?? "pending_review"} />
                  <p className="mt-1 text-xs text-slate-500">{record.reviewedBy ?? "-"}</p>
                </div>
                <div className="flex justify-end gap-2">
                  <IconButton
                    label="Approve"
                    disabled={reviewingKey === record.recordKey}
                    onClick={() => onReview(record.recordKey, "approved")}
                    icon={reviewingKey === record.recordKey ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                    tone="approve"
                  />
                  <IconButton
                    label="Needs revision"
                    disabled={reviewingKey === record.recordKey}
                    onClick={() => onReview(record.recordKey, "needs_revision")}
                    icon={<AlertCircle className="h-4 w-4" />}
                    tone="warn"
                  />
                  <IconButton
                    label="Reject"
                    disabled={reviewingKey === record.recordKey}
                    onClick={() => onReview(record.recordKey, "rejected")}
                    icon={<XCircle className="h-4 w-4" />}
                    tone="reject"
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function LandEvidenceReadinessPanel({
  review,
  gate,
  records,
  reviewer,
  saving,
  onReviewerChange,
  onSave,
}: {
  review?: LandAgentReview;
  gate?: ApprovalReadinessGate;
  records: LandEvidenceRecord[];
  reviewer: string;
  saving: boolean;
  onReviewerChange: (value: string) => void;
  onSave: (payload: Record<string, unknown>) => void;
}) {
  const [evidenceId, setEvidenceId] = useState("");
  const [sourceName, setSourceName] = useState("manual_land_closing_evidence");
  const [sourceRef, setSourceRef] = useState("");
  const [acquisitionRatio, setAcquisitionRatio] = useState("100");
  const [acquisitionStatus, setAcquisitionStatus] = useState("ownership_or_trust_ready");
  const [memo, setMemo] = useState("");

  const readiness = review?.evidenceReadiness ?? {};
  const summary = review?.summary ?? {};
  const landGateCodes = new Set([
    "LAND_PNU_READY",
    "LAND_OFFICIAL_PRICE_READY",
    "LAND_REGISTRY_EVIDENCE_READY",
    "LAND_ACQUISITION_READY",
    "LAND_EVIDENCE_READY",
  ]);
  const landGateIssues = (gate?.gates ?? []).filter((item) => landGateCodes.has(item.code) && !item.pass);
  const byType = records.reduce<Record<string, number>>((acc, record) => {
    acc[record.evidenceType] = (acc[record.evidenceType] ?? 0) + 1;
    return acc;
  }, {});
  const acquisitionRecords = records.filter((record) => record.evidenceType === "land_acquisition_status");
  const latestAcquisition = acquisitionRecords[0];
  const ratioNumber = rateOrUndefined(acquisitionRatio);
  const canSave = Boolean(sourceRef.trim()) && ratioNumber !== undefined;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-2">
          <Database className="mt-0.5 h-5 w-5 text-emerald-600" />
          <div>
            <h3 className="font-bold text-slate-950 dark:text-white">토지권원·증빙 보완</h3>
            <p className="text-sm leading-6 text-slate-500">
              PNU, 개별공시지가, 등기·제한물권, 토지취득률을 PF 승인 readiness 기준으로 점검합니다.
              취득률 또는 closing 증빙을 저장한 뒤 PF Review를 재실행하면 Land Agent가 최신 서버 증빙을 반영합니다.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={review?.status ?? "not_checked"} />
          <StatusBadge status={readiness.acquisitionReady ? "acquisition_ready" : "acquisition_blocked"} />
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <Mini label="PNU 확보율" value={fmtPercent(summary.pnuCoverageRatio)} />
        <Mini label="공시지가 확보율" value={fmtPercent(summary.officialLandPriceCoverageRatio)} />
        <Mini label="등기증빙 확보율" value={fmtPercent(summary.registryEvidenceCoverageRatio)} />
        <Mini label="토지취득률" value={fmtPercent(summary.acquisitionAreaRatio)} />
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-5">
        <ReadinessPill label="PNU" ready={Boolean(readiness.pnuReady)} />
        <ReadinessPill label="개별공시지가" ready={Boolean(readiness.officialPriceReady)} />
        <ReadinessPill label="등기·제한물권" ready={Boolean(readiness.registryEvidenceReady)} />
        <ReadinessPill label="토지취득" ready={Boolean(readiness.acquisitionReady)} />
        <ReadinessPill label="원천자료" ready={Boolean(readiness.sourceDataReady)} />
      </div>

      {landGateIssues.length || review?.dataLimitations?.length ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <GateIssueList title="토지 Gate 미해결 항목" items={landGateIssues} />
          <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
            <p className="text-xs font-bold uppercase text-slate-500">Land Agent 제한사항</p>
            {review?.dataLimitations?.length ? (
              <ul className="mt-3 space-y-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
                {review.dataLimitations.map((item) => <li key={item}>{item}</li>)}
              </ul>
            ) : (
              <p className="mt-3 text-sm font-semibold text-emerald-700 dark:text-emerald-300">제한사항 없음</p>
            )}
          </div>
        </div>
      ) : null}

      <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_1fr]">
        <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs font-bold uppercase text-slate-500">저장된 토지 증빙</p>
            <StatusBadge status={`${records.length}_records`} />
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <Mini label="PNU resolution" value={`${byType.pnu_resolution ?? 0}`} />
            <Mini label="Official price" value={`${byType.official_land_price ?? 0}`} />
            <Mini label="Registry" value={`${byType.registry_record ?? 0}`} />
            <Mini label="Acquisition" value={`${byType.land_acquisition_status ?? 0}`} />
          </div>
          {latestAcquisition ? (
            <div className="mt-3 rounded-lg bg-emerald-50 p-3 text-sm font-semibold text-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200">
              최근 취득 증빙: {fmtPercent(latestAcquisition.acquisitionAreaRatio)} / {latestAcquisition.sourceUrlOrEvidenceId ?? latestAcquisition.evidenceId}
            </div>
          ) : null}
          {records.length ? (
            <div className="mt-3 max-h-56 overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-800">
              <table className="w-full min-w-[560px] text-left text-sm">
                <thead className="bg-slate-50 text-xs uppercase text-slate-500 dark:bg-slate-800/70">
                  <tr>
                    <th className="px-3 py-2">증빙유형</th>
                    <th className="px-3 py-2">PNU/필지</th>
                    <th className="px-3 py-2">출처</th>
                    <th className="px-3 py-2">상태</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {records.slice(0, 12).map((record) => (
                    <tr key={record.recordKey}>
                      <td className="px-3 py-2 font-bold text-slate-950 dark:text-white">{landEvidenceTypeLabel(record.evidenceType)}</td>
                      <td className="px-3 py-2 text-slate-600 dark:text-slate-300">{record.pnu ?? record.parcelKey ?? "-"}</td>
                      <td className="px-3 py-2 text-slate-600 dark:text-slate-300">{record.sourceUrlOrEvidenceId ?? record.sourceName ?? "-"}</td>
                      <td className="px-3 py-2"><StatusBadge status={record.verificationStatus ?? "attached"} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="mt-3 rounded-lg border border-dashed border-slate-300 p-4 text-sm font-semibold text-slate-500 dark:border-slate-700">
              저장된 토지 증빙 record가 없습니다.
            </p>
          )}
        </div>

        <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
          <p className="text-xs font-bold uppercase text-slate-500">토지취득률 / Closing Evidence 저장</p>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            매매계약, 잔금영수증, 소유권이전 접수증, 담보신탁 접수증, 국공유지 매입승인 등 검증 가능한 자료를 기준으로 입력합니다.
          </p>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">검토자</span>
              <input
                value={reviewer}
                onChange={(event) => onReviewerChange(event.target.value)}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
              />
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">취득률</span>
              <input
                value={acquisitionRatio}
                onChange={(event) => setAcquisitionRatio(event.target.value)}
                placeholder="100 또는 1.0"
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
              />
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">증빙 ID</span>
              <input
                value={evidenceId}
                onChange={(event) => setEvidenceId(event.target.value)}
                placeholder="land-closing-001"
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
              />
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">증빙 경로 / 문서번호</span>
              <input
                value={sourceRef}
                onChange={(event) => setSourceRef(event.target.value)}
                placeholder="registry/closing-confirmation.pdf#p1"
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
              />
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">출처명</span>
              <input
                value={sourceName}
                onChange={(event) => setSourceName(event.target.value)}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
              />
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">Closing 상태</span>
              <select
                value={acquisitionStatus}
                onChange={(event) => setAcquisitionStatus(event.target.value)}
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
              >
                <option value="contracted">매매계약 체결</option>
                <option value="balance_paid">잔금 지급 완료</option>
                <option value="ownership_transferred">소유권 이전 완료</option>
                <option value="trust_registered">담보신탁 등기 완료</option>
                <option value="ownership_or_trust_ready">소유권/신탁 설정 가능</option>
              </select>
            </label>
            <label className="block md:col-span-2">
              <span className="text-xs font-semibold text-slate-500">검토 메모</span>
              <input
                value={memo}
                onChange={(event) => setMemo(event.target.value)}
                placeholder="예: 전 필지 잔금 지급 및 신탁등기 접수증 확인"
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
              />
            </label>
          </div>
          <button
            type="button"
            disabled={!canSave || saving}
            onClick={() => onSave({
              recordKey: `land_acquisition_status:${Date.now()}`,
              evidenceId: evidenceId.trim() || `land-acquisition-${Date.now()}`,
              evidenceType: "land_acquisition_status",
              pnu: summary.pnu,
              parcelKey: summary.pnu ?? "project_land",
              acquisitionAreaRatio: ratioNumber,
              acquisitionAmountRatio: ratioNumber,
              acquisitionStatus,
              closingStatus: acquisitionStatus,
              sourceName: sourceName.trim() || "manual_land_closing_evidence",
              sourceType: "manual_upload",
              sourceUrlOrEvidenceId: sourceRef.trim(),
              verificationStatus: ratioNumber !== undefined && ratioNumber >= 1 ? "verified_for_pf_review" : "attached_pending_review",
              reviewedBy: reviewer.trim() || "land_analyst",
              reviewedAt: new Date().toISOString(),
              reviewMemo: memo.trim(),
              sourceLocators: [
                {
                  type: "manual_evidence",
                  source: sourceRef.trim(),
                  label: "토지취득률/closing 증빙",
                  path: sourceRef.trim(),
                },
              ],
            })}
            className="mt-4 inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white dark:text-slate-950"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserCheck className="h-4 w-4" />}
            토지취득 증빙 저장
          </button>
        </div>
      </div>
    </section>
  );
}

function ReadinessPill({ label, ready }: { label: string; ready: boolean }) {
  return (
    <div className={`rounded-lg border p-3 text-sm font-bold ${
      ready
        ? "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100"
        : "border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-100"
    }`}>
      <div className="flex items-center justify-between gap-2">
        <span>{label}</span>
        {ready ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
      </div>
    </div>
  );
}

function landEvidenceTypeLabel(type: string) {
  const map: Record<string, string> = {
    pnu_resolution: "PNU 매칭",
    official_land_price: "개별공시지가",
    cadastral_record: "지적특성",
    registry_record: "등기/제한물권",
    land_acquisition_status: "토지취득/Closing",
  };
  return map[type] ?? type;
}

function DataQualityOverridePanel({
  review,
  overrides,
  reviewer,
  savingKey,
  updatingOverrideId,
  onReviewerChange,
  onSaveOverride,
  onUpdateOverrideStatus,
}: {
  review: DataQualityReview;
  overrides: AssumptionOverrideRecord[];
  reviewer: string;
  savingKey: string | null;
  updatingOverrideId: string | null;
  onReviewerChange: (value: string) => void;
  onSaveOverride: (payload: Record<string, unknown>) => void;
  onUpdateOverrideStatus: (override: AssumptionOverrideRecord, status: "revoked" | "expired" | "active") => void;
}) {
  const checks = useMemo(() => review.checks ?? [], [review.checks]);
  const actionableChecks = useMemo(
    () => checks.filter((check) => ["blocking", "warning", "missing_data"].includes(check.status)),
    [checks],
  );
  const [selectedValues, setSelectedValues] = useState<Record<string, string>>({});
  const [customValues, setCustomValues] = useState<Record<string, string>>({});
  const [reasons, setReasons] = useState<Record<string, string>>({});

  useEffect(() => {
    setSelectedValues((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const check of actionableChecks) {
        if (next[check.key]) continue;
        const values = dataQualityValues(check);
        const preferred = preferredDataQualityValue(values);
        next[check.key] = preferred ? valueOptionKey(preferred) : "__custom__";
        changed = true;
      }
      return changed ? next : prev;
    });
  }, [actionableChecks]);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-2">
          <FileJson className="mt-0.5 h-5 w-5 text-amber-600" />
          <div>
            <h3 className="font-bold text-slate-950 dark:text-white">Data Quality 기준값 승인</h3>
            <p className="text-sm leading-6 text-slate-500">
              문서, Excel, API, 에이전트 결과 사이의 숫자 충돌을 확인하고 심사 기준값을 명시적으로 승인합니다.
              원본 충돌값은 보존되고 다음 PF Review 재실행 때 승인 override가 적용됩니다.
            </p>
          </div>
        </div>
        <StatusBadge status={review.status ?? "unknown"} />
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-5">
        <Mini label="Checks" value={`${review.summary?.checkCount ?? checks.length}`} />
        <Mini label="Blocking" value={`${review.summary?.blockingCount ?? 0}`} />
        <Mini label="Warnings" value={`${review.summary?.warningCount ?? 0}`} />
        <Mini label="Missing" value={`${review.summary?.missingCount ?? 0}`} />
        <Mini label="Overridden" value={`${review.summary?.overriddenCount ?? 0}`} />
      </div>

      <label className="mt-4 block max-w-sm">
        <span className="text-xs font-semibold text-slate-500">승인자</span>
        <input
          value={reviewer}
          onChange={(event) => onReviewerChange(event.target.value)}
          className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
        />
      </label>

      {overrides.length > 0 ? (
        <div className="mt-4 rounded-lg border border-slate-200 dark:border-slate-800">
          <div className="flex items-center justify-between gap-3 border-b border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-800 dark:bg-slate-800/60">
            <div>
              <p className="text-sm font-bold text-slate-950 dark:text-white">저장된 기준값 override</p>
              <p className="text-xs text-slate-500">잘못 승인한 값은 revoke, 기준일이 지난 값은 expire 처리합니다.</p>
            </div>
            <Mini label="Active" value={`${overrides.filter((item) => (item.effectiveStatus ?? item.status) === "active").length}`} />
          </div>
          <div className="divide-y divide-slate-200 dark:divide-slate-800">
            {overrides.slice(0, 8).map((override) => {
              const effectiveStatus = override.effectiveStatus ?? override.status ?? "active";
              const isUpdating = updatingOverrideId === override.overrideId;
              return (
                <div key={override.overrideId} className="grid gap-3 px-4 py-3 text-sm lg:grid-cols-[1.4fr_1fr_1fr_auto] lg:items-center">
                  <div>
                    <p className="font-bold text-slate-950 dark:text-white">{dataQualityLabel(override.checkKey)}</p>
                    <p className="text-xs text-slate-500">{override.overrideId} / {override.canonicalField}</p>
                    <p className="mt-1 text-xs text-slate-500">사유: {override.reason || "-"}</p>
                  </div>
                  <div>
                    <p className="text-xs font-semibold uppercase text-slate-500">승인값</p>
                    <p className="font-bold text-slate-900 dark:text-white">{formatDataQualityValue(override.approvedValue as string | number | boolean | undefined, override.unit ?? "")}</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge status={effectiveStatus} />
                    {override.supersededByOverrideId ? (
                      <span className="text-xs text-slate-500">superseded by {override.supersededByOverrideId}</span>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap justify-start gap-2 lg:justify-end">
                    <button
                      type="button"
                      disabled={isUpdating || effectiveStatus !== "active"}
                      onClick={() => onUpdateOverrideStatus(override, "revoked")}
                      className="inline-flex h-8 items-center justify-center rounded-md border border-rose-200 px-3 text-xs font-bold text-rose-700 disabled:cursor-not-allowed disabled:opacity-40 dark:border-rose-900 dark:text-rose-300"
                    >
                      {isUpdating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Revoke"}
                    </button>
                    <button
                      type="button"
                      disabled={isUpdating || effectiveStatus !== "active"}
                      onClick={() => onUpdateOverrideStatus(override, "expired")}
                      className="inline-flex h-8 items-center justify-center rounded-md border border-amber-200 px-3 text-xs font-bold text-amber-700 disabled:cursor-not-allowed disabled:opacity-40 dark:border-amber-900 dark:text-amber-300"
                    >
                      Expire
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {checks.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-slate-300 p-4 text-sm font-semibold text-slate-500 dark:border-slate-700">
          Data Quality check가 없습니다.
        </div>
      ) : (
        <div className="mt-4 space-y-3">
          {checks.map((check) => {
            const values = dataQualityValues(check);
            const selected = selectedValues[check.key] ?? "__custom__";
            const selectedValue = values.find((value) => valueOptionKey(value) === selected);
            const custom = customValues[check.key] ?? "";
            const approvedValue = selected === "__custom__" ? parseOverrideValue(custom) : selectedValue?.value;
            const reason = reasons[check.key] ?? defaultOverrideReason(check, selectedValue);
            const canSave = ["blocking", "warning", "missing_data"].includes(check.status) && approvedValue !== undefined && reason.trim().length > 0;
            return (
              <div key={check.key} className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
                <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-bold text-slate-950 dark:text-white">{dataQualityLabel(check.label)}</p>
                      <StatusBadge status={check.status} />
                      <StatusBadge status={check.severity} />
                    </div>
                    <p className="mt-1 text-sm leading-6 text-slate-500">{check.message}</p>
                    <p className="mt-1 text-xs text-slate-400">{check.key} / {canonicalFieldForCheck(check.key)}</p>
                  </div>
                  {check.approvedAssumptionOverride ? (
                    <div className="rounded-lg bg-emerald-50 p-3 text-xs font-semibold text-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200">
                      승인 override: {check.approvedAssumptionOverride.overrideId ?? "-"}
                    </div>
                  ) : null}
                </div>

                {values.length ? (
                  <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
                    <table className="w-full min-w-[620px] text-left text-sm">
                      <thead className="bg-slate-50 text-xs uppercase text-slate-500 dark:bg-slate-800/70">
                        <tr>
                          <th className="px-3 py-2">Source</th>
                          <th className="px-3 py-2 text-right">Value</th>
                          <th className="px-3 py-2">Unit</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                        {values.map((value) => (
                          <tr key={valueOptionKey(value)}>
                            <td className="px-3 py-2 font-semibold text-slate-700 dark:text-slate-200">{value.source ?? "-"}</td>
                            <td className="px-3 py-2 text-right font-bold text-slate-950 dark:text-white">{formatDataQualityValue(value.value, dataQualityUnit(check))}</td>
                            <td className="px-3 py-2 text-slate-500">{dataQualityUnit(check)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}

                {["blocking", "warning", "missing_data"].includes(check.status) ? (
                  <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_1fr_1.2fr_auto]">
                    <label className="block">
                      <span className="text-xs font-semibold text-slate-500">기준값 선택</span>
                      <select
                        value={selected}
                        onChange={(event) => setSelectedValues((prev) => ({ ...prev, [check.key]: event.target.value }))}
                        className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                      >
                        {values.map((value) => (
                          <option key={valueOptionKey(value)} value={valueOptionKey(value)}>
                            {value.source ?? "source"} / {formatDataQualityValue(value.value, dataQualityUnit(check))}
                          </option>
                        ))}
                        <option value="__custom__">직접 입력</option>
                      </select>
                    </label>
                    <label className="block">
                      <span className="text-xs font-semibold text-slate-500">직접 입력값</span>
                      <input
                        value={custom}
                        onChange={(event) => setCustomValues((prev) => ({ ...prev, [check.key]: event.target.value }))}
                        disabled={selected !== "__custom__"}
                        placeholder="예: 165597000000"
                        className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 disabled:bg-slate-100 disabled:text-slate-400 dark:border-slate-700 dark:bg-slate-950 dark:text-white dark:disabled:bg-slate-900"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs font-semibold text-slate-500">승인 사유</span>
                      <input
                        value={reason}
                        onChange={(event) => setReasons((prev) => ({ ...prev, [check.key]: event.target.value }))}
                        className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                      />
                    </label>
                    <button
                      type="button"
                      disabled={!canSave || savingKey === check.key}
                      onClick={() => onSaveOverride({
                        overrideId: `ovr-${check.key}-${Date.now()}`,
                        checkKey: check.key,
                        canonicalField: canonicalFieldForCheck(check.key),
                        approvedValue,
                        unit: dataQualityUnit(check),
                        reason,
                        approvedBy: reviewer.trim() || "credit_officer",
                        evidenceIds: [],
                        sourceLocators: sourceLocatorsForOverride(check, selectedValue),
                      })}
                      className="mt-6 inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white dark:text-slate-950"
                    >
                      {savingKey === check.key ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserCheck className="h-4 w-4" />}
                      기준값 승인
                    </button>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function dataQualityValues(check: DataQualityCheck): DataQualityValue[] {
  const metadata = check.metadata;
  if (Array.isArray(metadata)) return metadata.map(normalizeDataQualityValue).filter(isDataQualityValue);
  if (metadata && typeof metadata === "object" && Array.isArray((metadata as { values?: unknown }).values)) {
    return ((metadata as { values?: unknown[] }).values ?? []).map(normalizeDataQualityValue).filter(isDataQualityValue);
  }
  return [];
}

function normalizeDataQualityValue(value: unknown): unknown {
  if (Array.isArray(value) && value.length >= 2) {
    return { source: String(value[0]), value: value[1] as number | string | boolean };
  }
  return value;
}

function isDataQualityValue(value: unknown): value is DataQualityValue {
  return Boolean(value && typeof value === "object" && "value" in value);
}

function valueOptionKey(value: DataQualityValue) {
  return `${value.source ?? "source"}::${String(value.value)}`;
}

function preferredDataQualityValue(values: DataQualityValue[]) {
  const numeric = values.filter((value) => typeof value.value === "number") as Array<DataQualityValue & { value: number }>;
  if (numeric.length) {
    return [...numeric].sort((a, b) => Math.abs(b.value) - Math.abs(a.value))[0];
  }
  return values[0];
}

function dataQualityUnit(check: DataQualityCheck) {
  const metadata = check.metadata;
  if (metadata && !Array.isArray(metadata) && typeof metadata === "object" && typeof (metadata as { unit?: unknown }).unit === "string") {
    return (metadata as { unit: string }).unit;
  }
  if (check.key.includes("revenue") || check.key.includes("pf")) return "KRW";
  if (check.key.includes("area")) return "sqm";
  if (check.key.includes("unit")) return "units";
  return "";
}

function canonicalFieldForCheck(key: string) {
  const map: Record<string, string> = {
    sales_revenue_consistency: "feasibility_input.total_revenue",
    pf_amount_consistency: "pf_structure.amount.sum",
    land_area_consistency: "land.area_sqm",
    gross_floor_area_consistency: "architectural_plan.gross_floor_area_sqm",
    unit_count_consistency: "architectural_plan.unit_count",
    sqm_py_conversion_consistency: "product_mix.area_conversion",
    vat_basis_consistency: "feasibility_input.vat_basis",
    schedule_sequence_consistency: "schedule",
    tranche_structure_quality: "pf_structure",
    project_name_consistency: "project.name",
  };
  return map[key] ?? key;
}

function defaultOverrideReason(check: DataQualityCheck, selected?: DataQualityValue) {
  if (selected?.source) return `${selected.source} 값을 심사 기준값으로 승인합니다.`;
  return `${dataQualityLabel(check.label)} 충돌 해소를 위한 심사역 기준값 승인입니다.`;
}

function parseOverrideValue(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const numeric = Number(trimmed.replaceAll(",", ""));
  if (Number.isFinite(numeric)) return numeric;
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  return trimmed;
}

function formatDataQualityValue(value: unknown, unit: string) {
  if (typeof value === "number") {
    if (unit === "KRW") return fmtEok(value);
    if (unit === "sqm") return `${value.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}㎡`;
    return value.toLocaleString("ko-KR", { maximumFractionDigits: 2 });
  }
  return String(value ?? "-");
}

function dataQualityLabel(label: string) {
  const labels: Record<string, string> = {
    "Project name consistency": "사업명 정합성",
    "Land area consistency": "대지면적 정합성",
    "Gross floor area consistency": "연면적 정합성",
    "Total sales revenue consistency": "총분양수입 정합성",
    "PF amount consistency": "PF 대출금 정합성",
    "Unit count consistency": "세대·호실수 정합성",
    "SQM/PY conversion consistency": "㎡/평 환산 정합성",
    "VAT basis consistency": "VAT 포함·제외 기준",
    "Schedule sequence consistency": "사업일정 정합성",
    "Tranche structure quality": "Tranche 구조 정합성",
  };
  return labels[label] ?? label;
}

function sourceLocatorsForOverride(check: DataQualityCheck, selected?: DataQualityValue) {
  const locators: Array<Record<string, unknown>> = [];
  const metadata = check.metadata;
  if (metadata && !Array.isArray(metadata) && typeof metadata === "object" && Array.isArray((metadata as { sourceLocators?: unknown }).sourceLocators)) {
    locators.push(...((metadata as { sourceLocators: Array<Record<string, unknown>> }).sourceLocators ?? []));
  }
  if (selected?.source) {
    locators.push({
      type: "data_quality_value",
      path: selected.source,
      label: `${dataQualityLabel(check.label)} selected source`,
      value: selected.value,
      locatorPrecision: "field_path",
    });
  } else {
    locators.push({
      type: "data_quality_check",
      path: check.key,
      label: `${dataQualityLabel(check.label)} manual override`,
      locatorPrecision: "check_level",
    });
  }
  return locators;
}

function HistoricalOutcomePanel({
  snapshots,
  selectedSnapshot,
  selectedSnapshotId,
  form,
  saving,
  outcomeUser,
  onSelectSnapshot,
  onFormChange,
  onOutcomeUserChange,
  onSave,
}: {
  snapshots: ProjectSnapshotSummary[];
  selectedSnapshot: ProjectSnapshotDetail | null;
  selectedSnapshotId: string;
  form: OutcomeFormState;
  saving: boolean;
  outcomeUser: string;
  onSelectSnapshot: (snapshotId: string) => void;
  onFormChange: (patch: Partial<OutcomeFormState>) => void;
  onOutcomeUserChange: (value: string) => void;
  onSave: () => void;
}) {
  const outcome = selectedSnapshot?.historicalOutcome;
  const quality = outcome?.outcomeQuality;
  const inputClass = "mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white";
  const checkClass = "h-4 w-4 rounded border-slate-300 text-slate-950 focus:ring-slate-500";

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-center gap-2">
          <Database className="h-5 w-5 text-sky-600" />
          <div>
            <h3 className="font-bold text-slate-950 dark:text-white">Historical Outcome</h3>
            <p className="text-sm text-slate-500">
              Save actual pilot performance so future similar-case search can compare what happened after approval.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusBadge status={outcome?.outcomeStatus ?? "not_recorded"} />
          <StatusBadge status={quality?.status ?? "partial"} />
        </div>
      </div>

      {snapshots.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-slate-300 p-4 text-sm font-semibold text-slate-500 dark:border-slate-700">
          No saved project snapshot exists yet. Run PF Review first, then record actual outcome data.
        </div>
      ) : (
        <>
          <div className="mt-4 grid gap-3 md:grid-cols-4">
            <Mini label="Outcome 상태" value={outcome?.outcomeStatus ?? "-"} />
            <Mini label="Actual Sales Rate" value={fmtPercent(outcome?.actualSalesRate ?? undefined)} />
            <Mini label="Recovery Rate" value={fmtPercent(outcome?.recoveryRate ?? undefined)} />
            <Mini label="Updated" value={fmtDate(outcome?.updatedAt)} />
          </div>

          <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_220px]">
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">Snapshot</span>
              <select
                value={selectedSnapshotId}
                onChange={(event) => onSelectSnapshot(event.target.value)}
                className={inputClass}
              >
                {snapshots.map((snapshot) => (
                  <option key={snapshot.snapshotId} value={snapshot.snapshotId}>
                    {snapshot.snapshotId} / {snapshot.runId ?? "-"} / {snapshot.outcomeStatus ?? "not_recorded"}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">Updated By</span>
              <input
                value={outcomeUser}
                onChange={(event) => onOutcomeUserChange(event.target.value)}
                className={inputClass}
              />
            </label>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <OutcomeTextField label="Actual Sales Rate" value={form.actualSalesRate} placeholder="0.82 or 82" inputClass={inputClass} onChange={(value) => onFormChange({ actualSalesRate: value })} />
            <OutcomeTextField label="Actual Discount Rate" value={form.actualDiscountRate} placeholder="0.05 or 5" inputClass={inputClass} onChange={(value) => onFormChange({ actualDiscountRate: value })} />
            <OutcomeTextField label="Recovery Rate" value={form.recoveryRate} placeholder="1.00" inputClass={inputClass} onChange={(value) => onFormChange({ recoveryRate: value })} />
            <OutcomeTextField label="Loss Amount" value={form.lossAmount} placeholder="0" inputClass={inputClass} onChange={(value) => onFormChange({ lossAmount: value })} />
            <OutcomeTextField label="Senior Recovery" value={form.seniorRecoveryRate} placeholder="1.00" inputClass={inputClass} onChange={(value) => onFormChange({ seniorRecoveryRate: value })} />
            <OutcomeTextField label="Mezz Recovery" value={form.mezzanineRecoveryRate} placeholder="1.00" inputClass={inputClass} onChange={(value) => onFormChange({ mezzanineRecoveryRate: value })} />
            <OutcomeTextField label="Junior Recovery" value={form.juniorRecoveryRate} placeholder="0.85" inputClass={inputClass} onChange={(value) => onFormChange({ juniorRecoveryRate: value })} />
            <OutcomeTextField label="Extension Count" value={form.extensionCount} placeholder="0" inputClass={inputClass} onChange={(value) => onFormChange({ extensionCount: value })} />
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            <OutcomeCheckbox label="Repayment Completed" checked={form.repaymentCompleted} checkClass={checkClass} onChange={(value) => onFormChange({ repaymentCompleted: value })} />
            <OutcomeCheckbox label="Extension Occurred" checked={form.extensionOccurred} checkClass={checkClass} onChange={(value) => onFormChange({ extensionOccurred: value })} />
            <OutcomeCheckbox label="EOD Occurred" checked={form.eodOccurred} checkClass={checkClass} onChange={(value) => onFormChange({ eodOccurred: value })} />
            <OutcomeCheckbox label="Loss Occurred" checked={form.lossOccurred} checkClass={checkClass} onChange={(value) => onFormChange({ lossOccurred: value })} />
            <OutcomeTextField label="Repayment Completion Date" value={form.repaymentCompletionDate} placeholder="2026-12-31" inputClass={inputClass} onChange={(value) => onFormChange({ repaymentCompletionDate: value })} />
            <OutcomeTextField label="EOD Date" value={form.eodDate} placeholder="YYYY-MM-DD" inputClass={inputClass} onChange={(value) => onFormChange({ eodDate: value })} />
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <OutcomeTextField label="Actual Exit Source" value={form.actualExitSource} placeholder="sale proceeds / refinance / bulk sale" inputClass={inputClass} onChange={(value) => onFormChange({ actualExitSource: value })} />
            <OutcomeTextField label="Source Name" value={form.sourceName} placeholder="loan repayment statement" inputClass={inputClass} onChange={(value) => onFormChange({ sourceName: value })} />
            <OutcomeTextField label="Source Type" value={form.sourceType} placeholder="manual_review" inputClass={inputClass} onChange={(value) => onFormChange({ sourceType: value })} />
            <OutcomeTextField label="Base Date" value={form.baseDate} placeholder="YYYY-MM-DD" inputClass={inputClass} onChange={(value) => onFormChange({ baseDate: value })} />
          </div>

          <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_1fr_160px]">
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">Outcome Memo</span>
              <textarea
                value={form.outcomeMemo}
                onChange={(event) => onFormChange({ outcomeMemo: event.target.value })}
                rows={4}
                className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
              />
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">Source Locators JSON</span>
              <textarea
                value={form.sourceLocatorsJson}
                onChange={(event) => onFormChange({ sourceLocatorsJson: event.target.value })}
                rows={4}
                className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-xs text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
              />
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">Confidence</span>
              <input
                value={form.confidence}
                onChange={(event) => onFormChange({ confidence: event.target.value })}
                placeholder="0.9"
                className={inputClass}
              />
              <button
                type="button"
                onClick={onSave}
                disabled={saving || !selectedSnapshotId}
                className="mt-4 inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-white dark:text-slate-950"
              >
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
                Save Outcome
              </button>
            </label>
          </div>

          {quality?.missingFields?.length ? (
            <div className="mt-4 rounded-lg bg-amber-50 p-4 text-sm font-semibold text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
              Missing outcome fields: {quality.missingFields.join(", ")}
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function OutcomeTextField({
  label,
  value,
  placeholder,
  inputClass,
  onChange,
}: {
  label: string;
  value: string;
  placeholder?: string;
  inputClass: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-slate-500">{label}</span>
      <input
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className={inputClass}
      />
    </label>
  );
}

function OutcomeCheckbox({
  label,
  checked,
  checkClass,
  onChange,
}: {
  label: string;
  checked: boolean;
  checkClass: string;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex h-10 items-center gap-2 rounded-lg border border-slate-200 px-3 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className={checkClass}
      />
      {label}
    </label>
  );
}

function HistoricalSimilarCasesPanel({
  snapshots,
  selectedSnapshotId,
  result,
  searching,
  limit,
  minScore,
  includeSameProject,
  onSelectSnapshot,
  onLimitChange,
  onMinScoreChange,
  onIncludeSameProjectChange,
  onSearch,
}: {
  snapshots: ProjectSnapshotSummary[];
  selectedSnapshotId: string;
  result: SimilarCaseSearchResponse | null;
  searching: boolean;
  limit: string;
  minScore: string;
  includeSameProject: boolean;
  onSelectSnapshot: (snapshotId: string) => void;
  onLimitChange: (value: string) => void;
  onMinScoreChange: (value: string) => void;
  onIncludeSameProjectChange: (value: boolean) => void;
  onSearch: () => void;
}) {
  const inputClass = "mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white";
  const cases = result?.similarCases ?? [];

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-center gap-2">
          <GitBranch className="h-5 w-5 text-indigo-600" />
          <div>
            <h3 className="font-bold text-slate-950 dark:text-white">Historical Similar Cases</h3>
            <p className="text-sm text-slate-500">
              Compare this snapshot with stored PF review snapshots, risk treatments, and actual outcomes.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusBadge status={result ? "searched" : "not_searched"} />
          {result?.summary ? <StatusBadge status={`top ${fmtPercent(result.summary.topScore)}`} /> : null}
        </div>
      </div>

      {snapshots.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-slate-300 p-4 text-sm font-semibold text-slate-500 dark:border-slate-700">
          No saved project snapshot exists yet. Run PF Review first to create a query snapshot.
        </div>
      ) : (
        <>
          <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_110px_120px_170px_auto]">
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">Query Snapshot</span>
              <select
                value={selectedSnapshotId}
                onChange={(event) => onSelectSnapshot(event.target.value)}
                className={inputClass}
              >
                {snapshots.map((snapshot) => (
                  <option key={snapshot.snapshotId} value={snapshot.snapshotId}>
                    {snapshot.snapshotId} / {snapshot.runId ?? "-"} / {snapshot.createdAt ? fmtDate(snapshot.createdAt) : "-"}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">Limit</span>
              <input value={limit} onChange={(event) => onLimitChange(event.target.value)} className={inputClass} />
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">Min Score</span>
              <input value={minScore} onChange={(event) => onMinScoreChange(event.target.value)} className={inputClass} />
            </label>
            <label className="mt-6 flex h-10 items-center gap-2 rounded-lg border border-slate-200 px-3 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:text-slate-200">
              <input
                type="checkbox"
                checked={includeSameProject}
                onChange={(event) => onIncludeSameProjectChange(event.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-slate-950 focus:ring-slate-500"
              />
              Same project
            </label>
            <button
              type="button"
              onClick={onSearch}
              disabled={searching || !selectedSnapshotId}
              className="mt-6 inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-white dark:text-slate-950"
            >
              {searching ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
              Search
            </button>
          </div>

          {result ? (
            <div className="mt-4 grid gap-3 md:grid-cols-4">
              <Mini label="Candidates" value={`${result.summary?.candidateCount ?? 0}`} />
              <Mini label="Returned" value={`${result.summary?.returnedCount ?? cases.length}`} />
              <Mini label="Query Risks" value={`${result.summary?.queryRiskCount ?? "-"}`} />
              <Mini label="Top Score" value={fmtPercent(result.summary?.topScore)} />
            </div>
          ) : null}

          {cases.length ? (
            <div className="mt-4 space-y-3">
              {cases.map((item) => (
                <SimilarCaseCard key={`${item.projectId}-${item.snapshotId}`} item={item} />
              ))}
            </div>
          ) : result ? (
            <div className="mt-4 rounded-lg border border-dashed border-slate-300 p-4 text-sm font-semibold text-slate-500 dark:border-slate-700">
              No similar cases returned. Lower the min score or include same-project snapshots for pilot testing.
            </div>
          ) : null}

          {result?.dataLimitations?.length ? (
            <div className="mt-4 rounded-lg bg-amber-50 p-4 text-sm font-semibold text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
              {result.dataLimitations.slice(0, 4).join(" / ")}
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function SimilarCaseCard({ item }: { item: SimilarCaseResult }) {
  const outcome = item.historicalOutcome;
  const summary = item.handlingSummary ?? {};
  const seed = item.caseComparisonSeed ?? {};
  return (
    <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate font-bold text-slate-950 dark:text-white">{item.snapshotId ?? "-"}</p>
            <StatusBadge status={item.decisionOutcome?.decision ?? "decision_unknown"} />
            <StatusBadge status={outcome?.outcomeStatus ?? "outcome_unknown"} />
          </div>
          <p className="mt-1 text-xs text-slate-500">
            {item.projectId ?? "-"} / {item.runId ?? "-"} / {fmtDate(item.createdAt)}
          </p>
        </div>
        <div className="text-right">
          <p className="text-xs font-semibold uppercase text-slate-500">Similarity</p>
          <p className="mt-1 text-xl font-black text-slate-950 dark:text-white">{fmtPercent(item.similarityScore)}</p>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <Mini label="PF Amount" value={fmtEok(toNumber(seed.pfAmount))} />
        <Mini label="Exit Rate" value={fmtPercent(toNumber(seed.exitSaleRate))} />
        <Mini label="Matched Risks" value={`${summary.matchedRiskCount ?? item.riskMatches?.length ?? 0}`} />
        <Mini label="Recovery" value={fmtPercent(outcome?.recoveryRate ?? summary.recoveryRate ?? undefined)} />
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
          <p className="text-xs font-bold uppercase text-slate-500">Risk Handling</p>
          {item.riskMatches?.length ? (
            <ul className="mt-2 space-y-2 text-sm text-slate-700 dark:text-slate-200">
              {item.riskMatches.slice(0, 3).map((risk, index) => (
                <li key={`${risk.historicalRiskId ?? risk.historicalRiskCode}-${index}`}>
                  <span className="font-bold">{risk.historicalRiskCode ?? risk.queryRiskCode ?? "risk"}</span>
                  {" "}
                  <span className="text-slate-500">/ {risk.reviewStatus ?? "-"} / {risk.severity ?? "-"}</span>
                  {risk.mitigant ? <p className="mt-1 text-xs text-slate-500">{risk.mitigant}</p> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm font-semibold text-slate-500">No exact risk-code matches.</p>
          )}
        </div>
        <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
          <p className="text-xs font-bold uppercase text-slate-500">Actual Outcome</p>
          <div className="mt-2 grid gap-2 text-sm">
            <div className="flex justify-between gap-3">
              <span className="text-slate-500">Sales / Discount</span>
              <span className="font-bold text-slate-950 dark:text-white">
                {fmtPercent(outcome?.actualSalesRate ?? undefined)} / {fmtPercent(outcome?.actualDiscountRate ?? undefined)}
              </span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-slate-500">Repayment / EOD / Loss</span>
              <span className="font-bold text-slate-950 dark:text-white">
                {String(outcome?.repaymentCompleted ?? summary.repaymentCompleted ?? "-")} / {String(outcome?.eodOccurred ?? summary.eodOccurred ?? "-")} / {String(outcome?.lossOccurred ?? summary.lossOccurred ?? "-")}
              </span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-slate-500">Exit Source</span>
              <span className="font-bold text-slate-950 dark:text-white">{outcome?.actualExitSource ?? "-"}</span>
            </div>
          </div>
        </div>
      </div>

      {item.approvalConditions?.length ? (
        <div className="mt-3 rounded-lg bg-emerald-50 p-3 text-sm font-semibold text-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200">
          {item.approvalConditions.slice(0, 3).join(" / ")}
        </div>
      ) : null}
    </div>
  );
}

function toNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value.replaceAll(",", ""));
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function SnapshotDiffPanel({
  snapshots,
  baseSnapshotId,
  compareSnapshotId,
  result,
  diffing,
  onBaseSnapshotChange,
  onCompareSnapshotChange,
  onDiff,
}: {
  snapshots: ProjectSnapshotSummary[];
  baseSnapshotId: string;
  compareSnapshotId: string;
  result: SnapshotDiffResponse | null;
  diffing: boolean;
  onBaseSnapshotChange: (value: string) => void;
  onCompareSnapshotChange: (value: string) => void;
  onDiff: () => void;
}) {
  const inputClass = "mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white";
  const canDiff = snapshots.length >= 2 && baseSnapshotId && compareSnapshotId && baseSnapshotId !== compareSnapshotId;
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-center gap-2">
          <GitBranch className="h-5 w-5 text-violet-600" />
          <div>
            <h3 className="font-bold text-slate-950 dark:text-white">Snapshot Diff</h3>
            <p className="text-sm text-slate-500">
              Compare two saved PF review snapshots and identify material changes that require re-review.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusBadge status={result ? "diffed" : "not_diffed"} />
          {result ? <StatusBadge status={result.summary.requiresReReview ? "re_review_required" : "no_re_review"} /> : null}
        </div>
      </div>

      {snapshots.length < 2 ? (
        <div className="mt-4 rounded-lg border border-dashed border-slate-300 p-4 text-sm font-semibold text-slate-500 dark:border-slate-700">
          At least two saved snapshots are required. Run PF Review again after changing inputs to compare versions.
        </div>
      ) : (
        <>
          <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_1fr_auto]">
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">Base Snapshot</span>
              <select
                value={baseSnapshotId}
                onChange={(event) => onBaseSnapshotChange(event.target.value)}
                className={inputClass}
              >
                {snapshots.map((snapshot) => (
                  <option key={snapshot.snapshotId} value={snapshot.snapshotId}>
                    {snapshot.snapshotId} / {snapshot.runId ?? "-"} / {fmtDate(snapshot.createdAt)}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-500">Compare Snapshot</span>
              <select
                value={compareSnapshotId}
                onChange={(event) => onCompareSnapshotChange(event.target.value)}
                className={inputClass}
              >
                {snapshots.map((snapshot) => (
                  <option key={snapshot.snapshotId} value={snapshot.snapshotId}>
                    {snapshot.snapshotId} / {snapshot.runId ?? "-"} / {fmtDate(snapshot.createdAt)}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={onDiff}
              disabled={diffing || !canDiff}
              className="mt-6 inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-white dark:text-slate-950"
            >
              {diffing ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
              Compare
            </button>
          </div>

          {result ? (
            <>
              <div className="mt-4 grid gap-3 md:grid-cols-4 xl:grid-cols-6">
                <Mini label="Seed Changes" value={`${result.summary.seedChangeCount}`} />
                <Mini label="Added Risks" value={`${result.summary.addedRiskCount}`} />
                <Mini label="Changed Risks" value={`${result.summary.changedRiskCount}`} />
                <Mini label="Decision Changed" value={result.summary.decisionChanged ? "Yes" : "No"} />
                <Mini label="Material" value={`${result.summary.materialChangeCount}`} />
                <Mini label="Re-review" value={result.summary.requiresReReview ? "Required" : "No"} />
              </div>

              <div className={`mt-4 rounded-lg p-4 text-sm font-semibold ${
                result.reReviewRecommendation.required
                  ? "bg-rose-50 text-rose-800 dark:bg-rose-950/30 dark:text-rose-200"
                  : "bg-emerald-50 text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200"
              }`}>
                <p className="font-bold">{result.reReviewRecommendation.message}</p>
                {result.reReviewRecommendation.recommendedActions.length ? (
                  <ul className="mt-2 space-y-1">
                    {result.reReviewRecommendation.recommendedActions.slice(0, 5).map((action) => (
                      <li key={action}>{action}</li>
                    ))}
                  </ul>
                ) : null}
              </div>

              <div className="mt-4 grid gap-4 xl:grid-cols-2">
                <DiffList
                  title="Material Changes"
                  emptyLabel="No material changes."
                  items={result.materialChanges.slice(0, 8).map((item) => ({
                    key: `${item.type}-${item.field ?? item.code ?? item.artifactKey ?? item.message}`,
                    title: item.type,
                    body: item.message,
                    status: item.code ?? item.field ?? item.artifactKey,
                  }))}
                />
                <DiffList
                  title="Seed Changes"
                  emptyLabel="No seed changes."
                  items={result.seedChanges.slice(0, 8).map((item) => ({
                    key: item.field,
                    title: item.field,
                    body: `${shortValue(item.before)} -> ${shortValue(item.after)}`,
                    status: item.material ? "material" : item.direction,
                  }))}
                />
                <DiffList
                  title="Risk Changes"
                  emptyLabel="No risk changes."
                  items={[
                    ...result.riskChanges.added.slice(0, 4).map((risk) => ({
                      key: `added-${risk.code ?? risk.riskId}`,
                      title: risk.code ?? risk.riskId ?? "risk",
                      body: `${risk.category ?? "-"} / ${risk.severity ?? "-"} / ${risk.reviewStatus ?? "-"}`,
                      status: "added",
                    })),
                    ...result.riskChanges.changed.slice(0, 4).map((risk) => ({
                      key: `changed-${risk.code ?? risk.riskIdAfter}`,
                      title: risk.code ?? risk.riskIdAfter ?? "risk",
                      body: (risk.fieldChanges ?? []).map((change) => `${change.field}: ${shortValue(change.before)} -> ${shortValue(change.after)}`).join(" / "),
                      status: risk.material ? "material" : "changed",
                    })),
                  ]}
                />
                <DiffList
                  title="Decision / Artifacts / Report"
                  emptyLabel="No decision, artifact, or report section changes."
                  items={[
                    ...(result.decisionChange.changed ? [{
                      key: "decision",
                      title: "Credit Decision",
                      body: `${shortValue(result.decisionChange.before?.decision)} -> ${shortValue(result.decisionChange.after?.decision)}`,
                      status: "changed",
                    }] : []),
                    ...result.artifactChanges.added.slice(0, 4).map((key) => ({
                      key: `artifact-added-${key}`,
                      title: key,
                      body: "Artifact added in compare snapshot.",
                      status: "added",
                    })),
                    ...result.reportChanges.addedSections.slice(0, 4).map((key) => ({
                      key: `section-added-${key}`,
                      title: key,
                      body: "Report section added in compare snapshot.",
                      status: "section_added",
                    })),
                  ]}
                />
              </div>
            </>
          ) : null}
        </>
      )}
    </section>
  );
}

function DiffList({
  title,
  emptyLabel,
  items,
}: {
  title: string;
  emptyLabel: string;
  items: Array<{ key: string; title: string; body: string; status?: string }>;
}) {
  return (
    <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
      <p className="text-xs font-bold uppercase text-slate-500">{title}</p>
      {items.length ? (
        <ul className="mt-3 space-y-2">
          {items.map((item) => (
            <li key={item.key} className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-bold text-slate-950 dark:text-white">{item.title}</p>
                {item.status ? <StatusBadge status={item.status} /> : null}
              </div>
              <p className="mt-1 text-xs font-semibold text-slate-600 dark:text-slate-300">{item.body || "-"}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm font-semibold text-emerald-700 dark:text-emerald-300">{emptyLabel}</p>
      )}
    </div>
  );
}

function ApprovalReadinessDashboard({
  gate,
  riskRegister,
  creditReviewDecision,
  handoffs,
}: {
  gate?: ApprovalReadinessGate;
  riskRegister?: RiskRegister;
  creditReviewDecision?: CreditReviewDecision;
  handoffs: AgentHandoff[];
}) {
  const riskStatus = gate?.summary.riskRegisterStatus ?? riskRegister?.status ?? "not_checked";
  const creditDecision = gate?.summary.creditDecision ?? creditReviewDecision?.decision ?? "not_checked";
  const unresolvedHandoffCount = gate?.summary.unresolvedHandoffReviewCount ?? handoffs.filter(isUnresolvedHandoff).length;
  const unresolvedRiskCount =
    gate?.summary.unresolvedRiskReviewCount ??
    (riskRegister?.risks ?? []).filter(isUnresolvedRisk).length;
  const dealStateRecordCount = gate?.summary.dealStateRecordCount ?? 0;
  const dealStateBlockingRiskCount = gate?.summary.dealStateBlockingRiskCount ?? 0;
  const dealStateLocatorMissingCount = gate?.summary.dealStateSourceLocatorMissingCount ?? 0;
  const dealStateReady = dealStateRecordCount > 0 && dealStateBlockingRiskCount === 0;
  const landGateCodes = [
    "LAND_PNU_READY",
    "LAND_OFFICIAL_PRICE_READY",
    "LAND_REGISTRY_EVIDENCE_READY",
    "LAND_ACQUISITION_READY",
    "LAND_EVIDENCE_READY",
  ];
  const landGates = (gate?.gates ?? []).filter((item) => landGateCodes.includes(item.code));
  const landBlockingCount = landGates.filter((item) => !item.pass && item.severity === "blocking").length;
  const landWarningCount = landGates.filter((item) => !item.pass && item.severity === "warning").length;
  const landReady = gate?.summary.landEvidenceReady ?? (landGates.length > 0 && landBlockingCount === 0);

  const cards = [
    {
      title: "Land Evidence",
      status: readinessStatus(landReady, landBlockingCount, landWarningCount),
      description: landReady
        ? "PNU, official price, registry evidence, and acquisition readiness are cleared."
        : `${landBlockingCount} blocking / ${landWarningCount} warning land evidence item(s).`,
      meta: landGates.map((item) => `${item.code}: ${item.pass ? "pass" : item.severity}`).join(" / ") || "No land evidence gate available.",
    },
    {
      title: "Risk Review",
      status: readinessStatus(unresolvedRiskCount === 0 && riskStatus !== "blocking", riskStatus === "blocking" ? 1 : unresolvedRiskCount, 0),
      description: unresolvedRiskCount === 0
        ? `Risk Register status is ${riskStatus}.`
        : `${unresolvedRiskCount} material risk row(s) still need approval, waiver, or resolution.`,
      meta: `Risk Register: ${riskStatus}`,
    },
    {
      title: "Credit Decision",
      status: creditDecisionStatus(creditDecision),
      description: creditDecision === "reject_recommended" || creditDecision === "rework_required"
        ? `Credit Review is ${creditDecision}; cure or rerun is required.`
        : `Credit Review is ${creditDecision}.`,
      meta: creditReviewDecision?.decisionReasons?.slice(0, 2).join(" / ") || "No decision reasons available.",
    },
    {
      title: "Handoff Review",
      status: readinessStatus(unresolvedHandoffCount === 0, unresolvedHandoffCount, 0),
      description: unresolvedHandoffCount === 0
        ? "Required agent handoffs are reviewed or non-blocking."
        : `${unresolvedHandoffCount} handoff review(s) remain unresolved.`,
      meta: `${handoffs.length} total handoff(s)`,
    },
    {
      title: "Deal State",
      status: readinessStatus(dealStateReady, dealStateBlockingRiskCount, dealStateLocatorMissingCount),
      description: dealStateRecordCount > 0
        ? `${dealStateRecordCount} record(s), ${dealStateBlockingRiskCount} blocking risk(s), ${dealStateLocatorMissingCount} missing locator(s).`
        : "Deal State records are not materialized yet.",
      meta: `Evidence ${gate?.summary.dealStateEvidenceCount ?? 0} / Metric ${gate?.summary.dealStateMetricCount ?? 0} / Assumption ${gate?.summary.dealStateAssumptionCount ?? 0}`,
    },
  ];

  return (
    <section className={`rounded-lg border p-5 shadow-sm ${
      gate?.pass
        ? "border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/20"
        : "border-rose-200 bg-white dark:border-rose-900 dark:bg-slate-900"
    }`}>
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-center gap-2">
          <ShieldCheck className={`h-5 w-5 ${gate?.pass ? "text-emerald-700 dark:text-emerald-300" : "text-rose-600 dark:text-rose-300"}`} />
          <div>
            <h3 className="font-bold text-slate-950 dark:text-white">Approval Readiness Dashboard</h3>
            <p className="text-sm text-slate-500">
              Credit approval readiness separated from report completeness.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={gate?.status ?? "not_checked"} />
          <StatusBadge status={gate?.pass ? "ready" : "blocking"} />
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-5">
        <Mini label="Blocking" value={`${gate?.summary.blockingIssueCount ?? "-"}`} />
        <Mini label="Needs Review" value={`${(gate?.summary.warningCount ?? 0) + unresolvedHandoffCount + unresolvedRiskCount}`} />
        <Mini label="Risk Reviews" value={`${unresolvedRiskCount}`} />
        <Mini label="Handoff Reviews" value={`${unresolvedHandoffCount}`} />
        <Mini label="Deal State" value={`${dealStateRecordCount || "-"}`} />
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-5">
        {cards.map((card) => (
          <ReadinessCard key={card.title} {...card} />
        ))}
      </div>

      {gate?.blockers?.length || gate?.warnings?.length ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <GateIssueList title="Blocking Items" items={gate.blockers ?? []} />
          <GateIssueList title="Needs Review / Warnings" items={gate.warnings ?? []} />
        </div>
      ) : null}

      {gate?.requiredActions?.length ? (
        <div className="mt-4 rounded-lg bg-slate-50 p-4 dark:bg-slate-800/70">
          <p className="text-xs font-bold uppercase text-slate-500">Required Actions</p>
          <ul className="mt-2 space-y-1 text-sm font-semibold text-slate-800 dark:text-slate-200">
            {gate.requiredActions.slice(0, 6).map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function ReadinessCard({
  title,
  status,
  description,
  meta,
}: {
  title: string;
  status: "ready" | "needs_review" | "blocking";
  description: string;
  meta: string;
}) {
  const tone = {
    ready: "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100",
    needs_review: "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100",
    blocking: "border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-100",
  }[status];
  return (
    <div className={`rounded-lg border p-4 ${tone}`}>
      <div className="flex items-center justify-between gap-3">
        <p className="font-bold">{title}</p>
        <StatusBadge status={status} />
      </div>
      <p className="mt-3 text-sm font-semibold leading-5">{description}</p>
      <p className="mt-3 line-clamp-3 text-xs opacity-80">{meta}</p>
    </div>
  );
}

function GateIssueList({ title, items }: { title: string; items: ApprovalReadinessGateCheck[] }) {
  return (
    <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
      <p className="text-xs font-bold uppercase text-slate-500">{title}</p>
      {items.length ? (
        <ul className="mt-3 space-y-2">
          {items.slice(0, 6).map((item) => (
            <li key={item.code} className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-bold text-slate-950 dark:text-white">{item.code}</p>
                <StatusBadge status={item.severity} />
              </div>
              <p className="mt-1 text-xs font-semibold text-slate-600 dark:text-slate-300">{item.message}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm font-semibold text-emerald-700 dark:text-emerald-300">No items.</p>
      )}
    </div>
  );
}

function readinessStatus(ready: boolean, blockingCount: number, warningCount: number): "ready" | "needs_review" | "blocking" {
  if (ready) return warningCount > 0 ? "needs_review" : "ready";
  return blockingCount > 0 ? "blocking" : "needs_review";
}

function creditDecisionStatus(decision: string): "ready" | "needs_review" | "blocking" {
  if (decision === "reject_recommended" || decision === "rework_required") return "blocking";
  if (decision === "conditional_approval") return "needs_review";
  if (decision === "approve") return "ready";
  return "needs_review";
}

function isUnresolvedHandoff(handoff: AgentHandoff) {
  return (
    handoff.reviewStatus === "rejected"
    || handoff.reviewStatus === "needs_revision"
    || (handoff.requiresHumanReview && handoff.reviewStatus !== "approved")
    || ((handoff.status === "blocked" || handoff.status === "needs_review") && handoff.reviewStatus !== "approved")
  );
}

function isUnresolvedRisk(risk: RiskRegisterRisk) {
  return (
    risk.requiresHumanReview
    && !["approved", "waived", "resolved"].includes(risk.reviewStatus ?? risk.status ?? "open")
  );
}

function ApprovalPanel({
  approval,
  approvalReadinessGate,
  canApprove,
  unresolvedHandoffCount,
  unresolvedRiskCount,
  status,
  approvalComment,
  approving,
  onApprovalCommentChange,
  onApprove,
}: {
  approval: RunSummary["approval"];
  approvalReadinessGate?: ApprovalReadinessGate;
  canApprove: boolean;
  unresolvedHandoffCount: number;
  unresolvedRiskCount: number;
  status: string;
  approvalComment: string;
  approving: boolean;
  onApprovalCommentChange: (value: string) => void;
  onApprove: () => void;
}) {
  if (approval) {
    return (
      <section className="rounded-lg border border-emerald-200 bg-emerald-50 p-5 shadow-sm dark:border-emerald-900 dark:bg-emerald-950/20">
        <div className="flex items-center gap-2">
          <UserCheck className="h-5 w-5 text-emerald-700 dark:text-emerald-300" />
          <h3 className="font-bold text-emerald-900 dark:text-emerald-200">Run Approved</h3>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <Mini label="Approved By" value={approval.approved_by} />
          <Mini label="Approved At" value={fmtDate(approval.approved_at)} />
          <Mini label="Comment" value={approval.comment || "-"} />
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2">
        <UserCheck className="h-5 w-5 text-slate-700 dark:text-slate-200" />
        <h3 className="font-bold text-slate-950 dark:text-white">Run Approval</h3>
      </div>
      {!canApprove ? (
        <p className="mt-3 text-sm text-slate-500">
          {approvalReadinessGate?.pass === false
            ? `Approval readiness is blocked by ${approvalReadinessGate.summary.blockingIssueCount} blocking item(s). Clear the dashboard before final approval.`
            : unresolvedHandoffCount > 0
            ? `${unresolvedHandoffCount} unresolved handoff review(s) must be cleared before final approval.`
            : unresolvedRiskCount > 0
              ? `${unresolvedRiskCount} unresolved risk register review(s) must be approved, waived, or resolved before final approval.`
            : `Current status is \`${status}\`. Only completed runs can be approved.`}
        </p>
      ) : (
        <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_auto]">
          <label className="block">
            <span className="text-xs font-semibold text-slate-500">Comment</span>
            <input
              value={approvalComment}
              onChange={(event) => onApprovalCommentChange(event.target.value)}
              placeholder="Reviewed agent artifacts and report quality."
              className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
            />
          </label>
          <button
            type="button"
            onClick={onApprove}
            disabled={approving}
            className="mt-5 inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 text-sm font-semibold text-white shadow-sm hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {approving ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserCheck className="h-4 w-4" />}
            Approve
          </button>
          <p className="lg:col-span-2 text-xs font-semibold text-slate-500">
            승인자는 입력값이 아니라 현재 로그인 세션과 PostgreSQL membership으로 결정됩니다. 작성자와 동일한 사용자는 승인할 수 없습니다.
          </p>
        </div>
      )}
    </section>
  );
}

function IconButton({
  label,
  icon,
  tone,
  disabled,
  onClick,
}: {
  label: string;
  icon: ReactNode;
  tone: "approve" | "warn" | "reject";
  disabled?: boolean;
  onClick: () => void;
}) {
  const toneClass = {
    approve: "border-emerald-200 text-emerald-700 hover:bg-emerald-50 dark:border-emerald-900 dark:text-emerald-300",
    warn: "border-amber-200 text-amber-700 hover:bg-amber-50 dark:border-amber-900 dark:text-amber-300",
    reject: "border-rose-200 text-rose-700 hover:bg-rose-50 dark:border-rose-900 dark:text-rose-300",
  }[tone];
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex h-9 w-9 items-center justify-center rounded-lg border bg-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-slate-950 ${toneClass}`}
    >
      {icon}
    </button>
  );
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-sm font-semibold text-slate-500">{label}</p>
      <p className={`mt-3 text-2xl font-black tracking-tight ${tone ?? "text-slate-950 dark:text-white"}`}>{value}</p>
    </div>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <p className="mt-1 text-sm font-bold text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const tone = normalized === "completed"
    || normalized === "approved"
    || normalized === "pass"
    || normalized === "auto_validated"
    || normalized === "ready"
    || normalized === "ready_for_approval"
    || normalized === "approve"
    || normalized === "resolved"
    || normalized === "waived"
    ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300"
    : normalized === "failed"
      || normalized === "rejected"
      || normalized === "reject_recommended"
      || normalized === "blocking"
      || normalized === "not_ready_for_approval"
      ? "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300"
      : "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300";
  return <span className={`rounded-md border px-2 py-0.5 text-xs font-semibold ${tone}`}>{status}</span>;
}

function ArtifactPanel({ icon, title, rows }: { icon: ReactNode; title: string; rows: Array<[string, string]> }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2">
        {icon}
        <h3 className="font-bold text-slate-950 dark:text-white">{title}</h3>
      </div>
      <dl className="mt-4 space-y-3">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-3 text-sm">
            <dt className="text-slate-500">{label}</dt>
            <dd className="text-right font-bold text-slate-950 dark:text-white">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

interface SensitivityMatricesPanelProps {
  evaluation: ScenarioEvaluation;
}

interface RestructuringCurePanelProps {
  plan?: RestructuringPlan;
  cures: RestructuringCureRecord[];
  runId: string;
  reviewer: string;
  saving: boolean;
  onReviewerChange: (value: string) => void;
  onSave: (payload: RestructuringCureRecord) => void;
}

const cureTypeOptions = [
  { value: "senior_reduction", label: "선순위 대출금 감액" },
  { value: "equity_dsra_injection", label: "Equity / DSRA 증액" },
  { value: "additional_collateral", label: "추가 담보 제공" },
  { value: "bulk_sale_forward_sale", label: "Bulk Sale / 선매입 증빙" },
  { value: "refinancing_evidence", label: "준공 후 Refinancing 증빙" },
  { value: "repayment_waterfall_strengthening", label: "상환계좌 배분 강화" },
];

function selectedCureLabel(cureType: string) {
  return cureTypeOptions.find((option) => option.value === cureType)?.label ?? cureType;
}

function numericField(source: Record<string, unknown> | undefined, keys: string[]) {
  if (!source) return undefined;
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string") {
      const parsed = Number(value.replaceAll(",", ""));
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return undefined;
}

function recommendedCureAmount(plan: RestructuringPlan | undefined, recommendations: RestructuringRecommendation[]) {
  const fromSenior = numericField(plan?.seniorShortfallFix, ["requiredCureAmount", "recommendedReductionAmount", "shortfall", "amount"]);
  if (fromSenior) return fromSenior;
  const fromUnsold = numericField(plan?.unsoldLtvFix, ["requiredCureAmount", "requiredCollateralAmount", "ltvExcessAmount", "amount"]);
  if (fromUnsold) return fromUnsold;
  return recommendations.find((item) => typeof item.amount === "number")?.amount;
}

function recommendedRiskCodes(plan: RestructuringPlan | undefined, recommendations: RestructuringRecommendation[]) {
  const codes = new Set<string>();
  for (const blocker of plan?.approvalPath?.remainingBlockers ?? []) {
    if (blocker.includes("SENIOR")) codes.add("SENIOR_SHORTFALL");
    if (blocker.includes("UNSOLD") || blocker.includes("LTV")) codes.add("UNSOLD_LTV_BREACH");
  }
  for (const item of recommendations) {
    if (item.targetRiskCode) codes.add(item.targetRiskCode);
    for (const code of item.riskCodes ?? []) codes.add(code);
  }
  if (codes.size === 0) {
    codes.add("SENIOR_SHORTFALL");
    codes.add("UNSOLD_LTV_BREACH");
  }
  return Array.from(codes);
}

function RestructuringCurePanel({
  plan,
  cures,
  runId,
  reviewer,
  saving,
  onReviewerChange,
  onSave,
}: RestructuringCurePanelProps) {
  const recommendations = plan?.recommendations ?? [];
  const remainingBlockers = plan?.approvalPath?.remainingBlockers ?? [];
  const adoptedSummary = plan?.adoptedCures;
  const activeCures = cures.filter((cure) => (cure.status ?? "active") === "active");
  const sourceBackedCures = activeCures.filter((cure) => Boolean(cure.sourceUrlOrEvidenceId || cure.evidenceIds?.length || cure.sourceLocators?.length));
  const suggestedAmount = recommendedCureAmount(plan, recommendations);
  const suggestedRiskCodes = recommendedRiskCodes(plan, recommendations);
  const [cureType, setCureType] = useState(cureTypeOptions[0]?.value ?? "senior_reduction");
  const [amount, setAmount] = useState(suggestedAmount ? String(Math.round(suggestedAmount)) : "");
  const [riskCodes, setRiskCodes] = useState(suggestedRiskCodes.join(", "));
  const [sourceRef, setSourceRef] = useState("");
  const [memo, setMemo] = useState("");

  useEffect(() => {
    if (!amount && suggestedAmount) setAmount(String(Math.round(suggestedAmount)));
    if (!riskCodes && suggestedRiskCodes.length > 0) setRiskCodes(suggestedRiskCodes.join(", "));
  }, [amount, riskCodes, suggestedAmount, suggestedRiskCodes]);

  const submit = () => {
    const parsedAmount = numberOrUndefined(amount);
    onSave({
      cureType,
      status: "active",
      riskCodes: riskCodes
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      amount: parsedAmount,
      amountUnit: "KRW",
      description: memo || selectedCureLabel(cureType),
      sourceName: "manual credit review",
      sourceType: "manual_review",
      sourceUrlOrEvidenceId: sourceRef.trim(),
      evidenceIds: sourceRef.trim() ? [sourceRef.trim()] : [],
      reviewedBy: reviewer || "credit_officer",
      appliesToRunId: runId,
    });
  };

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-xs font-bold uppercase text-slate-500">Financial Cure Workflow</p>
          <h3 className="mt-1 text-lg font-bold text-slate-950 dark:text-white">금융구조 보강안 선택</h3>
          <p className="mt-1 text-sm text-slate-500">
            선순위 부족, 미분양 LTV 초과 등 금융 리스크를 해소하기 위한 채택 보강안을 저장합니다. 저장만으로 blocker가 자동 해소되지는 않으며, 증빙과 재계산 결과가 함께 필요합니다.
          </p>
        </div>
        <div className="grid grid-cols-3 gap-2 text-center">
          <Mini label="저장 보강안" value={`${activeCures.length}`} />
          <Mini label="증빙 보강안" value={`${sourceBackedCures.length}`} />
          <Mini label="Run 반영" value={`${adoptedSummary?.sourceBackedActiveCount ?? adoptedSummary?.sourceBackedActiveCureCount ?? 0}`} />
        </div>
      </div>

      {remainingBlockers.length > 0 ? (
        <div className="mt-4 rounded-lg border border-rose-100 bg-rose-50 p-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-300">
          <p className="font-bold">남은 금융 blocker</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {remainingBlockers.map((blocker) => (
              <span key={blocker} className="rounded-md bg-white px-2 py-1 text-xs font-bold dark:bg-rose-950">
                {blocker}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {recommendations.length > 0 ? (
        <div className="mt-4 grid gap-3 lg:grid-cols-3">
          {recommendations.slice(0, 3).map((item, index) => (
            <div key={`${item.code ?? index}`} className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
              <p className="text-xs font-bold uppercase text-slate-500">{item.code ?? `CURE-${index + 1}`}</p>
              <p className="mt-1 text-sm font-bold text-slate-950 dark:text-white">{item.title ?? item.action ?? "보강안"}</p>
              <p className="mt-1 line-clamp-3 text-xs text-slate-500">{item.description ?? item.action ?? "-"}</p>
              {item.amount ? <p className="mt-2 text-xs font-bold text-indigo-700 dark:text-indigo-300">권고 금액: {fmtEok(item.amount)}</p> : null}
            </div>
          ))}
        </div>
      ) : null}

      <div className="mt-5 grid gap-3 lg:grid-cols-[1.2fr_1fr_1fr]">
        <label className="space-y-1 text-sm">
          <span className="font-bold text-slate-700 dark:text-slate-300">보강 유형</span>
          <select
            value={cureType}
            onChange={(event) => setCureType(event.target.value)}
            className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
          >
            {cureTypeOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-bold text-slate-700 dark:text-slate-300">보강 금액 KRW</span>
          <input
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            placeholder="예: 2000000000"
            className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
          />
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-bold text-slate-700 dark:text-slate-300">검토자</span>
          <input
            value={reviewer}
            onChange={(event) => onReviewerChange(event.target.value)}
            className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
          />
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-bold text-slate-700 dark:text-slate-300">대상 리스크 코드</span>
          <input
            value={riskCodes}
            onChange={(event) => setRiskCodes(event.target.value)}
            placeholder="SENIOR_SHORTFALL, UNSOLD_LTV_BREACH"
            className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
          />
        </label>
        <label className="space-y-1 text-sm lg:col-span-2">
          <span className="font-bold text-slate-700 dark:text-slate-300">증빙 ID / 출처</span>
          <input
            value={sourceRef}
            onChange={(event) => setSourceRef(event.target.value)}
            placeholder="예: dsra-confirmation-2026-06-09 또는 forward-sale-loi-p.3"
            className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
          />
        </label>
        <label className="space-y-1 text-sm lg:col-span-3">
          <span className="font-bold text-slate-700 dark:text-slate-300">심사 메모</span>
          <textarea
            value={memo}
            onChange={(event) => setMemo(event.target.value)}
            rows={2}
            placeholder="보강안 채택 사유와 재실행 시 확인해야 할 계산 조건을 입력하세요."
            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
          />
        </label>
      </div>

      <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-slate-500">
          증빙 ID가 없는 보강안은 감사 추적용으로 저장되지만, source-backed cure로 계산되지 않습니다.
        </p>
        <button
          type="button"
          onClick={submit}
          disabled={saving || !sourceRef.trim()}
          className="inline-flex items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 py-2 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white dark:text-slate-950"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
          보강안 저장
        </button>
      </div>

      {activeCures.length > 0 ? (
        <div className="mt-5 overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
          <div className="grid grid-cols-[1.2fr_1fr_1fr_1fr] bg-slate-50 px-4 py-2 text-xs font-bold uppercase text-slate-500 dark:bg-slate-800/70">
            <span>보강 유형</span>
            <span>대상 리스크</span>
            <span className="text-right">금액</span>
            <span className="text-right">증빙</span>
          </div>
          {activeCures.map((cure) => (
            <div key={cure.cureId ?? `${cure.cureType}-${cure.updatedAt}`} className="grid grid-cols-[1.2fr_1fr_1fr_1fr] gap-3 border-t border-slate-200 px-4 py-3 text-sm dark:border-slate-800">
              <span className="font-bold text-slate-900 dark:text-white">{selectedCureLabel(cure.cureType)}</span>
              <span className="text-slate-600 dark:text-slate-400">{(cure.riskCodes ?? []).join(", ") || "-"}</span>
              <span className="text-right font-semibold text-slate-900 dark:text-white">{fmtEok(cure.amount)}</span>
              <span className="truncate text-right text-xs text-slate-500">{cure.sourceUrlOrEvidenceId || cure.evidenceIds?.[0] || "-"}</span>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
function SensitivityMatricesPanel({ evaluation }: SensitivityMatricesPanelProps) {
  const [activeTab, setActiveTab] = useState<"waterfall" | "ltv" | "recovery">("waterfall");
  const [selectedScenarioId, setSelectedScenarioId] = useState<string>("");

  const waterfallScenarios = useMemo(() => evaluation.waterfallScenarios ?? [], [evaluation.waterfallScenarios]);
  const unsoldLtvMatrix = evaluation.unsoldLtvMatrix ?? null;
  const trancheRecoveryMatrix = useMemo(() => evaluation.trancheRecoveryMatrix ?? [], [evaluation.trancheRecoveryMatrix]);

  useEffect(() => {
    const firstScenario = waterfallScenarios[0];
    if (firstScenario && !selectedScenarioId) {
      setSelectedScenarioId(firstScenario.scenarioId);
    }
  }, [waterfallScenarios, selectedScenarioId]);

  const selectedScenario = waterfallScenarios.find((scenario) => scenario.scenarioId === selectedScenarioId);

  const ltvRows = unsoldLtvMatrix?.rows ?? [];
  const uniqueSaleRates = Array.from(new Set(ltvRows.map((row) => row.saleRate))).sort((a, b) => b - a);
  const uniqueDiscountRates = Array.from(new Set(ltvRows.map((row) => row.discountRate))).sort((a, b) => a - b);

  const getLtvCell = (saleRate: number, discountRate: number) => {
    return ltvRows.find((row) => Math.abs(row.saleRate - saleRate) < 0.001 && Math.abs(row.discountRate - discountRate) < 0.001);
  };

  const getLtvRiskClass = (riskLevel: string) => {
    switch (riskLevel) {
      case "safe":
        return "bg-emerald-50 text-emerald-800 border-emerald-100 dark:bg-emerald-950/20 dark:text-emerald-300 dark:border-emerald-900";
      case "caution":
        return "bg-amber-50 text-amber-800 border-amber-100 dark:bg-amber-950/20 dark:text-amber-300 dark:border-amber-900";
      case "high":
        return "bg-orange-50 text-orange-800 border-orange-100 dark:bg-orange-950/20 dark:text-orange-300 dark:border-orange-900";
      case "critical":
        return "bg-rose-50 text-rose-800 border-rose-100 dark:bg-rose-950/20 dark:text-rose-300 dark:border-rose-900";
      default:
        return "bg-slate-50 text-slate-800 border-slate-100 dark:bg-slate-900 dark:text-slate-300 dark:border-slate-800";
    }
  };

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between border-b border-slate-100 dark:border-slate-800 pb-4">
        <div className="flex items-center gap-2">
          <GitBranch className="h-5 w-5 text-indigo-600" />
          <div>
            <h3 className="font-bold text-slate-950 dark:text-white">금융 민감도 및 Stress 시나리오</h3>
            <p className="text-sm text-slate-500">상환 Waterfall, 미분양 LTV, Tranche별 회수율을 함께 검토합니다.</p>
          </div>
        </div>
        <div className="flex rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
          <button
            type="button"
            onClick={() => setActiveTab("waterfall")}
            className={`rounded-md px-3 py-1.5 text-xs font-bold transition-all ${
              activeTab === "waterfall"
                ? "bg-white text-slate-950 shadow dark:bg-slate-900 dark:text-white"
                : "text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200"
            }`}
          >
            상환 Waterfall
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("ltv")}
            className={`rounded-md px-3 py-1.5 text-xs font-bold transition-all ${
              activeTab === "ltv"
                ? "bg-white text-slate-950 shadow dark:bg-slate-900 dark:text-white"
                : "text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200"
            }`}
          >
            미분양 LTV
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("recovery")}
            className={`rounded-md px-3 py-1.5 text-xs font-bold transition-all ${
              activeTab === "recovery"
                ? "bg-white text-slate-950 shadow dark:bg-slate-900 dark:text-white"
                : "text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200"
            }`}
          >
            Tranche 회수율
          </button>
        </div>
      </div>

      <div className="mt-4">
        {activeTab === "waterfall" && (
          <div className="space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between bg-slate-50 p-3 rounded-lg dark:bg-slate-800/40">
              <span className="text-sm font-bold text-slate-800 dark:text-slate-200">시뮬레이션 시나리오</span>
              <select
                value={selectedScenarioId}
                onChange={(e) => setSelectedScenarioId(e.target.value)}
                className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
              >
                {waterfallScenarios.map((s) => (
                  <option key={s.scenarioId} value={s.scenarioId}>
                    분양률 {(s.saleRate * 100).toFixed(0)}% / 할인율 {(s.discountRate * 100).toFixed(0)}%
                  </option>
                ))}
              </select>
            </div>

            {selectedScenario ? (
              <div className="space-y-4">
                <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
                  <Mini label="상환 가능 현금" value={`${(selectedScenario.cashAvailable / 100_000_000).toLocaleString("ko-KR")}억원`} />
                  <Mini label="총 청구액" value={`${(selectedScenario.totalClaimAmount / 100_000_000).toLocaleString("ko-KR")}억원`} />
                  <Mini label="부족액" value={`${(selectedScenario.totalShortfall / 100_000_000).toLocaleString("ko-KR")}억원`} />
                  <Mini label="잔여 현금" value={`${(selectedScenario.remainingCash / 100_000_000).toLocaleString("ko-KR")}억원`} />
                </div>

                <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
                  <div className="grid grid-cols-[80px_1fr_1.2fr_1.2fr_1.2fr_100px] bg-slate-50 px-4 py-2 text-xs font-bold uppercase text-slate-500 dark:bg-slate-800/70">
                    <span>순위</span>
                    <span>Tranche</span>
                    <span className="text-right">원금 청구액</span>
                    <span className="text-right">총 청구액</span>
                    <span className="text-right">상환 수령액</span>
                    <span className="text-right">상태</span>
                  </div>
                  <div className="divide-y divide-slate-200 dark:divide-slate-800">
                    {selectedScenario.rows.map((row) => (
                      <div key={row.tranche} className="grid grid-cols-[80px_1fr_1.2fr_1.2fr_1.2fr_100px] items-center gap-3 px-4 py-3 text-sm">
                        <span className="font-bold text-slate-500">{row.rank}</span>
                        <span className="font-bold text-slate-900 dark:text-white">{row.tranche}</span>
                        <span className="text-right text-slate-700 dark:text-slate-300">{(row.principalClaim / 100_000_000).toLocaleString("ko-KR")}억원</span>
                        <span className="text-right font-semibold text-slate-900 dark:text-white">{(row.totalClaim / 100_000_000).toLocaleString("ko-KR")}억원</span>
                        <span className="text-right font-black text-indigo-600 dark:text-indigo-400">{(row.paymentReceived / 100_000_000).toLocaleString("ko-KR")}억원</span>
                        <span className="text-right"><StatusBadge status={row.status} /></span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        )}

        {activeTab === "ltv" && unsoldLtvMatrix && (
          <div className="space-y-4">
            <div className="rounded-lg bg-indigo-50/50 p-4 border border-indigo-100 dark:bg-indigo-950/20 dark:border-indigo-950">
              <h4 className="text-sm font-bold text-indigo-950 dark:text-indigo-300">미분양 LTV 리스크 요약</h4>
              <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                Base Case LTV: <span className="font-bold text-indigo-700 dark:text-indigo-400">{(unsoldLtvMatrix.summary.baseCaseLtv * 100).toFixed(1)}%</span> |
                Stress Case LTV: <span className="font-bold text-rose-600 dark:text-rose-400">{(unsoldLtvMatrix.summary.stressCaseLtv * 100).toFixed(1)}%</span> |
                기준 초과 시나리오 <span className="font-bold text-rose-600 dark:text-rose-400">{unsoldLtvMatrix.summary.breachCount}</span> 건
              </p>
            </div>

            <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
              <table className="w-full min-w-[600px] border-collapse text-sm text-left">
                <thead>
                  <tr className="bg-slate-50 border-b border-slate-200 dark:bg-slate-800/70 dark:border-slate-800 text-xs font-bold uppercase text-slate-500">
                    <th className="p-3 border-r border-slate-200 dark:border-slate-800">분양률 / 할인율</th>
                    {uniqueDiscountRates.map((discRate) => (
                      <th key={discRate} className="p-3 text-center">{(discRate * 100).toFixed(0)}% 할인</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                  {uniqueSaleRates.map((saleRate) => (
                    <tr key={saleRate} className="hover:bg-slate-50/50 dark:hover:bg-slate-900/50">
                      <td className="p-3 font-bold text-slate-900 dark:text-white border-r border-slate-200 dark:border-slate-800">
                        분양률 {(saleRate * 100).toFixed(0)}% (미분양 {(100 - saleRate * 100).toFixed(0)}%)
                      </td>
                      {uniqueDiscountRates.map((discRate) => {
                        const cell = getLtvCell(saleRate, discRate);
                        if (!cell) return <td key={discRate} className="p-3 text-center text-slate-400">-</td>;
                        return (
                          <td key={discRate} className="p-2 text-center">
                            <div className={`mx-auto max-w-[100px] rounded border px-2 py-1.5 text-xs font-black shadow-sm ${getLtvRiskClass(cell.riskLevel)}`}>
                              {(cell.unsoldLtv * 100).toFixed(1)}%
                              <span className="block mt-0.5 text-[9px] font-semibold opacity-70 uppercase">{cell.riskLevel}</span>
                            </div>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {activeTab === "recovery" && (
          <div className="space-y-4">
            <div className="rounded-lg bg-emerald-50/50 p-4 border border-emerald-100 dark:bg-emerald-950/20 dark:border-emerald-950">
              <h4 className="text-sm font-bold text-emerald-950 dark:text-emerald-300">Tranche 최종 회수율 분석</h4>
              <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                상환순위에 따라 선순위부터 배분합니다. 분양률 저하 또는 할인분양이 커질수록 중순위·후순위 회수율이 먼저 하락합니다.
              </p>
            </div>

            <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
              <div className="grid grid-cols-[1.2fr_1fr_1.5fr_2fr] bg-slate-50 px-4 py-2 text-xs font-bold uppercase text-slate-500 dark:bg-slate-800/70">
                <span>시나리오</span>
                <span>조건</span>
                <span className="text-right">회수 재원</span>
                <span className="text-right">Tranche별 회수율</span>
              </div>
              <div className="divide-y divide-slate-200 dark:divide-slate-800">
                {trancheRecoveryMatrix.map((row) => (
                  <div key={row.scenarioId} className="grid grid-cols-[1.2fr_1fr_1.5fr_2fr] items-center gap-3 px-4 py-3 text-sm">
                    <span className="font-bold text-slate-900 dark:text-white">{row.scenarioId}</span>
                    <span className="text-slate-600 dark:text-slate-400">
                      분양 {(row.saleRate * 100).toFixed(0)}% / 할인 {(row.discountRate * 100).toFixed(0)}%
                    </span>
                    <span className="text-right font-semibold text-slate-900 dark:text-white">
                      {(row.recoveryPool / 100_000_000).toLocaleString("ko-KR")}억원
                    </span>
                    <div className="flex flex-wrap justify-end gap-1.5">
                      {row.tranches.map((t) => (
                        <div
                          key={t.tranche}
                          className={`rounded border px-2 py-1 text-xs font-bold ${
                            t.recoveryRatio >= 1.0
                              ? "bg-emerald-50 text-emerald-800 border-emerald-100 dark:bg-emerald-950/20 dark:text-emerald-300 dark:border-emerald-900"
                              : t.recoveryRatio > 0.0
                                ? "bg-amber-50 text-amber-800 border-amber-100 dark:bg-amber-950/20 dark:text-amber-300 dark:border-amber-900"
                                : "bg-rose-50 text-rose-800 border-rose-100 dark:bg-rose-950/20 dark:text-rose-300 dark:border-rose-900"
                          }`}
                        >
                          {t.tranche}: {(t.recoveryRatio * 100).toFixed(0)}%
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
