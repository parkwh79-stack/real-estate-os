"use client";

import type { CashFlowTimingDraft, CashFlowTimingSource } from "../../lib/cash-flow-timing";

const control = "min-w-0 w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-sm dark:border-slate-700 dark:bg-slate-900";
const button = "min-h-10 rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold disabled:opacity-40 dark:border-slate-700";

export function CashFlowTimingReview({ draft, source, disabled, dirty, reason, onChange, onReasonChange }: {
  draft: CashFlowTimingDraft; source: CashFlowTimingSource; disabled: boolean; dirty: boolean; reason: string;
  onChange: (draft: CashFlowTimingDraft) => void; onReasonChange: (reason: string) => void;
}) {
  const edit = (patch: Partial<CashFlowTimingDraft>) => onChange({ ...draft, ...patch, remove: false });
  const events = source.basis.periods.filter((row) => row.periodKind === "event");
  return <fieldset id="cash-flow-timing-review" disabled={disabled} className="min-w-0 scroll-mt-24 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
    <legend className="px-1 font-bold">사건·월차·분양수금 분석 가정{dirty ? " · 저장 전" : ""}</legend>
    <p className="text-sm">원본 사건 순서는 실제 개월이 아닙니다. 아래 값은 분석용 사용자 가정으로 저장하며 원본·승인된 사실·실제 대출 약정은 바꾸지 않습니다.</p>
    <p className="mt-2 text-xs text-slate-500">확인한 값만 입력하세요. 빈 값은 미확인으로 저장하며 월별 금융 계산만 보류합니다. 기존 예산 현금흐름은 계속 검토할 수 있습니다.</p>
    {source.sourceDocument?.originalFileName ? <p className="mt-2 break-all text-xs text-slate-500">원본: {source.sourceDocument.originalFileName}</p> : null}
    {draft.sourceBasisHash && draft.sourceBasisHash !== source.sourceBasisHash ? <p role="alert" className="mt-2 text-sm text-rose-700">저장된 시간 가정의 원본 기간이 바뀌었습니다. 가정을 해제하고 최신 원본을 다시 확인하세요.</p> : null}
    {draft.remove ? <p role="status" className="mt-3 text-sm font-bold">저장하면 기존 시간 가정을 해제합니다. 아래 이유를 기록하세요.</p> : <>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="grid gap-1 text-sm">계산 M0 기준일<input aria-label="계산 M0 기준일" placeholder="YYYY-MM-DD" className={control} value={draft.baseDate} onChange={(event) => edit({ baseDate: event.target.value })} /></label>
        <label className="grid gap-1 text-sm">원본 1개월의 계산 월차<input aria-label="원본 1개월의 계산 월차" inputMode="numeric" placeholder="M0은 0" className={control} value={draft.firstMonthlyMonthIndex} onChange={(event) => edit({ firstMonthlyMonthIndex: event.target.value })} /></label>
      </div>
      <p className="mt-2 text-xs text-slate-500">원본의 확인된 월 번호 1, 2, …를 위 월차부터 연결합니다. 사건의 날짜와 월차는 같은 M0 기준월에 맞춰 입력하세요.</p>
      <div className="mt-3 space-y-3">{events.map((row) => {
        const event = draft.eventBindings[row.sourcePeriodIndex] ?? { plannedDate: "", monthIndex: "" };
        const update = (patch: Partial<typeof event>) => edit({ eventBindings: { ...draft.eventBindings, [row.sourcePeriodIndex]: { ...event, ...patch } } });
        return <div key={row.sourcePeriodIndex} className="rounded-md border border-slate-200 p-3 dark:border-slate-700">
          <p className="text-sm font-bold">{row.label}</p>
          <p className="mt-1 break-all text-xs text-slate-500">원본 {row.sourcePeriodIndex + 1} · {row.sourceLocator?.sheet}{row.sourceLocator?.cell ? `!${row.sourceLocator.cell}` : ""}</p>
          <div className="mt-2 grid gap-3 sm:grid-cols-2">
            <label className="grid gap-1 text-sm">분석용 사건 날짜<input aria-label={`${row.label} 분석용 사건 날짜`} className={control} placeholder="YYYY-MM-DD" value={event.plannedDate} onChange={(change) => update({ plannedDate: change.target.value })} /></label>
            <label className="grid gap-1 text-sm">계산 월차<input aria-label={`${row.label} 계산 월차`} className={control} inputMode="numeric" value={event.monthIndex} onChange={(change) => update({ monthIndex: change.target.value })} /></label>
          </div>
        </div>;
      })}</div>
      <p className="mt-4 text-sm font-bold">사업 전체 월별 분양수금 가정 · 원</p>
      <p className="mt-1 text-xs text-slate-500">상품별 배분은 미확인입니다. 합계가 현재 총매출과 같아야 월별 금융 계산을 실행합니다. 추가한 행의 확인한 0원도 직접 0을 입력하세요.</p>
      <label className="mt-2 flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={draft.unlistedMonthsAreZero} onChange={(event) => edit({ unlistedMonthsAreZero: event.target.checked })} />표에 없는 월은 분양수금 0원으로 가정합니다</label>
      <div className="mt-3 space-y-3">{draft.collections.map((row, index) => <div key={index} className="grid gap-2 rounded-md border border-slate-200 p-3 sm:grid-cols-2 dark:border-slate-700">
        {([ ["monthIndex", "월차"], ["contract", "계약금"], ["interim", "중도금"], ["balance", "잔금"] ] as const).map(([key, label]) => <label key={key} className="grid gap-1 text-sm">{label}
          <input aria-label={`수금 ${index + 1} ${label}`} className={control} inputMode="numeric" value={row[key]} onChange={(event) => edit({ collections: draft.collections.map((item, offset) => offset === index ? { ...item, [key]: event.target.value } : item) })} />
        </label>)}
        <button type="button" className={button} onClick={() => edit({ collections: draft.collections.filter((_, offset) => offset !== index) })}>수금 {index + 1} 삭제</button>
      </div>)}</div>
      <button type="button" className={`${button} mt-3`} onClick={() => edit({ collections: [...draft.collections, { monthIndex: "", contract: "", interim: "", balance: "" }] })}>월별 수금 추가</button>
    </>}
    <label className="mt-4 grid gap-1 text-sm">시간·수금 가정 이유<input aria-label="시간·수금 가정 이유" className={control} value={reason} onChange={(event) => onReasonChange(event.target.value)} /></label>
    <button type="button" className={`${button} mt-3`} onClick={() => onChange({ ...draft, remove: true })}>저장된 시간 가정 해제</button>
    {source.basis.warnings?.length ? <details className="mt-3 text-xs text-slate-500"><summary className="cursor-pointer">원본 기간의 확인 항목</summary>{source.basis.warnings.map((warning) => <p key={warning} className="mt-1">{warning}</p>)}</details> : null}
  </fieldset>;
}
