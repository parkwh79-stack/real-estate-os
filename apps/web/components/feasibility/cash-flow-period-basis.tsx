import Link from "next/link";
import type { CashFlowPeriodBasis } from "../../lib/project-client";

const scheduleLabels: Record<string, string> = {
  land_purchase: "토지 매입", permit_approval: "인허가", construction_start: "착공",
  presale_start: "분양 시작", completion: "준공", loan_maturity: "대출 만기",
};
function integer(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("ko-KR", { maximumFractionDigits: 0 }) : "미기록";
}

export function cashFlowPeriodLabel(basis: CashFlowPeriodBasis | undefined, index: number): string {
  if (basis?.kind !== "source_sequence") return `M${integer(index)}`;
  const period = basis.periods?.find((item) => item.sourcePeriodIndex === index);
  return `원본 ${integer(index + 1)}${period?.label ? `: ${period.label}` : ""}`;
}

/** Show only the basis captured in the selected saved calculation. */
export function CashFlowPeriodBasisPanel({ basis, runId, projectId }: {
  basis?: CashFlowPeriodBasis; runId: string; projectId: string;
}) {
  const sourceSequence = basis?.kind === "source_sequence";
  const periods = basis?.periods ?? [];
  const interest = basis?.interestCalculation;
  const range = typeof basis?.firstCalculationPeriodIndex === "number" && typeof basis?.lastCalculationPeriodIndex === "number"
    ? `${cashFlowPeriodLabel(basis, basis.firstCalculationPeriodIndex)} ~ ${cashFlowPeriodLabel(basis, basis.lastCalculationPeriodIndex)}` : "미기록";
  return <section aria-label="저장 계산의 기간·이자 기준" className="mt-4 min-w-0 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm dark:border-slate-700 dark:bg-slate-950">
    <h3 className="font-bold">저장 계산의 기간·이자 기준</h3>
    <p className="mt-1 break-all text-xs text-slate-500">계산 기록: {runId}</p>
    {!basis ? <p className="mt-2">이 계산에는 기간 기준이 미기록되어 있습니다. 현재 프로젝트 일정으로 이전 계산의 기준을 추정하지 않습니다.</p> : <>
      <p className="mt-2 font-bold">{sourceSequence ? "원본 사건·기간 순서 기준" : "달력·월차 기준"}</p>
      <p className="mt-1 text-xs leading-5 text-slate-600 dark:text-slate-400">{sourceSequence
        ? "원본 순번은 배열의 위치입니다. 사전 사건을 포함하므로 M0부터의 실제 개월 수나 달력 날짜로 해석하지 않습니다."
        : "저장된 일정의 달력·월차 관계를 사용하는 계산입니다. 원본 순서와 이자 발생 기록은 아래 저장 기준으로 확인하세요."}</p>
      <dl className="mt-3 grid gap-2 sm:grid-cols-2">
        <div><dt className="text-xs text-slate-500">원본 기간 행</dt><dd>{basis.periods ? `${integer(periods.length)}개 · 사전 사건 ${integer(periods.filter((row) => row.periodKind === "event").length)}개` : "미기록"}</dd></div>
        <div><dt className="text-xs text-slate-500">실제 계산 행</dt><dd>{integer(basis.calculationPeriodCount)}{typeof basis.calculationPeriodCount === "number" ? "개" : ""}</dd></div>
        <div className="sm:col-span-2"><dt className="text-xs text-slate-500">계산 배열 범위</dt><dd>{range}</dd></div>
      </dl>
      {sourceSequence ? <p className="mt-3 font-bold text-amber-800 dark:text-amber-300">달력 대응과 실제 이자기간은 미확인입니다. 기존 예산 금융비 분석은 이어갈 수 있으며, 월별 금융 재계산에는 사건·일정의 시간 대응을 먼저 확인해야 합니다.</p> : null}
      {interest?.status === "calculated" ? <p className="mt-3 text-xs leading-5">저장된 이자 발생: {integer(interest.chargedPeriodCount)}개 계산 행
        {interest.chargedPeriodCount === 0 ? " · 이자 발생 행 없음"
          : typeof interest.firstChargedMonthIndex === "number" && typeof interest.lastChargedMonthIndex === "number"
            ? ` · ${cashFlowPeriodLabel(basis, interest.firstChargedMonthIndex)} ~ ${cashFlowPeriodLabel(basis, interest.lastChargedMonthIndex)}` : " · 시작·종료 미기록"}
        {sourceSequence ? ". 원본에 기록된 비용 행이며 실제 이자 개월 수를 확정하지 않습니다." : ". 저장된 계산의 발생 범위이며 실제 대출 약정의 이자기간 확인과는 구분합니다."}</p>
        : <p className="mt-3 text-xs">{interest?.status === "not_calculated" ? "실제 월별 이자는 계산하지 않았습니다. 원본 기간의 길이와 사건·일정 대응을 확인해야 합니다." : "이자 발생 범위는 미기록입니다."}</p>}
      {Object.keys(basis.scheduleBindings ?? {}).length ? <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[360px] text-left text-xs">
        <caption className="mb-2 text-left font-bold">저장된 일정과 원본 순서 대응</caption>
        <thead><tr><th className="p-2">일정</th><th className="p-2">원본 위치</th><th className="p-2">원본 월 순번</th></tr></thead>
        <tbody>{Object.entries(basis.scheduleBindings ?? {}).map(([key, binding]) => <tr key={key} className="border-t border-slate-200 dark:border-slate-700">
          <th className="p-2">{scheduleLabels[key] ?? "추가 일정"}</th><td className="p-2">{binding.sourcePeriodIndex === null ? "미확인" : cashFlowPeriodLabel(basis, binding.sourcePeriodIndex)}</td>
          <td className="p-2">{binding.sourceMonthNumber === null ? "미확인" : `${integer(binding.sourceMonthNumber)}번째 월`}</td>
        </tr>)}</tbody>
      </table></div> : null}
      {periods.length ? <details className="mt-3"><summary className="cursor-pointer font-bold">원본 사건·기간과 출처 확인</summary>
        <div className="mt-2 max-h-80 overflow-auto"><table className="w-full min-w-[480px] text-left text-xs">
          <thead><tr>{["원본 순번", "원본 구분", "원본 월 순번", "출처"].map((label) => <th key={label} className="p-2">{label}</th>)}</tr></thead>
          <tbody>{periods.map((row) => <tr key={row.sourcePeriodIndex} className="border-t border-slate-200 dark:border-slate-700">
            <td className="p-2">{integer(row.sourcePeriodIndex + 1)} · {row.label}</td><td className="p-2">{row.periodKind === "event" ? "사전 사건" : row.periodKind === "monthly" ? "월별 자료" : "미확인"}</td>
            <td className="p-2">{row.sourceMonthNumber === null ? "미확인" : `${integer(row.sourceMonthNumber)}번째 월`}</td>
            <td className="p-2">{[row.sourceLocator?.sourceName, row.sourceLocator?.sheet && `${row.sourceLocator.sheet}${row.sourceLocator.cell ? `!${row.sourceLocator.cell}` : ""}`,
              row.sourceLocator?.monthCell && `월 순번 셀 ${row.sourceLocator.monthCell}`].filter(Boolean).join(" · ") || "미기록"}</td>
          </tr>)}</tbody>
        </table></div>
      </details> : null}
      {basis.warnings?.length ? <p className="mt-2 text-xs text-amber-800 dark:text-amber-300">이 계산에 저장된 기간 확인 항목이 {integer(basis.warnings.length)}개 있습니다. 사건·일정의 대응과 이자기간을 원본에서 확인하세요.</p> : null}
    </>}
    <Link href={`/project/${encodeURIComponent(projectId)}/input-package`} className="mt-3 inline-block text-xs font-bold text-indigo-600">저장 입력·원본 자료 확인</Link>
  </section>;
}
