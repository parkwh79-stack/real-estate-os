"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { AlertTriangle, ArrowRight, ChevronDown, RefreshCw } from "lucide-react";
import type { RecentWorkItem } from "../../lib/project-client";
import { safeInternalNextPath } from "../../lib/safe-navigation";
import {
  ApprovalStatusBadge,
  ExecutionStatusBadge,
  normalizeApprovalStatus,
  projectExecutionStatus,
  type ExecutionStatusPresentation,
} from "../ui/primitives";

export interface DashboardDataError {
  source: "projects" | "recent-work";
  message: string;
}

interface WorkInboxProps {
  items: RecentWorkItem[] | null;
  totalProjectCount: number | null;
  loading: boolean;
  projectListLoading: boolean;
  errors: DashboardDataError[];
  onRetry: () => void;
}

type WorkFilter = "recent" | "attention" | "processing";
type WorkReason = { text: string; severe?: boolean };
const PREVIEW_COUNT = 6;
const PROCESSING_STATUSES = new Set(["queued", "running", "retrying"]);
const ACTION_LABELS: Record<string, string> = {
  upload_source_documents: "자료 올리기",
  run_initial_analysis: "분석 시작",
  resume_open_reviews: "검토 이어가기",
  inspect_latest_run: "실행 확인",
  continue_next_review_step: "검토 이어가기",
};
const TRACK_LABELS: Record<string, string> = {
  development: "부동산 개발", pf: "부동산 PF", finance: "부동산 금융",
  real_asset_finance: "부동산 담보금융", reit: "리츠", fund: "부동산 펀드",
};
const APPROVAL_READINESS_LABELS: Record<string, string> = {
  not_started: "승인 준비 검토 전",
  not_ready_for_approval: "승인 자료 보완 필요",
  ready_for_approval: "승인 검토 준비 완료",
  needs_review: "승인 전 검토 필요",
  hold: "승인 보류",
};

function normalized(value: unknown) { return String(value ?? "").trim().toLowerCase(); }
function executionStatus(item: RecentWorkItem) { return normalized(item.latestRunStatus ?? item.executionStatus); }
function isProcessing(item: RecentWorkItem) { return PROCESSING_STATUSES.has(executionStatus(item)); }

function executionPresentation(item: RecentWorkItem): { status: ExecutionStatusPresentation; detail?: string } {
  const projection = projectExecutionStatus("recent_work_v2", item.latestRunStatus ?? item.executionStatus);
  const detailByCode = {
    EXACT: undefined,
    QUEUED_AS_PENDING: "대기열",
    RETRYING_AS_RUNNING: "재시도 중",
    COMPLETED_WITH_BLOCKERS_AS_WARNING: "차단 항목 있음",
    RECALCULATION_REQUIRED: "재계산 필요",
    NO_EXECUTION_RECORD: item.latestRunId ? "상태 미제공" : "실행 기록 없음",
    UNKNOWN_SOURCE_STATUS: projection.rawStatus ? `확인되지 않은 상태: ${projection.rawStatus}` : "실행 상태 미확인",
  } satisfies Record<typeof projection.mappingCode, string | undefined>;
  return { status: projection.status, detail: detailByCode[projection.mappingCode] };
}

function reviewReasons(item: RecentWorkItem): WorkReason[] {
  const reasons: WorkReason[] = [];
  const execution = executionStatus(item);
  const approval = normalized(item.approvalStatus);
  const readiness = normalized(item.approvalReadinessStatus);
  if (execution === "failed") reasons.push({ text: "최근 실행 실패", severe: true });
  if (["blocked", "completed_with_blockers"].includes(execution)) reasons.push({ text: "실행 차단 항목 확인 필요", severe: true });
  if (approval === "rejected") reasons.push({ text: "승인 반려", severe: true });
  if (readiness === "hold") reasons.push({ text: "승인 보류", severe: true });
  if (execution === "needs_recalculation") reasons.push({ text: "입력 변경 후 재계산 필요" });
  if (execution === "completed_with_warnings") reasons.push({ text: "실행 경고 확인 필요" });
  const projection = projectExecutionStatus("recent_work_v2", item.latestRunStatus ?? item.executionStatus);
  if (projection.mappingCode === "UNKNOWN_SOURCE_STATUS" || (projection.mappingCode === "NO_EXECUTION_RECORD" && item.latestRunId)) {
    reasons.push({ text: "최근 실행 상태 확인 필요" });
  }
  if (item.sourceDocumentCount === 0) reasons.push({ text: "원본 자료 업로드 필요" });
  if (item.openReviewCount > 0) reasons.push({ text: `미해결 검토 ${item.openReviewCount.toLocaleString("ko-KR")}건` });
  if (["not_ready_for_approval", "needs_review"].includes(readiness)) {
    reasons.push({ text: APPROVAL_READINESS_LABELS[readiness] ?? "승인 전 확인 필요" });
  }
  return reasons;
}

function needsAttention(item: RecentWorkItem) { return reviewReasons(item).length > 0; }

function actionHref(item: RecentWorkItem) {
  const base = `/project/${encodeURIComponent(item.projectId)}`;
  const fallback = item.nextAction === "upload_source_documents"
    ? `${base}/input-package`
    : ["run_initial_analysis", "resume_open_reviews", "inspect_latest_run", "continue_next_review_step"].includes(item.nextAction)
      ? `${base}/chief-orchestration`
      : base;
  const candidate = safeInternalNextPath(item.href ?? null, fallback);
  const pathname = candidate.split(/[?#]/, 1)[0];
  // The feed may contain an explicit saved analysis/review route. Keep it only
  // inside this project, and repair the older normalizer's project-home fallback.
  if (pathname === base && item.nextAction === "continue_next_review_step") return fallback;
  return pathname === base || pathname?.startsWith(`${base}/`) ? candidate : fallback;
}

function formatTimestamp(value: string | undefined) {
  if (!value) return "기록 없음";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "시각 미확인";
  return new Intl.DateTimeFormat("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(parsed);
}

export function WorkInbox({ items, totalProjectCount, loading, projectListLoading, errors, onRetry }: WorkInboxProps) {
  const [filter, setFilter] = useState<WorkFilter>("recent");
  const [showAll, setShowAll] = useState(false);
  const resultsId = useId();
  if (loading) return <WorkInboxLoading />;

  const ordered = [...(items ?? [])].sort((a, b) => String(b.lastWorkedAt ?? "").localeCompare(String(a.lastWorkedAt ?? "")));
  const attention = ordered.filter(needsAttention);
  const processing = ordered.filter(isProcessing);
  const filtered = filter === "attention" ? attention : filter === "processing" ? processing : ordered;
  const displayed = showAll ? filtered : filtered.slice(0, PREVIEW_COUNT);
  const scope = items === null ? "최근 작업 확인 불가" : `최근 작업 ${ordered.length.toLocaleString("ko-KR")}개 중`;
  const filters: Array<{ value: WorkFilter; label: string; count: number }> = [
    { value: "recent", label: "최근 작업", count: ordered.length },
    { value: "attention", label: "확인 필요", count: attention.length },
    { value: "processing", label: "처리 중", count: processing.length },
  ];

  return (
    <section aria-labelledby="work-inbox-title" className="space-y-5" data-testid="dashboard-work-inbox">
      <div className="grid grid-cols-3 gap-2 sm:gap-3" aria-label="프로젝트 현황">
        <SummaryMetric label="전체 프로젝트" value={totalProjectCount}
          note={projectListLoading ? "목록 확인 중" : totalProjectCount === null ? "목록 확인 불가" : "접근 가능한 프로젝트"}
          actionLabel="전체 프로젝트 보기" href="#all-projects" onClick={() => {
            const target = document.getElementById("all-projects");
            if (target instanceof HTMLDetailsElement) target.open = true;
          }} />
        <SummaryMetric label="확인 필요" value={items === null ? null : attention.length} note={scope}
          actionLabel="확인이 필요한 최근 작업 보기" active={filter === "attention"} disabled={items === null}
          controls={ordered.length > 0 ? resultsId : undefined} onClick={() => { setFilter("attention"); setShowAll(false); }} />
        <SummaryMetric label="처리 중" value={items === null ? null : processing.length} note={scope}
          actionLabel="처리 중인 최근 작업 보기" active={filter === "processing"} disabled={items === null}
          controls={ordered.length > 0 ? resultsId : undefined} onClick={() => { setFilter("processing"); setShowAll(false); }} />
      </div>

      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="work-inbox-title" className="text-lg font-bold text-re-text">프로젝트 이어하기</h2>
          <p className="mt-1 text-xs leading-5 text-re-muted">
            {items === null ? "최근 작업을 확인한 뒤 이어서 볼 수 있습니다."
              : `최근 작업 ${ordered.length.toLocaleString("ko-KR")}개 기준 · 마지막 작업 순`}
            {items !== null && (totalProjectCount === null || totalProjectCount > ordered.length) ? " · 전체 현황과 다를 수 있습니다." : ""}
          </p>
        </div>
        <button type="button" onClick={onRetry} className="inline-flex min-h-10 shrink-0 items-center gap-1.5 rounded-re-control border border-re-border px-3 text-xs font-medium text-re-secondary hover:bg-re-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-re-action">
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />새로고침
        </button>
      </div>

      {errors.length > 0 ? (
        <div className="flex gap-2 rounded-re-control border border-re-border bg-re-subtle px-4 py-3 text-sm" role="status">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-re-warning" aria-hidden="true" />
          <div>
            <p className="font-medium text-re-text">{items === null ? "최근 작업을 불러오지 못했습니다." : "확인된 데이터만 표시하고 있습니다."}</p>
            <ul className="mt-1 space-y-1 text-xs text-re-secondary">{errors.map((error, index) => <li key={`${error.source}-${index}`}>{error.message}</li>)}</ul>
          </div>
        </div>
      ) : null}

      {items === null ? (
        <p className="rounded-re-panel border border-dashed border-re-border px-5 py-8 text-center text-sm text-re-secondary">새로고침해서 다시 확인해 주세요. 확인되지 않은 작업 수는 표시하지 않습니다.</p>
      ) : ordered.length === 0 ? (
        <p className="rounded-re-panel border border-dashed border-re-border px-5 py-8 text-center text-sm text-re-secondary">불러온 최근 작업이 없습니다.</p>
      ) : (
        <>
          <div className="flex flex-wrap gap-1 rounded-re-control bg-re-subtle p-1" role="group" aria-label="최근 작업 필터">
            {filters.map((option) => (
              <button key={option.value} type="button" aria-pressed={filter === option.value} aria-controls={resultsId}
                onClick={() => { setFilter(option.value); setShowAll(false); }}
                className={`min-h-10 rounded-re-control px-3 text-xs font-medium transition-colors motion-reduce:transition-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-re-action sm:text-sm ${filter === option.value ? "bg-re-surface text-re-text shadow-sm" : "text-re-muted hover:text-re-text"}`}>
                {option.label}<span className="ml-1.5 tabular-nums">{option.count.toLocaleString("ko-KR")}</span>
              </button>
            ))}
          </div>
          <div id={resultsId}>
            <p className="sr-only" role="status">{filters.find((option) => option.value === filter)?.label} {filtered.length}개 중 {displayed.length}개 표시</p>
            {filtered.length === 0 ? (
              <p className="rounded-re-panel border border-dashed border-re-border px-5 py-8 text-center text-sm text-re-secondary">불러온 최근 작업에서 {filter === "processing" ? "처리 중인 항목" : "확인이 필요한 항목"}이 없습니다.</p>
            ) : (
              <ul className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3" aria-label="최근 프로젝트">
                {displayed.map((item) => <WorkInboxItem key={item.itemId ?? item.projectId} item={item} />)}
              </ul>
            )}
          </div>
          {filtered.length > PREVIEW_COUNT ? (
            <button type="button" onClick={() => setShowAll((value) => !value)} className="min-h-10 w-full rounded-re-control border border-re-border text-sm font-medium text-re-secondary hover:bg-re-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-re-action">
              {showAll ? "최근 6개만 보기" : `불러온 ${filtered.length.toLocaleString("ko-KR")}개 모두 보기`}
            </button>
          ) : null}
        </>
      )}
    </section>
  );
}

function SummaryMetric({ label, value, note, actionLabel, href, onClick, active, disabled, controls }: {
  label: string; value: number | null; note: string; actionLabel: string;
  href?: string; onClick: () => void; active?: boolean; disabled?: boolean; controls?: string;
}) {
  const descriptionId = useId();
  const className = "block h-full w-full rounded-re-panel px-2 py-3 text-left transition-colors hover:bg-re-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-re-action disabled:cursor-default disabled:hover:bg-transparent motion-reduce:transition-none sm:p-4";
  const content = <>
    <span className="block whitespace-nowrap text-[11px] font-medium text-re-secondary sm:text-xs">{label}</span>
    <span id={`${descriptionId}-value`} className="re-financial-number mt-2 block break-all text-xl font-bold text-re-text sm:text-2xl">{value === null ? "—" : `${value.toLocaleString("ko-KR")}개`}</span>
    <span id={`${descriptionId}-scope`} className="mt-1 block break-keep text-balance text-[11px] leading-4 text-re-muted">{note}</span>
  </>;
  return (
    <div className={`min-w-0 rounded-re-panel border ${active ? "border-re-action bg-re-subtle" : "border-re-border bg-re-surface"}`} role="group" aria-label={label}>
      {href ? <a href={href} onClick={onClick} aria-label={actionLabel} aria-describedby={`${descriptionId}-value ${descriptionId}-scope`} className={className}>{content}</a>
        : <button type="button" onClick={onClick} aria-label={actionLabel} aria-describedby={`${descriptionId}-value ${descriptionId}-scope`} aria-pressed={active} aria-controls={controls} disabled={disabled} className={className}>{content}</button>}
    </div>
  );
}

function WorkInboxItem({ item }: { item: RecentWorkItem }) {
  const [expanded, setExpanded] = useState(false);
  const detailId = useId();
  const execution = executionPresentation(item);
  const reasons = reviewReasons(item);
  const severeReasons = reasons.filter((reason) => reason.severe);
  const primaryReasons = severeReasons.length ? severeReasons : reasons.slice(0, 1);
  const readiness = normalized(item.approvalReadinessStatus);
  const track = item.track ? TRACK_LABELS[item.track] ?? item.track : "";
  return (
    <li className="flex min-w-0 flex-col rounded-re-panel border border-re-border bg-re-surface p-4 shadow-sm sm:p-5" aria-label={`${item.projectName} 작업`}>
      <div className="flex min-h-60 flex-1 flex-col gap-3">
        <div className="min-w-0">
          <Link href={`/project/${encodeURIComponent(item.projectId)}`} className="break-words text-sm font-semibold text-re-text hover:text-re-action focus-visible:outline focus-visible:outline-2 focus-visible:outline-re-action">{item.projectName}</Link>
          {track ? <p className="mt-1 text-xs text-re-muted">{track}</p> : null}
        </div>
        <div><ExecutionStatusBadge status={execution.status} detail={execution.detail} className="max-w-full" /></div>
        <div className="min-h-10 space-y-1 break-words text-xs leading-5">
          {primaryReasons.length ? primaryReasons.map((reason) => (
            <p key={reason.text} className={reason.severe ? "font-semibold text-re-danger" : "text-re-secondary"}>{reason.text}</p>
          )) : <p className="text-re-muted">{isProcessing(item) ? "처리 결과를 기다리고 있습니다." : "등록된 확인 항목 없음"}</p>}
        </div>
        <time className="mt-auto text-xs leading-5 text-re-muted" dateTime={item.lastWorkedAt}>마지막 작업 {formatTimestamp(item.lastWorkedAt)}</time>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-re-border pt-3">
          <Link href={actionHref(item)} aria-label={`${item.projectName} ${ACTION_LABELS[item.nextAction] ?? "작업 이어가기"}`} className="inline-flex min-h-10 shrink-0 items-center justify-center gap-1 whitespace-nowrap rounded-re-control border border-re-border px-3 text-xs font-semibold text-re-action hover:bg-re-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-re-action">
            {ACTION_LABELS[item.nextAction] ?? "작업 이어가기"}<ArrowRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          </Link>
          <button type="button" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded} aria-controls={detailId} aria-label={`${item.projectName} 상세 ${expanded ? "닫기" : "보기"}`} className="inline-flex min-h-10 shrink-0 items-center gap-1 whitespace-nowrap rounded-re-control px-2 text-xs text-re-muted hover:text-re-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-re-action">
            {expanded ? "상세 닫기" : "상세 보기"}<ChevronDown className={`h-3.5 w-3.5 transition-transform motion-reduce:transition-none ${expanded ? "rotate-180" : ""}`} aria-hidden="true" />
          </button>
        </div>
      </div>
      <div id={detailId} hidden={!expanded} className="mt-4 space-y-3 rounded-re-control bg-re-subtle p-4 text-xs leading-5">
        <p className="font-medium text-re-text">자료와 검토 기록</p>
        <dl className="grid grid-cols-3 gap-2 break-words text-re-secondary [&_dd]:break-all [&_div]:min-w-0">
          <div><dt>원본 문서</dt><dd className="font-semibold text-re-text">{item.sourceDocumentCount.toLocaleString("ko-KR")}건</dd></div>
          <div><dt>미해결 검토</dt><dd className="font-semibold text-re-text">{item.openReviewCount.toLocaleString("ko-KR")}건</dd></div>
          <div><dt>등록된 위험</dt><dd className="font-semibold text-re-text">{item.riskCount.toLocaleString("ko-KR")}건</dd></div>
        </dl>
        {reasons.length ? <ul className="space-y-1 text-re-secondary">{reasons.map((reason) => <li key={reason.text} className={reason.severe ? "font-medium text-re-danger" : undefined}>{reason.text}</li>)}</ul> : null}
        <div className="border-t border-re-border pt-3">
          <p className="mb-1.5 font-medium text-re-text">정식 승인</p>
          <ApprovalStatusBadge status={normalizeApprovalStatus(item.approvalStatus || "not_requested")} />
          <p className="mt-1 text-re-secondary">{readiness ? APPROVAL_READINESS_LABELS[readiness] ?? "승인 준비 상태 미확인" : "승인 준비 기록 없음"}</p>
          <p className="mt-1 text-re-muted">분석 실행 상태와 정식 승인은 별도로 확인합니다.</p>
        </div>
      </div>
    </li>
  );
}

function WorkInboxLoading() {
  return (
    <section aria-label="최근 프로젝트 로딩" aria-busy="true" className="space-y-5">
      <div className="grid grid-cols-3 gap-3">{Array.from({ length: 3 }).map((_, index) => <div key={index} className="h-24 animate-pulse rounded-re-panel border border-re-border bg-re-surface motion-reduce:animate-none" />)}</div>
      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">{Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-72 animate-pulse rounded-re-panel border border-re-border bg-re-surface motion-reduce:animate-none" />)}</div>
      <span className="sr-only">최근 프로젝트를 불러오는 중입니다.</span>
    </section>
  );
}
