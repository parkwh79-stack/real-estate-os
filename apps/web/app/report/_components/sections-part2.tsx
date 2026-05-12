import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { Section, Th, Td } from "./sections-part1";
import { formatEok, formatPercent } from "../../../lib/platform-data";
import {
  salesRevenueDistribution, summaryCashFlow,
  borrowerAnalysis, contractorAnalysis, marketAnalysis,
  feasibilityAnalysis, exitStrategy, drawdownConditionsFull,
  eodTriggers, fundExecutionOrder,
} from "../../../lib/report-data";

/* ── XI. 분양수입금 배분 ── */
export function SectionSalesDistribution() {
  return (
    <Section id="sec-11" title="XI. 분양수입금 배분 순서">
      <div className="space-y-2">
        {salesRevenueDistribution.map(r => (
          <div key={r.rank} className="flex items-center gap-3 rounded-lg border border-slate-200 p-3 dark:border-slate-800">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-xs font-black text-white">
              {r.rank}
            </span>
            <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{r.item}</p>
          </div>
        ))}
      </div>
    </Section>
  );
}

/* ── XII. 요약 현금흐름표 ── */
export function SectionSummaryCF() {
  return (
    <Section id="sec-12" title="XII. 요약 현금흐름표">
      <p className="mb-3 text-xs text-slate-500">단위: 억원, 분양률 가정 기반</p>
      <table className="report-table w-full border-collapse text-sm">
        <thead>
          <tr><Th>구간</Th><Th>Cash-In</Th><Th>Cash-Out</Th><Th>Net CF</Th><Th>누적 CF</Th></tr>
        </thead>
        <tbody>
          {summaryCashFlow.map(r => (
            <tr key={r.month}>
              <Td className="font-bold">{r.month}</Td>
              <Td>{formatEok(r.in, 0)}</Td>
              <Td>{formatEok(r.out, 0)}</Td>
              <Td className={r.net < 0 ? "text-rose-600 font-bold" : "font-bold"}>{formatEok(r.net, 0)}</Td>
              <Td className={r.cum < 0 ? "text-rose-600 font-black" : "font-black text-emerald-700 dark:text-emerald-300"}>{formatEok(r.cum, 0)}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

/* ── XIII. 차주 분석 ── */
export function SectionBorrower() {
  return (
    <Section id="sec-13" title="XIII. 차주(시행사) 분석">
      <div className="rounded-lg border border-slate-200 p-5 dark:border-slate-800">
        <h3 className="text-base font-black text-slate-950 dark:text-white">{borrowerAnalysis.name}</h3>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          {[
            ["설립일", borrowerAnalysis.established],
            ["신용등급", borrowerAnalysis.creditRating],
            ["재무현황", borrowerAnalysis.financials],
          ].map(([l, v]) => (
            <div key={l} className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900">
              <p className="text-xs font-semibold text-slate-500">{l}</p>
              <p className="mt-1 text-sm font-bold text-slate-950 dark:text-white">{v}</p>
            </div>
          ))}
        </div>
      </div>
    </Section>
  );
}

/* ── XIV. 시공사 분석 ── */
export function SectionContractor() {
  return (
    <Section id="sec-14" title="XIV. 시공사 분석">
      <div className="rounded-lg border border-slate-200 p-5 dark:border-slate-800">
        <h3 className="text-base font-black text-slate-950 dark:text-white">{contractorAnalysis.name}</h3>
        <div className="mt-4 grid gap-3 md:grid-cols-4">
          {[
            ["신용등급", contractorAnalysis.creditRating],
            ["수주잔고", contractorAnalysis.orderBacklog],
            ["재무비율", contractorAnalysis.financials],
          ].map(([l, v]) => (
            <div key={l} className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900">
              <p className="text-xs font-semibold text-slate-500">{l}</p>
              <p className="mt-1 text-sm font-bold text-slate-950 dark:text-white">{v}</p>
            </div>
          ))}
        </div>
      </div>
    </Section>
  );
}

/* ── XV. 분양성 분석 ── */
export function SectionMarket() {
  return (
    <Section id="sec-15" title="XV. 분양성 분석">
      <div className="grid gap-3 md:grid-cols-3">
        {[
          ["입지여건", marketAnalysis.location],
          ["경쟁단지", marketAnalysis.competitors],
          ["분양가 적정성", marketAnalysis.priceReasonableness],
        ].map(([l, v]) => (
          <div key={l} className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
            <p className="text-sm font-black text-slate-950 dark:text-white">{l}</p>
            <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-400">{v}</p>
          </div>
        ))}
      </div>
    </Section>
  );
}

/* ── XVI. 사업성 분석 ── */
export function SectionFeasibility() {
  return (
    <Section id="sec-16" title="XVI. 사업성 분석">
      <div className="grid gap-4 md:grid-cols-3">
        {[
          ["LTV", formatPercent(feasibilityAnalysis.ltv), feasibilityAnalysis.ltv < 70],
          ["Exit 분양률", formatPercent(feasibilityAnalysis.exitSalesRate), true],
          ["분양가 LTV", formatPercent(feasibilityAnalysis.priceLtv), feasibilityAnalysis.priceLtv < 70],
        ].map(([label, value, safe]) => (
          <div key={label as string} className="rounded-lg border border-slate-200 p-4 text-center dark:border-slate-800">
            <p className="text-xs font-semibold text-slate-500">{label as string}</p>
            <p className={`mt-2 text-2xl font-black ${safe ? "text-emerald-600" : "text-amber-600"}`}>{value as string}</p>
            <div className="mt-2 flex items-center justify-center gap-1">
              {safe ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <AlertTriangle className="h-4 w-4 text-amber-600" />}
              <span className="text-xs text-slate-500">{safe ? "기준 이내" : "주의 필요"}</span>
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

/* ── XVII. Exit 전략 및 리스크 ── */
export function SectionExitStrategy() {
  return (
    <Section id="sec-17" title="XVII. Exit 전략 및 리스크">
      <div className="space-y-3">
        {[
          ["시나리오 1 — 할인분양", exitStrategy.scenario1],
          ["시나리오 2 — 대물변제/임대매각", exitStrategy.scenario2],
        ].map(([t, b]) => (
          <div key={t} className="rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/30">
            <div className="flex gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <div>
                <p className="text-sm font-black text-slate-950 dark:text-white">{t}</p>
                <p className="mt-1 text-sm leading-6 text-slate-700 dark:text-slate-300">{b}</p>
              </div>
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

/* ── XVIII. 인출 선행/후행조건 ── */
export function SectionDrawdownConditions() {
  return (
    <Section id="sec-18" title="XVIII. 인출 선행/후행 조건">
      <div className="space-y-2">
        {drawdownConditionsFull.map(c => (
          <div key={c} className="flex gap-2 text-sm">
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
            <span className="text-slate-700 dark:text-slate-300">{c}</span>
          </div>
        ))}
      </div>
    </Section>
  );
}

/* ── XIX. EOD (채무불이행 사유) ── */
export function SectionEOD() {
  return (
    <Section id="sec-19" title="XIX. 채무불이행 사유 (EOD)">
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <h3 className="mb-2 flex items-center gap-2 text-sm font-black text-rose-700 dark:text-rose-300">
            <XCircle className="h-4 w-4" /> 당연 기한이익상실
          </h3>
          <div className="space-y-2">
            {eodTriggers.automatic.map(t => (
              <div key={t} className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">
                {t}
              </div>
            ))}
          </div>
        </div>
        <div>
          <h3 className="mb-2 flex items-center gap-2 text-sm font-black text-amber-700 dark:text-amber-300">
            <AlertTriangle className="h-4 w-4" /> 선택적 기한이익상실
          </h3>
          <div className="space-y-2">
            {eodTriggers.optional.map(t => (
              <div key={t} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                {t}
              </div>
            ))}
          </div>
        </div>
      </div>
    </Section>
  );
}

/* ── XX. 자금집행 순서 및 변제 충당 순위 ── */
export function SectionFundExecution() {
  return (
    <Section id="sec-20" title="XX. 자금집행 순서 및 변제 충당 순위">
      <div className="space-y-2">
        {fundExecutionOrder.map((item, i) => (
          <div key={item} className="flex items-center gap-3 rounded-lg border border-slate-200 p-3 dark:border-slate-800">
            <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-black text-white ${i < 2 ? "bg-rose-600" : i < 4 ? "bg-amber-600" : "bg-slate-600"}`}>
              {i + 1}
            </span>
            <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{item.replace(/^\d+순위:\s*/, "")}</p>
          </div>
        ))}
      </div>
    </Section>
  );
}
