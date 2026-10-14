"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { backendErrorMessage, parseBackendErrorResponse } from "../../lib/backend-error";
import { CashFlowComparisonDetails, type Comparison, type State } from "./cash-flow-review-workspace";

export type ComparisonReportSnapshot = {
  schemaVersion: string; projectId: string; capturedAt: string; comparison: Comparison;
  selection: { baselineRunId: string; comparisonRunId: string };
  baselineRun?: { inputSnapshot?: { reviewedMonthlyFinancingReview?: { reason?: string } } };
  comparisonRun?: { inputSnapshot?: { reviewedMonthlyFinancingReview?: { reason?: string } } };
};

const button = "rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold disabled:opacity-40 dark:border-slate-700";

async function responseBody<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) throw new Error(backendErrorMessage(await parseBackendErrorResponse(response), { action }));
  return response.json() as Promise<T>;
}

export function ComparisonReportReview({ projectId, reportId, snapshot, disabled, onCreated, onBusyChange }: {
  projectId: string; reportId?: string; snapshot?: ComparisonReportSnapshot; disabled: boolean;
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

  useEffect(() => { activeReport.current = reportId; }, [reportId]);

  const load = useCallback(async (signal?: AbortSignal) => {
    const result = await responseBody<State>(await fetch(`${endpoint}/cash-flow/comparison`, {
      cache: "no-store", headers: authHeaders("web"), signal,
    }), "저장된 비교 조회");
    if (!Array.isArray(result.availableRuns) || typeof result.comparisonHash !== "string") {
      throw new Error("저장된 비교를 확인하지 못했습니다. 다시 불러오세요.");
    }
    if (activeEndpoint.current === endpoint) setSaved(result);
  }, [endpoint]);

  useEffect(() => {
    const controller = new AbortController();
    activeEndpoint.current = endpoint;
    setSaved(null); setError(null); setNotice(null); setLoading(true); setBusy(false);
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

  async function download(format: "pdf" | "docx") {
    if (!reportId || !snapshot) return;
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
      anchor.href = url; anchor.download = `cash-flow-comparison-review.${format}`;
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
    } finally { URL.revokeObjectURL(url); }
    setNotice("선택한 초안에 고정된 비교를 내려받았습니다. 분석용 초안이며 최종 승인 자료가 아닙니다.");
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
