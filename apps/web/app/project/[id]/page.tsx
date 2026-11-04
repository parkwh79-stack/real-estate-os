"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  GitBranch,
  Loader2,
  Upload,
} from "lucide-react";
import { displayText, fetchWorkspaceContext, type WorkspacePageContext } from "../../../lib/workspace-context";
import { projectWorkspaceHref } from "../../../lib/project-navigation";
import { isWorkflowTrack } from "../../../lib/workflow-track";

function asArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function formatCount(value: number | undefined, unit = "건") {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toLocaleString("ko-KR")}${unit}` : "미확인";
}

export default function ProjectDetailPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const trackParam = useSearchParams().get("track");
  const selectedTrack = isWorkflowTrack(trackParam) ? trackParam : undefined;
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestVersion = useRef(0);

  const load = useCallback(async () => {
    const version = ++requestVersion.current;
    setLoading(true);
    setError(null);
    try {
      const result = await fetchWorkspaceContext(projectId, "overview", selectedTrack);
      if (version === requestVersion.current) setContext(result);
    } catch {
      if (version === requestVersion.current) {
        setError("프로젝트 현황을 불러오지 못했습니다.");
      }
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }, [projectId, selectedTrack]);

  useEffect(() => {
    void load();
    return () => { requestVersion.current += 1; };
  }, [load]);

  const projectContext = context?.projectId === projectId ? context : null;
  const pageData = projectContext?.pageData;
  const sourceDocumentCount = pageData?.sourceDocumentSummary?.count ?? projectContext?.projectMemory?.sourceDocumentCount;
  const parcelCount = pageData?.landLedgerSummary?.parcelCount;
  const pnuCount = pageData?.landLedgerSummary?.pnuCount;
  const landEvidence = projectContext?.unavailableSections?.includes("landEvidence") ? undefined : pageData?.landEvidenceSummary;
  const missingInputs = projectContext?.unavailableSections?.includes("missingInputs") ? undefined : projectContext?.missingInputRegister;
  const pods = asArray<Record<string, unknown>>(projectContext?.agentPods);
  const podRuns = asArray<Record<string, unknown>>(projectContext?.podRuns);
  const runByPod = new Map(podRuns.map((run) => [String(run.podKey ?? run.pod_key), run]));
  const decision = projectContext?.decisionGate?.decision || projectContext?.decisionGate?.status;
  const track = selectedTrack ?? projectContext?.workflowTrack;
  const intakeHref = projectWorkspaceHref(projectId, "input-package", track);

  return (
    <div className="space-y-5 pb-8">
      <header className="rounded-re-panel border border-re-border bg-re-surface p-4 shadow-re-panel sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-re-text">프로젝트 개요</h1>
            <p className="mt-1 text-sm text-re-secondary">자료 현황과 확인할 항목을 살펴보세요.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href={intakeHref} className="inline-flex min-h-11 items-center gap-2 rounded-re-control bg-re-action px-4 text-sm font-bold text-re-on-action transition-colors duration-150 hover:bg-re-action-hover">
              <Upload className="h-4 w-4" aria-hidden="true" />
              자료 입력
            </Link>
            <Link href={projectWorkspaceHref(projectId, "chief-orchestration", track)} className="inline-flex min-h-11 items-center gap-2 rounded-re-control border border-re-border px-4 text-sm font-semibold text-re-text transition-colors duration-150 hover:bg-re-subtle">
              <GitBranch className="h-4 w-4" aria-hidden="true" />
              AI 딜룸
            </Link>
          </div>
        </div>
      </header>

      {loading ? (
        <div className="flex min-h-[240px] items-center justify-center rounded-lg border border-slate-200 bg-white text-sm font-bold text-slate-500 dark:border-slate-800 dark:bg-slate-900">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          프로젝트 기억을 불러오는 중입니다.
        </div>
      ) : error ? (
        <div role="alert" className="rounded-re-panel border border-re-border bg-re-surface p-4 text-sm text-re-danger">
          <p>{error}</p>
          <button type="button" onClick={() => void load()} className="mt-2 min-h-11 rounded-re-control border border-re-border px-3 font-semibold text-re-text hover:bg-re-subtle">다시 불러오기</button>
        </div>
      ) : (
        <>
          <div className="grid gap-4 lg:grid-cols-[0.85fr_1.15fr]">
            <Panel title="자료와 심사 현황">
              <InfoRow label="저장 자료" value={formatCount(sourceDocumentCount)} />
              <InfoRow label="토지조서 필지" value={formatCount(parcelCount, "필지")} />
              <InfoRow label="PNU 확보" value={parcelCount === 0 ? "산정 전" : parcelCount != null && pnuCount != null ? `${pnuCount} / ${parcelCount}` : "미확인"} />
              <InfoRow label="토지 증빙" value={formatCount(landEvidence?.count)} />
              <InfoRow label="심사 판단" value={displayText(decision, "미확인")} />
            </Panel>

            <Panel title="다음 권장 작업">
              {missingInputs?.length ? (
                <div className="space-y-2">
                  {missingInputs.slice(0, 5).map((item, index) => (
                    <div key={index} className="rounded-re-control border border-re-border bg-re-subtle p-3 text-sm text-re-text">
                      <p className="font-black">{displayText(item.title ?? item.label ?? item.code, `보완 항목 ${index + 1}`)}</p>
                      <p className="mt-1 text-xs leading-5">{displayText(item.reason ?? item.description ?? item.message, "에이전트가 다음 단계 실행 전 보완이 필요하다고 판단했습니다.")}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-re-secondary">
                  {Array.isArray(missingInputs) ? "현재 조회된 보완 항목은 없습니다." : "보완 항목을 확인하지 못했습니다. 자료 입력에서 문서와 검토 상태를 확인하세요."}
                </p>
              )}
            </Panel>
          </div>

          {pods.length ? (
          <details className="rounded-re-panel border border-re-border bg-re-surface p-4">
            <summary className="cursor-pointer rounded-re-control font-semibold text-re-text hover:text-re-action">분석 실행 상세</summary>
            <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {pods.map((pod) => {
                const podKey = String(pod.podKey ?? pod.pod_key ?? pod.key ?? "");
                const run = runByPod.get(podKey);
                return (
                  <div key={podKey || String(pod.label)} className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
                    <div className="flex items-center justify-between gap-2">
                      <p className="font-black text-slate-950 dark:text-white">{displayText(pod.label ?? pod.name ?? podKey, podKey)}</p>
                      <PodStatus status={String(run?.status ?? pod.status ?? "not_started")} />
                    </div>
                    <p className="mt-2 text-xs leading-5 text-slate-500">{displayText(pod.mission ?? pod.description, "프로젝트 원장을 읽고 전용 분석 artifact를 생성합니다.")}</p>
                  </div>
                );
              })}
            </div>
          </details>
          ) : null}
        </>
      )}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-re-panel border border-re-border bg-re-surface p-4 shadow-re-panel sm:p-5">
      <h2 className="mb-3 text-lg font-bold text-re-text">{title}</h2>
      {children}
    </section>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-slate-100 py-3 text-sm last:border-0 dark:border-slate-800">
      <span className="font-semibold text-slate-500">{label}</span>
      <span className="text-right font-black text-slate-950 dark:text-white">{value}</span>
    </div>
  );
}

function PodStatus({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const done = normalized.includes("completed");
  const blocked = normalized.includes("blocked") || normalized.includes("failed");
  const label = done ? "완료" : blocked ? "차단" : normalized === "not_started" ? "대기" : displayText(status);
  const klass = done
    ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200"
    : blocked
      ? "bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-200"
      : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200";
  return (
    <span className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-bold ${klass}`}>
      {done ? <CheckCircle2 className="h-3.5 w-3.5" /> : blocked ? <AlertTriangle className="h-3.5 w-3.5" /> : null}
      {label}
    </span>
  );
}
