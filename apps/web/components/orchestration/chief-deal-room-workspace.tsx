"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  ArrowRight, Building2, CheckCircle2, ChevronDown, ClipboardList, FileCheck2,
  FileText, Landmark, LineChart, Loader2, MapPin, RefreshCw, ShieldCheck, Sparkles, X,
} from "lucide-react";
import { DashboardAtmosphere } from "../dashboard/dashboard-atmosphere";
import { ProjectWorkspaceLink } from "../layout/project-workspace-link";
import { isNormalizedLegalEvidenceApprovalEligible } from "../../lib/legal-approval-readiness";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { TRACK_LABELS } from "../../lib/workflow-track";
import type { ApprovalReadiness, ChiefDealOrchestrationPlan, PodKey } from "./chief-deal-room-map";
import styles from "./chief-deal-room-workspace.module.css";

type Plan = ChiefDealOrchestrationPlan;
type MissingInput = NonNullable<Plan["sharedDealStateGraph"]["missingInputRegister"]>[number];
type PreflightMaterial = MissingInput & { action?: "provide_material" | "review_document"; purpose?: string; podKeys?: PodKey[] };
type PreflightCheck = { key: string; label: string; status: "available" | "missing" | "needs_review" };
type PreflightResult = {
  schemaVersion: "realytics-chief-pod-preflight-v1";
  projectId: string;
  analysisOnly: true;
  pods: { podKey: PodKey; status: "checked" | "partial" | "needs_material"; checks: PreflightCheck[] }[];
  materialRequests: PreflightMaterial[];
  publicLookups: { key: string; label: string; status: "available" | "checked" | "unavailable" | "needs_input" | "failed"; message: string }[];
};

type TaskGroup = { title: string; path: string; action: string; items: PreflightMaterial[] };

const podLabels = {
  land_legal: { title: "토지·인허가", description: "필지, 용도지역, 권리관계", path: "land", icon: MapPin },
  market_product: { title: "시장·상품", description: "수요, 가격, 비교사례", path: "marketability", icon: Building2 },
  cost_schedule: { title: "공사비·일정", description: "공사비와 사업 일정", path: "feasibility", icon: ClipboardList },
  feasibility_cashflow: { title: "사업성·현금흐름", description: "수입, 비용, 자금 부족액", path: "cashflow", icon: LineChart },
  pf_credit: { title: "PF·금융구조", description: "대출조건, 담보와 상환", path: "pf-structure", icon: Landmark },
  public_policy: { title: "도시계획·공공성", description: "지구단위계획, 공공 요건", path: "land", icon: FileCheck2 },
} satisfies Record<PodKey, { title: string; description: string; path: string; icon: typeof MapPin }>;

const nextActions = {
  documents: { title: "심사 자료 올리기", path: "input-package", action: "자료 입력" },
  review: { title: "보관한 문서에서 후보값 확인", path: "documents/review", action: "문서 검토" },
  land: { title: "필지와 토지 근거 확인", path: "land", action: "토지 검토" },
  market: { title: "시장·상품 자료 확인", path: "marketability", action: "시장 검토" },
  feasibility: { title: "사업수지와 일정 입력", path: "feasibility", action: "사업성 검토" },
  finance: { title: "대출 조건 확인", path: "pf-structure", action: "금융구조 검토" },
};

const inputActions: Record<string, keyof typeof nextActions> = {
  source_documents_missing: "documents",
  pnu_or_land_ledger_missing: "land",
  land_evidence_missing: "land",
  registry_evidence_missing: "land",
  regulation_data_missing: "land",
  legal_cp_missing: "documents",
  market_evidence_missing: "market",
  product_mix_missing: "market",
  revenue_assumption_missing: "feasibility",
  cost_assumption_missing: "feasibility",
  schedule_missing: "feasibility",
  pf_structure_missing: "finance",
};

const preflightPodStatus = { checked: "확인 완료", partial: "일부 확인", needs_material: "자료 필요" };
const preflightCheckStatus = { available: "확인", missing: "미확인", needs_review: "검토 필요" };
const importance = { blocking: 0, high: 1, medium: 2, info: 3 };
const stateLabels = {
  planned: "대기",
  running: "검토 중",
  completed: "검토 완료",
  blocked: "보완 필요",
  failed: "다시 확인 필요",
  needs_recalculation: "재검토 필요",
} as const;

function openInputs(plan: Plan) {
  return (plan.sharedDealStateGraph.missingInputRegister ?? [])
    .filter((item) => !["resolved", "waived", "closed", "completed"].includes(item.status ?? ""))
    .sort((left, right) => importance[left.severity] - importance[right.severity]);
}

function taskGroups(items: PreflightMaterial[]) {
  const groups = new Map<string, TaskGroup>();
  for (const item of items) {
    const key = item.action === "review_document" ? "review" : inputActions[item.itemId] ?? "documents";
    const existing = groups.get(key);
    if (existing) existing.items.push(item);
    else groups.set(key, { ...nextActions[key], items: [item] });
  }
  return [...groups.values()];
}

function approvalSummary(readiness: ApprovalReadiness) {
  if (readiness.state === "loading") return "확인 중";
  if (readiness.state === "unavailable") return "조회 불가";
  const { gate } = readiness;
  if (gate.assessmentStatus !== "assessed") return "평가 전";
  return gate.pass === true && isNormalizedLegalEvidenceApprovalEligible(gate) ? "승인 준비 검토 통과" : "추가 확인 필요";
}

const primaryButton = "inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-white px-4 py-2 text-sm font-bold text-slate-950 shadow-lg shadow-slate-950/20 transition-colors hover:bg-cyan-50 disabled:cursor-not-allowed disabled:opacity-60";
const secondaryButton = "inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border border-re-border bg-re-surface px-3 py-2 text-sm font-semibold text-re-text transition-colors hover:border-re-border-strong hover:bg-re-subtle disabled:cursor-not-allowed disabled:opacity-60";

export function ChiefDealRoomWorkspace({
  projectId, plan, loading, error, readiness, executing, canAnalyze, onRefresh, onRun,
}: {
  projectId: string;
  plan: Plan | null;
  loading: boolean;
  error: string | null;
  readiness: ApprovalReadiness;
  executing: boolean;
  canAnalyze: boolean;
  onRefresh: () => void;
  onRun: () => void;
}) {
  const details = useRef<HTMLDialogElement>(null);
  const detailsTitleId = useId();
  const [selected, setSelected] = useState<PodKey | null>(null);

  const preflightRequest = useRef<AbortController | null>(null);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [preflightBusy, setPreflightBusy] = useState(false);
  const [preflightError, setPreflightError] = useState(false);

  useEffect(() => () => preflightRequest.current?.abort(), []);

  function resetPreflight() {
    preflightRequest.current?.abort();
    preflightRequest.current = null;
    setPreflight(null);
    setPreflightBusy(false);
    setPreflightError(false);
  }

  async function startPreflight() {
    if (preflightRequest.current || !plan || !canAnalyze) return;
    const controller = new AbortController();
    preflightRequest.current = controller;
    setPreflight(null);
    setPreflightBusy(true);
    setPreflightError(false);
    try {
      const response = await fetch(apiBase + "/projects/" + encodeURIComponent(projectId) + "/chief-orchestration/preflight", {
        method: "POST",
        cache: "no-store",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ workflowTrack: plan.taskGraph?.workflowTrack }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("Preflight unavailable");
      const result = (await response.json()) as PreflightResult;
      if (result.schemaVersion !== "realytics-chief-pod-preflight-v1" || result.projectId !== projectId || result.analysisOnly !== true
        || !Array.isArray(result.pods) || !Array.isArray(result.materialRequests) || !Array.isArray(result.publicLookups)) {
        throw new Error("Preflight contract mismatch");
      }
      if (!controller.signal.aborted) setPreflight(result);
    } catch {
      if (!controller.signal.aborted) setPreflightError(true);
    } finally {
      if (preflightRequest.current === controller) {
        preflightRequest.current = null;
        setPreflightBusy(false);
      }
    }
  }

  function refreshPlan() {
    resetPreflight();
    onRefresh();
  }

  function startAnalysis() {
    resetPreflight();
    onRun();
  }

  const missing = preflight?.materialRequests ?? (plan ? openInputs(plan) : []);
  const groups = taskGroups(missing);
  const publicLookups = new Set((plan?.taskGraph?.missingInputWorkPlan ?? [])
    .filter((task) => task.action === "collect_public_evidence")
    .map((task) => task.itemId));
  const pods = new Set<PodKey>();
  const order = plan?.taskGraph?.recommendedRunOrder;
  if (order?.length) {
    for (const key of order) {
      if (Object.hasOwn(podLabels, key)) pods.add(key as PodKey);
    }
  }
  if (!pods.size) {
    for (const row of [...(plan?.agentPods ?? []), ...(plan?.podRuns ?? [])]) {
      if (Object.hasOwn(podLabels, row.podKey)) pods.add(row.podKey);
    }
  }
  const runs = new Map(plan?.podRuns?.map((row) => [row.podKey, row]));
  const summaryRows = new Map(plan?.runAllSummary?.rows?.map((row) => [row.podKey, row]));
  const phases = [...pods].map((key) => {
    const related = missing.filter((item) => item.requiredFor?.some((target) => target === key || target === "all_pods"));
    const run = runs.get(key)?.status;
    const result = summaryRows.get(key)?.status;
    const state = result === "blocked" || result === "failed" || result === "needs_recalculation"
      ? result : run ?? result ?? "planned";
    const blocked = related.some((item) => item.severity === "blocking");
    return {
      key,
      related,
      state: blocked && state === "planned" ? "blocked" as const : state,
    };
  });
  const completed = phases.filter((phase) => phase.state === "completed").length;
  const blockedCount = phases.filter((phase) => phase.state === "blocked").length;
  const executionFailed = phases.some((phase) => phase.state === "failed");
  const activeRun = executing || phases.some((phase) => phase.state === "running");
  const next = groups[0];
  const hardGate = missing.some((item) => item.severity === "blocking" && item.requiredFor?.includes("all_pods"));
  const canRun = Boolean(plan) && canAnalyze && !hardGate && !activeRun && !loading && !error && !preflightBusy && (Boolean(preflight) || preflightError);
  const selectedPhase = phases.find(({ key }) => key === selected);
  const track = plan?.taskGraph?.workflowTrack;

  const title = loading ? "프로젝트 상태 확인 중"
    : !plan ? "검토 상태를 불러올 수 없습니다"
    : preflightBusy ? "분석에 쓸 근거를 확인하고 있습니다"
    : activeRun ? "분석을 진행하고 있습니다"
    : blockedCount > 0 || hardGate ? (completed ? "완료 결과와 보완 자료를 확인하세요" : "분석 전에 자료를 보완하세요")
    : executionFailed ? "다시 확인할 검토가 있습니다"
    : completed ? "검토 결과를 확인하세요"
    : "사업 분석을 시작하세요";

  const description = loading ? "저장된 자료와 분석 이력을 확인하고 있습니다."
    : !plan ? "잠시 후 다시 확인하거나 프로젝트 자료를 먼저 열어보세요."
    : preflightBusy ? "확인한 근거를 각 검토에 전달하고, 남은 자료 요청을 정리합니다."
    : activeRun ? "결과가 도착하면 아래 검토 현황에 반영됩니다."
    : next ? "아래에서 필요한 자료와 각 검토의 진행 상태를 확인할 수 있습니다."
    : completed ? "완료한 검토와 승인 준비 상태를 확인할 수 있습니다."
    : "저장된 프로젝트 자료로 검토를 시작할 수 있습니다.";

  return (
    <div className="mx-auto max-w-6xl space-y-6 pb-8 sm:space-y-8" aria-busy={loading || executing || preflightBusy}>
      <section className={styles.hero} aria-labelledby="deal-room-title">
        <DashboardAtmosphere className={styles.atmosphere} testId="deal-room-parallax" strength={0.16} />
        <div className="relative grid min-w-0 gap-6 p-5 sm:p-8 lg:grid-cols-[minmax(0,1fr)_minmax(15rem,19rem)] lg:items-center lg:gap-8 lg:p-10">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-cyan-100">
              <Sparkles className="h-4 w-4 shrink-0" aria-hidden="true" />
              <h1 id="deal-room-title">AI 딜룸</h1>
              {track ? <span className="rounded-full border border-white/25 bg-white/10 px-2.5 py-0.5 text-xs text-white">{TRACK_LABELS[track]}</span> : null}
            </div>
            <h2 className="mt-4 max-w-2xl break-keep text-2xl font-bold leading-tight tracking-tight text-white sm:text-4xl">{title}</h2>
            <p className="mt-3 max-w-xl text-sm leading-6 text-slate-200 sm:text-base">{description}</p>
            <div className="mt-5 flex flex-wrap items-center gap-3">
              {loading || activeRun || preflightBusy ? (
                <span role="status" className="inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-white">
                  <Loader2 className="h-4 w-4 motion-safe:animate-spin" aria-hidden="true" />
                  {loading ? "검토 상태 확인 중" : preflightBusy ? "사전 검토 중" : "분석 중"}
                </span>
              ) : plan && canAnalyze && !hardGate && !preflight && !preflightError && !completed ? (
                <button type="button" onClick={() => void startPreflight()} className={primaryButton}>사전 검토 실행 <ArrowRight className="h-4 w-4" aria-hidden="true" /></button>
              ) : next ? (
                <ProjectWorkspaceLink projectId={projectId} path={next.path} className={primaryButton}>
                  {next.action}<ArrowRight className="h-4 w-4" aria-hidden="true" />
                </ProjectWorkspaceLink>
              ) : canRun && completed === 0 && !executionFailed && blockedCount === 0 ? (
                <button type="button" onClick={startAnalysis} className={primaryButton}>분석 시작 <ArrowRight className="h-4 w-4" aria-hidden="true" /></button>
              ) : completed ? (
                <a href="#deal-review-progress" className={primaryButton}>검토 현황 보기 <ArrowRight className="h-4 w-4" aria-hidden="true" /></a>
              ) : (
                <button type="button" onClick={refreshPlan} className={primaryButton}>상태 다시 확인 <RefreshCw className="h-4 w-4" aria-hidden="true" /></button>
              )}
            </div>
          </div>
          <div className={styles.glass} data-testid="deal-room-glass">
            <p className="text-xs font-semibold text-cyan-100">검토 요약</p>
            <dl className="mt-3 grid grid-cols-2 gap-3">
              <div><dt className="text-xs text-slate-200">완료한 검토</dt><dd className="mt-1 text-2xl font-semibold text-white">{plan && phases.length ? completed + " / " + phases.length : "—"}</dd></div>
              <div><dt className="text-xs text-slate-200">확인·보완 항목</dt><dd className="mt-1 text-2xl font-semibold text-white">{plan ? missing.length + "건" : "—"}</dd></div>
            </dl>
            <p className="mt-3 border-t border-white/20 pt-3 text-sm text-slate-100">승인 준비 <span className="ml-1 font-semibold">{approvalSummary(readiness)}</span></p>
          </div>
        </div>
      </section>

      {error ? <p role="alert" className="rounded-xl border border-rose-200 bg-re-surface px-4 py-3 text-sm font-semibold text-re-danger">
        {plan ? "요청을 완료하지 못했습니다. 저장된 자료와 검토 상태를 다시 확인해 주세요." : "검토 상태를 확인하지 못했습니다. 상태 다시 확인을 눌러 주세요."}
      </p> : null}

      {preflight ? (
        <details className={styles.panel}>
          <summary className="cursor-pointer px-4 py-4 text-sm font-bold text-re-text sm:px-5">
            사전 검토 결과 · {preflight.pods.length}개 영역 · 제출·확인 항목 {preflight.materialRequests.length}건
          </summary>
          <div className="border-t border-re-border px-4 py-4 sm:px-5">
            <p className="mb-3 text-sm text-re-secondary">보관 자료와 조회 가능한 공개 근거를 확인했습니다. 정식 분석과 승인 준비는 아래 검토 현황에서 별도로 표시합니다.</p>
            <ul className="grid gap-2 md:grid-cols-2">
              {preflight.pods.map((pod) => {
                const meta = podLabels[pod.podKey];
                if (!meta) return null;
                return (
                  <li key={pod.podKey} className="rounded-xl border border-re-border bg-re-subtle px-3 py-2 text-sm">
                    <p className="font-bold text-re-text">{meta.title} <span className="ml-1 font-medium text-re-secondary">{preflightPodStatus[pod.status]}</span></p>
                    <ul className="mt-1 space-y-1 text-xs text-re-secondary">
                      {pod.checks.map((check) => <li key={check.key}>{check.label} · {preflightCheckStatus[check.status]}</li>)}
                    </ul>
                  </li>
                );
              })}
            </ul>
            {preflight.publicLookups.length ? <ul className="mt-3 space-y-1 text-xs text-re-secondary">
              {preflight.publicLookups.map((lookup) => <li key={lookup.key}>{lookup.label} · {lookup.message}</li>)}
            </ul> : null}
          </div>
        </details>
      ) : null}

      {preflightError ? <p role="alert" className="rounded-xl border border-re-border bg-re-surface px-4 py-3 text-sm text-re-secondary">
        사전 검토를 마치지 못했습니다. 다시 실행하거나 기존 자료 상태를 확인해 주세요.
      </p> : null}

      {plan && !loading ? (
        <div className="grid min-w-0 gap-5 lg:grid-cols-[minmax(0,1.12fr)_minmax(0,1fr)]">
          <section className={styles.panel} aria-labelledby="deal-next-actions">
            <header className="border-b border-re-border px-4 py-4 sm:px-5">
              <div className="flex items-center gap-2 text-re-action"><ClipboardList className="h-5 w-5" aria-hidden="true" /><h2 id="deal-next-actions" className="text-lg font-bold text-re-text">다음 할 일</h2></div>
              <p className="mt-1 text-sm text-re-secondary">{preflight ? "사전 검토 뒤 남은 확인·제출 항목입니다." : "현재 자료로 확인할 후보입니다. 사전 검토를 실행하면 남은 항목을 정리합니다."}</p>
            </header>
            {groups.length ? (
              <ol className="divide-y divide-re-border">
                {groups.map((group, index) => (
                  <li key={group.path} className="grid grid-cols-[auto_minmax(0,1fr)] gap-3 px-4 py-4 sm:px-5">
                    <span aria-hidden="true" className="grid h-8 w-8 place-items-center rounded-lg bg-re-subtle text-sm font-bold text-re-action">{index + 1}</span>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <h3 className="text-sm font-bold text-re-text">{group.title}</h3>
                        <ProjectWorkspaceLink projectId={projectId} path={group.path} className={secondaryButton}>{group.action}<ArrowRight className="h-4 w-4" aria-hidden="true" /></ProjectWorkspaceLink>
                      </div>
                      <details className={styles.accordion}>
                        <summary className="flex cursor-pointer list-none items-center gap-1 text-sm font-medium text-re-secondary">
                          필요한 자료 {group.items.length}건 <ChevronDown className="h-4 w-4 shrink-0" aria-hidden="true" />
                        </summary>
                        <ul className="mt-2 space-y-3 text-sm leading-5 text-re-text">
                          {group.items.map((item) => (
                            <li key={item.itemId}>
                              <p>{item.description}</p>
                              {item.purpose ? <p className="mt-0.5 text-xs text-re-secondary">사용 목적 · {item.purpose}</p> : null}
                              {!preflight && publicLookups.has(item.itemId) ? <p className="mt-0.5 text-xs font-semibold text-re-action">공개자료 확인 대상</p> : null}
                              {item.acceptableSources?.length ? <p className="mt-0.5 text-xs text-re-secondary">예시 자료 · {item.acceptableSources.join(" · ")}</p> : null}
                            </li>
                          ))}
                        </ul>
                      </details>
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="px-4 py-5 text-sm leading-6 text-re-secondary sm:px-5">
                {blockedCount ? "중단된 검토가 있습니다. 각 검토에서 상태를 확인한 뒤 다시 시도하세요." : "현재 등록된 보완 자료 요청이 없습니다."}
              </p>
            )}
            <footer className="flex flex-wrap gap-2 border-t border-re-border px-4 py-3 sm:px-5">
              <button type="button" onClick={refreshPlan} disabled={activeRun || preflightBusy} className={secondaryButton}><RefreshCw className="h-4 w-4" aria-hidden="true" />자료 반영 확인</button>
              {canAnalyze && (hardGate || preflight || preflightError || completed > 0) ? (
                <button type="button" onClick={() => void startPreflight()} disabled={activeRun || preflightBusy} className={secondaryButton}>
                  <ClipboardList className="h-4 w-4" aria-hidden="true" />{preflight || preflightError ? "사전 검토 다시 실행" : "사전 검토 실행"}
                </button>
              ) : null}
              {canRun && (next || completed > 0 || executionFailed || blockedCount > 0) ? (
                <button type="button" onClick={startAnalysis} className={secondaryButton}>
                  <Sparkles className="h-4 w-4" aria-hidden="true" />{completed || executionFailed || blockedCount ? "분석 다시 실행" : "분석 시작"}
                </button>
              ) : null}
              {!canAnalyze ? <span className="self-center text-xs text-re-secondary">현재 계정은 검토 결과를 조회할 수 있습니다.</span> : null}
            </footer>
          </section>

          <section id="deal-review-progress" className={styles.panel} aria-labelledby="deal-review-title">
            <header className="border-b border-re-border px-4 py-4 sm:px-5">
              <div className="flex items-center gap-2 text-re-action"><CheckCircle2 className="h-5 w-5" aria-hidden="true" /><h2 id="deal-review-title" className="text-lg font-bold text-re-text">검토 현황</h2></div>
              <p className="mt-1 text-sm text-re-secondary">항목을 선택하면 필요한 자료와 관련 화면을 볼 수 있습니다.</p>
            </header>
            {phases.length ? (
              <ul className="grid min-w-0 gap-2 p-4 sm:grid-cols-2 sm:p-5">
                {phases.map((phase) => {
                  const meta = podLabels[phase.key];
                  const Icon = meta.icon;
                  return (
                    <li key={phase.key} className="min-w-0">
                      <button type="button" onClick={() => { setSelected(phase.key); details.current?.showModal(); }}
                        className={styles.reviewCard} data-state={phase.state} aria-haspopup="dialog">
                        <Icon className="h-5 w-5 shrink-0 text-re-action" aria-hidden="true" />
                        <span className="min-w-0 flex-1">
                          <span className="block break-keep text-sm font-semibold text-re-text">{meta.title}</span>
                          <span className={styles.reviewStatus}>{stateLabels[phase.state] ?? "상태 확인 필요"}</span>
                        </span>
                        <ArrowRight className="h-4 w-4 shrink-0 text-re-muted" aria-hidden="true" />
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : <p className="px-4 py-5 text-sm text-re-secondary sm:px-5">표시할 검토 항목이 없습니다.</p>}
            <footer className="flex flex-wrap gap-2 border-t border-re-border px-4 py-3 sm:px-5">
              <ProjectWorkspaceLink projectId={projectId} path="risk" className={secondaryButton}><ShieldCheck className="h-4 w-4" aria-hidden="true" />리스크·승인</ProjectWorkspaceLink>
              {completed > 0 ? <ProjectWorkspaceLink projectId={projectId} path="report" className={secondaryButton}><FileText className="h-4 w-4" aria-hidden="true" />보고서 보기</ProjectWorkspaceLink> : null}
            </footer>
          </section>
        </div>
      ) : null}

      <dialog ref={details} aria-labelledby={detailsTitleId} className={styles.drawer}>
        {selectedPhase ? (
          <div className="p-5 sm:p-7">
            <header className="flex items-start justify-between gap-3">
              <div><h2 id={detailsTitleId} className="text-xl font-bold text-re-text">{podLabels[selectedPhase.key].title}</h2>
                <p className="mt-1 text-sm text-re-secondary">{podLabels[selectedPhase.key].description}</p></div>
              <form method="dialog"><button autoFocus type="submit" aria-label="검토 상세 닫기" className={secondaryButton}><X className="h-4 w-4" aria-hidden="true" /></button></form>
            </header>
            <p className="mt-5 rounded-lg bg-re-subtle px-3 py-2 text-sm font-semibold text-re-text">{stateLabels[selectedPhase.state] ?? "상태 확인 필요"}</p>
            {selectedPhase.related.length ? (
              <ul className="mt-4 list-disc space-y-2 pl-5 text-sm leading-6 text-re-text">
                {selectedPhase.related.map((item) => <li key={item.itemId}>{item.description}</li>)}
              </ul>
            ) : (
              <p className="mt-4 text-sm leading-6 text-re-secondary">
                {selectedPhase.state === "blocked" ? "이 검토의 선행 작업을 확인하세요." : "관련 화면에서 저장 자료와 결과를 확인할 수 있습니다."}
              </p>
            )}
            <ProjectWorkspaceLink projectId={projectId} path={podLabels[selectedPhase.key].path} className={secondaryButton + " mt-5"}>
              관련 화면 열기 <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </ProjectWorkspaceLink>
          </div>
        ) : null}
      </dialog>
    </div>
  );
}
