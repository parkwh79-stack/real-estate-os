"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { FileText, Info, Printer } from "lucide-react";
import { formatEok } from "../../lib/platform-data";
import { reportSectionOrder } from "../../lib/report-data";
import { ProjectPicker, getStoredSelectedProjectId } from "../../components/project/project-picker";
import { fetchProjectDetail } from "../../lib/project-client";
import {
  SectionOverallOpinion,
  SectionFinanceConditions,
  SectionBondPreservation,
  SectionDiscountTrigger,
  SectionFundingStructure,
  SectionFundUsage,
  SectionLandSecuring,
  SectionProjectSchedule,
  SectionFinancialStructureDiagram,
  SectionSalesPayment,
} from "./_components/sections-part1";
import {
  SectionSalesDistribution,
  SectionSummaryCF,
  SectionBorrower,
  SectionContractor,
  SectionMarket,
  SectionFeasibility,
  SectionExitStrategy,
  SectionDrawdownConditions,
  SectionEOD,
  SectionFundExecution,
} from "./_components/sections-part2";

const toc = reportSectionOrder.map((section) => ({
  id: `sec-${section.index}`,
  label: `${section.index}. ${section.title}`,
}));

const TYPE_LABEL: Record<string, string> = {
  mixed_use: "주상복합 개발",
  logistics: "물류센터 개발",
  residential: "공동주택 개발",
  office: "오피스 개발",
  lodging: "숙박시설 개발",
  land_feasibility: "토지 사업성",
};

interface ReportDetail {
  project?: { id?: string; name?: string; type?: string; financial_institution?: string; review_date?: string };
  land?: { address?: string };
  expected_results?: { total_pf_amount?: number };
}

const wonToEok = (v?: number): number => (typeof v === "number" ? v / 1e8 : 0);

export default function ReportPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [d, setD] = useState<ReportDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (id: string) => {
    setSelectedId(id);
    setError(null);
    try {
      setD(await fetchProjectDetail<ReportDetail>(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "프로젝트를 불러오지 못했습니다.");
    }
  }, []);

  useEffect(() => {
    const stored = getStoredSelectedProjectId();
    if (stored) void load(stored);
  }, [load]);

  const docNo = d?.project?.id ?? "—";
  const projectName = d?.project?.name ?? "프로젝트 미선택";
  const institution = d?.project?.financial_institution ?? "금융기관";
  const reviewDate = d?.project?.review_date ?? "—";
  const address = d?.land?.address ?? "—";
  const assetType = d?.project?.type ? TYPE_LABEL[d.project.type] ?? d.project.type : "—";
  const totalLoan = wonToEok(d?.expected_results?.total_pf_amount);

  return (
    <div className="space-y-5 pb-10">
      <div className="flex flex-col gap-4 print:hidden md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-500">{docNo}</p>
          <h2 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            <FileText className="h-7 w-7 text-indigo-600" />
            PF 심사 보고서
          </h2>
          <p className="mt-1 text-sm text-slate-500">선택한 프로젝트 기준 표지 · 20개 섹션 표준 양식</p>
        </div>
        <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
          <div className="w-72">
            <ProjectPicker value={selectedId} onChange={(id) => void load(id)} label="심사 프로젝트" />
          </div>
          <button
            type="button"
            onClick={() => window.print()}
            className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
          >
            <Printer className="h-4 w-4" />
            PDF 저장 / 인쇄
          </button>
        </div>
      </div>

      {error ? <p className="text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      {selectedId ? (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900 print:hidden dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
          <Info className="mt-0.5 h-4 w-4 shrink-0" />
          <p>
            표지는 선택한 프로젝트의 실제 정보입니다. 아래 본문 20개 섹션은 <b>표준 보고서 양식 예시</b>이며,
            이 프로젝트의 <b>실제 생성 여신심사보고서</b>는{" "}
            <Link href={`/project/${selectedId}/orchestration`} className="font-bold underline">
              PF 심사 실행 결과
            </Link>
            에서 생성·확인할 수 있습니다.
          </p>
        </div>
      ) : null}

      <article className="report-print-page mx-auto max-w-5xl overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm print:max-w-none print:border-0 print:shadow-none dark:border-slate-800 dark:bg-slate-900">
        <header className="bg-slate-950 p-8 text-white print:border-b-4 print:border-slate-950 print:bg-white print:text-slate-950">
          <div className="grid gap-6 md:grid-cols-[1fr_auto]">
            <div>
              <p className="text-sm font-semibold text-slate-400 print:text-slate-500">
                {institution}
              </p>
              <h1 className="mt-2 text-3xl font-black tracking-tight">투자심사 보고서</h1>
              <p className="mt-3 text-lg font-semibold text-slate-200 print:text-slate-700">
                {projectName}
              </p>
            </div>
            <div className="text-sm leading-7 text-slate-300 print:text-slate-600 md:text-right">
              <p>문서번호: {docNo}</p>
              <p>심의일: {reviewDate}</p>
              <p>작성: REALYTICS AI 심사팀</p>
            </div>
          </div>

          <div className="mt-7 grid gap-3 md:grid-cols-4">
            {[
              ["대상지", address],
              ["자산유형", assetType],
              ["검토금액", totalLoan ? formatEok(totalLoan, 0) : "—"],
              ["심의내용", d?.project ? "프로젝트 기준" : "—"],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg bg-white/10 p-3 print:border print:border-slate-200 print:bg-slate-50">
                <p className="text-xs text-slate-400 print:text-slate-500">{label}</p>
                <p className="mt-1 font-bold text-white print:text-slate-950">{value}</p>
              </div>
            ))}
          </div>
        </header>

        <div className="report-toc border-b border-slate-200 p-6 print:break-after-page dark:border-slate-800">
          <h2 className="mb-4 text-lg font-black text-slate-950 dark:text-white">목차</h2>
          <div className="grid gap-1 md:grid-cols-2">
            {toc.map((item) => (
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

        <div className="report-body space-y-8 p-6 md:p-8 print:p-6">
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

          <div className="approval-block flex justify-end border-t border-slate-200 pt-6 dark:border-slate-800">
            <table className="report-table border-collapse text-center text-xs">
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
