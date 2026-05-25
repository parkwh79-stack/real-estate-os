"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useParams } from "next/navigation";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  Database,
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
    riskRegister?: RiskRegister;
    creditReviewDecision?: CreditReviewDecision;
  };
}

interface ProjectResponse {
  project?: {
    name?: string;
  };
}

const apiBase = "http://127.0.0.1:8000";

function fmtPercent(value?: number) {
  if (value === undefined || Number.isNaN(value)) return "-";
  return `${(value * 100).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%`;
}

function fmtEok(value?: number) {
  if (value === undefined || Number.isNaN(value)) return "-";
  return `${(value / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 0 })} eok`;
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

export default function ProjectOrchestrationPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [project, setProject] = useState<ProjectResponse>({});
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState<OrchestrationRun | null>(null);
  const [marketEvidence, setMarketEvidence] = useState<MarketEvidenceRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [approving, setApproving] = useState(false);
  const [reviewingKey, setReviewingKey] = useState<string | null>(null);
  const [reviewingHandoffId, setReviewingHandoffId] = useState<string | null>(null);
  const [reviewingRiskId, setReviewingRiskId] = useState<string | null>(null);
  const [approvedBy, setApprovedBy] = useState("credit_officer");
  const [approvalComment, setApprovalComment] = useState("");
  const [evidenceReviewer, setEvidenceReviewer] = useState("market_analyst");
  const [handoffReviewer, setHandoffReviewer] = useState("credit_officer");
  const [riskReviewer, setRiskReviewer] = useState("credit_officer");
  const [error, setError] = useState<string | null>(null);

  const loadMarketEvidence = useCallback(async (runId?: string) => {
    if (!runId) {
      setMarketEvidence([]);
      return;
    }
    const evidenceRes = await fetch(`${apiBase}/projects/${projectId}/market-evidence?run_id=${encodeURIComponent(runId)}`, {
      cache: "no-store",
    });
    if (!evidenceRes.ok) throw new Error("Unable to load market evidence records.");
    setMarketEvidence((await evidenceRes.json()) as MarketEvidenceRecord[]);
  }, [projectId]);

  const loadRunDetail = useCallback(async (runId: string) => {
    const detailRes = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${runId}`, {
      cache: "no-store",
    });
    if (!detailRes.ok) throw new Error("Unable to load selected orchestration run.");
    return (await detailRes.json()) as OrchestrationRun;
  }, [projectId]);

  const selectRun = useCallback(async (runId: string) => {
    const detail = await loadRunDetail(runId);
    setSelectedRun(detail);
    await loadMarketEvidence(runId);
  }, [loadMarketEvidence, loadRunDetail]);

  const loadRuns = useCallback(async (preferredRunId?: string) => {
    setError(null);
    const [projectRes, runsRes] = await Promise.all([
      fetch(`${apiBase}/projects/${projectId}`, { cache: "no-store" }),
      fetch(`${apiBase}/projects/${projectId}/orchestrations`, { cache: "no-store" }),
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
    }
  }, [projectId, selectRun]);

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
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/orchestrations/pf-review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ options: { requested_by: "web" } }),
      });
      if (!res.ok) throw new Error("Unable to start PF review run.");
      const run = (await res.json()) as OrchestrationRun;
      setSelectedRun(run);
      await loadMarketEvidence(run.run_id);
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
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${selectedRun.run_id}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          approved_by: approvedBy.trim() || "credit_officer",
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
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/market-evidence/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${selectedRun.run_id}/handoffs/${handoffId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${selectedRun.run_id}/risk-register/${riskId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
    && unresolvedHandoffCount === 0
    && unresolvedRiskCount === 0
  );
  const progressItems = useMemo(
    () => Object.values(selectedRun?.agentProgress ?? {}),
    [selectedRun],
  );
  const completedProgressCount = progressItems.filter((item) => item.status === "completed").length;
  const handoffCount = selectedRun?.handoffs?.length ?? 0;

  const qualityTone = useMemo(() => {
    if (!selectedRun?.artifacts.qualityCheck) return "text-slate-500";
    return selectedRun.artifacts.qualityCheck.pass ? "text-emerald-600" : "text-amber-600";
  }, [selectedRun]);

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-500">Project #{projectId}</p>
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

      {error ? (
        <div className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm font-semibold text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
          <AlertCircle className="h-4 w-4" />
          {error}
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
            <Kpi label="Current Status" value={selectedRun?.status ?? "-"} />
            <Kpi label="Agents" value={selectedRun ? `${completedProgressCount}/${progressItems.length}` : "-"} />
            <Kpi label="Handoffs" value={selectedRun ? `${handoffCount}` : "-"} />
            <Kpi label="Open Reviews" value={selectedRun ? `${unresolvedHandoffCount}` : "-"} tone={unresolvedHandoffCount > 0 ? "text-amber-600" : "text-emerald-600"} />
            <Kpi label="Quality Check" value={selectedRun?.artifacts.qualityCheck?.pass ? "Pass" : selectedRun ? "Review" : "-"} tone={qualityTone} />
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
                    canApprove={canApprove}
                    unresolvedHandoffCount={unresolvedHandoffCount}
                    unresolvedRiskCount={unresolvedRiskCount}
                    status={selectedRun.status}
                    approvedBy={approvedBy}
                    approvalComment={approvalComment}
                    approving={approving}
                    onApprovedByChange={setApprovedBy}
                    onApprovalCommentChange={setApprovalComment}
                    onApprove={() => void approveRun()}
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
                        ["PF Status", selectedRun.artifacts.developerFeasibility?.pfReadiness.status ?? "-"],
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
            <span>Status</span>
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

function ApprovalPanel({
  approval,
  canApprove,
  unresolvedHandoffCount,
  unresolvedRiskCount,
  status,
  approvedBy,
  approvalComment,
  approving,
  onApprovedByChange,
  onApprovalCommentChange,
  onApprove,
}: {
  approval: RunSummary["approval"];
  canApprove: boolean;
  unresolvedHandoffCount: number;
  unresolvedRiskCount: number;
  status: string;
  approvedBy: string;
  approvalComment: string;
  approving: boolean;
  onApprovedByChange: (value: string) => void;
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
          {unresolvedHandoffCount > 0
            ? `${unresolvedHandoffCount} unresolved handoff review(s) must be cleared before final approval.`
            : unresolvedRiskCount > 0
              ? `${unresolvedRiskCount} unresolved risk register review(s) must be approved, waived, or resolved before final approval.`
            : `Current status is \`${status}\`. Only completed runs can be approved.`}
        </p>
      ) : (
        <div className="mt-4 grid gap-3 lg:grid-cols-[220px_1fr_auto]">
          <label className="block">
            <span className="text-xs font-semibold text-slate-500">Approver</span>
            <input
              value={approvedBy}
              onChange={(event) => onApprovedByChange(event.target.value)}
              className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
            />
          </label>
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
  const tone = status === "completed" || status === "approved" || status === "pass" || status === "auto_validated"
    ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300"
    : status === "failed" || status === "rejected"
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
            <h3 className="font-bold text-slate-950 dark:text-white">Financial Sensitivity & Stress Scenarios</h3>
            <p className="text-sm text-slate-500">Real-time waterfall repayment, unsold LTV stress, and tranche-level recovery estimation.</p>
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
            Waterfall Allocation
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
            Unsold LTV Matrix
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
            Tranche Recovery Rates
          </button>
        </div>
      </div>

      <div className="mt-4">
        {activeTab === "waterfall" && (
          <div className="space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between bg-slate-50 p-3 rounded-lg dark:bg-slate-800/40">
              <span className="text-sm font-bold text-slate-800 dark:text-slate-200">Select Simulation Scenario:</span>
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
                  <Mini label="Available Cash" value={`${(selectedScenario.cashAvailable / 100_000_000).toLocaleString("ko-KR")} eok`} />
                  <Mini label="Total Claim" value={`${(selectedScenario.totalClaimAmount / 100_000_000).toLocaleString("ko-KR")} eok`} />
                  <Mini label="Shortfall" value={`${(selectedScenario.totalShortfall / 100_000_000).toLocaleString("ko-KR")} eok`} />
                  <Mini label="Remaining Cash" value={`${(selectedScenario.remainingCash / 100_000_000).toLocaleString("ko-KR")} eok`} />
                </div>

                <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
                  <div className="grid grid-cols-[80px_1fr_1.2fr_1.2fr_1.2fr_100px] bg-slate-50 px-4 py-2 text-xs font-bold uppercase text-slate-500 dark:bg-slate-800/70">
                    <span>Rank</span>
                    <span>Tranche</span>
                    <span className="text-right">Principal Claim</span>
                    <span className="text-right">Total Claim</span>
                    <span className="text-right">Payment Received</span>
                    <span className="text-right">Status</span>
                  </div>
                  <div className="divide-y divide-slate-200 dark:divide-slate-800">
                    {selectedScenario.rows.map((row) => (
                      <div key={row.tranche} className="grid grid-cols-[80px_1fr_1.2fr_1.2fr_1.2fr_100px] items-center gap-3 px-4 py-3 text-sm">
                        <span className="font-bold text-slate-500">{row.rank}</span>
                        <span className="font-bold text-slate-900 dark:text-white">{row.tranche}</span>
                        <span className="text-right text-slate-700 dark:text-slate-300">{(row.principalClaim / 100_000_000).toLocaleString("ko-KR")} eok</span>
                        <span className="text-right font-semibold text-slate-900 dark:text-white">{(row.totalClaim / 100_000_000).toLocaleString("ko-KR")} eok</span>
                        <span className="text-right font-black text-indigo-600 dark:text-indigo-400">{(row.paymentReceived / 100_000_000).toLocaleString("ko-KR")} eok</span>
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
              <h4 className="text-sm font-bold text-indigo-950 dark:text-indigo-300">Unsold LTV Risk Highlights:</h4>
              <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                Base Case LTV: <span className="font-bold text-indigo-700 dark:text-indigo-400">{(unsoldLtvMatrix.summary.baseCaseLtv * 100).toFixed(1)}%</span> |
                Stress Case LTV: <span className="font-bold text-rose-600 dark:text-rose-400">{(unsoldLtvMatrix.summary.stressCaseLtv * 100).toFixed(1)}%</span> |
                LTV threshold breaches detected in <span className="font-bold text-rose-600 dark:text-rose-400">{unsoldLtvMatrix.summary.breachCount}</span> scenarios.
              </p>
            </div>

            <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
              <table className="w-full min-w-[600px] border-collapse text-sm text-left">
                <thead>
                  <tr className="bg-slate-50 border-b border-slate-200 dark:bg-slate-800/70 dark:border-slate-800 text-xs font-bold uppercase text-slate-500">
                    <th className="p-3 border-r border-slate-200 dark:border-slate-800">Sale Rate / Discount Rate</th>
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
              <h4 className="text-sm font-bold text-emerald-950 dark:text-emerald-300">Tranche Final Recovery Analysis:</h4>
              <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                Lenders have highest priority. Under high sale rate, recovery is 100%. Under extreme discount or low sale rate, mezzanine or junior tranches suffer recovery haircuts first.
              </p>
            </div>

            <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
              <div className="grid grid-cols-[1.2fr_1fr_1.5fr_2fr] bg-slate-50 px-4 py-2 text-xs font-bold uppercase text-slate-500 dark:bg-slate-800/70">
                <span>Scenario ID</span>
                <span>Drivers</span>
                <span className="text-right">Recovery Asset Pool</span>
                <span className="text-right">Tranche Recovery Ratios</span>
              </div>
              <div className="divide-y divide-slate-200 dark:divide-slate-800">
                {trancheRecoveryMatrix.map((row) => (
                  <div key={row.scenarioId} className="grid grid-cols-[1.2fr_1fr_1.5fr_2fr] items-center gap-3 px-4 py-3 text-sm">
                    <span className="font-bold text-slate-900 dark:text-white">{row.scenarioId}</span>
                    <span className="text-slate-600 dark:text-slate-400">
                      분양 {(row.saleRate * 100).toFixed(0)}% / 할인 {(row.discountRate * 100).toFixed(0)}%
                    </span>
                    <span className="text-right font-semibold text-slate-900 dark:text-white">
                      {(row.recoveryPool / 100_000_000).toLocaleString("ko-KR")} eok
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
