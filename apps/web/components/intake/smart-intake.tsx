"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, FileQuestion, Loader2, PlayCircle, UploadCloud } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { backendErrorMessageForResponse } from "../../lib/backend-error";
import { intakeProcessingFailure } from "../../lib/intake-processing-status";
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

const STATUS_LABEL: Record<RowStatus, string> = {
  idle: "보류",
  processing: "처리 중",
  done: "처리 완료",
  skipped: "보강 필요",
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
      return typeof n === "number" ? `${n}필지` : "토지조서 분석 완료";
    }
    if (type === "feasibility_workbook" || type === "risk_report_workbook") {
      const status = ((body.validation as Record<string, unknown>)?.status) as string | undefined;
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
  if (!body.landLedger) return body;
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

function confidenceTone(confidence: number): string {
  if (confidence >= 0.75) return "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200";
  if (confidence >= 0.55) return "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200";
  return "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200";
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
}: {
  projectId: string;
  onProcessed?: () => void;
  initialFiles?: File[];
  onClassified?: (files: Classified[], result: IntakeResult) => void;
  onStatusChange?: (status: IntakeWorkStatus) => void;
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
  const busy = loading || processing;
  const pending = initialPending || busy || failedFiles.length > 0 || rows.some((row) => row.status !== "done");
  const canPickFiles = !busy && failedFiles.length === 0;

  useEffect(() => {
    onStatusChange?.({ busy, pending });
  }, [busy, pending, onStatusChange]);

  const uploadFiles = useCallback(
    async (arr: File[]) => {
      if (!arr.length || workInFlight.current) return;
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
          status: "idle",
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
    [projectId, onClassified],
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
    if (workInFlight.current) return;
    workInFlight.current = true;
    setProcessing(true);
    setError(null);
    for (const row of rows) {
      if (row.status === "done") continue;
      const route = ROUTES[row.type];
      if (!route) {
        setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, status: "skipped", note: "자동 처리 경로 없음" } : r)));
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
          body = { ...body, candidateImport: await readIntakeResponse(candidateRes) };
        }
        if (row.type === "land_ledger") {
          body = await enrichLandLedgerAfterAnalyze(projectId, body);
        }
        setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, status: "done", note: summarizeResult(row.type, body) } : r)));
      } catch (err) {
        const note = err instanceof Error ? err.message : "오류";
        setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, status: "error", note } : r)));
      }
    }
    workInFlight.current = false;
    setProcessing(false);
    onProcessed?.();
  }, [rows, projectId, onProcessed]);

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
        className={`flex flex-col items-center justify-center gap-2 rounded-re-panel border border-dashed px-5 py-5 text-center ${canPickFiles ? "cursor-pointer" : "cursor-not-allowed"} ${
          dragOver
            ? "border-re-action bg-re-subtle"
            : "border-re-border"
        }`}
      >
        {loading ? <Loader2 className="h-6 w-6 animate-spin text-re-action" aria-hidden="true" /> : <UploadCloud className="h-6 w-6 text-re-secondary" aria-hidden="true" />}
        <p className="text-sm text-re-text">{loading ? "자료를 분류하고 있습니다…" : failedFiles.length ? "분류하지 못한 파일을 먼저 확인해 주세요." : "추가 자료를 끌어다 놓거나 선택하세요."}</p>
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
        <div className="mt-4">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-xs font-bold text-slate-500">
              AI 분류 결과 {rows.length}건 · 검토 필요 {rows.filter((row) => row.c.needsReview).length}건
            </p>
            <button
              type="button"
              onClick={() => void processAll()}
              disabled={busy || routable === 0}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-indigo-600 px-3 text-xs font-bold text-white disabled:opacity-40"
            >
              {processing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
              분류대로 처리 ({routable})
            </button>
          </div>
          <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="bg-slate-50 text-left text-xs font-bold text-slate-500 dark:bg-slate-950/40">
                <tr>
                  <th className="px-3 py-2">파일</th>
                  <th className="px-3 py-2">AI 분류</th>
                  <th className="px-3 py-2">신뢰도</th>
                  <th className="px-3 py-2">유형 수정</th>
                  <th className="px-3 py-2">상태</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {rows.map((row) => (
                  <tr key={row.id} className="hover:bg-slate-50 dark:hover:bg-slate-950/40">
                    <td className="px-3 py-2 font-semibold text-slate-900 dark:text-white">
                      <span className="flex items-center gap-1.5">
                        {row.c.needsReview ? (
                          <FileQuestion className="h-4 w-4 shrink-0 text-amber-500" />
                        ) : (
                          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
                        )}
                        <span className="truncate">{row.c.filename}</span>
                      </span>
                    </td>
                    <td className="px-3 py-2 text-slate-700 dark:text-slate-200">{TYPE_LABEL[row.c.documentType] ?? row.c.label}</td>
                    <td className="px-3 py-2">
                      <span className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${confidenceTone(row.c.confidence)}`}>
                        {Math.round(row.c.confidence * 100)}%
                      </span>
                      <p className="mt-1 max-w-[220px] text-[11px] leading-4 text-slate-400">
                        {confidenceBasis(row.c).slice(0, 90)}
                      </p>
                    </td>
                    <td className="px-3 py-2">
                      <select
                        value={row.type}
                        disabled={busy}
                        onChange={(event) => setRows((prev) => prev.map((item) => (item.id === row.id ? { ...item, type: event.target.value } : item)))}
                        className="rounded-md border border-slate-300 px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-900"
                      >
                        {TYPE_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className={`px-3 py-2 text-xs font-semibold ${STATUS_TONE[row.status]}`}>
                      {STATUS_LABEL[row.status]}
                      {row.note ? ` (${row.note})` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}
