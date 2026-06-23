"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, Gauge, Loader2, RefreshCw, ShieldCheck, TrendingUp } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { EmptyState, MetricCard, SkeletonMetrics } from "../ui/primitives";

interface MetricEntry {
  value?: number | null;
}
interface ReturnMetrics {
  metrics?: {
    projectIrr?: MetricEntry;
    equityIrr?: MetricEntry;
    equityMultiple?: MetricEntry;
    paybackPeriodMonths?: { value?: number | null };
    icr?: MetricEntry;
  };
  summary?: string;
}
interface StressScenario {
  key: string;
  name: string;
  projectIrr?: number | null;
  seniorStatus?: string;
  statusLabel?: string;
}
interface StressTest {
  scenarios?: StressScenario[];
  worstCaseKey?: string | null;
  seniorBreakScenarioKey?: string | null;
}
interface CreditEnhancement {
  enhancementGrade?: string;
  baseSeniorLtv?: number;
  adjustedSeniorLtv?: number;
  totalSeniorLtvDeltaPp?: number;
  activeCount?: number;
  missingCriticalMeasures?: string[];
}
interface DevelopabilityDimension {
  label?: string;
  score?: number | null;
  available?: boolean;
}
interface Developability {
  overallScore?: number | null;
  grade?: string;
  drivers?: string[];
  gaps?: string[];
  dimensions?: Record<string, DevelopabilityDimension>;
  summary?: string;
}
interface RunArtifacts {
  returnMetrics?: ReturnMetrics;
  stressTest?: StressTest;
  creditEnhancement?: CreditEnhancement;
  developabilityScore?: Developability;
}

function pct(value?: number | null): string {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
}
function num(value?: number | null, suffix = ""): string {
  return typeof value === "number" ? `${value.toFixed(2)}${suffix}` : "—";
}

const SENIOR_TONE: Record<string, string> = {
  normal: "text-emerald-600 dark:text-emerald-300",
  credit_enhancement_triggered: "text-amber-600 dark:text-amber-300",
  senior_principal_loss_possible: "text-rose-600 dark:text-rose-300",
};

/**
 * Surfaces the deck headline engines (Return metrics, 4-stage Stress Test,
 * Credit Enhancement) from the latest PF review run's artifacts.
 */
export function EngineMetricsPanel({ projectId }: { projectId: string }) {
  const [artifacts, setArtifacts] = useState<RunArtifacts | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const listRes = await fetch(`${apiBase}/projects/${projectId}/orchestrations`, {
        cache: "no-store",
        headers: authHeaders("web"),
      });
      if (!listRes.ok) throw new Error(`목록 조회 실패 (${listRes.status})`);
      const listBody = await listRes.json();
      const runs = Array.isArray(listBody) ? listBody : (listBody.runs ?? listBody.orchestrations ?? []);
      if (!runs.length) {
        setArtifacts(null);
        return;
      }
      const runId = runs[0].run_id ?? runs[0].runId;
      const detailRes = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${runId}`, {
        cache: "no-store",
        headers: authHeaders("web"),
      });
      if (!detailRes.ok) throw new Error(`실행 상세 조회 실패 (${detailRes.status})`);
      const detail = await detailRes.json();
      setArtifacts((detail.artifacts ?? {}) as RunArtifacts);
    } catch (err) {
      setError(err instanceof Error ? err.message : "지표를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const rm = artifacts?.returnMetrics?.metrics;
  const stress = artifacts?.stressTest;
  const ce = artifacts?.creditEnhancement;
  const dev = artifacts?.developabilityScore;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-200">
            <TrendingUp className="h-5 w-5" />
          </div>
          <div>
            <p className="font-black text-slate-950 dark:text-white">수익지표 · Stress · 신용보강</p>
            <p className="text-xs font-semibold text-slate-500">최신 PF 심사 실행 결과 기준</p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 px-3 text-xs font-bold text-slate-700 disabled:opacity-40 dark:border-slate-700 dark:text-slate-200"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          새로고침
        </button>
      </div>

      {error ? <p className="mt-3 text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      {loading && !artifacts ? <div className="mt-4"><SkeletonMetrics count={5} /></div> : null}

      {!artifacts && !loading && !error ? (
        <div className="mt-4">
          <EmptyState
            title="아직 실행된 PF 심사가 없습니다"
            body="통합심사를 실행하면 수익지표·Stress·신용보강 결과가 여기에 표시됩니다."
          />
        </div>
      ) : null}

      {artifacts ? (
        <div className="mt-4 space-y-4">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
            <MetricCard label="Project IRR" value={pct(stress?.scenarios?.find((s) => s.key === "base")?.projectIrr ?? rm?.projectIrr?.value)} />
            <MetricCard label="Equity IRR" value={pct(rm?.equityIrr?.value)} />
            <MetricCard label="Equity Multiple" value={num(rm?.equityMultiple?.value, "x")} />
            <MetricCard label="Payback(개월)" value={typeof rm?.paybackPeriodMonths?.value === "number" ? rm.paybackPeriodMonths.value.toFixed(1) : "—"} />
            <MetricCard label="ICR" value={num(rm?.icr?.value, "x")} />
          </div>

          {stress?.scenarios?.length ? (
            <div>
              <p className="mb-2 flex items-center gap-1 text-sm font-bold text-slate-950 dark:text-white">
                <Activity className="h-4 w-4" /> 4단계 Stress Test
              </p>
              <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-50 text-slate-500 dark:bg-slate-800">
                    <tr>
                      <th className="px-3 py-2">시나리오</th>
                      <th className="px-3 py-2">Project IRR</th>
                      <th className="px-3 py-2">Senior 상태</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stress.scenarios.map((s) => (
                      <tr key={s.key} className="border-t border-slate-100 dark:border-slate-800">
                        <td className="px-3 py-2 font-semibold text-slate-900 dark:text-white">{s.name}</td>
                        <td className="px-3 py-2">{pct(s.projectIrr)}</td>
                        <td className={`px-3 py-2 font-bold ${SENIOR_TONE[s.seniorStatus ?? ""] ?? "text-slate-500"}`}>
                          {s.statusLabel ?? s.seniorStatus ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          {ce ? (
            <div className="rounded-lg bg-slate-50 p-4 dark:bg-slate-950/40">
              <p className="flex items-center gap-1 text-sm font-bold text-slate-950 dark:text-white">
                <ShieldCheck className="h-4 w-4" /> 신용보강 (등급 {ce.enhancementGrade ?? "—"})
              </p>
              <p className="mt-1 text-sm font-semibold text-slate-600 dark:text-slate-300">
                Senior LTV {pct(ce.baseSeniorLtv)} → {pct(ce.adjustedSeniorLtv)}
                {typeof ce.totalSeniorLtvDeltaPp === "number" ? ` (−${ce.totalSeniorLtvDeltaPp.toFixed(0)}%p)` : ""} · 적용 {ce.activeCount ?? 0}개
              </p>
              {ce.missingCriticalMeasures && ce.missingCriticalMeasures.length > 0 ? (
                <p className="mt-1 text-xs font-bold text-rose-600 dark:text-rose-300">
                  핵심수단 미확보: {ce.missingCriticalMeasures.join(", ")}
                </p>
              ) : null}
            </div>
          ) : null}

          {dev ? (
            <div className="rounded-lg bg-slate-50 p-4 dark:bg-slate-950/40">
              <p className="flex items-center gap-2 text-sm font-bold text-slate-950 dark:text-white">
                <Gauge className="h-4 w-4" /> 개발가능성 점수
                <span className="rounded-md bg-indigo-100 px-2 py-0.5 text-xs font-black text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-200">
                  {typeof dev.overallScore === "number" ? dev.overallScore.toFixed(0) : "—"}점 · {dev.grade ?? "—"}등급
                </span>
              </p>
              {dev.dimensions && Object.keys(dev.dimensions).length > 0 ? (
                <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
                  {Object.entries(dev.dimensions).map(([key, d]) => (
                    <div key={key} className="rounded-md border border-slate-200 px-2 py-1 dark:border-slate-800">
                      <p className="text-[11px] font-semibold text-slate-500">{d.label ?? key}</p>
                      <p className="text-sm font-bold text-slate-900 dark:text-white">
                        {d.available && typeof d.score === "number" ? `${d.score.toFixed(0)}점` : "데이터 부족"}
                      </p>
                    </div>
                  ))}
                </div>
              ) : null}
              {dev.gaps && dev.gaps.length > 0 ? (
                <p className="mt-2 text-xs font-bold text-amber-600 dark:text-amber-300">
                  보완 필요: {dev.gaps.join(", ")}
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
