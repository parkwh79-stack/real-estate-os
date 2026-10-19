"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { backendErrorMessage, parseBackendErrorResponse } from "../../lib/backend-error";
import type { CashflowResult } from "../../lib/project-client";

const MONTHLY_POLICY = {
  method: "reviewed_monthly_v1", interestBasis: "opening_balance", annualDayBasis: "monthly_12",
  feeBasis: "committed_limit_first_draw", maturityBasis: "project_month_zero", repaymentSource: "repayment_reserve",
  costTreatment: "replace_budget_finance_cost", overdueInterestBasis: "contract_rate_simple", revolving: "non_revolving",
  rounding: "unrounded_analysis",
} as const;
type Run = { calculationRunId: string; label: string; createdAt?: string; policyMode?: string;
  inputHash?: string; resultHash?: string;
  period: { baseMonth: string | null; lastMonthIndex: number | null }; sourceHashes?: string[];
  inputReviews?: Array<{ field: string; label: string; classification: string; reason: string; sourceName?: string | null; sourceCell?: string | null }> };
export type Comparison = {
  status: string; reasons: string[]; baseline?: Run; comparison?: Run; sourceChanged?: boolean;
  metrics: Array<{ key: string; label: string; unit: string; baselineValue: number | null; comparisonValue: number | null; delta: number | null }>;
  inputChanges: Array<{ path: string; label?: string; before: unknown; after: unknown }>;
  inputChangeCount?: number; inputChangesTruncated?: boolean;
};
export type State = { comparisonHash: string; availableRuns: Run[]; selection: { baselineRunId: string; comparisonRunId: string; saved: boolean } | null;
  comparison: Comparison | null };
const control = "min-w-0 w-full rounded-lg border border-slate-300 bg-white p-2 text-sm dark:border-slate-700 dark:bg-slate-900";
const button = "rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold disabled:opacity-40 dark:border-slate-700";
const reasons: Record<string, string> = {
  period_mismatch: "기간이 달라 사업 전체 집계만 비교합니다. 월별 수치를 같은 달로 정렬하지 않습니다.",
  financing_policy_mismatch: "금융비용 계산 방식이 다른 두 안입니다.",
  same_calculation_run: "서로 다른 두 계산을 선택하세요.",
  saved_calculation_changed: "저장된 계산 기록이 바뀌었습니다. 계산 기준을 다시 확인하세요.",
  saved_calculation_missing: "저장된 선택의 계산 기록을 찾지 못했습니다.",
  unsupported_currency_or_unit: "통화 또는 금액 단위가 달라 비교할 수 없습니다.",
  invalid_month_indices: "월별 계산 기간을 확인할 수 없습니다.",
  financial_input_hash_mismatch: "계산 입력 기록의 일치 여부를 확인할 수 없습니다.",
  calculation_not_completed: "완료된 계산 기록을 선택하세요.",
};

async function read<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) throw new Error(backendErrorMessage(await parseBackendErrorResponse(response), { action }));
  return response.json() as Promise<T>;
}
async function readState(response: Response, action: string): Promise<State> {
  const saved = await read<State>(response, action);
  if (!Array.isArray(saved.availableRuns) || typeof saved.comparisonHash !== "string") {
    throw new Error("계산 목록 응답을 확인할 수 없습니다. 잠시 후 다시 불러오세요.");
  }
  return saved;
}
function amount(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "미확인";
  return Math.abs(value) < 0.5 ? "0" : value.toLocaleString("ko-KR", { maximumFractionDigits: 0 });
}
function period(run?: Run): string {
  if (!run) return "미확인";
  return `${run.period.baseMonth || "명시된 월차"} · M0~M${run.period.lastMonthIndex ?? "?"}`;
}
function runLabel(run: Run): string {
  const parsed = run.createdAt ? new Date(run.createdAt) : null;
  const date = parsed && !Number.isNaN(parsed.getTime()) ? parsed.toLocaleString("ko-KR") : run.label;
  return `${date} · ${run.policyMode === "reviewed_monthly_v1" ? "월별 금융 검토" : "기존 계산"} · ${run.calculationRunId.slice(-8)}`;
}
function changeValue(value: unknown, path: string): string {
  if (value === null || value === undefined) return "미입력";
  if (typeof value === "number") {
    if (/interestRate|feeRate|interest_rate|fee_rate/.test(path)) {
      const percent = value * 100;
      return `${Number.isInteger(percent) ? "" : "약 "}${amount(percent)}%`;
    }
    if (/amount|Cost|Revenue|Loan|equity|financialCost/i.test(path)) return `${amount(value)}원`;
    if (/month/i.test(path)) return `${amount(value)}개월`;
    return amount(value);
  }
  if (typeof value === "string") return value;
  if (path === "monthlyFinancing") return "월별 금융 검토 방식";
  return Array.isArray(value) ? `${value.length}개 항목` : "입력 묶음";
}

export function CashFlowReviewWorkspace({ projectId, cashflow, disabled = false, onCalculated, onBusyChange }: {
  projectId: string; disabled?: boolean; onCalculated: () => Promise<void>; onBusyChange: (busy: boolean) => void;
  cashflow: CashflowResult | null;
}) {
  const endpoint = `${apiBase}/projects/${encodeURIComponent(projectId)}`;
  const activeEndpoint = useRef<string | null>(null);
  const inFlight = useRef<string | null>(null);
  const [state, setState] = useState<State | null>(null);
  const [reviewHash, setReviewHash] = useState("");
  const [baseline, setBaseline] = useState("");
  const [changed, setChanged] = useState("");
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [selectionDirty, setSelectionDirty] = useState(false);
  const [method, setMethod] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const headers = () => authHeaders("web");

  const load = useCallback(async () => {
    const responses = await Promise.all([
      fetch(`${endpoint}/cash-flow/comparison`, { cache: "no-store", headers: authHeaders("web") }).then((r) => readState(r, "비교 조회")),
      fetch(`${endpoint}/feasibility-workbook/review`, { cache: "no-store", headers: authHeaders("web") }).then((r) => read<{ reviewHash: string }>(r, "검토 입력 조회")),
    ]);
    return { saved: responses[0], review: responses[1] };
  }, [endpoint]);
  const restore = (saved: State, hash: string) => {
    setState(saved); setReviewHash(hash); setBaseline(saved.selection?.baselineRunId ?? "");
    setChanged(saved.selection?.comparisonRunId ?? ""); setComparison(saved.comparison); setSelectionDirty(false);
  };
  useEffect(() => {
    activeEndpoint.current = endpoint; setBusy(true); setState(null); setReviewHash("");
    setBaseline(""); setChanged(""); setComparison(null); setSelectionDirty(false); setMethod(""); setReason("");
    setError(null); setNotice(null);
    void load().then(({ saved, review }) => { if (activeEndpoint.current === endpoint) restore(saved, review.reviewHash); })
      .catch((caught) => { if (activeEndpoint.current === endpoint) setError(caught instanceof Error ? caught.message : "계산 목록을 불러오지 못했습니다."); })
      .finally(() => { if (activeEndpoint.current === endpoint) setBusy(false); });
    return () => { activeEndpoint.current = null; if (inFlight.current === endpoint) inFlight.current = null; onBusyChange(false); };
  }, [endpoint, load, onBusyChange]);

  async function perform(action: () => Promise<void>) {
    if (inFlight.current === endpoint || activeEndpoint.current !== endpoint) return;
    inFlight.current = endpoint; setBusy(true); onBusyChange(true); setError(null); setNotice(null);
    try { await action(); }
    catch (caught) { if (activeEndpoint.current === endpoint) setError(caught instanceof Error ? caught.message : "처리하지 못했습니다."); }
    finally { if (inFlight.current === endpoint) inFlight.current = null; if (activeEndpoint.current === endpoint) { setBusy(false); onBusyChange(false); } }
  }
  async function calculate() {
    if (!method) throw new Error("월별 금융 계산 방식을 선택하세요.");
    if (!reason.trim()) throw new Error("월별 금융 검토 이유를 입력하세요.");
    if (!reviewHash) throw new Error("최신 입력을 먼저 불러오세요.");
    const result = await read<{ calculationRunId: string }>(await fetch(`${endpoint}/cash-flow/run`, {
      method: "POST", headers: { ...headers(), "Content-Type": "application/json" },
      body: JSON.stringify({ reviewedMonthlyFinancing: { policy: MONTHLY_POLICY, reason: reason.trim(), expectedReviewHash: reviewHash } }),
    }), "월별 금융 검토 계산");
    if (activeEndpoint.current !== endpoint) return;
    const { saved, review } = await load();
    if (activeEndpoint.current !== endpoint) return;
    const base = baseline || state?.availableRuns[0]?.calculationRunId || "";
    setState(saved); setReviewHash(review.reviewHash); setBaseline(base); setChanged(result.calculationRunId);
    setComparison(null); setSelectionDirty(true);
    setNotice("월별 금융 검토안을 계산·저장했습니다. 기준 계산을 선택해 차이를 확인하고 비교 선택을 저장하세요.");
    await onCalculated();
  }
  async function preview() {
    if (!baseline || !changed) throw new Error("기준 계산과 변경 계산을 선택하세요.");
    const params = new URLSearchParams({ baselineRunId: baseline, comparisonRunId: changed });
    const saved = await readState(await fetch(`${endpoint}/cash-flow/comparison?${params}`, { cache: "no-store", headers: headers() }), "차이 확인");
    if (activeEndpoint.current === endpoint) setComparison(saved.comparison);
  }
  async function save() {
    if (!baseline || !changed || !state?.comparisonHash) throw new Error("기준 계산과 변경 계산을 선택하고 최신 기록을 불러오세요.");
    const saved = await readState(await fetch(`${endpoint}/cash-flow/comparison`, { method: "POST",
      headers: { ...headers(), "Content-Type": "application/json" }, body: JSON.stringify({ baselineRunId: baseline,
        comparisonRunId: changed, expectedComparisonHash: state.comparisonHash }) }), "비교 저장");
    if (activeEndpoint.current !== endpoint) return;
    restore(saved, reviewHash); setNotice("비교 선택을 저장했습니다. 같은 프로젝트를 다시 열어 이어볼 수 있습니다.");
  }
  const locked = disabled || busy;
  return <section aria-label="금융 검토안 계산과 비교" className="rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-900">
    <h2 className="font-black">금융 조건 변경안 계산·비교</h2>
    <p className="mt-2 text-sm text-slate-500">저장한 금액·사업기간·대출 조건으로 별도 검토안을 만듭니다. 원본과 승인된 기준안은 유지되며 결과는 분석용입니다.</p>
    <Link href={`/project/${encodeURIComponent(projectId)}/input-package`} className="mt-2 inline-block text-sm font-bold text-indigo-600">저장된 입력·대출 조건 확인</Link>
    <fieldset disabled={locked} className="mt-4 min-w-0 space-y-3">
      <label className="grid gap-1 text-sm font-bold">월별 금융 계산 방식
        <select aria-label="월별 금융 계산 방식" value={method} className={control} onChange={(e) => setMethod(e.target.value)}>
          <option value="">계산 방식을 직접 선택하세요</option><option value="reviewed_monthly_v1">월초 잔액 단리·상환계정 만기상환</option>
        </select>
      </label>
      <p className="text-xs leading-5 text-slate-500">월초 잔액 단리 · 약정한도 기준 최초 수수료 · M0 기준 만기부터 상환계정으로 원금 상환 · 예산 금융비 대체</p>
      <details className="text-xs leading-5 text-slate-500">
      <summary className="cursor-pointer font-bold">계산 기준과 지원 범위</summary>
      <ul className="mt-2 list-disc space-y-1 pl-5">
        <li>월초 대출잔액 × 연 금리 ÷ 12. 당월 인출분의 이자는 다음 달부터 계산합니다.</li>
        <li>첫 인출월에 약정한도 × 수수료율을 한 번 지출하며, 기존 예산 금융비용을 새 이자·수수료로 대체합니다.</li>
        <li>기준월 M0부터 입력한 만기 개월에 상환을 시작합니다. 만기 이후 신규 인출과 상환한 한도의 재사용은 지원하지 않습니다.</li>
        <li>분양대금 상환계정에서 순위에 따라 원금만 상환합니다. 이자·수수료는 운영계정에서 지급하며 계정 간 자유 이체를 가정하지 않습니다.</li>
        <li>미상환 원금은 만기 후에도 같은 금리로 단리 계산합니다. 연체 가산금리·변동금리·이자 자본화·일수 이자는 지원하지 않습니다.</li>
        <li>중간 계산은 원 미만도 유지하고 화면에서만 반올림합니다. 금리·수수료·만기·상환재원이 미입력이면 계산을 진행할 수 없습니다.</li>
      </ul>
      </details>
      <label className="grid gap-1 text-sm">월별 금융 검토 이유<textarea aria-label="월별 금융 검토 이유" value={reason}
        onChange={(e) => setReason(e.target.value)} maxLength={4000} className={control} placeholder="이 방식으로 가정을 검토하는 이유" /></label>
      <button type="button" className={button} disabled={!reviewHash} onClick={() => void perform(calculate)}>월별 금융 검토안 계산·저장</button>
    </fieldset>
    <div className="mt-5 border-t border-slate-200 pt-4 dark:border-slate-700">
      <h3 className="font-bold">저장된 기준안·변경안 비교{selectionDirty ? " · 선택 저장 전" : state?.selection ? " · 저장됨" : ""}</h3>
      <div className="mt-3 grid gap-3 md:grid-cols-2">{([ ["기준 계산", baseline, setBaseline], ["변경 계산", changed, setChanged] ] as const).map(([label, value, setValue]) =>
        <label key={label} className="grid gap-1 text-sm">{label}<select aria-label={label} disabled={locked} className={control} value={value}
          onChange={(e) => { setValue(e.target.value); setSelectionDirty(true); setComparison(null); setNotice(null); }}>
          <option value="">저장된 계산 선택</option>{(state?.availableRuns ?? []).map((run) => <option key={run.calculationRunId} value={run.calculationRunId}>{runLabel(run)}</option>)}
        </select></label>)}</div>
      {state && state.availableRuns.length < 2 ? <p className="mt-2 text-sm text-slate-500">비교하려면 서로 다른 두 계산을 저장하세요. 기존 Cash Flow 재계산과 월별 금융 검토안 계산을 이용할 수 있습니다.</p> : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className={button} disabled={locked || !baseline || !changed} onClick={() => void perform(preview)}>차이 확인</button>
        <button type="button" className={button} disabled={locked || !baseline || !changed || !selectionDirty || comparison?.status === "incompatible"} onClick={() => void perform(save)}>비교 선택 저장</button>
        <button type="button" className={button} disabled={locked} onClick={() => void perform(async () => {
          const { saved, review } = await load(); if (activeEndpoint.current === endpoint) restore(saved, review.reviewHash);
        })}>입력·계산 목록 새로고침{selectionDirty ? " (선택 취소)" : ""}</button>
      </div>
    </div>
    {busy ? <p role="status" className="mt-3 text-sm">계산 기록을 처리하고 있습니다.</p> : null}
    {error ? <p role="alert" className="mt-3 text-sm font-bold text-rose-600">{error}</p> : null}
    {notice ? <p role="status" className="mt-3 text-sm font-bold text-emerald-700">{notice}</p> : null}
    {cashflow?.monthlyFinancing && cashflow.financingMonthly?.length ? <details className="mt-4 rounded-lg border border-slate-200 p-3 dark:border-slate-700" open>
      <summary className="cursor-pointer text-sm font-bold">최근 저장된 월별 금융 원장 · 분석용</summary>
      <p className="mt-2 text-xs text-slate-500">예산 금융비용 {amount(typeof cashflow.monthlyFinancing.budgetFinancialCost === "number" ? cashflow.monthlyFinancing.budgetFinancialCost : null)}원
        → 계산 금융비용 {amount(typeof cashflow.monthlyFinancing.calculatedFinancialCost === "number" ? cashflow.monthlyFinancing.calculatedFinancialCost : null)}원.
        원금 상환은 사업비와 구분합니다. 아래 금액은 원 단위 표시입니다.</p>
      <div className="mt-2 max-h-96 overflow-auto"><table className="w-full min-w-[650px] text-right text-xs">
        <thead><tr>{["월", "이자", "수수료", "원금 상환", "대출잔액", "만기 후 잔액", "미충당 운영·금융비"].map((label) => <th key={label} className="p-2">{label}</th>)}</tr></thead>
        <tbody>{cashflow.financingMonthly.map((row) => <tr key={row.period} className="border-t border-slate-200 dark:border-slate-700">
          <th className="p-2">{row.period}</th>{[row.interest, row.fees, row.principalRepayment, row.outstandingDebt, row.overdueDebt, row.unfundedOperatingCost]
            .map((value, index) => <td key={index} className="p-2">{amount(value)}</td>)}
        </tr>)}</tbody></table></div>
    </details> : null}
    {comparison ? <CashFlowComparisonDetails comparison={comparison} /> : null}
    {comparison && comparison.status !== "incompatible" && state?.selection?.saved && !selectionDirty
      ? <Link className="mt-4 inline-block text-sm font-bold text-indigo-600" href={`/project/${encodeURIComponent(projectId)}/report`}>저장된 비교로 검토 초안 만들기</Link> : null}
  </section>;
}

export function CashFlowComparisonDetails({ comparison }: { comparison: Comparison }) {
  return <div className="mt-4 space-y-3">
      {comparison.reasons.map((code) => <p key={code} className="text-sm font-bold text-amber-700">{reasons[code] ?? "계산 기준을 확인해 주세요. 비교할 수 없는 입력이 있습니다."}</p>)}
      {comparison.sourceChanged ? <p className="text-sm text-amber-700">두 계산의 원본·근거 기준도 변경되었습니다.</p> : null}
      <p className="text-xs text-slate-500">기준: {period(comparison.baseline)} / 변경: {period(comparison.comparison)}</p>
      <div aria-label="계산 당시 입력 근거와 가정" className="space-y-3 text-sm">
        <p className="font-bold">입력 근거와 사용자 가정</p>
        <p className="text-xs text-slate-500">각 계산에 저장된 검토 기록입니다. 최종 승인 자료가 없어도 가능한 분석은 계속할 수 있으며, 원본 대조와 최종 승인은 구분합니다.</p>
        {([['기준안', comparison.baseline], ['변경안', comparison.comparison]] as const).map(([label, run]) =>
          <div key={label} className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
            <p className="font-semibold">{label}</p>
            {run?.inputReviews?.length ? <ul className="mt-2 space-y-2">{run.inputReviews.map((row) =>
              <li key={row.field} className="break-words"><p>{row.label} · {row.classification}</p><p>{row.reason}</p>
                {row.sourceName || row.sourceCell ? <p className="text-xs text-slate-500">{[row.sourceName, row.sourceCell].filter(Boolean).join(' · ')}</p> : null}
              </li>)}</ul> : <p className="mt-2 text-slate-500">이 계산에는 항목별 검토 기록이 없습니다. 현재 프로젝트의 메모를 대신 표시하지 않습니다.</p>}
          </div>)}
      </div>
      {comparison.metrics.length ? <div className="overflow-x-auto"><table className="w-full min-w-[440px] text-right text-sm">
        <caption className="mb-2 text-left text-xs text-slate-500">사업 전체 집계 · 원 · 표시값 소수 둘째 자리 반올림 · 차이 = 변경 − 기준</caption>
        <thead><tr><th className="p-2 text-left">항목</th><th className="p-2">기준</th><th className="p-2">변경</th><th className="p-2">차이</th></tr></thead>
        <tbody>{comparison.metrics.map((metric) => <tr key={metric.key} className="border-t border-slate-200 dark:border-slate-700">
          <th className="p-2 text-left font-medium">{metric.label}</th><td className="p-2">{amount(metric.baselineValue)}</td><td className="p-2">{amount(metric.comparisonValue)}</td><td className="p-2">{amount(metric.delta)}</td>
        </tr>)}</tbody></table></div> : null}
      <div className="text-sm"><p className="font-bold">수치·조건 변경 {comparison.inputChangeCount ?? comparison.inputChanges.length}건</p>
        {comparison.inputChanges.length ? <ul className="mt-2 space-y-1">{comparison.inputChanges.slice(0, 30).map((change, index) =>
          <li key={`${change.path}-${index}`} className="break-words">{change.label ?? "검토 입력"}: {changeValue(change.before, change.path)} → {changeValue(change.after, change.path)}</li>)}</ul>
          : <p className="mt-2 text-slate-500">비교 대상 업무 입력의 차이가 없습니다. 계산 기록과 근거는 각 실행에 보존됩니다.</p>}
        {comparison.inputChanges.length > 30 || comparison.inputChangesTruncated ? <p className="mt-2 text-xs text-slate-500">처음 30개 변경을 표시합니다.</p> : null}
      </div>
      <p className="text-xs text-slate-500">이 비교는 검토용이며 승인·발행 상태를 부여하지 않습니다. 월별 검토안의 기존 일괄 상환·스트레스 시나리오는 별도 계산 기준 확인이 필요합니다.</p>
    </div>;
}
