"use client";

import { useState } from "react";
import { canLoadWorkbookFinancialField, workbookFinancialSourceCells, type FieldReview, type WorkbookFinancialReview } from "../../lib/feasibility-review";

const money = (value: number) => value.toLocaleString("ko-KR", { maximumFractionDigits: 0 });
const rawText = (value: number | string | null): string => value === null ? "미확인" : typeof value === "number" || /^-?\d+(?:\.\d+)?$/.test(value) ? money(Number(value)) : value;
const sourceUnitLabels: Record<string, string> = { KRW: "원", KRW_THOUSAND: "천원", KRW_MILLION: "백만원" };
const sourceUnit = (value?: string) => sourceUnitLabels[value ?? ""] ?? value;
const button = "rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold disabled:opacity-40 dark:border-slate-700";

export function WorkbookFinancialReviewPanel({ review, disabled, onLoad, onShowSource }: {
  review: WorkbookFinancialReview; disabled: boolean;
  onLoad: (keys: string[]) => void; onShowSource: (source: FieldReview) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const entries = Object.entries(review.sourceFields);
  const readCount = entries.filter(([, field]) => typeof field.value === "number" && Number.isSafeInteger(field.value)).length;
  return <details open aria-label="원본 재무 항목 검토" className="mt-4 rounded-lg border border-indigo-200 bg-indigo-50/40 p-3 dark:border-indigo-800 dark:bg-indigo-950/20">
    <summary className="cursor-pointer font-bold">상봉 Excel에서 읽은 재무 항목 · {readCount}개</summary>
    <p className="mt-2 break-all text-xs text-slate-500">{review.sourceWorkbook ?? review.sourceDocument?.originalFileName}</p>
    <p className="mt-2 text-sm">원본에서 읽은 검토 후보입니다. 선택한 금액을 아래 검토 입력에 불러온 뒤 이유를 남기고 저장하세요. 원본값과 정식 승인값은 자동 변경되지 않습니다.</p>
    <p className="mt-1 text-xs font-semibold text-amber-800 dark:text-amber-300">저장된 수식값은 Excel의 마지막 저장값입니다. 수식을 다시 계산하거나 약정 조건을 확인했다는 뜻이 아닙니다.</p>
    {review.variant === "rental" ? <p className="mt-2 text-xs text-slate-600 dark:text-slate-300">임대보증금과 분양전환 정산을 구분해 확인합니다. 원본 수입과 매출의 대사가 확인되지 않은 총액은 검토 입력으로 불러오지 않습니다.</p> : null}
    <ul className="mt-3 divide-y divide-indigo-100 dark:divide-indigo-900">{entries.map(([key, field]) => {
      const eligible = canLoadWorkbookFinancialField(review, key);
      const cells = workbookFinancialSourceCells(field);
      return <li key={key} className="grid min-w-0 gap-2 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <label className="flex min-w-0 items-start gap-2 text-sm font-bold">
          <input type="checkbox" aria-label={`${field.label} 불러오기 선택`} className="mt-1" disabled={disabled || !eligible} checked={selected.includes(key)}
            onChange={(event) => setSelected((current) => event.target.checked ? [...current, key] : current.filter((item) => item !== key))} />
          <span className="min-w-0 break-words">{field.label}<span className="mt-1 block font-normal tabular-nums">{typeof field.value === "number" && Number.isSafeInteger(field.value) ? `${money(field.value)}원` : "미확인"}</span></span>
        </label>
        <div className="min-w-0 text-xs text-slate-600 dark:text-slate-300">
          <p>{field.calculationState === "literal" ? "원본 직접 입력값" : field.calculationState === "saved_formula_cache" ? "저장된 수식값 · 재계산 미검증" : field.calculationState?.startsWith("derived_from_source") ? "원본 저장값 합계 · 재계산 미검증" : "확인 필요"}{field.sourceUnit ? ` · 원본 단위 ${sourceUnit(field.sourceUnit)}` : ""}</p>
          {field.rawValue !== null && field.rawValue !== undefined ? <p className="mt-1">원본 저장값: {Array.isArray(field.rawValue) ? field.rawValue.map(rawText).join(" + ") : rawText(field.rawValue)}{field.sourceUnit ? ` ${sourceUnit(field.sourceUnit)}` : ""} · 정수 표시</p> : null}
          {cells.length ? cells.map((cell) => <button key={cell} type="button" disabled={disabled || !review.sourceDocument?.archiveId} className="mt-1 block max-w-full break-all text-left font-semibold text-indigo-700 underline disabled:opacity-40 dark:text-indigo-300"
            onClick={() => onShowSource({ origin: "document_correction", reason: "원본 재무 항목 대조", sourceCell: cell, sourceDocument: review.sourceDocument })}>{cell} 원본 보기</button>) : field.sourceCell ? <p className="mt-1 break-all">{field.sourceCell}</p> : null}
          {field.formula || field.calculationExpression ? <details className="mt-1"><summary className="cursor-pointer">{field.formula ? "원본 수식 확인" : "원본 합산식 확인"}</summary><code className="mt-1 block break-all">{field.formula ?? field.calculationExpression}</code></details> : null}
          {!eligible && field.value !== null ? <p className="mt-1 text-amber-800 dark:text-amber-300">금액 기준·출처 확인 후 직접 검토해야 하는 항목입니다.</p> : null}
        </div>
      </li>;
    })}</ul>
    <button type="button" className={`${button} mt-2 bg-white dark:bg-slate-900`} disabled={disabled || selected.length === 0}
      onClick={() => { onLoad(selected.filter((key) => canLoadWorkbookFinancialField(review, key))); setSelected([]); }}>선택 항목을 검토 입력에 불러오기</button>
    {review.limitations?.length ? <details className="mt-3 text-xs text-slate-600 dark:text-slate-300"><summary className="cursor-pointer">추가 확인 사항</summary><ul className="mt-2 list-disc space-y-1 pl-4">{review.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></details> : null}
  </details>;
}
