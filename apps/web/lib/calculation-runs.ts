"use client";

import { apiBase, authHeaders } from "./auth-client";

export type CalculationOutputMetric = {
  key: string;
  value?: unknown;
  unit?: string;
  label?: string;
  status?: string;
};

export type CalculationRiskIssue = {
  code?: string;
  severity?: string;
  message?: string;
  [key: string]: unknown;
};

export type CalculationRun = {
  calculationRunId: string;
  engineKey: string;
  engineVersion?: string;
  status?: string;
  outputMetrics?: CalculationOutputMetric[];
  riskIssues?: CalculationRiskIssue[];
  artifactRef?: Record<string, unknown>;
  inputEvidenceIds?: string[];
  assumptionIds?: string[];
  inputDigest?: Record<string, unknown>;
  inputSnapshot?: Record<string, unknown> | null;
  resultSnapshot?: Record<string, unknown> | null;
  createdAt?: string;
  updatedAt?: string;
};

export type CalculationRunList = {
  schemaVersion?: string;
  projectId?: string;
  engineKey?: string | null;
  count?: number;
  records?: CalculationRun[];
};

/** Korean labels for the engines the registry persists. */
export const ENGINE_LABELS: Record<string, string> = {
  feasibility: "사업수지",
  cashflow: "현금흐름",
  cash_flow_engine: "현금흐름 엔진",
  pf_metrics: "PF 지표",
  ltv_ltc_dscr: "LTV·LTC·DSCR",
  exit_sale_rate: "Exit 분양률",
  return_metrics: "수익성 지표",
  waterfall: "Waterfall",
  unsold_ltv: "미분양 LTV",
  tranche_recovery: "Tranche 회수",
  discount_stress: "스트레스(할인)",
  stress_test: "스트레스(할인)",
};

export function engineLabel(engineKey: string): string {
  return ENGINE_LABELS[engineKey] ?? engineKey;
}

export async function fetchCalculationRuns(
  projectId: string,
  options: { engineKey?: string; limit?: number; actor?: string; signal?: AbortSignal } = {},
): Promise<CalculationRun[]> {
  const params = new URLSearchParams();
  if (options.engineKey) params.set("engine_key", options.engineKey);
  params.set("limit", String(options.limit ?? 200));
  const res = await fetch(`${apiBase}/projects/${projectId}/calculation-runs?${params}`, {
    cache: "no-store",
    headers: authHeaders(options.actor ?? "web-auditor"),
    signal: options.signal,
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) throw new Error("계산 이력을 조회할 권한이 없습니다. 로그인 상태와 프로젝트 접근 권한을 확인해 주세요.");
    throw new Error("계산 이력을 불러오지 못했습니다. 잠시 후 새로고침해 주세요.");
  }
  const body = (await res.json()) as CalculationRunList;
  if (!Array.isArray(body.records)) throw new Error("계산 이력 응답을 확인하지 못했습니다. 다시 불러와 주세요.");
  const records = body.records;
  // Newest first — the registry does not guarantee order.
  return [...records].sort((a, b) => String(b.createdAt ?? "").localeCompare(String(a.createdAt ?? "")));
}

/** Group runs by engineKey, each group newest-first, groups ordered by most recent run. */
export function groupRunsByEngine(runs: CalculationRun[]): { engineKey: string; runs: CalculationRun[] }[] {
  const map = new Map<string, CalculationRun[]>();
  for (const run of runs) {
    const list = map.get(run.engineKey) ?? [];
    list.push(run);
    map.set(run.engineKey, list);
  }
  return [...map.entries()]
    .map(([engineKey, list]) => ({
      engineKey,
      runs: [...list].sort((a, b) => String(b.createdAt ?? "").localeCompare(String(a.createdAt ?? ""))),
    }))
    .sort((a, b) => String(b.runs[0]?.createdAt ?? "").localeCompare(String(a.runs[0]?.createdAt ?? "")));
}

export function metricMap(run: CalculationRun | null | undefined): Map<string, CalculationOutputMetric> {
  const map = new Map<string, CalculationOutputMetric>();
  for (const metric of run?.outputMetrics ?? []) {
    if (metric?.key) map.set(metric.key, metric);
  }
  return map;
}

export function formatMetricValue(metric: CalculationOutputMetric | undefined): string {
  if (!metric || metric.value === undefined || metric.value === null) return "—";
  const value = metric.value;
  if (typeof value === "number") {
    const rounded = Math.abs(value) >= 1000 ? value.toLocaleString("ko-KR") : String(Number(value.toFixed(4)));
    return `${rounded}${metric.unit ? ` ${metric.unit}` : ""}`;
  }
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

/** Signed numeric delta between two metrics, or null when not numerically comparable. */
export function metricDelta(a: CalculationOutputMetric | undefined, b: CalculationOutputMetric | undefined): number | null {
  const av = typeof a?.value === "number" ? a.value : null;
  const bv = typeof b?.value === "number" ? b.value : null;
  if (av === null || bv === null) return null;
  return bv - av;
}

export function formatRunTimestamp(run: CalculationRun): string {
  const raw = run.createdAt;
  if (!raw) return "시각 미상";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function shortRunId(runId: string): string {
  // "calc:unsold_ltv:6a2b9b83-..." -> "6a2b9b83"
  const tail = runId.split(":").pop() ?? runId;
  return tail.slice(0, 8);
}

/** Critical/high blocker codes on a run (SENIOR_SHORTFALL, UNSOLD_LTV_BREACH, ...). */
export function blockerCodes(run: CalculationRun): string[] {
  return (run.riskIssues ?? [])
    .filter((issue) => {
      const sev = String(issue.severity ?? "").toLowerCase();
      return sev === "critical" || sev === "high";
    })
    .map((issue) => String(issue.code ?? ""))
    .filter(Boolean);
}
