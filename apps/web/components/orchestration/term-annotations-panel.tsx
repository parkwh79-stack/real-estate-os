"use client";

import { useCallback, useEffect, useState } from "react";
import { BookMarked, Loader2, RefreshCw } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { EmptyState, MetricCard, StatusBadge } from "../ui/primitives";

interface Annotation {
  surface: string;
  termId?: string | null;
  displayNameKo?: string | null;
  canonicalField?: string | null;
  category?: string | null;
  sourceField?: string;
  matchType?: string;
  status?: string;
}
interface TermAnnotations {
  summary?: {
    mappedCount?: number;
    unmappedCandidateCount?: number;
    distinctTerms?: number;
    distinctCanonicalFields?: number;
  };
  annotations?: Annotation[];
}

const MATCH_LABEL: Record<string, string> = {
  canonical_field: "필드 매핑",
  canonical_field_value: "필드값 매핑",
  alias_text: "별칭 매칭",
  none: "미매핑",
};

/**
 * Surfaces controlled-vocabulary term annotations (termId + canonicalField +
 * sourceLocator) from the latest PF review run, plus unmapped candidates that
 * can be promoted via glossary review.
 */
export function TermAnnotationsPanel({ projectId }: { projectId: string }) {
  const [data, setData] = useState<TermAnnotations | null>(null);
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
      setData((detail.artifacts?.termAnnotations ?? null) as TermAnnotations | null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "용어 표준화 결과를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const summary = data?.summary;
  const annotations = data?.annotations ?? [];
  const mapped = annotations.filter((a) => a.status === "mapped");
  const unmapped = annotations.filter((a) => a.status === "unmapped_candidate");

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200">
            <BookMarked className="h-5 w-5" />
          </div>
          <div>
            <p className="font-black text-slate-950 dark:text-white">용어 표준화 (Glossary)</p>
            <p className="text-xs font-semibold text-slate-500">추출 용어에 termId · canonicalField · 출처를 부착</p>
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
          <EmptyState title="용어 표준화 결과가 없습니다" body="통합심사를 실행하면 표준 용어 매핑과 미매핑 후보가 표시됩니다." />
        </div>
      ) : null}

      {data ? (
        <div className="mt-4 space-y-4">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <MetricCard label="매핑 용어" value={`${summary?.mappedCount ?? mapped.length}`} />
            <MetricCard label="고유 termId" value={`${summary?.distinctTerms ?? "—"}`} />
            <MetricCard label="canonical 필드" value={`${summary?.distinctCanonicalFields ?? "—"}`} />
            <MetricCard label="미매핑 후보" value={`${summary?.unmappedCandidateCount ?? unmapped.length}`} />
          </div>

          {mapped.length ? (
            <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 text-slate-500 dark:bg-slate-800">
                  <tr>
                    <th className="px-3 py-2">표면형</th>
                    <th className="px-3 py-2">표준 용어</th>
                    <th className="px-3 py-2">Canonical 필드</th>
                    <th className="px-3 py-2">출처</th>
                    <th className="px-3 py-2">매칭</th>
                  </tr>
                </thead>
                <tbody>
                  {mapped.slice(0, 60).map((a, index) => (
                    <tr key={`${a.termId}-${a.sourceField}-${index}`} className="border-t border-slate-100 dark:border-slate-800">
                      <td className="px-3 py-2 font-semibold text-slate-900 dark:text-white">{a.surface}</td>
                      <td className="px-3 py-2">{a.displayNameKo ?? a.termId}</td>
                      <td className="px-3 py-2 font-mono text-slate-500">{a.canonicalField}</td>
                      <td className="px-3 py-2 font-mono text-slate-400">{a.sourceField}</td>
                      <td className="px-3 py-2 text-slate-500">{MATCH_LABEL[a.matchType ?? ""] ?? a.matchType}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {unmapped.length ? (
            <div>
              <div className="mb-2 flex items-center gap-2">
                <StatusBadge tone="warn" label={`미매핑 후보 ${unmapped.length}`} />
                <span className="text-xs text-slate-500">글로서리 검토(/glossary/candidates)에서 표준 용어로 승격할 수 있습니다.</span>
              </div>
              <ul className="space-y-1">
                {unmapped.slice(0, 15).map((a, index) => (
                  <li key={`${a.sourceField}-${index}`} className="rounded-lg bg-slate-50 px-3 py-2 text-xs dark:bg-slate-950/40">
                    <span className="font-semibold text-slate-800 dark:text-slate-200">{a.surface}</span>
                    <span className="ml-2 font-mono text-slate-400">{a.sourceField}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
