"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import {
  Activity,
  ArrowRight,
  Calculator,
  CircleDollarSign,
  Percent,
  RotateCcw,
  TrendingUp,
} from "lucide-react";
import {
  calcIRR,
  formatEok,
  formatPercent,
} from "../../lib/platform-data";
import { ProjectPicker, getStoredSelectedProjectId } from "../../components/project/project-picker";
import { fetchProjectDetail } from "../../lib/project-client";

const colors = ["#2563eb", "#10b981", "#f59e0b", "#ef4444", "#7c3aed"];

interface FeasibilityDetail {
  project?: { id?: string; name?: string };
  feasibility_input?: {
    total_revenue?: number;
    land_cost?: number;
    construction_cost?: number;
    finance_cost?: number;
    other_cost?: number;
  };
}

const wonToEok = (v?: number): number => (typeof v === "number" ? Math.round((v / 1e8) * 10) / 10 : 0);

interface Seed {
  revenue: number;
  landCost: number;
  constructionCost: number;
  financeCost: number;
  otherCost: number;
}
const ZERO_SEED: Seed = { revenue: 0, landCost: 0, constructionCost: 0, financeCost: 0, otherCost: 0 };

export default function FeasibilityPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState<string | null>(null);
  const [projectId, setProjectId] = useState<string>("");
  const [seed, setSeed] = useState<Seed>(ZERO_SEED);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [revenue, setRevenue] = useState(0);
  const [landCost, setLandCost] = useState(0);
  const [constructionCost, setConstructionCost] = useState(0);
  const [financeCost, setFinanceCost] = useState(0);
  const [otherCost, setOtherCost] = useState(0);

  const applySeed = (s: Seed) => {
    setRevenue(s.revenue);
    setLandCost(s.landCost);
    setConstructionCost(s.constructionCost);
    setFinanceCost(s.financeCost);
    setOtherCost(s.otherCost);
  };

  const load = useCallback(async (id: string) => {
    setSelectedId(id);
    setLoadError(null);
    try {
      const d = await fetchProjectDetail<FeasibilityDetail>(id);
      const f = d.feasibility_input ?? {};
      const s: Seed = {
        revenue: wonToEok(f.total_revenue),
        landCost: wonToEok(f.land_cost),
        constructionCost: wonToEok(f.construction_cost),
        financeCost: wonToEok(f.finance_cost),
        otherCost: wonToEok(f.other_cost),
      };
      setSeed(s);
      applySeed(s);
      setProjectName(d.project?.name ?? null);
      setProjectId(d.project?.id ?? id);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "프로젝트를 불러오지 못했습니다.");
    }
  }, []);

  useEffect(() => {
    const stored = getStoredSelectedProjectId();
    if (stored) void load(stored);
  }, [load]);

  const totalCost = landCost + constructionCost + financeCost + otherCost;
  const profit = revenue - totalCost;
  const margin = revenue > 0 ? (profit / revenue) * 100 : 0;
  const equity = Math.max(1, totalCost * 0.12);

  const projectIrr = calcIRR([-totalCost, 0, revenue]);
  const equityIrr = calcIRR([-equity, 0, profit + equity]);

  const chartData = useMemo(
    () => [
      { name: "토지비", value: landCost },
      { name: "공사비", value: constructionCost },
      { name: "금융비용", value: financeCost },
      { name: "기타사업비", value: otherCost },
      { name: "사업이익", value: Math.max(0, profit) },
    ],
    [constructionCost, financeCost, landCost, otherCost, profit],
  );

  const reset = () => applySeed(seed);

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-500">{projectId || "프로젝트 미선택"}</p>
          <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            사업성 분석
          </h2>
          <p className="mt-1 text-sm text-slate-500">{projectName ?? "심사 프로젝트를 선택하면 사업수지 가정이 채워집니다."}</p>
          {loadError ? <p className="mt-1 text-sm font-semibold text-rose-600">{loadError}</p> : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="w-72">
            <ProjectPicker value={selectedId} onChange={(id) => void load(id)} label="프로젝트" />
          </div>
          <button
            type="button"
            onClick={reset}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            <RotateCcw className="h-4 w-4" />
            기본값
          </button>
          <Link
            href="/cashflow-detail"
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
          >
            <Calculator className="h-4 w-4" />
            CF 스케줄
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </div>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <KpiCard
          label="총매출"
          value={formatEok(revenue)}
          icon={<CircleDollarSign className="h-5 w-5 text-sky-600" />}
        />
        <KpiCard
          label="총사업비"
          value={formatEok(totalCost)}
          icon={<Calculator className="h-5 w-5 text-rose-600" />}
        />
        <KpiCard
          label="사업이익"
          value={formatEok(profit)}
          tone={profit >= 0 ? "good" : "bad"}
          icon={<TrendingUp className="h-5 w-5 text-emerald-600" />}
        />
        <KpiCard
          label="매출이익률"
          value={formatPercent(margin)}
          icon={<Percent className="h-5 w-5 text-indigo-600" />}
        />
        <KpiCard
          label="Equity IRR"
          value={equityIrr ? formatPercent(equityIrr * 100) : "N/A"}
          tone={equityIrr && equityIrr >= 0.15 ? "good" : "bad"}
          icon={<Activity className="h-5 w-5 text-violet-600" />}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.78fr_1.22fr]">
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <h3 className="text-lg font-bold text-slate-950 dark:text-white">
            주요 가정
          </h3>
          <div className="mt-5 space-y-4">
            <InputRow label="예상 총매출" value={revenue} onChange={setRevenue} />
            <InputRow
              label="토지비"
              value={landCost}
              onChange={setLandCost}
              note="거래사례 반영"
            />
            <InputRow
              label="공사비"
              value={constructionCost}
              onChange={setConstructionCost}
            />
            <InputRow
              label="PF 금융비용"
              value={financeCost}
              onChange={setFinanceCost}
            />
            <InputRow label="기타 사업비" value={otherCost} onChange={setOtherCost} />
          </div>

          <div className="mt-6 rounded-lg border border-sky-200 bg-sky-50 p-4 dark:border-sky-900 dark:bg-sky-950/30">
            <p className="text-sm font-bold text-sky-900 dark:text-sky-200">
              Agent memo
            </p>
            <p className="mt-2 text-sm leading-6 text-sky-800 dark:text-sky-300">
              현재 구조에서는 사업이익률 {formatPercent(margin)}가 기준입니다. 중순위
              참여 전제라면 토지비 민감도와 공사비 증액 여지를 별도 조건으로 묶는 편이
              안전합니다.
            </p>
          </div>
        </div>

        <div className="space-y-4">
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-lg font-bold text-slate-950 dark:text-white">
                  비용 및 이익 구성
                </h3>
                <p className="text-sm text-slate-500">
                  Project IRR {projectIrr ? formatPercent(projectIrr * 100) : "N/A"} ·
                  자기자본 {formatEok(equity)}
                </p>
              </div>
            </div>
            <div className="mt-4 h-[340px]">
              <ClientChart>
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={chartData}
                      cx="50%"
                      cy="47%"
                      innerRadius={84}
                      outerRadius={125}
                      paddingAngle={4}
                      dataKey="value"
                      stroke="none"
                    >
                      {chartData.map((entry, index) => (
                        <Cell key={entry.name} fill={colors[index % colors.length]} />
                      ))}
                    </Pie>
                    <Tooltip
                      formatter={(value) => [formatEok(Number(value)), "금액"]}
                      contentStyle={{
                        border: "1px solid #e2e8f0",
                        borderRadius: 8,
                        boxShadow: "0 10px 24px rgba(15,23,42,.12)",
                      }}
                    />
                    <Legend iconType="circle" />
                  </PieChart>
                </ResponsiveContainer>
              </ClientChart>
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-5">
            {chartData.map((item, index) => (
              <div
                key={item.name}
                className="rounded-lg border border-slate-200 bg-white p-4 text-center shadow-sm dark:border-slate-800 dark:bg-slate-900"
              >
                <p className="text-xs font-semibold text-slate-500">{item.name}</p>
                <p className="mt-1 text-lg font-black text-slate-950 dark:text-white">
                  {formatEok(item.value)}
                </p>
                <p
                  className="mt-1 text-xs font-bold"
                  style={{ color: colors[index % colors.length] }}
                >
                  {formatPercent(revenue > 0 ? (item.value / revenue) * 100 : 0)}
                </p>
              </div>
            ))}
          </div>
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

function KpiCard({
  label,
  value,
  icon,
  tone,
}: {
  label: string;
  value: string;
  icon: ReactNode;
  tone?: "good" | "bad";
}) {
  const valueColor =
    tone === "good"
      ? "text-emerald-600 dark:text-emerald-300"
      : tone === "bad"
        ? "text-rose-600 dark:text-rose-300"
        : "text-slate-950 dark:text-white";

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-slate-500">{label}</p>
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-50 dark:bg-slate-800">
          {icon}
        </div>
      </div>
      <p className={`mt-4 text-2xl font-black tracking-tight ${valueColor}`}>{value}</p>
    </div>
  );
}

function InputRow({
  label,
  value,
  onChange,
  note,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  note?: string;
}) {
  return (
    <label className="block">
      <div className="mb-1.5 flex items-center justify-between gap-3">
        <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">
          {label}
        </span>
        <span className="text-xs text-slate-400">{note ?? "단위 억원"}</span>
      </div>
      <input
        type="number"
        step="1"
        value={Number(value.toFixed(1))}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-10 w-full rounded-lg border border-slate-200 bg-slate-50 px-3 text-right text-sm font-semibold text-slate-950 outline-none focus:border-slate-400 dark:border-slate-800 dark:bg-slate-950 dark:text-white"
      />
    </label>
  );
}
