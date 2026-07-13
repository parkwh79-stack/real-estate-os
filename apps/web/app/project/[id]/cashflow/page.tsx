"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useParams } from "next/navigation";
import { AlertTriangle, ArrowLeft, Calculator, Download, Loader2, PlayCircle, RefreshCw, ShieldCheck, TrendingDown, Wallet } from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import { fetchLatestCalculationRuns, fetchLatestCashflow, type CalculationRunRecord, type CashflowResult } from "../../../../lib/project-client";

const SCENARIO_ENGINES = ["waterfall", "tranche_recovery", "unsold_ltv", "discount_stress"];

const FEAS_METHOD_LABEL: Record<string, string> = {
  feasibility_workbook_v2: "사업수지 워크북(정본)",
  parsed_fact: "파싱된 사업수지 후보값",
  derived: "토지·규제·상품기획 기반 추정",
  no_source: "근거 없음",
};

function formatEok(value?: number, digits = 1): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  return `${value.toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits })}억원`;
}

function formatValue(value: unknown): string {
  if (typeof value !== "number") return String(value ?? "-");
  return value.toLocaleString("ko-KR", { maximumFractionDigits: 4 });
}

function metric(run: CalculationRunRecord | null, keys: string[]): unknown {
  const found = (run?.outputMetrics ?? []).find((item) => keys.includes(String(item.key ?? item.metricKey ?? "")));
  return found?.value;
}

export default function ProjectCashflowPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [cashflow, setCashflow] = useState<CashflowResult | null>(null);
  const [scenarioRuns, setScenarioRuns] = useState<Record<string, CalculationRunRecord | null>>({});
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [buildingFeas, setBuildingFeas] = useState(false);
  const [feasInfo, setFeasInfo] = useState<{ method?: string; needsReview?: boolean; missing?: string[]; hasRevenueAndCost?: boolean; applied?: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [cashflowResult, runs] = await Promise.all([
        fetchLatestCashflow(projectId),
        fetchLatestCalculationRuns(projectId, SCENARIO_ENGINES),
      ]);
      setCashflow(cashflowResult);
      setScenarioRuns(runs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "계산 결과를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { void load(); }, [load]);

  const runPod = useCallback(async () => {
    setRunning(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/chief-orchestration/run-pod`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders("web") },
        body: JSON.stringify({ podKey: "feasibility_cashflow", options: { forceTrigger: "max_minus_exceeds_pf_limit" } }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(String(body.detail ?? "Feasibility-Cashflow Pod 실행에 실패했습니다."));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Feasibility-Cashflow Pod 실행에 실패했습니다.");
    } finally {
      setRunning(false);
    }
  }, [load, projectId]);

  const buildFeasibilityInput = useCallback(async () => {
    setBuildingFeas(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/feasibility/build-input`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders("web") },
        body: JSON.stringify({ apply: true }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(String(body.detail ?? "사업수지 구성에 실패했습니다."));
      setFeasInfo(body);
      if (body.hasRevenueAndCost) {
        await runPod(); // feasibility_input이 채워졌으니 Cash Flow를 계산
      } else {
        await load();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "사업수지 구성에 실패했습니다.");
    } finally {
      setBuildingFeas(false);
    }
  }, [projectId, runPod, load]);

  const rows = useMemo(() => cashflow?.monthly.slice(0, 60) ?? [], [cashflow]);
  const summary = cashflow?.summary ?? {};
  const runId = cashflow?.runId ?? "-";

  return (
    <div className="space-y-6 pb-8">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-3">
          <Link href={`/project/${projectId}`} className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50" aria-label="프로젝트로 돌아가기"><ArrowLeft className="h-4 w-4" /></Link>
          <div>
            <p className="text-xs font-black uppercase tracking-wider text-indigo-600">Feasibility-Cashflow Pod</p>
            <h1 className="mt-1 flex items-center gap-2 text-2xl font-black text-slate-950"><Wallet className="h-6 w-6 text-indigo-600" />월별 현금흐름 및 Max Minus</h1>
            <p className="mt-1 max-w-3xl text-sm font-semibold leading-6 text-slate-500">최신 CalculationRun의 월별 현금흐름, PF 인출, 상환·회수 시나리오를 같은 원장에서 확인합니다.</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => void load()} disabled={loading || running || buildingFeas} className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-bold text-slate-700 disabled:opacity-40"><RefreshCw className="h-4 w-4" />새로고침</button>
          <button type="button" onClick={() => void buildFeasibilityInput()} disabled={loading || running || buildingFeas} className="inline-flex h-10 items-center gap-2 rounded-lg border border-indigo-300 bg-indigo-50 px-3 text-sm font-black text-indigo-700 disabled:opacity-40"><Calculator className="h-4 w-4" />{buildingFeas ? "사업수지 구성 중…" : "AI 사업수지 구성"}</button>
          <button type="button" onClick={() => void runPod()} disabled={loading || running || buildingFeas} className="inline-flex h-10 items-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-black text-white disabled:opacity-40">{running ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}{running ? "계산 중" : "Cash Flow 재실행"}</button>
          <a href={`/api/export-cf?projectId=${encodeURIComponent(projectId)}`} download className="inline-flex h-10 items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 text-sm font-black text-indigo-700"><Download className="h-4 w-4" />수식 Excel 다운로드</a>
        </div>
      </header>

      {error ? <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-700">{error}</div> : null}
      {feasInfo ? (
        <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-3 text-xs font-semibold text-indigo-800">
          {feasInfo.hasRevenueAndCost
            ? `사업수지 구성 완료 — 출처: ${FEAS_METHOD_LABEL[feasInfo.method ?? ""] ?? feasInfo.method}${feasInfo.needsReview ? " · 일부 추정값(검토 필요)" : ""}${feasInfo.missing?.length ? ` · 미확보: ${feasInfo.missing.join(", ")}` : ""}`
            : `총수입·총사업비 근거를 찾지 못했습니다. 사업수지 Excel(또는 사업계획서)을 업로드하거나 상품구성·분양가를 입력하세요. (출처: ${FEAS_METHOD_LABEL[feasInfo.method ?? ""] ?? feasInfo.method})`}
        </div>
      ) : null}
      {loading ? <EmptyState title="CalculationRun을 조회하는 중입니다." body="프로젝트 원장에서 최신 계산 결과와 근거를 읽고 있습니다." loading /> : !cashflow ? <EmptyState title="저장된 Cash Flow 계산 결과가 없습니다." body="파싱된 사업수지가 있으면 ‘AI 사업수지 구성’으로 total_revenue·total_cost를 채운 뒤 Cash Flow를 계산합니다. 없으면 사업수지 Excel/사업계획서를 먼저 업로드하세요." action={<div className="flex gap-2"><button type="button" onClick={() => void buildFeasibilityInput()} disabled={buildingFeas} className="inline-flex h-10 items-center gap-2 rounded-lg border border-indigo-300 bg-indigo-50 px-4 text-sm font-black text-indigo-700 disabled:opacity-40"><Calculator className="h-4 w-4" />AI 사업수지 구성</button><button type="button" onClick={() => void runPod()} className="inline-flex h-10 items-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-black text-white"><PlayCircle className="h-4 w-4" />Pod 실행</button></div>} /> : (
        <>
          <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <KpiCard label="Max Minus" value={formatEok(Math.abs(cashflow.maxMinusEok))} sub={`M${cashflow.maxMinusMonth || "-"} 발생`} icon={<TrendingDown className="h-5 w-5 text-rose-600" />} tone="text-rose-700" />
            <KpiCard label="총수입" value={formatEok(Number(summary.totalRevenue ?? 0) / 1e8)} sub="분양·매각·기타 수입" icon={<Calculator className="h-5 w-5 text-emerald-600" />} />
            <KpiCard label="총사업비" value={formatEok(Number(summary.totalProjectCost ?? 0) / 1e8)} sub="토지비·공사비·금융비" icon={<Calculator className="h-5 w-5 text-sky-600" />} />
            <KpiCard label="PF 한도" value={formatEok(Number(summary.pfLimit ?? 0) / 1e8)} sub={`총 인출 ${formatEok(Number(summary.totalPfDraw ?? 0) / 1e8)}`} icon={<Wallet className="h-5 w-5 text-indigo-600" />} />
            <KpiCard label="계산 상태" value={String(summary.status ?? "완료")} sub={`CalculationRun ${runId.slice(0, 18)}`} icon={<ShieldCheck className="h-5 w-5 text-slate-600" />} />
          </section>

          <ScenarioRegistryPanel runs={scenarioRuns} />
          <section className="grid gap-4 xl:grid-cols-[1.6fr_1fr]">
            <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
              <div className="flex items-center justify-between border-b border-slate-200 p-5"><div><h2 className="text-lg font-black text-slate-950">월별 Cash Flow</h2><p className="mt-1 text-xs font-semibold text-slate-500">저장된 CalculationRun의 실데이터입니다. 단위: 억원</p></div><span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-black text-emerald-700">원장 기준</span></div>
              <div className="max-h-[620px] overflow-auto"><table className="w-full min-w-[720px] border-collapse text-right text-sm"><thead className="sticky top-0 z-10 border-b border-slate-200 bg-slate-50 text-xs text-slate-500"><tr><th className="p-3 text-left">기간</th><th className="p-3">Cash-In</th><th className="p-3">Cash-Out</th><th className="p-3">Net CF</th><th className="p-3">누적 CF</th></tr></thead><tbody className="divide-y divide-slate-100">{rows.map((row) => <tr key={row.period} className={row.period === `M${cashflow.maxMinusMonth}` ? "bg-rose-50" : ""}><td className="p-3 text-left font-black">{row.period}</td><td className="p-3">{row.cashInEok.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}</td><td className="p-3">{row.cashOutEok.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}</td><td className={`p-3 font-bold ${row.netCashFlowEok < 0 ? "text-rose-600" : ""}`}>{row.netCashFlowEok.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}</td><td className={`p-3 font-black ${row.cumulativeEok < 0 ? "text-rose-600" : ""}`}>{row.cumulativeEok.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}</td></tr>)}</tbody></table></div>
            </div>
            <aside className="space-y-4"><InfoPanel title="계산 근거"><Fact label="CalculationRun" value={runId} /><Fact label="Engine" value="cash_flow_engine" /><Fact label="완료 시각" value={cashflow.completedAt || "-"} /><Fact label="Source locator" value={JSON.stringify(cashflow.sourceMetadata ?? {})} /></InfoPanel><InfoPanel title="Cash Flow 위험"><RiskList flags={cashflow.riskFlags ?? []} /></InfoPanel></aside>
          </section>
        </>
      )}
    </div>
  );
}

function ScenarioRegistryPanel({ runs }: { runs: Record<string, CalculationRunRecord | null> }) {
  const items = [
    ["waterfall", "상환 Waterfall", ["waterfall.summary.shortfallFlagCount"]],
    ["tranche_recovery", "Tranche별 회수율", ["tranche_recovery.senior"]],
    ["unsold_ltv", "미분양 LTV", ["unsold_ltv.summary.maxUnsoldLtv"]],
    ["discount_stress", "할인분양 Stress", ["discount_stress.discount10.exitSaleRate"]],
  ] as const;
  return <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">{items.map(([key, title, keys]) => { const run = runs[key]; return <div key={key} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"><div className="flex items-center justify-between"><p className="text-sm font-black text-slate-950">{title}</p><span className={`rounded-full px-2 py-1 text-[11px] font-black ${run ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{run ? "저장됨" : "미실행"}</span></div><p className="mt-3 text-xl font-black text-slate-900">{run ? formatValue(metric(run, [...keys])) : "-"}</p><p className="mt-1 break-all text-[11px] font-semibold text-slate-500">{run ? `${run.calculationRunId} · ${run.engineVersion ?? "-"}` : "해당 엔진을 실행하면 표시됩니다."}</p><p className="mt-2 break-all text-[10px] text-slate-400">{run?.sourceLocator ? JSON.stringify(run.sourceLocator) : "source locator 없음"}</p></div>; })}</section>;
}

function RiskList({ flags }: { flags: Array<Record<string, unknown>> }) { return flags.length ? <div className="space-y-2">{flags.slice(0, 8).map((flag, index) => <div key={String(flag.code ?? index)} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs"><p className="font-black text-amber-800">{String(flag.code ?? "RISK_FLAG")}</p><p className="mt-1 font-semibold text-amber-700">{String(flag.message ?? flag.severity ?? "검토 필요")}</p></div>)}</div> : <p className="text-xs font-semibold text-slate-500">저장된 Cash Flow 위험이 없습니다.</p>; }
function EmptyState({ title, body, action, loading }: { title: string; body: string; action?: ReactNode; loading?: boolean }) { return <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center"><div className="mx-auto flex h-9 w-9 items-center justify-center text-amber-500">{loading ? <Loader2 className="h-8 w-8 animate-spin" /> : <AlertTriangle className="h-8 w-8" />}</div><p className="mt-4 text-lg font-black text-slate-950">{title}</p><p className="mx-auto mt-2 max-w-2xl text-sm font-semibold leading-6 text-slate-500">{body}</p>{action ? <div className="mt-5 flex justify-center">{action}</div> : null}</div>; }
function KpiCard({ label, value, sub, icon, tone }: { label: string; value: string; sub: string; icon: ReactNode; tone?: string }) { return <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"><div className="flex items-center justify-between"><p className="text-xs font-black tracking-wider text-slate-500">{label}</p>{icon}</div><p className={`mt-4 text-xl font-black ${tone ?? "text-slate-950"}`}>{value}</p><p className="mt-1 text-xs font-semibold text-slate-500">{sub}</p></div>; }
function InfoPanel({ title, children }: { title: string; children: ReactNode }) { return <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"><h3 className="text-sm font-black text-slate-950">{title}</h3><div className="mt-3">{children}</div></div>; }
function Fact({ label, value }: { label: string; value: string }) { return <div className="flex items-start justify-between gap-3 border-b border-slate-100 py-2 text-xs last:border-0"><span className="font-bold text-slate-500">{label}</span><span className="max-w-[240px] break-all text-right font-black text-slate-900">{value}</span></div>; }
