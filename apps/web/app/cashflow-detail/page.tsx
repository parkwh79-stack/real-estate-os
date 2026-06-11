import Link from "next/link";
import type { ReactNode } from "react";
import {
  ArrowLeft,
  Calculator,
  CalendarDays,
  Download,
  FileSpreadsheet,
  Landmark,
  ListChecks,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import {
  calculationTraceSteps,
  demoProject,
  excelMirrorSheets,
  formatEok,
  generateCashFlowRows,
  getMaxMinusPoint,
} from "../../lib/platform-data";

const months = Array.from({ length: 24 }, (_, index) => `M${index + 1}`);

export default function CashFlowDetailPage() {
  const rows = generateCashFlowRows();
  const maxMinus = getMaxMinusPoint();
  const cumulativeEnd = rows.find((row) => row.category === "Cumulative CF")?.total ?? 0;
  const totalCashIn = rows.find((row) => row.subCategory === "수입 합계")?.total ?? 0;
  const totalCashOut = rows.find((row) => row.subCategory === "지출 합계")?.total ?? 0;

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
              월별 Cash Flow 및 계산근거
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              실제 본리동 사업수지 Excel의 입력, 분양, 사업수지, 현금흐름 구조를 기준으로 계산 경로를 표시합니다.
            </p>
          </div>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <a
            href="/api/export-risk-excel"
            download="REALYTICS_Risk_Report_Compact.xlsx"
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-800 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800"
          >
            <FileSpreadsheet className="h-4 w-4" />
            리스크 보고서 첨부용 Excel
          </a>
          <a
            href="/api/export-cf"
            download="REALYTICS_Formula_CashFlow_Model.xlsx"
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
          >
            <Download className="h-4 w-4" />
            수식 검증형 Excel 다운로드
          </a>
        </div>
      </div>

      <section className="grid gap-4 md:grid-cols-4">
        <KpiCard
          label="최대 자금부족"
          value={formatEok(Math.abs(maxMinus.value))}
          sub={`${maxMinus.month} 기준 Max Minus`}
          icon={<TrendingDown className="h-5 w-5 text-rose-600" />}
        />
        <KpiCard
          label="PF 대출금"
          value={formatEok(demoProject.totalLoan, 0)}
          sub="Tr.A/B/C 합산"
          icon={<Landmark className="h-5 w-5 text-indigo-600" />}
        />
        <KpiCard
          label="누적 현금흐름"
          value={formatEok(cumulativeEnd)}
          sub="표시 기간 종료 시점"
          icon={<TrendingUp className="h-5 w-5 text-emerald-600" />}
        />
        <KpiCard
          label="수입/지출 대사"
          value={`${formatEok(totalCashIn)} / ${formatEok(Math.abs(totalCashOut))}`}
          sub="월별 수입합계 / 지출합계"
          icon={<Calculator className="h-5 w-5 text-sky-600" />}
        />
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div>
            <h3 className="flex items-center gap-2 text-lg font-bold text-slate-950 dark:text-white">
              <FileSpreadsheet className="h-5 w-5 text-indigo-600" />
              실제 Excel 구조 반영 현황
            </h3>
            <p className="text-sm text-slate-500">
              다운로드 파일에는 원본형 `입력`, `분양`, `사업수지`, `2.현금흐름` 탭과 검증용 REALYTICS 탭이 함께 들어갑니다.
            </p>
          </div>
          <span className="rounded-md bg-emerald-50 px-3 py-1 text-xs font-bold text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">
            샘플 기반 수식 대사 가능
          </span>
        </div>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[900px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-left text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-950/60">
                <th className="p-3 font-bold">Excel 탭</th>
                <th className="p-3 font-bold">역할</th>
                <th className="p-3 font-bold">샘플 원천</th>
                <th className="p-3 font-bold">웹앱 계산 목적</th>
                <th className="p-3 font-bold">상태</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {excelMirrorSheets.map((sheet) => (
                <tr key={sheet.sheetName}>
                  <td className="p-3 font-bold text-slate-950 dark:text-white">{sheet.sheetName}</td>
                  <td className="p-3 text-slate-700 dark:text-slate-300">{sheet.role}</td>
                  <td className="p-3 font-mono text-xs text-slate-600 dark:text-slate-400">{sheet.sourceRange}</td>
                  <td className="p-3 text-slate-700 dark:text-slate-300">{sheet.webPurpose}</td>
                  <td className="p-3">
                    <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                      {sheet.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div>
          <h3 className="flex items-center gap-2 text-lg font-bold text-slate-950 dark:text-white">
            <ListChecks className="h-5 w-5 text-indigo-600" />
            계산 흐름과 수식 근거
          </h3>
          <p className="text-sm text-slate-500">
            각 항목은 원본 Excel 셀, 다운로드 workbook 시트, 웹앱 산출물이 서로 연결되도록 설계되어 있습니다.
          </p>
        </div>
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          {calculationTraceSteps.map((step) => (
            <div
              key={step.id}
              className="rounded-lg border border-slate-200 p-4 dark:border-slate-800"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-black text-slate-950 dark:text-white">{step.label}</p>
                  <p className="mt-1 text-xs font-semibold text-indigo-600 dark:text-indigo-300">
                    {step.workbookSheet}
                  </p>
                </div>
                <span className="rounded-md bg-indigo-50 px-2 py-1 text-xs font-bold text-indigo-700 dark:bg-indigo-950/30 dark:text-indigo-300">
                  {step.webOutput}
                </span>
              </div>
              <dl className="mt-3 space-y-2 text-sm">
                <div>
                  <dt className="text-xs font-bold text-slate-500">원본 셀</dt>
                  <dd className="font-mono text-xs text-slate-700 dark:text-slate-300">{step.sampleSource}</dd>
                </div>
                <div>
                  <dt className="text-xs font-bold text-slate-500">수식</dt>
                  <dd className="font-mono text-xs text-slate-700 dark:text-slate-300">{step.formula}</dd>
                </div>
                <div>
                  <dt className="text-xs font-bold text-slate-500">해석</dt>
                  <dd className="text-slate-700 dark:text-slate-300">{step.explanation}</dd>
                </div>
              </dl>
            </div>
          ))}
        </div>
      </section>

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="border-b border-slate-200 p-5 dark:border-slate-800">
          <h3 className="text-lg font-bold text-slate-950 dark:text-white">
            월별 Cash Flow 상세
          </h3>
          <p className="text-sm text-slate-500">
            단위: 억원, VAT 제외. 실제 Excel에서는 45개월 전체와 수식 셀을 제공합니다.
          </p>
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
                  합계
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
      <div className="flex items-center justify-between gap-3">
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
