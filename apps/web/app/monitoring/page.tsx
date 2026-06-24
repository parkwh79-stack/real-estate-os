"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, Loader2, Minus, RefreshCw } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";

type TrendEntry = { direction: string; from: number; to: number; delta: number };
type Check = { key: string; label: string; kind: string; value: number; threshold: number; status: string };
type Alert = {
  projectId?: string;
  projectName?: string;
  code?: string;
  severity?: string;
  covenant?: string;
  message?: string;
  recommendedAction?: string;
};
type ProjectRow = {
  projectId: string;
  name: string;
  overallStatus: string;
  breachCount: number;
  watchCount: number;
  alertCount: number;
  criticalAlertCount: number;
  metrics: Record<string, number>;
  checks: Check[];
  topAlert?: Alert | null;
  trend?: Record<string, TrendEntry>;
};
type Portfolio = {
  summary?: {
    projectCount?: number;
    monitoredCount?: number;
    statusCounts?: Record<string, number>;
    totalCriticalAlerts?: number;
    totalCautionAlerts?: number;
    worstStatus?: string;
  };
  projects?: ProjectRow[];
  alertFeed?: Alert[];
};

const POLL_MS = 30000;

const STATUS_LABEL: Record<string, string> = { ok: "정상", watch: "주의", breach: "위반", no_data: "자료 없음" };
const STATUS_TONE: Record<string, string> = {
  ok: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  watch: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  breach: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200",
  no_data: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
};
const SUMMARY_CARD: Record<string, string> = {
  breach: "border-rose-200 bg-rose-50 dark:border-rose-900 dark:bg-rose-950/20",
  watch: "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/20",
  ok: "border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/20",
  no_data: "border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900",
};
const METRIC_LABEL: Record<string, string> = {
  ltv: "LTV",
  ltc: "LTC",
  exit_sale_rate: "Exit 분양률",
  dscr: "DSCR",
  sales_rate: "분양률",
};

function fmtMetric(key: string, value: number): string {
  if (key === "dscr") return `${value.toFixed(2)}x`;
  return `${(value * 100).toFixed(1)}%`;
}

function TrendArrow({ entry }: { entry?: TrendEntry }) {
  if (!entry) return <span className="text-slate-300">—</span>;
  if (entry.direction === "개선") return <ArrowUpRight className="h-3.5 w-3.5 text-emerald-600" />;
  if (entry.direction === "악화") return <ArrowDownRight className="h-3.5 w-3.5 text-rose-600" />;
  return <Minus className="h-3.5 w-3.5 text-slate-400" />;
}

export default function PortfolioMonitoringPage() {
  const [data, setData] = useState<Portfolio | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/monitoring/portfolio`, { cache: "no-store", headers: authHeaders("web") });
      if (!res.ok) throw new Error(`포트폴리오 모니터링 조회 실패 (${res.status})`);
      setData((await res.json()) as Portfolio);
    } catch (err) {
      setError(err instanceof Error ? err.message : "모니터링 데이터를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const interval = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(interval);
  }, [load]);

  const summary = data?.summary ?? {};
  const counts = summary.statusCounts ?? {};
  const projects = data?.projects ?? [];
  const alertFeed = data?.alertFeed ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-900 text-white dark:bg-white dark:text-slate-950">
            <Activity className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-xl font-black text-slate-950 dark:text-white">실시간 PF 모니터링</h1>
            <p className="text-xs font-semibold text-slate-500">
              전체 프로젝트 covenant 상태 · EOD 경보 · 직전 심사 대비 추세 (30초 자동 갱신)
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 px-3 text-sm font-bold text-slate-700 disabled:opacity-40 dark:border-slate-700 dark:text-slate-200"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          새로고침
        </button>
      </header>

      {error ? <p className="text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {(["breach", "watch", "ok", "no_data"] as const).map((status) => (
          <div key={status} className={`rounded-lg border p-4 ${SUMMARY_CARD[status]}`}>
            <p className="text-xs font-bold text-slate-500">{STATUS_LABEL[status]} 프로젝트</p>
            <p className="mt-1 text-2xl font-black text-slate-950 dark:text-white">{counts[status] ?? 0}</p>
          </div>
        ))}
      </section>

      <section className="flex flex-wrap gap-4 rounded-lg border border-slate-200 bg-white p-4 text-sm dark:border-slate-800 dark:bg-slate-900">
        <span className="font-semibold text-slate-600 dark:text-slate-300">
          전체 <strong className="text-slate-950 dark:text-white">{summary.projectCount ?? 0}</strong>개 / 모니터링{" "}
          <strong className="text-slate-950 dark:text-white">{summary.monitoredCount ?? 0}</strong>개
        </span>
        <span className="font-semibold text-rose-600 dark:text-rose-300">
          심각 경보 {summary.totalCriticalAlerts ?? 0}건
        </span>
        <span className="font-semibold text-amber-600 dark:text-amber-300">
          주의 경보 {summary.totalCautionAlerts ?? 0}건
        </span>
      </section>

      {alertFeed.length ? (
        <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <h2 className="flex items-center gap-2 font-black text-slate-950 dark:text-white">
            <AlertTriangle className="h-4 w-4 text-rose-500" /> 경보 피드 (심각 우선)
          </h2>
          <div className="mt-3 space-y-2">
            {alertFeed.slice(0, 20).map((a, index) => (
              <div
                key={`${a.projectId}-${a.code}-${index}`}
                className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${a.severity === "critical" ? SUMMARY_CARD.breach : SUMMARY_CARD.watch}`}
              >
                <AlertTriangle className={`mt-0.5 h-4 w-4 shrink-0 ${a.severity === "critical" ? "text-rose-600" : "text-amber-600"}`} />
                <div className="min-w-0">
                  <p className="font-bold text-slate-900 dark:text-white">
                    {a.projectId ? (
                      <Link href={`/project/${a.projectId}`} className="underline-offset-2 hover:underline">
                        {a.projectName ?? a.projectId}
                      </Link>
                    ) : null}{" "}
                    — {a.message}
                  </p>
                  {a.recommendedAction ? (
                    <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-300">조치: {a.recommendedAction}</p>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="border-b border-slate-200 px-5 py-3 dark:border-slate-800">
          <h2 className="font-black text-slate-950 dark:text-white">프로젝트별 상태</h2>
        </div>
        {projects.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="bg-slate-50 text-left text-xs font-bold text-slate-500 dark:bg-slate-950/40">
                <tr>
                  <th className="px-4 py-2">프로젝트</th>
                  <th className="px-4 py-2">상태</th>
                  <th className="px-4 py-2">경보</th>
                  <th className="px-4 py-2">LTV</th>
                  <th className="px-4 py-2">Exit 분양률</th>
                  <th className="px-4 py-2">추세</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {projects.map((p) => (
                  <tr key={p.projectId} className="hover:bg-slate-50 dark:hover:bg-slate-950/40">
                    <td className="px-4 py-3 font-semibold text-slate-900 dark:text-white">
                      <Link href={`/project/${p.projectId}`} className="underline-offset-2 hover:underline">
                        {p.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-black ${STATUS_TONE[p.overallStatus] ?? STATUS_TONE.no_data}`}>
                        {STATUS_LABEL[p.overallStatus] ?? p.overallStatus}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-700 dark:text-slate-200">
                      {p.criticalAlertCount ? <span className="font-bold text-rose-600">심각 {p.criticalAlertCount}</span> : null}
                      {p.criticalAlertCount && p.alertCount > p.criticalAlertCount ? " · " : null}
                      {p.alertCount > p.criticalAlertCount ? (
                        <span className="text-amber-600">주의 {p.alertCount - p.criticalAlertCount}</span>
                      ) : null}
                      {!p.alertCount ? <span className="text-slate-400">없음</span> : null}
                    </td>
                    <td className="px-4 py-3 text-slate-700 dark:text-slate-200">
                      {typeof p.metrics.ltv === "number" ? (
                        <span className="inline-flex items-center gap-1">
                          {fmtMetric("ltv", p.metrics.ltv)} <TrendArrow entry={p.trend?.ltv} />
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-700 dark:text-slate-200">
                      {typeof p.metrics.exit_sale_rate === "number" ? (
                        <span className="inline-flex items-center gap-1">
                          {fmtMetric("exit_sale_rate", p.metrics.exit_sale_rate)} <TrendArrow entry={p.trend?.exit_sale_rate} />
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">
                      {p.trend && Object.keys(p.trend).length
                        ? Object.entries(p.trend)
                            .map(([k, v]) => `${METRIC_LABEL[k] ?? k} ${v.direction}`)
                            .join(" · ")
                        : "직전 심사 없음"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="px-5 py-8 text-center text-sm text-slate-500">
            모니터링할 프로젝트가 없습니다. 프로젝트를 생성하고 PF 구조·지표를 입력하면 표시됩니다.
          </p>
        )}
      </section>
    </div>
  );
}
