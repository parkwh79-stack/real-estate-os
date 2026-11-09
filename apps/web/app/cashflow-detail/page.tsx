"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Calculator,
  CalendarDays,
  Download,
  FileSpreadsheet,
  Landmark,
  Loader2,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { formatEok } from "../../lib/platform-data";
import { ProjectPicker, getStoredSelectedProjectId } from "../../components/project/project-picker";
import { fetchLatestCashflow, type CashflowResult } from "../../lib/project-client";
import { cashFlowPeriodLabel } from "../../components/feasibility/cash-flow-period-basis";

const wonToEok = (v?: number): number => (typeof v === "number" ? v / 1e8 : 0);

export default function CashFlowDetailPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [cf, setCf] = useState<CashflowResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestVersion = useRef(0);

  const load = useCallback(async (id: string) => {
    const version = ++requestVersion.current;
    setSelectedId(id);
    setLoading(true);
    setLoaded(false);
    setError(null);
    setCf(null);
    try {
      const result = await fetchLatestCashflow(id);
      if (version === requestVersion.current) setCf(result);
    } catch (e) {
      if (version === requestVersion.current) setError(e instanceof Error ? e.message : "현금흐름 결과를 불러오지 못했습니다.");
    } finally {
      if (version === requestVersion.current) {
        setLoading(false);
        setLoaded(true);
      }
    }
  }, []);

  useEffect(() => {
    const stored = getStoredSelectedProjectId();
    if (stored) void load(stored);
    return () => { requestVersion.current += 1; };
  }, [load]);

  const s = cf?.summary ?? {};
  const sourceSequence = cf?.periodBasis?.kind === "source_sequence";

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            <CalendarDays className="h-7 w-7 text-indigo-600" />
            {sourceSequence ? "원본 순서별 Cash Flow" : "월별 Cash Flow"}
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            선택한 프로젝트의 PF 심사(현금흐름 엔진) 실행 결과에서 {sourceSequence ? "원본 순서별" : "월별"} 누적 현금흐름과 Max Minus를 표시합니다.
          </p>
        </div>
        <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
          <div className="w-72">
            <ProjectPicker value={selectedId} onChange={(id) => void load(id)} label="심사 프로젝트" />
          </div>
          <a
            href={selectedId ? `/api/projects/${encodeURIComponent(selectedId)}/cashflow/export` : undefined}
            aria-disabled={!selectedId || loading || !cf}
            onClick={(event) => { if (!selectedId || loading || !cf) event.preventDefault(); }}
            className="inline-flex h-11 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-800 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800"
          >
            <Download className="h-4 w-4" />
            프로젝트 Cash Flow Excel
          </a>
        </div>
      </div>

      {error ? <p className="text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      {!selectedId ? (
        <EmptyState
          title="심사 프로젝트를 선택하세요."
          body="월별 현금흐름은 프로젝트의 PF 심사 실행 결과에서 계산됩니다."
        />
      ) : loading ? (
        <div className="flex min-h-[240px] items-center justify-center rounded-lg border border-slate-200 text-sm text-slate-500 dark:border-slate-800">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" /> 현금흐름 결과 불러오는 중
        </div>
      ) : loaded && !cf ? (
        <EmptyState
          title="아직 PF 심사를 실행하지 않았습니다."
          body="이 프로젝트에는 현금흐름 엔진 실행 결과가 없습니다. 프로젝트의 오케스트레이션에서 PF 심사를 실행하면 실제 월별 현금흐름이 여기에 표시됩니다."
          action={
            selectedId ? (
              <Link
                href={`/project/${selectedId}/orchestration`}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950"
              >
                PF 심사 실행으로 이동
              </Link>
            ) : null
          }
        />
      ) : cf ? (
        <>
          <section className="grid gap-4 md:grid-cols-4">
            <KpiCard
              label="최대 자금부족 (Max Minus)"
              value={cf.maxMinusEok === null ? "미확인" : formatEok(Math.abs(cf.maxMinusEok), 0)}
              sub={cf.maxMinusPeriodIndex === null ? "발생 위치 미확인" : `${cashFlowPeriodLabel(cf.periodBasis, cf.maxMinusPeriodIndex)} 시점`}
              icon={<TrendingDown className="h-5 w-5 text-rose-600" />}
            />
            <KpiCard
              label="총매출"
              value={formatEok(wonToEok(s.totalRevenue), 0)}
              sub="사업수지 기준"
              icon={<TrendingUp className="h-5 w-5 text-emerald-600" />}
            />
            <KpiCard
              label="총사업비"
              value={formatEok(wonToEok(s.totalProjectCost), 0)}
              sub="사업수지 기준"
              icon={<Calculator className="h-5 w-5 text-sky-600" />}
            />
            <KpiCard
              label="필요 PF 한도"
              value={formatEok(wonToEok(s.requiredPfLimit))}
              sub={`PF 한도 ${formatEok(wonToEok(s.pfLimit), 0)}`}
              icon={<Landmark className="h-5 w-5 text-indigo-600" />}
            />
          </section>

          <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <div className="flex items-center justify-between gap-2 border-b border-slate-200 p-5 dark:border-slate-800">
              <div>
                <h3 className="flex items-center gap-2 text-lg font-bold text-slate-950 dark:text-white">
                  <FileSpreadsheet className="h-5 w-5 text-indigo-600" />
                  {sourceSequence ? "원본 순서별 현금흐름 상세" : "월별 현금흐름 상세"}
                </h3>
                <p className="text-sm text-slate-500">단위: 억원 · 실행 결과 {cf.monthly.length.toLocaleString("ko-KR")}개 계산 행{sourceSequence ? " · 달력·이자기간 미확인" : ""}</p>
              </div>
              <span className="shrink-0 rounded-md bg-emerald-50 px-3 py-1 text-xs font-bold text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">
                실제 실행 결과
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] border-collapse text-right text-sm">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-950/60">
                    <th className="sticky left-0 z-10 bg-slate-50 p-3 text-left font-bold dark:bg-slate-950">{sourceSequence ? "원본 순서" : "월"}</th>
                    <th className="p-3 font-bold">현금유입</th>
                    <th className="p-3 font-bold">현금유출</th>
                    <th className="p-3 font-bold">순현금흐름</th>
                    <th className="p-3 font-black text-indigo-700 dark:text-indigo-300">누적 CF</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {cf.monthly.map((m, index) => (
                    <tr key={m.period} className={cf.maxMinusPeriodIndex !== null && index === cf.maxMinusPeriodIndex ? "bg-rose-50/60 dark:bg-rose-950/20" : ""}>
                      <td className="sticky left-0 z-10 bg-inherit p-3 text-left font-bold text-slate-900 dark:text-white">{m.period}</td>
                      <td className="p-3 text-slate-700 dark:text-slate-300">{cell(m.cashInEok)}</td>
                      <td className="p-3 text-slate-700 dark:text-slate-300">{cell(m.cashOutEok)}</td>
                      <td className={`p-3 ${m.netCashFlowEok !== null && m.netCashFlowEok < 0 ? "text-rose-600 dark:text-rose-300" : "text-slate-700 dark:text-slate-300"}`}>
                        {cell(m.netCashFlowEok)}
                      </td>
                      <td className={`p-3 font-black ${m.cumulativeEok !== null && m.cumulativeEok < 0 ? "text-rose-600 dark:text-rose-300" : "text-slate-950 dark:text-white"}`}>
                        {cell(m.cumulativeEok)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}

function cell(value: number | null): string {
  if (value === null) return "미확인";
  return (Math.abs(value) < 0.5 ? 0 : value).toLocaleString("ko-KR", { maximumFractionDigits: 0 });
}

function EmptyState({ title, body, action }: { title: string; body: string; action?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 p-12 text-center dark:border-slate-700">
      <p className="text-base font-bold text-slate-950 dark:text-white">{title}</p>
      <p className="mx-auto mt-2 max-w-xl text-sm text-slate-500">{body}</p>
      {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
    </div>
  );
}

function KpiCard({ label, value, sub, icon }: { label: string; value: string; sub: string; icon: ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold text-slate-500">{label}</p>
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-50 dark:bg-slate-800">{icon}</div>
      </div>
      <p className="mt-4 text-2xl font-black text-slate-950 dark:text-white">{value}</p>
      <p className="mt-1 text-sm text-slate-500">{sub}</p>
    </div>
  );
}
