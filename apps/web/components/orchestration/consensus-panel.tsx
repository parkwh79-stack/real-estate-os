"use client";

import { useCallback, useEffect, useState } from "react";
import { GitMerge, Loader2, RefreshCw } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { EmptyState } from "../ui/primitives";

interface Resolution {
  key?: string;
  label?: string;
  adoptedSource?: string;
  rationale?: string;
}
interface Unresolved {
  key?: string;
  label?: string;
  reason?: string;
}
interface ConsensusResolution {
  status?: string;
  coordinator?: string;
  rounds?: number;
  resolutions?: Resolution[];
  unresolved?: Unresolved[];
  aiSelection?: { selected?: boolean; provider?: string; model?: string };
  summary?: { resolvedCount?: number; unresolvedCount?: number };
}

/**
 * Surfaces the iterative cross-agent consensus result from the latest PF run:
 * how many conflicts were resolved (by which agent/source), what remains, and
 * whether an LLM coordinator or the deterministic fallback drove the loop.
 */
export function ConsensusPanel({ projectId }: { projectId: string }) {
  const [data, setData] = useState<ConsensusResolution | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
        setData(null);
        return;
      }
      const runId = runs[0].run_id ?? runs[0].runId;
      const detailRes = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${runId}`, {
        cache: "no-store",
        headers: authHeaders("web"),
      });
      if (!detailRes.ok) throw new Error(`실행 상세 조회 실패 (${detailRes.status})`);
      const detail = await detailRes.json();
      setData((detail.artifacts ?? {}).consensusResolution ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "합의 결과를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const statusLabel = data?.status === "converged" ? "수렴 완료" : data?.status === "partial" ? "부분 수렴(증빙 필요)" : data?.status;
  const coordinatorLabel = data?.coordinator === "llm" ? "LLM 조정자" : "결정론 조정자";

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-200">
            <GitMerge className="h-5 w-5" />
          </div>
          <div>
            <p className="font-black text-slate-950 dark:text-white">교차 정합 합의 (Consensus)</p>
            <p className="text-xs font-semibold text-slate-500">에이전트 간 반복 재분석으로 수치 합의</p>
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

      {!data && !loading && !error ? (
        <div className="mt-4">
          <EmptyState title="합의 결과가 없습니다" body="통합심사(PF 심사)를 실행하면 교차 정합 합의 결과가 표시됩니다." />
        </div>
      ) : null}

      {data ? (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap gap-2 text-xs font-bold">
            <span className={`rounded-md px-2 py-1 ${data.status === "converged" ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200" : "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200"}`}>
              {statusLabel}
            </span>
            <span className="rounded-md bg-slate-100 px-2 py-1 text-slate-700 dark:bg-slate-800 dark:text-slate-200">{coordinatorLabel}</span>
            <span className="rounded-md bg-slate-100 px-2 py-1 text-slate-700 dark:bg-slate-800 dark:text-slate-200">{data.rounds ?? 0}라운드</span>
            <span className="rounded-md bg-slate-100 px-2 py-1 text-slate-700 dark:bg-slate-800 dark:text-slate-200">
              합의 {data.summary?.resolvedCount ?? 0} · 미해결 {data.summary?.unresolvedCount ?? 0}
            </span>
            {data.aiSelection?.selected ? (
              <span className="rounded-md bg-indigo-100 px-2 py-1 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-200">
                AI: {data.aiSelection.provider}/{data.aiSelection.model}
              </span>
            ) : null}
          </div>

          {data.resolutions && data.resolutions.length > 0 ? (
            <div>
              <p className="mb-1 text-sm font-bold text-slate-950 dark:text-white">합의된 항목</p>
              <ul className="space-y-1 text-xs text-slate-700 dark:text-slate-300">
                {data.resolutions.map((r, i) => (
                  <li key={`${r.key}-${i}`} className="rounded-md border border-slate-200 px-2 py-1 dark:border-slate-800">
                    <span className="font-bold">{r.label ?? r.key}</span>
                    {r.adoptedSource ? <span className="ml-1 text-emerald-600 dark:text-emerald-300">[{r.adoptedSource}]</span> : null}
                    {r.rationale ? <span className="ml-1 text-slate-500">— {r.rationale}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {data.unresolved && data.unresolved.length > 0 ? (
            <div>
              <p className="mb-1 text-sm font-bold text-amber-700 dark:text-amber-300">미해결 (증빙·검토 필요)</p>
              <ul className="space-y-1 text-xs text-slate-700 dark:text-slate-300">
                {data.unresolved.map((u, i) => (
                  <li key={`${u.key}-${i}`}>• {u.label ?? u.key}: {u.reason}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
