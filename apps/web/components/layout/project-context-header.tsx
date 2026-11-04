"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  BarChart3,
  Boxes,
  CheckCircle2,
  CircleDot,
  FileSearch,
  FileText,
  GitBranch,
  Landmark,
  LayoutDashboard,
  Loader2,
  Map,
  Scale,
  ShieldAlert,
  Upload,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  displayText,
  fetchWorkspaceContext,
  type WorkspacePageContext,
} from "../../lib/workspace-context";

interface ProjectNavigationItem {
  key: string;
  label: string;
  href: string;
  icon: LucideIcon;
}

interface ProjectNavigationGroup {
  key: string;
  label: string;
  defaultHref: string;
  icon: LucideIcon;
  items: ProjectNavigationItem[];
}

const PROJECT_NAVIGATION: ProjectNavigationGroup[] = [
  {
    key: "deal-overview",
    label: "딜 개요",
    defaultHref: "",
    icon: LayoutDashboard,
    items: [
      { key: "overview", label: "개요", href: "", icon: Boxes },
      { key: "deal-room", label: "AI 딜룸", href: "chief-orchestration", icon: GitBranch },
    ],
  },
  {
    key: "due-diligence",
    label: "실사·근거",
    defaultHref: "input-package",
    icon: FileSearch,
    items: [
      { key: "input", label: "자료 입력", href: "input-package", icon: Upload },
      { key: "document-review", label: "문서 검토", href: "documents/review", icon: FileSearch },
      { key: "land", label: "토지", href: "land", icon: Landmark },
      { key: "map", label: "지도", href: "map", icon: Map },
      { key: "evidence", label: "근거", href: "evidence", icon: FileSearch },
    ],
  },
  {
    key: "feasibility",
    label: "사업성",
    defaultHref: "feasibility",
    icon: BarChart3,
    items: [
      { key: "market", label: "시장성", href: "marketability", icon: BarChart3 },
      { key: "feasibility", label: "사업성", href: "feasibility", icon: BarChart3 },
      { key: "asset-comparison", label: "자산 비교", href: "asset-comparison", icon: Scale },
    ],
  },
  {
    key: "finance",
    label: "금융구조",
    defaultHref: "cashflow",
    icon: Wallet,
    items: [
      { key: "cashflow", label: "Cash Flow", href: "cashflow", icon: Wallet },
      { key: "pf", label: "PF 구조", href: "pf-structure", icon: Landmark },
    ],
  },
  {
    key: "risk-approval",
    label: "리스크·승인",
    defaultHref: "risk",
    icon: ShieldAlert,
    items: [{ key: "risk", label: "리스크·승인", href: "risk", icon: ShieldAlert }],
  },
  {
    key: "report-audit",
    label: "보고서·감사",
    defaultHref: "report",
    icon: FileText,
    items: [{ key: "report", label: "보고서", href: "report", icon: FileText }],
  },
];

const TRACK_LABELS: Record<string, string> = {
  development: "부동산 개발",
  pf: "부동산 PF",
  finance: "부동산 금융",
  real_asset_finance: "부동산 담보금융",
  reit: "리츠",
  fund: "부동산 펀드",
};

const GATE_LABELS: Record<string, string> = {
  PASS: "통과",
  CONDITIONAL_PASS: "조건부 통과",
  HOLD: "보류",
  FAIL: "차단",
  READY: "승인 가능",
  NOT_READY: "승인 준비 미완료",
};

function projectUrl(projectId: string, href: string) {
  return href ? `/project/${projectId}/${href}` : `/project/${projectId}`;
}

function isRouteActive(pathname: string, projectId: string, href: string) {
  const url = projectUrl(projectId, href);
  if (!href) return pathname === url;
  return pathname === url || pathname.startsWith(`${url}/`);
}

function pageFromPath(pathname: string, projectId: string) {
  const base = `/project/${projectId}`;
  const rest = pathname.startsWith(base) ? pathname.slice(base.length).replace(/^\/+/, "") : "";
  const first = rest.split("/")[0] || "overview";
  if (first === "input-package") return "input_package";
  if (first === "documents") return "input_package";
  if (first === "chief-orchestration") return "chief_orchestration";
  if (first === "pf-structure") return "pf";
  return first;
}

function firstText(...values: unknown[]) {
  for (const value of values) {
    const text = String(value ?? "").trim();
    if (text) return displayText(text);
  }
  return undefined;
}

function formatTimestamp(value: string | undefined) {
  if (!value) return undefined;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return displayText(value);
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function isOpenStatus(value: unknown) {
  const status = String(value ?? "open").toLowerCase();
  return !["approved", "resolved", "waived", "closed", "superseded", "rejected"].includes(status);
}

function decisionTone(value: string | undefined) {
  const normalized = String(value ?? "").toUpperCase();
  if (["PASS", "READY", "APPROVED"].includes(normalized)) return "good" as const;
  if (["FAIL", "BLOCKED", "REJECTED"].includes(normalized)) return "danger" as const;
  if (["CONDITIONAL_PASS", "HOLD", "NOT_READY", "NEEDS_REVIEW"].includes(normalized)) {
    return "warn" as const;
  }
  return "neutral" as const;
}

function formatDecision(context: WorkspacePageContext | null) {
  const value = firstText(context?.decisionGate?.decision, context?.decisionGate?.status);
  if (!value) return undefined;
  return GATE_LABELS[value.toUpperCase()] ?? value.replaceAll("_", " ");
}

export function ProjectContextHeader({ projectId }: { projectId: string }) {
  const pathname = usePathname() ?? "";
  const searchParams = useSearchParams();
  const page = useMemo(() => pageFromPath(pathname, projectId), [pathname, projectId]);
  const requestedTrack = searchParams.get("track") ?? undefined;
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(false);
    fetchWorkspaceContext(projectId, page, requestedTrack)
      .then((body) => {
        if (active) setContext(body);
      })
      .catch(() => {
        if (active) setError(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [projectId, page, requestedTrack]);

  const activeGroup =
    PROJECT_NAVIGATION.find((group) =>
      group.items.some((item) => isRouteActive(pathname, projectId, item.href)),
    ) ?? PROJECT_NAVIGATION[0]!;
  const projectMemory = context?.projectMemory;
  const projectRecord = projectMemory?.project;
  const projectName = displayText(projectMemory?.projectName, `프로젝트 #${projectId}`);
  const workflowTrack = firstText(context?.workflowTrack, requestedTrack);
  const trackLabel = workflowTrack ? TRACK_LABELS[workflowTrack.toLowerCase()] ?? workflowTrack : undefined;
  const projectStage = firstText(
    projectMemory?.stage,
    projectRecord?.stage,
    projectRecord?.projectStage,
    projectMemory?.status,
    projectRecord?.status,
  );
  const asOfDate = firstText(
    projectMemory?.asOfDate,
    projectRecord?.asOfDate,
    projectRecord?.asOf,
  );
  const freshness = firstText(projectMemory?.freshness, projectRecord?.freshness);
  const updatedAt = formatTimestamp(
    firstText(projectMemory?.updatedAt, projectRecord?.updatedAt, context?.dealStateGraph?.updatedAt),
  );
  const gateValue = firstText(context?.decisionGate?.decision, context?.decisionGate?.status);
  const gateLabel = formatDecision(context);
  const missingInputs = context?.missingInputRegister;
  const conflicts = context?.dealStateGraph?.graph?.conflicts;
  const openIssueCount = !context?.unavailableSections?.includes("missingInputs")
    && Array.isArray(missingInputs) && Array.isArray(conflicts)
    ? missingInputs.filter((item) => isOpenStatus(item.status)).length
      + conflicts.filter((item) => isOpenStatus(item.status)).length
    : null;
  const documentCount = projectMemory?.sourceDocumentCount ?? context?.pageData?.sourceDocumentSummary?.count;
  const evidenceCount =
    context?.dealStateGraph?.summary?.evidenceCount ??
    (context?.evidenceVault ? context.evidenceVault.length : projectMemory?.landEvidenceCount);

  return (
    <section
      aria-label="프로젝트 컨텍스트와 업무 메뉴"
      className="border-b border-re-border bg-re-surface text-re-text shadow-re-panel"
    >
      <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-3 px-4 py-3 sm:px-6 lg:flex-row lg:items-center lg:justify-between lg:px-8">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] font-semibold text-re-muted">
            <span className="uppercase tracking-[0.12em]">프로젝트</span>
            {trackLabel ? <ContextMeta label="트랙" value={trackLabel} /> : null}
            {projectStage ? <ContextMeta label="상태" value={projectStage} /> : null}
          </div>
          <div className="mt-1 flex min-w-0 items-center gap-2">
            <Link
              href={projectUrl(projectId, "")}
              className="truncate text-base font-black tracking-tight text-re-text hover:text-re-action sm:text-lg"
              title={projectName}
            >
              {projectName}
            </Link>
            <span className="shrink-0 rounded-re-control border border-re-border bg-re-subtle px-1.5 py-0.5 font-mono text-[10px] font-bold text-re-muted">
              #{projectId}
            </span>
          </div>
          {asOfDate || freshness || updatedAt ? (
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] font-medium text-re-muted">
              {asOfDate ? <span>기준일 {asOfDate}</span> : null}
              {freshness ? <span>신선도 {freshness}</span> : null}
              {updatedAt ? <time dateTime={projectMemory?.updatedAt}>업데이트 {updatedAt}</time> : null}
            </div>
          ) : null}
        </div>

        <div className="min-w-0 lg:max-w-[760px]">
          {loading ? (
            <p className="inline-flex items-center gap-2 text-xs font-semibold text-re-muted" role="status">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              프로젝트 컨텍스트를 불러오는 중
            </p>
          ) : error ? (
            <p className="inline-flex items-center gap-2 text-xs font-bold text-re-warning" role="status">
              <AlertTriangle className="h-4 w-4" aria-hidden="true" />
              프로젝트 상태를 갱신하지 못했습니다. 메뉴는 계속 사용할 수 있습니다.
            </p>
          ) : (
            <dl className="flex gap-2 overflow-x-auto pb-1 lg:justify-end">
              {gateLabel ? (
                <ContextFact
                  label="승인 Gate"
                  value={gateLabel}
                  icon={gateValue && decisionTone(gateValue) === "good" ? CheckCircle2 : ShieldAlert}
                  tone={decisionTone(gateValue)}
                />
              ) : null}
              <ContextFact
                label="열린 이슈"
                value={openIssueCount === null ? "미확인" : String(openIssueCount)}
                icon={openIssueCount === null ? CircleDot : openIssueCount > 0 ? AlertTriangle : CheckCircle2}
                tone={openIssueCount === null ? "neutral" : openIssueCount > 0 ? "warn" : "good"}
              />
              {typeof documentCount === "number" ? (
                <ContextFact label="문서" value={String(documentCount)} icon={FileText} />
              ) : null}
              {typeof evidenceCount === "number" ? (
                <ContextFact label="근거" value={String(evidenceCount)} icon={FileSearch} />
              ) : null}
            </dl>
          )}
        </div>
      </div>

      <nav aria-label="프로젝트 업무 영역" className="border-t border-re-border">
        <div className="mx-auto flex w-full max-w-[1600px] items-center gap-1 overflow-x-auto px-4 py-1.5 sm:px-6 lg:px-8">
          {PROJECT_NAVIGATION.map((group) => {
            const Icon = group.icon;
            const active = activeGroup.key === group.key;
            return (
              <Link
                key={group.key}
                href={projectUrl(projectId, group.defaultHref)}
                aria-current={active ? "location" : undefined}
                className={`inline-flex h-9 shrink-0 items-center gap-1.5 rounded-re-control px-3 text-xs font-bold transition-colors ${
                  active
                    ? "bg-re-brand text-white"
                    : "text-re-secondary hover:bg-re-subtle hover:text-re-text"
                }`}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                {group.label}
              </Link>
            );
          })}
        </div>
      </nav>

      <nav aria-label={`${activeGroup.label} 세부 메뉴`} className="border-t border-re-border bg-re-subtle">
        <div className="mx-auto flex w-full max-w-[1600px] items-center gap-1 overflow-x-auto px-4 py-1 sm:px-6 lg:px-8">
          <span className="mr-1 hidden shrink-0 text-[10px] font-black uppercase tracking-[0.12em] text-re-muted sm:inline">
            {activeGroup.label}
          </span>
          {activeGroup.items.map((item) => {
            const Icon = item.icon;
            const active = isRouteActive(pathname, projectId, item.href);
            return (
              <Link
                key={item.key}
                href={projectUrl(projectId, item.href)}
                aria-current={active ? "page" : undefined}
                className={`inline-flex h-8 shrink-0 items-center gap-1.5 rounded-re-control px-2.5 text-xs font-bold transition-colors ${
                  active
                    ? "bg-re-surface text-re-action shadow-re-panel ring-1 ring-re-border"
                    : "text-re-secondary hover:bg-re-surface hover:text-re-text"
                }`}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                {item.label}
              </Link>
            );
          })}
        </div>
      </nav>
    </section>
  );
}

function ContextMeta({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <CircleDot className="h-3 w-3" aria-hidden="true" />
      <span className="sr-only">{label}:</span>
      {value}
    </span>
  );
}

function ContextFact({
  label,
  value,
  icon: Icon,
  tone = "neutral",
}: {
  label: string;
  value: string;
  icon: LucideIcon;
  tone?: "good" | "warn" | "danger" | "neutral";
}) {
  const toneClass =
    tone === "good"
      ? "text-re-success"
      : tone === "warn"
        ? "text-re-warning"
        : tone === "danger"
          ? "text-re-danger"
          : "text-re-text";
  return (
    <div className="flex min-w-[118px] shrink-0 items-center gap-2 rounded-re-control border border-re-border bg-re-subtle px-2.5 py-1.5">
      <Icon className={`h-4 w-4 shrink-0 ${toneClass}`} aria-hidden="true" />
      <div className="min-w-0">
        <dt className="text-[10px] font-semibold text-re-muted">{label}</dt>
        <dd className={`truncate text-xs font-black ${toneClass}`}>{value}</dd>
      </div>
    </div>
  );
}
