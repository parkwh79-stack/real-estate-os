"use client";

import { useId, useMemo } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { CashflowResult } from "../../lib/project-client";

const incomeColor = "var(--re-success-600)";
const expenseColor = "var(--re-warning-600)";
const balanceColor = "var(--re-action-600)";
const deficitColor = "var(--re-danger-600)";
const amountFormatter = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });

function displayAmount(value: number | null): string {
  return value === null ? "미확인" : `${amountFormatter.format(Math.abs(value) < 0.5 ? 0 : value)}억원`;
}

export function CashFlowPeriodChart({ cashflow }: { cashflow: CashflowResult }) {
  const titleId = useId();
  const sourceSequence = cashflow.periodBasis?.kind === "source_sequence";
  const capturedSource = cashflow.sourceBasis === "captured_workbook_cash_flow";
  const monthlyReview = cashflow.monthlyFinancing != null || cashflow.inputSnapshot?.monthlyFinancing != null;
  const balanceLabel = capturedSource ? "조달 전 누적 영업현금" : monthlyReview ? "월말 계정합계" : "누적 현금흐름";
  const incomeLabel = capturedSource ? "영업 수입" : "수입";
  const expenseLabel = capturedSource ? "영업 지급" : "지출";
  const data = useMemo(() => cashflow.monthly.map((row, index) => ({
    ...row,
    axisKey: String(index),
    // Expenses are drawn in the opposite direction; keep the saved value in the tooltip.
    expensePlotEok: row.cashOutEok === null ? null : -row.cashOutEok,
  })), [cashflow.monthly]);
  const deficit = cashflow.maxMinusEok;
  const deficitRow = cashflow.maxMinusPeriodIndex === null ? undefined : data[cashflow.maxMinusPeriodIndex];
  // The monthly account balance can include PF draw and repayment. The
  // maximum deficit marker uses the separate, stored pre-draw cumulative row.
  const verifiedDeficit = deficit !== null && deficit < 0 && deficitRow?.prePfCumulativeEok === deficit
    ? { amount: deficit, row: deficitRow } : null;
  const hasValues = data.some((row) => row.cashInEok !== null || row.cashOutEok !== null || row.cumulativeEok !== null)
    || verifiedDeficit !== null;

  return (
    <figure aria-labelledby={titleId} className="min-w-0 rounded-re-panel border border-re-border bg-re-surface p-4 shadow-sm sm:p-5">
      <figcaption>
        <h2 id={titleId} className="text-lg font-bold text-re-text">현금흐름 추이</h2>
        <p className="mt-1 text-xs leading-5 text-re-secondary">
          {sourceSequence ? "원본 사건·기간 순서" : cashflow.periodBasis ? "저장된 달력·월차" : "저장 행 순서 · 기간 기준 미기록"}
          {" · 억원 · 표시값은 정수 반올림"}
        </p>
        {capturedSource ? <p className="mt-1 text-xs leading-5 text-re-secondary">
          PF·자기자본·보증금 등 조달 제외 · 최종 심사보고서 확정값 아님
        </p> : null}
      </figcaption>
      {hasValues ? <>
        <ul aria-label="차트 범례" className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-xs text-re-secondary">
          <li className="flex items-center gap-1.5"><span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: incomeColor }} />{incomeLabel}</li>
          <li className="flex items-center gap-1.5"><span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: expenseColor }} />{expenseLabel}</li>
          <li className="flex items-center gap-1.5"><span aria-hidden="true" className="h-0.5 w-4" style={{ backgroundColor: balanceColor }} />{balanceLabel}</li>
          <li className="flex items-center gap-1.5"><span aria-hidden="true" className="h-0 w-4 border-t border-dashed border-re-muted" />0원 기준선</li>
        </ul>
        <div className="mt-3 h-64 min-w-0 sm:h-80">
          <ResponsiveContainer width="100%" height="100%" minWidth={0} debounce={80}>
            <ComposedChart data={data} margin={{ top: 12, right: 12, bottom: 4, left: 0 }} accessibilityLayer aria-label="기간별 수입·지출과 누적 잔액">
              <CartesianGrid stroke="var(--re-border-default)" vertical={false} />
              <XAxis dataKey="axisKey" minTickGap={20} tick={{ fill: "var(--re-text-secondary)", fontSize: 11 }} tickLine={false}
                tickFormatter={(key: string) => {
                  const period = data[Number(key)]?.period ?? "";
                  return sourceSequence ? period.match(/^원본 \d+/)?.[0] ?? `#${Number(key) + 1}` : period;
                }} />
              <YAxis width={55} domain={["auto", "auto"]} allowDecimals={false} tickLine={false} axisLine={false}
                tick={{ fill: "var(--re-text-secondary)", fontSize: 11 }} tickFormatter={(value: number) => amountFormatter.format(value)} />
              <Tooltip filterNull={false} isAnimationActive={false}
                content={({ active, label }) => {
                  const row = active && label != null ? data[Number(label)] : undefined;
                  if (!row) return null;
                  return <div role="status" aria-atomic="true" className="max-w-56 rounded-lg border border-re-border bg-re-surface px-3 py-2 text-xs text-re-text shadow-sm">
                    <p className="font-bold">{row.period}</p>
                    <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1">
                      <dt>{incomeLabel}</dt><dd className="text-right">{displayAmount(row.cashInEok)}</dd>
                      <dt>{expenseLabel}</dt><dd className="text-right">{displayAmount(row.cashOutEok)}</dd>
                      <dt>{balanceLabel}</dt><dd className="text-right">{displayAmount(row.cumulativeEok)}</dd>
                      {verifiedDeficit?.row.axisKey === row.axisKey ? <><dt>최대 자금 부족</dt><dd className="text-right">{displayAmount(-verifiedDeficit.amount)}</dd></> : null}
                    </dl>
                  </div>;
                }} />
              <ReferenceLine y={0} ifOverflow="extendDomain" stroke="var(--re-text-muted)" strokeDasharray="4 3" />
              <Bar dataKey="cashInEok" name={incomeLabel} fill={incomeColor} maxBarSize={24} isAnimationActive={false} />
              <Bar dataKey="expensePlotEok" name={expenseLabel} fill={expenseColor} maxBarSize={24} isAnimationActive={false} />
              <Line type="linear" dataKey="cumulativeEok" name={balanceLabel} stroke={balanceColor} strokeWidth={2} dot={{ r: 2 }} connectNulls={false} isAnimationActive={false} />
              {verifiedDeficit ? <ReferenceDot x={verifiedDeficit.row.axisKey} y={verifiedDeficit.amount} ifOverflow="extendDomain"
                r={5} fill={deficitColor} stroke="var(--re-bg-surface)" strokeWidth={2} className="cashflow-maximum-deficit" /> : null}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </> : <p className="mt-4 text-sm text-re-secondary">차트에 표시할 저장 금액이 없습니다.</p>}
      {verifiedDeficit ? <p className="mt-2 text-sm font-bold text-re-danger">
        최대 자금 부족 {displayAmount(-verifiedDeficit.amount)} · {verifiedDeficit.row.period}
      </p> : <p className="mt-2 text-sm text-re-secondary">
        {deficit === 0 ? "저장 계산에서 자금 부족이 발생하지 않았습니다."
          : "저장된 최대 자금 부족의 위치를 확인할 수 없습니다."}
      </p>}
      <p className="mt-2 text-xs leading-5 text-re-secondary">
        지출 막대는 저장값의 부호를 반대로 표시하며, 미확인 값은 비워 둡니다.
        {sourceSequence ? " 가로 간격은 실제 경과 기간을 나타내지 않습니다." : ""}
        {monthlyReview && !capturedSource && verifiedDeficit ? " 최대 자금 부족 표시는 PF 인출 전 기준입니다." : ""}
      </p>
    </figure>
  );
}
