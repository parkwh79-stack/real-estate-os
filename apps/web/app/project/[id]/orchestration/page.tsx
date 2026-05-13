"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useParams } from "next/navigation";
import {
  AlertCircle,
  CheckCircle2,
  FileText,
  GitBranch,
  Loader2,
  Play,
  RefreshCw,
  ShieldCheck,
  UserCheck,
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
    marketabilityAnalysis?: {
      applicable: boolean;
      score?: number;
      grade: string;
      summary: string;
      recommendations: string[];
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
  return `${(value / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}억`;
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

export default function ProjectOrchestrationPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [project, setProject] = useState<ProjectResponse>({});
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState<OrchestrationRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [approving, setApproving] = useState(false);
  const [approvedBy, setApprovedBy] = useState("credit_officer");
  const [approvalComment, setApprovalComment] = useState("");
  const [error, setError] = useState<string | null>(null);

  const loadRunDetail = useCallback(async (runId: string) => {
    const detailRes = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${runId}`, {
      cache: "no-store",
    });
    if (!detailRes.ok) throw new Error("선택한 run 상세 정보를 불러오지 못했습니다.");
    return (await detailRes.json()) as OrchestrationRun;
  }, [projectId]);

  const loadRuns = useCallback(async (preferredRunId?: string) => {
    setError(null);
    const [projectRes, runsRes] = await Promise.all([
      fetch(`${apiBase}/projects/${projectId}`, { cache: "no-store" }),
      fetch(`${apiBase}/projects/${projectId}/orchestrations`, { cache: "no-store" }),
    ]);

    if (!projectRes.ok) throw new Error("프로젝트 정보를 불러오지 못했습니다.");
    if (!runsRes.ok) throw new Error("오케스트레이션 run 목록을 불러오지 못했습니다.");

    const projectData = (await projectRes.json()) as ProjectResponse;
    const runData = (await runsRes.json()) as RunSummary[];
    setProject(projectData);
    setRuns(runData);

    const targetRun = preferredRunId
      ? runData.find((run) => run.run_id === preferredRunId)
      : [...runData].sort((a, b) => b.started_at.localeCompare(a.started_at))[0];

    setSelectedRun(targetRun ? await loadRunDetail(targetRun.run_id) : null);
  }, [loadRunDetail, projectId]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    loadRuns()
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : "오케스트레이션 정보를 불러오지 못했습니다.");
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
      if (!res.ok) throw new Error("오케스트레이션 실행에 실패했습니다.");
      const run = (await res.json()) as OrchestrationRun;
      setSelectedRun(run);
      await loadRuns(run.run_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "오케스트레이션 실행에 실패했습니다.");
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
        throw new Error(body.detail ?? "승인 처리에 실패했습니다.");
      }
      const approvedRun = (await res.json()) as OrchestrationRun;
      setSelectedRun(approvedRun);
      setRuns((prev) => prev.map((run) => (
        run.run_id === approvedRun.run_id
          ? { ...run, status: approvedRun.status, approval: approvedRun.approval }
          : run
      )));
    } catch (err) {
      setError(err instanceof Error ? err.message : "승인 처리에 실패했습니다.");
    } finally {
      setApproving(false);
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
          <p className="text-sm font-semibold text-slate-500">프로젝트 #{projectId}</p>
          <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            AI 오케스트레이션
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            {project.project?.name ?? "프로젝트"}의 agent 실행, 산출물, 품질 점검, 승인 상태를 확인합니다.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void loadRuns(selectedRun?.run_id)}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          >
            <RefreshCw className="h-4 w-4" />
            새로고침
          </button>
          <button
            type="button"
            onClick={() => void startRun()}
            disabled={starting}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-white dark:text-slate-950"
          >
            {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            PF 리뷰 실행
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
            <Kpi label="Run 수" value={`${runs.length}건`} />
            <Kpi label="현재 상태" value={selectedRun?.status ?? "-"} />
            <Kpi label="단계" value={selectedRun ? `${selectedRun.steps.length}개` : "-"} />
            <Kpi label="품질 점검" value={selectedRun?.artifacts.qualityCheck?.pass ? "Pass" : selectedRun ? "Review" : "-"} tone={qualityTone} />
          </section>

          <section className="grid gap-4 xl:grid-cols-[360px_1fr]">
            <div className="space-y-3">
              <h3 className="text-lg font-bold text-slate-950 dark:text-white">실행 이력</h3>
              {runs.length === 0 ? (
                <div className="rounded-lg border border-dashed border-slate-300 p-6 text-sm text-slate-500 dark:border-slate-700">
                  아직 실행된 run이 없습니다.
                </div>
              ) : (
                runs.map((run) => (
                  <button
                    key={run.run_id}
                    type="button"
                    onClick={async () => {
                      setSelectedRun(await loadRunDetail(run.run_id));
                    }}
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
                      </div>
                      <StatusBadge status={selectedRun.status} />
                    </div>
                    <div className="mt-4 grid gap-3 md:grid-cols-3">
                      <Mini label="시작" value={fmtDate(selectedRun.started_at)} />
                      <Mini label="완료" value={fmtDate(selectedRun.completed_at)} />
                      <Mini label="승인자" value={selectedRun.approval ? selectedRun.approval.approved_by : "-"} />
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
                      title="사업수지"
                      rows={[
                        ["개발이익", fmtEok(selectedRun.artifacts.developerFeasibility?.developerReturns.developmentProfit)],
                        ["매출이익률", fmtPercent(selectedRun.artifacts.developerFeasibility?.developerReturns.profitMargin)],
                        ["PF 상태", selectedRun.artifacts.developerFeasibility?.pfReadiness.status ?? "-"],
                      ]}
                    />
                    <ArtifactPanel
                      icon={<ShieldCheck className="h-5 w-5 text-emerald-600" />}
                      title="분양성/법률"
                      rows={[
                        ["분양성", selectedRun.artifacts.marketabilityAnalysis?.applicable ? `${selectedRun.artifacts.marketabilityAnalysis.grade} / ${selectedRun.artifacts.marketabilityAnalysis.score}` : "비적용"],
                        ["법률 검토", selectedRun.artifacts.legalPermitReview?.status ?? "-"],
                        ["Law MCP", selectedRun.artifacts.legalPermitReview?.mcp.connected ? selectedRun.artifacts.legalPermitReview.mcp.source : "미연결"],
                      ]}
                    />
                    <ArtifactPanel
                      icon={<FileText className="h-5 w-5 text-sky-600" />}
                      title="보고서"
                      rows={[
                        ["보고서", selectedRun.artifacts.reportRecord?.title ?? "-"],
                        ["섹션 수", selectedRun.artifacts.reportRecord ? `${selectedRun.artifacts.reportRecord.metadata.section_count}개` : "-"],
                        ["완성도", selectedRun.artifacts.reportRecord?.metadata.complete ? "Complete" : "-"],
                      ]}
                    />
                  </section>

                  <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                    <h3 className="text-lg font-bold text-slate-950 dark:text-white">실행 단계</h3>
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
                  왼쪽에서 run을 선택하거나 새 PF 리뷰를 실행하세요.
                </div>
              )}
            </div>
          </section>
        </>
      )}
    </div>
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
          <h3 className="font-bold text-emerald-900 dark:text-emerald-200">승인 완료</h3>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <Mini label="승인자" value={approval.approved_by} />
          <Mini label="승인 시각" value={fmtDate(approval.approved_at)} />
          <Mini label="의견" value={approval.comment || "-"} />
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2">
        <UserCheck className="h-5 w-5 text-slate-700 dark:text-slate-200" />
        <h3 className="font-bold text-slate-950 dark:text-white">심사 승인</h3>
      </div>
      {!canApprove ? (
        <p className="mt-3 text-sm text-slate-500">
          현재 상태가 `{status}`이므로 완료된 run만 승인할 수 있습니다.
        </p>
      ) : (
        <div className="mt-4 grid gap-3 lg:grid-cols-[220px_1fr_auto]">
          <label className="block">
            <span className="text-xs font-semibold text-slate-500">승인자</span>
            <input
              value={approvedBy}
              onChange={(event) => onApprovedByChange(event.target.value)}
              className="mt-1 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-slate-500 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
            />
          </label>
          <label className="block">
            <span className="text-xs font-semibold text-slate-500">승인 의견</span>
            <input
              value={approvalComment}
              onChange={(event) => onApprovalCommentChange(event.target.value)}
              placeholder="예: 품질 점검 및 심사 보고서 확인"
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
            승인
          </button>
        </div>
      )}
    </section>
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
  const tone = status === "completed" || status === "approved"
    ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300"
    : status === "failed"
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
