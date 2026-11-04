"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
import { useProjectName } from "../../../../components/project/project-name-editor";
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

type ParsedFact = {
  recordId?: string;
  key?: string;
  canonicalField?: string;
  parserPath?: string;
  value?: unknown;
  normalizedValue?: unknown;
  unit?: string;
  confidence?: number;
  validationStatus?: string;
  reviewStatus?: string;
  locatorStatus?: string;
  locatorPrecision?: string;
  targetPods?: string[];
  sourceLocator?: Record<string, unknown>;
  textQualityFlags?: Record<string, boolean>;
  podValidationStatusByPod?: Record<string, {
    status?: string;
    findingCount?: number;
    findingCodes?: string[];
    validatedAt?: string;
  }>;
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

function confirmedCount(...values: unknown[]): number | null {
  return values.find((value): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0) ?? null;
}

function countLabel(value: number | null, unit = "건") {
  return value === null ? "미확인" : `${value.toLocaleString("ko-KR")}${unit}`;
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

  const contextKey = `${projectId}:${activeTrack}`;
  const requestVersion = useRef(0);
  const [contextResult, setContextResult] = useState<{ key: string; value: WorkspacePageContext } | null>(null);
  const context = contextResult?.key === contextKey ? contextResult.value : null;
  const [contextError, setContextError] = useState<string | null>(null);
  const [intakePlan, setIntakePlan] = useState<IntakeReadinessPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const version = ++requestVersion.current;
    setLoading(true);
    setContextError(null);
    setContextResult(null);
    try {
      const next = await fetchWorkspaceContext(projectId, "input_package", activeTrack);
      if (!next || typeof next !== "object" || Array.isArray(next) || next.projectId !== projectId) {
        throw new Error("Invalid workspace context");
      }
      if (version === requestVersion.current) setContextResult({ key: contextKey, value: next });
    } catch {
      if (version === requestVersion.current) setContextError("자료 입력 패키지 상태를 불러오지 못했습니다. 저장된 자료와 검토 건수는 아직 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }, [activeTrack, contextKey, projectId]);

  useEffect(() => {
    void refresh();
    return () => { requestVersion.current += 1; };
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
      if (!res.ok) throw new Error(`자료 보완 계획 생성 실패 (${res.status})`);
      const body = (await res.json()) as { intakeReadinessPlan?: IntakeReadinessPlan };
      setIntakePlan(body.intakeReadinessPlan ?? null);
      setMessage(body.intakeReadinessPlan?.nextRecommendedAction ?? "자료 접수 및 보완 계획을 업데이트했습니다.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "자료 보완 계획 생성에 실패했습니다.");
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
        body: JSON.stringify({ podKey, options: { autoCascade: true } }),
      });
      if (!res.ok) throw new Error(`${label} 실행 실패 (${res.status})`);
      setMessage(`${label} 실행 결과가 프로젝트 원장에 저장되었습니다.`);
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
      setMessage(`PNU 보강 완료: ${body?.summary?.resolvedCount ?? body?.resolvedCount ?? 0}필지 해석`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "PNU 보강에 실패했습니다.");
    } finally {
      setRunning(null);
    }
  };

  const reviewParsedFact = async (recordId: string | undefined, reviewStatus: "approved" | "needs_revision") => {
    if (!recordId) return;
    setRunning(`parsed-fact-review:${recordId}`);
    setError(null);
    setMessage(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/deal-state-records/${encodeURIComponent(recordId)}/review`, {
        method: "PATCH",
        headers: { ...authHeaders("web-reviewer"), "Content-Type": "application/json" },
        body: JSON.stringify({
          reviewStatus,
          comment: reviewStatus === "approved" ? "파싱 후보값을 기준값 후보로 승인" : "보완자료 또는 근거 위치 보강 필요",
        }),
      });
      if (!res.ok) throw new Error(`파싱 후보값 검토 저장 실패 (${res.status})`);
      setMessage(reviewStatus === "approved" ? "파싱 후보값을 승인했습니다." : "보완 요청 상태로 저장했습니다.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "파싱 후보값 검토 저장에 실패했습니다.");
    } finally {
      setRunning(null);
    }
  };

  const pageData = asRecord(context?.pageData);
  const unavailableSections = new Set(context?.unavailableSections ?? []);
  const hasParsedFacts = !unavailableSections.has("parsedFacts") && Array.isArray(pageData.parsedFacts);
  const hasMissingInputs = !unavailableSections.has("missingInputs") && Array.isArray(context?.missingInputRegister);
  const landEvidenceUnavailable = unavailableSections.has("landEvidence");
  const cascadeDataUnavailable = ["landEvidence", "parsedFacts", "assumptionOverrides", "missingInputs"]
    .some((section) => unavailableSections.has(section));
  const documents = asArray<SourceDocument>(pageData.sourceDocuments);
  const sourceSummary = asRecord(pageData.sourceDocumentSummary);
  const landLedgerSummary = asRecord(pageData.landLedgerSummary);
  const landEvidenceSummary = asRecord(pageData.landEvidenceSummary);
  const parsedFacts = asArray<ParsedFact>(pageData.parsedFacts);
  const parsedFactSummary = asRecord(pageData.parsedFactSummary);
  const recommendedActions = asArray<string>(pageData.recommendedActions);
  const missingInputs = asArray<Record<string, unknown>>(context?.missingInputRegister);
  const projectName = useProjectName(projectId, displayText(context?.projectMemory?.projectName, `프로젝트 ${projectId}`));
  const docCount = confirmedCount(context?.projectMemory?.sourceDocumentCount, sourceSummary.count, Array.isArray(pageData.sourceDocuments) ? documents.length : undefined);
  const evidenceCount = unavailableSections.has("landEvidence") ? null : confirmedCount(context?.projectMemory?.landEvidenceCount, landEvidenceSummary.count);
  const parcelCount = confirmedCount(landLedgerSummary.parcelCount);
  const pnuCount = confirmedCount(landLedgerSummary.pnuCount);
  const parsedFactCount = unavailableSections.has("parsedFacts") ? null : confirmedCount(parsedFactSummary.recordCount, hasParsedFacts ? parsedFacts.length : undefined);
  const missingInputCount = hasMissingInputs ? missingInputs.length : null;

  return (
    <div className="space-y-6 pb-8" data-testid="input-package-page">
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
              업로드 원본, 자동분류 결과, 토지·PNU·공시지가 증빙, Pod 실행 결과를 같은 프로젝트 저장소에서 추적합니다.
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
        <div role="status" className="flex items-center justify-center rounded-lg border border-slate-200 bg-white p-5 text-sm font-bold text-slate-500 dark:border-slate-800 dark:bg-slate-900">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" aria-hidden="true" /> 자료 입력 패키지를 불러오는 중입니다.
        </div>
      ) : contextError ? (
        <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          <p>{contextError}</p>
          <button type="button" onClick={() => void refresh()} disabled={running !== null}
            className="mt-3 inline-flex min-h-10 items-center gap-2 rounded-lg border border-amber-300 px-3 font-bold focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:opacity-50">
            <RefreshCw className="h-4 w-4" aria-hidden="true" />자료 상태 다시 불러오기
          </button>
        </div>
      ) : null}
      {context?.contextCompleteness === "partial" ? (
        <div role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          <p>일부 자료 상태만 확인했습니다. 확인하지 못한 자료와 검토 건수는 미확인으로 표시합니다. 업로드와 사업수지 입력 검토는 계속할 수 있습니다.</p>
          <button type="button" onClick={() => void refresh()} disabled={loading || running !== null}
            className="mt-3 inline-flex min-h-10 items-center gap-2 rounded-lg border border-amber-300 px-3 font-bold focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:opacity-50">
            <RefreshCw className="h-4 w-4" aria-hidden="true" />자료 상태 다시 불러오기
          </button>
        </div>
      ) : null}
          {error ? <Notice tone="bad">{error}</Notice> : null}
          {message ? <Notice tone="good">{message}</Notice> : null}

          {context ? <section aria-label="저장 자료 현황" className="grid gap-4 md:grid-cols-2 xl:grid-cols-6">
            <Kpi label="저장 문서" value={countLabel(docCount)} icon={<FileArchive />} />
            <Kpi label="토지조서 필지" value={countLabel(parcelCount, "필지")} icon={<FileText />} />
            <Kpi label="PNU 확보" value={pnuCount === null || parcelCount === null ? "미확인" : `${pnuCount.toLocaleString("ko-KR")}/${parcelCount.toLocaleString("ko-KR")}`} icon={<Database />} />
            <Kpi label="토지 증빙" value={countLabel(evidenceCount)} icon={<Database />} />
            <Kpi label="파싱 후보값" value={countLabel(parsedFactCount)} icon={<GitBranch />} />
            <Kpi label="보완 필요" value={countLabel(missingInputCount)} icon={<GitBranch />} />
          </section> : null}

          <section className="rounded-lg border border-indigo-200 bg-white p-5 shadow-sm dark:border-indigo-900/60 dark:bg-slate-900">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <p className="text-xs font-black uppercase tracking-wide text-indigo-600 dark:text-indigo-300">
                  Selected Track Workflow
                </p>
                <h2 className="mt-1 text-xl font-black text-slate-950 dark:text-white">
                  {TRACK_LABELS[activeTrack]} 자료 보완 흐름
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

          <SmartIntake projectId={projectId} onProcessed={context && !landEvidenceUnavailable ? finalizeIntake : () => void refresh()} />
          <FeasibilityExtractReview key={projectId} projectId={projectId} onApplied={() => void refresh()} />

          {context ? <>
          <section className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
            <Panel title="다음 보완 작업">
              <div className="flex flex-wrap gap-2">
                <ActionButton label="자료 보완 계획 생성" busy={running === "finalize"} disabled={running !== null || landEvidenceUnavailable} onClick={() => void finalizeIntake()} />
                <ActionButton label="PNU 일괄 해석" busy={running === "pnu"} disabled={running !== null || landEvidenceUnavailable || !landLedgerSummary.present} onClick={() => void runPnuResolve()} />
                <Link href={`/project/${projectId}/land`} className="inline-flex h-10 items-center rounded-lg border border-slate-300 px-4 text-sm font-bold dark:border-slate-700">토지·인허가 미리보기</Link>
                <ActionButton label="Market-Product Pod" busy={running === "market_product"} disabled={running !== null || cascadeDataUnavailable} onClick={() => void runPod("market_product", "Market-Product Pod")} />
                <ActionButton label="Feasibility-CF Pod" busy={running === "feasibility_cashflow"} disabled={running !== null || cascadeDataUnavailable} onClick={() => void runPod("feasibility_cashflow", "Feasibility-Cashflow Pod")} />
              </div>
              {landEvidenceUnavailable || cascadeDataUnavailable ? (
                <p className="mt-3 text-xs leading-5 text-amber-800 dark:text-amber-200">확인하지 못한 자료에 의존하는 보완 계획·PNU 저장·연쇄 Pod 실행은 자료 상태를 다시 확인한 뒤 사용할 수 있습니다. 업로드와 사업수지 입력 검토는 계속할 수 있습니다.</p>
              ) : null}
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
                    자료를 업로드하면 오케스트레이터가 어떤 Pod를 실행해야 하는지와 부족한 자료를 안내합니다.
                  </p>
                )}
              </div>
            </Panel>

            <Panel title="보완 필요 항목">
              {missingInputCount === null ? (
                <p className="text-sm text-slate-500">보완 필요 항목을 확인하지 못했습니다.</p>
              ) : missingInputs.length ? (
                <div className="space-y-2">
                  {missingInputs.slice(0, 8).map((item, index) => (
                    <div key={index} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                      <p className="font-black">{displayText(item.title ?? item.label ?? item.code, `보완 항목 ${index + 1}`)}</p>
                      <p className="mt-1 text-xs leading-5">
                        {displayText(item.reason ?? item.description ?? item.message, "다음 단계 실행을 위해 보완이 필요합니다.")}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm font-bold text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100">
                  현재 공통 자료 기준으로 즉시 표시할 보완 항목이 없습니다.
                </div>
              )}
            </Panel>
          </section>

          <Panel title="AI 파싱 후보값 원장">
            {!hasParsedFacts ? (
              <p className="text-sm text-slate-500">파싱 후보값 목록을 확인하지 못했습니다.</p>
            ) : parsedFacts.length ? (
              <div className="space-y-4">
                <div className="grid gap-3 md:grid-cols-3">
                  <MiniStat label="전체 후보값" value={countLabel(parsedFactCount)} />
                  <MiniStat label="근거 위치 부족" value={countLabel(confirmedCount(parsedFactSummary.missingLocatorCount))} />
                  <MiniStat label="핵심 근거 위치 부족" value={countLabel(confirmedCount(parsedFactSummary.criticalMissingLocatorCount))} />
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[1160px] text-left text-sm">
                    <thead className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-800">
                      <tr>
                        <th className="px-3 py-2">표준 필드</th>
                        <th className="px-3 py-2">추출값</th>
                        <th className="px-3 py-2">검증 Pod</th>
                        <th className="px-3 py-2">신뢰도</th>
                        <th className="px-3 py-2">근거 위치</th>
                        <th className="px-3 py-2">Pod 검증</th>
                        <th className="px-3 py-2">상태</th>
                        <th className="px-3 py-2">검토 액션</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                      {parsedFacts.slice(0, 12).map((fact, index) => (
                        <tr key={fact.recordId ?? `${fact.canonicalField}-${index}`}>
                          <td className="px-3 py-3">
                            <p className="font-black text-slate-950 dark:text-white">
                              {displayText(fact.canonicalField ?? fact.key, "미매핑 필드")}
                            </p>
                            <p className="mt-1 text-xs text-slate-500">{displayText(fact.parserPath, "-")}</p>
                          </td>
                          <td className="px-3 py-3 font-bold text-slate-700 dark:text-slate-200">
                            {formatParsedFactValue(fact)}
                          </td>
                          <td className="px-3 py-3">
                            <div className="flex flex-wrap gap-1">
                              {(fact.targetPods?.length ? fact.targetPods : ["chief_orchestrator"]).map((pod) => (
                                <span key={pod} className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-bold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                                  {podLabel(pod)}
                                </span>
                              ))}
                            </div>
                          </td>
                          <td className="px-3 py-3 text-slate-500">{formatConfidence(fact.confidence)}</td>
                          <td className="px-3 py-3 text-slate-500">{parsedFactLocatorSummary(fact)}</td>
                          <td className="px-3 py-3">
                            <PodValidationChips fact={fact} />
                          </td>
                          <td className="px-3 py-3">
                            <span className={`rounded-full px-2 py-1 text-xs font-black ${parsedFactStatusClass(fact.validationStatus)}`}>
                              {parsedFactStatusLabel(fact.validationStatus)}
                            </span>
                            {fact.reviewStatus ? (
                              <p className="mt-1 text-[11px] font-bold text-slate-500">검토: {parsedFactReviewLabel(fact.reviewStatus)}</p>
                            ) : null}
                          </td>
                          <td className="px-3 py-3">
                            <div className="flex flex-wrap gap-1">
                              <button
                                type="button"
                                disabled={!fact.recordId || running === `parsed-fact-review:${fact.recordId}`}
                                onClick={() => void reviewParsedFact(fact.recordId, "approved")}
                                className="rounded-md bg-emerald-50 px-2 py-1 text-[11px] font-black text-emerald-700 disabled:opacity-40 dark:bg-emerald-950/30 dark:text-emerald-100"
                              >
                                승인
                              </button>
                              <button
                                type="button"
                                disabled={!fact.recordId || running === `parsed-fact-review:${fact.recordId}`}
                                onClick={() => void reviewParsedFact(fact.recordId, "needs_revision")}
                                className="rounded-md bg-amber-50 px-2 py-1 text-[11px] font-black text-amber-700 disabled:opacity-40 dark:bg-amber-950/30 dark:text-amber-100"
                              >
                                보완요청
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <div className="rounded-lg bg-slate-50 p-5 text-sm leading-6 text-slate-500 dark:bg-slate-800/70">
                아직 저장된 파싱 후보값이 없습니다. 사업계획서, IM, 토지조서, 사업수지 Excel을 업로드하면 ParserAgent가 후보값을 원장에 저장하고 각 Pod가 검증합니다.
              </div>
            )}
          </Panel>

          <Panel title="저장된 원본 문서">
            {!Array.isArray(pageData.sourceDocuments) ? (
              <p className="text-sm text-slate-500">저장된 원본 문서 목록을 확인하지 못했습니다.</p>
            ) : documents.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-left text-sm">
                  <thead className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-800">
                    <tr>
                      <th className="px-3 py-2">문서 유형</th>
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
                        <td className="px-3 py-3 text-slate-500">
                          {doc.documentType === "feasibility_workbook" && doc.validationStatus === "failed"
                            ? <span className="font-semibold text-amber-800 dark:text-amber-200">검증 항목 확인 필요</span>
                            : displayText(doc.validationStatus, "-")}
                        </td>
                        <td className="px-3 py-3 text-slate-500">{displayText(doc.archiveIntegrityStatus, "-")}</td>
                        <td className="px-3 py-3 text-slate-500">{formatDate(doc.uploadedAt)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="rounded-lg bg-slate-50 p-5 text-sm text-slate-500 dark:bg-slate-800/70">
                아직 저장된 원본 문서가 없습니다. 위의 스마트 업로드에 파일을 올리면 서버 원장에 저장됩니다.
              </div>
            )}
          </Panel>
          </> : null}
    </div>
  );
}

function Kpi({ label, value, icon }: { label: string; value: string; icon: ReactNode }) {
  return (
    <div role="group" aria-label={label} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
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

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-950">
      <p className="text-xs font-bold text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-black text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function formatParsedFactValue(fact: ParsedFact) {
  const value = fact.normalizedValue ?? fact.value;
  if (typeof value === "number") {
    return `${value.toLocaleString("ko-KR")}${fact.unit ? ` ${fact.unit}` : ""}`;
  }
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "-";
  return JSON.stringify(value);
}

function formatConfidence(value?: number) {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  return `${Math.round(value * 100)}%`;
}

function parsedFactLocatorSummary(fact: ParsedFact) {
  const locator = fact.sourceLocator ?? {};
  const preferred = [
    locator.documentName ? String(locator.documentName) : "",
    locator.pageNumber || locator.page ? `p.${String(locator.pageNumber ?? locator.page)}` : "",
    locator.sheet || locator.sheetName ? String(locator.sheet ?? locator.sheetName) : "",
    locator.cell ? `셀 ${String(locator.cell)}` : "",
    locator.apiRowId ? `API row ${String(locator.apiRowId)}` : "",
    locator.evidenceId ? `증빙 ${String(locator.evidenceId)}` : "",
  ].filter(Boolean);
  if (preferred.length) return preferred.join(" · ");
  if (fact.locatorStatus === "document_only") return "문서 단위";
  return "위치 미확인";
}

function podLabel(podKey: string) {
  return ({
    land_legal: "토지·법무",
    market_product: "시장·상품",
    cost_schedule: "원가·일정",
    feasibility_cashflow: "사업수지·CF",
    pf_credit: "PF·심사",
    public_policy: "공공·정책",
    chief_orchestrator: "오케스트레이터",
  } as Record<string, string>)[podKey] ?? podKey;
}

function parsedFactStatusLabel(status?: string) {
  if (status === "pod_validated") return "Pod 검증 완료";
  if (status === "parsed") return "파싱 완료";
  if (status === "needs_source_locator") return "근거 위치 필요";
  if (status === "needs_review") return "검토 필요";
  return status || "대기";
}

function parsedFactStatusClass(status?: string) {
  if (status === "pod_validated" || status === "parsed") {
    return "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-100";
  }
  if (status === "needs_source_locator") {
    return "bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-100";
  }
  return "bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-100";
}

function parsedFactReviewLabel(status?: string) {
  if (status === "approved") return "승인";
  if (status === "waived") return "유보";
  if (status === "resolved") return "해결";
  if (status === "needs_revision") return "보완요청";
  return status ?? "-";
}

function podValidationStatusLabel(status?: string) {
  if (status === "validated") return "검증";
  if (status === "needs_source_locator") return "근거";
  if (status === "needs_review") return "검토";
  return "대기";
}

function podValidationStatusClass(status?: string) {
  if (status === "validated") {
    return "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-100";
  }
  if (status === "needs_source_locator") {
    return "bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-100";
  }
  if (status === "needs_review") {
    return "bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-100";
  }
  return "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-300";
}

function PodValidationChips({ fact }: { fact: ParsedFact }) {
  const statuses = fact.podValidationStatusByPod ?? {};
  const targetPods = fact.targetPods?.length ? fact.targetPods : ["chief_orchestrator"];
  return (
    <div className="flex flex-wrap gap-1">
      {targetPods.map((pod) => {
        const status = statuses[pod]?.status;
        const findingCount = Number(statuses[pod]?.findingCount ?? 0);
        return (
          <span
            key={pod}
            title={statuses[pod]?.findingCodes?.join(", ") || podLabel(pod)}
            className={`rounded-full px-2 py-1 text-[11px] font-black ${podValidationStatusClass(status)}`}
          >
            {podLabel(pod)} · {podValidationStatusLabel(status)}
            {findingCount ? ` ${findingCount}` : ""}
          </span>
        );
      })}
    </div>
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
