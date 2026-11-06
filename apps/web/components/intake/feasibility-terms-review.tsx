"use client";

import { FINANCING_PRIORITIES, SCHEDULE_FIELDS, type FieldReview, type FinancingDraft,
  type ReviewSource, type TermsDraft, type WorkbookFinancingReview } from "../../lib/feasibility-review";

const control = "rounded-md border border-slate-300 bg-white px-2 py-2 text-sm dark:border-slate-700 dark:bg-slate-900";
const button = "rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold disabled:opacity-40 dark:border-slate-700";

export function FeasibilityTermsReview({ draft, disabled, selectedSource, workbookFinancing, onChange, onShowSource }: {
  draft: TermsDraft; disabled: boolean; selectedSource?: ReviewSource;
  workbookFinancing?: WorkbookFinancingReview | null;
  onChange: (draft: TermsDraft) => void; onShowSource: (review: FieldReview) => void;
}) {
  const editReview = (group: "schedule" | "financing", patch: Partial<FieldReview>) => onChange({
    ...draft, [`${group}Dirty`]: true,
    reviews: { ...draft.reviews, [group]: { ...draft.reviews[group], ...patch } },
  });
  const editFinancing = (id: string, patch: Partial<FinancingDraft>) => onChange({ ...draft, financingDirty: true,
    financing: draft.financing.map((row) => row.tranche_id === id ? { ...row, ...patch } : row) });
  const reviewControls = (group: "schedule" | "financing", label: string) => {
    const review = draft.reviews[group];
    return <div className="mt-3 space-y-2">
      <label className="flex flex-wrap items-center gap-2 text-sm">{label} 입력 구분
        <select aria-label={`${label} 입력 구분`} className={control} value={review.origin}
          onChange={(event) => editReview(group, { origin: event.target.value as FieldReview["origin"] })}>
          <option value="user_assumption">사용자 가정</option><option value="document_correction">원본 대조·수정</option>
        </select>
      </label>
      {review.origin === "document_correction" ? <div className="flex flex-wrap gap-2">
        <input aria-label={`${label} 출처 셀`} className={`${control} min-w-0 flex-1`} value={review.sourceCell}
          placeholder="시트명!셀 (예: 일정!B2)" onChange={(event) => editReview(group, {
            sourceCell: event.target.value, sourceDocument: selectedSource ?? review.sourceDocument,
          })} />
        <button type="button" className={button} onClick={() => onShowSource(review)}>{label} 원본 보기</button>
        {review.sourceDocument ? <p className="w-full break-all text-xs text-slate-500">{review.sourceDocument.originalFileName}</p> : null}
      </div> : null}
      <input aria-label={`${label} 검토 이유`} className={`${control} w-full`} value={review.reason}
        placeholder="이 묶음의 수정 이유 또는 가정의 근거" onChange={(event) => editReview(group, { reason: event.target.value })} />
    </div>;
  };
  return <div className="mt-5 space-y-4">
    <fieldset id="cash-flow-schedule-review" disabled={disabled} className="min-w-0 scroll-mt-24 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
      <legend className="px-1 font-bold">사업기간{draft.scheduleDirty ? " · 저장 전" : ""}</legend>
      <p className="mb-3 text-xs text-slate-500">계획일을 YYYY-MM-DD로 입력하세요. 미입력 항목은 비워 두며, 입력을 지우면 미입력으로 저장합니다.</p>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{SCHEDULE_FIELDS.map(([key, label]) =>
        <label key={key} className="grid gap-1 text-sm">{label}
          <input aria-label={label} value={draft.schedule[key]} placeholder="YYYY-MM-DD" className={`${control} min-w-0 w-full`}
            onChange={(event) => onChange({ ...draft, scheduleDirty: true,
              schedule: { ...draft.schedule, [key]: event.target.value },
              changedSchedule: [...new Set([...draft.changedSchedule, key])],
            })} />
        </label>)}
      </div>
      {reviewControls("schedule", "사업기간")}
    </fieldset>
    <fieldset id="cash-flow-financing-review" disabled={disabled} className="min-w-0 scroll-mt-24 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
      <legend className="px-1 font-bold">대출 조건{draft.financingDirty ? " · 저장 전" : ""}</legend>
      {workbookFinancing?.terms?.length ? <details className="mb-3 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
        <summary className="cursor-pointer font-bold">원본에서 읽은 대출 조건</summary>
        <p className="mt-2 break-all text-xs text-slate-500">{workbookFinancing.sourceWorkbook}</p>
        <p className="mt-2 text-sm">사업수지의 검토 후보입니다. 아래 저장값과 대조한 뒤 수정 이유를 남겨 저장하세요. 법적 순위·만기·고정/변동금리·수수료 부과 기준·인출과 상환 조건은 실제 약정서 확인이 필요합니다.</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">{workbookFinancing.terms.map((row) => <div key={row.trancheId}
          className="min-w-0 rounded-md border border-slate-200 p-3 dark:border-slate-700" aria-label={`${row.name} 원본 조건`}>
          <p className="font-bold">{row.name}</p>
          {row.phase ? <p className="mt-1 text-xs font-bold text-indigo-700 dark:text-indigo-300">{row.phase === "refinancing" ? "후속 대환자금 후보 · 초기 PF와 구분" : "건설 단계 초기자금 후보"}</p> : null}
          {row.phase === "refinancing" ? <p className="mt-1 text-xs text-amber-800 dark:text-amber-300">기존 건설 대출을 상환하는 후속 자금입니다. 초기 PF 금액이나 아래 저장 대출에 자동 합산하지 않습니다.</p> : null}
          {row.contractVerified === false ? <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">체결 약정 미확인 · 아래 기간은 원본의 단계별 가정이며 법적 만기·기산일은 확인이 필요합니다.</p> : null}
          {([ ["amount", "금액", "억원", 1e-8], ["interest_rate", "금리", "%", 100], ["fee_rate", "수수료율", "%", 100], ["term_months", "원본 기간 가정", "개월", 1] ] as const).map(([key, label, unit, scale]) => {
            const field = row.fields[key];
            const value = field?.value;
            return <div key={key} className="mt-1 text-sm">
              <span>{label}: {typeof value === "number" && Number.isFinite(value) ? `${(value * scale).toLocaleString("ko-KR", { maximumFractionDigits: 8 })}${unit}` : field?.status === "missing" ? "미입력" : "확인 필요"}</span>
              {field?.sourceCell ? <div className="break-all text-xs text-slate-500">{workbookFinancing.sourceDocument?.archiveId
                ? <button type="button" className="underline" onClick={() => onShowSource({ origin: "document_correction", reason: "원본 대출 조건 대조",
                  sourceCell: field.sourceCell, sourceDocument: workbookFinancing.sourceDocument })}>{field.sourceCell} 원본 보기</button>
                : field.sourceCell}</div> : null}
            </div>;
          })}
          {row.unconfirmedTerms?.length ? <details className="mt-2 text-xs text-slate-600 dark:text-slate-300"><summary className="cursor-pointer">약정에서 확인할 항목</summary><p className="mt-1">{row.unconfirmedTerms.join(" · ")}</p></details> : null}
          {!row.principalReconciled ? <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">입력표와 현금흐름의 대출금액을 대조하지 못했습니다. 이 행의 금리·수수료를 자동 적용하지 않습니다.</p> : null}
        </div>)}</div>
      </details> : null}
      <p className="text-xs text-slate-500">금액은 억원, 연 금리·수수료율은 %, 만기는 개월입니다. 빈 값은 미입력, 0은 명시적인 0으로 저장합니다. 신규 행은 대출을 입력하며 기존 자기자본 항목은 보존합니다.</p>
      <p className="mt-1 text-xs text-slate-500">기존 값에 ‘원본값 확인 필요’가 표시되면 원본을 확인해 정정하거나 비워 두세요. 확인 전에는 대출 조건을 저장하지 않습니다.</p>
      {!draft.financing.length ? <p className="mt-3 text-sm text-slate-500">저장된 대출 조건이 없습니다.</p> : null}
      <div className="mt-3 space-y-3">{draft.financing.map((row, index) => <fieldset key={row.tranche_id}
        className="min-w-0 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
        <legend className="px-1 text-sm font-bold">자금조달 {index + 1}</legend>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <label className="grid gap-1 text-sm">명칭<input aria-label={`대출 ${index + 1} 명칭`} value={row.name}
            className={`${control} min-w-0 w-full`} onChange={(event) => editFinancing(row.tranche_id, { name: event.target.value })} /></label>
          <label className="grid gap-1 text-sm">순위<select aria-label={`대출 ${index + 1} 순위`} value={row.priority}
            className={`${control} min-w-0 w-full`} onChange={(event) => editFinancing(row.tranche_id, { priority: event.target.value })}>
            <option value="">선택</option>{FINANCING_PRIORITIES.filter(([key]) => key !== "equity" || row.priority === "equity")
              .map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select></label>
          {([ ["amount", "금액 (억원)"], ["interest_rate", "연 금리 (%)"], ["fee_rate", "수수료율 (%)"],
            ["term_months", "만기 (개월)"] ] as const).map(([key, label]) => <label key={key} className="grid gap-1 text-sm">{label}
            <input aria-label={`대출 ${index + 1} ${label}`} value={row[key]} inputMode={key === "term_months" ? "numeric" : "decimal"}
              className={`${control} min-w-0 w-full`} placeholder="미입력" onChange={(event) => editFinancing(row.tranche_id, { [key]: event.target.value })} />
          </label>)}
          <label className="grid gap-1 text-sm sm:col-span-2">상환 재원<input aria-label={`대출 ${index + 1} 상환 재원`}
            value={row.repayment_source} className={`${control} min-w-0 w-full`} placeholder="미입력"
            onChange={(event) => editFinancing(row.tranche_id, { repayment_source: event.target.value })} /></label>
        </div>
        <button type="button" className={`${button} mt-3`} aria-label={`대출 ${index + 1} 삭제`} onClick={() => onChange({
          ...draft, financingDirty: true, financing: draft.financing.filter((item) => item.tranche_id !== row.tranche_id),
        })}>이 항목 삭제</button>
      </fieldset>)}</div>
      <button type="button" className={`${button} mt-3`} onClick={() => onChange({ ...draft, financingDirty: true,
        financing: [...draft.financing, { tranche_id: crypto.randomUUID(), name: "", priority: "", amount: "",
          interest_rate: "", fee_rate: "", term_months: "", repayment_source: "" }],
      })}>대출 항목 추가</button>
      {reviewControls("financing", "대출 조건")}
      <p className="mt-3 text-xs text-slate-500">조건과 검토 이유를 저장·재개할 수 있습니다. 분석 화면에서 월별 금융 계산 방식을 선택하면 저장한 조건으로 별도 검토안을 계산합니다. 기존 예산 금융비용 방식도 사용할 수 있습니다.</p>
    </fieldset>
  </div>;
}
