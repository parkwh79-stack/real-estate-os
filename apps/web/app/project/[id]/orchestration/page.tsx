"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useParams } from "next/navigation";
import {
  AlertCircle,
  CheckCircle2,
  Database,
  FileText,
  GitBranch,
  Loader2,
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

interface OrchestrationRun extends RunSummary {
  steps: RunStep[];
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
  const [approvedBy, setApprovedBy] = useState("credit_officer");
  const [approvalComment, setApprovalComment] = useState("");
  const [evidenceReviewer, setEvidenceReviewer] = useState("market_analyst");
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

  const canApprove = selectedRun?.status === "completed" || selectedRun?.status === "completed_with_warnings";

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
          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Kpi label="Runs" value={`${runs.length}`} />
            <Kpi label="Current Status" value={selectedRun?.status ?? "-"} />
            <Kpi label="Steps" value={selectedRun ? `${selectedRun.steps.length}` : "-"} />
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
                    status={selectedRun.status}
                    approvedBy={approvedBy}
                    approvalComment={approvalComment}
                    approving={approving}
                    onApprovedByChange={setApprovedBy}
                    onApprovalCommentChange={setApprovalComment}
                    onApprove={() => void approveRun()}
                  />

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
          Current status is `{status}`. Only completed runs can be approved.
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
