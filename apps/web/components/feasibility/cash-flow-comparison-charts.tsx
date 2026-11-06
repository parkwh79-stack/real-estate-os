"use client";

import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export type AlignedCashFlow = {
  baseMonth: string;
  cashFlowBasis?: "before_all_financing";
  rows: Array<{ monthIndex: number; baselineValue: number | null; comparisonValue: number | null }>;
};

type ChartComparison = {
  status: string;
  metrics: Array<{ key: string; label: string; delta: number | null; unit: string }>;
  alignedCashFlow?: AlignedCashFlow;
};

const deltaKeys = ["totalProjectCost", "developmentProfit", "requiredPfLimit"];
const money = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });

function isAmount(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function CashFlowComparisonCharts({ comparison }: { comparison: ChartComparison }) {
  if (comparison.status === "incompatible") return null;

  const metrics = deltaKeys.flatMap((key) => comparison.metrics.filter((metric) => metric.key === key && metric.unit === "KRW"));
  const maxDelta = Math.max(1, ...metrics.map(({ delta }) => isAmount(delta) ? Math.abs(delta) : 0));
  const aligned = comparison.alignedCashFlow;
  if (!metrics.length && !aligned?.rows.length) return null;

  return <div className="grid min-w-0 gap-4">
    {metrics.length ? <section aria-label="주요 금액 차이" className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
      <h4 className="text-sm font-semibold">주요 금액 차이</h4>
      <p className="mt-1 text-xs text-slate-500">변경안 − 기준안 · 원 · 왼쪽은 감소, 오른쪽은 증가</p>
      <ul className="mt-3 space-y-3">
        {metrics.map(({ key, label, delta }) => <li key={key} className="grid min-w-0 gap-1">
          <p className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
            <span>{label}</span>
            <strong className="tabular-nums">{isAmount(delta) ? `${delta >= 0.5 ? "+" : ""}${money.format(Math.abs(delta) < 0.5 ? 0 : delta)}원` : "미확인"}</strong>
          </p>
          <div aria-hidden="true" className="relative h-3 rounded-sm bg-slate-100 dark:bg-slate-800">
            <span className="absolute inset-y-0 left-1/2 w-px bg-slate-400" />
            {isAmount(delta) && delta !== 0 ? <span className={`absolute inset-y-0 rounded-sm ${delta < 0 ? "right-1/2 bg-indigo-600 dark:bg-indigo-400" : "left-1/2 bg-teal-600 dark:bg-teal-400"}`}
              style={{ width: `${Math.abs(delta) / maxDelta * 50}%` }} /> : null}
          </div>
        </li>)}
      </ul>
    </section> : null}
    {aligned?.rows.length ? <section aria-label="같은 월차의 누적 현금흐름 비교" className="min-w-0 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
      <h4 className="text-sm font-semibold">같은 월차의 누적 현금흐름 비교</h4>
      <p className="mt-1 text-xs text-slate-500">{aligned.baseMonth} 기준 · {aligned.cashFlowBasis === "before_all_financing" ? "조달 전 누적 영업현금" : "PF 인출 전 누적 현금흐름"} · 원</p>
      <div className="mt-2 h-64 min-w-0">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <LineChart data={aligned.rows} accessibilityLayer margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="#94a3b8" strokeOpacity={0.35} vertical={false} />
            <XAxis dataKey="monthIndex" tickFormatter={(value) => `M${value}`} minTickGap={20} tick={{ fill: "currentColor", fontSize: 11 }} />
            <YAxis width={104} tickFormatter={(value) => money.format(value)} tick={{ fill: "currentColor", fontSize: 11 }} />
            <ReferenceLine y={0} stroke="#64748b" />
            <Tooltip
              labelFormatter={(label) => `M${label}`}
              formatter={(value) => isAmount(value) ? `${money.format(value)}원` : "미확인"}
              contentStyle={{ color: "#0f172a", backgroundColor: "#fff" }}
            />
            <Legend />
            <Line type="linear" dataKey="baselineValue" name="기준안" stroke="#3b82f6" strokeWidth={2} connectNulls={false} isAnimationActive={false} dot={{ r: 2 }} />
            <Line type="linear" dataKey="comparisonValue" name="변경안" stroke="#0d9488" strokeWidth={2} strokeDasharray="5 3" connectNulls={false} isAnimationActive={false} dot={{ r: 2 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section> : metrics.length ? <p className="text-xs text-slate-500">같은 달력 월차와 두 계산의 누적 현금흐름이 확인되면 시계열을 함께 표시합니다.</p> : null}
  </div>;
}
