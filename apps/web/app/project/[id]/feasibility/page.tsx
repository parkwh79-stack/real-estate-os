"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowLeft, BarChart3, FileSpreadsheet, Loader2, RefreshCw, Wallet } from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import { fetchLatestCashflow, type CashflowResult } from "../../../../lib/project-client";
import { displayText, fetchWorkspaceContext, type WorkspacePageContext } from "../../../../lib/workspace-context";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function formatEok(value: unknown) {
  const n = Number(value ?? 0);
  return n ? `${(n / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억원` : "미확인";
}

function formatPct(value: unknown) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "미확인";
  return `${(n * 100).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%`;
}

export default function FeasibilityPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [cashflow, setCashflow] = useState<CashflowResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ctx, cf] = await Promise.all([
        fetchWorkspaceContext(projectId, "feasibility"),
        fetchLatestCashflow(projectId),
      ]);
      setContext(ctx);
      setCashflow(cf);
    } catch (err) {
      setError(err instanceof Error ? err.message : "사업성 원장을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runFeasibilityPod() {
    setRunning(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/chief-orchestration/run-pod`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ podKey: "feasibility_cashflow" }),
      });
      if (!res.ok) throw new Error(`Feasibility-Cashflow Pod 실행 실패 (${res.status})`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Feasibility-Cashflow Pod 실행에 실패했습니다.");
    } finally {
      setRunning(false);
    }
  }

  const pageData = asRecord(context?.pageData);
  const feasibility = asRecord(pageData.feasibility);
  const developmentFeasibility = asRecord(pageData.developmentFeasibility);
  const returnMetrics = asRecord(developmentFeasibility.returnMetrics ?? developmentFeasibility.metrics);
  const cfSummary = cashflow?.summary ?? {};
  const projectName = displayText(context?.projectMemory?.projectName, `프로젝트 ${projectId}`);

  const totalRevenue = Number(cfSummary.totalRevenue ?? feasibility.total_revenue ?? feasibility.totalRevenue ?? 0);
  const totalCost = Number(cfSummary.totalProjectCost ?? feasibility.total_cost ?? feasibility.totalCost ?? 0);
  const profit = totalRevenue && totalCost ? totalRevenue - totalCost : Number(returnMetrics.developmentProfit ?? 0);
  const margin = totalRevenue ? profit / totalRevenue : Number(returnMetrics.profitMargin ?? Number.NaN);

  const rows = useMemo(() => cashflow?.monthly.slice(0, 12) ?? [], [cashflow]);

  return (
    <div className="space-y-6 pb-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <Link
            href={`/project/${projectId}`}
            className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
            aria-label="프로젝트로 돌아가기"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <p className="text-sm font-semibold text-slate-500">{projectName}</p>
            <h1 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
              <BarChart3 className="h-7 w-7 text-indigo-600" />
              사업성 분석
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              저장된 사업수지, Feasibility-Cashflow Pod artifact, 월별 CF 계산 결과를 기준으로 표시합니다. 고정 숫자 mock은 사용하지 않습니다.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading || running}
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-bold text-slate-700 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          >
            <RefreshCw className="h-4 w-4" />
            새로고침
          </button>
          <button
            type="button"
            onClick={() => void runFeasibilityPod()}
            disabled={loading || running}
            className="inline-flex h-10 items-center gap-2 rounded-lg bg-indigo-600 px-4 text-sm font-bold text-white disabled:opacity-50"
          >
            {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wallet className="h-4 w-4" />}
            Feasibility-Cashflow Pod 실행
          </button>
        </div>
      </header>

      {loading ? (
        <div className="flex min-h-[240px] items-center justify-center rounded-lg border border-slate-200 bg-white text-sm font-bold text-slate-500 dark:border-slate-800 dark:bg-slate-900">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          사업성 원장을 불러오는 중입니다.
        </div>
      ) : (
        <>
          {error ? (
            <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">
              {error}
            </div>
          ) : null}

          {!cashflow && !totalRevenue ? (
            <section className="rounded-lg border border-amber-200 bg-amber-50 p-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              <p className="text-lg font-black">저장된 사업성/CF 계산 artifact가 없습니다.</p>
              <p className="mt-2 text-sm font-semibold">
                사업수지 Excel 또는 사업계획서를 업로드한 뒤 Feasibility-Cashflow Pod를 실행하면 이 페이지가 저장된 계산 결과를 표시합니다.
              </p>
            </section>
          ) : null}

          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Kpi label="총 분양/매각수입" value={formatEok(totalRevenue)} />
            <Kpi label="총 사업비" value={formatEok(totalCost)} />
            <Kpi label="개발이익" value={formatEok(profit)} tone={profit > 0 ? "good" : profit < 0 ? "bad" : undefined} />
            <Kpi label="이익률" value={formatPct(margin)} tone={margin > 0 ? "good" : margin < 0 ? "bad" : undefined} />
          </section>

          <section className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
            <Panel title="계산 출처">
              <InfoRow label="프로젝트 기억" value={projectName} />
              <InfoRow label="CF artifact" value={cashflow?.artifactKey || "미저장"} />
              <InfoRow label="실행 Run" value={cashflow?.runId || "미실행"} />
              <InfoRow label="계산 소스" value={cashflow?.source === "chief_pod_artifact" ? "Chief Pod artifact" : cashflow ? "PF Review orchestration" : "미확인"} />
            </Panel>

            <Panel title="Cash Flow 핵심">
              <InfoRow label="Max Minus" value={cashflow ? `${cashflow.maxMinusEok.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억원` : "미확인"} />
              <InfoRow label="Max Minus 발생월" value={cashflow ? `M${cashflow.maxMinusMonth}` : "미확인"} />
              <InfoRow label="월별 CF Row" value={cashflow ? `${cashflow.monthly.length.toLocaleString("ko-KR")}개월` : "미확인"} />
              <InfoRow label="PF 한도/Draw" value={formatEok(cfSummary.pfLimit ?? cfSummary.totalPfDraw)} />
            </Panel>
          </section>

          <Panel title="월별 Cash Flow Preview">
            {rows.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] text-left text-sm">
                  <thead className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-800">
                    <tr>
                      <th className="px-3 py-2">기간</th>
                      <th className="px-3 py-2">Cash-In</th>
                      <th className="px-3 py-2">Cash-Out</th>
                      <th className="px-3 py-2">Net CF</th>
                      <th className="px-3 py-2">누적 CF</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {rows.map((row) => (
                      <tr key={row.period}>
                        <td className="px-3 py-3 font-bold text-slate-950 dark:text-white">{row.period}</td>
                        <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{row.cashInEok.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억원</td>
                        <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{row.cashOutEok.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억원</td>
                        <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{row.netCashFlowEok.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억원</td>
                        <td className="px-3 py-3 font-bold text-slate-950 dark:text-white">{row.cumulativeEok.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억원</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="rounded-lg bg-slate-50 p-5 text-sm text-slate-500 dark:bg-slate-800/70">
                월별 Cash Flow가 아직 저장되지 않았습니다. Feasibility-Cashflow Pod를 실행하세요.
              </div>
            )}
          </Panel>

          <Panel title="Excel 연동 기준">
            <div className="grid gap-3 md:grid-cols-4">
              <Step icon={<FileSpreadsheet className="h-4 w-4" />} title="입력" body="사업명, 위치, 상품구성, 기본 가정" />
              <Step icon={<FileSpreadsheet className="h-4 w-4" />} title="사업수지" body="총수입, 토지비, 공사비, 금융비, 이익률" />
              <Step icon={<FileSpreadsheet className="h-4 w-4" />} title="공정율" body="월별 공정 및 공사비 집행 스케줄" />
              <Step icon={<FileSpreadsheet className="h-4 w-4" />} title="2.현금흐름" body="Cash-In/Out, 누적 CF, Max Minus" />
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: "good" | "bad" }) {
  const color = tone === "good" ? "text-emerald-600 dark:text-emerald-300" : tone === "bad" ? "text-rose-600 dark:text-rose-300" : "text-slate-950 dark:text-white";
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-sm font-semibold text-slate-500">{label}</p>
      <p className={`mt-3 text-2xl font-black tracking-tight ${color}`}>{value}</p>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <h2 className="mb-4 text-lg font-black text-slate-950 dark:text-white">{title}</h2>
      {children}
    </section>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-slate-100 py-3 text-sm last:border-0 dark:border-slate-800">
      <span className="font-semibold text-slate-500">{label}</span>
      <span className="text-right font-black text-slate-950 dark:text-white">{value}</span>
    </div>
  );
}

function Step({ icon, title, body }: { icon: ReactNode; title: string; body: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-4 dark:bg-slate-800/70">
      <div className="flex items-center gap-2 text-sm font-black text-slate-950 dark:text-white">
        {icon}
        {title}
      </div>
      <p className="mt-2 text-xs leading-5 text-slate-500">{body}</p>
    </div>
  );
}
