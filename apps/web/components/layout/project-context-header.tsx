"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  CircleDot,
  FileSearch,
  FileText,
  Loader2,
  ShieldAlert,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { ProjectNameEditor, useProjectName } from "../project/project-name-editor";
import { projectWorkspaceHref, projectWorkspaceLocation } from "../../lib/project-navigation";
import { isWorkflowTrack, TRACK_LABELS } from "../../lib/workflow-track";
import {
  displayText,
  fetchWorkspaceContext,
  type WorkspacePageContext,
} from "../../lib/workspace-context";

const GATE_LABELS: Record<string, string> = {
  PASS: "통과",
  CONDITIONAL_PASS: "조건부 통과",
  HOLD: "보류",
  FAIL: "차단",
  READY: "승인 가능",
  NOT_READY: "승인 준비 미완료",
};

function contextPage(route: string | undefined) {
  const first = route?.split("/")[0] || "overview";
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
  const location = projectWorkspaceLocation(pathname, projectId);
  const page = contextPage(location?.route.path);
  const requestedTrack = searchParams.get("track") ?? undefined;
  const [result, setResult] = useState<{
    page: string;
    track: string | undefined;
    context: WorkspacePageContext | null;
  } | null>(null);
  const current = result?.page === page && result?.track === requestedTrack ? result : null;
  const context = current?.context ?? null;
  const loading = current === null;
  const error = current !== null && context === null;

  useEffect(() => {
    let active = true;
    fetchWorkspaceContext(projectId, page, requestedTrack)
      .then((body) => {
        if (active) setResult({ page, track: requestedTrack, context: body.projectId === projectId ? body : null });
      })
      .catch(() => {
        if (active) setResult({ page, track: requestedTrack, context: null });
      });
    return () => {
      active = false;
    };
  }, [projectId, page, requestedTrack]);

  const projectMemory = context?.projectMemory;
  const projectRecord = projectMemory?.project;
  const projectName = useProjectName(projectId, displayText(context?.projectId === projectId ? projectMemory?.projectName : undefined, `프로젝트 #${projectId}`));
  const workflowTrack = firstText(requestedTrack, context?.workflowTrack);
  const normalizedTrack = workflowTrack?.toLowerCase();
  const trackLabel = isWorkflowTrack(normalizedTrack) && Object.hasOwn(TRACK_LABELS, normalizedTrack)
    ? TRACK_LABELS[normalizedTrack] : workflowTrack;
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
      aria-label="프로젝트 정보"
      className="mb-4 flex items-center gap-2 border-b border-re-border pb-3 text-re-text sm:gap-3"
    >
      <div className="min-w-0 flex-1">
        <Link
          href={projectWorkspaceHref(projectId, "", requestedTrack)}
          className="block w-fit max-w-full truncate text-sm font-semibold hover:text-re-action sm:text-base"
          title={projectName}
        >
          {projectName}
        </Link>
        <p className="mt-0.5 truncate text-xs text-re-secondary" title={trackLabel}>
          {location ? `${location.group.label} / ${location.route.label}` : "프로젝트 업무"}
          {trackLabel ? <span className="hidden sm:inline"> · {trackLabel}</span> : null}
        </p>
      </div>
      {!loading && !error && context?.projectId === projectId
        ? <ProjectNameEditor key={projectId} projectId={projectId} currentName={projectName} />
        : null}
      <details className="group relative shrink-0">
        <summary className="flex min-h-9 cursor-pointer list-none items-center gap-1 rounded-re-control border border-re-border bg-re-surface px-2 text-xs font-semibold text-re-secondary transition-colors hover:bg-re-subtle motion-reduce:transition-none [&::-webkit-details-marker]:hidden">
          상태<span className="sr-only"> 상세 보기</span>
          <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180 motion-reduce:transition-none" aria-hidden="true" />
        </summary>
        <div className="absolute right-0 top-full z-10 mt-2 w-[min(19rem,calc(100vw_-_2rem))] rounded-re-panel border border-re-border bg-re-surface p-3 shadow-re-panel">
          <div className="mb-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-re-secondary">
            <span>프로젝트 #{projectId}</span>
            {trackLabel ? <span>{trackLabel}</span> : null}
            {projectStage ? <span>단계 {projectStage}</span> : null}
            {asOfDate ? <span>기준일 {asOfDate}</span> : null}
            {freshness ? <span>신선도 {freshness}</span> : null}
            {updatedAt ? <time dateTime={projectMemory?.updatedAt}>업데이트 {updatedAt}</time> : null}
          </div>
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
            <dl className="grid grid-cols-2 gap-2">
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
      </details>
    </section>
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
    <div className="flex min-w-0 items-center gap-2 rounded-re-control bg-re-subtle px-2 py-1.5">
      <Icon className={`h-4 w-4 shrink-0 ${toneClass}`} aria-hidden="true" />
      <div className="min-w-0">
        <dt className="text-[10px] font-semibold text-re-muted">{label}</dt>
        <dd className={`break-words text-xs font-semibold ${toneClass}`}>{value}</dd>
      </div>
    </div>
  );
}
