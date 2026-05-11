import type { ReactNode } from "react";
import { CheckCircle2, Shield, Calendar, Building2 } from "lucide-react";
import {
  demoProject, formatEok, formatPercent, getMaxMinusPoint, tranches,
} from "../../../lib/platform-data";
import {
  financeConditions, bondPreservation, discountTrigger,
  fundingStructure, landSecuring, projectSchedule,
  financialStructureText, salesPaymentSchedule,
} from "../../../lib/report-data";

/* ── Shared helpers ── */
export function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-24 print:break-inside-avoid">
      <h2 className="mb-4 flex items-center gap-2 border-l-4 border-slate-950 pl-3 text-lg font-black text-slate-950 dark:border-white dark:text-white">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function Th({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <th className={`border border-slate-300 bg-slate-50 p-2.5 text-center text-xs font-bold dark:border-slate-700 dark:bg-slate-900 ${className}`}>{children}</th>;
}

export function Td({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <td className={`border border-slate-300 p-2.5 text-center text-xs text-slate-700 dark:border-slate-700 dark:text-slate-300 ${className}`}>{children}</td>;
}

/* ── I. 종합 심사 의견 ── */
export function SectionOverallOpinion() {
  const maxMinus = getMaxMinusPoint();
  const totalLoan = tranches.reduce((s, t) => s + t.amount, 0);
  const ltv = (totalLoan / demoProject.totalRevenue) * 100;
  const ltc = (totalLoan / demoProject.totalCost) * 100;
  return (
    <Section id="sec-1" title="I. 종합 심사 의견">
      <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-5 dark:border-emerald-900 dark:bg-emerald-950/30">
        <div className="flex gap-3">
          <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
          <div>
            <p className="font-black text-emerald-800 dark:text-emerald-200">종합판정: 조건부 취급 적격</p>
            <p className="mt-2 text-sm leading-7 text-slate-700 dark:text-slate-300">
              본 건은 담보가치 대비 LTV {formatPercent(ltv)}, 총사업비 대비 LTC {formatPercent(ltc)}로
              Alpha 기준 내에 위치합니다. Max Minus {formatEok(Math.abs(maxMinus.value))} 구간이
              있어 분양관리, 공사비 증액 통제, 할인분양 Trigger를 약정서에 명시하는 조건으로
              취급하는 것이 타당합니다.
            </p>
          </div>
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-4">
          {[
            ["PF 가능성", `${demoProject.pfScore}점`],
            ["LTV", formatPercent(ltv)],
            ["LTC", formatPercent(ltc)],
            ["Exit 분양률", "67.0%"],
          ].map(([l, v]) => (
            <div key={l} className="rounded-lg border border-slate-200 bg-white p-3 text-center dark:border-slate-800 dark:bg-slate-900">
              <p className="text-xs font-semibold text-slate-500">{l}</p>
              <p className="mt-1 text-xl font-black text-slate-950 dark:text-white">{v}</p>
            </div>
          ))}
        </div>
      </div>
    </Section>
  );
}

/* ── II. 주요 금융조건 요약표 ── */
export function SectionFinanceConditions() {
  const rows = [
    { label: "대출금액", vals: financeConditions.tranches.map(t => formatEok(t.amount, 0)) },
    { label: "대출금리", vals: financeConditions.tranches.map(t => formatPercent(t.rate, 1)) },
    { label: "취급수수료", vals: financeConditions.tranches.map(t => formatPercent(t.fee, 1)) },
    { label: "All-in Cost", vals: financeConditions.tranches.map(t => formatPercent(t.allInCost, 2)) },
    { label: "담보순위", vals: financeConditions.tranches.map(t => t.priority) },
  ];
  return (
    <Section id="sec-2" title="II. 주요 금융조건 요약표">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[600px] border-collapse text-sm">
          <thead>
            <tr>
              <Th>구분</Th>
              {financeConditions.tranches.map(t => <Th key={t.name}>{t.name}</Th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.label}>
                <Th>{r.label}</Th>
                {r.vals.map((v, i) => <Td key={i}>{v}</Td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

/* ── III. 주요 채권보전방안 ── */
export function SectionBondPreservation() {
  return (
    <Section id="sec-3" title="III. 주요 채권보전방안">
      <div className="grid gap-3 md:grid-cols-2">
        {bondPreservation.map(b => (
          <div key={b.category} className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
            <div className="flex items-center gap-2">
              <Shield className="h-4 w-4 text-indigo-600" />
              <p className="text-sm font-black text-slate-950 dark:text-white">{b.category}</p>
            </div>
            <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-400">{b.desc}</p>
          </div>
        ))}
      </div>
    </Section>
  );
}

/* ── IV. 할인분양 Trigger ── */
export function SectionDiscountTrigger() {
  return (
    <Section id="sec-4" title="IV. 할인분양 Trigger">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr><Th>시기</Th><Th>목표분양률</Th><Th>할인조건</Th></tr>
        </thead>
        <tbody>
          {discountTrigger.map(d => (
            <tr key={d.period}>
              <Td>{d.period}</Td>
              <Td className="font-bold">{d.targetRate}%</Td>
              <Td>{d.discountRate}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

/* ── V. 자금조달 구조 ── */
export function SectionFundingStructure() {
  const fmt = (n: number) => (n / 100).toLocaleString("ko-KR", { minimumFractionDigits: 1 }) + "억";
  return (
    <Section id="sec-5" title="V. 자금조달 구조 (Cash-In / Out)">
      <div className="grid gap-4 md:grid-cols-2">
        {(["cashIn", "cashOut"] as const).map(key => (
          <div key={key}>
            <h3 className="mb-2 text-sm font-black text-slate-700 dark:text-slate-300">
              {key === "cashIn" ? "Cash-In (조달)" : "Cash-Out (집행)"}
            </h3>
            <table className="w-full border-collapse text-sm">
              <thead><tr><Th>항목</Th><Th>금액 (백만원)</Th><Th>억원 환산</Th></tr></thead>
              <tbody>
                {fundingStructure[key].map(r => (
                  <tr key={r.item}>
                    <Td className="text-left font-semibold">{r.item}</Td>
                    <Td>{r.amount.toLocaleString()}</Td>
                    <Td>{fmt(r.amount)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </Section>
  );
}

/* ── VI. 자금 사용 용도 ── */
export function SectionFundUsage() {
  const items = fundingStructure.cashOut;
  const total = items.reduce((s, r) => s + r.amount, 0);
  return (
    <Section id="sec-6" title="VI. 자금 사용 용도">
      <table className="w-full border-collapse text-sm">
        <thead><tr><Th>항목</Th><Th>금액 (백만원)</Th><Th>비중</Th></tr></thead>
        <tbody>
          {items.map(r => (
            <tr key={r.item}>
              <Td className="text-left font-semibold">{r.item}</Td>
              <Td>{r.amount.toLocaleString()}</Td>
              <Td>{((r.amount / total) * 100).toFixed(1)}%</Td>
            </tr>
          ))}
          <tr className="font-bold">
            <Th>합계</Th><Td className="font-bold">{total.toLocaleString()}</Td><Td className="font-bold">100%</Td>
          </tr>
        </tbody>
      </table>
    </Section>
  );
}

/* ── VII. 사업부지 확보 ── */
export function SectionLandSecuring() {
  return (
    <Section id="sec-7" title="VII. 사업부지 확보">
      <table className="w-full border-collapse text-sm">
        <thead><tr><Th>필지</Th><Th>소유자</Th><Th>지분율</Th><Th>면적</Th><Th>근저당권</Th></tr></thead>
        <tbody>
          {landSecuring.map(l => (
            <tr key={l.parcel}>
              <Td className="text-left">{l.parcel}</Td><Td>{l.owner}</Td><Td>{l.share}</Td><Td>{l.area}</Td><Td>{l.mortgage}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

/* ── VIII. 사업추진 일정 ── */
export function SectionProjectSchedule() {
  return (
    <Section id="sec-8" title="VIII. 사업추진 일정">
      <div className="space-y-3">
        {projectSchedule.map(s => (
          <div key={s.step} className="flex gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-indigo-100 dark:bg-indigo-900/40">
              <Calendar className="h-4 w-4 text-indigo-600" />
            </div>
            <div className="flex-1 rounded-lg border border-slate-200 p-3 dark:border-slate-800">
              <div className="flex items-center justify-between">
                <p className="text-sm font-bold text-slate-900 dark:text-white">{s.step}</p>
                <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${s.status === "완료" ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400"}`}>
                  {s.status}
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-500">예정 {s.planned} / 완료 {s.completed}</p>
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

/* ── IX. 금융구조도 ── */
export function SectionFinancialStructureDiagram() {
  const actors = [
    { label: "차주 (시행사)", sub: "(주)리얼코어개발", color: "bg-sky-600" },
    { label: "신탁사", sub: "하나자산신탁", color: "bg-indigo-600" },
    { label: "시공사", sub: "신세계건설", color: "bg-emerald-600" },
    { label: "대주단", sub: "하나금융투자 외", color: "bg-violet-600" },
  ];
  return (
    <Section id="sec-9" title="IX. 금융구조도">
      <div className="flex flex-wrap items-center justify-center gap-4">
        {actors.map((a, i) => (
          <div key={a.label} className="flex items-center gap-4">
            <div className="rounded-lg border border-slate-200 p-4 text-center dark:border-slate-800">
              <div className={`mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-lg ${a.color}`}>
                <Building2 className="h-5 w-5 text-white" />
              </div>
              <p className="text-sm font-black text-slate-950 dark:text-white">{a.label}</p>
              <p className="text-xs text-slate-500">{a.sub}</p>
            </div>
            {i < actors.length - 1 && <span className="text-2xl text-slate-400">→</span>}
          </div>
        ))}
      </div>
      <div className="mt-4 rounded-lg border border-sky-200 bg-sky-50 p-4 text-sm leading-7 text-sky-900 dark:border-sky-900 dark:bg-sky-950/30 dark:text-sky-200">
        {financialStructureText.trim().split("\n").map((line, i) => <p key={i}>{line.trim()}</p>)}
      </div>
    </Section>
  );
}

/* ── X. 분양대금 납입 일정 ── */
export function SectionSalesPayment() {
  return (
    <Section id="sec-10" title="X. 분양대금 납입 일정">
      <div className="flex h-10 overflow-hidden rounded-lg">
        {salesPaymentSchedule.map(s => (
          <div key={s.type} className={`flex items-center justify-center text-xs font-black text-white ${s.type === "계약금" ? "bg-emerald-600" : s.type === "중도금" ? "bg-sky-600" : "bg-indigo-600"}`} style={{ width: `${s.ratio}%` }}>
            {s.type} {s.ratio}%
          </div>
        ))}
      </div>
      <table className="mt-4 w-full border-collapse text-sm">
        <thead><tr><Th>구분</Th><Th>비율</Th><Th>납입 시기</Th></tr></thead>
        <tbody>
          {salesPaymentSchedule.map(s => (
            <tr key={s.type}><Td className="font-bold">{s.type}</Td><Td>{s.ratio}%</Td><Td>{s.time}</Td></tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}
