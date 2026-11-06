"use client";

import { useEffect, useRef, useState } from "react";
import { FileSearch, Loader2 } from "lucide-react";
import { useAuth } from "../auth/auth-context";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { hasPermission } from "../../lib/authorization";
import type { DocumentReviewData } from "../../lib/document-review-workspace";

import { SOURCE_REVIEW_PROFILE_ID, SOURCE_REVIEW_PROFILE_VERSION, SOURCE_REVIEW_IDEMPOTENCY_PREFIX } from "../../lib/intake-processing-status";

type Progress = { status: "running" | "needs_review" | "needs_mapping" | "failed"; message: string; terminalRunId?: string };

export function ArchiveExtractionAction({ projectId, originals, onComplete, compact = false, disabled = false }: {
  projectId: string;
  originals: DocumentReviewData;
  onComplete: () => void;
  compact?: boolean;
  disabled?: boolean;
}) {
  const { user, status, permissions } = useAuth();
  const actor = status === "authenticated" ? JSON.stringify([user?.tenantId, user?.userId]) : "";
  const canAnalyze = status === "authenticated" && hasPermission(permissions, "project:analyze");
  const request = useRef<AbortController | null>(null);
  const attemptKeys = useRef<Record<string, string>>({});
  const [progress, setProgress] = useState<Record<string, Progress>>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setProgress({});
    attemptKeys.current = {};
    setBusy(false);
    return () => {
      request.current?.abort();
      request.current = null;
    };
  }, [projectId, actor, canAnalyze]);

  const documents = originals.projectId === projectId ? originals.sourceDocuments.flatMap((document) => {
    const archiveId = document.archiveId ?? document.originalFileHash ?? document.sha256;
    const name = document.originalFileName ?? document.fileName ?? "";
    return typeof archiveId === "string" && /^[0-9a-f]{64}$/i.test(archiveId) && /\.(pdf|xlsx|xlsm)$/i.test(name)
      ? [{ archiveId: archiveId.toLowerCase(), name }] : [];
  }) : [];
  const uniqueDocuments = documents.filter((document, index) => documents.findIndex((item) => item.archiveId === document.archiveId) === index);
  // Reopening shows stored registry results, not a fresh local "waiting" state.
  // Only candidate records count; preview cells never become review/approval data.
  const persistedProgress: Record<string, Progress> = {};
  if (originals.projectId === projectId) {
    const packages = originals.packages.filter((item) => item.parsingRun.profileId === SOURCE_REVIEW_PROFILE_ID)
      .sort((left, right) => String(left.parsingRun.requestedAt ?? left.parsingRun.createdAt ?? "").localeCompare(String(right.parsingRun.requestedAt ?? right.parsingRun.createdAt ?? "")));
    for (const item of packages) {
      const archiveId = item.documentVersion.archiveId ?? item.documentVersion.sourceHash ?? item.documentVersion.sha256;
      if (typeof archiveId !== "string" || !/^[0-9a-f]{64}$/i.test(archiveId)) continue;
      const run = item.parsingRun;
      const terminalRunId = typeof run.parsingRunId === "string" ? run.parsingRunId : undefined;
      if (["completed", "completed_with_warnings"].includes(run.executionStatus ?? "")) {
        const count = item.candidates.length;
        persistedProgress[archiveId.toLowerCase()] = count > 0
          ? { status: "needs_review", message: "저장된 검토 후보 " + count.toLocaleString("ko-KR") + "건 · 원본과 단위·기준을 대조해 주세요.", terminalRunId }
          : { status: "needs_mapping", message: "추출은 완료됐지만 지원 범위에서 검토 후보를 찾지 못했습니다. 원본을 읽거나 새 추출로 다시 확인하세요.", terminalRunId };
      } else if (["failed", "cancelled"].includes(run.executionStatus ?? "")) {
        persistedProgress[archiveId.toLowerCase()] = { status: "failed", message: "저장된 추출 실행이 중단됐습니다. 새 실행으로 다시 확인할 수 있습니다.", terminalRunId };
      } else {
        persistedProgress[archiveId.toLowerCase()] = { status: "running", message: "서버에서 추출 중입니다. 최신 원장을 새로고침해 결과를 확인하세요." };
      }
    }
  }
  const currentProgress = { ...persistedProgress, ...progress };
  const failedDocuments = uniqueDocuments.filter((document) => currentProgress[document.archiveId]?.status === "failed");
  const hasUnattempted = uniqueDocuments.some((document) => !currentProgress[document.archiveId]);
  const pending = failedDocuments.length ? failedDocuments : uniqueDocuments.filter((document) => {
    const state = currentProgress[document.archiveId];
    return !state || state.status === "needs_mapping";
  });

  async function extract() {
    if (!canAnalyze || disabled || request.current || !pending.length) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    let attempted = false;
    function update(archiveId: string, value: Progress) {
      if (!controller.signal.aborted) setProgress((current) => ({ ...current, [archiveId]: value }));
    }
    for (const document of pending) {
      if (controller.signal.aborted) break;
      attempted = true;
      if (currentProgress[document.archiveId]?.terminalRunId) {
        attemptKeys.current[document.archiveId] = SOURCE_REVIEW_IDEMPOTENCY_PREFIX + document.archiveId + "-" + crypto.randomUUID();
      }
      const idempotencyKey = attemptKeys.current[document.archiveId] ?? SOURCE_REVIEW_IDEMPOTENCY_PREFIX + document.archiveId;
      attemptKeys.current[document.archiveId] = idempotencyKey;
      update(document.archiveId, { status: "running", message: "원본과 일치하는 값을 확인하고 있습니다." });
      try {
        const response = await fetch(apiBase + "/projects/" + encodeURIComponent(projectId) + "/parsing-v2/runs", {
          method: "POST", cache: "no-store",
          headers: { ...authHeaders("web"), "Content-Type": "application/json" },
          body: JSON.stringify({
            archiveId: document.archiveId, profileId: SOURCE_REVIEW_PROFILE_ID, profileVersion: SOURCE_REVIEW_PROFILE_VERSION,
            idempotencyKey,
          }),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Candidate extraction unavailable");
        const body = await response.json();
        const run = body.parsingRun;
        if (body.candidateOnly !== true || body.authoritative !== false || run?.projectId !== projectId) {
          throw new Error("Candidate extraction scope not verified");
        }
        if (["failed", "cancelled"].includes(run?.executionStatus) && typeof run?.parsingRunId === "string" && run.parsingRunId) {
          update(document.archiveId, {
            status: "failed", terminalRunId: run.parsingRunId,
            message: "원본값 추출이 중단됐습니다. 다시 확인을 누르면 새 추출 실행으로 재시도합니다.",
          });
          continue;
        }
        if (!["completed", "completed_with_warnings"].includes(run?.executionStatus)) throw new Error("Candidate extraction incomplete");
        const collection = body.candidateCollection ?? body.orchestration?.candidateCollection ?? run?.orchestrationArtifact?.candidateCollection;
        const count: unknown = collection?.candidateCount;
        if (typeof count !== "number" || !Number.isSafeInteger(count) || count < 0) throw new Error("Candidate result not verified");
        update(document.archiveId, count > 0
          ? { status: "needs_review", message: "검토 후보 " + count.toLocaleString("ko-KR") + "건 · 원본과 단위·기준을 대조해 주세요." }
          : { status: "needs_mapping", terminalRunId: typeof run.parsingRunId === "string" ? run.parsingRunId : undefined,
            message: "지원하는 명시적 항목을 찾지 못했습니다. 원본 페이지 읽기나 항목 연결이 필요합니다." });
      } catch {
        update(document.archiveId, { status: "failed", message: "원본값 추출을 완료하지 못했습니다. 연결과 문서 상태를 확인한 뒤 다시 시도해 주세요." });
      }
    }
    if (request.current === controller) {
      request.current = null;
      setBusy(false);
    }
    if (attempted && !controller.signal.aborted) onComplete();
  }

  const states = uniqueDocuments.map((document) => currentProgress[document.archiveId]);
  const files = (
    <ul className="space-y-2" aria-live={compact ? undefined : "polite"}>
        {uniqueDocuments.map((document) => <li key={document.archiveId} className="text-xs leading-5">
          <span className="break-words font-semibold text-slate-800 dark:text-slate-200">{document.name}</span>
          <p className={currentProgress[document.archiveId]?.status === "failed" ? "text-rose-700 dark:text-rose-300" : "text-slate-600 dark:text-slate-300"}>
            {currentProgress[document.archiveId]?.message ?? "원본값 추출 대기"}
          </p>
        </li>)}
      </ul>
  );

  if (compact && !uniqueDocuments.length) return null;

  return (
    <section aria-label="보관 원본값 추출" className="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-700 dark:bg-slate-900">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">보관 문서에서 검토할 값 찾기</h3>
          <p className="mt-1 text-xs leading-5 text-slate-600 dark:text-slate-300">
            PDF 앞 20쪽과 Excel 최대 2,500셀에서 명시된 항목·단위를 확인합니다. 추출한 후보를 원본과 대조하고, 한도를 넘는 범위와 표 항목은 추가로 검토해 주세요.
          </p>
        </div>
        {canAnalyze ? <button type="button" onClick={() => void extract()} disabled={disabled || busy || !pending.length}
          className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-blue-700 px-4 text-sm font-semibold text-white hover:bg-blue-800 disabled:opacity-50">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSearch className="h-4 w-4" />}
          {busy ? "보관 원본 확인 중" : Object.values(currentProgress).some((item) => item.status === "failed") ? "실패한 문서 다시 확인" : !hasUnattempted && Object.values(currentProgress).some((item) => item.status === "needs_mapping") ? "값이 없는 문서 다시 추출" : "보관 원본값 추출"}
        </button> : <p className="text-xs text-slate-500">원본값 추출에는 프로젝트 분석 권한이 필요합니다.</p>}
      </div>
      {!uniqueDocuments.length ? <p className="text-xs text-slate-600 dark:text-slate-300">값 추출을 지원하는 보관 PDF·Excel이 없습니다.</p> : null}
      {compact ? (
        <>
          <p role="status" aria-live="polite" aria-atomic="true" className="text-xs leading-5 text-slate-600 dark:text-slate-300">
            추출 대상 {uniqueDocuments.length}개 · 대기 {states.filter((item) => !item).length}개
            {" · 확인 중 " + states.filter((item) => item?.status === "running").length + "개"}
            {" · 후보 검토 " + states.filter((item) => item?.status === "needs_review").length + "개"}
            {" · 항목 연결 " + states.filter((item) => item?.status === "needs_mapping").length + "개"}
            {" · 실패 " + states.filter((item) => item?.status === "failed").length + "개"}
          </p>
          <details className="border-t border-slate-200 pt-2 dark:border-slate-700">
            <summary className="cursor-pointer py-1 text-xs font-semibold text-slate-600 dark:text-slate-300">문서별 추출 상태 보기</summary>
            <div className="mt-2">{files}</div>
          </details>
        </>
      ) : files}
    </section>
  );
}
