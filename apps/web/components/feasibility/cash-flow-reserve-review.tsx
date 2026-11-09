import { eokToWon, wonToEok } from "../../lib/feasibility-review";

export type ReserveInput = {
  ratePercent: number;
  baselineIncludedReserveWon: number;
  comparisonIncludedReserveWon: number;
  reason: string;
};

export type ReserveResult = ReserveInput & {
  baselineCostExcludingReserveWon: number;
  comparisonCostExcludingReserveWon: number;
  targetReserveWon: number;
  reviewBudgetWon: number;
  costIncreaseWon: number;
  absorbedWon: number;
  remainingWon: number;
  excessWon: number;
};

export type ReserveDraft = {
  enabled: boolean;
  rate: string;
  baselineIncluded: string;
  comparisonIncluded: string;
  reason: string;
};

export function reserveDraftFromInput(input?: ReserveInput | null): ReserveDraft {
  return {
    enabled: input != null,
    rate: input == null ? "" : String(input.ratePercent),
    baselineIncluded: wonToEok(input?.baselineIncludedReserveWon),
    comparisonIncluded: wonToEok(input?.comparisonIncludedReserveWon),
    reason: input?.reason ?? "",
  };
}

export function reserveInputFromDraft(draft: ReserveDraft): ReserveInput | null {
  if (!draft.enabled) return null;
  const rate = draft.rate.trim();
  if (!/^\d{1,3}(?:\.\d{1,2})?$/.test(rate) || Number(rate) > 100) {
    throw new Error("예비비율은 0~100% 범위에서 소수 둘째 자리까지 입력하세요.");
  }
  if (!draft.baselineIncluded.trim() || !draft.comparisonIncluded.trim()) {
    throw new Error("두 계산에 이미 포함된 예비비를 입력하세요. 포함된 금액이 없음을 확인한 경우 0을 입력하세요.");
  }
  const baselineIncludedReserveWon = eokToWon(draft.baselineIncluded);
  const comparisonIncludedReserveWon = eokToWon(draft.comparisonIncluded);
  if (baselineIncludedReserveWon < 0 || comparisonIncludedReserveWon < 0) {
    throw new Error("이미 포함된 예비비는 0원 이상이어야 합니다.");
  }
  const reason = draft.reason.trim();
  if (!reason) throw new Error("예비비율과 포함 금액의 검토 근거를 입력하세요.");
  if (reason.length > 1000) throw new Error("예비비 검토 근거는 1,000자 이내로 입력하세요.");
  return { ratePercent: Number(rate), baselineIncludedReserveWon, comparisonIncludedReserveWon, reason };
}

const control = "mt-1 w-full min-w-0 rounded-lg border border-slate-300 bg-white p-2 text-sm dark:border-slate-700 dark:bg-slate-900";
const integer = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });

export function CashFlowReserveForm({ draft, onChange, disabled }: {
  draft: ReserveDraft;
  onChange: (draft: ReserveDraft) => void;
  disabled: boolean;
}) {
  return <fieldset disabled={disabled} className="mt-4 min-w-0 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
    <legend className="px-1 text-sm font-bold">사업비 예비비 검토</legend>
    <label className="flex items-center gap-2 text-sm font-semibold">
      <input type="checkbox" checked={draft.enabled} onChange={(event) => onChange({ ...draft, enabled: event.target.checked })} />
      예비비로 사업비 차이 검토
    </label>
    {draft.enabled ? <div className="mt-3 space-y-3">
      <p className="text-xs leading-5 text-slate-500">
        두 계산의 총사업비에서 기존 예비비를 각각 분리합니다. 기준안의 예비비 제외 사업비에 새 비율을 적용해 예산을 검토합니다.
        포함된 예비비가 없음을 확인한 경우 0을 입력하세요.
      </p>
      <div className="grid gap-3 md:grid-cols-3">
        <label className="text-sm">예비비율 (%)
          <input className={control} inputMode="decimal" value={draft.rate} maxLength={6}
            onChange={(event) => onChange({ ...draft, rate: event.target.value })} />
        </label>
        <label className="text-sm">기준안에 포함된 예비비 (억원)
          <input className={control} inputMode="decimal" value={draft.baselineIncluded} maxLength={20}
            onChange={(event) => onChange({ ...draft, baselineIncluded: event.target.value })} />
        </label>
        <label className="text-sm">변경안에 포함된 예비비 (억원)
          <input className={control} inputMode="decimal" value={draft.comparisonIncluded} maxLength={20}
            onChange={(event) => onChange({ ...draft, comparisonIncluded: event.target.value })} />
        </label>
      </div>
      <label className="block text-sm">예비비 검토 근거
        <textarea className={control} value={draft.reason} maxLength={1000} rows={2}
          placeholder="비율을 정한 이유와 기존 예비비의 출처를 입력하세요."
          onChange={(event) => onChange({ ...draft, reason: event.target.value })} />
      </label>
      <p className="text-xs leading-5 text-slate-500">비교 선택을 저장하면 예비비, 충당 가능액, 잔여액과 초과액을 확인할 수 있습니다. 실제 지출 시점은 별도로 검토합니다.</p>
    </div> : null}
  </fieldset>;
}

export function CashFlowReserveResult({ review }: { review: ReserveResult }) {
  const rate = Number.isInteger(review.ratePercent) ? `${review.ratePercent}%` : `${Math.round(review.ratePercent * 100)}bp`;
  const status = review.excessWon > 0 ? "예비비 초과" : review.costIncreaseWon > 0 ? "예비비 범위 이내" : "사업비 증가 없음";
  const amounts = [
    ["기준안 사업비 (예비비 제외)", review.baselineCostExcludingReserveWon],
    ["변경안 사업비 (예비비 제외)", review.comparisonCostExcludingReserveWon],
    ["검토 예비비", review.targetReserveWon],
    ["예비비 반영 검토 예산", review.reviewBudgetWon],
    ["사업비 증가액", review.costIncreaseWon],
    ["예비비 충당 가능액", review.absorbedWon],
    ["잔여 예비비", review.remainingWon],
    ["예비비 초과액", review.excessWon],
  ] as const;
  return <section aria-label="저장된 사업비 예비비 검토" className="min-w-0 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h4 className="text-sm font-bold">사업비 예비비 검토</h4>
      <strong className={review.excessWon > 0 ? "text-sm text-re-danger" : "text-sm text-re-text"}>{status}</strong>
    </div>
    <p className="mt-2 text-xs text-slate-500">검토 비율 {rate} · 기준안의 예비비 제외 사업비 기준{rate.endsWith("bp") ? " · 100bp = 1%" : ""}</p>
    <p className="mt-1 text-xs text-slate-500">
      기존 포함 예비비 · 기준안 {integer.format(review.baselineIncludedReserveWon)}원 · 변경안 {integer.format(review.comparisonIncludedReserveWon)}원
    </p>
    <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {amounts.map(([label, value]) => <div key={label} className="min-w-0 rounded-lg bg-re-subtle p-2">
        <dt className="text-xs text-re-secondary">{label}</dt>
        <dd className="mt-1 break-words text-sm font-bold tabular-nums">{integer.format(value)}원</dd>
      </div>)}
    </dl>
    <p className="mt-3 break-words text-sm">검토 근거 · {review.reason}</p>
    <p className="mt-1 text-xs leading-5 text-slate-500">저장된 두 계산의 사업비와 현금흐름은 각각 원래 결과를 표시합니다. 예비비는 별도 예산 검토에 반영됩니다.</p>
  </section>;
}
