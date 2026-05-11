import Link from "next/link";
import type { ReactNode } from "react";
import {
  ArrowLeft,
  CalendarDays,
  Download,
  Landmark,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import {
  demoProject,
  formatEok,
  generateCashFlowRows,
  getMaxMinusPoint,
} from "../../lib/platform-data";

const months = Array.from({ length: 24 }, (_, index) => `M${index + 1}`);

export default function CashFlowDetailPage() {
  const rows = generateCashFlowRows();
  const maxMinus = getMaxMinusPoint();

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex items-start gap-3">
          <Link
            href="/feasibility"
            className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
            aria-label="사업성 분석으로 이동"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <p className="text-sm font-semibold text-slate-500">{demoProject.id}</p>
            <h2 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
              <CalendarDays className="h-7 w-7 text-indigo-600" />
              월별 현금흐름 스케줄
            </h2>
            <p className="mt-1 text-sm text-slate-500">{demoProject.name}</p>
          </div>
        </div>
        <a
          href="/api/export-cf"
          download="Platform9_CashFlow_Schedule.xlsx"
          className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
        >
          <Download className="h-4 w-4" />
          Excel 다운로드
        </a>
      </div>

      <section className="grid gap-4 md:grid-cols-3">
        <KpiCard
          label="최대 자금부족"
          value={formatEok(Math.abs(maxMinus.value))}
          sub={`${maxMinus.month} 기준`}
          icon={<TrendingDown className="h-5 w-5 text-rose-600" />}
        />
        <KpiCard
          label="PF 조달 필요액"
          value={formatEok(demoProject.totalLoan, 0)}
          sub="Tr.A/B/C 합산"
          icon={<Landmark className="h-5 w-5 text-indigo-600" />}
        />
        <KpiCard
          label="누적 순현금흐름"
          value={formatEok(
            rows.find((row) => row.category === "Cumulative CF")?.total ?? 0,
          )}
          sub="M24 종료 시점"
          icon={<TrendingUp className="h-5 w-5 text-emerald-600" />}
        />
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h3 className="text-lg font-bold text-slate-950 dark:text-white">
              자금 조달 구조
            </h3>
            <p className="text-sm text-slate-500">Equity 12% · Senior 70% · Mezz/Junior 18%</p>
          </div>
          <p className="text-sm font-semibold text-slate-500">
            총 필요자금 {formatEok(1453.1)}
          </p>
        </div>
        <div className="mt-5 flex h-10 overflow-hidden rounded-lg bg-slate-100 dark:bg-slate-800">
          <div className="flex items-center justify-center bg-emerald-600 text-xs font-black text-white" style={{ width: "12%" }}>
            Equity
          </div>
          <div className="flex items-center justify-center bg-sky-600 text-xs font-black text-white" style={{ width: "63%" }}>
            Senior
          </div>
          <div className="flex items-center justify-center bg-indigo-600 text-xs font-black text-white" style={{ width: "12%" }}>
            Mezz
          </div>
          <div className="flex items-center justify-center bg-violet-600 text-xs font-black text-white" style={{ width: "13%" }}>
            Junior
          </div>
        </div>
      </section>

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="border-b border-slate-200 p-5 dark:border-slate-800">
          <h3 className="text-lg font-bold text-slate-950 dark:text-white">
            월별 CF 상세
          </h3>
          <p className="text-sm text-slate-500">단위: 억원, VAT 제외</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1320px] border-collapse text-right text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-950/60">
                <th className="sticky left-0 z-10 min-w-[132px] border-r border-slate-200 bg-slate-50 p-3 text-left font-bold dark:border-slate-800 dark:bg-slate-950">
                  구분
                </th>
                <th className="sticky left-[132px] z-10 min-w-[170px] border-r border-slate-200 bg-slate-50 p-3 text-left font-bold dark:border-slate-800 dark:bg-slate-950">
                  항목
                </th>
                <th className="min-w-[104px] border-r border-slate-200 bg-indigo-50 p-3 font-black text-indigo-700 dark:border-slate-800 dark:bg-indigo-950/30 dark:text-indigo-300">
                  Total
                </th>
                {months.map((month) => (
                  <th key={month} className="min-w-[72px] p-3 font-bold">
                    {month}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {rows.map((row, rowIndex) => (
                <tr
                  key={`${row.category}-${row.subCategory}`}
                  className={`${row.isTotal ? "bg-slate-50/80 font-bold dark:bg-slate-950/40" : ""}`}
                >
                  <td className="sticky left-0 z-10 border-r border-slate-200 bg-inherit p-3 text-left text-slate-700 dark:border-slate-800 dark:text-slate-300">
                    <span
                      className={
                        rowIndex > 0 && rows[rowIndex - 1]?.category === row.category
                          ? "opacity-0"
                          : ""
                      }
                    >
                      {row.category}
                    </span>
                  </td>
                  <td className="sticky left-[132px] z-10 border-r border-slate-200 bg-inherit p-3 text-left text-slate-700 dark:border-slate-800 dark:text-slate-300">
                    {row.subCategory}
                  </td>
                  <td
                    className={`border-r border-slate-200 bg-indigo-50/60 p-3 font-black dark:border-slate-800 dark:bg-indigo-950/20 ${
                      row.isNegative ? "text-rose-600" : "text-slate-950 dark:text-white"
                    }`}
                  >
                    {formatCell(row.total, row.isNegative)}
                  </td>
                  {row.values.map((value, index) => {
                    const isNegative = row.isNegative || value < 0;
                    return (
                      <td
                        key={`${row.subCategory}-${months[index]}`}
                        className={`p-3 ${
                          value === 0
                            ? "text-slate-300 dark:text-slate-700"
                            : isNegative
                              ? "text-rose-600 dark:text-rose-300"
                              : "text-slate-700 dark:text-slate-300"
                        }`}
                      >
                        {formatCell(value, row.isNegative)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function KpiCard({
  label,
  value,
  sub,
  icon,
}: {
  label: string;
  value: string;
  sub: string;
  icon: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-slate-500">{label}</p>
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-50 dark:bg-slate-800">
          {icon}
        </div>
      </div>
      <p className="mt-4 text-2xl font-black text-slate-950 dark:text-white">{value}</p>
      <p className="mt-1 text-sm text-slate-500">{sub}</p>
    </div>
  );
}

function formatCell(value: number, forceNegative = false) {
  if (value === 0) {
    return "-";
  }

  const prefix = forceNegative && value > 0 ? "-" : "";
  return `${prefix}${Math.abs(value).toLocaleString("ko-KR", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })}`;
}
