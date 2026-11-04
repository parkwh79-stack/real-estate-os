"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, FileQuestion, Loader2, PlayCircle, UploadCloud } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { backendErrorMessageForResponse } from "../../lib/backend-error";
import { hasArchivedFeasibilityReviewDraft, intakeProcessingFailure } from "../../lib/intake-processing-status";
import { classifyIntakeFiles, processTransferredIntakeFile, type IntakeClassifiedFile } from "../../lib/intake-upload-client";

type Classified = IntakeClassifiedFile;

type IntakeResult = { files: Classified[] };
type RowStatus = "idle" | "processing" | "done" | "skipped" | "error";
type Row = { id: string; file: File; c: Classified; type: string; status: RowStatus; note?: string };

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
  { value: "market_evidence", label: "시장·거래 증빙" },
  { value: "unknown", label: "미분류" },
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

function isArchivedWithoutProcessingRoute(type: string, classified: Classified) {
  return classified.archiveStored === true && !ROUTES[type];
}

function isTerminalRow(row: Row) {
  return row.status === "done" || (row.status === "skipped" && isArchivedWithoutProcessingRoute(row.type, row.c));
}

const STATUS_LABEL: Record<RowStatus, string> = {
  idle: "보류",
  processing: "처리 중",
  done: "처리 완료",
  skipped: "원본 저장 · 자동 처리 경로 없음",
  error: "실패",
};

const STATUS_TONE: Record<RowStatus, string> = {
  idle: "text-slate-500",
  processing: "text-sky-600",
  done: "text-emerald-600 dark:text-emerald-300",
  skipped: "text-slate-400",
  error: "text-rose-600 dark:text-rose-300",
};

async function readIntakeResponse(response: Response): Promise<Record<string, unknown>> {
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
  const failure = intakeProcessingFailure(body);
  if (failure) throw new Error(failure);
  return body;
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
  onProcessed,
  initialFiles,
  onClassified,
  onStatusChange,
  disabled = false,
}: {
  projectId: string;
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
        if (!Array.isArray(data.files) || data.files.length !== arr.length) {
          throw new Error("선택한 파일 수와 분류 결과가 일치하지 않습니다. 파일을 유지했으니 다시 시도해 주세요.");
        }
        onClassified?.(data.files, data);
        const newRows: Row[] = data.files.map((c, index) => ({
          id: `${Date.now()}-${index}`,
          file: arr[index] as File,
          c,
          type: c.documentType,
          status: isArchivedWithoutProcessingRoute(c.documentType, c) ? "skipped" : "idle",
        }));
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
    setError(null);
    let allProcessed = rows.length > 0 && failedFiles.length === 0;
    for (const row of rows) {
      if (isTerminalRow(row)) continue;
      const route = ROUTES[row.type];
      if (!route) {
        if (isArchivedWithoutProcessingRoute(row.type, row.c)) {
          setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, status: "skipped", note: undefined } : r)));
        } else {
          allProcessed = false;
          setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, status: "error", note: "원본 저장을 확인하지 못했고 자동 처리 경로가 없습니다. 문서 유형과 원본 저장을 확인해 주세요." } : r)));
        }
        continue;
      }
      setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, status: "processing" } : r)));
      try {
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
        let body = await readIntakeResponse(res);
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
    else setError("처리하지 못한 자료가 남아 있습니다. 파일별 안내와 문서 유형을 확인한 뒤 다시 시도해 주세요. 완료된 자료는 다시 처리하지 않습니다.");
  }, [rows, projectId, onProcessed, disabled, failedFiles.length]);

  const routable = rows.filter((row) => ROUTES[row.type] && row.status !== "done").length;

  return (
    <section className="space-y-4" aria-label="자료 분류와 처리">
      <div>
        <h3 className="text-base font-semibold text-re-text">자료 분류·처리</h3>
        <p className="mt-1 text-sm text-re-secondary">문서 유형을 확인하거나 수정한 뒤 자료를 처리하세요.</p>
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
              AI 분류 결과 {rows.length}건 · 검토 필요 {rows.filter((row) => row.c.needsReview).length}건
            </p>
            <button
              type="button"
              onClick={() => void processAll()}
              disabled={busy || routable === 0}
              className="inline-flex min-h-11 items-center gap-2 rounded-re-control bg-re-action px-3 text-sm font-semibold text-re-on-action transition-colors hover:bg-re-action-hover disabled:opacity-40"
            >
              {processing ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <PlayCircle className="h-4 w-4" aria-hidden="true" />}
              분류대로 처리 ({routable})
            </button>
          </div>
          <ul className="divide-y divide-re-border rounded-re-panel border border-re-border">
            {rows.map((row) => (
              <li key={row.id} className="min-w-0 space-y-2 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="flex min-w-0 items-start gap-2 text-sm font-semibold text-re-text">
                    {row.c.needsReview ? (
                      <FileQuestion className="h-4 w-4 shrink-0 text-re-warning" aria-hidden="true" />
                    ) : (
                      <CheckCircle2 className="h-4 w-4 shrink-0 text-re-success" aria-hidden="true" />
                    )}
                    <span className="break-all">{row.c.filename}</span>
                  </p>
                  <p className={`break-words text-xs font-semibold ${STATUS_TONE[row.status]}`}>
                    {STATUS_LABEL[row.status]}
                    {row.note ? ` (${row.note})` : ""}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                  <p className="text-xs text-re-secondary">AI 분류 {TYPE_LABEL[row.c.documentType] ?? row.c.label}</p>
                  <label className="flex max-w-full flex-wrap items-center gap-2 text-xs font-semibold text-re-secondary">
                    문서 유형
                    <select
                      aria-label={`${row.c.filename} 문서 유형`}
                      value={row.type}
                      disabled={busy}
                      onChange={(event) => {
                        const type = event.target.value;
                        setError(null);
                        setRows((prev) => prev.map((item) => item.id === row.id ? {
                          ...item, type, status: isArchivedWithoutProcessingRoute(type, item.c) ? "skipped" : "idle", note: undefined,
                        } : item));
                      }}
                      className="min-h-11 max-w-full rounded-re-control border border-re-border bg-re-surface px-2 text-sm text-re-text disabled:opacity-50"
                    >
                      {TYPE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                </div>
                <details className="text-xs text-re-secondary">
                  <summary className="cursor-pointer py-1 font-semibold hover:text-re-text">분류 근거 · {Math.round(row.c.confidence * 100)}%</summary>
                  <p className="mt-1 break-words">{confidenceBasis(row.c)}</p>
                </details>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
