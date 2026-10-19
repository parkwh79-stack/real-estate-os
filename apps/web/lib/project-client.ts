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

export type RecentWorkKind = "project" | "review" | "analysis";

export interface RecentWorkItem {
  /** Stable row key. Older recent-work-v2 responses omit this field. */
  itemId?: string;
  /** Work category. Older recent-work-v2 responses are normalized as project. */
  kind?: RecentWorkKind;
  projectId: string;
  projectName: string;
  title?: string;
  subtitle?: string;
  summary?: string;
  track?: string;
  lastWorkedAt?: string;
  updatedAt?: string;
  latestRunId?: string;
  latestRunStatus?: string;
  executionStatus?: string;
  sourceDocumentCount: number;
  openReviewCount: number;
  riskCount: number;
  approvalStatus: string;
  approvalReadinessStatus?: string;
  nextAction: string;
  nextActionLabel?: string;
  href?: string;
  sourceHashes?: string[];
}

export interface RecentWorkFeed {
  schemaVersion: string;
  items: RecentWorkItem[];
  totalCount: number;
  /** Non-fatal contract or upstream warnings. Valid rows remain usable. */
  warnings: string[];
}

export class RecentWorkRequestError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "RecentWorkRequestError";
  }
}

/**
 * Fetch the tenant-scoped recent-work projection.
 *
 * The normalizer accepts the original flat recent-work-v2 response as well as
 * the extended project/review/analysis collections. Invalid rows are dropped
 * with a visible warning instead of turning the whole work inbox into an empty
 * state.
 */
export async function fetchRecentWorkFeed(
  limit = 8,
  signal?: AbortSignal,
): Promise<RecentWorkFeed> {
  // A live tenant lookup crosses the BFF identity boundary and the normalized
  // PostgreSQL RLS boundary.  Keep the request bounded, but leave enough room
  // for a cold Supabase pool/JWKS cache instead of racing a valid 10-second
  // response into a false timeout state.
  const timeoutSignal = AbortSignal.timeout(20_000);
  const requestSignal = signal
    ? AbortSignal.any([signal, timeoutSignal])
    : timeoutSignal;
  let res: Response;
  try {
    res = await fetch(`${apiBase}/projects/recent-work?limit=${limit}`, {
      cache: "no-store",
      headers: authHeaders("web"),
      signal: requestSignal,
    });
  } catch (error) {
    if (timeoutSignal.aborted && !signal?.aborted) {
      throw new RecentWorkRequestError(
        "최근 작업 조회가 20초 안에 완료되지 않았습니다. 새 분석은 계속 사용할 수 있으며, 잠시 후 새로고침해 주세요.",
      );
    }
    throw error;
  }
  if (!res.ok) {
    const message =
      res.status === 401
        ? "로그인 세션이 만료되어 최근 작업을 불러오지 못했습니다."
        : res.status === 403
          ? "현재 계정의 Tenant 프로젝트 조회 권한을 확인할 수 없습니다."
          : `최근 작업을 불러오지 못했습니다. (${res.status})`;
    throw new RecentWorkRequestError(message, res.status);
  }
  const body: unknown = await res.json();
  return normalizeRecentWorkResponse(body, limit);
}

export async function fetchRecentWork(
  limit = 5,
  signal?: AbortSignal,
): Promise<RecentWorkItem[]> {
  return (await fetchRecentWorkFeed(limit, signal)).items;
}

export function normalizeRecentWorkResponse(
  body: unknown,
  limit = 20,
): RecentWorkFeed {
  const root = record(body);
  if (!root) {
    throw new RecentWorkRequestError("최근 작업 응답 형식이 올바르지 않습니다.");
  }
  const data = record(root.data) ?? root;
  const warnings = stringArray(
    first(data, "warnings", "warningMessages", "warning_messages", "errors"),
  );
  const candidates: Array<{ value: unknown; fallbackKind?: RecentWorkKind }> = [];
  const flat = first(data, "items", "recentWork", "recent_work");
  let groupedContractSeen = false;
  if (Array.isArray(flat)) {
    flat.forEach((value) => candidates.push({ value }));
  } else {
    const collections: Array<[RecentWorkKind, unknown]> = [
      ["project", first(data, "projects", "projectItems", "project_items")],
      ["review", first(data, "reviews", "reviewItems", "review_items")],
      ["analysis", first(data, "analyses", "analysisItems", "analysis_items")],
    ];
    collections.forEach(([fallbackKind, values]) => {
      if (Array.isArray(values)) {
        groupedContractSeen = true;
        values.forEach((value) => candidates.push({ value, fallbackKind }));
      }
    });
  }

  if (!Array.isArray(flat) && !groupedContractSeen) {
    throw new RecentWorkRequestError("최근 작업 응답에 items 또는 유형별 목록이 없습니다.");
  }

  const normalized: RecentWorkItem[] = [];
  let dropped = 0;
  candidates.forEach(({ value, fallbackKind }, index) => {
    const item = normalizeRecentWorkItem(value, index, fallbackKind);
    if (item) normalized.push(item);
    else dropped += 1;
  });
  if (dropped > 0) warnings.push(`필수 프로젝트 식별자가 없는 최근 작업 ${dropped}건을 제외했습니다.`);
  if (boolean(first(data, "partial", "isPartial", "is_partial"))) {
    warnings.push("서버가 일부 최근 작업만 반환했습니다.");
  }

  const cappedLimit = Math.max(1, Math.min(50, Number.isFinite(limit) ? Math.trunc(limit) : 20));
  const items = normalized
    .sort((left, right) => String(right.updatedAt ?? right.lastWorkedAt ?? "").localeCompare(String(left.updatedAt ?? left.lastWorkedAt ?? "")))
    .slice(0, cappedLimit);
  const declaredCount = number(first(data, "totalCount", "total_count", "count"));
  return {
    schemaVersion: text(first(data, "schemaVersion", "schema_version")) ?? "realytics-recent-work-v2",
    items,
    totalCount: declaredCount ?? normalized.length,
    warnings: unique(warnings),
  };
}

function normalizeRecentWorkItem(
  value: unknown,
  index: number,
  fallbackKind?: RecentWorkKind,
): RecentWorkItem | null {
  const source = record(value);
  if (!source) return null;
  const project = record(first(source, "project"));
  const review = record(first(source, "review"));
  const analysis = record(first(source, "analysis"));
  const kind = normalizeKind(
    first(source, "kind", "itemKind", "item_kind", "itemType", "item_type", "workType", "work_type"),
    fallbackKind ?? (review ? "review" : analysis ? "analysis" : "project"),
  );
  const projectId = text(
    first(source, "projectId", "project_id") ?? first(project ?? {}, "id", "projectId", "project_id"),
  );
  if (!projectId) return null;

  const projectName =
    text(first(source, "projectName", "project_name")) ??
    text(first(project ?? {}, "name", "projectName", "project_name")) ??
    `프로젝트 ${projectId}`;
  const title =
    text(first(source, "title", "displayTitle", "display_title", "workTitle", "work_title", "reviewTitle", "review_title", "analysisTitle", "analysis_title")) ??
    text(first(review ?? {}, "title", "name")) ??
    text(first(analysis ?? {}, "title", "name")) ??
    projectName;
  const updatedAt = text(
    first(
      source,
      "updatedAt",
      "updated_at",
      "lastWorkedAt",
      "last_worked_at",
      "reviewedAt",
      "reviewed_at",
      "completedAt",
      "completed_at",
      "startedAt",
      "started_at",
    ),
  );
  const latestRunStatus = text(
    first(source, "latestRunStatus", "latest_run_status", "executionStatus", "execution_status"),
  );
  const approvalStatus =
    text(first(source, "approvalStatus", "approval_status")) ?? "not_requested";
  const sourceDocumentCount = integer(
    first(source, "sourceDocumentCount", "source_document_count", "documentCount", "document_count"),
  );
  const openReviewCount = integer(
    first(source, "openReviewCount", "open_review_count", "pendingReviewCount", "pending_review_count"),
  );
  const riskCount = integer(first(source, "riskCount", "risk_count"));
  const nextAction =
    text(first(source, "nextAction", "next_action")) ??
    (kind === "review" ? "resume_open_reviews" : kind === "analysis" ? "inspect_latest_run" : "continue_next_review_step");
  const explicitHref = text(first(source, "href", "url", "link", "nextHref", "next_href"));
  const href = safeInternalHref(explicitHref) ?? fallbackRecentWorkHref(kind, projectId, nextAction);
  const summary =
    text(first(source, "summary", "description", "detail")) ??
    buildRecentWorkSummary({ kind, sourceDocumentCount, openReviewCount, riskCount });

  return {
    itemId:
      text(first(source, "itemId", "item_id", "id")) ??
      `${kind}:${projectId}:${updatedAt ?? index}`,
    kind,
    projectId,
    projectName,
    title,
    subtitle: text(first(source, "subtitle", "subTitle", "sub_title")),
    summary,
    track: text(first(source, "track", "workflowTrack", "workflow_track")),
    lastWorkedAt: updatedAt,
    updatedAt,
    latestRunId: text(first(source, "latestRunId", "latest_run_id", "runId", "run_id")),
    latestRunStatus,
    executionStatus: latestRunStatus,
    sourceDocumentCount,
    openReviewCount,
    riskCount,
    approvalStatus,
    approvalReadinessStatus: text(first(source, "approvalReadinessStatus", "approval_readiness_status")),
    nextAction,
    nextActionLabel: text(first(source, "nextActionLabel", "next_action_label", "actionLabel", "action_label")),
    href,
    sourceHashes: stringArray(first(source, "sourceHashes", "source_hashes")),
  };
}

function fallbackRecentWorkHref(kind: RecentWorkKind, projectId: string, nextAction: string) {
  const base = `/project/${encodeURIComponent(projectId)}`;
  if (kind === "review") return `${base}/documents/review`;
  if (kind === "analysis") return `${base}/chief-orchestration`;
  if (nextAction === "upload_source_documents") return `${base}/input-package`;
  if (["run_initial_analysis", "resume_open_reviews", "inspect_latest_run"].includes(nextAction)) {
    return `${base}/chief-orchestration`;
  }
  return base;
}

function buildRecentWorkSummary(input: {
  kind: RecentWorkKind;
  sourceDocumentCount: number;
  openReviewCount: number;
  riskCount: number;
}) {
  if (input.kind === "review") return `미해결 검토 ${input.openReviewCount.toLocaleString("ko-KR")}건`;
  if (input.kind === "analysis") return `문서 ${input.sourceDocumentCount.toLocaleString("ko-KR")}건 · Risk ${input.riskCount.toLocaleString("ko-KR")}건`;
  return `문서 ${input.sourceDocumentCount.toLocaleString("ko-KR")}건 · 검토 ${input.openReviewCount.toLocaleString("ko-KR")}건 · Risk ${input.riskCount.toLocaleString("ko-KR")}건`;
}

function safeInternalHref(value: string | undefined) {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return undefined;
  return value;
}

function normalizeKind(value: unknown, fallback: RecentWorkKind): RecentWorkKind {
  const normalized = text(value)?.toLowerCase().replaceAll("-", "_");
  if (normalized && ["review", "document_review", "approval_review", "심사", "검토"].includes(normalized)) return "review";
  if (normalized && ["analysis", "analysis_run", "orchestration", "run", "calculation", "calculation_run", "분석", "실행"].includes(normalized)) return "analysis";
  if (normalized && ["project", "deal", "프로젝트", "딜"].includes(normalized)) return "project";
  return fallback;
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function first(source: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(source, key) && source[key] !== undefined && source[key] !== null) {
      return source[key];
    }
  }
  return undefined;
}

function text(value: unknown) {
  if (typeof value !== "string" && typeof value !== "number") return undefined;
  const normalized = String(value).trim();
  return normalized || undefined;
}

function number(value: unknown) {
  if (value === null || value === undefined || value === "") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function integer(value: unknown) {
  return Math.max(0, Math.trunc(number(value) ?? 0));
}

function boolean(value: unknown) {
  return value === true || String(value ?? "").trim().toLowerCase() === "true";
}

function stringArray(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value
    .map((entry) => {
      if (typeof entry === "string" || typeof entry === "number") return String(entry).trim();
      const item = record(entry);
      return item ? text(first(item, "message", "detail", "code")) : undefined;
    })
    .filter((entry): entry is string => Boolean(entry));
}

function unique(values: string[]) {
  return [...new Set(values)];
}

interface RawProjectSummary {
  id?: string | number;
  project?: {
    id?: string | number;
    name?: string;
    projectName?: string;
    type?: string;
    status?: string;
  };
  land?: { address?: string; pnu?: string };
}

function toOption(p: RawProjectSummary): ProjectOption | null {
  const id = p.project?.id ?? p.id;
  if (id === undefined || id === null) return null;
  const name = p.project?.name || p.project?.projectName || `프로젝트 ${id}`;
  return {
    id: String(id),
    name,
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
  if (!res.ok) throw new Error(`프로젝트 목록을 불러오지 못했습니다. (${res.status})`);
  const data = (await res.json()) as RawProjectSummary[];
  return (Array.isArray(data) ? data : []).map(toOption).filter((o): o is ProjectOption => o !== null);
}

/** Fetch a single project's full record. */
export async function fetchProjectDetail<T = Record<string, unknown>>(projectId: string): Promise<T> {
  const res = await fetch(`${apiBase}/projects/${projectId}`, {
    cache: "no-store",
    headers: authHeaders("web"),
  });
  if (!res.ok) throw new Error(`프로젝트를 불러오지 못했습니다. (${res.status})`);
  return (await res.json()) as T;
}

/** Real monthly cash flow extracted from a completed PF-review (orchestration) run. */
export interface CashflowMonth {
  period: string;
  cashInEok: number | null;
  cashOutEok: number | null;
  netCashFlowEok: number | null;
  cumulativeEok: number | null;
}

export type CashFlowPeriodBasis = {
  schemaVersion: "cash-flow-period-basis-v1";
  kind: "source_sequence" | "calendar_monthly";
  periods?: Array<{ sourcePeriodIndex: number; label: string; periodKind: "event" | "monthly" | "unknown";
    sourceMonthNumber: number | null; sourceLocator: { sourceName?: string; sheet?: string; cell?: string; monthCell?: string } }>;
  scheduleBindings?: Record<string, { sourcePeriodIndex: number | null; sourceMonthNumber: number | null }>;
  warnings?: string[];
  calculationPeriodCount?: number;
  firstCalculationPeriodIndex?: number;
  lastCalculationPeriodIndex?: number;
  interestCalculation?: { status: string; firstChargedMonthIndex?: number | null;
    lastChargedMonthIndex?: number | null; chargedPeriodCount?: number; method?: string; reason?: string };
};

export interface CashflowResult {
  runId: string;
  completedAt?: string;
  source: "chief_pod_artifact" | "pf_review_orchestration";
  artifactKey?: string;
  artifactType?: string;
  monthly: CashflowMonth[];
  maxMinusEok: number | null;
  /** Engine compatibility value: one-based row ordinal, not an M-month index. */
  maxMinusMonth: number | null;
  maxMinusPeriodIndex: number | null;
  summary: Record<string, number>;
  reportFragments?: Record<string, string>;
  riskFlags?: Array<Record<string, unknown>>;
  sourceMetadata?: Record<string, unknown>;
  inputSnapshot?: Record<string, unknown>;
  periodBasis?: CashFlowPeriodBasis;
  monthlyFinancing?: Record<string, unknown>;
  financingMonthly?: Array<{ period: string; interest: number | null; fees: number | null;
    principalRepayment: number | null; outstandingDebt: number | null; overdueDebt: number | null;
    unfundedOperatingCost: number | null }>;
}

interface RunListItem {
  run_id?: string;
  status?: string;
  started_at?: string;
  completed_at?: string;
  createdAt?: string;
  updatedAt?: string;
  calculationRunId?: string;
  engineKey?: string;
  resultSnapshot?: Record<string, unknown>;
  inputSnapshot?: Record<string, unknown>;
  sourceLocator?: Record<string, unknown>;
}

export interface CalculationRunRecord {
  calculationRunId: string;
  engineKey: string;
  engineVersion?: string;
  inputEvidenceIds?: string[];
  assumptionIds?: string[];
  outputMetrics?: Array<Record<string, unknown>>;
  riskIssueIds?: string[];
  sourceLocator?: Record<string, unknown>;
  scenario?: Record<string, unknown>;
  resultSnapshot?: Record<string, unknown>;
  inputSnapshot?: Record<string, unknown>;
  createdAt?: string;
  updatedAt?: string;
}

/** Show dependent results only when both their parent and exact input agree. */
export function bindCashflowDependents(
  cashflow: CashflowResult | null,
  runs: Record<string, CalculationRunRecord | null>,
): Record<string, CalculationRunRecord | null> {
  const digest = cashflow?.inputSnapshot?.financialInputHash;
  const monthlyReview = cashflow?.inputSnapshot?.monthlyFinancing != null;
  return Object.fromEntries(Object.entries(runs).map(([key, run]) => {
    const snapshot = run?.inputSnapshot ?? {};
    const source = run?.sourceLocator ?? {};
    const parent = snapshot.parentCalculationRunId ?? source.parentCalculationRunId;
    const inputHash = snapshot.financialInputHash ?? source.financialInputHash;
    const matches = !monthlyReview && typeof digest === "string" && digest.length > 0
      && parent === cashflow?.runId && inputHash === digest;
    return [key, matches ? run : null];
  }));
}

export async function fetchLatestCalculationRuns(
  projectId: string,
  engineKeys: string[],
): Promise<Record<string, CalculationRunRecord | null>> {
  const entries = await Promise.all(engineKeys.map(async (engineKey) => {
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/calculation-runs?engine_key=${encodeURIComponent(engineKey)}&limit=1`, {
        cache: "no-store",
        headers: authHeaders("web"),
      });
      if (!res.ok) return [engineKey, null] as const;
      const body = await res.json();
      const record = Array.isArray(body.records) ? body.records[0] : null;
      return [engineKey, record ?? null] as const;
    } catch {
      // A missing optional scenario must not hide the primary Cash Flow run.
      return [engineKey, null] as const;
    }
  }));
  return Object.fromEntries(entries);
}

const WON = 1e8;

function finiteNumberOrNull(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function wonToEokOrNull(value: unknown): number | null {
  const won = finiteNumberOrNull(value);
  return won === null ? null : won / WON;
}

function cashflowFromResult(
  cf: Record<string, unknown> | null | undefined,
  source: CashflowResult["source"],
  meta: { runId: string; completedAt?: string; artifactKey?: string; artifactType?: string; inputSnapshot?: Record<string, unknown> },
): CashflowResult | null {
  const rows = cf?.monthlyCashFlow ?? cf?.rows;
  if (!Array.isArray(rows) || rows.length === 0) return null;
  const summary = (cf?.summary ?? {}) as Record<string, number>;
  const monthlyReview = cf?.monthlyFinancing != null || meta.inputSnapshot?.monthlyFinancing != null;
  const maximumRow = summary.maxMinusMonth;
  const maximumPeriodIndex = typeof maximumRow === "number" && Number.isSafeInteger(maximumRow)
    && maximumRow > 0 && maximumRow <= rows.length ? maximumRow - 1 : null;
  return {
    runId: meta.runId,
    completedAt: meta.completedAt,
    source,
    artifactKey: meta.artifactKey,
    artifactType: meta.artifactType,
    monthly: rows.map((r: Record<string, unknown>) => {
      const cashIn = (r.cashIn ?? {}) as Record<string, unknown>;
      const cashOut = (r.cashOut ?? {}) as Record<string, unknown>;
      const financing = (r.monthlyFinancing ?? {}) as Record<string, unknown>;
      const operatingAndFinanceCost = finiteNumberOrNull(cashOut.totalCashOut);
      const principalRepayment = finiteNumberOrNull(financing.principalRepayment);
      const monthlyTotalOut = operatingAndFinanceCost !== null && principalRepayment !== null
        ? operatingAndFinanceCost + principalRepayment : null;
      return {
        period: String(r.period ?? `M${r.monthIndex ?? r.month ?? ""}`),
        cashInEok: wonToEokOrNull(monthlyReview ? cashIn.totalCashIn : cashIn.totalCashIn ?? r.cash_in),
        cashOutEok: wonToEokOrNull(monthlyReview ? monthlyTotalOut : cashOut.totalCashOut ?? r.cash_out),
        netCashFlowEok: wonToEokOrNull(monthlyReview ? r.netCashFlow :
          r.netCashFlowBeforePfDraw ?? r.netCashFlow ?? r.net_cashflow,
        ),
        cumulativeEok: wonToEokOrNull(monthlyReview ? r.endingCash :
          r.cumulativeCashFlowBeforePfDraw ??
            r.cumulativeCashFlow ??
            r.cumulative_cashflow,
        ),
      };
    }),
    maxMinusEok: wonToEokOrNull(summary.maxMinus),
    maxMinusMonth: finiteNumberOrNull(summary.maxMinusMonth),
    maxMinusPeriodIndex: maximumPeriodIndex,
    summary,
    reportFragments: (cf?.reportFragments ?? {}) as Record<string, string>,
    riskFlags: Array.isArray(cf?.riskFlags) ? (cf?.riskFlags as Array<Record<string, unknown>>) : [],
    sourceMetadata: (cf?.sourceMetadata ?? {}) as Record<string, unknown>,
    inputSnapshot: meta.inputSnapshot,
    periodBasis: cf?.periodBasis && typeof cf.periodBasis === "object"
      && !Array.isArray(cf.periodBasis)
      && (cf.periodBasis as CashFlowPeriodBasis).schemaVersion === "cash-flow-period-basis-v1"
      && ["source_sequence", "calendar_monthly"].includes((cf.periodBasis as CashFlowPeriodBasis).kind)
      ? cf.periodBasis as CashFlowPeriodBasis : undefined,
    monthlyFinancing: cf?.monthlyFinancing && typeof cf.monthlyFinancing === "object"
      ? cf.monthlyFinancing as Record<string, unknown> : undefined,
    financingMonthly: rows.filter((row: Record<string, unknown>) => row.monthlyFinancing != null).map((row: Record<string, unknown>) => {
      const financing = row.monthlyFinancing as Record<string, unknown>;
      return { period: String(row.period ?? `M${row.monthIndex}`),
        interest: finiteNumberOrNull(financing.interest), fees: finiteNumberOrNull(financing.fees),
        principalRepayment: finiteNumberOrNull(financing.principalRepayment),
        outstandingDebt: finiteNumberOrNull(financing.outstandingDebt), overdueDebt: finiteNumberOrNull(financing.overdueDebt),
        unfundedOperatingCost: finiteNumberOrNull(financing.unfundedOperatingCost) };
    }),
  };
}

/**
 * Fetch the latest stored cash-flow-engine output.
 *
 * Priority:
 * 1. Chief Deal Room feasibility_cashflow pod artifact
 * 2. Legacy/full PF-review orchestration artifact
 *
 * Returns null when the project has no stored cash-flow result, so callers show
 * an honest "run the Feasibility-Cashflow Pod first" state instead of demo data.
 */
export async function fetchLatestCashflow(projectId: string): Promise<CashflowResult | null> {
  // CalculationRun is the canonical source for the screen. The snapshot is
  // the exact deterministic output used by the run, including monthly rows;
  // artifact lookup remains a compatibility fallback for older projects.
  try {
    const runRes = await fetch(`${apiBase}/projects/${projectId}/calculation-runs?engine_key=cash_flow_engine&limit=10`, {
      cache: "no-store",
      headers: authHeaders("web"),
    });
    if (runRes.ok) {
      const runBody = await runRes.json();
      const runs: RunListItem[] = Array.isArray(runBody.records) ? runBody.records : [];
      const latest = runs[0];
      const snapshot = latest?.resultSnapshot;
      if (snapshot) {
        const parsed = cashflowFromResult(snapshot, "pf_review_orchestration", {
          runId: String(latest.calculationRunId ?? latest.run_id ?? ""),
          completedAt: String(latest.updatedAt ?? latest.completed_at ?? latest.createdAt ?? latest.started_at ?? ""),
          artifactKey: String((latest.sourceLocator as Record<string, unknown> | undefined)?.artifactKey ?? ""),
          artifactType: "calculation_run",
          inputSnapshot: latest.inputSnapshot,
        });
        if (parsed) return parsed;
      }
    }
  } catch {
    // Continue to compatibility artifacts when the calculation registry is temporarily unavailable.
  }

  try {
    const artifactRes = await fetch(`${apiBase}/projects/${projectId}/chief-orchestration/artifacts?pod_key=feasibility_cashflow`, {
      cache: "no-store",
      headers: authHeaders("web"),
    });
    if (artifactRes.ok) {
      const body = await artifactRes.json();
      const records = Array.isArray(body.records) ? body.records : [];
      for (const record of records) {
        const result = (record.result ?? record) as Record<string, unknown>;
        const cf = result.cashFlowRunResult as Record<string, unknown> | undefined;
        const parsed = cashflowFromResult(cf, "chief_pod_artifact", {
          runId: String(record.runId ?? record.artifactKey ?? "chief-pod"),
          completedAt: String(record.updatedAt ?? record.createdAt ?? ""),
          artifactKey: String(record.artifactKey ?? ""),
          artifactType: String(result.artifactType ?? record.artifactType ?? ""),
        });
        if (parsed) return parsed;
      }
    }
  } catch {
    // Compatibility fallback is optional; never hide a registry result or empty state.
  }

  try {
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
      try {
        const detailRes = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${run.run_id}`, {
          cache: "no-store",
          headers: authHeaders("web"),
        });
        if (!detailRes.ok) continue;
        const detail = await detailRes.json();
        const parsed = cashflowFromResult(detail?.artifacts?.developerFeasibility?.cashFlowRunResult, "pf_review_orchestration", {
          runId: String(run.run_id),
          completedAt: run.completed_at,
        });
        if (parsed) return parsed;
      } catch {
        continue;
      }
    }
  } catch {
    return null;
  }
  return null;
}
