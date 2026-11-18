"use client";

import type { CashFlowRecalculationBasis } from "../../lib/cash-flow-recalculation";

export function CashFlowRecalculationBasisSelector({ value, onChange, disabled }: {
  value: CashFlowRecalculationBasis; onChange: (value: CashFlowRecalculationBasis) => void; disabled: boolean;
}) {
  return <div className="rounded-xl border border-re-border bg-re-surface p-4">
    <label className="flex flex-wrap items-center gap-3 text-sm font-bold text-re-text">
      Cash Flow 재계산 기준
      <select aria-label="Cash Flow 재계산 기준" value={value} disabled={disabled}
        onChange={(event) => onChange(event.target.value as CashFlowRecalculationBasis)}
        className="min-h-10 max-w-full rounded-lg border border-re-border bg-re-surface px-3 text-sm font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-re-action disabled:opacity-50">
        <option value="project">프로젝트 기본 기준</option>
        <option value="working">저장한 검토 입력으로 예산 계산</option>
      </select>
    </label>
    <p className="mt-2 text-xs leading-5 text-re-secondary">프로젝트 기본 기준은 등록된 원본 계획을 사용할 수 있습니다. 수정한 사업비·매출을 반영하려면 저장한 검토 입력을 선택하세요. 예산 계산은 실제 월별 약정 검증과 구분됩니다.</p>
  </div>;
}
