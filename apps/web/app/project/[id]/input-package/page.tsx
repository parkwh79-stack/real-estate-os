"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  Database,
  FileArchive,
  FileText,
  GitBranch,
  Loader2,
  PackageCheck,
  RefreshCw,
} from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import { SmartIntake } from "../../../../components/intake/smart-intake";
import { FeasibilityExtractReview } from "../../../../components/intake/feasibility-extract-review";
import {
  displayText,
  fetchWorkspaceContext,
  type WorkspacePageContext,
} from "../../../../lib/workspace-context";
import {
  getStoredWorkflowTrack,
  getTrackWorkflowDefinition,
  isWorkflowTrack,
  TRACK_LABELS,
  type WorkflowTrack,
} from "../../../../lib/workflow-track";

type SourceDocument = {
  originalFileName?: string;
  fileName?: string;
  documentType?: string;
  contentType?: string;
  fileSizeBytes?: number;
  uploadedAt?: string;
  validationStatus?: string;
  archiveIntegrityStatus?: string;
};

type IntakeReadinessPlan = {
  track?: WorkflowTrack;
  trackLabel?: string;
  summary?: {
    readyStageCount?: number;
    needsSupplementCount?: number;
    blockedStageCount?: number;
  };
  stages?: Array<{
    key?: string;
    title?: string;
    status?: string;
    missingMaterials?: string[];
    agents?: string[];
  }>;
  nextRecommendedAction?: string;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function formatBytes(value?: number) {
  if (!value) return "-";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ko-KR");
}

export default function InputPackagePage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const projectId = String(params.id);
  const activeTrack = useMemo<WorkflowTrack>(() => {
    const fromUrl = searchParams.get("track");
    if (isWorkflowTrack(fromUrl)) return fromUrl;
    return getStoredWorkflowTrack() || "development";
  }, [searchParams]);
  const workflow = useMemo(() => getTrackWorkflowDefinition(activeTrack), [activeTrack]);

  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [intakePlan, setIntakePlan] = useState<IntakeReadinessPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setContext(await fetchWorkspaceContext(projectId, "input_package", activeTrack));
    } catch (err) {
      setError(err instanceof Error ? err.message : "자료 입력 패키지 저장 상태를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [activeTrack, projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const finalizeIntake = useCallback(async () => {
    setRunning("finalize");
    setError(null);
    setMessage(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/intake/finalize`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ track: activeTrack, run: false }),
      });
      if (!res.ok) throw new Error(`자료 보강 계획 생성 실패 (${res.status})`);
      const body = (await res.json()) as { intakeReadinessPlan?: IntakeReadinessPlan };
      setIntakePlan(body.intakeReadinessPlan ?? null);
      setMessage(body.intakeReadinessPlan?.nextRecommendedAction ?? "자료 접수와 보강 계획을 업데이트했습니다.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "자료 보강 계획 생성에 실패했습니다.");
    } finally {
      setRunning(null);
    }
  }, [activeTrack, projectId, refresh]);

  const runPod = async (podKey: string, label: string) => {
    setRunning(podKey);
    setError(null);
    setMessage(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/chief-orchestration/run-pod`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ podKey }),
      });
      if (!res.ok) throw new Error(`${label} 실행 실패 (${res.status})`);
      setMessage(`${label} 실행 결과가 프로젝트 artifact로 저장되었습니다.`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : `${label} 실행에 실패했습니다.`);
    } finally {
      setRunning(null);
    }
  };

  const runPnuResolve = async () => {
    setRunning("pnu");
    setError(null);
    setMessage(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/land/parcels/resolve`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ persistEvidence: true, limit: 500 }),
      });
      if (!res.ok) throw new Error(`PNU 보강 실패 (${res.status})`);
      const body = await res.json();
      setMessage(`PNU 보강 완료: ${body?.summary?.resolvedCount ?? body?.resolvedCount ?? 0}건 해석`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "PNU 보강에 실패했습니다.");
    } finally {
      setRunning(null);
    }
  };

  const pageData = asRecord(context?.pageData);
  const documents = asArray<SourceDocument>(pageData.sourceDocuments);
  const sourceSummary = asRecord(pageData.sourceDocumentSummary);
  const landLedgerSummary = asRecord(pageData.landLedgerSummary);
  const landEvidenceSummary = asRecord(pageData.landEvidenceSummary);
  const recommendedActions = asArray<string>(pageData.recommendedActions);
  const missingInputs = asArray<Record<string, unknown>>(context?.missingInputRegister);
  const projectName = displayText(context?.projectMemory?.projectName, `프로젝트 ${projectId}`);
  const docCount = Number(context?.projectMemory?.sourceDocumentCount ?? sourceSummary.count ?? documents.length ?? 0);
  const evidenceCount = Number(context?.projectMemory?.landEvidenceCount ?? landEvidenceSummary.count ?? 0);

  return (
    <div className="space-y-6 pb-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <Link
            href={`/project/${projectId}`}
            className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
            aria-label="프로젝트로 돌아가기"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <p className="text-sm font-semibold text-slate-500">{projectName}</p>
            <h1 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
              <PackageCheck className="h-7 w-7 text-indigo-600" />
              자료 입력 패키지
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              업로드 원본, 자동분류 결과, 토지/PNU/공시지가 증빙, Pod 실행 결과를 같은 프로젝트 저장소에서 추적합니다.
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading || running !== null}
          className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-bold text-slate-700 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          새로고침
        </button>
      </header>

      {loading ? (
        <div className="flex min-h-[220px] items-center justify-center rounded-lg border border-slate-200 bg-white text-sm font-bold text-slate-500 dark:border-slate-800 dark:bg-slate-900">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" /> 자료 입력 패키지를 불러오는 중입니다.
        </div>
      ) : (
        <>
          {error ? <Notice tone="bad">{error}</Notice> : null}
          {message ? <Notice tone="good">{message}</Notice> : null}

          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
            <Kpi label="저장 문서" value={`${docCount.toLocaleString("ko-KR")}건`} icon={<FileArchive />} />
            <Kpi label="토지조서 필지" value={`${Number(landLedgerSummary.parcelCount ?? 0).toLocaleString("ko-KR")}필지`} icon={<FileText />} />
            <Kpi label="PNU 확보" value={`${Number(landLedgerSummary.pnuCount ?? 0)}/${Number(landLedgerSummary.parcelCount ?? 0)}`} icon={<Database />} />
            <Kpi label="토지 증빙" value={`${evidenceCount.toLocaleString("ko-KR")}건`} icon={<Database />} />
            <Kpi label="보완 필요" value={`${missingInputs.length.toLocaleString("ko-KR")}건`} icon={<GitBranch />} />
          </section>

          <section className="rounded-lg border border-indigo-200 bg-white p-5 shadow-sm dark:border-indigo-900/60 dark:bg-slate-900">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <p className="text-xs font-black uppercase tracking-wide text-indigo-600 dark:text-indigo-300">
                  Selected Track Workflow
                </p>
                <h2 className="mt-1 text-xl font-black text-slate-950 dark:text-white">
                  {TRACK_LABELS[activeTrack]} 자료 보강 흐름
                </h2>
                <p className="mt-1 text-sm leading-6 text-slate-600 dark:text-slate-300">{workflow.intent}</p>
              </div>
              <div className="rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm font-bold text-indigo-950 dark:border-indigo-900 dark:bg-indigo-950/30 dark:text-indigo-100">
                판단 질문: {workflow.decisionQuestion}
              </div>
            </div>
            <div className="mt-4 grid gap-3 xl:grid-cols-5">
              {workflow.steps.map((step, index) => (
                <div key={step.key} className="rounded-lg border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950">
                  <span className="flex h-7 w-7 items-center justify-center rounded-full bg-indigo-600 text-xs font-black text-white">
                    {index + 1}
                  </span>
                  <p className="mt-3 text-sm font-black text-slate-950 dark:text-white">{step.title}</p>
                  <p className="mt-2 text-xs leading-5 text-slate-500">{step.purpose}</p>
                </div>
              ))}
            </div>
          </section>

          <SmartIntake projectId={projectId} onProcessed={finalizeIntake} />
          <FeasibilityExtractReview projectId={projectId} onApplied={refresh} />

          <section className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
            <Panel title="다음 보강 작업">
              <div className="flex flex-wrap gap-2">
                <ActionButton label="자료 보강 계획 생성" busy={running === "finalize"} disabled={running !== null} onClick={() => void finalizeIntake()} />
                <ActionButton label="PNU 일괄 해석" busy={running === "pnu"} disabled={running !== null || !landLedgerSummary.present} onClick={() => void runPnuResolve()} />
                <ActionButton label="Land-Legal Pod" busy={running === "land_legal"} disabled={running !== null} onClick={() => void runPod("land_legal", "Land-Legal Pod")} />
                <ActionButton label="Market-Product Pod" busy={running === "market_product"} disabled={running !== null} onClick={() => void runPod("market_product", "Market-Product Pod")} />
                <ActionButton label="Feasibility-CF Pod" busy={running === "feasibility_cashflow"} disabled={running !== null} onClick={() => void runPod("feasibility_cashflow", "Feasibility-Cashflow Pod")} />
              </div>
              <div className="mt-4 space-y-2 text-sm leading-6 text-slate-600 dark:text-slate-300">
                {intakePlan?.nextRecommendedAction || recommendedActions.length ? (
                  <>
                    {intakePlan?.nextRecommendedAction ? <p className="font-bold text-slate-950 dark:text-white">{intakePlan.nextRecommendedAction}</p> : null}
                    {recommendedActions.map((item) => (
                      <p key={item}>- {item}</p>
                    ))}
                  </>
                ) : (
                  <p>
                    자료를 업로드하면 오케스트레이터가 어떤 Pod를 실행해야 하는지와 부족자료를 표시합니다.
                  </p>
                )}
              </div>
            </Panel>

            <Panel title="보완 필요 항목">
              {missingInputs.length ? (
                <div className="space-y-2">
                  {missingInputs.slice(0, 8).map((item, index) => (
                    <div key={index} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                      <p className="font-black">{displayText(item.title ?? item.label ?? item.code, `보완 항목 ${index + 1}`)}</p>
                      <p className="mt-1 text-xs leading-5">
                        {displayText(item.reason ?? item.description ?? item.message, "다음 단계 실행 전 보완이 필요합니다.")}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm font-bold text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100">
                  현재 공통 저장 기준으로 즉시 표시되는 보완 항목은 없습니다.
                </div>
              )}
            </Panel>
          </section>

          <Panel title="저장된 원본 문서">
            {documents.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-left text-sm">
                  <thead className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-800">
                    <tr>
                      <th className="px-3 py-2">문서유형</th>
                      <th className="px-3 py-2">파일명</th>
                      <th className="px-3 py-2">크기</th>
                      <th className="px-3 py-2">검증</th>
                      <th className="px-3 py-2">무결성</th>
                      <th className="px-3 py-2">업로드</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {documents.map((doc, index) => (
                      <tr key={`${doc.originalFileName ?? doc.fileName}-${index}`}>
                        <td className="px-3 py-3 font-bold text-slate-950 dark:text-white">
                          {displayText(doc.documentType, "source_document")}
                        </td>
                        <td className="px-3 py-3 text-slate-600 dark:text-slate-300">
                          {displayText(doc.originalFileName ?? doc.fileName, "파일명 미확인")}
                        </td>
                        <td className="px-3 py-3 text-slate-500">{formatBytes(doc.fileSizeBytes)}</td>
                        <td className="px-3 py-3 text-slate-500">{displayText(doc.validationStatus, "-")}</td>
                        <td className="px-3 py-3 text-slate-500">{displayText(doc.archiveIntegrityStatus, "-")}</td>
                        <td className="px-3 py-3 text-slate-500">{formatDate(doc.uploadedAt)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="rounded-lg bg-slate-50 p-5 text-sm text-slate-500 dark:bg-slate-800/70">
                아직 저장된 원본 문서가 없습니다. 위의 스마트 업로드에 파일을 올리세요.
              </div>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}

function Kpi({ label, value, icon }: { label: string; value: string; icon: ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2 text-xs font-bold text-slate-500">{icon}{label}</div>
      <p className="mt-2 text-xl font-black text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-4 flex items-center gap-2">
        <CheckCircle2 className="h-5 w-5 text-slate-700 dark:text-slate-200" />
        <h2 className="text-lg font-black text-slate-950 dark:text-white">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function Notice({ tone, children }: { tone: "good" | "bad"; children: ReactNode }) {
  const klass = tone === "good"
    ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100"
    : "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200";
  return <div className={`rounded-lg border p-4 text-sm font-bold ${klass}`}>{children}</div>;
}

function ActionButton({
  label,
  busy,
  disabled,
  onClick,
}: {
  label: string;
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-slate-950"
    >
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
      {label}
    </button>
  );
}
