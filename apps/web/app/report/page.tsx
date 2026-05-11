"use client";

import { FileText, Printer } from "lucide-react";
import { demoProject, formatEok, tranches } from "../../lib/platform-data";
import { coverData } from "../../lib/report-data";
import {
  SectionOverallOpinion, SectionFinanceConditions, SectionBondPreservation,
  SectionDiscountTrigger, SectionFundingStructure, SectionFundUsage,
  SectionLandSecuring, SectionProjectSchedule, SectionFinancialStructureDiagram,
  SectionSalesPayment,
} from "./_components/sections-part1";
import {
  SectionSalesDistribution, SectionSummaryCF, SectionBorrower,
  SectionContractor, SectionMarket, SectionFeasibility,
  SectionExitStrategy, SectionDrawdownConditions, SectionEOD,
  SectionFundExecution,
} from "./_components/sections-part2";

const toc = [
  { id: "sec-1", label: "I. 종합 심사 의견" },
  { id: "sec-2", label: "II. 주요 금융조건 요약표" },
  { id: "sec-3", label: "III. 주요 채권보전방안" },
  { id: "sec-4", label: "IV. 할인분양 Trigger" },
  { id: "sec-5", label: "V. 자금조달 구조" },
  { id: "sec-6", label: "VI. 자금 사용 용도" },
  { id: "sec-7", label: "VII. 사업부지 확보" },
  { id: "sec-8", label: "VIII. 사업추진 일정" },
  { id: "sec-9", label: "IX. 금융구조도" },
  { id: "sec-10", label: "X. 분양대금 납입 일정" },
  { id: "sec-11", label: "XI. 분양수입금 배분" },
  { id: "sec-12", label: "XII. 요약 현금흐름표" },
  { id: "sec-13", label: "XIII. 차주 분석" },
  { id: "sec-14", label: "XIV. 시공사 분석" },
  { id: "sec-15", label: "XV. 분양성 분석" },
  { id: "sec-16", label: "XVI. 사업성 분석" },
  { id: "sec-17", label: "XVII. Exit 전략 및 리스크" },
  { id: "sec-18", label: "XVIII. 인출 선행/후행 조건" },
  { id: "sec-19", label: "XIX. EOD (채무불이행 사유)" },
  { id: "sec-20", label: "XX. 자금집행 및 변제 순위" },
];

export default function ReportPage() {
  const totalLoan = tranches.reduce((s, t) => s + t.amount, 0);

  return (
    <div className="space-y-5 pb-10">
      {/* ── 상단 액션바 (인쇄 시 숨김) ── */}
      <div className="flex flex-col gap-4 print:hidden md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-500">{demoProject.id}</p>
          <h2 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            <FileText className="h-7 w-7 text-indigo-600" />
            PF 심사 보고서
          </h2>
          <p className="mt-1 text-sm text-slate-500">하나금융투자 PF 심사보고서 양식 기반 (20개 섹션)</p>
        </div>
        <button
          type="button"
          onClick={() => window.print()}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
        >
          <Printer className="h-4 w-4" />
          PDF 저장 / 인쇄
        </button>
      </div>

      {/* ── 보고서 본문 ── */}
      <article className="mx-auto max-w-5xl overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm print:max-w-none print:border-0 print:shadow-none dark:border-slate-800 dark:bg-slate-900">
        {/* ── 표지 ── */}
        <header className="bg-slate-950 p-8 text-white print:border-b-4 print:border-slate-950 print:bg-white print:text-slate-950">
          <div className="grid gap-6 md:grid-cols-[1fr_auto]">
            <div>
              <p className="text-sm font-semibold text-slate-400 print:text-slate-500">
                {coverData.financialInstitution}
              </p>
              <h1 className="mt-2 text-3xl font-black tracking-tight">투자심사 보고서</h1>
              <p className="mt-3 text-lg font-semibold text-slate-200 print:text-slate-700">
                {coverData.projectName}
              </p>
              <p className="mt-1 text-sm text-slate-400 print:text-slate-500">{coverData.summary}</p>
            </div>
            <div className="text-sm leading-7 text-slate-300 print:text-slate-600 md:text-right">
              <p>문서번호: {demoProject.id}</p>
              <p>심의일: {coverData.reviewDate}</p>
              <p>작성: REALYTICS AI 심사역</p>
            </div>
          </div>

          <div className="mt-7 grid gap-3 md:grid-cols-4">
            {[
              ["대상지", demoProject.address],
              ["자산유형", demoProject.assetType],
              ["검토금액", formatEok(totalLoan, 0)],
              ["심의내용", "조건부 승인"],
            ].map(([l, v]) => (
              <div key={l} className="rounded-lg bg-white/10 p-3 print:border print:border-slate-200 print:bg-slate-50">
                <p className="text-xs text-slate-400 print:text-slate-500">{l}</p>
                <p className="mt-1 font-bold text-white print:text-slate-950">{v}</p>
              </div>
            ))}
          </div>
        </header>

        {/* ── 목차 ── */}
        <div className="border-b border-slate-200 p-6 print:break-after-page dark:border-slate-800">
          <h2 className="mb-4 text-lg font-black text-slate-950 dark:text-white">목차</h2>
          <div className="grid gap-1 md:grid-cols-2">
            {toc.map(item => (
              <a
                key={item.id}
                href={`#${item.id}`}
                className="rounded-lg px-3 py-2 text-sm font-semibold text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-950 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white"
              >
                {item.label}
              </a>
            ))}
          </div>
        </div>

        {/* ── 본문 섹션 ── */}
        <div className="space-y-8 p-6 md:p-8 print:p-6">
          <SectionOverallOpinion />
          <SectionFinanceConditions />
          <SectionBondPreservation />
          <SectionDiscountTrigger />
          <SectionFundingStructure />
          <SectionFundUsage />
          <SectionLandSecuring />
          <SectionProjectSchedule />
          <SectionFinancialStructureDiagram />
          <SectionSalesPayment />
          <SectionSalesDistribution />
          <SectionSummaryCF />
          <SectionBorrower />
          <SectionContractor />
          <SectionMarket />
          <SectionFeasibility />
          <SectionExitStrategy />
          <SectionDrawdownConditions />
          <SectionEOD />
          <SectionFundExecution />

          {/* ── 결재란 ── */}
          <div className="flex justify-end border-t border-slate-200 pt-6 dark:border-slate-800">
            <table className="border-collapse text-center text-xs">
              <tbody>
                <tr>
                  <th rowSpan={2} className="border border-slate-300 bg-slate-50 p-2">결재</th>
                  <th className="h-9 w-24 border border-slate-300 bg-slate-50 p-2">담당</th>
                  <th className="h-9 w-24 border border-slate-300 bg-slate-50 p-2">팀장</th>
                  <th className="h-9 w-24 border border-slate-300 bg-slate-50 p-2">위원장</th>
                </tr>
                <tr>
                  <td className="h-14 border border-slate-300 align-bottom">AI Agent</td>
                  <td className="h-14 border border-slate-300" />
                  <td className="h-14 border border-slate-300" />
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </article>
    </div>
  );
}
