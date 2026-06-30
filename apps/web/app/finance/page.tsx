"use client";

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
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
  LineChart,
  ShieldCheck,
} from "lucide-react";
import { formatEok, formatPercent } from "../../lib/platform-data";
import { ProjectPicker, getStoredSelectedProjectId } from "../../components/project/project-picker";
import { fetchProjectDetail } from "../../lib/project-client";

type Tab = "tranche" | "scenario";
type Status = "safe" | "warn" | "danger";

interface ProjectDetail {
  project?: { id?: string; name?: string };
  feasibility_input?: { total_revenue?: number; total_cost?: number };
  expected_results?: { total_pf_amount?: number; development_profit?: number; profit_margin?: number };
  pf_structure?: Array<{ tranche?: string; amount?: number; interest_rate?: number; fee_rate?: number; priority?: number }>;
}

/** Real-project-derived financial seed (all monetary values in 억원). */
interface Fin {
  id: string;
  name: string;
  revenueEok: number;
  costEok: number;
  loanEok: number;
  profitEok: number;
  marginPct: number;
  senior: number;
  mezz: number;
  junior: number;
  seniorRate: number;
  mezzRate: number;
  juniorRate: number;
}

const wonToEok = (v?: number): number => (typeof v === "number" ? Math.round((v / 1e8) * 10) / 10 : 0);
const normRate = (r?: number): number => (typeof r === "number" ? (r < 1 ? r * 100 : r) : 0);

function deriveFin(d: ProjectDetail): Fin {
  const tr = [...(d.pf_structure ?? [])].sort((a, b) => (a.priority ?? 99) - (b.priority ?? 99));
  const amt = (i: number) => wonToEok(tr[i]?.amount);
  return {
    id: d.project?.id ?? "",
    name: d.project?.name ?? "프로젝트",
    revenueEok: wonToEok(d.feasibility_input?.total_revenue),
    costEok: wonToEok(d.feasibility_input?.total_cost),
    loanEok: wonToEok(d.expected_results?.total_pf_amount),
    profitEok: wonToEok(d.expected_results?.development_profit),
    marginPct: normRate(d.expected_results?.profit_margin),
    senior: amt(0),
    mezz: amt(1),
    junior: amt(2),
    seniorRate: normRate(tr[0]?.interest_rate) || 4.5,
    mezzRate: normRate(tr[1]?.interest_rate) || 6.5,
    juniorRate: normRate(tr[2]?.interest_rate) || 8,
  };
}

export default function FinancePage() {
  const [tab, setTab] = useState<Tab>("tranche");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [fin, setFin] = useState<Fin | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (id: string) => {
    setSelectedId(id);
    setError(null);
    try {
      const detail = await fetchProjectDetail<ProjectDetail>(id);
      setFin(deriveFin(detail));
    } catch (e) {
      setError(e instanceof Error ? e.message : "프로젝트를 불러오지 못했습니다.");
    }
  }, []);

  useEffect(() => {
    const stored = getStoredSelectedProjectId();
    if (stored) void load(stored);
  }, [load]);

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-500">{fin ? `${fin.name}${fin.id ? ` · ${fin.id}` : ""}` : "금융구조 분석"}</p>
          <h2 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            <Layers className="h-7 w-7 text-indigo-600" />
            금융구조 분석
          </h2>
          <p className="mt-1 text-sm text-slate-500">Tranche, LTV, LTC, 미분양 회수 시나리오 — 선택한 심사 프로젝트 기준</p>
        </div>
        <div className="sm:w-80">
          <ProjectPicker value={selectedId} onChange={(id) => void load(id)} label="심사 프로젝트" />
        </div>
      </div>

      {error ? <p className="text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      {!fin ? (
        <div className="rounded-lg border border-dashed border-slate-300 p-12 text-center dark:border-slate-700">
          <p className="text-base font-bold text-slate-950 dark:text-white">심사 프로젝트를 선택하세요.</p>
          <p className="mt-2 text-sm text-slate-500">
            선택한 프로젝트의 사업수지(매출·총사업비)와 PF 구조를 기준으로 LTV·LTC와 회수 시나리오를 계산합니다.
          </p>
        </div>
      ) : (
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-5 flex flex-wrap gap-2 border-b border-slate-200 pb-4 dark:border-slate-800">
            <TabButton active={tab === "tranche"} onClick={() => setTab("tranche")}>
              Tranche 구조
            </TabButton>
            <TabButton active={tab === "scenario"} onClick={() => setTab("scenario")}>
              미분양 회수 시나리오
            </TabButton>
          </div>
          {tab === "tranche" ? <TrancheTab fin={fin} /> : <ScenarioTab fin={fin} />}
        </div>
      )}
    </div>
  );
}

function TrancheTab({ fin }: { fin: Fin }) {
  const [senior, setSenior] = useState(fin.senior);
  const [mezz, setMezz] = useState(fin.mezz);
  const [junior, setJunior] = useState(fin.junior);
  const [seniorRate, setSeniorRate] = useState(fin.seniorRate);
  const [mezzRate, setMezzRate] = useState(fin.mezzRate);
  const [juniorRate, setJuniorRate] = useState(fin.juniorRate);

  // Reseed when the selected project changes.
  useEffect(() => {
    setSenior(fin.senior);
    setMezz(fin.mezz);
    setJunior(fin.junior);
    setSeniorRate(fin.seniorRate);
    setMezzRate(fin.mezzRate);
    setJuniorRate(fin.juniorRate);
  }, [fin]);

  const totalLoan = senior + mezz + junior;
  const collateralValue = fin.revenueEok;
  const totalProjectCost = fin.costEok;
  const ltv = collateralValue > 0 ? (totalLoan / collateralValue) * 100 : 0;
  const ltc = totalProjectCost > 0 ? (totalLoan / totalProjectCost) * 100 : 0;
  const sliderMax = Math.max(1000, Math.ceil((fin.loanEok || totalLoan || 100) * 1.5));

  const waterfallData = [
    { name: "분양수입", cashIn: fin.revenueEok, senior: 0, mezz: 0, junior: 0, equity: 0 },
    { name: "Senior 상환", cashIn: 0, senior, mezz: 0, junior: 0, equity: 0 },
    { name: "Mezz 상환", cashIn: 0, senior: 0, mezz, junior: 0, equity: 0 },
    { name: "Junior 상환", cashIn: 0, senior: 0, mezz: 0, junior, equity: 0 },
    { name: "Equity 배분", cashIn: 0, senior: 0, mezz: 0, junior: 0, equity: Math.max(0, fin.revenueEok - totalLoan) },
  ];

  return (
    <div className="space-y-5">
      <section className="grid gap-4 md:grid-cols-3">
        <KpiCard
          label="LTV"
          value={collateralValue > 0 ? formatPercent(ltv) : "—"}
          sub={`${formatEok(totalLoan, 0)} / 담보가치 ${collateralValue > 0 ? formatEok(collateralValue) : "—"}`}
          status={ltv < 60 ? "safe" : ltv < 75 ? "warn" : "danger"}
        />
        <KpiCard
          label="LTC"
          value={totalProjectCost > 0 ? formatPercent(ltc) : "—"}
          sub={`${formatEok(totalLoan, 0)} / 총사업비 ${totalProjectCost > 0 ? formatEok(totalProjectCost) : "—"}`}
          status={ltc < 70 ? "safe" : ltc < 85 ? "warn" : "danger"}
        />
        <KpiCard
          label="예상 개발이익"
          value={fin.profitEok ? formatEok(fin.profitEok) : "—"}
          sub={fin.marginPct ? `이익률 ${formatPercent(fin.marginPct)}` : "사업수지 기준"}
          status={fin.marginPct >= 15 ? "safe" : fin.marginPct >= 8 ? "warn" : "danger"}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.85fr_1.15fr]">
        <div className="rounded-lg border border-slate-200 p-5 dark:border-slate-800">
          <h3 className="text-lg font-bold text-slate-950 dark:text-white">Tranche 입력</h3>
          <p className="mt-1 text-xs text-slate-500">초기값은 선택한 프로젝트의 PF 구조에서 가져왔습니다. 슬라이더로 시뮬레이션할 수 있습니다.</p>
          <div className="mt-5 space-y-5">
            <LoanInput label="Senior Loan" value={senior} onChange={setSenior} rate={seniorRate} onRateChange={setSeniorRate} color="bg-sky-600" max={sliderMax} />
            <LoanInput label="Mezzanine Loan" value={mezz} onChange={setMezz} rate={mezzRate} onRateChange={setMezzRate} color="bg-indigo-600" max={sliderMax} />
            <LoanInput label="Junior Loan" value={junior} onChange={setJunior} rate={juniorRate} onRateChange={setJuniorRate} color="bg-violet-600" max={sliderMax} />
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 p-5 dark:border-slate-800">
          <h3 className="text-lg font-bold text-slate-950 dark:text-white">상환 Waterfall</h3>
          <div className="mt-4 h-[340px]">
            <ClientChart>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={waterfallData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip
                    formatter={(value) => [formatEok(Number(value)), ""]}
                    contentStyle={{ border: "1px solid #e2e8f0", borderRadius: 8, boxShadow: "0 10px 24px rgba(15,23,42,.12)" }}
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

function ScenarioTab({ fin }: { fin: Fin }) {
  const [discount, setDiscount] = useState(20);
  const senior = fin.senior;
  const seniorMezz = fin.senior + fin.mezz;

  const scenarios = [50, 70, 90, 100].map((salesRate) => {
    const soldRevenue = fin.revenueEok * (salesRate / 100);
    const unsoldValue = fin.revenueEok * ((100 - salesRate) / 100) * (1 - discount / 100);
    const totalRecovery = soldRevenue + unsoldValue;
    return {
      salesRate,
      soldRevenue,
      unsoldValue,
      totalRecovery,
      seniorRecovery: Math.min(senior, totalRecovery),
      mezzRecovery: Math.min(fin.mezz, Math.max(0, totalRecovery - senior)),
      juniorRecovery: Math.min(fin.junior, Math.max(0, totalRecovery - seniorMezz)),
    };
  });

  const unsoldCollateral = fin.revenueEok * (1 - discount / 100);
  const unsoldLtv = unsoldCollateral > 0 ? (fin.loanEok / unsoldCollateral) * 100 : 0;

  return (
    <div className="space-y-5">
      <section className="grid gap-4 md:grid-cols-3">
        <KpiCard label="총 PF 금액" value={fin.loanEok ? formatEok(fin.loanEok) : "—"} sub="사업수지 기준 PF 총액" status="warn" />
        <KpiCard
          label="미분양 처분가 LTV"
          value={unsoldCollateral > 0 ? formatPercent(unsoldLtv) : "—"}
          sub={`할인율 ${formatPercent(discount, 0)} 적용`}
          status={unsoldLtv < 65 ? "safe" : unsoldLtv < 80 ? "warn" : "danger"}
        />
        <KpiCard label="총사업비" value={fin.costEok ? formatEok(fin.costEok) : "—"} sub="사업수지 총원가" status="safe" />
      </section>

      <section className="rounded-lg border border-dashed border-slate-300 p-5 dark:border-slate-700">
        <div className="flex items-center gap-2">
          <LineChart className="h-5 w-5 text-slate-400" />
          <h3 className="text-lg font-bold text-slate-950 dark:text-white">월별 누적 현금흐름 · Max Minus</h3>
        </div>
        <p className="mt-2 text-sm text-slate-500">
          월별 현금흐름과 Max Minus는 저장된 입력만으로는 산출되지 않습니다. 프로젝트의 PF 심사(현금흐름 엔진)를 실행하면 실제 월별 누적 CF가 여기에 표시됩니다.
        </p>
      </section>

      <section className="rounded-lg border border-slate-200 p-5 dark:border-slate-800">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="text-lg font-bold text-slate-950 dark:text-white">분양률별 회수 시나리오</h3>
            <p className="text-sm text-slate-500">단위: 억원 · 회수 우선순위는 선택 프로젝트의 Tranche 금액 기준</p>
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
                  <td className="p-3 text-left font-black text-slate-950 dark:text-white">{scenario.salesRate}%</td>
                  <td className="p-3">{formatEok(scenario.soldRevenue)}</td>
                  <td className="p-3 text-rose-600">{formatEok(scenario.unsoldValue)}</td>
                  <td className="p-3 font-bold text-slate-950 dark:text-white">{formatEok(scenario.totalRecovery)}</td>
                  <td className="p-3 font-bold text-sky-600">{formatEok(scenario.seniorRecovery)}</td>
                  <td className="p-3 font-bold text-indigo-600">{formatEok(scenario.mezzRecovery)}</td>
                  <td className="p-3 font-bold text-violet-600">{formatEok(scenario.juniorRecovery)}</td>
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

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg px-4 py-2 text-sm font-bold transition-colors ${
        active ? "bg-slate-950 text-white dark:bg-white dark:text-slate-950" : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
      }`}
    >
      {children}
    </button>
  );
}

function KpiCard({ label, value, sub, status }: { label: string; value: string; sub: string; status: Status }) {
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
  max,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  rate: number;
  onRateChange: (value: number) => void;
  color: string;
  max: number;
}) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="space-y-2 border-b border-slate-200 pb-4 last:border-b-0 last:pb-0 dark:border-slate-800">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-slate-500" />
          <p className="text-sm font-bold text-slate-800 dark:text-slate-200">{label}</p>
        </div>
        <p className="text-sm font-black text-slate-950 dark:text-white">{formatEok(value, 0)}</p>
      </div>
      <div className="h-1.5 rounded-full bg-slate-100 dark:bg-slate-800">
        <div className={`h-1.5 rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <input
        type="range"
        min={0}
        max={max}
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
