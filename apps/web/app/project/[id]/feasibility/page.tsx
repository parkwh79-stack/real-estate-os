"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowRight,
  BarChart3,
  Calculator,
  FileSearch,
  FileSpreadsheet,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Wallet,
} from "lucide-react";
import { FinancialMetric } from "@repo/ui/financial-metric";
import {
  ApprovalReadinessBadge,
  ApprovalStatusBadge,
  ExecutionStatusBadge,
  ReportEligibilityBadge,
  normalizeApprovalReadinessStatus,
  normalizeApprovalStatus,
  normalizeExecutionStatus,
} from "../../../../components/ui/primitives";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import {
  resolveFinancialDecisionBinding,
  type FinancialDecisionBinding,
} from "../../../../lib/financial-decision-package";
import { fetchLatestCashflow, type CashflowResult } from "../../../../lib/project-client";
import {
  displayText,
  fetchWorkspaceContext,
  type WorkspacePageContext,
} from "../../../../lib/workspace-context";

interface ApprovalGateCheck {
  code?: string;
  message?: string;
  severity?: string;
  pass?: boolean;
}

interface ApprovalReadinessGate {
  pass?: boolean;
  status?: string;
  summary?: {
    blockingIssueCount?: number;
    warningCount?: number;
    dataQualityStatus?: string;
    reconciliationStatus?: string;
  };
  blockers?: ApprovalGateCheck[];
  warnings?: ApprovalGateCheck[];
  requiredActions?: string[];
}

interface DecisionRun {
  run_id: string;
  workflow?: string;
  status?: string;
  executionStatus?: string;
  approvalStatus?: string;
  approvalReadinessStatus?: string;
  reportStatus?: string;
  started_at?: string;
  completed_at?: string;
  approval?: {
    approved_by?: string;
    approved_at?: string;
  } | null;
  artifacts?: {
    approvalReadinessGate?: ApprovalReadinessGate;
    projectSnapshot?: {
      snapshotId?: string;
      snapshotHash?: string;
      runId?: string;
    };
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function firstFinite(...values: unknown[]): number | null {
  for (const value of values) {
    if (value === null || value === undefined || value === "") continue;
    const number = Number(value);
    if (Number.isFinite(number)) return number;
  }
  return null;
}

function firstText(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function formatWonAsEok(value: number | null) {
  if (value === null) return "미확인";
  return (value / 100_000_000).toLocaleString("ko-KR", {
    maximumFractionDigits: 1,
  });
}

function formatRatio(value: number | null) {
  if (value === null) return "미확인";
  return (value * 100).toLocaleString("ko-KR", { maximumFractionDigits: 1 });
}

function formatEok(value: number | null) {
  if (value === null) return "미확인";
  return value.toLocaleString("ko-KR", { maximumFractionDigits: 1 });
}

function formatDate(value?: string) {
  if (!value) return "미확인";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function responseError(response: Response) {
  const body = (await response.json().catch(() => null)) as { detail?: string } | null;
  return body?.detail || `요청을 처리하지 못했습니다. (${response.status})`;
}

async function fetchLatestDecisionRun(projectId: string): Promise<DecisionRun | null> {
  const listResponse = await fetch(`${apiBase}/projects/${projectId}/orchestrations`, {
    cache: "no-store",
    headers: authHeaders("web"),
  });
  if (!listResponse.ok) throw new Error(await responseError(listResponse));

  const body = (await listResponse.json()) as
    | DecisionRun[]
    | { runs?: DecisionRun[]; orchestrations?: DecisionRun[] };
  const runs = Array.isArray(body) ? body : body.runs ?? body.orchestrations ?? [];
  const latest = [...runs]
    .filter((item) => item.run_id)
    .sort((a, b) =>
      String(b.completed_at ?? b.started_at ?? "").localeCompare(
        String(a.completed_at ?? a.started_at ?? ""),
      ),
    )[0];
  if (!latest) return null;

  const detailResponse = await fetch(
    `${apiBase}/projects/${projectId}/orchestrations/${latest.run_id}`,
    { cache: "no-store", headers: authHeaders("web") },
  );
  if (!detailResponse.ok) throw new Error(await responseError(detailResponse));
  return (await detailResponse.json()) as DecisionRun;
}

export default function FeasibilityPage() {
  const params = useParams<{ id: string }>();
  const projectId = String(params.id);
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [cashflow, setCashflow] = useState<CashflowResult | null>(null);
  const [decisionRun, setDecisionRun] = useState<DecisionRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [contextResult, cashflowResult, runResult] = await Promise.allSettled([
      fetchWorkspaceContext(projectId, "feasibility"),
      fetchLatestCashflow(projectId),
      fetchLatestDecisionRun(projectId),
    ]);

    if (contextResult.status === "fulfilled") setContext(contextResult.value);
    if (cashflowResult.status === "fulfilled") setCashflow(cashflowResult.value);
    if (runResult.status === "fulfilled") setDecisionRun(runResult.value);

    const failures = [contextResult, cashflowResult, runResult]
      .filter((result): result is PromiseRejectedResult => result.status === "rejected")
      .map((result) =>
        result.reason instanceof Error ? result.reason.message : "일부 데이터를 불러오지 못했습니다.",
      );
    if (failures.length) setError(`부분 조회 실패: ${failures.join(" · ")}`);
    setLoading(false);
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runFeasibilityPod() {
    setRunning(true);
    setError(null);
    try {
      const response = await fetch(
        `${apiBase}/projects/${projectId}/chief-orchestration/run-pod`,
        {
          method: "POST",
          headers: { ...authHeaders("web"), "Content-Type": "application/json" },
          body: JSON.stringify({ podKey: "feasibility_cashflow" }),
        },
      );
      if (!response.ok) throw new Error(await responseError(response));
      await load();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Feasibility-Cashflow Pod 실행에 실패했습니다.",
      );
    } finally {
      setRunning(false);
    }
  }

  const cashflowSummary = cashflow?.summary ?? {};
  const inputSnapshot = asRecord(cashflow?.inputSnapshot);
  const sourceMetadata = asRecord(cashflow?.sourceMetadata);
  const projectName = displayText(
    context?.projectMemory?.projectName,
    `프로젝트 ${projectId}`,
  );

  // Every displayed financial value must come from the same Cash Flow result.
  // Mutable workspace projections are intentionally not used as fallbacks.
  const totalRevenue = firstFinite(cashflowSummary.totalRevenue);
  const totalCost = firstFinite(cashflowSummary.totalProjectCost);
  const developmentProfit =
    totalRevenue !== null && totalCost !== null
      ? totalRevenue - totalCost
      : firstFinite(cashflowSummary.developmentProfit);
  const profitMargin =
    totalRevenue !== null && totalRevenue !== 0 && developmentProfit !== null
      ? developmentProfit / totalRevenue
      : firstFinite(cashflowSummary.profitMargin);
  const pfLimit = firstFinite(cashflowSummary.pfLimit, cashflowSummary.totalPfDraw);

  const sourceSnapshotId = firstText(
    inputSnapshot.snapshotId,
    inputSnapshot.snapshot_id,
    sourceMetadata.sourceSnapshotId,
  );
  const calculationVersion = firstText(
    sourceMetadata.engineVersion,
    sourceMetadata.calculationVersion,
    sourceMetadata.schemaVersion,
  );
  const calculationSource =
    cashflow?.source === "chief_pod_artifact"
      ? "Feasibility-Cashflow Pod artifact"
      : cashflow
        ? "PF Review orchestration artifact"
        : undefined;

  const decisionSnapshot = decisionRun?.artifacts?.projectSnapshot;
  const decisionBinding = resolveFinancialDecisionBinding(
    {
      runId: firstText(
        sourceMetadata.orchestrationRunId,
        sourceMetadata.parentOrchestrationRunId,
        inputSnapshot.orchestrationRunId,
      ),
      snapshotId: firstText(
        sourceMetadata.projectSnapshotId,
        inputSnapshot.projectSnapshotId,
      ),
      snapshotHash: firstText(
        sourceMetadata.projectSnapshotHash,
        inputSnapshot.projectSnapshotHash,
      ),
    },
    {
      runId: decisionRun?.run_id,
      snapshotId: decisionSnapshot?.snapshotId,
      snapshotHash: decisionSnapshot?.snapshotHash,
    },
  );

  const gate = decisionRun?.artifacts?.approvalReadinessGate;
  const executionStatus = normalizeExecutionStatus(
    decisionRun?.executionStatus ?? decisionRun?.status,
  );
  const latestDecisionApprovalStatus = normalizeApprovalStatus(
    decisionRun?.approvalStatus ??
      (decisionRun?.approval ? "approved" : decisionRun ? "not_requested" : undefined),
  );
  const approvalReadinessStatus = normalizeApprovalReadinessStatus(
    decisionRun?.approvalReadinessStatus ?? gate?.status,
  );
  const sourceDeclaresReportEligible =
    sourceMetadata.reportEligible === true || sourceMetadata.report_eligible === true;
  const sourceDeclaresAnalysisOnly =
    sourceMetadata.analysisOnly === true || sourceMetadata.analysis_only === true;
  const reportEligibility =
    decisionBinding.status === "bound" &&
    sourceDeclaresReportEligible &&
    !sourceDeclaresAnalysisOnly
      ? "eligible"
      : "analysis_only";
  const calculationApprovalStatus =
    decisionBinding.status === "bound" ? latestDecisionApprovalStatus : "unknown";
  const rows = useMemo(() => cashflow?.monthly.slice(0, 18) ?? [], [cashflow]);
  const riskFlags = cashflow?.riskFlags ?? [];
  const metricProps = {
    currency: "KRW",
    scenario: "base",
    calculationRunId: cashflow?.runId,
    calculationVersion,
    sourceSnapshotId,
    source: calculationSource,
    approvalStatus: calculationApprovalStatus,
    reportEligibility: reportEligibility as "eligible" | "analysis_only",
  };

  return (
    <div className="space-y-5 pb-8">
      <header className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <p className="text-sm font-semibold text-re-secondary">{projectName}</p>
          <h1 className="mt-1 flex items-center gap-2 text-2xl font-black tracking-tight text-re-text">
            <BarChart3 className="h-6 w-6 text-re-info" />
            사업성·PF 의사결정
          </h1>
          <p className="mt-2 max-w-4xl text-sm leading-6 text-re-secondary">
            하나의 저장된 계산 artifact에서만 수치를 표시합니다. 최신 PF Review 승인과 계산
            lineage가 정확히 일치하지 않으면 승인 상태를 상속하지 않습니다.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading || running}
            className="inline-flex h-10 items-center gap-2 rounded-re-control border border-re-border bg-re-surface px-4 text-sm font-bold text-re-text disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            새로고침
          </button>
          <button
            type="button"
            onClick={() => void runFeasibilityPod()}
            disabled={loading || running}
            className="inline-flex h-10 items-center gap-2 rounded-re-control bg-re-primary px-4 text-sm font-bold text-white disabled:opacity-50"
          >
            {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wallet className="h-4 w-4" />}
            Cash Flow 재계산
          </button>
        </div>
      </header>

      {error ? (
        <div className="flex items-start gap-2 rounded-re-control border border-amber-200 bg-amber-50 p-4 text-sm font-semibold text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error} 현재 확인 가능한 데이터는 계속 표시합니다.</span>
        </div>
      ) : null}

      {cashflow && decisionBinding.status === "unbound" ? (
        <div className="flex items-start gap-2 rounded-re-control border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-black">표시 계산과 최신 PF Review 패키지가 결합되지 않았습니다.</p>
            <p className="mt-1 leading-6">
              동일 run·project snapshot ID·hash를 모두 확인할 수 없어 계산값은 분석 전용이며,
              최신 승인 상태를 계산 지표에 적용하지 않습니다.
            </p>
          </div>
        </div>
      ) : null}

      {loading ? (
        <div className="flex min-h-[260px] items-center justify-center rounded-re-panel border border-re-border bg-re-surface text-sm font-bold text-re-secondary">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          사업성 원장과 최신 승인 Gate를 조회하고 있습니다.
        </div>
      ) : (
        <>
          <section className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_360px]">
            <div className="space-y-4">
              {!cashflow && totalRevenue === null ? (
                <EmptyState
                  icon={<FileSpreadsheet className="h-6 w-6" />}
                  title="저장된 사업성 계산 artifact가 없습니다."
                  body="사업수지 Excel 또는 사업계획서를 업로드·검토한 뒤 Cash Flow를 계산하세요."
                  actionHref={`/project/${projectId}/documents/review`}
                  actionLabel="문서 검토로 이동"
                />
              ) : null}

              <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                <FinancialMetric
                  {...metricProps}
                  metricKey="revenue.total"
                  label="총 분양·매각수입"
                  rawValue={totalRevenue}
                  rawUnit="KRW"
                  displayValue={formatWonAsEok(totalRevenue)}
                  displayUnit="억원"
                  basis="동일 계산 artifact 입력 기준"
                  valueNature="calculated"
                />
                <FinancialMetric
                  {...metricProps}
                  metricKey="cost.total_project_cost"
                  label="총 사업비"
                  rawValue={totalCost}
                  rawUnit="KRW"
                  displayValue={formatWonAsEok(totalCost)}
                  displayUnit="억원"
                  basis="동일 계산 artifact 입력 기준"
                  valueNature="calculated"
                />
                <FinancialMetric
                  {...metricProps}
                  metricKey="returns.development_profit"
                  label="개발이익"
                  rawValue={developmentProfit}
                  rawUnit="KRW"
                  displayValue={formatWonAsEok(developmentProfit)}
                  displayUnit="억원"
                  basis="총수입 - 총사업비"
                  valueNature="calculated"
                />
                <FinancialMetric
                  {...metricProps}
                  metricKey="returns.profit_margin"
                  label="이익률"
                  rawValue={profitMargin}
                  rawUnit="ratio"
                  displayValue={formatRatio(profitMargin)}
                  displayUnit="%"
                  basis="개발이익 / 총수입"
                  valueNature="calculated"
                />
                <FinancialMetric
                  {...metricProps}
                  metricKey="cashflow.max_minus"
                  label="Max Minus"
                  rawValue={cashflow?.maxMinusEok ?? null}
                  rawUnit="KRW_100M"
                  displayValue={formatEok(cashflow?.maxMinusEok ?? null)}
                  displayUnit="억원"
                  basis={cashflow?.maxMinusMonth === null || cashflow?.maxMinusMonth === undefined ? "발생월 미확인" : `M${cashflow.maxMinusMonth}`}
                  valueNature="calculated"
                />
                <FinancialMetric
                  {...metricProps}
                  metricKey="financing.pf_limit_or_draw"
                  label="PF 한도·Draw"
                  rawValue={pfLimit}
                  rawUnit="KRW"
                  displayValue={formatWonAsEok(pfLimit)}
                  displayUnit="억원"
                  basis="저장된 계산 summary"
                  valueNature="calculated"
                />
              </section>
            </div>

            <DecisionGatePanel
              projectId={projectId}
              run={decisionRun}
              gate={gate}
              executionStatus={executionStatus}
              approvalStatus={latestDecisionApprovalStatus}
              approvalReadinessStatus={approvalReadinessStatus}
              reportEligibility={reportEligibility}
              decisionBinding={decisionBinding}
            />
          </section>

          <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
            <Panel title="월별 Cash Flow" subtitle="최근 저장 결과의 최초 18개월 · 억원">
              {rows.length ? (
                <div className="max-h-[520px] overflow-auto">
                  <table className="w-full min-w-[760px] border-separate border-spacing-0 text-sm">
                    <thead className="sticky top-0 z-10 bg-re-surface text-left text-xs text-re-secondary">
                      <tr>
                        <TableHead>기간</TableHead>
                        <TableHead align="right">Cash-In</TableHead>
                        <TableHead align="right">Cash-Out</TableHead>
                        <TableHead align="right">Net CF</TableHead>
                        <TableHead align="right">누적 CF</TableHead>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row) => (
                        <tr key={row.period} className="border-b border-re-border last:border-0">
                          <TableCell strong>{row.period}</TableCell>
                          <TableCell align="right">{formatEok(row.cashInEok)}</TableCell>
                          <TableCell align="right">{formatEok(row.cashOutEok)}</TableCell>
                          <TableCell align="right" negative={row.netCashFlowEok !== null && row.netCashFlowEok < 0}>
                            {formatEok(row.netCashFlowEok)}
                          </TableCell>
                          <TableCell align="right" strong negative={row.cumulativeEok !== null && row.cumulativeEok < 0}>
                            {formatEok(row.cumulativeEok)}
                          </TableCell>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="rounded-re-control bg-re-subtle p-5 text-sm text-re-secondary">
                  월별 Cash Flow가 아직 저장되지 않았습니다.
                </p>
              )}
            </Panel>

            <div className="space-y-4">
              <Panel title="계산 Lineage">
                <InfoRow label="계산 Run" value={cashflow?.runId} mono />
                <InfoRow label="Artifact" value={cashflow?.artifactKey} mono />
                <InfoRow label="입력 Snapshot" value={sourceSnapshotId} mono />
                <InfoRow
                  label="승인 패키지 결합"
                  value={decisionBinding.status === "bound" ? "정확히 일치" : "미결합·분석 전용"}
                />
                <InfoRow label="계산 버전" value={calculationVersion} mono />
                <InfoRow label="완료 시각" value={formatDate(cashflow?.completedAt)} />
                <InfoRow label="계산 소스" value={calculationSource} />
              </Panel>

              <Panel title="계산 Risk Flags" subtitle="Cash Flow artifact에 저장된 항목만 표시">
                {riskFlags.length ? (
                  <ul className="space-y-2">
                    {riskFlags.slice(0, 8).map((flag, index) => {
                      const record = asRecord(flag);
                      const label = firstText(record.message, record.title, record.code) ?? `Risk ${index + 1}`;
                      return (
                        <li key={`${label}-${index}`} className="rounded-re-control border border-amber-200 bg-amber-50 p-3 text-xs font-semibold leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                          {label}
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  <p className="text-sm text-re-secondary">저장된 Risk Flag가 없습니다.</p>
                )}
              </Panel>
            </div>
          </section>

          <section className="grid gap-3 md:grid-cols-4">
            <ActionLink
              href={`/project/${projectId}/documents/review`}
              icon={<FileSearch className="h-4 w-4" />}
              title="문서·추출값 검토"
              body="원문 locator와 승인 후보 확인"
            />
            <ActionLink
              href={`/project/${projectId}/evidence`}
              icon={<ShieldCheck className="h-4 w-4" />}
              title="근거 원장"
              body="누락·충돌·검증 상태 확인"
            />
            <ActionLink
              href={`/project/${projectId}/orchestration`}
              icon={<Calculator className="h-4 w-4" />}
              title="PF Review·승인"
              body="Gate 차단 항목 해소 및 최종 승인"
            />
            <ActionLink
              href={`/project/${projectId}/report`}
              icon={<FileSpreadsheet className="h-4 w-4" />}
              title="심사보고서"
              body="분석 초안과 최종 발행 상태 확인"
            />
          </section>
        </>
      )}
    </div>
  );
}

function DecisionGatePanel({
  projectId,
  run,
  gate,
  executionStatus,
  approvalStatus,
  approvalReadinessStatus,
  reportEligibility,
  decisionBinding,
}: {
  projectId: string;
  run: DecisionRun | null;
  gate?: ApprovalReadinessGate;
  executionStatus: ReturnType<typeof normalizeExecutionStatus>;
  approvalStatus: ReturnType<typeof normalizeApprovalStatus>;
  approvalReadinessStatus: ReturnType<typeof normalizeApprovalReadinessStatus>;
  reportEligibility: "eligible" | "analysis_only";
  decisionBinding: FinancialDecisionBinding;
}) {
  const blockers = gate?.blockers ?? [];
  const actions = gate?.requiredActions ?? [];
  const gateLabel = gate?.pass === true ? "최종 승인 가능" : gate?.pass === false ? "최종 승인 차단" : "Gate 미평가";

  return (
    <aside className="rounded-re-panel border border-re-border bg-re-surface p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.14em] text-re-muted">Latest PF Review Gate</p>
          <h2 className={`mt-2 text-xl font-black ${gate?.pass === true ? "text-re-success" : "text-re-danger"}`}>
            {gateLabel}
          </h2>
        </div>
        <ShieldCheck className="h-6 w-6 text-re-secondary" />
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <ExecutionStatusBadge status={executionStatus} />
        <ApprovalReadinessBadge status={approvalReadinessStatus} />
        <ApprovalStatusBadge status={approvalStatus} />
        <ReportEligibilityBadge status={reportEligibility} />
      </div>

      {decisionBinding.status === "unbound" ? (
        <div className="mt-4 rounded-re-control border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          이 Gate는 최신 PF Review 패키지의 상태입니다. 현재 표시 중인 계산값과 동일한
          run·snapshot ID·hash가 증명되지 않아 계산값의 승인 근거로 적용되지 않습니다.
        </div>
      ) : null}

      <dl className="mt-5 grid grid-cols-2 gap-2">
        <GateCount label="차단" value={gate?.summary?.blockingIssueCount} tone="bad" />
        <GateCount label="경고" value={gate?.summary?.warningCount} tone="warn" />
      </dl>

      {blockers.length ? (
        <div className="mt-5">
          <h3 className="text-sm font-black text-re-text">승인 차단 사유</h3>
          <ul className="mt-2 space-y-2">
            {blockers.slice(0, 5).map((blocker, index) => (
              <li key={`${blocker.code ?? "blocker"}-${index}`} className="rounded-re-control border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100">
                <span className="font-black">{blocker.code ?? "BLOCKED"}</span>
                {blocker.message ? <span className="mt-0.5 block">{blocker.message}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {actions.length ? (
        <div className="mt-5">
          <h3 className="text-sm font-black text-re-text">다음 조치</h3>
          <ol className="mt-2 space-y-2 text-xs font-semibold leading-5 text-re-secondary">
            {actions.slice(0, 5).map((action, index) => (
              <li key={`${action}-${index}`} className="flex gap-2">
                <span className="text-re-muted">{index + 1}.</span>
                <span>{action}</span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      {!run ? (
        <p className="mt-5 rounded-re-control bg-re-subtle p-3 text-xs leading-5 text-re-secondary">
          PF Review 실행 이력이 없어 승인 Gate를 평가할 수 없습니다.
        </p>
      ) : null}

      <Link href={`/project/${projectId}/orchestration`} className="mt-5 inline-flex h-10 w-full items-center justify-center gap-2 rounded-re-control border border-re-border bg-re-subtle px-3 text-sm font-black text-re-text hover:bg-re-hover">
        Gate 상세와 보완 작업
        <ArrowRight className="h-4 w-4" />
      </Link>
    </aside>
  );
}

function Panel({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <section className="rounded-re-panel border border-re-border bg-re-surface p-5 shadow-sm">
      <div className="mb-4">
        <h2 className="text-base font-black text-re-text">{title}</h2>
        {subtitle ? <p className="mt-1 text-xs text-re-secondary">{subtitle}</p> : null}
      </div>
      {children}
    </section>
  );
}

function InfoRow({ label, value, mono = false }: { label: string; value?: string; mono?: boolean }) {
  return (
    <div className="grid grid-cols-[100px_minmax(0,1fr)] gap-3 border-b border-re-border py-2.5 text-xs last:border-0">
      <span className="font-semibold text-re-muted">{label}</span>
      <span className={`min-w-0 break-all text-right font-bold text-re-text ${mono ? "font-mono" : ""}`}>
        {value || "미확인"}
      </span>
    </div>
  );
}

function GateCount({ label, value, tone }: { label: string; value?: number; tone: "bad" | "warn" }) {
  const className = tone === "bad" ? "text-re-danger" : "text-re-warning";
  return (
    <div className="rounded-re-control bg-re-subtle p-3">
      <dt className="text-xs font-semibold text-re-muted">{label}</dt>
      <dd className={`mt-1 text-xl font-black ${className}`}>{value ?? "—"}</dd>
    </div>
  );
}

function TableHead({ children, align = "left" }: { children: ReactNode; align?: "left" | "right" }) {
  return <th className={`border-b border-re-border px-3 py-2.5 font-bold ${align === "right" ? "text-right" : "text-left"}`}>{children}</th>;
}

function TableCell({ children, align = "left", strong = false, negative = false }: { children: ReactNode; align?: "left" | "right"; strong?: boolean; negative?: boolean }) {
  return (
    <td className={`border-b border-re-border px-3 py-2.5 tabular-nums ${align === "right" ? "text-right" : "text-left"} ${strong ? "font-black text-re-text" : "font-semibold text-re-secondary"} ${negative ? "text-re-danger" : ""}`}>
      {children}
    </td>
  );
}

function EmptyState({ icon, title, body, actionHref, actionLabel }: { icon: ReactNode; title: string; body: string; actionHref: string; actionLabel: string }) {
  return (
    <section className="rounded-re-panel border border-dashed border-re-border bg-re-surface p-8 text-center">
      <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-re-subtle text-re-secondary">{icon}</div>
      <h2 className="mt-3 text-base font-black text-re-text">{title}</h2>
      <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-re-secondary">{body}</p>
      <Link href={actionHref} className="mt-4 inline-flex items-center gap-2 text-sm font-black text-re-info">
        {actionLabel}
        <ArrowRight className="h-4 w-4" />
      </Link>
    </section>
  );
}

function ActionLink({ href, icon, title, body }: { href: string; icon: ReactNode; title: string; body: string }) {
  return (
    <Link href={href} className="group rounded-re-panel border border-re-border bg-re-surface p-4 shadow-sm transition hover:border-re-primary">
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-2 text-sm font-black text-re-text">
          {icon}
          {title}
        </span>
        <ArrowRight className="h-4 w-4 text-re-muted transition group-hover:translate-x-0.5 group-hover:text-re-primary" />
      </div>
      <p className="mt-2 text-xs leading-5 text-re-secondary">{body}</p>
    </Link>
  );
}
