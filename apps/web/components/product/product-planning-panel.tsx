"use client";

import { useCallback, useEffect, useState } from "react";
import { Building2, Loader2 } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";

type PlanRow = {
  assetType: string;
  assetTypeLabel: string;
  incomeAsset?: boolean;
  grossFloorAreaSqm?: number;
  exclusiveAreaSqm?: number;
  efficiency?: number;
  unitCount?: number | null;
  parkingSpaces?: number | null;
  pricePerPy?: number | null;
  salesRevenue?: number | null;
  annualNoi?: number | null;
  incomeValue?: number | null;
  assumptionBasis?: string;
  notes?: string[];
};

type PlanResult = {
  schemaVersion: string;
  landAreaSqm?: number;
  appliedFar?: number;
  scenarioCount?: number;
  pricedCount?: number;
  rows: PlanRow[];
  topByValue?: { assetTypeLabel?: string; value?: number; valueKind?: string } | null;
  dataQualityNotes?: string[];
  notes?: string;
};

function eok(value?: number | null) {
  if (!value) return "—";
  return `${(value / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}억`;
}
function sqm(value?: number) {
  if (!value) return "—";
  return `${value.toLocaleString("ko-KR")}㎡`;
}
function count(value?: number | null, unit = "") {
  if (value === null || value === undefined) return "—";
  return `${value.toLocaleString("ko-KR")}${unit}`;
}
function pct(value?: number) {
  if (!value) return "—";
  return `${(value * 100).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}%`;
}

export function ProductPlanningPanel({ projectId }: { projectId: string }) {
  const [result, setResult] = useState<PlanResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/product-planning/compare`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error(`상품기획 비교 실패 (${res.status})`);
      setResult((await res.json()) as PlanResult);
    } catch (err) {
      setError(err instanceof Error ? err.message : "상품기획 비교를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-1 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Building2 className="h-5 w-5 text-indigo-600" />
          <h2 className="text-lg font-black text-slate-950 dark:text-white">자산유형별 상품기획</h2>
        </div>
        {result?.topByValue?.assetTypeLabel ? (
          <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-black text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
            최고 산출가치: {result.topByValue.assetTypeLabel}
          </span>
        ) : null}
      </div>
      <p className="mb-4 text-xs text-slate-500">
        동일 토지·규제 조건의 자산유형별 연면적·전용면적·세대/호실·전용률·주차·분양가·임대료입니다. 전용률·평형·주차원단위는 계획 벤치마크이며 실제 가정 입력 시 대체됩니다. 가격 근거가 없으면 산정하지 않습니다.
      </p>

      {loading ? (
        <div className="flex min-h-[120px] items-center justify-center text-sm font-bold text-slate-500">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />상품기획 산출 중입니다.
        </div>
      ) : error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">{error}</div>
      ) : result ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[880px] text-left text-sm">
            <thead className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-800">
              <tr>
                <th className="px-3 py-2">자산유형</th>
                <th className="px-3 py-2">연면적</th>
                <th className="px-3 py-2">전용면적</th>
                <th className="px-3 py-2">전용률</th>
                <th className="px-3 py-2">세대/호실</th>
                <th className="px-3 py-2">주차</th>
                <th className="px-3 py-2">분양가/평</th>
                <th className="px-3 py-2">분양수입</th>
                <th className="px-3 py-2">NOI/수익환원</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {result.rows.map((row) => (
                <tr key={row.assetType}>
                  <td className="px-3 py-3 font-black text-slate-950 dark:text-white">
                    {row.assetTypeLabel}
                    {row.assumptionBasis === "planning_benchmark" ? (
                      <span className="ml-1.5 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold text-slate-500 dark:bg-slate-800 dark:text-slate-400">벤치마크</span>
                    ) : null}
                  </td>
                  <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{sqm(row.grossFloorAreaSqm)}</td>
                  <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{sqm(row.exclusiveAreaSqm)}</td>
                  <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{pct(row.efficiency)}</td>
                  <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{count(row.unitCount, row.incomeAsset ? "실" : "세대")}</td>
                  <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{count(row.parkingSpaces, "대")}</td>
                  <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{row.pricePerPy ? `${(row.pricePerPy / 10000).toLocaleString("ko-KR")}만` : "—"}</td>
                  <td className="px-3 py-3 font-bold text-slate-950 dark:text-white">{eok(row.salesRevenue)}</td>
                  <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{row.incomeAsset ? `${eok(row.annualNoi)}/${eok(row.incomeValue)}` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.dataQualityNotes?.length ? (
            <div className="mt-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              {result.dataQualityNotes.map((note, i) => <p key={i}>{note}</p>)}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
