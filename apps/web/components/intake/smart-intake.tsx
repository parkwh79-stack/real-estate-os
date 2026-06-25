"use client";

import { useCallback, useRef, useState } from "react";
import { CheckCircle2, FileQuestion, Loader2, Sparkles, UploadCloud } from "lucide-react";
import { authHeaders } from "../../lib/auth-client";

const apiBase = "http://127.0.0.1:8000";

type Classified = {
  filename: string;
  documentType: string;
  label: string;
  confidence: number;
  needsReview: boolean;
  reasons?: string[];
  archiveStored?: boolean;
};
type IntakeResult = {
  count: number;
  byType?: Record<string, number>;
  needsReviewCount?: number;
  files: Classified[];
};

// Korean type options for manual override (mirrors backend DOC_TYPES).
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

function confidenceTone(c: number): string {
  if (c >= 0.75) return "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200";
  if (c >= 0.55) return "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200";
  return "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200";
}

/** 스마트 업로드 — 여러 자료를 한 번에 올리면 파서 에이전트가 문서 유형을 자동 분류·보관. */
export function SmartIntake({ projectId }: { projectId: string }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [files, setFiles] = useState<Classified[]>([]);
  const [overrides, setOverrides] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const upload = useCallback(
    async (fileList: FileList) => {
      if (!fileList.length) return;
      setLoading(true);
      setError(null);
      try {
        const fd = new FormData();
        Array.from(fileList).forEach((f) => fd.append("files", f));
        const res = await fetch(`${apiBase}/projects/${projectId}/intake/classify`, {
          method: "POST",
          headers: authHeaders("web"),
          body: fd,
        });
        if (!res.ok) throw new Error(`분류 실패 (${res.status})`);
        const data = (await res.json()) as IntakeResult;
        setFiles((prev) => [...data.files, ...prev]);
      } catch (err) {
        setError(err instanceof Error ? err.message : "업로드·분류 중 오류가 발생했습니다.");
      } finally {
        setLoading(false);
      }
    },
    [projectId],
  );

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-white">
          <Sparkles className="h-5 w-5" />
        </div>
        <div>
          <h2 className="font-black text-slate-950 dark:text-white">스마트 업로드 (AI 자동 분류)</h2>
          <p className="text-xs font-semibold text-slate-500">
            가진 자료를 종류 구분 없이 한 번에 올리세요. 파서 에이전트가 사업계획서·토지대장·사업수지·토지증빙 등으로 자동 분류하고 원본을 보관합니다.
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
        <p className="text-sm font-bold text-slate-700 dark:text-slate-200">
          파일을 끌어다 놓거나 클릭해서 선택 (여러 개 가능)
        </p>
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

      {files.length ? (
        <div className="mt-4">
          <p className="mb-2 text-xs font-bold text-slate-500">
            AI 분류 결과 {files.length}건 · 검토 필요 {files.filter((f) => f.needsReview).length}건
            <span className="ml-2 font-normal text-slate-400">분류가 틀리면 유형을 직접 바꾸세요.</span>
          </p>
          <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="bg-slate-50 text-left text-xs font-bold text-slate-500 dark:bg-slate-950/40">
                <tr>
                  <th className="px-3 py-2">파일</th>
                  <th className="px-3 py-2">AI 분류</th>
                  <th className="px-3 py-2">신뢰도</th>
                  <th className="px-3 py-2">유형(수정 가능)</th>
                  <th className="px-3 py-2">보관</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {files.map((f, i) => (
                  <tr key={`${f.filename}-${i}`} className="hover:bg-slate-50 dark:hover:bg-slate-950/40">
                    <td className="px-3 py-2 font-semibold text-slate-900 dark:text-white">
                      <span className="flex items-center gap-1.5">
                        {f.needsReview ? (
                          <FileQuestion className="h-4 w-4 shrink-0 text-amber-500" />
                        ) : (
                          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
                        )}
                        <span className="truncate">{f.filename}</span>
                      </span>
                    </td>
                    <td className="px-3 py-2 text-slate-700 dark:text-slate-200">{f.label}</td>
                    <td className="px-3 py-2">
                      <span className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${confidenceTone(f.confidence)}`}>
                        {Math.round(f.confidence * 100)}%
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <select
                        value={overrides[i] ?? f.documentType}
                        onChange={(e) => setOverrides((prev) => ({ ...prev, [i]: e.target.value }))}
                        className="rounded-md border border-slate-300 px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-900"
                      >
                        {TYPE_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-500">{f.archiveStored ? "보관됨" : "—"}</td>
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
