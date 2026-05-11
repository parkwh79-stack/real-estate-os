"use client";

import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertTriangle,
  CheckCircle2,
  Info,
  Layers,
  ShieldCheck,
} from "lucide-react";
import {
  demoProject,
  formatEok,
  formatPercent,
  generateCashFlowRows,
  getMaxMinusPoint,
} from "../../lib/platform-data";

type Tab = "tranche" | "scenario";
type Status = "safe" | "warn" | "danger";

export default function FinancePage() {
  const [tab, setTab] = useState<Tab>("tranche");

  return (
    <div className="space-y-6 pb-8">
      <div>
        <p className="text-sm font-semibold text-slate-500">{demoProject.id}</p>
        <h2 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
          <Layers className="h-7 w-7 text-indigo-600" />
          금융구조 분석
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          Tranche, LTV, LTC, DSCR, Max Minus, 미분양 회수 시나리오
        </p>
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="mb-5 flex flex-wrap gap-2 border-b border-slate-200 pb-4 dark:border-slate-800">
          <TabButton active={tab === "tranche"} onClick={() => setTab("tranche")}>
            Tranche 구조
          </TabButton>
          <TabButton active={tab === "scenario"} onClick={() => setTab("scenario")}>
            Max Minus / 미분양 LTV
          </TabButton>
        </div>
        {tab === "tranche" ? <TrancheTab /> : <ScenarioTab />}
      </div>
    </div>
  );
}

function TrancheTab() {
  const [senior, setSenior] = useState(630);
  const [mezz, setMezz] = useState(120);
  const [junior, setJunior] = useState(150);
  const [seniorRate, setSeniorRate] = useState(4.5);
  const [mezzRate, setMezzRate] = useState(6.5);
  const [juniorRate, setJuniorRate] = useState(8);

  const totalLoan = senior + mezz + junior;
  const collateralValue = demoProject.totalRevenue;
  const totalProjectCost = demoProject.totalCost;
  const ltv = (totalLoan / collateralValue) * 100;
  const ltc = (totalLoan / totalProjectCost) * 100;
  const annualInterest =
    (senior * seniorRate) / 100 +
    (mezz * mezzRate) / 100 +
    (junior * juniorRate) / 100;
  const annualOperatingCf = 165;
  const dscr = annualOperatingCf / Math.max(1, annualInterest);

  const waterfallData = [
    { name: "분양수입", cashIn: demoProject.totalRevenue, senior: 0, mezz: 0, junior: 0, equity: 0 },
    { name: "Senior 상환", cashIn: 0, senior, mezz: 0, junior: 0, equity: 0 },
    { name: "Mezz 상환", cashIn: 0, senior: 0, mezz, junior: 0, equity: 0 },
    { name: "Junior 상환", cashIn: 0, senior: 0, mezz: 0, junior, equity: 0 },
    {
      name: "Equity 배분",
      cashIn: 0,
      senior: 0,
      mezz: 0,
      junior: 0,
      equity: Math.max(0, demoProject.totalRevenue - totalLoan),
    },
  ];

  return (
    <div className="space-y-5">
      <section className="grid gap-4 md:grid-cols-3">
        <KpiCard
          label="LTV"
          value={formatPercent(ltv)}
          sub={`${formatEok(totalLoan, 0)} / 담보가치 ${formatEok(collateralValue)}`}
          status={ltv < 60 ? "safe" : ltv < 75 ? "warn" : "danger"}
        />
        <KpiCard
          label="LTC"
          value={formatPercent(ltc)}
          sub={`${formatEok(totalLoan, 0)} / 총사업비 ${formatEok(totalProjectCost)}`}
          status={ltc < 70 ? "safe" : ltc < 85 ? "warn" : "danger"}
        />
        <KpiCard
          label="DSCR"
          value={`${dscr.toFixed(2)}x`}
          sub={`연 CF ${formatEok(annualOperatingCf, 0)} / 이자 ${formatEok(annualInterest)}`}
          status={dscr >= 1.3 ? "safe" : dscr >= 1 ? "warn" : "danger"}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.85fr_1.15fr]">
        <div className="rounded-lg border border-slate-200 p-5 dark:border-slate-800">
          <h3 className="text-lg font-bold text-slate-950 dark:text-white">
            Tranche 입력
          </h3>
          <div className="mt-5 space-y-5">
            <LoanInput
              label="Senior Loan"
              value={senior}
              onChange={setSenior}
              rate={seniorRate}
              onRateChange={setSeniorRate}
              color="bg-sky-600"
            />
            <LoanInput
              label="Mezzanine Loan"
              value={mezz}
              onChange={setMezz}
              rate={mezzRate}
              onRateChange={setMezzRate}
              color="bg-indigo-600"
            />
            <LoanInput
              label="Junior Loan"
              value={junior}
              onChange={setJunior}
              rate={juniorRate}
              onRateChange={setJuniorRate}
              color="bg-violet-600"
            />
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 p-5 dark:border-slate-800">
          <h3 className="text-lg font-bold text-slate-950 dark:text-white">
            상환 Waterfall
          </h3>
          <div className="mt-4 h-[340px]">
            <ClientChart>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={waterfallData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip
                    formatter={(value) => [formatEok(Number(value)), ""]}
                    contentStyle={{
                      border: "1px solid #e2e8f0",
                      borderRadius: 8,
                      boxShadow: "0 10px 24px rgba(15,23,42,.12)",
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="cashIn" name="Cash In" fill="#10b981" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="senior" name="Senior" fill="#0284c7" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="mezz" name="Mezz" fill="#4f46e5" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="junior" name="Junior" fill="#7c3aed" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="equity" name="Equity" fill="#f59e0b" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </ClientChart>
          </div>
        </div>
      </section>
    </div>
  );
}

function ScenarioTab() {
  const [discount, setDiscount] = useState(20);
  const maxMinus = getMaxMinusPoint();

  const cfData = useMemo(() => {
    const row = generateCashFlowRows().find((item) => item.category === "Cumulative CF");
    return (row?.values ?? []).map((value, index) => ({
      month: `M${index + 1}`,
      cumulative: value,
    }));
  }, []);

  const scenarios = [50, 70, 90, 100].map((salesRate) => {
    const soldRevenue = demoProject.totalRevenue * (salesRate / 100);
    const unsoldValue =
      demoProject.totalRevenue * ((100 - salesRate) / 100) * (1 - discount / 100);
    const totalRecovery = soldRevenue + unsoldValue;
    return {
      salesRate,
      soldRevenue,
      unsoldValue,
      totalRecovery,
      seniorRecovery: Math.min(630, totalRecovery),
      mezzRecovery: Math.min(120, Math.max(0, totalRecovery - 630)),
      juniorRecovery: Math.min(150, Math.max(0, totalRecovery - 750)),
    };
  });

  const unsoldCollateral = demoProject.totalRevenue * (1 - discount / 100);
  const unsoldLtv = (demoProject.totalLoan / unsoldCollateral) * 100;

  return (
    <div className="space-y-5">
      <section className="grid gap-4 md:grid-cols-3">
        <KpiCard
          label="Cash Flow Max Minus"
          value={formatEok(Math.abs(maxMinus.value))}
          sub={`${maxMinus.month} 최저 누적 CF`}
          status="warn"
        />
        <KpiCard
          label="미분양 처분가 LTV"
          value={formatPercent(unsoldLtv)}
          sub={`할인율 ${formatPercent(discount, 0)} 적용`}
          status={unsoldLtv < 65 ? "safe" : unsoldLtv < 80 ? "warn" : "danger"}
        />
        <KpiCard
          label="Exit 분양률"
          value="67.0%"
          sub="Tr.B 원리금 회수 기준"
          status="safe"
        />
      </section>

      <section className="rounded-lg border border-slate-200 p-5 dark:border-slate-800">
        <h3 className="text-lg font-bold text-slate-950 dark:text-white">
          누적 현금흐름과 Max Minus
        </h3>
        <div className="mt-4 h-[300px]">
          <ClientChart>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={cfData}>
                <defs>
                  <linearGradient id="cfGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#4f46e5" stopOpacity={0.28} />
                    <stop offset="95%" stopColor="#4f46e5" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip
                  formatter={(value) => [formatEok(Number(value)), "누적 CF"]}
                  contentStyle={{
                    border: "1px solid #e2e8f0",
                    borderRadius: 8,
                    boxShadow: "0 10px 24px rgba(15,23,42,.12)",
                  }}
                />
                <ReferenceLine y={0} stroke="#94a3b8" strokeDasharray="4 4" />
                <ReferenceLine
                  x={maxMinus.month}
                  stroke="#ef4444"
                  strokeDasharray="4 4"
                  label={{
                    value: `Max Minus ${formatEok(Math.abs(maxMinus.value), 0)}`,
                    fill: "#ef4444",
                    fontSize: 11,
                    position: "insideTopRight",
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="cumulative"
                  name="누적 CF"
                  stroke="#4f46e5"
                  fill="url(#cfGradient)"
                  strokeWidth={2}
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </ClientChart>
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 p-5 dark:border-slate-800">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="text-lg font-bold text-slate-950 dark:text-white">
              분양률별 회수 시나리오
            </h3>
            <p className="text-sm text-slate-500">단위: 억원</p>
          </div>
          <label className="flex items-center gap-2 text-sm font-semibold text-slate-600 dark:text-slate-300">
            미분양 할인율
            <input
              type="number"
              min={0}
              max={50}
              value={discount}
              onChange={(event) => setDiscount(Number(event.target.value))}
              className="h-9 w-20 rounded-lg border border-slate-200 bg-slate-50 px-2 text-right dark:border-slate-700 dark:bg-slate-950"
            />
            %
          </label>
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse text-right text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-950/60">
                <th className="p-3 text-left font-bold">분양률</th>
                <th className="p-3 font-bold">분양수입</th>
                <th className="p-3 font-bold">미분양 처분가</th>
                <th className="p-3 font-bold">총 회수액</th>
                <th className="p-3 font-bold text-sky-600">Senior 회수</th>
                <th className="p-3 font-bold text-indigo-600">Mezz 회수</th>
                <th className="p-3 font-bold text-violet-600">Junior 회수</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {scenarios.map((scenario) => (
                <tr key={scenario.salesRate}>
                  <td className="p-3 text-left font-black text-slate-950 dark:text-white">
                    {scenario.salesRate}%
                  </td>
                  <td className="p-3">{formatEok(scenario.soldRevenue)}</td>
                  <td className="p-3 text-rose-600">{formatEok(scenario.unsoldValue)}</td>
                  <td className="p-3 font-bold text-slate-950 dark:text-white">
                    {formatEok(scenario.totalRecovery)}
                  </td>
                  <td className="p-3 font-bold text-sky-600">
                    {formatEok(scenario.seniorRecovery)}
                  </td>
                  <td className="p-3 font-bold text-indigo-600">
                    {formatEok(scenario.mezzRecovery)}
                  </td>
                  <td className="p-3 font-bold text-violet-600">
                    {formatEok(scenario.juniorRecovery)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function ClientChart({ children }: { children: ReactNode }) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return <div className="h-full w-full animate-pulse rounded-lg bg-slate-100 dark:bg-slate-800" />;
  }

  return children;
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg px-4 py-2 text-sm font-bold transition-colors ${
        active
          ? "bg-slate-950 text-white dark:bg-white dark:text-slate-950"
          : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
      }`}
    >
      {children}
    </button>
  );
}

function KpiCard({
  label,
  value,
  sub,
  status,
}: {
  label: string;
  value: string;
  sub: string;
  status: Status;
}) {
  const tone = {
    safe: "text-emerald-600 dark:text-emerald-300",
    warn: "text-amber-600 dark:text-amber-300",
    danger: "text-rose-600 dark:text-rose-300",
  }[status];
  const Icon = status === "safe" ? CheckCircle2 : status === "warn" ? Info : AlertTriangle;

  return (
    <div className="rounded-lg border border-slate-200 p-5 dark:border-slate-800">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-slate-500">{label}</p>
        <Icon className={`h-5 w-5 ${tone}`} />
      </div>
      <p className={`mt-4 text-2xl font-black ${tone}`}>{value}</p>
      <p className="mt-1 text-sm text-slate-500">{sub}</p>
    </div>
  );
}

function LoanInput({
  label,
  value,
  onChange,
  rate,
  onRateChange,
  color,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  rate: number;
  onRateChange: (value: number) => void;
  color: string;
}) {
  return (
    <div className="space-y-2 border-b border-slate-200 pb-4 last:border-b-0 last:pb-0 dark:border-slate-800">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-slate-500" />
          <p className="text-sm font-bold text-slate-800 dark:text-slate-200">{label}</p>
        </div>
        <p className="text-sm font-black text-slate-950 dark:text-white">
          {formatEok(value, 0)}
        </p>
      </div>
      <div className="h-1.5 rounded-full bg-slate-100 dark:bg-slate-800">
        <div className={`h-1.5 rounded-full ${color}`} style={{ width: `${value / 10}%` }} />
      </div>
      <input
        type="range"
        min={0}
        max={900}
        step={10}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full accent-indigo-600"
      />
      <label className="flex items-center gap-2 text-xs font-semibold text-slate-500">
        금리
        <input
          type="number"
          step={0.1}
          value={rate}
          onChange={(event) => onRateChange(Number(event.target.value))}
          className="h-8 w-20 rounded-lg border border-slate-200 bg-slate-50 px-2 text-right text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
        />
        %
      </label>
    </div>
  );
}
