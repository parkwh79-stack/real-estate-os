"use client";

import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  BriefcaseBusiness,
  CheckCircle2,
  Clock3,
  FileText,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import type { RecentWorkItem } from "../../lib/project-client";
import {
  ApprovalStatusBadge,
  ExecutionStatusBadge,
  normalizeApprovalStatus,
  projectExecutionStatus,
  type ApprovalStatusPresentation,
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
  errors: DashboardDataError[];
  onRetry: () => void;
}

interface AxisPresentation<T> {
  status: T;
  detail: string;
}

const TRACK_LABELS: Record<string, string> = {
  development: "부동산 개발",
  pf: "부동산 PF",
  finance: "부동산 금융",
  real_asset_finance: "부동산 담보금융",
  reit: "리츠",
  fund: "부동산 펀드",
};

const ACTION_LABELS: Record<string, string> = {
  upload_source_documents: "원본 문서 업로드",
  run_initial_analysis: "초기 분석 실행",
  resume_open_reviews: "미해결 검토 이어가기",
  inspect_latest_run: "최근 실행 상태 확인",
  continue_next_review_step: "다음 검토 단계 진행",
};

const APPROVAL_READINESS_LABELS: Record<string, string> = {
  not_started: "Gate 미실행",
  not_ready_for_approval: "승인 준비 미완료",
  ready_for_approval: "승인 가능·미승인",
  needs_review: "승인 전 검토 필요",
  hold: "승인 보류",
};

function normalized(value: unknown) {
  return String(value ?? "").trim().toLowerCase();
}

function executionPresentation(item: RecentWorkItem): AxisPresentation<ExecutionStatusPresentation> {
  const projection = projectExecutionStatus("recent_work_v2", item.latestRunStatus);
  const detailByCode = {
    EXACT: projection.rawStatus?.replaceAll("_", " ") ?? "실행 상태 확인",
    QUEUED_AS_PENDING: "대기열",
    RETRYING_AS_RUNNING: "재시도 중",
    COMPLETED_WITH_BLOCKERS_AS_WARNING: "차단 항목 있음",
    RECALCULATION_REQUIRED: "재계산 필요",
    NO_EXECUTION_RECORD: item.latestRunId ? "상태 미제공" : "실행 기록 없음",
    UNKNOWN_SOURCE_STATUS: projection.rawStatus
      ? `정의되지 않은 상태: ${projection.rawStatus}`
      : "정의되지 않은 실행 상태",
  } satisfies Record<typeof projection.mappingCode, string>;
  return {
    status: projection.status,
    detail: detailByCode[projection.mappingCode],
  };
}

function approvalPresentation(item: RecentWorkItem): AxisPresentation<ApprovalStatusPresentation> {
  const raw = normalized(item.approvalStatus);
  if (!raw) {
    return { status: normalizeApprovalStatus("not_requested"), detail: "승인 기록 없음" };
  }
  return {
    status: normalizeApprovalStatus(raw),
    detail: raw.replaceAll("_", " "),
  };
}

function isApprovalAttention(item: RecentWorkItem) {
  const readiness = normalized(item.approvalReadinessStatus);
  return ["not_ready_for_approval", "needs_review", "hold"].includes(readiness);
}

function isExecutionAttention(item: RecentWorkItem) {
  const status = normalized(item.latestRunStatus);
  return (
    ["queued", "running", "retrying", "failed", "blocked", "completed_with_blockers", "needs_recalculation"].includes(status) ||
    ["run_initial_analysis", "inspect_latest_run"].includes(item.nextAction)
  );
}

function priorityScore(item: RecentWorkItem) {
  const execution = normalized(item.latestRunStatus);
  let score = 0;
  if (["failed", "blocked"].includes(execution)) score += 100;
  if (isApprovalAttention(item)) score += 80;
  if (item.sourceDocumentCount === 0) score += 70;
  if (item.openReviewCount > 0) score += 50 + Math.min(item.openReviewCount, 10);
  if (["queued", "running", "retrying"].includes(execution)) score += 30;
  if (item.riskCount > 0) score += Math.min(item.riskCount, 20);
  return score;
}

function reviewReasons(item: RecentWorkItem) {
  const reasons: Array<{ text: string; important: boolean }> = [];
  const execution = normalized(item.latestRunStatus);
  const approvalReadiness = normalized(item.approvalReadinessStatus);
  if (execution === "failed") reasons.push({ text: "최근 실행 실패", important: true });
  if (execution === "blocked") reasons.push({ text: "최근 실행 차단", important: true });
  if (item.sourceDocumentCount === 0) reasons.push({ text: "원본 문서 없음", important: true });
  if (isApprovalAttention(item)) {
    reasons.push({
      text: `승인 Gate: ${APPROVAL_READINESS_LABELS[approvalReadiness] ?? approvalReadiness}`,
      important: true,
    });
  }
  if (item.openReviewCount > 0) {
    reasons.push({ text: `미해결 검토 ${item.openReviewCount.toLocaleString("ko-KR")}건`, important: false });
  }
  if (reasons.length === 0) reasons.push({ text: "차단 사유 상세 미제공", important: false });
  return reasons;
}

function actionHref(item: RecentWorkItem) {
  const base = `/project/${encodeURIComponent(item.projectId)}`;
  if (item.nextAction === "upload_source_documents") return `${base}/input-package`;
  if (["run_initial_analysis", "resume_open_reviews", "inspect_latest_run"].includes(item.nextAction)) {
    return `${base}/chief-orchestration`;
  }
  return base;
}

function formatTimestamp(value: string | undefined) {
  if (!value) return "기록 없음";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

export function WorkInbox({ items, totalProjectCount, loading, errors, onRetry }: WorkInboxProps) {
  if (loading) return <WorkInboxLoading />;

  const ordered = [...(items ?? [])].sort((a, b) => {
    const priority = priorityScore(b) - priorityScore(a);
    if (priority !== 0) return priority;
    return String(b.lastWorkedAt ?? "").localeCompare(String(a.lastWorkedAt ?? ""));
  });
  const executionAttentionCount = ordered.filter(isExecutionAttention).length;
  const approvalAttentionCount = ordered.filter(isApprovalAttention).length;
  const openReviewCount = ordered.reduce((sum, item) => sum + item.openReviewCount, 0);

  return (
    <section aria-labelledby="work-inbox-title" className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.14em] text-re-muted">Role-based work inbox</p>
          <h2 id="work-inbox-title" className="mt-1 text-xl font-black tracking-tight text-re-text sm:text-2xl">
            오늘 검토할 딜
          </h2>
          <p className="mt-1 text-sm text-re-secondary">
            마감 추정이나 AI 추천이 아니라 최근 실행, 승인 Gate와 저장된 다음 행동을 기준으로 정렬했습니다.
          </p>
        </div>
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex h-9 shrink-0 items-center justify-center gap-2 rounded-re-control border border-re-border bg-re-surface px-3 text-xs font-bold text-re-secondary hover:bg-re-subtle hover:text-re-text"
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          새로고침
        </button>
      </div>

      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <SummaryMetric
          label="전체 프로젝트"
          value={totalProjectCount === null ? "—" : `${totalProjectCount.toLocaleString("ko-KR")}건`}
          note={totalProjectCount === null ? "프로젝트 목록 미수신" : "현재 권한 범위"}
          icon={BriefcaseBusiness}
        />
        <SummaryMetric
          label="실행 확인 필요"
          value={`${executionAttentionCount.toLocaleString("ko-KR")}건`}
          note="실행 전·진행·실패·재계산"
          icon={Clock3}
          tone={executionAttentionCount > 0 ? "warning" : "success"}
        />
        <SummaryMetric
          label="승인 준비 미완료"
          value={`${approvalAttentionCount.toLocaleString("ko-KR")}건`}
          note="승인 완료 건수와 분리"
          icon={ShieldAlert}
          tone={approvalAttentionCount > 0 ? "danger" : "success"}
        />
        <SummaryMetric
          label="미해결 검토"
          value={`${openReviewCount.toLocaleString("ko-KR")}건`}
          note={`업무함 ${ordered.length.toLocaleString("ko-KR")}개 프로젝트`}
          icon={FileText}
          tone={openReviewCount > 0 ? "warning" : "success"}
        />
      </div>

      {errors.length > 0 ? (
        <div className="flex flex-col gap-2 rounded-re-control border border-re-border bg-re-subtle px-4 py-3 text-sm text-re-secondary sm:flex-row sm:items-start" role="status">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-re-warning" aria-hidden="true" />
          <div>
            <p className="font-bold text-re-text">
              {items === null ? "업무함 데이터를 불러오지 못했습니다." : "일부 데이터만 표시하고 있습니다."}
            </p>
            <ul className="mt-1 space-y-0.5 text-xs text-re-muted">
              {errors.map((error) => (
                <li key={error.source}>{error.message}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}

      {items === null ? (
        <div className="rounded-re-panel border border-dashed border-re-border bg-re-surface px-6 py-10 text-center">
          <AlertTriangle className="mx-auto h-7 w-7 text-re-warning" aria-hidden="true" />
          <p className="mt-3 text-sm font-black text-re-text">검토 업무함을 표시할 수 없습니다.</p>
          <p className="mt-1 text-xs text-re-muted">새로고침 후에도 계속되면 API 연결과 현재 Tenant 권한을 확인하세요.</p>
        </div>
      ) : ordered.length === 0 ? (
        <div className="rounded-re-panel border border-dashed border-re-border bg-re-surface px-6 py-10 text-center">
          <CheckCircle2 className="mx-auto h-7 w-7 text-re-success" aria-hidden="true" />
          <p className="mt-3 text-sm font-black text-re-text">표시할 프로젝트 작업이 없습니다.</p>
          <p className="mt-1 text-xs text-re-muted">프로젝트가 없다면 아래 문서 업로드에서 새 딜을 시작할 수 있습니다.</p>
        </div>
      ) : (
        <>
          <div className="hidden overflow-hidden rounded-re-panel border border-re-border bg-re-surface shadow-re-panel lg:block">
            <table className="w-full table-fixed border-collapse text-left">
              <thead className="border-b border-re-border bg-re-subtle text-[11px] font-bold text-re-muted">
                <tr>
                  <th className="w-[22%] px-4 py-3">프로젝트</th>
                  <th className="w-[24%] px-4 py-3">실행 / 승인</th>
                  <th className="w-[20%] px-4 py-3">차단·검토 사유</th>
                  <th className="w-[12%] px-4 py-3">작업량</th>
                  <th className="w-[10%] px-4 py-3">최근 작업</th>
                  <th className="w-[12%] px-4 py-3 text-right">다음 행동</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-re-border">
                {ordered.map((item) => (
                  <WorkInboxRow key={item.projectId} item={item} />
                ))}
              </tbody>
            </table>
          </div>

          <div className="grid gap-3 lg:hidden">
            {ordered.map((item) => (
              <WorkInboxCard key={item.projectId} item={item} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function WorkInboxLoading() {
  return (
    <section aria-label="검토 업무함 로딩" className="space-y-4" aria-busy="true">
      <div>
        <div className="h-3 w-32 animate-pulse rounded bg-re-subtle" />
        <div className="mt-2 h-7 w-48 animate-pulse rounded bg-re-subtle" />
      </div>
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="h-24 animate-pulse rounded-re-panel border border-re-border bg-re-surface" />
        ))}
      </div>
      <div className="space-y-2 rounded-re-panel border border-re-border bg-re-surface p-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="h-14 animate-pulse rounded-re-control bg-re-subtle" />
        ))}
      </div>
      <span className="sr-only">검토 업무함을 불러오는 중입니다.</span>
    </section>
  );
}

function SummaryMetric({
  label,
  value,
  note,
  icon: Icon,
  tone = "neutral",
}: {
  label: string;
  value: string;
  note: string;
  icon: typeof BriefcaseBusiness;
  tone?: "neutral" | "success" | "warning" | "danger";
}) {
  const toneClass =
    tone === "success"
      ? "text-re-success"
      : tone === "warning"
        ? "text-re-warning"
        : tone === "danger"
          ? "text-re-danger"
          : "text-re-action";
  return (
    <div className="rounded-re-panel border border-re-border bg-re-surface p-4 shadow-re-panel">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-bold text-re-muted">{label}</p>
          <p className={`re-financial-number mt-2 text-2xl font-black ${toneClass}`}>{value}</p>
        </div>
        <Icon className={`h-5 w-5 shrink-0 ${toneClass}`} aria-hidden="true" />
      </div>
      <p className="mt-1 text-[11px] font-medium text-re-muted">{note}</p>
    </div>
  );
}

function WorkInboxRow({ item }: { item: RecentWorkItem }) {
  const execution = executionPresentation(item);
  const approval = approvalPresentation(item);
  const reasons = reviewReasons(item);
  return (
    <tr className="align-top hover:bg-re-subtle/60">
      <td className="px-4 py-3">
        <Link href={`/project/${encodeURIComponent(item.projectId)}`} className="font-black text-re-text hover:text-re-action">
          {item.projectName}
        </Link>
        <p className="mt-1 text-[11px] text-re-muted">
          {item.track ? TRACK_LABELS[item.track] ?? item.track : "트랙 미지정"} · #{item.projectId}
        </p>
      </td>
      <td className="space-y-1.5 px-4 py-3">
        <ExecutionStatusBadge status={execution.status} detail={execution.detail} className="max-w-full" />
        <ApprovalStatusBadge status={approval.status} detail={approval.detail} className="max-w-full" />
      </td>
      <td className="px-4 py-3">
        <ul className="space-y-1 text-xs leading-5">
          {reasons.slice(0, 2).map((reason) => (
            <li key={reason.text} className={reason.important ? "font-bold text-re-danger" : "text-re-secondary"}>
              {reason.text}
            </li>
          ))}
          {reasons.length > 2 ? <li className="text-re-muted">외 {reasons.length - 2}건</li> : null}
        </ul>
      </td>
      <td className="px-4 py-3 text-xs leading-5 text-re-secondary">
        <p>문서 {item.sourceDocumentCount.toLocaleString("ko-KR")}건</p>
        <p>검토 {item.openReviewCount.toLocaleString("ko-KR")}건</p>
        <p>Risk {item.riskCount.toLocaleString("ko-KR")}건</p>
      </td>
      <td className="px-4 py-3 text-xs text-re-secondary">
        <time dateTime={item.lastWorkedAt}>{formatTimestamp(item.lastWorkedAt)}</time>
      </td>
      <td className="px-4 py-3 text-right">
        <Link
          href={actionHref(item)}
          className="inline-flex min-h-9 items-center justify-end gap-1.5 rounded-re-control border border-re-border bg-re-surface px-2.5 text-xs font-bold text-re-action hover:bg-re-subtle"
        >
          {ACTION_LABELS[item.nextAction] ?? "작업 이어가기"}
          <ArrowRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        </Link>
      </td>
    </tr>
  );
}

function WorkInboxCard({ item }: { item: RecentWorkItem }) {
  const execution = executionPresentation(item);
  const approval = approvalPresentation(item);
  const reasons = reviewReasons(item);
  return (
    <article className="rounded-re-panel border border-re-border bg-re-surface p-4 shadow-re-panel">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link href={`/project/${encodeURIComponent(item.projectId)}`} className="block truncate font-black text-re-text hover:text-re-action">
            {item.projectName}
          </Link>
          <p className="mt-1 text-[11px] text-re-muted">
            {item.track ? TRACK_LABELS[item.track] ?? item.track : "트랙 미지정"} · #{item.projectId}
          </p>
        </div>
        <time className="shrink-0 text-[10px] text-re-muted" dateTime={item.lastWorkedAt}>
          {formatTimestamp(item.lastWorkedAt)}
        </time>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        <ExecutionStatusBadge status={execution.status} detail={execution.detail} />
        <ApprovalStatusBadge status={approval.status} detail={approval.detail} />
      </div>
      <div className="mt-3 rounded-re-control bg-re-subtle px-3 py-2">
        {reasons.slice(0, 2).map((reason) => (
          <p key={reason.text} className={`text-xs leading-5 ${reason.important ? "font-bold text-re-danger" : "text-re-secondary"}`}>
            {reason.text}
          </p>
        ))}
      </div>
      <p className="mt-3 text-xs text-re-muted">
        문서 {item.sourceDocumentCount.toLocaleString("ko-KR")}건 · 검토 {item.openReviewCount.toLocaleString("ko-KR")}건 · Risk {item.riskCount.toLocaleString("ko-KR")}건
      </p>
      <Link
        href={actionHref(item)}
        className="mt-3 inline-flex h-9 w-full items-center justify-center gap-1.5 rounded-re-control bg-re-brand px-3 text-xs font-bold text-white"
      >
        {ACTION_LABELS[item.nextAction] ?? "작업 이어가기"}
        <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
      </Link>
    </article>
  );
}
