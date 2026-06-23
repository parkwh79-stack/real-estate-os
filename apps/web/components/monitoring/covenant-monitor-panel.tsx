"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";

interface Check {
  key: string;
  label: string;
  kind: string;
  value: number;
  threshold: number;
  status: "ok" | "watch" | "breach";
}
interface Alert {
  code: string;
  severity: string;
  covenant?: string;
  message: string;
  recommendedAction?: string;
}
interface Monitoring {
  overallStatus?: string;
  checks?: Check[];
  alerts?: Alert[];
  summary?: { breachCount?: number; watchCount?: number; alertCount?: number };
}

const POLL_MS = 30000;

const STATUS_CARD: Record<string, string> = {
  ok: "border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/20",
  watch: "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/20",
  breach: "border-rose-200 bg-rose-50 dark:border-rose-900 dark:bg-rose-950/20",
};
const OVERALL_LABEL: Record<string, string> = {
  ok: "정상", watch: "주의", breach: "위반", no_data: "데이터 없음",
};
const OVERALL_TONE: Record<string, string> = {
  ok: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  watch: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  breach: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200",
  no_data: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
};

function fmt(value: number, threshold: number): string {
  // ratios < 1 render as %, coverage ratios (DSCR) as x
  return threshold < 1 ? `${(value * 100).toFixed(1)}%` : `${value.toFixed(2)}x`;
}

/** Live PF covenant monitoring (DSCR/LTV/LTC/Exit/sales-rate vs thresholds + stress EOD). */
export function CovenantMonitorPanel({ projectId }: { projectId: string }) {
  const [data, setData] = useState<Monitoring | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/monitoring`, {
        cache: "no-store",
        headers: authHeaders("web"),
      });
      if (!res.ok) throw new Error(`모니터링 조회 실패 (${res.status})`);
      setData((await res.json()) as Monitoring);
    } catch (err) {
      setError(err instanceof Error ? err.message : "모니터링 데이터를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    const interval = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(interval);
  }, [load]);

  const overall = data?.overallStatus ?? "no_data";
  const checks = data?.checks ?? [];
  const alerts = data?.alerts ?? [];

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-900 text-white dark:bg-white dark:text-slate-950">
            <Activity className="h-5 w-5" />
          </div>
          <div>
            <p className="font-black text-slate-950 dark:text-white">Covenant 모니터링</p>
            <p className="text-xs font-semibold text-slate-500">DSCR · LTV · LTC · Exit 분양률 · 분양률 임계 추적 (30초 자동 갱신)</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-black ${OVERALL_TONE[overall] ?? OVERALL_TONE.no_data}`}>
            {OVERALL_LABEL[overall] ?? overall}
          </span>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex h-8 items-center gap-1 rounded-lg border border-slate-200 px-2 text-xs font-bold text-slate-700 disabled:opacity-40 dark:border-slate-700 dark:text-slate-200"
          >
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          </button>
        </div>
      </div>

      {error ? <p className="mt-3 text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      {checks.length ? (
        <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-5">
          {checks.map((c) => (
            <div key={c.key} className={`rounded-lg border p-3 ${STATUS_CARD[c.status] ?? STATUS_CARD.ok}`}>
              <p className="text-[11px] font-bold text-slate-500">{c.label}</p>
              <p className="mt-1 text-base font-black text-slate-950 dark:text-white">{fmt(c.value, c.threshold)}</p>
              <p className="text-[11px] text-slate-500">{c.kind === "floor" ? "≥" : "≤"} {c.threshold < 1 ? `${(c.threshold * 100).toFixed(0)}%` : `${c.threshold.toFixed(2)}x`}</p>
            </div>
          ))}
        </div>
      ) : null}

      {alerts.length ? (
        <div className="mt-4 space-y-2">
          {alerts.map((a, index) => (
            <div
              key={`${a.code}-${index}`}
              className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${a.severity === "critical" ? STATUS_CARD.breach : STATUS_CARD.watch}`}
            >
              <AlertTriangle className={`mt-0.5 h-4 w-4 shrink-0 ${a.severity === "critical" ? "text-rose-600" : "text-amber-600"}`} />
              <div className="min-w-0">
                <p className="font-bold text-slate-900 dark:text-white">{a.message}</p>
                {a.recommendedAction ? <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-300">조치: {a.recommendedAction}</p> : null}
              </div>
            </div>
          ))}
        </div>
      ) : checks.length ? (
        <p className="mt-3 text-sm font-semibold text-emerald-700 dark:text-emerald-300">현재 임계 위반·근접 covenant가 없습니다.</p>
      ) : (
        <p className="mt-3 text-sm text-slate-500">PF 구조·지표가 입력되면 covenant 모니터링이 표시됩니다.</p>
      )}
    </section>
  );
}
