import Link from "next/link";
import type { ReactNode } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  Clock3,
  FileSearch,
  FileText,
  GitBranch,
  Layers,
  MapPin,
  ShieldCheck,
  Upload,
} from "lucide-react";
import FileUpload from "../../components/FileUpload";
import { IntentPlanMenu } from "../../../components/orchestration/intent-plan-menu";
import { CovenantMonitorPanel } from "../../../components/monitoring/covenant-monitor-panel";
import { financeTerm, readinessLabel, translateFinancialTerm } from "../../../lib/financial-terms";

interface PfTranche {
  amount?: number;
}

interface ProjectDetailResponse {
  project?: {
    name?: string;
    status?: string;
  };
  land?: {
    address?: string;
  };
  feasibility_input?: {
    total_revenue?: number;
    total_cost?: number;
  };
  pf_structure?: PfTranche[];
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
    unresolvedHandoffReviewCount?: number;
    unresolvedRiskReviewCount?: number;
    creditDecision?: string;
    landEvidenceReady?: boolean;
  };
  blockers?: Array<{ code?: string; message?: string; severity?: string }>;
  requiredActions?: string[];
}

interface OrchestrationRun {
  run_id: string;
  status?: string;
  started_at?: string;
  handoffs?: Array<{ reviewStatus?: string; requiresHumanReview?: boolean; status?: string }>;
  artifacts?: {
    approvalReadinessGate?: ApprovalReadinessGate;
    creditReviewDecision?: {
      decision?: string;
      decisionLabel?: string;
      decisionReasons?: string[];
      requiredFollowUps?: string[];
    };
    riskRegister?: {
      risks?: Array<{
        reviewStatus?: string;
        status?: string;
        requiresHumanReview?: boolean;
      }>;
    };
    returnMetrics?: {
      metrics?: {
        projectIrr?: { value?: number | null };
        equityIrr?: { value?: number | null };
        equityMultiple?: { value?: number | null };
        icr?: { value?: number | null };
        paybackPeriodMonths?: { value?: number | null };
      };
    };
    stressTest?: {
      scenarios?: Array<{ key?: string; projectIrr?: number | null; seniorStatus?: string; statusLabel?: string }>;
      worstCaseKey?: string | null;
      seniorBreakScenarioKey?: string | null;
    };
    creditEnhancement?: {
      enhancementGrade?: string;
      baseSeniorLtv?: number;
      adjustedSeniorLtv?: number;
      activeCount?: number;
    };
    developabilityScore?: {
      overallScore?: number | null;
      grade?: string;
    };
  };
}

interface EngineKpi {
  label: string;
  value: string;
}

interface ProjectDiagnosis {
  statusLabel: string;
  statusTone: "good" | "warn" | "bad";
  summary: string;
  primaryAction: { href: string; label: string };
  updateAreas: Array<{
    title: string;
    body: string;
    href: string;
    severity: "good" | "warn" | "bad";
  }>;
  nextSteps: string[];
}

const apiBase = "http://127.0.0.1:8000";

function fmtEok(value?: number) {
  if (!value || Number.isNaN(value)) return "-";
  const eok = value / 100_000_000;
  return `${eok.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}억원`;
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
  const credit = artifacts.creditReviewDecision;
  const baseIrr = stress?.scenarios?.find((s) => s.key === "base")?.projectIrr ?? rm?.projectIrr?.value;

  const kpis: EngineKpi[] = [];
  if (typeof baseIrr === "number") kpis.push({ label: "Project IRR", value: fmtPct(baseIrr) });
  if (typeof rm?.equityMultiple?.value === "number") kpis.push({ label: "Equity Multiple", value: `${rm.equityMultiple.value.toFixed(2)}x` });
  if (typeof rm?.icr?.value === "number") kpis.push({ label: "ICR", value: `${rm.icr.value.toFixed(2)}x` });
  if (ce?.enhancementGrade) kpis.push({ label: "신용보강", value: `${ce.enhancementGrade}등급` });
  const dev = artifacts.developabilityScore;
  if (dev?.grade) {
    kpis.push({
      label: "개발가능성",
      value: typeof dev.overallScore === "number" ? `${dev.overallScore.toFixed(0)}점 ${dev.grade}등급` : `${dev.grade}등급`,
    });
  }
  if (stress?.scenarios?.length) {
    kpis.push({ label: "Stress 결과", value: stress.seniorBreakScenarioKey ? `Senior 손실가능(${stress.seniorBreakScenarioKey})` : "Senior 보존" });
  }
  if (credit?.decisionLabel) kpis.push({ label: "여신 의견", value: credit.decisionLabel });
  return kpis;
}

async function safeJson<T>(url: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return fallback;
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

function riskNeedsReview(risk: { reviewStatus?: string; status?: string; requiresHumanReview?: boolean }) {
  const reviewStatus = risk.reviewStatus ?? risk.status ?? "open";
  return risk.requiresHumanReview && !["approved", "waived", "resolved"].includes(reviewStatus);
}

function buildDiagnosis(projectId: string, latestRun: OrchestrationRun | null): ProjectDiagnosis {
  if (!latestRun) {
    return {
      statusLabel: "사업자료 입력 필요",
      statusTone: "warn",
      summary:
        "아직 AI 통합심사가 실행되지 않았습니다. 사업계획서, 사업수지 Excel, 토지조서, 공시지가·등기부 증빙을 올리면 AI가 보완자료와 승인 가능성을 진단합니다.",
      primaryAction: { href: `/project/${projectId}/input-package`, label: "사업자료 입력하기" },
      updateAreas: [
        {
          title: financeTerm("inputPackage"),
          body: "사업계획서, 건축개요, 사업수지 Excel, 토지조서, 공시지가, 등기부 증빙을 하나의 파일 패키지로 등록하세요.",
          href: `/project/${projectId}/input-package`,
          severity: "warn",
        },
      ],
      nextSteps: [
        "사업계획서 또는 PF 검토용 자료를 업로드합니다.",
        "사업수지 Excel과 토지조서를 함께 올리면 숫자 충돌을 더 정확히 잡을 수 있습니다.",
        "자료 입력 후 AI 업무 주문서에서 실행할 분석을 선택합니다.",
      ],
    };
  }

  const gate = latestRun.artifacts?.approvalReadinessGate;
  const credit = latestRun.artifacts?.creditReviewDecision;
  const riskRegister = latestRun.artifacts?.riskRegister;
  const blockers = gate?.blockers ?? [];
  const unresolvedRiskCount = (riskRegister?.risks ?? []).filter(riskNeedsReview).length;
  const unresolvedHandoffCount = latestRun.handoffs?.filter((item) => (
    item.reviewStatus === "needs_revision"
    || item.reviewStatus === "rejected"
    || (item.requiresHumanReview && item.reviewStatus !== "approved")
    || ((item.status === "blocked" || item.status === "needs_review") && item.reviewStatus !== "approved")
  )).length ?? 0;

  const landBlocked = gate?.summary?.landEvidenceReady === false
    || blockers.some((item) => (item.code ?? "").startsWith("LAND_"));
  const creditRejected = credit?.decision === "reject_recommended" || credit?.decision === "rework_required";
  const updateAreas: ProjectDiagnosis["updateAreas"] = [];

  if (landBlocked) {
    updateAreas.push({
      title: financeTerm("landEvidence"),
      body: "PNU, 개별공시지가, 지적특성, 등기부 권리관계, 토지취득률 증빙이 부족합니다. 이 항목이 해소되어야 법무검토와 PF 승인 가능성 판단으로 넘어갈 수 있습니다.",
      href: `/project/${projectId}/input-package`,
      severity: "bad",
    });
  }

  if (creditRejected) {
    updateAreas.push({
      title: financeTerm("pfStructure"),
      body: "여신심사 의견이 부결 또는 재작업 권고입니다. 선순위 상환부족, 미분양 LTV 초과, Exit 분양률 부담이 있으면 PF 감액, Equity/DSRA 증액, 상환계좌 배분 강화, 추가담보 보강이 필요합니다.",
      href: `/project/${projectId}/orchestration`,
      severity: "bad",
    });
  }

  if (unresolvedRiskCount > 0) {
    updateAreas.push({
      title: financeTerm("riskRegister"),
      body: `${unresolvedRiskCount}개 주요 리스크가 승인, 유보 승인, 보완 요청, 해소 중 하나로 정리되지 않았습니다.`,
      href: `/project/${projectId}/orchestration`,
      severity: "warn",
    });
  }

  if (unresolvedHandoffCount > 0) {
    updateAreas.push({
      title: financeTerm("handoff"),
      body: `${unresolvedHandoffCount}개 Agent 이관사항이 미검토 상태입니다. 다음 Agent 또는 최종 심사로 넘기기 전에 검토 상태를 확정하세요.`,
      href: `/project/${projectId}/orchestration`,
      severity: "warn",
    });
  }

  if (updateAreas.length === 0) {
    updateAreas.push({
      title: financeTerm("approvalReadiness"),
      body: gate?.pass
        ? "현재 기준으로 승인 가능성 게이트를 통과했습니다. 보고서와 승인조건, 근거 연결도를 최종 확인하세요."
        : "차단 항목은 없지만 일부 보완 권고가 남아 있습니다. 승인조건과 보고서 문구를 확인하세요.",
      href: gate?.pass ? `/project/${projectId}/report` : `/project/${projectId}/orchestration`,
      severity: gate?.pass ? "good" : "warn",
    });
  }

  const requiredActions = (gate?.requiredActions ?? []).map(translateFinancialTerm);
  const followUps = credit?.requiredFollowUps ?? [];
  const nextSteps = requiredActions.length ? requiredActions.slice(0, 4) : followUps.slice(0, 4);

  return {
    statusLabel: gate?.pass
      ? readinessLabel(true)
      : creditRejected
        ? "금융구조 보완 필요"
        : landBlocked
          ? "토지 증빙 보완 필요"
          : "보완 및 재심사 필요",
    statusTone: gate?.pass ? "good" : creditRejected || landBlocked ? "bad" : "warn",
    summary: gate?.pass
      ? "AI 통합심사 기준으로 자료 정합성, 리스크 검토, 승인 가능성 평가가 통과되었습니다."
      : "AI가 현재 심사 기준본을 검토한 결과, 승인 전에 보완해야 할 항목이 남아 있습니다.",
    primaryAction: {
      href: landBlocked ? `/project/${projectId}/input-package` : `/project/${projectId}/orchestration`,
      label: landBlocked ? "증빙 보완하기" : "보완항목 확인하기",
    },
    updateAreas,
    nextSteps: nextSteps.length
      ? nextSteps
      : [
        "승인 가능성 화면에서 차단 항목을 확인합니다.",
        "필요 증빙과 리스크 검토 상태를 보완합니다.",
        "보완 후 AI 통합심사를 다시 실행합니다.",
      ],
  };
}

const workflowSteps = [
  {
    title: "1. 사업자료 업로드",
    body: "사업계획서, 건축개요, 사업수지 Excel, 토지조서를 파일 패키지로 올립니다.",
  },
  {
    title: "2. AI 업무 주문서",
    body: "AI가 가능한 업무, 추천 workflow, 부족자료, 검증조건을 먼저 정리합니다.",
  },
  {
    title: "3. 필요한 분석 실행",
    body: "토지권원, 사업수지, 분양성, PF 구조, Stress Test 중 필요한 분석만 실행합니다.",
  },
  {
    title: "4. 보완자료 확인",
    body: "숫자 충돌, 근거 부족, 승인 차단 리스크를 업무 항목으로 보여줍니다.",
  },
  {
    title: "5. 보고서·근거 출력",
    body: "PF 심사보고서와 Evidence Graph로 숫자와 문장의 출처를 확인합니다.",
  },
];

const workspaceMenus = [
  {
    href: "input-package",
    label: financeTerm("inputPackage"),
    icon: Upload,
    desc: "사업계획서, 사업수지, 토지조서, 공시지가, 등기부 등 원천자료 입력",
    tone: "text-indigo-600",
  },
  {
    href: "orchestration",
    label: financeTerm("orchestration"),
    icon: GitBranch,
    desc: "AI 업무 주문서, Agent 실행, 승인 가능성, 리스크 검토 상태 확인",
    tone: "text-slate-700 dark:text-slate-200",
  },
  {
    href: "evidence",
    label: financeTerm("evidenceGraph"),
    icon: FileSearch,
    desc: "리스크, 보고서 문장, 계산값이 어느 문서·셀·API row에서 왔는지 추적",
    tone: "text-amber-600",
  },
  {
    href: "land",
    label: financeTerm("landEvidence"),
    icon: MapPin,
    desc: "PNU, 공시지가, 등기부 권리관계, 토지취득률 검토",
    tone: "text-emerald-600",
  },
  {
    href: "feasibility",
    label: `${financeTerm("feasibility")} / ${financeTerm("cashFlow")}`,
    icon: BarChart3,
    desc: "분양수입, 사업비, 시행이익, 월별 현금흐름, Max Minus 검토",
    tone: "text-sky-600",
  },
  {
    href: "pf-structure",
    label: financeTerm("pfStructure"),
    icon: Layers,
    desc: "Tranche, LTV/LTC, Exit 분양률, 상환재원, 구조 보완 검토",
    tone: "text-violet-600",
  },
  {
    href: "risk",
    label: financeTerm("riskRegister"),
    icon: ShieldCheck,
    desc: "주요 리스크, 완화방안, 승인·유보·보완·해소 상태 관리",
    tone: "text-rose-600",
  },
  {
    href: "report",
    label: financeTerm("report"),
    icon: FileText,
    desc: "20개 섹션 PF 심사보고서와 Markdown/HTML/PDF/DOCX 출력 확인",
    tone: "text-cyan-600",
  },
  {
    href: "audit",
    label: financeTerm("audit"),
    icon: Clock3,
    desc: "사용자 활동, 원본 조회, 승인·유보, 증빙 변경 이력 확인",
    tone: "text-orange-600",
  },
];

export default async function ProjectDetailPage({ params }: { params: Promise<{ id: string }> | { id: string } }) {
  const resolvedParams = await params;
  const projectId = resolvedParams.id;
  const data = await safeJson<ProjectDetailResponse>(`${apiBase}/projects/${projectId}`, {});
  const runs = await safeJson<RunSummary[]>(`${apiBase}/projects/${projectId}/orchestrations`, []);
  const latestRunSummary = [...runs].sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? ""))[0];
  const latestRun = latestRunSummary
    ? await safeJson<OrchestrationRun | null>(`${apiBase}/projects/${projectId}/orchestrations/${latestRunSummary.run_id}`, null)
    : null;

  const project = data.project ?? {};
  const feasibility = data.feasibility_input ?? {};
  const pfTotal = (data.pf_structure ?? []).reduce((sum, tranche) => sum + (tranche.amount ?? 0), 0);
  const revenue = feasibility.total_revenue ?? 0;
  const cost = feasibility.total_cost ?? 0;
  const profit = revenue - cost;
  const diagnosis = buildDiagnosis(projectId, latestRun);
  const engineKpis = buildEngineKpis(latestRun);

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-3">
          <Link
            href="/dashboard"
            className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
            aria-label="대시보드로 돌아가기"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <p className="text-sm font-semibold text-slate-500">프로젝트 #{projectId}</p>
            <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
              {project.name || "사업명 미입력"}
            </h2>
            <p className="mt-1 text-sm text-slate-500">{data.land?.address ?? "주소 미입력"}</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <HeaderLink href={`/project/${projectId}/input-package`} icon={<Upload className="h-4 w-4" />} label="자료 입력" />
          <HeaderLink href={`/project/${projectId}/orchestration`} icon={<GitBranch className="h-4 w-4" />} label="AI 심사" />
          <HeaderLink href={`/project/${projectId}/evidence`} icon={<FileSearch className="h-4 w-4" />} label="근거 연결도" />
          <HeaderLink href={`/project/${projectId}/map`} icon={<MapPin className="h-4 w-4" />} label="지도 분석" />
        </div>
      </div>

      <section className="grid gap-3 lg:grid-cols-5">
        {workflowSteps.map((step, index) => (
          <div
            key={step.title}
            className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900"
          >
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-slate-950 text-xs font-black text-white dark:bg-white dark:text-slate-950">
              {index + 1}
            </div>
            <p className="mt-3 text-sm font-bold text-slate-950 dark:text-white">{step.title}</p>
            <p className="mt-2 text-xs leading-5 text-slate-500">{step.body}</p>
          </div>
        ))}
      </section>

      <section className={`rounded-lg border p-5 shadow-sm ${diagnosisToneClass(diagnosis.statusTone)}`}>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white/80 dark:bg-slate-950/50">
              {diagnosis.statusTone === "good"
                ? <CheckCircle2 className="h-5 w-5 text-emerald-600" />
                : <AlertCircle className="h-5 w-5 text-rose-600" />}
            </div>
            <div>
              <p className="text-xs font-bold uppercase text-slate-500">AI 자동 진단</p>
              <h3 className="mt-1 text-xl font-black text-slate-950 dark:text-white">{diagnosis.statusLabel}</h3>
              <p className="mt-2 text-sm font-semibold leading-6 text-slate-700 dark:text-slate-200">{diagnosis.summary}</p>
            </div>
          </div>
          <Link
            href={diagnosis.primaryAction.href}
            className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 dark:bg-white dark:text-slate-950"
          >
            {diagnosis.primaryAction.label}
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>

        {engineKpis.length > 0 ? (
          <div className="mt-5">
            <p className="text-xs font-bold uppercase text-slate-500">핵심 지표 (최신 심사)</p>
            <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
              {engineKpis.map((kpi) => (
                <div key={kpi.label} className="rounded-lg bg-white/80 p-3 dark:bg-slate-950/40">
                  <p className="text-[11px] font-semibold text-slate-500">{kpi.label}</p>
                  <p className="mt-1 text-sm font-black text-slate-950 dark:text-white">{kpi.value}</p>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div className="mt-5 grid gap-3 lg:grid-cols-[1fr_0.85fr]">
          <div className="space-y-3">
            <p className="text-sm font-bold text-slate-950 dark:text-white">업데이트가 필요한 항목</p>
            {diagnosis.updateAreas.map((area) => (
              <Link
                key={area.title}
                href={area.href}
                className="block rounded-lg bg-white/80 p-4 transition-colors hover:bg-white dark:bg-slate-950/40 dark:hover:bg-slate-950/70"
              >
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-bold text-slate-950 dark:text-white">{area.title}</p>
                  <StatusPill tone={area.severity} />
                </div>
                <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-300">{area.body}</p>
              </Link>
            ))}
          </div>
          <div className="rounded-lg bg-white/80 p-4 dark:bg-slate-950/40">
            <p className="text-sm font-bold text-slate-950 dark:text-white">다음 조치</p>
            <ol className="mt-3 space-y-2 text-sm font-semibold leading-6 text-slate-700 dark:text-slate-200">
              {diagnosis.nextSteps.map((step, index) => (
                <li key={`${step}-${index}`}>
                  {index + 1}. {step}
                </li>
              ))}
            </ol>
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi label="총분양수입" value={fmtEok(revenue)} />
        <Kpi label="총사업비" value={fmtEok(cost)} />
        <Kpi label="PF 대출금" value={fmtEok(pfTotal)} />
        <Kpi label="시행이익" value={fmtEok(profit)} tone={profit > 0 ? "good" : profit < 0 ? "bad" : undefined} />
      </section>

      <CovenantMonitorPanel projectId={projectId} />

      <IntentPlanMenu projectId={projectId} />

      <section className="grid gap-4 xl:grid-cols-[1.05fr_0.95fr]">
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-50 dark:bg-indigo-950/30">
              <Upload className="h-5 w-5 text-indigo-600" />
            </div>
            <div>
              <h3 className="font-bold text-slate-950 dark:text-white">사업계획서 빠른 입력</h3>
              <p className="text-xs text-slate-500">PDF를 올리면 AI가 사업개요와 주요 수치를 추출하고, 필요한 보완자료를 진단합니다.</p>
            </div>
          </div>
          <div className="mt-4">
            <FileUpload projectId={projectId} />
          </div>
          <div className="mt-4 rounded-lg bg-slate-50 p-3 text-xs font-semibold leading-5 text-slate-500 dark:bg-slate-800/70">
            정식 파일럿은 사업자료·증빙 입력 화면에서 사업수지 Excel, 토지조서, 공시지가, 등기부까지 묶어서 올리는 방식이 더 정확합니다.
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-slate-700 dark:text-slate-200" />
            <h3 className="font-bold text-slate-950 dark:text-white">심사 진행 순서</h3>
          </div>
          <div className="mt-4 space-y-3">
            <WorkflowLink
              href={`/project/${projectId}/input-package`}
              title="1. 사업자료·증빙 입력"
              body="사업계획서, 사업수지, 토지조서, 공시지가, 등기부 등 원천자료를 등록합니다."
            />
            <WorkflowLink
              href={`/project/${projectId}/orchestration`}
              title="2. AI 업무 주문서 확인"
              body="AI가 가능한 분석과 부족자료, 추천 workflow, 검증조건을 제시합니다."
            />
            <WorkflowLink
              href={`/project/${projectId}/evidence`}
              title="3. 근거 연결도 확인"
              body="리스크와 보고서 문구가 어느 문서, Excel 셀, API row에서 왔는지 감사 관점에서 확인합니다."
            />
          </div>
        </div>
      </section>

      <section>
        <div className="mb-4">
          <h3 className="text-lg font-bold text-slate-950 dark:text-white">업무 메뉴</h3>
          <p className="text-sm text-slate-500">시스템 용어 대신 국내 부동산 PF 심사 실무 용어 기준으로 정리했습니다.</p>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {workspaceMenus.map((menu) => {
            const Icon = menu.icon;
            return (
              <Link
                key={menu.href}
                href={`/project/${projectId}/${menu.href}`}
                className="group rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition-all hover:border-slate-300 hover:shadow-md dark:border-slate-800 dark:bg-slate-900 dark:hover:border-slate-700"
              >
                <div className="flex items-start gap-3">
                  <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-slate-100 transition-colors group-hover:bg-indigo-50 dark:bg-slate-800">
                    <Icon className={`h-5 w-5 ${menu.tone}`} />
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-bold text-slate-950 dark:text-white">{menu.label}</p>
                    <p className="mt-1 text-xs leading-5 text-slate-500">{menu.desc}</p>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function diagnosisToneClass(tone: ProjectDiagnosis["statusTone"]) {
  if (tone === "good") return "border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/20";
  if (tone === "bad") return "border-rose-200 bg-rose-50 dark:border-rose-900 dark:bg-rose-950/20";
  return "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/20";
}

function HeaderLink({ href, icon, label }: { href: string; icon: ReactNode; label: string }) {
  return (
    <Link
      href={href}
      className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
    >
      {icon}
      {label}
    </Link>
  );
}

function WorkflowLink({ href, title, body }: { href: string; title: string; body: string }) {
  return (
    <Link
      href={href}
      className="block rounded-lg bg-slate-50 p-3 transition-colors hover:bg-slate-100 dark:bg-slate-800/70 dark:hover:bg-slate-800"
    >
      <p className="text-sm font-bold text-slate-950 dark:text-white">{title}</p>
      <p className="mt-1 text-xs leading-5 text-slate-500">{body}</p>
    </Link>
  );
}

function StatusPill({ tone }: { tone: "good" | "warn" | "bad" }) {
  const label = tone === "good" ? "정상" : tone === "bad" ? "차단" : "확인";
  const klass = tone === "good"
    ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300"
    : tone === "bad"
      ? "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300"
      : "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300";
  return <span className={`rounded-md border px-2 py-0.5 text-xs font-semibold ${klass}`}>{label}</span>;
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: "good" | "bad" }) {
  const color = tone === "good"
    ? "text-emerald-600 dark:text-emerald-300"
    : tone === "bad"
      ? "text-rose-600 dark:text-rose-300"
      : "text-slate-950 dark:text-white";
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-sm font-semibold text-slate-500">{label}</p>
      <p className={`mt-3 text-2xl font-black tracking-tight ${color}`}>{value}</p>
    </div>
  );
}
