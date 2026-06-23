"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, PlayCircle, XCircle } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { MetricCard } from "../ui/primitives";

const POLL_MS = 1500;

interface JobResult {
  status?: string;
  stepCount?: number | null;
  approvalReadiness?: string | null;
  creditDecision?: string | null;
  errorCount?: number | null;
}
interface Job {
  jobId: string;
  status: string;
  runId?: string | null;
  result?: JobResult | null;
  error?: string | null;
}

const CREDIT_LABEL: Record<string, string> = {
  approve: "승인 가능",
  conditional_approval: "조건부 승인",
  rework_required: "보완 후 재심",
  reject_recommended: "부결 권고",
};
const READINESS_LABEL: Record<string, string> = {
  ready_for_approval: "승인 준비",
  not_ready_for_approval: "승인 미준비",
};
const STATUS_LABEL: Record<string, string> = {
  queued: "대기 중",
  running: "실행 중",
  completed: "완료",
  failed: "실패",
};

/**
 * Non-blocking PF review: submits an async job and polls its status live,
 * so reviewers can launch the full 22-step run without blocking the page.
 */
export function AsyncRunPanel({
  projectId,
  onComplete,
}: {
  projectId: string;
  onComplete?: () => void;
}) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const startedAt = useRef<number | null>(null);
  const polling = useRef(false);
  const completedRef = useRef(false);

  const isTerminal = job?.status === "completed" || job?.status === "failed";

  const poll = useCallback(
    async (jobId: string) => {
      if (polling.current) return;
      polling.current = true;
      try {
        const res = await fetch(`${apiBase}/projects/${projectId}/jobs/${jobId}`, {
          cache: "no-store",
          headers: authHeaders("web"),
        });
        if (!res.ok) throw new Error(`상태 조회 실패 (${res.status})`);
        const next = (await res.json()) as Job;
        setJob(next);
        if ((next.status === "completed" || next.status === "failed") && !completedRef.current) {
          completedRef.current = true;
          onComplete?.();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "상태 조회에 실패했습니다.");
      } finally {
        polling.current = false;
      }
    },
    [projectId, onComplete],
  );

  // Poll while the job is non-terminal; tick the elapsed timer.
  useEffect(() => {
    if (!job?.jobId || isTerminal) return;
    const interval = window.setInterval(() => {
      if (startedAt.current) setElapsed(Math.round((Date.now() - startedAt.current) / 1000));
      void poll(job.jobId);
    }, POLL_MS);
    return () => window.clearInterval(interval);
  }, [job?.jobId, isTerminal, poll]);

  async function start() {
    setError(null);
    setElapsed(0);
    completedRef.current = false;
    startedAt.current = Date.now();
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/orchestrations/pf-review/async`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders("web") },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error(`실행 제출 실패 (${res.status})`);
      const body = (await res.json()) as { job: Job };
      setJob(body.job);
    } catch (err) {
      setError(err instanceof Error ? err.message : "백그라운드 실행 제출에 실패했습니다.");
    }
  }

  const running = Boolean(job) && !isTerminal;
  const result = job?.result;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="font-black text-slate-950 dark:text-white">백그라운드 통합심사</p>
          <p className="text-xs font-semibold text-slate-500">논블로킹 실행 — 제출 후 진행 상태를 실시간으로 추적합니다.</p>
        </div>
        <button
          type="button"
          onClick={() => void start()}
          disabled={running}
          className="inline-flex h-10 shrink-0 items-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-bold text-white disabled:opacity-40 dark:bg-white dark:text-slate-950"
        >
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}
          {running ? "실행 중..." : "백그라운드 실행"}
        </button>
      </div>

      {error ? <p className="mt-3 text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      {job ? (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={job.status} />
            {!isTerminal ? <span className="text-xs font-semibold text-slate-500">경과 {elapsed}s</span> : null}
            {job.runId ? <span className="text-xs font-mono text-slate-400">run {job.runId.slice(0, 8)}</span> : null}
          </div>

          {!isTerminal ? (
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
              <div className="h-full w-1/3 animate-pulse rounded-full bg-slate-900 dark:bg-white" />
            </div>
          ) : null}

          {job.status === "completed" && result ? (
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <MetricCard label="단계" value={`${result.stepCount ?? "—"}개`} />
              <MetricCard label="여신 의견" value={CREDIT_LABEL[result.creditDecision ?? ""] ?? result.creditDecision ?? "—"} />
              <MetricCard label="승인 준비" value={READINESS_LABEL[result.approvalReadiness ?? ""] ?? result.approvalReadiness ?? "—"} />
              <MetricCard label="오류" value={`${result.errorCount ?? 0}건`} />
            </div>
          ) : null}

          {job.status === "failed" ? (
            <p className="text-sm font-bold text-rose-600 dark:text-rose-300">{job.error ?? "실행 실패"}</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "completed"
      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200"
      : status === "failed"
        ? "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200"
        : "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200";
  const Icon = status === "completed" ? CheckCircle2 : status === "failed" ? XCircle : Loader2;
  const spin = status === "queued" || status === "running" ? "animate-spin" : "";
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-black ${tone}`}>
      <Icon className={`h-3.5 w-3.5 ${spin}`} />
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

