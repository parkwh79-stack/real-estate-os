"use client";

import { useCallback, useRef, useState } from "react";
import { CheckCircle2, FileSpreadsheet, Loader2, Sparkles } from "lucide-react";
import { authHeaders, apiBase } from "../../lib/auth-client";

const FIELDS: { key: string; label: string }[] = [
  { key: "total_revenue", label: "총매출" },
  { key: "total_cost", label: "총사업비" },
  { key: "land_cost", label: "토지비" },
  { key: "construction_cost", label: "공사비" },
  { key: "finance_cost", label: "금융비용" },
  { key: "other_cost", label: "기타사업비" },
  { key: "development_profit", label: "개발이익" },
  { key: "pf_amount", label: "PF 총액" },
];

interface FieldEvidence {
  value_krw: number | null;
  source?: string | null;
  unit?: string | null;
  raw?: number | string | null;
}
interface ExtractResponse {
  parsingSource?: string;
  fields?: Record<string, FieldEvidence>;
  note?: string;
  error?: string;
}

type Row = { key: string; label: string; eok: string; source: string; unit: string };

const toEok = (won: number | null | undefined): string =>
  typeof won === "number" ? String(Math.round((won / 1e8) * 10) / 10) : "";

/**
 * LLM-assisted feasibility workbook review: upload any 사업수지 workbook, see the
 * extracted figures with source-cell evidence, edit if needed, then apply to the
 * project's feasibility_input (reviewer-gated). Values are shown/edited in 억원.
 */
export function FeasibilityExtractReview({ projectId, onApplied }: { projectId: string; onApplied?: () => void }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);

  const extract = useCallback(
    async (file: File) => {
      setLoading(true);
      setError(null);
      setNotice(null);
      setRows([]);
      setFileName(file.name);
      try {
        const fd = new FormData();
        fd.append("file", file);
        const res = await fetch(`${apiBase}/projects/${projectId}/feasibility-workbook/extract`, {
          method: "POST",
          headers: authHeaders("web"),
          body: fd,
        });
        if (!res.ok) throw new Error(`추출 실패 (${res.status})`);
        const data = (await res.json()) as ExtractResponse;
        if (data.parsingSource !== "llm") {
          throw new Error(data.error || "AI 추출을 사용할 수 없습니다 (제공자 미설정).");
        }
        const f = data.fields ?? {};
        setRows(
          FIELDS.map((fld) => ({
            key: fld.key,
            label: fld.label,
            eok: toEok(f[fld.key]?.value_krw),
            source: f[fld.key]?.source ?? "",
            unit: f[fld.key]?.unit ?? "",
          })),
        );
        setNotice(data.note ?? "추출 완료 — 값을 검토·수정한 뒤 적용하세요.");
      } catch (err) {
        setError(err instanceof Error ? err.message : "추출 중 오류가 발생했습니다.");
      } finally {
        setLoading(false);
      }
    },
    [projectId],
  );

  const apply = useCallback(async () => {
    setApplying(true);
    setError(null);
    setNotice(null);
    try {
      const feasibility_input: Record<string, number> = {};
      for (const r of rows) {
        const n = Number(r.eok);
        if (r.eok.trim() !== "" && Number.isFinite(n)) feasibility_input[r.key] = Math.round(n * 1e8);
      }
      if (Object.keys(feasibility_input).length === 0) throw new Error("적용할 값이 없습니다.");
      const res = await fetch(`${apiBase}/projects/${projectId}/feasibility-workbook/apply-input`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ feasibility_input, sourceDocument: { originalFileName: fileName } }),
      });
      if (res.status === 403) throw new Error("적용 권한이 없습니다 (심사역/관리자 권한 필요).");
      if (!res.ok) throw new Error(`적용 실패 (${res.status})`);
      setNotice("사업수지 값을 프로젝트에 적용했습니다. PF 심사를 다시 실행하면 현금흐름까지 반영됩니다.");
      onApplied?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "적용 중 오류가 발생했습니다.");
    } finally {
      setApplying(false);
    }
  }, [rows, projectId, fileName, onApplied]);

  const setEok = (key: string, value: string) =>
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, eok: value } : r)));

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-600 text-white">
          <Sparkles className="h-5 w-5" />
        </div>
        <div>
          <h2 className="font-black text-slate-950 dark:text-white">사업수지 AI 추출·검토·적용</h2>
          <p className="text-xs font-semibold text-slate-500">
            어떤 양식의 사업수지 Excel이든 올리면 AI가 매출·원가·PF를 출처 셀과 함께 추출합니다. 검토·수정 후 적용하세요(단위: 억원).
          </p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={loading}
          className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSpreadsheet className="h-4 w-4" />}
          사업수지 Excel 선택
        </button>
        {fileName ? <span className="truncate text-xs text-slate-500">{fileName}</span> : null}
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx,.xls,.xlsm"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void extract(f);
          }}
        />
      </div>

      {error ? <p className="mt-3 text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}
      {notice ? (
        <p className="mt-3 flex items-start gap-1.5 rounded-lg bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> {notice}
        </p>
      ) : null}

      {rows.length ? (
        <div className="mt-4">
          <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
            <table className="w-full min-w-[560px] text-sm">
              <thead className="bg-slate-50 text-left text-xs font-bold text-slate-500 dark:bg-slate-950/40">
                <tr>
                  <th className="px-3 py-2">항목</th>
                  <th className="px-3 py-2">값 (억원, 수정 가능)</th>
                  <th className="px-3 py-2">출처 셀</th>
                  <th className="px-3 py-2">단위</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {rows.map((r) => (
                  <tr key={r.key}>
                    <td className="px-3 py-2 font-semibold text-slate-900 dark:text-white">{r.label}</td>
                    <td className="px-3 py-2">
                      <input
                        value={r.eok}
                        onChange={(e) => setEok(r.key, e.target.value)}
                        inputMode="decimal"
                        placeholder="—"
                        className="h-8 w-28 rounded-md border border-slate-300 px-2 text-right font-bold dark:border-slate-700 dark:bg-slate-900"
                      />
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-slate-500">{r.source || "—"}</td>
                    <td className="px-3 py-2 text-xs text-slate-500">{r.unit || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex items-center justify-between gap-2">
            <p className="text-[11px] text-slate-500">
              AI 추출은 단위를 잘못 읽을 수 있습니다. 출처 셀을 확인하고 값을 검토한 뒤 적용하세요.
            </p>
            <button
              type="button"
              onClick={() => void apply()}
              disabled={applying}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-emerald-600 px-4 text-sm font-bold text-white disabled:opacity-50"
            >
              {applying ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
              프로젝트에 적용
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
