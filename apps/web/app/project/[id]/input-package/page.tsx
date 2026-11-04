"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  ChevronDown,
  Database,
  FileArchive,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import { SmartIntake } from "../../../../components/intake/smart-intake";
import { FeasibilityExtractReview } from "../../../../components/intake/feasibility-extract-review";
import { translateFinancialTerm } from "../../../../lib/financial-terms";
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
  fileSizeBytes?: number;
  uploadedAt?: string;
  validationStatus?: string;
  archiveIntegrityStatus?: string;
};

type ParsedFact = {
  recordId?: string;
  key?: string;
  canonicalField?: string;
  termId?: string;
  value?: unknown;
  normalizedValue?: unknown;
  unit?: string;
  confidence?: number;
  validationStatus?: string;
  reviewStatus?: string;
  locatorStatus?: string;
  targetPods?: string[];
  sourceLocator?: Record<string, unknown>;
  podValidationStatusByPod?: Record<string, {
    status?: string;
    findingCount?: number;
    findingCodes?: string[];
  }>;
};

const INTAKE_VIEWS = [
  { id: "materials", label: "자료·원본" },
  { id: "review", label: "사업수지 검토" },
  { id: "supplement", label: "보완 작업" },
] as const;

type IntakeView = typeof INTAKE_VIEWS[number]["id"];
const REVIEW_ANCHORS = new Set(["#cash-flow-schedule-review", "#cash-flow-financing-review"]);

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
  const selectedView = INTAKE_VIEWS.find(({ id }) => id === searchParams.get("view"))?.id ?? "materials";
  const selectView = (view: IntakeView) => {
    const url = new URL(window.location.href);
    if (view === "materials") url.searchParams.delete("view");
    else url.searchParams.set("view", view);
    if ((url.hash === "#intake-upload" && view !== "materials") || (REVIEW_ANCHORS.has(url.hash) && view !== "review")) url.hash = "";
    window.history.replaceState(null, "", url);
  };

  const handleViewKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const count = INTAKE_VIEWS.length;
    let next: number;
    switch (event.key) {
      case "ArrowLeft": next = (index + count - 1) % count; break;
      case "ArrowRight": next = (index + 1) % count; break;
      case "Home": next = 0; break;
      case "End": next = count - 1; break;
      default: return;
    }
    event.preventDefault();
    selectView(INTAKE_VIEWS[next]!.id);
    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  };

  const activeTrack = useMemo<WorkflowTrack>(() => {
    const fromUrl = searchParams.get("track");
    if (isWorkflowTrack(fromUrl)) return fromUrl;
    return getStoredWorkflowTrack() || "development";
  }, [searchParams]);
  const workflow = useMemo(() => getTrackWorkflowDefinition(activeTrack), [activeTrack]);

  // Existing Cash Flow links may name the review section using only a fragment.
  useEffect(() => {
    const url = new URL(window.location.href);
    if (REVIEW_ANCHORS.has(url.hash) && !url.searchParams.has("view")) {
      url.searchParams.set("view", "review");
      window.history.replaceState(null, "", url);
    }
  }, [projectId]);
  useEffect(() => {
    const anchor = window.location.hash;
    if (selectedView === "review" && REVIEW_ANCHORS.has(anchor)) {
      document.getElementById(anchor.slice(1))?.scrollIntoView();
    }
  }, [selectedView, projectId]);

  const contextKey = `${projectId}:${activeTrack}`;
  const requestVersion = useRef(0);
  const [contextResult, setContextResult] = useState<{ key: string; value: WorkspacePageContext } | null>(null);
  const context = contextResult?.key === contextKey ? contextResult.value : null;
  const [contextError, setContextError] = useState<string | null>(null);
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
      const body = (await res.json()) as { intakeReadinessPlan?: { nextRecommendedAction?: string } };
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
  const docCount = confirmedCount(context?.projectMemory?.sourceDocumentCount, sourceSummary.count, Array.isArray(pageData.sourceDocuments) ? documents.length : undefined);
  const evidenceCount = unavailableSections.has("landEvidence") ? null : confirmedCount(context?.projectMemory?.landEvidenceCount, landEvidenceSummary.count);
  const parcelCount = confirmedCount(landLedgerSummary.parcelCount);
  const pnuCount = confirmedCount(landLedgerSummary.pnuCount);
  const parsedFactCount = unavailableSections.has("parsedFacts") ? null : confirmedCount(parsedFactSummary.recordCount, hasParsedFacts ? parsedFacts.length : undefined);
  const missingInputCount = hasMissingInputs ? missingInputs.length : null;
  const projectPath = `/project/${encodeURIComponent(projectId)}`;
  const trackQuery = `?track=${encodeURIComponent(activeTrack)}`;

  return (
    <div className="min-w-0 space-y-4 pb-8" data-testid="input-package-page">
      <header className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
        <div>
          <p className="text-xs font-semibold text-re-secondary">{TRACK_LABELS[activeTrack]}</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-re-text sm:text-[28px]">자료 입력 패키지</h1>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading || running !== null}
          className="inline-flex min-h-11 items-center gap-2 rounded-re-control border border-re-border bg-re-surface px-3 text-sm font-semibold text-re-secondary transition-colors hover:border-re-border-strong hover:bg-re-subtle disabled:opacity-50"
          aria-label="자료 상태 다시 불러오기"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="h-4 w-4" aria-hidden="true" />}
          다시 불러오기
        </button>
      </header>

      {loading ? (
        <p role="status" className="text-sm text-re-secondary">저장 자료를 확인하는 중입니다.</p>
      ) : contextError ? (
        <p role="alert" className="rounded-re-control border border-re-border bg-re-surface px-3 py-2 text-sm text-re-warning">
          자료 입력 패키지 상태를 불러오지 못했습니다. 다시 불러오거나 자료 입력을 계속할 수 있습니다.
        </p>
      ) : context ? (
        <section aria-label="저장 자료 현황" className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-re-secondary">
          <dl className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <div className="inline-flex gap-1.5"><dt>저장 문서</dt><dd className="font-semibold text-re-text">{countLabel(docCount)}</dd></div>
            <div className="inline-flex gap-1.5"><dt>보완 필요</dt><dd className="font-semibold text-re-text">{countLabel(missingInputCount)}</dd></div>
          </dl>
          {context.contextCompleteness === "partial" ? <p role="status" className="text-xs text-re-secondary">일부 자료 상태만 확인했습니다.</p> : null}
        </section>
      ) : null}

      <div role="tablist" aria-label="자료 입력 업무" className="grid grid-cols-3 gap-1 rounded-re-panel border border-re-border bg-re-subtle p-1">
        {INTAKE_VIEWS.map(({ id, label }, index) => (
          <button
            key={id}
            id={`intake-tab-${id}`}
            role="tab"
            type="button"
            aria-selected={selectedView === id}
            aria-controls={`intake-panel-${id}`}
            tabIndex={selectedView === id ? 0 : -1}
            onClick={() => selectView(id)}
            onKeyDown={(event) => handleViewKey(event, index)}
            className={`min-h-11 rounded-re-control px-1.5 text-xs font-semibold transition-colors sm:px-3 sm:text-sm ${selectedView === id ? "border border-re-border bg-re-surface text-re-text shadow-re-panel" : "text-re-secondary hover:bg-re-surface hover:text-re-text"}`}
          >
            {label}
          </button>
        ))}
      </div>

      {error ? <Notice tone="bad">{error}</Notice> : null}
      {message ? <Notice tone="good">{message}</Notice> : null}

      <div id="intake-panel-materials" role="tabpanel" aria-labelledby="intake-tab-materials" hidden={selectedView !== "materials"} className="min-w-0 space-y-4">
        <div id="intake-upload" className="min-w-0 rounded-re-panel border border-re-border bg-re-surface p-3 shadow-re-panel sm:p-5">
          <SmartIntake key={projectId} projectId={projectId} onProcessed={context && !landEvidenceUnavailable ? finalizeIntake : () => void refresh()} />
        </div>

        {context ? (
          <Panel title="저장된 원본 문서" icon={<FileArchive className="h-4 w-4" aria-hidden="true" />}>
            {!Array.isArray(pageData.sourceDocuments) ? (
              <p className="text-sm text-re-secondary">저장된 원본 문서 목록을 확인하지 못했습니다.</p>
            ) : documents.length ? (
              <ul className="divide-y divide-re-border">
                {documents.map((doc, index) => (
                  <li key={`${doc.originalFileName ?? doc.fileName}-${index}`} className="min-w-0 py-3 first:pt-0">
                    <p className="break-words text-sm font-semibold text-re-text">{displayText(doc.originalFileName ?? doc.fileName, "파일명 미확인")}</p>
                    <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-re-secondary">
                      <span>{doc.documentType ? translateFinancialTerm(doc.documentType) : "원본 문서"}</span>
                      <span>{formatBytes(doc.fileSizeBytes)}</span>
                      <span>{formatDate(doc.uploadedAt)}</span>
                      {doc.documentType === "feasibility_workbook" && doc.validationStatus === "failed"
                        ? <span className="font-semibold text-re-warning">검증 항목 확인 필요</span>
                        : doc.validationStatus && <span>검증 {displayText(doc.validationStatus)}</span>}
                      {doc.archiveIntegrityStatus ? <span>무결성 {displayText(doc.archiveIntegrityStatus)}</span> : null}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-re-secondary">아직 저장된 원본 문서가 없습니다. 위에서 파일을 추가하세요.</p>
            )}
            {documents.length ? (
              <Link href={`${projectPath}/documents/review${trackQuery}`} className="mt-2 inline-flex min-h-11 items-center rounded-re-control text-sm font-semibold text-re-action hover:underline">문서·출처 검토 열기</Link>
            ) : null}
          </Panel>
        ) : null}
      </div>

      {/* Keep the editor mounted so switching views does not discard its unsaved inputs. */}
      <div id="intake-panel-review" role="tabpanel" aria-labelledby="intake-tab-review" hidden={selectedView !== "review"} className="min-w-0 space-y-4">
        <FeasibilityExtractReview key={projectId} projectId={projectId} onApplied={() => void refresh()} />
        {context ? (
          <Disclosure title={`AI 추출값 · ${countLabel(parsedFactCount)}`}>
            {!hasParsedFacts ? (
              <p className="text-sm text-re-secondary">파싱 후보값 목록을 확인하지 못했습니다.</p>
            ) : parsedFacts.length ? (
              <>
                <p className="mb-2 text-xs text-re-secondary">
                  근거 위치 부족 {countLabel(confirmedCount(parsedFactSummary.missingLocatorCount))} · 핵심 근거 위치 부족 {countLabel(confirmedCount(parsedFactSummary.criticalMissingLocatorCount))}
                </p>
                <ul className="divide-y divide-re-border">
                  {parsedFacts.slice(0, 12).map((fact, index) => (
                    <li key={fact.recordId ?? `${fact.canonicalField}-${index}`} className="space-y-2 py-3">
                      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                        <div className="min-w-0 flex-1">
                          <p className="break-words text-sm font-semibold text-re-text">{parsedFactLabel(fact, index)}</p>
                          <p className="break-words text-sm font-semibold text-re-text">{formatParsedFactValue(fact)}</p>
                        </div>
                        <div className="text-xs">
                          <span className={`inline-block rounded-full px-2 py-1 font-semibold ${parsedFactStatusClass(fact.validationStatus)}`}>
                            {parsedFactStatusLabel(fact.validationStatus)}
                          </span>
                          {fact.reviewStatus ? <p className="mt-1 text-re-secondary">검토 {parsedFactReviewLabel(fact.reviewStatus)}</p> : null}
                        </div>
                      </div>
                      <p className="break-words text-xs text-re-secondary">{parsedFactLocatorSummary(fact)}</p>
                      <div className="flex flex-wrap gap-2">
                        <button type="button" disabled={!fact.recordId || running !== null}
                          onClick={() => void reviewParsedFact(fact.recordId, "approved")}
                          className="min-h-11 rounded-re-control border border-re-border px-3 text-sm font-semibold text-re-success transition-colors hover:bg-re-subtle disabled:opacity-40">
                          승인
                        </button>
                        <button type="button" disabled={!fact.recordId || running !== null}
                          onClick={() => void reviewParsedFact(fact.recordId, "needs_revision")}
                          className="min-h-11 rounded-re-control border border-re-border px-3 text-sm font-semibold text-re-warning transition-colors hover:bg-re-subtle disabled:opacity-40">
                          보완 요청
                        </button>
                      </div>
                      <details className="text-xs text-re-secondary">
                        <summary className="cursor-pointer py-1 font-semibold hover:text-re-text">추출 신뢰도·검증 내역</summary>
                        <p className="my-2">추출 신뢰도 {formatConfidence(fact.confidence)}</p>
                        <PodValidationChips fact={fact} />
                      </details>
                    </li>
                  ))}
                </ul>
                <Link href={`${projectPath}/documents/review${trackQuery}`} className="inline-flex min-h-11 items-center text-sm font-semibold text-re-action hover:underline">문서·출처 검토 열기</Link>
              </>
            ) : (
              <p className="text-sm text-re-secondary">아직 저장된 파싱 후보값이 없습니다. 자료를 추가하면 여기에 표시됩니다.</p>
            )}
          </Disclosure>
        ) : null}
      </div>

      <div id="intake-panel-supplement" role="tabpanel" aria-labelledby="intake-tab-supplement" hidden={selectedView !== "supplement"} className="min-w-0 space-y-4">
        {context ? (
          <>
            <Panel title="보완 필요 항목">
              {missingInputCount === null ? (
                <p className="text-sm text-re-secondary">보완 필요 항목을 확인하지 못했습니다.</p>
              ) : missingInputs.length ? (
                <ul className="divide-y divide-re-border">
                  {missingInputs.slice(0, 8).map((item, index) => (
                    <li key={index} className="py-3 first:pt-0">
                      <p className="break-words text-sm font-semibold text-re-text">{displayText(item.title ?? item.label ?? item.code, `보완 항목 ${index + 1}`)}</p>
                      <p className="mt-1 break-words text-xs leading-5 text-re-secondary">
                        {displayText(item.reason ?? item.description ?? item.message, "다음 단계 실행을 위해 보완이 필요합니다.")}
                      </p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-re-secondary">현재 공통 자료 기준으로 즉시 표시할 보완 항목이 없습니다.</p>
              )}
              {recommendedActions.length ? (
                <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-re-secondary">
                  {recommendedActions.map((item, index) => <li key={index}>{item}</li>)}
                </ul>
              ) : null}
            </Panel>

            <Disclosure title="토지 자료 현황과 보완 실행">
              <dl aria-label="토지 자료 현황" className="mb-4 flex flex-wrap gap-x-4 gap-y-2 text-sm text-re-secondary">
                <div className="inline-flex gap-1.5"><dt>토지조서 필지</dt><dd className="font-semibold text-re-text">{countLabel(parcelCount, "필지")}</dd></div>
                <div className="inline-flex gap-1.5"><dt>PNU 확보</dt><dd className="font-semibold text-re-text">{pnuCount === null || parcelCount === null ? "미확인" : parcelCount === 0 ? "산정 전" : `${pnuCount.toLocaleString("ko-KR")}/${parcelCount.toLocaleString("ko-KR")}`}</dd></div>
                <div className="inline-flex gap-1.5"><dt>토지 증빙</dt><dd className="font-semibold text-re-text">{countLabel(evidenceCount)}</dd></div>
              </dl>
              <div className="flex flex-wrap gap-2">
                <ActionButton label="자료 보완 계획 생성" busy={running === "finalize"} disabled={running !== null || landEvidenceUnavailable} onClick={() => void finalizeIntake()} />
                <ActionButton label="PNU 일괄 해석" busy={running === "pnu"} disabled={running !== null || landEvidenceUnavailable || !landLedgerSummary.present} onClick={() => void runPnuResolve()} />
                <Link href={`${projectPath}/land${trackQuery}`} className="inline-flex min-h-11 items-center rounded-re-control border border-re-border px-3 text-sm font-semibold text-re-action transition-colors hover:bg-re-subtle">토지·인허가 미리보기</Link>
                <ActionButton label="Market-Product Pod" busy={running === "market_product"} disabled={running !== null || cascadeDataUnavailable} onClick={() => void runPod("market_product", "Market-Product Pod")} />
                <ActionButton label="Feasibility-CF Pod" busy={running === "feasibility_cashflow"} disabled={running !== null || cascadeDataUnavailable} onClick={() => void runPod("feasibility_cashflow", "Feasibility-Cashflow Pod")} />
              </div>
              {landEvidenceUnavailable || cascadeDataUnavailable ? (
                <p className="mt-3 text-xs leading-5 text-re-warning">자료 상태를 다시 확인한 뒤 보완 계획·PNU 해석·연쇄 실행을 사용할 수 있습니다. 자료 추가와 사업수지 입력 검토는 계속할 수 있습니다.</p>
              ) : null}
            </Disclosure>
          </>
        ) : null}
        <Disclosure title={`${TRACK_LABELS[activeTrack]} 자료 보완 흐름`}>
          <p className="text-sm text-re-secondary">{workflow.intent}</p>
          <p className="mt-2 text-sm font-semibold text-re-text">{workflow.decisionQuestion}</p>
          <ol className="mt-3 list-decimal space-y-3 pl-5">
            {workflow.steps.map((step) => (
              <li key={step.key} className="pl-1">
                <p className="text-sm font-semibold text-re-text">{step.title}</p>
                <p className="mt-1 text-xs leading-5 text-re-secondary">{step.purpose}</p>
              </li>
            ))}
          </ol>
        </Disclosure>
      </div>
    </div>
  );
}

function Panel({ title, icon, children }: { title: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <section className="min-w-0 rounded-re-panel border border-re-border bg-re-surface p-4 shadow-re-panel sm:p-5">
      <h2 className="mb-3 flex items-center gap-2 text-base font-semibold text-re-text">{icon}{title}</h2>
      {children}
    </section>
  );
}

function Disclosure({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="group min-w-0 rounded-re-panel border border-re-border bg-re-surface shadow-re-panel">
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 rounded-re-panel px-4 py-3 text-sm font-semibold text-re-text transition-colors hover:bg-re-subtle [&::-webkit-details-marker]:hidden">
        {title}
        <ChevronDown className="h-4 w-4 shrink-0 transition-transform group-open:rotate-180" aria-hidden="true" />
      </summary>
      <div className="border-t border-re-border px-4 py-3">{children}</div>
    </details>
  );
}

function parsedFactLabel(fact: ParsedFact, index: number) {
  const term = fact.termId ?? fact.canonicalField ?? fact.key;
  const label = translateFinancialTerm(term);
  return term && (label !== term || /[가-힣]/.test(label)) ? label : `추출 항목 ${index + 1}`;
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
  return (
    <p role={tone === "bad" ? "alert" : "status"} className={`rounded-re-control border border-re-border bg-re-surface px-3 py-2 text-sm font-semibold ${tone === "bad" ? "text-re-danger" : "text-re-success"}`}>
      {children}
    </p>
  );
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
      className="inline-flex min-h-11 items-center justify-center gap-2 rounded-re-control border border-re-border px-3 text-sm font-semibold text-re-text transition-colors hover:bg-re-subtle disabled:cursor-not-allowed disabled:opacity-40"
    >
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
      {label}
    </button>
  );
}
