import { apiBase, authHeaders } from "./auth-client";

/** Lightweight project option for pickers. */
export interface ProjectOption {
  id: string;
  name: string;
  address?: string;
  pnu?: string;
  type?: string;
  status?: string;
}

interface RawProjectSummary {
  id?: string;
  project?: { id?: string | number; name?: string; type?: string; status?: string };
  land?: { address?: string; pnu?: string };
}

function toOption(p: RawProjectSummary): ProjectOption | null {
  const id = p.project?.id ?? p.id;
  if (id === undefined || id === null) return null;
  return {
    id: String(id),
    name: p.project?.name || `프로젝트 ${id}`,
    address: p.land?.address,
    pnu: p.land?.pnu,
    type: p.project?.type,
    status: p.project?.status,
  };
}

/** Fetch the session-scoped project list as light options for selectors. */
export async function fetchProjectOptions(): Promise<ProjectOption[]> {
  const res = await fetch(`${apiBase}/projects/`, {
    cache: "no-store",
    headers: authHeaders("web"),
  });
  if (!res.ok) throw new Error(`프로젝트 목록을 불러오지 못했습니다 (${res.status})`);
  const data = (await res.json()) as RawProjectSummary[];
  return (Array.isArray(data) ? data : []).map(toOption).filter((o): o is ProjectOption => o !== null);
}

/** Fetch a single project's full record. */
export async function fetchProjectDetail<T = Record<string, unknown>>(projectId: string): Promise<T> {
  const res = await fetch(`${apiBase}/projects/${projectId}`, {
    cache: "no-store",
    headers: authHeaders("web"),
  });
  if (!res.ok) throw new Error(`프로젝트를 불러오지 못했습니다 (${res.status})`);
  return (await res.json()) as T;
}

/** Real monthly cash flow extracted from a completed PF-review (orchestration) run. */
export interface CashflowMonth {
  period: string;
  cashInEok: number;
  cashOutEok: number;
  netCashFlowEok: number;
  cumulativeEok: number;
}

export interface CashflowResult {
  runId: string;
  completedAt?: string;
  monthly: CashflowMonth[];
  maxMinusEok: number;
  maxMinusMonth: number;
  summary: Record<string, number>;
}

interface RunListItem {
  run_id?: string;
  status?: string;
  started_at?: string;
  completed_at?: string;
}

const WON = 1e8;

/**
 * Fetch the latest completed run's cash-flow-engine output (real monthly cumulative
 * CF + Max Minus). Returns null when the project has no run with cash-flow results,
 * so callers show an honest "run the PF review first" state instead of demo data.
 */
export async function fetchLatestCashflow(projectId: string): Promise<CashflowResult | null> {
  const listRes = await fetch(`${apiBase}/projects/${projectId}/orchestrations`, {
    cache: "no-store",
    headers: authHeaders("web"),
  });
  if (!listRes.ok) return null;
  const listBody = await listRes.json();
  const runs: RunListItem[] = Array.isArray(listBody) ? listBody : listBody.runs ?? listBody.orchestrations ?? [];
  const ordered = [...runs]
    .filter((r) => r.run_id)
    .sort((a, b) => String(b.completed_at ?? b.started_at ?? "").localeCompare(String(a.completed_at ?? a.started_at ?? "")));

  // Probe the most recent runs for one that carries cash-flow results.
  for (const run of ordered.slice(0, 6)) {
    const detailRes = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${run.run_id}`, {
      cache: "no-store",
      headers: authHeaders("web"),
    });
    if (!detailRes.ok) continue;
    const detail = await detailRes.json();
    const cf = detail?.artifacts?.developerFeasibility?.cashFlowRunResult;
    const rows = cf?.monthlyCashFlow;
    if (!Array.isArray(rows) || rows.length === 0) continue;
    const summary = (cf.summary ?? {}) as Record<string, number>;
    return {
      runId: String(run.run_id),
      completedAt: run.completed_at,
      monthly: rows.map((r: Record<string, unknown>) => {
        const cashIn = (r.cashIn ?? {}) as Record<string, unknown>;
        const cashOut = (r.cashOut ?? {}) as Record<string, unknown>;
        return {
          period: String(r.period ?? `M${r.monthIndex ?? ""}`),
          cashInEok: Number(cashIn.totalCashIn ?? 0) / WON,
          cashOutEok: Number(cashOut.totalCashOut ?? 0) / WON,
          netCashFlowEok: Number(r.netCashFlow ?? 0) / WON,
          cumulativeEok: Number(r.cumulativeCashFlowBeforePfDraw ?? 0) / WON,
        };
      }),
      maxMinusEok: Number(summary.maxMinus ?? 0) / WON,
      maxMinusMonth: Number(summary.maxMinusMonth ?? 0),
      summary,
    };
  }
  return null;
}
