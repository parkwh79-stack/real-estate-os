"use client";

import { useCallback, useEffect, useState } from "react";
import { Copy, Download, FileText, Loader2, RefreshCw } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { EmptyState } from "../ui/primitives";
import { MarkdownView } from "../ui/markdown-view";

interface CreditDoc {
  documentType?: string;
  stageName?: string;
  markdown?: string;
}
interface CreditDocuments {
  termSheet?: CreditDoc | null;
  termSheetStages?: Record<string, CreditDoc> | null;
  icMemo?: CreditDoc | null;
}

type TabKey = "icMemo" | "termSheet";
type StageKey = "main_pf" | "bridge";

const STAGE_LABELS: Record<StageKey, string> = { main_pf: "본PF", bridge: "브릿지론" };

/**
 * Surfaces the lender-facing credit documents (Term Sheet, IC Memo) generated
 * during the latest PF review run. Read-only preview with copy-to-clipboard;
 * the documents are deterministic restatements of the run's artifacts.
 */
export function CreditDocumentsPanel({ projectId }: { projectId: string }) {
  const [docs, setDocs] = useState<CreditDocuments | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("icMemo");
  const [stageSel, setStageSel] = useState<StageKey>("main_pf");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const listRes = await fetch(`${apiBase}/projects/${projectId}/orchestrations`, {
        cache: "no-store",
        headers: authHeaders("web"),
      });
      if (!listRes.ok) throw new Error(`목록 조회 실패 (${listRes.status})`);
      const listBody = await listRes.json();
      const runs = Array.isArray(listBody) ? listBody : (listBody.runs ?? listBody.orchestrations ?? []);
      if (!runs.length) {
        setDocs(null);
        return;
      }
      const latestRunId = runs[0].run_id ?? runs[0].runId;
      setRunId(latestRunId);
      const res = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${latestRunId}/credit-documents`, {
        cache: "no-store",
        headers: authHeaders("web"),
      });
      if (res.status === 404) {
        setDocs(null);
        return;
      }
      if (!res.ok) throw new Error(`여신 문서 조회 실패 (${res.status})`);
      const body = await res.json();
      setDocs({ termSheet: body.termSheet, icMemo: body.icMemo });
    } catch (err) {
      setError(err instanceof Error ? err.message : "여신 문서를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const termSheetDoc = docs?.termSheetStages?.[stageSel] ?? docs?.termSheet;
  const active = tab === "icMemo" ? docs?.icMemo : termSheetDoc;
  const markdown = active?.markdown ?? "";

  const copy = async () => {
    if (!markdown) return;
    try {
      await navigator.clipboard.writeText(markdown);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("클립보드 복사에 실패했습니다.");
    }
  };

  const download = async (format: "pdf" | "docx") => {
    if (!runId) return;
    const documentSlug = tab === "icMemo" ? "ic-memo" : "term-sheet";
    const stageParam = tab === "termSheet" ? `&stage=${stageSel}` : "";
    setDownloading(format);
    setError(null);
    try {
      const res = await fetch(
        `${apiBase}/projects/${projectId}/orchestrations/${runId}/credit-documents/${documentSlug}/export?format=${format}${stageParam}`,
        { cache: "no-store", headers: authHeaders("web") },
      );
      if (!res.ok) throw new Error(`다운로드 실패 (${res.status})`);
      const blob = await res.blob();
      const disposition = res.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename="?([^"]+)"?/);
      const fileName = match?.[1] ?? `${documentSlug}.${format}`;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = fileName;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "다운로드에 실패했습니다.");
    } finally {
      setDownloading(null);
    }
  };

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200">
            <FileText className="h-5 w-5" />
          </div>
          <div>
            <p className="font-black text-slate-950 dark:text-white">여신 문서 (Term Sheet · IC Memo)</p>
            <p className="text-xs font-semibold text-slate-500">최신 PF 심사 실행 결과 기준 · 초안</p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 px-3 text-xs font-bold text-slate-700 disabled:opacity-40 dark:border-slate-700 dark:text-slate-200"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          새로고침
        </button>
      </div>

      {error ? <p className="mt-3 text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      {!docs && !loading && !error ? (
        <div className="mt-4">
          <EmptyState
            title="생성된 여신 문서가 없습니다"
            body="통합심사(PF 심사)를 실행하면 거래조건서와 여신심사보고서 초안이 여기에 생성됩니다."
          />
        </div>
      ) : null}

      {docs ? (
        <div className="mt-4">
          <div className="flex items-center justify-between gap-2">
            <div className="inline-flex rounded-lg border border-slate-200 p-1 dark:border-slate-700">
              <button
                type="button"
                onClick={() => setTab("icMemo")}
                className={`rounded-md px-3 py-1 text-xs font-bold ${tab === "icMemo" ? "bg-emerald-600 text-white" : "text-slate-600 dark:text-slate-300"}`}
              >
                여신심사보고서
              </button>
              <button
                type="button"
                onClick={() => setTab("termSheet")}
                className={`rounded-md px-3 py-1 text-xs font-bold ${tab === "termSheet" ? "bg-emerald-600 text-white" : "text-slate-600 dark:text-slate-300"}`}
              >
                거래조건서
              </button>
            </div>
            {tab === "termSheet" ? (
              <div className="inline-flex rounded-lg border border-slate-200 p-1 dark:border-slate-700">
                {(Object.keys(STAGE_LABELS) as StageKey[]).map((key) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setStageSel(key)}
                    className={`rounded-md px-3 py-1 text-xs font-bold ${stageSel === key ? "bg-indigo-600 text-white" : "text-slate-600 dark:text-slate-300"}`}
                  >
                    {STAGE_LABELS[key]}
                  </button>
                ))}
              </div>
            ) : null}
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => void copy()}
                disabled={!markdown}
                className="inline-flex h-8 items-center gap-1 rounded-lg border border-slate-200 px-3 text-xs font-bold text-slate-700 disabled:opacity-40 dark:border-slate-700 dark:text-slate-200"
              >
                <Copy className="h-3.5 w-3.5" />
                {copied ? "복사됨" : "복사"}
              </button>
              <button
                type="button"
                onClick={() => void download("pdf")}
                disabled={!markdown || downloading !== null}
                className="inline-flex h-8 items-center gap-1 rounded-lg border border-slate-200 px-3 text-xs font-bold text-slate-700 disabled:opacity-40 dark:border-slate-700 dark:text-slate-200"
              >
                {downloading === "pdf" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                PDF
              </button>
              <button
                type="button"
                onClick={() => void download("docx")}
                disabled={!markdown || downloading !== null}
                className="inline-flex h-8 items-center gap-1 rounded-lg border border-slate-200 px-3 text-xs font-bold text-slate-700 disabled:opacity-40 dark:border-slate-700 dark:text-slate-200"
              >
                {downloading === "docx" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                DOCX
              </button>
            </div>
          </div>

          <div className="mt-3 max-h-[28rem] overflow-auto rounded-lg bg-slate-50 p-4 dark:bg-slate-950/40">
            {markdown ? <MarkdownView markdown={markdown} /> : <p className="text-sm text-slate-500">내용이 없습니다.</p>}
          </div>
        </div>
      ) : null}
    </section>
  );
}
