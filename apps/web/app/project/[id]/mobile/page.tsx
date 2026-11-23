import Link from "next/link";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  FileSearch,
  GitBranch,
  MapPin,
  ShieldCheck,
  Upload,
} from "lucide-react";
import {
  financeTerm,
  readinessLabel,
  severityLabel,
  translateFinancialTerm,
} from "../../../../lib/financial-terms";
import { fetchAuthenticatedBackend } from "../../../../lib/authenticated-backend-fetch";

interface ProjectDetailResponse {
  project?: {
    name?: string;
    status?: string;
  };
  land?: {
    address?: string;
  };
}

interface RunSummary {
  run_id: string;
  workflow?: string;
  status?: string;
  started_at?: string;
}

interface ApprovalReadinessGate {
  pass?: boolean;
  status?: string;
  summary?: {
    blockingIssueCount?: number;
    warningCount?: number;
    unresolvedHandoffReviewCount?: number;
    unresolvedRiskReviewCount?: number;
    creditDecision?: string;
    landEvidenceReady?: boolean;
  };
  blockers?: Array<{ code?: string; message?: string; severity?: string }>;
  warnings?: Array<{ code?: string; message?: string; severity?: string }>;
  requiredActions?: string[];
}

interface OrchestrationRun {
  run_id: string;
  status?: string;
  started_at?: string;
  handoffs?: Array<{
    reviewStatus?: string;
    requiresHumanReview?: boolean;
    status?: string;
    handoffType?: string;
    fromAgent?: string;
    toAgent?: string;
  }>;
  artifacts?: {
    approvalReadinessGate?: ApprovalReadinessGate;
    creditReviewDecision?: {
      decision?: string;
      decisionLabel?: string;
      decisionReasons?: string[];
      approvalConditions?: string[];
      requiredFollowUps?: string[];
    };
    riskRegister?: {
      status?: string;
      summary?: {
        riskCount?: number;
        criticalCount?: number;
        highCount?: number;
        humanReviewRequiredCount?: number;
      };
      risks?: Array<{
        riskId?: string;
        code?: string;
        title?: string;
        severity?: string;
        reviewStatus?: string;
        status?: string;
        requiresHumanReview?: boolean;
      }>;
    };
    returnMetrics?: {
      metrics?: {
        projectIrr?: { value?: number | null };
        equityMultiple?: { value?: number | null };
        icr?: { value?: number | null };
      };
    };
    stressTest?: {
      scenarios?: Array<{ key?: string; projectIrr?: number | null }>;
      seniorBreakScenarioKey?: string | null;
    };
    creditEnhancement?: { enhancementGrade?: string };
    developabilityScore?: { overallScore?: number | null; grade?: string };
  };
}

interface EngineKpi {
  label: string;
  value: string;
}

interface MobileAction {
  title: string;
  body: string;
  href: string;
  tone: "critical" | "warning" | "ready";
  icon: "upload" | "risk" | "land" | "review";
}

type BackendReadFailure = {
  state: "login_required" | "forbidden" | "authority_unavailable" | "not_found" | "request_failed";
  status?: number;
};

type BackendReadResult<T> =
  | { state: "ready"; data: T }
  | BackendReadFailure;

function backendReadFailure(status: number): BackendReadFailure {
  if (status === 401) return { state: "login_required", status };
  if (status === 403) return { state: "forbidden", status };
  if (status === 404) return { state: "not_found", status };
  if (status === 503 || status >= 500) {
    return { state: "authority_unavailable", status };
  }
  return { state: "request_failed", status };
}

async function readBackendJson<T>(
  path: string,
  decode: (value: unknown) => T | undefined,
): Promise<BackendReadResult<T>> {
  try {
    const res = await fetchAuthenticatedBackend(path);
    if (!res.ok) return backendReadFailure(res.status);
    const decoded = decode(await res.json());
    return decoded === undefined
      ? { state: "authority_unavailable", status: 502 }
      : { state: "ready", data: decoded };
  } catch {
    return { state: "authority_unavailable" };
  }
}

function decodeProjectDetail(value: unknown): ProjectDetailResponse | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as ProjectDetailResponse
    : undefined;
}

function decodeRunSummaries(value: unknown): RunSummary[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.every((item) => (
    item
    && typeof item === "object"
    && !Array.isArray(item)
    && typeof (item as { run_id?: unknown }).run_id === "string"
  ))
    ? value as RunSummary[]
    : undefined;
}

function decodeOrchestrationRun(value: unknown): OrchestrationRun | undefined {
  return value
    && typeof value === "object"
    && !Array.isArray(value)
    && typeof (value as { run_id?: unknown }).run_id === "string"
    ? value as OrchestrationRun
    : undefined;
}

function MobileBackendState({
  failure,
  currentPath,
}: {
  failure: BackendReadFailure;
  currentPath: string;
}) {
  if (failure.state === "login_required") {
    const query = new URLSearchParams({ next: currentPath });
    redirect(`/login?${query.toString()}`);
  }

  const content = failure.state === "forbidden"
    ? {
      eyebrow: "접근 권한 제한",
      title: "이 프로젝트의 모바일 심사 정보를 볼 수 없습니다.",
      body: "현재 Tenant 멤버십과 프로젝트 열람 권한을 확인해 주세요. 권한 변경이 필요하면 조직 관리자에게 요청하세요.",
      tone: "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-100",
    }
    : failure.state === "not_found"
      ? {
        eyebrow: "프로젝트 확인 필요",
        title: "요청한 프로젝트를 찾을 수 없습니다.",
        body: "프로젝트가 삭제되었거나 현재 Tenant에 존재하지 않을 수 있습니다. 프로젝트 목록에서 다시 선택해 주세요.",
        tone: "border-slate-200 bg-white text-slate-900 dark:border-slate-800 dark:bg-slate-900 dark:text-white",
      }
      : failure.state === "authority_unavailable"
        ? {
          eyebrow: "권위 저장소 연결 장애",
          title: "프로젝트 심사 정보를 불러오지 못했습니다.",
          body: "인증 또는 PostgreSQL 업무 권위에 일시적으로 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
          tone: "border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-100",
        }
        : {
          eyebrow: "조회 실패",
          title: "프로젝트 응답을 확인할 수 없습니다.",
          body: "요청이 정상 처리되지 않았습니다. 계속되면 관리자에게 요청 상태를 알려 주세요.",
          tone: "border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-100",
        };

  return (
    <main className="mx-auto max-w-2xl px-4 py-10">
      <section className={`rounded-3xl border p-6 shadow-sm ${content.tone}`} role="alert">
        <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-white/80 text-slate-700 shadow-sm dark:bg-slate-950/40 dark:text-slate-200">
          {failure.state === "forbidden" ? (
            <ShieldCheck className="h-5 w-5" />
          ) : (
            <AlertCircle className="h-5 w-5" />
          )}
        </div>
        <p className="mt-5 text-xs font-black uppercase tracking-[0.18em] opacity-70">{content.eyebrow}</p>
        <h1 className="mt-2 text-2xl font-black leading-tight">{content.title}</h1>
        <p className="mt-3 text-sm font-semibold leading-6 opacity-80">{content.body}</p>
        <div className="mt-6 flex flex-wrap gap-3">
          {failure.state === "authority_unavailable" || failure.state === "request_failed" ? (
            <Link href={currentPath} className="rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-black text-white dark:bg-white dark:text-slate-950">
              다시 시도
            </Link>
          ) : null}
          <Link href="/dashboard" className="rounded-xl border border-current/20 bg-white/60 px-4 py-2.5 text-sm font-black dark:bg-slate-950/30">
            프로젝트 목록
          </Link>
        </div>
      </section>
    </main>
  );
}

function riskNeedsReview(risk: { reviewStatus?: string; status?: string; requiresHumanReview?: boolean }) {
  const reviewStatus = risk.reviewStatus ?? risk.status ?? "open";
  return risk.requiresHumanReview && !["approved", "waived", "resolved"].includes(reviewStatus);
}

function handoffNeedsReview(handoff: { reviewStatus?: string; requiresHumanReview?: boolean; status?: string }) {
  return (
    handoff.reviewStatus === "needs_revision"
    || handoff.reviewStatus === "rejected"
    || (handoff.requiresHumanReview && handoff.reviewStatus !== "approved")
    || ((handoff.status === "blocked" || handoff.status === "needs_review") && handoff.reviewStatus !== "approved")
  );
}

function buildActions(projectId: string, run: OrchestrationRun | null): MobileAction[] {
  if (!run) {
    return [
      {
        title: "사업자료 패키지 등록",
        body: "사업계획서, 사업수지 Excel, 토지조서, 공시지가·등기 증빙을 먼저 업로드하세요.",
        href: `/project/${projectId}/input-package`,
        tone: "warning",
        icon: "upload",
      },
    ];
  }

  const gate = run.artifacts?.approvalReadinessGate;
  const credit = run.artifacts?.creditReviewDecision;
  const risks = run.artifacts?.riskRegister?.risks ?? [];
  const unresolvedRisks = risks.filter(riskNeedsReview);
  const unresolvedHandoffs = (run.handoffs ?? []).filter(handoffNeedsReview);
  const blockers = gate?.blockers ?? [];
  const landBlocked = gate?.summary?.landEvidenceReady === false
    || blockers.some((item) => (item.code ?? "").startsWith("LAND_"));
  const creditBlocked = credit?.decision === "reject_recommended" || credit?.decision === "rework_required";

  const actions: MobileAction[] = [];
  if (landBlocked) {
    actions.push({
      title: "토지권원·증빙 보완",
      body: "PNU, 개별공시지가, 지적특성, 등기부 권리관계, 토지취득률 증빙을 보완해야 합니다.",
      href: `/project/${projectId}/input-package`,
      tone: "critical",
      icon: "land",
    });
  }
  if (creditBlocked) {
    actions.push({
      title: "금융구조 보강안 검토",
      body: "선순위 부족, 미분양 LTV 초과, Exit 분양률 부담이 있으면 감액, Equity/DSRA, 담보 보강, 상환계좌 배분 강화를 검토하세요.",
      href: `/project/${projectId}/orchestration`,
      tone: "critical",
      icon: "review",
    });
  }
  if (unresolvedRisks.length > 0) {
    actions.push({
      title: "리스크 검토상태 확정",
      body: `${unresolvedRisks.length}개 리스크를 승인, 유보 승인, 보완 요청, 해소 중 하나로 정리해야 합니다.`,
      href: `/project/${projectId}/orchestration`,
      tone: "warning",
      icon: "risk",
    });
  }
  if (unresolvedHandoffs.length > 0) {
    actions.push({
      title: "에이전트 인계사항 검토",
      body: `${unresolvedHandoffs.length}개 인계사항이 다음 에이전트 또는 최종 심사 전 검토를 기다립니다.`,
      href: `/project/${projectId}/orchestration`,
      tone: "warning",
      icon: "review",
    });
  }
  if (actions.length === 0) {
    actions.push({
      title: gate?.pass ? "승인 검토 가능" : "보완 권고 확인",
      body: gate?.pass
        ? "현재 기준으로 승인 가능성 점검이 통과되었습니다. 보고서와 근거 연결도를 최종 확인하세요."
        : "차단 항목은 없지만 일부 보완 권고가 남아 있습니다.",
      href: gate?.pass ? `/project/${projectId}/report` : `/project/${projectId}/orchestration`,
      tone: gate?.pass ? "ready" : "warning",
      icon: "review",
    });
  }
  return actions;
}

function fmtPct(value?: number | null) {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
}

function buildEngineKpis(run: OrchestrationRun | null): EngineKpi[] {
  const artifacts = run?.artifacts;
  if (!artifacts) return [];
  const rm = artifacts.returnMetrics?.metrics;
  const stress = artifacts.stressTest;
  const ce = artifacts.creditEnhancement;
  const baseIrr = stress?.scenarios?.find((s) => s.key === "base")?.projectIrr ?? rm?.projectIrr?.value;
  const kpis: EngineKpi[] = [];
  if (typeof baseIrr === "number") kpis.push({ label: "Project IRR", value: fmtPct(baseIrr) });
  if (typeof rm?.equityMultiple?.value === "number") kpis.push({ label: "Equity Multiple", value: `${rm.equityMultiple.value.toFixed(2)}x` });
  if (typeof rm?.icr?.value === "number") kpis.push({ label: "ICR", value: `${rm.icr.value.toFixed(2)}x` });
  if (ce?.enhancementGrade) kpis.push({ label: "신용보강", value: `${ce.enhancementGrade}등급` });
  const dev = artifacts.developabilityScore;
  if (dev?.grade) {
    kpis.push({ label: "개발가능성", value: typeof dev.overallScore === "number" ? `${dev.overallScore.toFixed(0)}점 ${dev.grade}` : `${dev.grade}등급` });
  }
  if (stress?.scenarios?.length) {
    kpis.push({ label: "Stress", value: stress.seniorBreakScenarioKey ? "Senior 손실가능" : "Senior 보존" });
  }
  return kpis;
}

function iconFor(action: MobileAction) {
  const cls = "h-5 w-5";
  if (action.icon === "upload") return <Upload className={cls} />;
  if (action.icon === "land") return <MapPin className={cls} />;
  if (action.icon === "risk") return <ShieldCheck className={cls} />;
  return <GitBranch className={cls} />;
}

function toneClass(tone: "critical" | "warning" | "ready") {
  if (tone === "ready") return "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-200";
  if (tone === "critical") return "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-200";
  return "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200";
}

function fmtDate(value?: string) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export default async function ProjectMobileActionPage({ params }: { params: Promise<{ id: string }> | { id: string } }) {
  const resolvedParams = await params;
  const projectId = resolvedParams.id;
  const encodedProjectId = encodeURIComponent(projectId);
  const currentPath = `/project/${encodedProjectId}/mobile`;
  const projectResult = await readBackendJson(
    `/projects/${encodedProjectId}`,
    decodeProjectDetail,
  );
  if (projectResult.state !== "ready") {
    return <MobileBackendState failure={projectResult} currentPath={currentPath} />;
  }

  const runListResult = await readBackendJson(
    `/projects/${encodedProjectId}/orchestrations`,
    decodeRunSummaries,
  );
  if (runListResult.state !== "ready") {
    return <MobileBackendState failure={runListResult} currentPath={currentPath} />;
  }

  const data = projectResult.data;
  const runs = runListResult.data;
  const latestRunSummary = [...runs].sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? ""))[0];
  let latestRun: OrchestrationRun | null = null;
  if (latestRunSummary) {
    const latestRunResult = await readBackendJson(
      `/projects/${encodedProjectId}/orchestrations/${encodeURIComponent(latestRunSummary.run_id)}`,
      decodeOrchestrationRun,
    );
    if (latestRunResult.state !== "ready") {
      return <MobileBackendState failure={latestRunResult} currentPath={currentPath} />;
    }
    latestRun = latestRunResult.data;
  }

  const gate = latestRun?.artifacts?.approvalReadinessGate;
  const credit = latestRun?.artifacts?.creditReviewDecision;
  const riskRegister = latestRun?.artifacts?.riskRegister;
  const risks = riskRegister?.risks ?? [];
  const unresolvedRiskCount = risks.filter(riskNeedsReview).length;
  const unresolvedHandoffCount = (latestRun?.handoffs ?? []).filter(handoffNeedsReview).length;
  const actions = buildActions(projectId, latestRun);
  const topBlockers = gate?.blockers?.slice(0, 3) ?? [];
  const engineKpis = buildEngineKpis(latestRun);

  return (
    <main className="mx-auto max-w-2xl space-y-4 pb-20">
      <section className="rounded-b-3xl bg-slate-950 px-5 pb-6 pt-5 text-white shadow-sm dark:bg-slate-900">
        <p className="text-xs font-bold text-slate-400">카카오 알림 액션센터</p>
        <h1 className="mt-2 text-2xl font-black leading-tight">
          {data.project?.name || "사업명 미입력"}
        </h1>
        <p className="mt-1 text-sm leading-6 text-slate-300">{data.land?.address ?? "주소 미입력"}</p>
        <div className="mt-4 rounded-2xl bg-white/10 p-4">
          <p className="text-xs font-semibold text-slate-300">{financeTerm("approvalReadiness")}</p>
          <div className="mt-2 flex items-center justify-between gap-3">
            <div>
              <p className="text-xl font-black">{readinessLabel(gate?.pass)}</p>
              <p className="mt-1 text-xs text-slate-300">
                최근 실행 {latestRun ? fmtDate(latestRun.started_at) : "없음"}
              </p>
            </div>
            {gate?.pass ? (
              <CheckCircle2 className="h-9 w-9 text-emerald-300" />
            ) : (
              <AlertCircle className="h-9 w-9 text-amber-300" />
            )}
          </div>
        </div>
      </section>

      <section className="grid grid-cols-2 gap-3 px-4">
        <MiniStat label="차단항목" value={`${gate?.summary?.blockingIssueCount ?? 0}`} />
        <MiniStat label="리스크 미검토" value={`${unresolvedRiskCount}`} />
        <MiniStat label="인계 미검토" value={`${unresolvedHandoffCount}`} />
        <MiniStat label="여신심사 의견" value={translateFinancialTerm(credit?.decisionLabel ?? credit?.decision ?? "-")} />
      </section>

      {engineKpis.length > 0 ? (
        <section className="px-4">
          <p className="mb-2 text-sm font-black text-slate-950 dark:text-white">핵심 지표</p>
          <div className="grid grid-cols-2 gap-3">
            {engineKpis.map((kpi) => (
              <MiniStat key={kpi.label} label={kpi.label} value={kpi.value} />
            ))}
          </div>
        </section>
      ) : null}

      <section className="space-y-3 px-4">
        <div>
          <p className="text-sm font-black text-slate-950 dark:text-white">지금 해야 할 일</p>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            카카오톡 알림은 민감정보를 보내지 않습니다. 상세 자료와 승인 판단은 REALYTICS 로그인 후 확인합니다.
          </p>
        </div>
        {actions.map((action) => (
          <Link
            key={`${action.title}-${action.href}`}
            href={action.href}
            className={`block rounded-2xl border p-4 shadow-sm ${toneClass(action.tone)}`}
          >
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/80 dark:bg-slate-950/40">
                {iconFor(action)}
              </div>
              <div className="min-w-0 flex-1">
                <p className="font-black">{action.title}</p>
                <p className="mt-1 text-sm font-semibold leading-6 opacity-85">{action.body}</p>
              </div>
              <ArrowRight className="mt-1 h-5 w-5 shrink-0" />
            </div>
          </Link>
        ))}
      </section>

      {topBlockers.length > 0 ? (
        <section className="space-y-3 px-4">
          <p className="text-sm font-black text-slate-950 dark:text-white">주요 차단 사유</p>
          {topBlockers.map((blocker, index) => (
            <div
              key={`${blocker.code ?? "blocker"}-${index}`}
              className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900"
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-black text-slate-950 dark:text-white">{blocker.code ?? "BLOCKER"}</p>
                <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                  {severityLabel(blocker.severity)}
                </span>
              </div>
              <p className="mt-2 text-sm font-semibold leading-6 text-slate-600 dark:text-slate-300">
                {blocker.message ?? "상세 메시지 없음"}
              </p>
            </div>
          ))}
        </section>
      ) : null}

      <section className="space-y-3 px-4">
        <p className="text-sm font-black text-slate-950 dark:text-white">빠른 이동</p>
        <div className="grid gap-3">
          <QuickLink href={`/project/${projectId}/input-package`} icon={<Upload className="h-4 w-4" />} label={financeTerm("inputPackage")} />
          <QuickLink href={`/project/${projectId}/orchestration`} icon={<GitBranch className="h-4 w-4" />} label={financeTerm("orchestration")} />
          <QuickLink href={`/project/${projectId}/evidence`} icon={<FileSearch className="h-4 w-4" />} label={financeTerm("evidenceGraph")} />
        </div>
      </section>

      <section className="px-4">
        <div className="rounded-2xl bg-slate-100 p-4 text-xs font-semibold leading-5 text-slate-600 dark:bg-slate-900 dark:text-slate-300">
          카카오톡에는 검토 필요 여부와 REALYTICS 링크만 표시합니다. PF 금액, 계좌, 소유자, PNU, 등기·제한물권,
          법률의견 세부내용은 메시지 본문에 포함하지 않습니다.
        </div>
      </section>
    </main>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-xs font-bold text-slate-500">{label}</p>
      <p className="mt-2 truncate text-lg font-black text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function QuickLink({ href, icon, label }: { href: string; icon: ReactNode; label: string }) {
  return (
    <Link
      href={href}
      className="flex items-center justify-between rounded-2xl border border-slate-200 bg-white p-4 text-sm font-black text-slate-950 shadow-sm dark:border-slate-800 dark:bg-slate-900 dark:text-white"
    >
      <span className="flex items-center gap-2">
        {icon}
        {label}
      </span>
      <ArrowRight className="h-4 w-4 text-slate-400" />
    </Link>
  );
}
