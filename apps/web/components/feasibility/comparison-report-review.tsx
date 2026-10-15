"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { backendErrorMessage, parseBackendErrorResponse } from "../../lib/backend-error";
import { CashFlowComparisonDetails, type Comparison, type State } from "./cash-flow-review-workspace";
import { ReportInputReconciliationPanel, type ReportInputReconciliation } from "./report-input-reconciliation";

export type ComparisonReportSnapshot = {
  schemaVersion: string; projectId: string; capturedAt: string; comparison: Comparison;
  selection: { baselineRunId: string; comparisonRunId: string };
  baselineRun?: { inputSnapshot?: { reviewedMonthlyFinancingReview?: { reason?: string } } };
  comparisonRun?: { inputSnapshot?: { reviewedMonthlyFinancingReview?: { reason?: string } } };
};

export type ReportCashFlowSnapshot = {
  calculationRunId: string; createdAt?: string;
  resultSnapshot: { summary: { totalRevenue?: number; totalProjectCost?: number; endingCash?: number } };
};

const calculationLabels = { feasibility: "사업성 요약", pf: "PF 지표", returns: "투자 수익률",
  waterfall: "상환순서 시나리오", unsold_ltv: "미분양 LTV 시나리오", tranche_recovery: "트랜치 회수 시나리오" } as const;
type CalculationSlot = keyof typeof calculationLabels;
type CalculationChoice = { slot: CalculationSlot; calculationRunId: string; createdAt?: string;
  engineKey: string; eligible: boolean; issue?: string; inputHash?: string; resultHash?: string };
export type ReportCalculationSnapshots = Partial<Record<CalculationSlot, { calculationRunId: string; createdAt?: string }>>;

const button = "rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold disabled:opacity-40 dark:border-slate-700";

async function responseBody<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) throw new Error(backendErrorMessage(await parseBackendErrorResponse(response), { action }));
  return response.json() as Promise<T>;
}

export function ComparisonReportReview({ projectId, reportId, snapshot, cashFlowSnapshot, calculationSnapshots, reconciliation, disabled, onCreated, onBusyChange }: {
  projectId: string; reportId?: string; snapshot?: ComparisonReportSnapshot; disabled: boolean;
  cashFlowSnapshot?: ReportCashFlowSnapshot;
  calculationSnapshots?: ReportCalculationSnapshots;
  reconciliation?: ReportInputReconciliation;
  onCreated: (reportId: string) => Promise<void>; onBusyChange: (busy: boolean) => void;
}) {
  const endpoint = `${apiBase}/projects/${encodeURIComponent(projectId)}`;
  const activeEndpoint = useRef<string | null>(null);
  const inFlight = useRef<string | null>(null);
  const activeReport = useRef(reportId);
  const [saved, setSaved] = useState<State | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cashFlowRunId, setCashFlowRunId] = useState("");
  const [choices, setChoices] = useState<CalculationChoice[] | null>(null);
  const [selected, setSelected] = useState<Partial<Record<CalculationSlot, string>>>({});

  useEffect(() => { activeReport.current = reportId; }, [reportId]);

  const load = useCallback(async (signal?: AbortSignal) => {
    const result = await responseBody<State>(await fetch(`${endpoint}/cash-flow/comparison`, {
      cache: "no-store", headers: authHeaders("web"), signal,
    }), "저장된 비교 조회");
    if (!Array.isArray(result.availableRuns) || typeof result.comparisonHash !== "string") {
      throw new Error("저장된 비교를 확인하지 못했습니다. 다시 불러오세요.");
    }
    if (activeEndpoint.current === endpoint) {
      setSaved(result);
      setCashFlowRunId((current) => result.availableRuns.some((run) => run.calculationRunId === current) ? current : "");
    }
  }, [endpoint]);

  useEffect(() => {
    const controller = new AbortController();
    activeEndpoint.current = endpoint;
    setSaved(null); setError(null); setNotice(null); setLoading(true); setBusy(false);
    setCashFlowRunId("");
    setChoices(null); setSelected({});
    onBusyChange(false);
    void load(controller.signal).catch((failure: unknown) => {
      if (!controller.signal.aborted && activeEndpoint.current === endpoint) {
        setError(failure instanceof Error ? failure.message : "저장된 비교를 불러오지 못했습니다.");
      }
    }).finally(() => {
      if (!controller.signal.aborted && activeEndpoint.current === endpoint) setLoading(false);
    });
    return () => { controller.abort(); activeEndpoint.current = null; };
  }, [endpoint, load, onBusyChange]);

  const eligible = Boolean(saved?.selection?.saved && saved.comparison && saved.comparison.status !== "incompatible");
  const locked = disabled || busy || loading;
  const selectedCashFlow = saved?.availableRuns.find((run) => run.calculationRunId === cashFlowRunId);
  const selectedCalculations = (choices ?? []).filter((choice) => choice.eligible && choice.inputHash && choice.resultHash && selected[choice.slot] === choice.calculationRunId);
  const canCreatePf = Boolean(selectedCashFlow?.inputHash && selectedCashFlow?.resultHash) || selectedCalculations.length > 0;

  async function loadCalculations() {
    const result = await responseBody<{ records: CalculationChoice[] }>(await fetch(`${endpoint}/reports/calculation-bases`, {
      cache: "no-store", headers: authHeaders("web"),
    }), "보고서 계산 목록 조회");
    if (!Array.isArray(result.records)) throw new Error("계산 목록을 확인하지 못했습니다. 다시 불러오세요.");
    if (activeEndpoint.current !== endpoint) return;
    setChoices(result.records);
    setSelected((current) => Object.fromEntries(Object.entries(current).filter(([slot, id]) =>
      result.records.some((choice) => choice.slot === slot && choice.calculationRunId === id && choice.eligible))));
  }

  async function perform(action: () => Promise<void>) {
    if (locked || inFlight.current === endpoint) return;
    inFlight.current = endpoint; setBusy(true); onBusyChange(true); setError(null); setNotice(null);
    try { await action(); }
    catch (failure) {
      if (activeEndpoint.current === endpoint) setError(failure instanceof Error ? failure.message : "비교 검토 초안을 처리하지 못했습니다.");
    } finally {
      if (inFlight.current === endpoint) inFlight.current = null;
      if (activeEndpoint.current === endpoint) { setBusy(false); onBusyChange(false); }
    }
  }

  async function create() {
    if (!saved || !eligible) return;
    const report = await responseBody<{ report_id: string }>(await fetch(`${endpoint}/reports/cash-flow-comparison`, {
      method: "POST", headers: { "Content-Type": "application/json", ...authHeaders("web") },
      body: JSON.stringify({ expectedComparisonHash: saved.comparisonHash }),
    }), "비교 검토 초안 생성");
    if (activeEndpoint.current !== endpoint) return;
    if (typeof report.report_id !== "string" || !report.report_id) throw new Error("생성된 초안의 저장 번호를 확인하지 못했습니다.");
    await onCreated(report.report_id);
    if (activeEndpoint.current === endpoint) setNotice("비교 검토 초안을 저장했습니다. 아래 저장 보고서에서 다시 열고 내보낼 수 있습니다.");
  }

  async function createPf() {
    if (!canCreatePf) return;
    const report = await responseBody<{ report_id: string }>(await fetch(`${endpoint}/reports/generate`, {
      method: "POST", headers: { "Content-Type": "application/json", ...authHeaders("web") },
      body: JSON.stringify({ ...(selectedCashFlow ? { cashFlowBasis: { calculationRunId: selectedCashFlow.calculationRunId,
        inputHash: selectedCashFlow.inputHash, resultHash: selectedCashFlow.resultHash } } : {}),
        ...(selectedCalculations.length ? { calculationBases: Object.fromEntries(selectedCalculations.map((choice) =>
          [choice.slot, { calculationRunId: choice.calculationRunId, inputHash: choice.inputHash, resultHash: choice.resultHash }])) } : {}) }),
    }), "저장 계산을 연결한 PF 초안 생성");
    if (activeEndpoint.current !== endpoint) return;
    if (typeof report.report_id !== "string" || !report.report_id) throw new Error("생성된 초안의 저장 번호를 확인하지 못했습니다.");
    await onCreated(report.report_id);
    if (activeEndpoint.current === endpoint) setNotice("선택한 계산을 PF 초안에 고정해 저장했습니다.");
  }

  async function download(format: "pdf" | "docx" | "json") {
    if (!reportId || (!snapshot && !cashFlowSnapshot && !calculationSnapshots)) return;
    const reportEndpoint = `${endpoint}/reports/${encodeURIComponent(reportId)}`;
    const exported = await responseBody<{ export_id: string }>(await fetch(`${reportEndpoint}/exports`, {
      method: "POST", headers: { "Content-Type": "application/json", ...authHeaders("web") },
      body: JSON.stringify({ format }),
    }), "비교 초안 내보내기");
    if (!exported.export_id) throw new Error("내보낸 파일의 저장 번호를 확인하지 못했습니다.");
    if (activeEndpoint.current !== endpoint || activeReport.current !== reportId) return;
    const response = await fetch(`${reportEndpoint}/exports/${encodeURIComponent(exported.export_id)}/download`, {
      cache: "no-store", headers: authHeaders("web"),
    });
    if (!response.ok) throw new Error(backendErrorMessage(await parseBackendErrorResponse(response), { action: "비교 초안 다운로드" }));
    const blob = await response.blob();
    if (activeEndpoint.current !== endpoint || activeReport.current !== reportId) return;
    const url = URL.createObjectURL(blob);
    try {
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `${snapshot ? "cash-flow-comparison-review" : calculationSnapshots ? "pf-calculation-review" : "pf-cash-flow-review"}.${format}`;
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
    } finally { URL.revokeObjectURL(url); }
    setNotice(snapshot
      ? "선택한 초안에 고정된 비교를 내려받았습니다. 분석용 초안이며 최종 승인 자료가 아닙니다."
      : "선택한 초안에 고정된 계산을 내려받았습니다. 분석용 초안이며 최종 승인 자료가 아닙니다.");
  }

  return <section aria-label="저장된 비교와 검토 초안" className="min-w-0 rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-900">
    <h3 className="font-black">저장된 비교로 검토 초안 만들기</h3>
    <p className="mt-2 text-sm leading-6 text-slate-500">두 계산의 지표·입력 변경·근거와 한계를 초안에 고정합니다. 이후 프로젝트 입력이나 비교 선택을 바꿔도 저장한 초안은 유지됩니다. 분석용이며 최종 승인·발행 대상이 아닙니다.</p>
    <div className="mt-3 flex flex-wrap gap-2">
      <button type="button" className={button} disabled={locked || !eligible} onClick={() => void perform(create)}>저장된 비교로 초안 생성·저장</button>
      <button type="button" className={button} disabled={locked} onClick={() => void perform(() => load())}>비교 다시 불러오기</button>
      <Link className="px-2 py-2 text-sm font-bold text-indigo-600" href={`/project/${encodeURIComponent(projectId)}/feasibility`}>기준안·변경안 선택하기</Link>
    </div>
    {loading ? <p className="mt-3 text-sm">저장된 비교를 불러오고 있습니다.</p>
      : eligible && saved?.selection ? <p className="mt-3 break-all text-xs text-slate-500">생성 기준 계산: {saved.selection.baselineRunId} / 변경 계산: {saved.selection.comparisonRunId}</p>
      : <p className="mt-3 text-sm text-amber-700">사업성 화면에서 서로 다른 두 계산을 비교하고 선택을 저장하세요. 저장된 비교가 없거나 계산 기준을 확인해야 합니다.</p>}
    {error ? <p role="alert" className="mt-3 text-sm font-bold text-rose-600">{error}</p> : null}
    {notice ? <p role="status" className="mt-3 text-sm font-bold text-emerald-700">{notice}</p> : null}
    {eligible && saved?.comparison ? <details className="mt-3 text-sm"><summary className="cursor-pointer font-bold">생성에 사용할 현재 비교 확인</summary><CashFlowComparisonDetails comparison={saved.comparison} /></details> : null}
    <div className="mt-5 min-w-0 border-t border-slate-200 pt-4 dark:border-slate-700">
      <h4 className="font-bold">PF 초안의 계산 기준 선택</h4>
      <p className="mt-2 text-sm text-slate-500">현금흐름·사업성·PF·시나리오를 각각 선택할 수 있습니다. 선택한 입력·결과를 보고서에 보관합니다. 서로 다른 실행의 입력 정합성이나 실제 약정이 확인되었다는 뜻은 아닙니다.</p>
      <label className="mt-3 block text-sm font-bold">보고서에 넣을 현금흐름 계산
        <select value={cashFlowRunId} onChange={(event) => setCashFlowRunId(event.target.value)} disabled={locked}
          className="mt-2 w-full min-w-0 rounded-lg border border-slate-300 bg-white p-2 dark:border-slate-700 dark:bg-slate-900">
          <option value="">저장 계산 선택</option>
          {(saved?.availableRuns ?? []).map((run) => <option key={run.calculationRunId} value={run.calculationRunId}>
            {run.label} · {run.policyMode === "reviewed_monthly_v1" ? "월별 금융 검토" : "기존 계산"} · {run.calculationRunId.slice(-8)}
          </option>)}
        </select>
      </label>
      <button type="button" className={`${button} mt-3`} disabled={locked} onClick={() => void perform(loadCalculations)}>사업성·PF·시나리오 계산 불러오기</button>
      {choices !== null ? <div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2">
        <p className="text-sm text-slate-500 sm:col-span-2">계산 종류별 최근 20개 기록입니다. 추가 계산을 선택하면 사업성·상환순서·회수 절의 미선택 항목은 미확인으로 표시합니다. 과거 입력 미보관 기록은 다시 계산해야 합니다.</p>
        {(Object.entries(calculationLabels) as [CalculationSlot, string][]).map(([slot, label]) => <label key={slot} className="min-w-0 text-sm font-bold">보고서에 넣을 {label}
          <select className="mt-2 w-full min-w-0 rounded-lg border border-slate-300 bg-white p-2 dark:border-slate-700 dark:bg-slate-900"
            aria-label={`보고서에 넣을 ${label}`}
            value={selected[slot] ?? ""} disabled={locked} onChange={(event) => setSelected((current) => ({ ...current, [slot]: event.target.value }))}>
            <option value="">미선택</option>
            {choices.filter((choice) => choice.slot === slot).map((choice) => <option key={choice.calculationRunId} value={choice.calculationRunId} disabled={!choice.eligible}>
              {choice.createdAt || "시각 미기록"} · {choice.calculationRunId.slice(-8)}{choice.issue ? ` · ${choice.issue}` : ""}
            </option>)}
          </select>
        </label>)}
        {!choices.some((choice) => choice.eligible) ? <p className="text-sm text-amber-700 sm:col-span-2">선택 가능한 추가 계산이 없습니다. 해당 분석을 실행·저장한 뒤 다시 불러오세요.</p> : null}
      </div> : null}
      <button type="button" className={`${button} mt-3`} disabled={locked || !canCreatePf} onClick={() => void perform(createPf)}>선택 계산으로 PF 초안 생성</button>
      {!loading && !saved?.availableRuns.length ? <p className="mt-2 text-sm text-amber-700">선택 가능한 현금흐름 계산이 없습니다. 사업성 화면에서 현금흐름 계산을 저장한 뒤 다시 불러오세요.</p> : null}
    </div>
    {cashFlowSnapshot ? <div aria-label="PF 초안에 고정된 현금흐름" className="mt-5 min-w-0 border-t border-slate-200 pt-4 dark:border-slate-700">
      <h4 className="font-bold">선택한 PF 초안에 고정된 현금흐름</h4>
      <p className="mt-2 break-all text-xs">계산 기록: {cashFlowSnapshot.calculationRunId}</p>
      <p className="mt-2 text-sm text-amber-700">요약 현금흐름표의 기준입니다. 다른 절까지 동일 실행으로 검증된 것은 아닙니다.</p>
      <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-3">
        {([["총매출", "totalRevenue"], ["총사업비", "totalProjectCost"], ["기말 현금", "endingCash"]] as const).map(([label, key]) => {
          const value = cashFlowSnapshot.resultSnapshot.summary[key];
          return <div key={key}><dt>{label}</dt><dd className="break-words font-bold">{typeof value === "number" && Number.isFinite(value) ? `${value.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}원` : "미확인"}</dd></div>;
        })}
      </dl>
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className={button} disabled={locked || !reportId} onClick={() => void perform(() => download("json"))}>입력·결과 JSON 다운로드</button>
        <button type="button" className={button} disabled={locked || !reportId} onClick={() => void perform(() => download("pdf"))}>이 PF 초안 PDF 다운로드</button>
        <button type="button" className={button} disabled={locked || !reportId} onClick={() => void perform(() => download("docx"))}>이 PF 초안 Word 다운로드</button>
      </div>
    </div> : null}
    {calculationSnapshots ? <div aria-label="PF 초안에 고정된 추가 계산" className="mt-5 min-w-0 border-t border-slate-200 pt-4 dark:border-slate-700">
      <h4 className="font-bold">선택한 PF 초안에 고정된 사업성·PF·시나리오</h4>
      <p className="mt-2 text-sm text-amber-700">아래는 저장 보고서의 계산입니다. 현재 선택과 다를 수 있으며 각 실행의 입력·결과는 JSON에 함께 보관됩니다. 미선택 항목은 미확인입니다.</p>
      <ul className="mt-2 space-y-2 text-sm">{(Object.entries(calculationSnapshots) as [CalculationSlot, { calculationRunId: string }][]).map(([slot, run]) =>
        <li className="break-all" key={slot}>{calculationLabels[slot]}: {run.calculationRunId}</li>)}</ul>
      {!cashFlowSnapshot ? <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className={button} disabled={locked || !reportId} onClick={() => void perform(() => download("json"))}>입력·결과 JSON 다운로드</button>
        <button type="button" className={button} disabled={locked || !reportId} onClick={() => void perform(() => download("pdf"))}>이 PF 초안 PDF 다운로드</button>
        <button type="button" className={button} disabled={locked || !reportId} onClick={() => void perform(() => download("docx"))}>이 PF 초안 Word 다운로드</button>
      </div> : null}
    </div> : null}
    {(cashFlowSnapshot || calculationSnapshots) ? <ReportInputReconciliationPanel value={reconciliation} /> : null}
    {snapshot ? <div aria-label="초안에 고정된 비교" className="mt-5 min-w-0 border-t border-slate-200 pt-4 dark:border-slate-700">
      <h4 className="font-bold">선택한 초안에 고정된 비교</h4>
      <p className="mt-2 break-all text-xs text-slate-500">기준 계산: {snapshot.selection.baselineRunId} / 변경 계산: {snapshot.selection.comparisonRunId}</p>
      <p className="mt-1 text-xs text-slate-500">아래 값은 저장한 초안의 값입니다. 현재 비교 선택과 다를 수 있습니다.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className={button} disabled={locked || !reportId} onClick={() => void perform(() => download("pdf"))}>이 초안 PDF 다운로드</button>
        <button type="button" className={button} disabled={locked || !reportId} onClick={() => void perform(() => download("docx"))}>이 초안 Word 다운로드</button>
      </div>
      <p className="mt-2 break-words text-sm">기준안 검토 이유: {snapshot.baselineRun?.inputSnapshot?.reviewedMonthlyFinancingReview?.reason || "미기록"}</p>
      <p className="mt-1 break-words text-sm">변경안 검토 이유: {snapshot.comparisonRun?.inputSnapshot?.reviewedMonthlyFinancingReview?.reason || "미기록"}</p>
      <CashFlowComparisonDetails comparison={snapshot.comparison} />
    </div> : null}
  </section>;
}
