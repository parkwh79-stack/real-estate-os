"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, FileQuestion, Loader2, PlayCircle, Sparkles, UploadCloud } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { backendErrorMessageForResponse } from "../../lib/backend-error";

type Classified = {
  filename: string;
  documentType: string;
  label: string;
  confidence: number;
  confidenceBasis?: string;
  needsReview: boolean;
  archiveStored?: boolean;
  reasons?: string[];
};

type IntakeResult = { files: Classified[] };
type RowStatus = "idle" | "processing" | "done" | "skipped" | "error";
type Row = { id: string; file: File; c: Classified; type: string; status: RowStatus; note?: string };

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
  const enrichRes = await fetch(`${apiBase}/projects/${projectId}/land-ledger/enrich`, {
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
  return (await enrichRes.json().catch(() => body)) as Record<string, unknown>;
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
}: {
  projectId: string;
  onProcessed?: () => void;
  initialFiles?: File[];
  onClassified?: (files: Classified[], result: IntakeResult) => void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const uploadFiles = useCallback(
    async (arr: File[]) => {
      if (!arr.length) return;
      setLoading(true);
      setError(null);
      try {
        const fd = new FormData();
        arr.forEach((file) => fd.append("files", file));
        const res = await fetch(`${apiBase}/projects/${projectId}/intake/classify`, {
          method: "POST",
          headers: authHeaders("web"),
          body: fd,
        });
        if (!res.ok) {
          throw new Error(
            await backendErrorMessageForResponse(res, { action: "자료 업로드 및 문서 분류" }),
          );
        }
        const data = (await res.json()) as IntakeResult;
        onClassified?.(data.files, data);
        const newRows: Row[] = data.files.map((c, index) => ({
          id: `${Date.now()}-${index}`,
          file: arr[index] as File,
          c,
          type: c.documentType,
          status: "idle",
        }));
        setRows((prev) => [...newRows, ...prev]);
      } catch (err) {
        setError(err instanceof Error ? err.message : "업로드 또는 분류 중 오류가 발생했습니다.");
      } finally {
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
        if (route.kind === "file") {
          const fd = new FormData();
          fd.append("file", row.file);
          res = await fetch(`${apiBase}/projects/${projectId}/${route.path}`, {
            method: "POST",
            headers: authHeaders("web"),
            body: fd,
          });
        } else {
          const text = await row.file.text();
          const parsed = JSON.parse(text);
          const payload = Array.isArray(parsed) ? { records: parsed } : parsed;
          res = await fetch(`${apiBase}/projects/${projectId}/${route.path}`, {
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
        let body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        if (row.type === "feasibility_workbook" && body.feasibilityStudyV2Draft) {
          const candidateRes = await fetch(`${apiBase}/projects/${projectId}/feasibility-workbook/import-candidate`, {
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
          body = { ...body, candidateImport: await candidateRes.json().catch(() => ({})) };
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
    setProcessing(false);
    onProcessed?.();
  }, [rows, projectId, onProcessed]);

  const routable = rows.filter((row) => ROUTES[row.type] && row.status !== "done").length;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-white">
          <Sparkles className="h-5 w-5" />
        </div>
        <div>
          <h2 className="font-black text-slate-950 dark:text-white">스마트 업로드 (AI 자동 분류)</h2>
          <p className="text-xs font-semibold text-slate-500">
            자료를 종류 구분 없이 올리면 Parser Agent가 자동 분류하고, 분류된 경로에 맞춰 각 Agent에 전달합니다.
          </p>
        </div>
      </div>

      <div
        role="button"
        tabIndex={0}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
        }}
        onDragOver={(event) => {
          event.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragOver(false);
          if (event.dataTransfer.files) void upload(event.dataTransfer.files);
        }}
        className={`mt-4 flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
          dragOver
            ? "border-indigo-400 bg-indigo-50 dark:border-indigo-500 dark:bg-indigo-950/30"
            : "border-slate-300 hover:border-indigo-300 dark:border-slate-700"
        }`}
      >
        {loading ? <Loader2 className="h-8 w-8 animate-spin text-indigo-600" /> : <UploadCloud className="h-8 w-8 text-slate-400" />}
        <p className="text-sm font-bold text-slate-700 dark:text-slate-200">파일을 끌어다 놓거나 클릭해서 선택하세요. 여러 개도 가능합니다.</p>
        <p className="text-xs text-slate-400">PDF · XLS/XLSX · DOCX · JSON · CSV</p>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(event) => {
            if (event.target.files) void upload(event.target.files);
            event.target.value = "";
          }}
        />
      </div>

      {error ? <p className="mt-3 text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      {rows.length ? (
        <div className="mt-4">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-xs font-bold text-slate-500">
              AI 분류 결과 {rows.length}건 · 검토 필요 {rows.filter((row) => row.c.needsReview).length}건
            </p>
            <button
              type="button"
              onClick={() => void processAll()}
              disabled={processing || routable === 0}
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
