"use client";

import { useCallback, useRef, useState } from "react";
import { CheckCircle2, FileQuestion, Loader2, PlayCircle, Sparkles, UploadCloud } from "lucide-react";
import { authHeaders } from "../../lib/auth-client";

const apiBase = "http://127.0.0.1:8000";

type Classified = {
  filename: string;
  documentType: string;
  label: string;
  confidence: number;
  needsReview: boolean;
  archiveStored?: boolean;
};
type IntakeResult = { files: Classified[] };

type RowStatus = "idle" | "processing" | "done" | "skipped" | "error";
type Row = { id: string; file: File; c: Classified; type: string; status: RowStatus; note?: string };

const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "business_plan", label: "사업계획서 / IR" },
  { value: "basic_architectural_plan", label: "기본설계 도서" },
  { value: "land_ledger", label: "토지대장 / 토지조서" },
  { value: "feasibility_workbook", label: "사업수지 / 현금흐름 워크북" },
  { value: "risk_report_workbook", label: "리스크 보고서 워크북" },
  { value: "land_evidence", label: "토지 증빙" },
  { value: "market_evidence", label: "시장·실거래 자료" },
  { value: "unknown", label: "미분류" },
];

// documentType → existing parser endpoint. "skip" = archived only.
const ROUTES: Record<string, { path: string; kind: "file" | "json" }> = {
  business_plan: { path: "upload", kind: "file" },
  basic_architectural_plan: { path: "upload", kind: "file" },
  land_ledger: { path: "land-ledger/analyze", kind: "file" },
  feasibility_workbook: { path: "feasibility-workbook/validate", kind: "file" },
  risk_report_workbook: { path: "risk-report-workbook/validate", kind: "file" },
  land_evidence: { path: "land-evidence", kind: "json" },
};

function summarizeResult(type: string, body: Record<string, unknown>): string | undefined {
  try {
    if (type === "land_ledger") {
      const n = (((body.landLedger as Record<string, unknown>)?.summary as Record<string, unknown>)?.parcelCount) as number | undefined;
      return typeof n === "number" ? `${n}필지` : "임포트 완료";
    }
    if (type === "feasibility_workbook" || type === "risk_report_workbook") {
      const status = ((body.validation as Record<string, unknown>)?.status) as string | undefined;
      return status ? `검증 ${status}` : "검증 완료";
    }
    if (type === "land_evidence") return "증빙 저장";
    if (type === "business_plan" || type === "basic_architectural_plan") {
      return body.status === "host_llm_required" ? "LLM 추출 필요" : "파싱 완료";
    }
  } catch {
    /* best-effort */
  }
  return "완료";
}

function confidenceTone(c: number): string {
  if (c >= 0.75) return "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200";
  if (c >= 0.55) return "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200";
  return "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200";
}
const STATUS_LABEL: Record<RowStatus, string> = {
  idle: "보관됨",
  processing: "처리중…",
  done: "처리 완료",
  skipped: "보관만",
  error: "실패",
};
const STATUS_TONE: Record<RowStatus, string> = {
  idle: "text-slate-500",
  processing: "text-sky-600",
  done: "text-emerald-600 dark:text-emerald-300",
  skipped: "text-slate-400",
  error: "text-rose-600 dark:text-rose-300",
};

/** 스마트 업로드 — 한 번에 올리면 파서 에이전트가 자동 분류하고, "분류대로 처리"로 각 파서에 자동 전송. */
export function SmartIntake({ projectId, onProcessed }: { projectId: string; onProcessed?: () => void }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const upload = useCallback(
    async (fileList: FileList) => {
      if (!fileList.length) return;
      setLoading(true);
      setError(null);
      const arr = Array.from(fileList);
      try {
        const fd = new FormData();
        arr.forEach((f) => fd.append("files", f));
        const res = await fetch(`${apiBase}/projects/${projectId}/intake/classify`, {
          method: "POST",
          headers: authHeaders("web"),
          body: fd,
        });
        if (!res.ok) throw new Error(`분류 실패 (${res.status})`);
        const data = (await res.json()) as IntakeResult;
        const newRows: Row[] = data.files.map((c, i) => ({
          id: `${Date.now()}-${i}`,
          file: arr[i] as File,
          c,
          type: c.documentType,
          status: "idle" as const,
        }));
        setRows((prev) => [...newRows, ...prev]);
      } catch (err) {
        setError(err instanceof Error ? err.message : "업로드·분류 중 오류가 발생했습니다.");
      } finally {
        setLoading(false);
      }
    },
    [projectId],
  );

  const processAll = useCallback(async () => {
    setProcessing(true);
    setError(null);
    for (const row of rows) {
      if (row.status === "done") continue;
      const route = ROUTES[row.type];
      if (!route) {
        setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, status: "skipped" } : r)));
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
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        const summary = summarizeResult(row.type, body);
        setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, status: "done", note: summary } : r)));
      } catch (err) {
        const note = err instanceof Error ? err.message : "오류";
        setRows((prev) => prev.map((r) => (r.id === row.id ? { ...r, status: "error", note } : r)));
      }
    }
    setProcessing(false);
    onProcessed?.();
  }, [rows, projectId, onProcessed]);

  const routable = rows.filter((r) => ROUTES[r.type] && r.status !== "done").length;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-white">
          <Sparkles className="h-5 w-5" />
        </div>
        <div>
          <h2 className="font-black text-slate-950 dark:text-white">스마트 업로드 (AI 자동 분류)</h2>
          <p className="text-xs font-semibold text-slate-500">
            가진 자료를 종류 구분 없이 한 번에 올리세요. 파서 에이전트가 자동 분류·보관하고, “분류대로 처리”로 각 파서에 전송합니다.
          </p>
        </div>
      </div>

      <div
        role="button"
        tabIndex={0}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer.files) void upload(e.dataTransfer.files);
        }}
        className={`mt-4 flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
          dragOver
            ? "border-indigo-400 bg-indigo-50 dark:border-indigo-500 dark:bg-indigo-950/30"
            : "border-slate-300 hover:border-indigo-300 dark:border-slate-700"
        }`}
      >
        {loading ? <Loader2 className="h-8 w-8 animate-spin text-indigo-600" /> : <UploadCloud className="h-8 w-8 text-slate-400" />}
        <p className="text-sm font-bold text-slate-700 dark:text-slate-200">파일을 끌어다 놓거나 클릭해서 선택 (여러 개 가능)</p>
        <p className="text-xs text-slate-400">PDF · XLS/XLSX · DOCX · JSON · CSV</p>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) void upload(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {error ? <p className="mt-3 text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      {rows.length ? (
        <div className="mt-4">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-xs font-bold text-slate-500">
              AI 분류 결과 {rows.length}건 · 검토 필요 {rows.filter((r) => r.c.needsReview).length}건
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
            <table className="w-full min-w-[680px] text-sm">
              <thead className="bg-slate-50 text-left text-xs font-bold text-slate-500 dark:bg-slate-950/40">
                <tr>
                  <th className="px-3 py-2">파일</th>
                  <th className="px-3 py-2">AI 분류</th>
                  <th className="px-3 py-2">신뢰도</th>
                  <th className="px-3 py-2">유형(수정 가능)</th>
                  <th className="px-3 py-2">상태</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {rows.map((r) => (
                  <tr key={r.id} className="hover:bg-slate-50 dark:hover:bg-slate-950/40">
                    <td className="px-3 py-2 font-semibold text-slate-900 dark:text-white">
                      <span className="flex items-center gap-1.5">
                        {r.c.needsReview ? (
                          <FileQuestion className="h-4 w-4 shrink-0 text-amber-500" />
                        ) : (
                          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
                        )}
                        <span className="truncate">{r.c.filename}</span>
                      </span>
                    </td>
                    <td className="px-3 py-2 text-slate-700 dark:text-slate-200">{r.c.label}</td>
                    <td className="px-3 py-2">
                      <span className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${confidenceTone(r.c.confidence)}`}>
                        {Math.round(r.c.confidence * 100)}%
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <select
                        value={r.type}
                        onChange={(e) => setRows((prev) => prev.map((x) => (x.id === r.id ? { ...x, type: e.target.value } : x)))}
                        className="rounded-md border border-slate-300 px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-900"
                      >
                        {TYPE_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className={`px-3 py-2 text-xs font-semibold ${STATUS_TONE[r.status]}`}>
                      {STATUS_LABEL[r.status]}
                      {r.note ? ` (${r.note})` : ""}
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
