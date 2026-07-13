"use client";

import { useCallback, useEffect, useState } from "react";
import { Building, Landmark, Loader2, ListChecks, Users } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";

type ComparisonRow = {
  provider: string;
  label: string;
  proposedPricePerPy?: number | null;
  status?: string;
  deltaVsPublicMedian?: number | null;
  assessment?: string;
};

type DepthResult = {
  schemaVersion: string;
  sourceProvenance?: {
    byProvider?: Record<string, { label: string; count: number }>;
    developerProvided?: number;
    salesAgentProvided?: number;
    publicData?: number;
    autoSupplemented?: number;
  };
  providerComparison?: {
    publicMedianPerPy?: number | null;
    rows?: ComparisonRow[];
    developerVsSalesAgent?: { deltaPct?: number | null; note?: string } | null;
  };
  productMarketabilityScores?: Array<{ productType: string; grade: string; score?: number | null }>;
  absorptionModel?: { monthlyAbsorptionRate?: number; rateBasis?: string; estimatedMonthsToSellout?: number | null };
  aiRecommendedNextActions?: { mode?: string; actions?: Array<{ priority: string; action: string; category?: string }> };
};

function man(value?: number | null) {
  if (!value) return "미제출";
  return `${(value / 10000).toLocaleString("ko-KR")}만/평`;
}
function pctDelta(value?: number | null) {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%`;
}

const PRIORITY_CLS: Record<string, string> = {
  high: "border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200",
  medium: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200",
  low: "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300",
};

export function MarketDepthPanel({ projectId }: { projectId: string }) {
  const [result, setResult] = useState<DepthResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/market-product-depth/analyze`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ options: { podAiReviewMode: "deterministic" } }),
      });
      if (!res.ok) throw new Error(`시장성 심화 분석 실패 (${res.status})`);
      setResult((await res.json()) as DepthResult);
    } catch (err) {
      setError(err instanceof Error ? err.message : "시장성 심화 분석을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const prov = result?.sourceProvenance;
  const comparison = result?.providerComparison;
  const absorption = result?.absorptionModel;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-4 flex items-center gap-2">
        <Users className="h-5 w-5 text-indigo-600" />
        <h2 className="text-lg font-black text-slate-950 dark:text-white">자료 제공주체 비교 · 시장성 심화</h2>
      </div>
      <p className="mb-4 text-xs text-slate-500">
        시행사·매각 주관사·공공(국토부 실거래가)·AI 자동보강 자료를 제공주체별로 명명·비교합니다. 제안 분양가는 공공 실거래 중위값을 앵커로 괴리를 산출하며, 추가로 필요한 작업은 AI가 판단합니다.
      </p>

      {loading ? (
        <div className="flex min-h-[120px] items-center justify-center text-sm font-bold text-slate-500">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />시장성 심화 분석 중입니다.
        </div>
      ) : error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">{error}</div>
      ) : result ? (
        <div className="space-y-5">
          <div className="grid gap-3 md:grid-cols-4">
            <Tile icon={<Building className="h-4 w-4 text-indigo-600" />} label="시행사 제공" value={`${prov?.developerProvided ?? 0}건`} />
            <Tile icon={<Users className="h-4 w-4 text-sky-600" />} label="매각 주관사 제공" value={`${prov?.salesAgentProvided ?? 0}건`} warn={!prov?.salesAgentProvided} />
            <Tile icon={<Landmark className="h-4 w-4 text-emerald-600" />} label="공공 실거래" value={`${prov?.publicData ?? 0}건`} />
            <Tile icon={<Database4 />} label="AI 자동보강" value={`${prov?.autoSupplemented ?? 0}건`} />
          </div>

          <div>
            <h3 className="mb-2 text-sm font-black text-slate-950 dark:text-white">제안 분양가 비교 (공공 실거래 중위값 {man(comparison?.publicMedianPerPy)} 기준)</h3>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[620px] text-left text-sm">
                <thead className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-800">
                  <tr><th className="px-3 py-2">제공주체</th><th className="px-3 py-2">제안 분양가</th><th className="px-3 py-2">공공 대비</th><th className="px-3 py-2">판단</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {(comparison?.rows ?? []).map((row) => (
                    <tr key={row.provider}>
                      <td className="px-3 py-3 font-bold text-slate-950 dark:text-white">{row.label}</td>
                      <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{man(row.proposedPricePerPy)}</td>
                      <td className={`px-3 py-3 font-bold ${typeof row.deltaVsPublicMedian === "number" && row.deltaVsPublicMedian > 0.15 ? "text-rose-600 dark:text-rose-300" : "text-slate-600 dark:text-slate-300"}`}>{pctDelta(row.deltaVsPublicMedian)}</td>
                      <td className="px-3 py-3 text-xs text-slate-500">{row.assessment}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {comparison?.developerVsSalesAgent?.note ? (
              <p className="mt-2 rounded-lg bg-slate-50 p-3 text-xs font-semibold text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">{comparison.developerVsSalesAgent.note}</p>
            ) : null}
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <h3 className="mb-2 text-sm font-black text-slate-950 dark:text-white">상품유형별 분양성 Score</h3>
              {result.productMarketabilityScores?.length ? (
                <div className="flex flex-wrap gap-2">
                  {result.productMarketabilityScores.map((s) => (
                    <span key={s.productType} className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-bold text-slate-700 dark:border-slate-700 dark:text-slate-200">
                      {s.productType} · <span className="font-black text-indigo-600 dark:text-indigo-300">{s.grade}</span>
                    </span>
                  ))}
                </div>
              ) : <p className="text-xs text-slate-400">상품 구성·가격 자료 입력 시 산출됩니다.</p>}
            </div>
            <div>
              <h3 className="mb-2 text-sm font-black text-slate-950 dark:text-white">흡수율 모델</h3>
              <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                월 {absorption ? `${((absorption.monthlyAbsorptionRate ?? 0) * 100).toFixed(1)}%` : "—"} · 완판 예상 {absorption?.estimatedMonthsToSellout ?? "—"}개월
                {absorption?.rateBasis === "planning_benchmark" ? <span className="ml-1.5 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold text-slate-500 dark:bg-slate-800">벤치마크</span> : null}
              </p>
            </div>
          </div>

          <div>
            <h3 className="mb-2 flex items-center gap-2 text-sm font-black text-slate-950 dark:text-white">
              <ListChecks className="h-4 w-4 text-indigo-600" />추가 필요작업 (AI 판단)
              {result.aiRecommendedNextActions?.mode ? <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold text-slate-500 dark:bg-slate-800">{result.aiRecommendedNextActions.mode}</span> : null}
            </h3>
            <div className="space-y-2">
              {(result.aiRecommendedNextActions?.actions ?? []).map((a, i) => (
                <div key={i} className={`rounded-lg border p-3 text-xs ${PRIORITY_CLS[a.priority] ?? PRIORITY_CLS.medium}`}>
                  <span className="font-black uppercase">{a.priority}</span> · {a.action}
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function Tile({ icon, label, value, warn }: { icon: React.ReactNode; label: string; value: string; warn?: boolean }) {
  return (
    <div className={`rounded-lg border p-3 ${warn ? "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/20" : "border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900"}`}>
      <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-500">{icon}{label}</div>
      <p className="mt-2 text-xl font-black text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function Database4() {
  return <span className="inline-block h-4 w-4 rounded-sm bg-violet-500/80" aria-hidden />;
}
