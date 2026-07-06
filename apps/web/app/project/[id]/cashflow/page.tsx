"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useParams } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  Calculator,
  Download,
  FileSpreadsheet,
  Loader2,
  PlayCircle,
  RefreshCw,
  ShieldCheck,
  TrendingDown,
  Wallet,
} from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import { fetchLatestCashflow, type CashflowResult } from "../../../../lib/project-client";

function formatEok(value?: number, digits = 1): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  return `${value.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}억원`;
}

function formatWonAsEok(value?: number, digits = 1): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  return formatEok(value / 100_000_000, digits);
}

function cell(value: number): string {
  if (Math.abs(value) < 0.05) return "-";
  return value.toLocaleString("ko-KR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

export default function ProjectCashflowPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [cf, setCf] = useState<CashflowResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setCf(await fetchLatestCashflow(projectId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cash Flow 계산 결과를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const runFeasibilityCashflowPod = useCallback(async () => {
    setRunning(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/chief-orchestration/run-pod`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders("web") },
        body: JSON.stringify({
          podKey: "feasibility_cashflow",
          options: { forceTrigger: "max_minus_exceeds_pf_limit" },
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(String(body.detail ?? "Feasibility-Cashflow Pod 실행에 실패했습니다."));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Feasibility-Cashflow Pod 실행에 실패했습니다.");
    } finally {
      setRunning(false);
    }
  }, [projectId, load]);

  const maxMinusAbs = Math.abs(cf?.maxMinusEok ?? 0);
  const s = cf?.summary ?? {};
  const riskFlags = cf?.riskFlags ?? [];
  const tableRows = useMemo(() => cf?.monthly.slice(0, 60) ?? [], [cf]);

  return (
    <div className="space-y-6 pb-8">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-3">
          <Link
            href={`/project/${projectId}`}
            className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
            aria-label="프로젝트로 돌아가기"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <p className="text-xs font-black uppercase tracking-wider text-indigo-600 dark:text-indigo-300">
              Feasibility-Cashflow Pod
            </p>
            <h1 className="mt-1 flex items-center gap-2 text-2xl font-black text-slate-950 dark:text-white">
              <Wallet className="h-6 w-6 text-indigo-600" />
              월별 Cash Flow / Max Minus
            </h1>
            <p className="mt-1 max-w-3xl text-sm font-semibold leading-6 text-slate-500">
              샘플 사업수지 Excel의 `입력`, `사업수지`, `공정율`, `2.현금흐름` 구조를 기준으로 저장된 Cash Flow Engine 결과를 표시합니다.
              고정 숫자는 표시하지 않으며, 결과가 없으면 Pod를 실행해 계산 artifact를 생성합니다.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading || running}
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            새로고침
          </button>
          <button
            type="button"
            onClick={() => void runFeasibilityCashflowPod()}
            disabled={loading || running}
            className="inline-flex h-10 items-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-black text-white hover:bg-slate-800 disabled:opacity-40 dark:bg-white dark:text-slate-950"
          >
            {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}
            {running ? "Pod 실행 중" : "Feasibility-Cashflow Pod 실행"}
          </button>
          <a
            href="/api/export-cf"
            download="REALYTICS_Formula_CashFlow_Model.xlsx"
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 text-sm font-black text-indigo-700 hover:bg-indigo-100 dark:border-indigo-900/60 dark:bg-indigo-950/30 dark:text-indigo-200"
          >
            <Download className="h-4 w-4" />
            수식 Excel 다운로드
          </a>
        </div>
      </header>

      {error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-200">
          {error}
        </div>
      ) : null}

      {loading ? (
        <div className="flex min-h-[260px] items-center justify-center rounded-xl border border-slate-200 bg-white text-sm font-bold text-slate-500 dark:border-slate-800 dark:bg-slate-900">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          저장된 Cash Flow artifact를 조회하는 중입니다.
        </div>
      ) : !cf ? (
        <EmptyState
          title="아직 저장된 Cash Flow 계산 결과가 없습니다."
          body="사업수지/상품구성/비용/일정 자료를 바탕으로 Feasibility-Cashflow Pod를 실행하면 월별 Cash Flow, Max Minus, PF 인출 필요액이 저장됩니다."
          action={
            <button
              type="button"
              onClick={() => void runFeasibilityCashflowPod()}
              disabled={running}
              className="inline-flex h-10 items-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-black text-white hover:bg-slate-800 disabled:opacity-40 dark:bg-white dark:text-slate-950"
            >
              {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}
              Feasibility-Cashflow Pod 실행
            </button>
          }
        />
      ) : (
        <>
          <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <KpiCard
              label="Max Minus"
              value={formatEok(maxMinusAbs)}
              sub={`M${cf.maxMinusMonth || "-"} 시점 누적 최저점`}
              icon={<TrendingDown className="h-5 w-5 text-rose-600" />}
              tone="text-rose-700 dark:text-rose-300"
            />
            <KpiCard
              label="총 수입"
              value={formatWonAsEok(s.totalRevenue)}
              sub="분양/매각/임대 수입 가정"
              icon={<Calculator className="h-5 w-5 text-emerald-600" />}
            />
            <KpiCard
              label="총 사업비"
              value={formatWonAsEok(s.totalProjectCost)}
              sub="토지비·공사비·제경비·금융비"
              icon={<FileSpreadsheet className="h-5 w-5 text-sky-600" />}
            />
            <KpiCard
              label="PF 한도"
              value={formatWonAsEok(s.pfLimit)}
              sub={`총 인출 ${formatWonAsEok(s.totalPfDraw, 1)}`}
              icon={<Wallet className="h-5 w-5 text-indigo-600" />}
            />
            <KpiCard
              label="Funding Status"
              value={String(s.status ?? "-")}
              sub={cf.source === "chief_pod_artifact" ? "Chief Pod artifact" : "PF review run"}
              icon={<ShieldCheck className="h-5 w-5 text-slate-600" />}
            />
          </section>

          <section className="grid gap-4 xl:grid-cols-[1.6fr_1fr]">
            <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 p-5 dark:border-slate-800">
                <div>
                  <h2 className="text-lg font-black text-slate-950 dark:text-white">월별 현금흐름표</h2>
                  <p className="mt-1 text-xs font-semibold text-slate-500">
                    단위: 억원. `2.현금흐름` 샘플 구조의 Cash-In, Cash-Out, Net CF, 누적 CF를 화면용으로 정규화했습니다.
                  </p>
                </div>
                <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-black text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200">
                  저장 artifact 기준
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] border-collapse text-right text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-950/60">
                      <th className="sticky left-0 z-10 bg-slate-50 p-3 text-left font-black dark:bg-slate-950">월차</th>
                      <th className="p-3 font-black">Cash-In</th>
                      <th className="p-3 font-black">Cash-Out</th>
                      <th className="p-3 font-black">Net CF</th>
                      <th className="p-3 font-black text-indigo-700 dark:text-indigo-300">누적 CF</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {tableRows.map((m) => (
                      <tr key={m.period} className={m.period === `M${cf.maxMinusMonth}` ? "bg-rose-50/70 dark:bg-rose-950/20" : ""}>
                        <td className="sticky left-0 z-10 bg-inherit p-3 text-left font-black text-slate-900 dark:text-white">{m.period}</td>
                        <td className="p-3 text-slate-700 dark:text-slate-300">{cell(m.cashInEok)}</td>
                        <td className="p-3 text-slate-700 dark:text-slate-300">{cell(m.cashOutEok)}</td>
                        <td className={`p-3 font-bold ${m.netCashFlowEok < 0 ? "text-rose-600 dark:text-rose-300" : "text-slate-700 dark:text-slate-300"}`}>
                          {cell(m.netCashFlowEok)}
                        </td>
                        <td className={`p-3 font-black ${m.cumulativeEok < 0 ? "text-rose-600 dark:text-rose-300" : "text-slate-950 dark:text-white"}`}>
                          {cell(m.cumulativeEok)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <aside className="space-y-4">
              <InfoPanel title="계산 근거">
                <Fact label="Run / Artifact" value={cf.artifactKey || cf.runId} />
                <Fact label="Artifact Type" value={cf.artifactType || cf.source} />
                <Fact label="Completed At" value={cf.completedAt || "-"} />
                <Fact label="Engine" value="realytics_cash_flow_engine_v1" />
              </InfoPanel>

              <InfoPanel title="샘플 Excel 반영 구조">
                <ul className="space-y-2 text-xs font-semibold leading-5 text-slate-600 dark:text-slate-300">
                  <li>`입력`: 사업명, 대지위치, 설계개요, 상품구성 입력</li>
                  <li>`사업수지`: 총수입, 토지비, 공사비, 제경비, 금융비 집계</li>
                  <li>`공정율`: 월차별 공정률과 공사비 배분 근거</li>
                  <li>`2.현금흐름`: 월별 Cash-In/Out, Net CF, 누적 CF, Max Minus 산정</li>
                </ul>
              </InfoPanel>

              <InfoPanel title="Risk Flags">
                {riskFlags.length ? (
                  <div className="space-y-2">
                    {riskFlags.slice(0, 6).map((flag, index) => (
                      <div key={String(flag.code ?? index)} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs dark:border-amber-900/60 dark:bg-amber-950/20">
                        <p className="font-black text-amber-800 dark:text-amber-200">{String(flag.code ?? "RISK_FLAG")}</p>
                        <p className="mt-1 font-semibold text-amber-700 dark:text-amber-300">{String(flag.message ?? flag.severity ?? "검토 필요")}</p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs font-semibold text-slate-500">저장된 Cash Flow 위험 플래그가 없습니다.</p>
                )}
              </InfoPanel>
            </aside>
          </section>
        </>
      )}
    </div>
  );
}

function EmptyState({ title, body, action }: { title: string; body: string; action?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center dark:border-slate-700 dark:bg-slate-900">
      <AlertTriangle className="mx-auto h-8 w-8 text-amber-500" />
      <p className="mt-4 text-lg font-black text-slate-950 dark:text-white">{title}</p>
      <p className="mx-auto mt-2 max-w-2xl text-sm font-semibold leading-6 text-slate-500">{body}</p>
      {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
    </div>
  );
}

function KpiCard({
  label,
  value,
  sub,
  icon,
  tone,
}: {
  label: string;
  value: string;
  sub: string;
  icon: ReactNode;
  tone?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-black uppercase tracking-wider text-slate-500">{label}</p>
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-50 dark:bg-slate-800">{icon}</div>
      </div>
      <p className={`mt-4 text-xl font-black ${tone ?? "text-slate-950 dark:text-white"}`}>{value}</p>
      <p className="mt-1 text-xs font-semibold text-slate-500">{sub}</p>
    </div>
  );
}

function InfoPanel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <h3 className="text-sm font-black text-slate-950 dark:text-white">{title}</h3>
      <div className="mt-3">{children}</div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-slate-100 py-2 text-xs last:border-b-0 dark:border-slate-800">
      <span className="font-bold text-slate-500">{label}</span>
      <span className="max-w-[220px] break-all text-right font-black text-slate-900 dark:text-slate-100">{value}</span>
    </div>
  );
}
