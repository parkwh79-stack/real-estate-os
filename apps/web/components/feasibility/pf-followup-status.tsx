import Link from "next/link";
import { pfFollowupStates, type PfFollowupExecution, type PfFollowupState } from "../../lib/pf-followup-status";
import type { CalculationRunRecord, CashFlowDependentEngine, CashflowResult } from "../../lib/project-client";

const ENGINES = {
  waterfall: {
    title: "상환순서", metric: "waterfall.summary.shortfallFlagCount", label: "상환 부족 경고", format: "count",
  },
  tranche_recovery: {
    title: "트랜치 회수율", metric: "tranche_recovery.senior", label: "선순위 최저 회수율", format: "ratio",
  },
  unsold_ltv: {
    title: "미분양 LTV", metric: "unsold_ltv.summary.maxUnsoldLtv", label: "최대 미분양 LTV", format: "ratio",
  },
  discount_stress: {
    title: "할인분양 스트레스", metric: "discount_stress.discount10.exitSaleRate", label: "10% 할인 시 Exit 분양률", format: "ratio",
  },
} as const;

const BADGES = {
  completed: { label: "완료", style: "bg-emerald-50 text-emerald-800" },
  needs_input: { label: "자료 필요", style: "bg-amber-50 text-amber-800" },
  unsupported: { label: "미지원", style: "bg-slate-100 text-slate-700" },
  failed: { label: "오류", style: "bg-rose-50 text-rose-800" },
  unavailable: { label: "조회 실패", style: "bg-rose-50 text-rose-800" },
  unknown: { label: "미확인", style: "bg-slate-100 text-slate-700" },
} as const;

const integer = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
const percent = new Intl.NumberFormat("ko-KR", { style: "percent", maximumFractionDigits: 0 });

function inputAction(projectId: string, state: PfFollowupState) {
  const base = `/project/${encodeURIComponent(projectId)}/input-package`;
  if (state.scheduleNeeded) return { href: `${base}#cash-flow-schedule-review`, label: "사업기간 확인" };
  if (state.missingFields?.includes("productMix")) return { href: base, label: "사업수지·분양 자료 검토" };
  if (state.engine === "discount_stress") return { href: base, label: "사업수지·일정 검토" };
  return { href: `${base}#cash-flow-financing-review`, label: "PF 대출 조건 확인" };
}

export function PfFollowupStatus({ projectId, cashflow, runs, execution, unavailableEngines }: {
  projectId: string;
  cashflow: CashflowResult;
  runs?: Record<string, CalculationRunRecord | null>;
  execution?: PfFollowupExecution | null;
  unavailableEngines?: readonly CashFlowDependentEngine[];
}) {
  const states = pfFollowupStates(cashflow, runs, execution, unavailableEngines);
  return <section aria-label="PF 후속 계산 상태" className="space-y-3">
    <h2 className="text-lg font-bold text-re-text">PF 후속 계산 상태</h2>
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      {states.map((state) => {
        const engine = ENGINES[state.engine];
        const badge = BADGES[state.status];
        const metric = state.run?.outputMetrics?.find((item) => (item.key ?? item.metricKey) === engine.metric)?.value;
        const displayValue = typeof metric === "number" && Number.isFinite(metric)
          ? engine.format === "count" ? `${integer.format(metric)}건` : percent.format(metric)
          : "미확인";
        const action = inputAction(projectId, state);
        return <article key={state.engine} aria-label={engine.title} className="min-w-0 rounded-re-panel border border-re-border bg-re-surface p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-bold text-re-text">{engine.title}</h3>
            <span className={`rounded-full px-2 py-1 text-xs font-semibold ${badge.style}`}>{badge.label}</span>
          </div>
          {state.run ? <>
            <p className="mt-3 text-xs font-semibold text-re-secondary">{engine.label}</p>
            <p className="mt-1 text-lg font-bold tabular-nums text-re-text">{displayValue}</p>
            <p className="mt-2 break-all text-xs leading-5 text-re-secondary">계산 ID {state.run.calculationRunId}</p>
          </> : <>
            <p className="mt-3 text-sm leading-6 text-re-secondary">{state.message}</p>
            <Link href={action.href} prefetch={false} className="mt-2 inline-block text-xs font-semibold text-re-info hover:underline">{action.label}</Link>
          </>}
        </article>;
      })}
    </div>
  </section>;
}
