"use client";

import { useState } from "react";
import { Library, Loader2, Search } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";

interface RagHit {
  snapshotId?: string;
  projectId?: string;
  runId?: string;
  score?: number;
  caseComparisonSeed?: Record<string, unknown>;
  historicalOutcome?: { outcomeStatus?: string; recoveryRate?: number | null };
}
interface RagResult {
  status?: string;
  backend?: string;
  count?: number;
  hits?: RagHit[];
}

/**
 * Institutional-memory RAG: search prior reviewed cases and show cited hits.
 * Grounding only — figures/decisions stay in the deterministic engines.
 */
export function SimilarCasesRagPanel({ projectId }: { projectId: string }) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<RagResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const search = async () => {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `${apiBase}/projects/${projectId}/similar-cases/rag?q=${encodeURIComponent(query)}&k=5`,
        { cache: "no-store", headers: authHeaders("web") },
      );
      if (!res.ok) throw new Error(`검색 실패 (${res.status})`);
      setResult(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "유사사례 검색에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-200">
          <Library className="h-5 w-5" />
        </div>
        <div>
          <p className="font-black text-slate-950 dark:text-white">유사사례 검색 (RAG)</p>
          <p className="text-xs font-semibold text-slate-500">과거 심사 사례를 인용과 함께 검색</p>
        </div>
      </div>

      <div className="mt-3 flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void search();
          }}
          placeholder="예: 대구 주상복합 미분양 UNSOLD_LTV"
          className="h-9 flex-1 rounded-lg border border-slate-200 px-3 text-sm dark:border-slate-700 dark:bg-slate-950 dark:text-white"
        />
        <button
          type="button"
          onClick={() => void search()}
          disabled={loading || !query.trim()}
          className="inline-flex h-9 items-center gap-1 rounded-lg bg-cyan-600 px-3 text-xs font-bold text-white disabled:opacity-40"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          검색
        </button>
      </div>

      {error ? <p className="mt-3 text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      {result ? (
        <div className="mt-4">
          <p className="text-xs font-semibold text-slate-500">
            {result.count ?? 0}건 · 백엔드 {result.backend ?? "lexical"}
          </p>
          <ul className="mt-2 space-y-2">
            {(result.hits ?? []).map((hit) => {
              const seed = hit.caseComparisonSeed ?? {};
              return (
                <li key={hit.snapshotId} className="rounded-lg border border-slate-200 p-3 text-xs dark:border-slate-800">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-slate-900 dark:text-white">
                      {String(seed["projectType"] ?? "—")} · {String(seed["location"] ?? "—")}
                    </span>
                    <span className="font-bold text-cyan-700 dark:text-cyan-300">score {hit.score?.toFixed(3)}</span>
                  </div>
                  <div className="mt-1 text-slate-600 dark:text-slate-300">
                    시장성 {String(seed["marketabilityGrade"] ?? "—")} · LTV {String(seed["ltv"] ?? "—")} · 결과 {hit.historicalOutcome?.outcomeStatus ?? "—"}
                  </div>
                  <div className="mt-1 text-[11px] text-slate-400">
                    인용: 프로젝트 {hit.projectId} · 스냅샷 {hit.snapshotId} · run {hit.runId}
                  </div>
                </li>
              );
            })}
          </ul>
          {(result.hits ?? []).length === 0 ? (
            <p className="mt-2 text-sm text-slate-500">일치하는 과거 사례가 없습니다.</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
