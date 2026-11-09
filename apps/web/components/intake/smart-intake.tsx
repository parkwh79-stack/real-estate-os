"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, PlayCircle, UploadCloud } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { backendErrorMessageForResponse } from "../../lib/backend-error";
import { hasArchivedFeasibilityReviewDraft, hasArchivedWorkbookSourceReview, intakeProcessingFailure, SOURCE_REVIEW_PROFILE_ID, SOURCE_REVIEW_PROFILE_VERSION, SOURCE_REVIEW_IDEMPOTENCY_PREFIX } from "../../lib/intake-processing-status";
import { classifyIntakeFiles, processTransferredIntakeFile, releaseIntakeTransfer, type IntakeClassifiedFile } from "../../lib/intake-upload-client";
import { projectWorkspaceHref } from "../../lib/project-navigation";

type Classified = IntakeClassifiedFile;

type IntakeResult = { files: Classified[] };
type RowStatus = "review" | "idle" | "processing" | "done" | "inspection" | "skipped" | "error";
type Row = { id: string; file: File; c: Classified; options: string[]; reviewMode: boolean; type: string; status: RowStatus; note?: string; reviewOnlyReason?: string };

export interface IntakeWorkStatus {
  busy: boolean;
  pending: boolean;
}

const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "business_plan", label: "사업계획서 / IM" },
  { value: "basic_architectural_plan", label: "건축 기본계획" },
  { value: "land_ledger", label: "토지대장 / 토지조서" },
  { value: "feasibility_workbook", label: "사업수지 / Cash Flow Excel" },
  { value: "risk_report_workbook", label: "리스크 보고서 Excel" },
  { value: "land_evidence", label: "토지 증빙" },
];

const TYPE_LABEL = Object.fromEntries(TYPE_OPTIONS.map((item) => [item.value, item.label])) as Record<string, string>;

const ROUTES: Record<string, { path: string; kind: "file" | "json" }> = {
  business_plan: { path: "upload", kind: "file" },
  basic_architectural_plan: { path: "upload", kind: "file" },
  land_ledger: { path: "land-ledger/analyze", kind: "file" },
  feasibility_workbook: { path: "feasibility-workbook/validate", kind: "file" },
  risk_report_workbook: { path: "risk-report-workbook/validate", kind: "file" },
  land_evidence: { path: "land-evidence", kind: "json" },
};

const NARRATIVE_TYPES = ["business_plan", "basic_architectural_plan", "investment_committee_memo"];
const WORKBOOK_TYPES = ["land_ledger", "feasibility_workbook", "risk_report_workbook"];
const LEGACY_TYPES_BY_EXTENSION: Record<string, string[]> = {
  pdf: ["business_plan", "basic_architectural_plan"],
  xls: ["land_ledger"],
  xlsx: WORKBOOK_TYPES,
  xlsm: WORKBOOK_TYPES,
  json: ["land_evidence"],
};
const ARCHIVE_ONLY = "archive_only";
const DOCUMENT_REVIEW = "document_review";

function archivedReviewId(classified: Classified): string | null {
  return classified.archiveStored === true && typeof classified.archiveId === "string" && /^[a-f0-9]{64}$/i.test(classified.archiveId)
    ? classified.archiveId.toLowerCase() : null;
}

function initialRow(id: string, file: File, c: Classified): Row {
  const extension = /\.([^.]+)$/.exec(file.name.toLowerCase())?.[1] ?? "";
  const archivedId = archivedReviewId(c);
  const reviewMode = c.processingMode === DOCUMENT_REVIEW ||
    (c.processingTypes === undefined && (extension === "pdf" || extension === "docx") && archivedId !== null);
  const supported = c.processingTypes ?? (reviewMode ? NARRATIVE_TYPES : LEGACY_TYPES_BY_EXTENSION[extension]) ?? [];
  const options = reviewMode
    ? [DOCUMENT_REVIEW]
    : TYPE_OPTIONS.filter(({ value }) => supported.includes(value) && Object.hasOwn(ROUTES, value)).map(({ value }) => value);
  const row = { id, file, c, options, reviewMode };
  if (c.formatIssue === "extension_mismatch") {
    return { ...row, options: [], type: c.archiveStored === true ? ARCHIVE_ONLY : "", status: c.archiveStored === true ? "skipped" : "error",
      note: "파일 확장자와 실제 형식을 확인한 뒤 올바른 형식으로 다시 올려 주세요." };
  }
  if (reviewMode && !archivedId) {
    return { ...row, options: [], type: "", status: "error", note: "문서 검토에 필요한 원본 저장을 확인하지 못했습니다. 파일을 다시 추가해 주세요." };
  }
  if (!options.length || c.documentType === "market_evidence") {
    return c.archiveStored === true
      ? { ...row, type: ARCHIVE_ONLY, status: "skipped" }
      : { ...row, type: "", status: "error", note: "원본 저장을 확인하지 못했습니다. 지원되는 파일인지 확인하고 다시 추가해 주세요." };
  }
  const recommendationSupported = (reviewMode ? supported : options).includes(c.documentType);
  return c.needsReview || c.confidence < 0.55 || !recommendationSupported
    ? { ...row, type: "", status: "review" }
    : { ...row, type: reviewMode ? DOCUMENT_REVIEW : c.documentType, status: "idle" };
}

function isTerminalRow(row: Row) {
  return row.status === "done" || row.status === "skipped" || row.status === "inspection";
}

function canProcessRow(row: Row) {
  return !isTerminalRow(row) && row.options.includes(row.type);
}

const STATUS_LABEL: Record<RowStatus, string> = {
  review: "유형 확인 필요",
  idle: "처리 대기",
  processing: "처리 중",
  done: "처리 완료",
  inspection: "원본 저장 · 검토 필요",
  skipped: "원본 저장 · 자동 처리 경로 없음",
  error: "실패",
};

const STATUS_TONE: Record<RowStatus, string> = {
  review: "text-re-warning",
  idle: "text-slate-500",
  processing: "text-sky-600",
  done: "text-emerald-600 dark:text-emerald-300",
  inspection: "text-re-warning",
  skipped: "text-slate-400",
  error: "text-rose-600 dark:text-rose-300",
};

async function readIntakeResponse(response: Response, documentType?: string): Promise<Record<string, unknown>> {
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    throw new Error("자료 처리 응답을 읽지 못했습니다. 파일을 유지했으니 다시 시도해 주세요.");
  }
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).length === 0) {
    throw new Error("자료 처리 결과를 확인하지 못했습니다. 파일을 유지했으니 다시 시도해 주세요.");
  }
  const body = value as Record<string, unknown>;
  const failure = intakeProcessingFailure(body, documentType);
  if (failure) throw new Error(failure);
  return body;
}

async function prepareDocumentReview(projectId: string, row: Row): Promise<Pick<Row, "status" | "note">> {
  const archiveId = archivedReviewId(row.c);
  if (!archiveId) throw new Error("문서 검토에 필요한 원본 저장을 확인하지 못했습니다.");
  const response = await fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}/parsing-v2/runs`, {
    method: "POST",
    headers: { ...authHeaders("web"), "Content-Type": "application/json" },
    body: JSON.stringify({
      archiveId,
      ...(/\.(pdf|xlsx|xlsm)$/i.test(row.c.filename) ? {
        profileId: SOURCE_REVIEW_PROFILE_ID, profileVersion: SOURCE_REVIEW_PROFILE_VERSION,
        idempotencyKey: SOURCE_REVIEW_IDEMPOTENCY_PREFIX + archiveId,
      } : {}),
    }),
  });
  if (!response.ok) throw new Error(await backendErrorMessageForResponse(response, { action: "문서 검토 준비" }));
  const result = await readIntakeResponse(response);
  const run = result.parsingRun as Record<string, unknown> | undefined;
  if (result.candidateOnly !== true || result.authoritative !== false || typeof run?.parsingRunId !== "string") {
    throw new Error("문서 검토 준비 결과를 확인하지 못했습니다. 문서 검토에서 원본과 처리 상태를 확인해 주세요.");
  }
  if (run.executionStatus === "completed") {
    const collection = (result.candidateCollection ?? (result.orchestration as Record<string, unknown> | undefined)?.candidateCollection
      ?? (run.orchestrationArtifact as Record<string, unknown> | undefined)?.candidateCollection) as Record<string, unknown> | undefined;
    if (collection?.candidateCount === 0) return { status: "inspection", note: row.reviewOnlyReason
      ? `${row.reviewOnlyReason} 원본 읽음 · 추출 항목 연결 필요` : "원본 읽음 · 추출 항목 연결 필요. 문서 검토에서 페이지·셀을 확인해 주세요." };
    return row.reviewOnlyReason ? { status: "inspection", note: row.reviewOnlyReason }
      : { status: "done", note: "문서 검토 준비" };
  }
  if (run.executionStatus === "completed_with_warnings") {
    return { status: "inspection", note: row.reviewOnlyReason
      ? `${row.reviewOnlyReason} 자동으로 매핑하지 못한 항목은 문서 검토에서 확인해 주세요.`
      : "일부 내용은 문서 검토에서 확인해 주세요." };
  }
  if (run.executionStatus === "failed" || run.executionStatus === "cancelled") {
    return { status: "inspection", note: "문서 읽기를 완료하지 못했습니다. 문서 검토에서 원본과 페이지를 확인해 주세요." };
  }
  throw new Error("문서 검토 상태를 확인하지 못했습니다. 문서 검토에서 진행 상태를 확인해 주세요.");
}

function summarizeResult(type: string, body: Record<string, unknown>): string | undefined {
  try {
    if (type === "land_ledger") {
      const n = (((body.landLedger as Record<string, unknown>)?.summary as Record<string, unknown>)?.parcelCount) as number | undefined;
      if (body.landEvidenceEnrichment === "deferred") return `${typeof n === "number" ? `${n}필지 저장` : "필지 저장"} · PNU 증빙 보강 대기`;
      return typeof n === "number" ? `${n}필지` : "토지조서 분석 완료";
    }
    if (type === "feasibility_workbook" || type === "risk_report_workbook") {
      const status = ((body.validation as Record<string, unknown>)?.status) as string | undefined;
      if (type === "feasibility_workbook" && status === "failed" && (body.candidateImport as Record<string, unknown>)?.status === "candidate") {
        return "검토 후보 저장 · 수식 오류 확인 필요";
      }
      return status ? `검증 ${status}` : "검증 완료";
    }
    if (type === "land_evidence") return "증빙 저장";
    if (type === "business_plan" || type === "basic_architectural_plan") {
      return body.status === "host_llm_required" ? "LLM 호출 필요" : "파싱 완료";
    }
  } catch {
    /* best-effort summary */
  }
  return "처리 완료";
}

async function enrichLandLedgerAfterAnalyze(projectId: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (!body.landLedger || body.landEvidenceEnrichment === "deferred") return body;
  const enrichRes = await fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}/land-ledger/enrich`, {
    method: "POST",
    headers: { ...authHeaders("web"), "Content-Type": "application/json" },
    body: JSON.stringify({
      landLedger: body.landLedger,
      persist: true,
      persistEvidence: true,
      options: {
        autoResolvePnuFromAddress: true,
        useLiveConnectors: true,
        mode: "smart_intake_auto_enrichment",
      },
    }),
  });
  if (!enrichRes.ok) {
    throw new Error(
      await backendErrorMessageForResponse(enrichRes, { action: "토지자료 PNU·증빙 보강" }),
    );
  }
  return readIntakeResponse(enrichRes);
}

function confidenceBasis(row: Classified) {
  return row.reasons?.[0] || row.confidenceBasis || "파일명, 확장자, 문서 제목, 표 구조, 키워드 신호 기준";
}

export function SmartIntake({
  projectId,
  track,
  onProcessed,
  initialFiles,
  onClassified,
  onStatusChange,
  disabled = false,
}: {
  projectId: string;
  track?: string | null;
  onProcessed?: () => void;
  initialFiles?: File[];
  onClassified?: (files: Classified[], result: IntakeResult) => void;
  onStatusChange?: (status: IntakeWorkStatus) => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const workInFlight = useRef(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [initialPending, setInitialPending] = useState(Boolean(initialFiles?.length));
  const [failedFiles, setFailedFiles] = useState<File[]>([]);
  const busy = loading || processing || disabled;
  const pending = initialPending || busy || failedFiles.length > 0 || rows.some((row) => !isTerminalRow(row));
  const canPickFiles = !busy && failedFiles.length === 0;

  useEffect(() => {
    onStatusChange?.({ busy, pending });
  }, [busy, pending, onStatusChange]);

  const uploadFiles = useCallback(
    async (arr: File[]) => {
      if (!arr.length || workInFlight.current || disabled) return;
      workInFlight.current = true;
      setLoading(true);
      setError(null);
      try {
        const data = await classifyIntakeFiles(projectId, arr, setUploadProgress);
        if (data.files.length !== arr.length || data.files.some((file, index) => file.filename !== arr[index]?.name)) {
          throw new Error("선택한 파일 수와 분류 결과가 일치하지 않습니다. 파일을 유지했으니 다시 시도해 주세요.");
        }
        onClassified?.(data.files, data);
        const batchId = Date.now();
        const newRows = data.files.map((c, index) => initialRow(`${batchId}-${index}`, arr[index] as File, c));
        setRows((prev) => [...newRows, ...prev]);
        setFailedFiles([]);
      } catch (err) {
        setFailedFiles(arr);
        setError(err instanceof Error ? err.message : "업로드 또는 분류 중 오류가 발생했습니다.");
      } finally {
        workInFlight.current = false;
        setUploadProgress("");
        setInitialPending(false);
        setLoading(false);
      }
    },
    [projectId, onClassified, disabled],
  );

  const upload = useCallback((fileList: FileList) => uploadFiles(Array.from(fileList)), [uploadFiles]);

  const autoStarted = useRef(false);
  useEffect(() => {
    if (autoStarted.current) return;
    if (initialFiles && initialFiles.length) {
      autoStarted.current = true;
      void uploadFiles(initialFiles);
    }
  }, [initialFiles, uploadFiles]);

  const processAll = useCallback(async () => {
    if (workInFlight.current || disabled) return;
    workInFlight.current = true;
    setProcessing(true);
    let allProcessed = rows.length > 0 && failedFiles.length === 0 && rows.every((row) => isTerminalRow(row) || canProcessRow(row));
    for (const row of rows) {
      if (!canProcessRow(row)) continue;
      setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, status: "processing", note: undefined } : r)));
      try {
        if (row.type === DOCUMENT_REVIEW) {
          const outcome = await prepareDocumentReview(projectId, row);
          setRows((prev) => prev.map((r) => r.id === row.id ? { ...r, ...outcome } : r));
          if (row.c.transferId) void releaseIntakeTransfer(projectId, row.file, row.c.transferId);
          continue;
        }
        const route = ROUTES[row.type];
        if (!route) throw new Error("선택한 유형의 처리 경로를 확인하지 못했습니다.");
        let res: Response;
        if (row.c.transferId) {
          res = await processTransferredIntakeFile(projectId, row.file, row.type);
        } else if (route.kind === "file") {
          const fd = new FormData();
          fd.append("file", row.file);
          res = await fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}/${route.path}`, {
            method: "POST",
            headers: authHeaders("web"),
            body: fd,
          });
        } else {
          const text = await row.file.text();
          const parsed = JSON.parse(text);
          const payload = Array.isArray(parsed) ? { records: parsed } : parsed;
          res = await fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}/${route.path}`, {
            method: "POST",
            headers: { ...authHeaders("web"), "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
        }
        if (!res.ok) {
          throw new Error(
            await backendErrorMessageForResponse(res, { action: `${TYPE_LABEL[row.type] ?? "자료"} 처리` }),
          );
        }
        let body = await readIntakeResponse(res, row.type);
        if (row.type === "feasibility_workbook" && hasArchivedWorkbookSourceReview(body)) {
          const review = body.sourceReview as Record<string, unknown>;
          if (body.project_id !== projectId || review.archiveId !== archivedReviewId(row.c)) {
            throw new Error("항목 매핑 검토 원본이 현재 프로젝트·파일과 일치하지 않습니다.");
          }
          const reviewRow: Row = { ...row, type: DOCUMENT_REVIEW, reviewMode: true, options: [DOCUMENT_REVIEW],
            reviewOnlyReason: "사업수지 양식 매핑 필요 · 원본값 검토 후보만 준비하며 수지·수식 반영은 하지 않습니다." };
          // A failed parser connection retries this same archive, without
          // repeating the upload, workbook validation or financial import.
          setRows((prev) => prev.map((r) => r.id === row.id ? { ...reviewRow, status: "processing" } : r));
          const outcome = await prepareDocumentReview(projectId, reviewRow);
          setRows((prev) => prev.map((r) => r.id === row.id ? { ...r, ...outcome } : r));
          if (row.c.transferId) void releaseIntakeTransfer(projectId, row.file, row.c.transferId);
          continue;
        }
        if (row.type === "feasibility_workbook" && hasArchivedFeasibilityReviewDraft(body) && body.project_id !== projectId) {
          throw new Error("사업수지 후보의 프로젝트가 현재 자료와 일치하지 않습니다.");
        }
        if (row.type === "feasibility_workbook" && body.feasibilityStudyV2Draft) {
          const candidateRes = await fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}/feasibility-workbook/import-candidate`, {
            method: "POST",
            headers: { ...authHeaders("web"), "Content-Type": "application/json" },
            body: JSON.stringify({
              feasibilityStudyV2Draft: body.feasibilityStudyV2Draft,
              validation: body.validation,
              sourceDocument: body.upload,
            }),
          });
          if (!candidateRes.ok) {
            throw new Error(
              await backendErrorMessageForResponse(candidateRes, { action: "사업수지 원장 반영" }),
            );
          }
          const candidateImport = await readIntakeResponse(candidateRes);
          if (hasArchivedFeasibilityReviewDraft(body)
            && (candidateImport.status !== "candidate" || candidateImport.projectId !== projectId || candidateImport.authoritative !== false)) {
            throw new Error("사업수지 검토 후보의 저장 결과를 확인하지 못했습니다. 원본과 처리 결과를 유지했으니 다시 확인해 주세요.");
          }
          body = { ...body, candidateImport };
        }
        if (row.type === "land_ledger") {
          body = await enrichLandLedgerAfterAnalyze(projectId, body);
        }
        setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, status: "done", note: summarizeResult(row.type, body) } : r)));
      } catch (err) {
        allProcessed = false;
        const note = err instanceof Error ? err.message : "오류";
        setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, status: "error", note } : r)));
      }
    }
    workInFlight.current = false;
    setProcessing(false);
    if (allProcessed) onProcessed?.();
  }, [rows, projectId, onProcessed, disabled, failedFiles.length]);

  function changeRowType(row: Row, type: string) {
    if (busy || (type !== ARCHIVE_ONLY && !row.options.includes(type))) return;
    if (type === ARCHIVE_ONLY && row.c.archiveStored !== true) return;
    setRows((prev) => prev.map((item) => item.id === row.id ? {
      ...item, type, status: type === ARCHIVE_ONLY ? "skipped" : "idle", note: undefined,
    } : item));
    if (type === ARCHIVE_ONLY && row.reviewMode && archivedReviewId(row.c) && row.c.transferId) {
      void releaseIntakeTransfer(projectId, row.file, row.c.transferId);
    }
  }

  const routable = rows.filter(canProcessRow).length;
  const needsReview = rows.filter((row) => row.status === "review").length;

  return (
    <section className="space-y-4" aria-label="자료 분류와 처리">
      <div>
        <h3 className="text-base font-semibold text-re-text">자료 분류·처리</h3>
        <p className="mt-1 text-sm text-re-secondary">분류된 자료를 한 번에 처리할 수 있습니다. 확인이 필요한 파일은 따로 표시합니다.</p>
      </div>

      <div
        role="button"
        tabIndex={0}
        aria-label="추가 자료 파일 선택"
        aria-disabled={!canPickFiles}
        onClick={() => canPickFiles && inputRef.current?.click()}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (canPickFiles) inputRef.current?.click();
          }
        }}
        onDragOver={(event) => {
          event.preventDefault();
          if (canPickFiles) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragOver(false);
          if (canPickFiles && event.dataTransfer.files) void upload(event.dataTransfer.files);
        }}
        className={`flex flex-col items-center justify-center gap-2 rounded-re-panel border border-dashed px-4 py-4 text-center transition-colors ${canPickFiles ? "cursor-pointer hover:border-re-action hover:bg-re-subtle" : "cursor-not-allowed"} ${
          dragOver
            ? "border-re-action bg-re-subtle"
            : "border-re-border"
        }`}
      >
        <p className={`inline-flex min-h-11 items-center gap-2 rounded-re-control px-3 text-sm font-semibold ${canPickFiles ? "bg-re-action text-re-on-action" : "text-re-secondary"}`}>
          {loading ? <Loader2 className="h-5 w-5 animate-spin text-re-action" aria-hidden="true" /> : <UploadCloud className="h-5 w-5" aria-hidden="true" />}
          {loading ? "자료를 분류하고 있습니다…" : failedFiles.length ? "분류하지 못한 파일 확인" : "자료 추가"}
        </p>
        {canPickFiles ? <p className="text-xs text-re-secondary">누르거나 파일을 끌어다 놓으세요.</p> : null}
        <p className="text-xs text-re-secondary">PDF · XLS/XLSX · DOCX · JSON · CSV · 파일당 최대 64MB</p>
        {loading && uploadProgress ? <p className="text-xs text-re-secondary" role="status">{uploadProgress}</p> : null}
        <input
          ref={inputRef}
          type="file"
          multiple
          disabled={!canPickFiles}
          className="hidden"
          onChange={(event) => {
            if (event.target.files) void upload(event.target.files);
            event.target.value = "";
          }}
        />
      </div>

      {error ? <p className="text-sm text-re-danger" role="alert">{error}</p> : null}
      {failedFiles.length ? (
        <div className="flex flex-wrap items-center gap-3 text-sm text-re-secondary">
          <span>분류 대기 파일 {failedFiles.length}개 보관 중</span>
          <button type="button" disabled={busy} onClick={() => void uploadFiles(failedFiles)} className="min-h-10 rounded-re-control border border-re-border px-3 text-re-action disabled:opacity-50">분류 다시 시도</button>
          <button type="button" disabled={busy} onClick={() => {
            if (window.confirm("분류하지 못한 파일의 선택을 취소할까요? 이미 저장된 자료는 유지됩니다.")) {
              setFailedFiles([]);
              setError(null);
            }
          }} className="min-h-10 rounded-re-control px-3 disabled:opacity-50">파일 선택 취소</button>
        </div>
      ) : null}

      {rows.length ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-semibold text-re-secondary">
              자료 {rows.length}건{needsReview > 0 ? ` · 유형 확인 필요 ${needsReview}건` : ""}
            </p>
            <button
              type="button"
              onClick={() => void processAll()}
              disabled={busy || routable === 0}
              className="inline-flex min-h-11 items-center gap-2 rounded-re-control bg-re-action px-3 text-sm font-semibold text-re-on-action transition-colors hover:bg-re-action-hover disabled:opacity-40"
            >
              {processing ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <PlayCircle className="h-4 w-4" aria-hidden="true" />}
              자료 처리 ({routable})
            </button>
          </div>
          <ul className="divide-y divide-re-border rounded-re-panel border border-re-border">
            {rows.map((row) => {
              const recommended = TYPE_LABEL[row.c.documentType] ?? row.c.label;
              const reviewMode = row.reviewMode;
              const mayEdit = row.options.length > 0 && row.status !== "done" && row.status !== "inspection";
              const mayArchiveOnly = row.c.archiveStored === true && row.type !== ARCHIVE_ONLY;
              const statusLabel = row.status === "skipped" && row.options.length ? "원본만 보관" : STATUS_LABEL[row.status];
              const reviewArchiveId = reviewMode && (row.status === "done" || row.status === "inspection") ? archivedReviewId(row.c) : null;
              const reviewHref = projectWorkspaceHref(projectId, "documents/review", track);
              return (
              <li key={row.id} className="min-w-0 space-y-2 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="min-w-0 break-all text-sm font-semibold text-re-text">{row.c.filename}</p>
                  <p className={`break-words text-xs font-semibold ${STATUS_TONE[row.status]}`}>
                    {statusLabel}
                  </p>
                </div>
                <div className="flex flex-wrap items-start gap-x-4 gap-y-1">
                  <p className="py-1 text-sm text-re-secondary">{row.status === "review" ? `추천 · ${recommended}` : TYPE_LABEL[row.type] ?? recommended}</p>
                  {mayEdit && reviewMode ? (
                    <div className="flex flex-wrap gap-x-3">
                      {row.type !== DOCUMENT_REVIEW ? (
                        <button type="button" disabled={busy} onClick={() => changeRowType(row, DOCUMENT_REVIEW)}
                          className="min-h-8 text-xs font-semibold text-re-action underline disabled:opacity-50">이 자료 검토</button>
                      ) : null}
                      {mayArchiveOnly && row.status === "review" ? (
                        <button type="button" disabled={busy} onClick={() => changeRowType(row, ARCHIVE_ONLY)}
                          className="min-h-8 text-xs text-re-secondary underline disabled:opacity-50">원본만 보관</button>
                      ) : null}
                    </div>
                  ) : null}
                  {mayEdit && !reviewMode ? (
                    <details className="max-w-full text-xs text-re-secondary">
                      <summary className="cursor-pointer py-1 font-semibold text-re-action">{row.status === "review" ? "유형 선택" : "유형 바꾸기"}</summary>
                      <select
                        aria-label={`${row.c.filename} 처리 유형`}
                        value={row.type}
                        disabled={busy}
                        onChange={(event) => changeRowType(row, event.target.value)}
                        className="min-h-11 max-w-full rounded-re-control border border-re-border bg-re-surface px-2 text-sm text-re-text disabled:opacity-50"
                      >
                        <option value="" disabled>처리 유형 선택</option>
                        {row.options.map((type) => <option key={type} value={type}>{TYPE_LABEL[type]}</option>)}
                        {row.c.archiveStored === true ? <option value={ARCHIVE_ONLY}>원본만 보관</option> : null}
                      </select>
                    </details>
                  ) : null}
                </div>
                {row.note ? <p role={row.status === "error" ? "alert" : undefined} className={`text-xs ${STATUS_TONE[row.status]}`}>{row.note}</p> : null}
                {row.status === "error" && row.c.archiveStored === true ? <p className="text-xs text-re-secondary">원본은 저장되어 있습니다.</p> : null}
                {reviewArchiveId ? (
                  <Link href={`${reviewHref}${reviewHref.includes("?") ? "&" : "?"}archiveId=${reviewArchiveId}`}
                    className="text-xs font-semibold text-re-action underline">문서 검토 열기</Link>
                ) : null}
                <details className="text-xs text-re-secondary">
                  <summary className="cursor-pointer py-1 font-semibold hover:text-re-text">분류 근거 · {Math.round(row.c.confidence * 100)}%</summary>
                  {row.type && !reviewMode && row.type !== row.c.documentType ? <p>자동 분류 제안 · {recommended}</p> : null}
                  {row.c.topics?.length ? <p>감지한 주제 · {row.c.topics.slice(0, 5).join(" · ")}</p> : null}
                  <p className="mt-1 break-words">{confidenceBasis(row.c)}</p>
                </details>
              </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
