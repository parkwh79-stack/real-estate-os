"use client";

import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Building2,
  CheckCircle2,
  ClipboardCheck,
  FileClock,
  Loader2,
  Plus,
  RefreshCw,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  fetchRecentWorkFeed,
  type RecentWorkFeed,
  type RecentWorkItem,
  type RecentWorkKind,
} from "../../lib/project-client";

type WorkFilter = "all" | RecentWorkKind;

const KIND_META: Record<RecentWorkKind, { label: string; icon: typeof Building2 }> = {
  project: { label: "프로젝트", icon: Building2 },
  review: { label: "심사·검토", icon: ClipboardCheck },
  analysis: { label: "분석·실행", icon: Activity },
};

const ACTION_LABELS: Record<string, string> = {
  upload_source_documents: "자료 업로드",
  run_initial_analysis: "초기 분석 실행",
  resume_open_reviews: "미해결 검토",
  inspect_latest_run: "실행 결과 확인",
  continue_next_review_step: "다음 단계",
};

const EXECUTION_LABELS: Record<string, string> = {
  not_started: "미실행",
  pending: "대기",
  queued: "대기열",
  running: "실행 중",
  retrying: "재시도 중",
  completed: "완료",
  completed_with_warnings: "경고 포함 완료",
  completed_with_blockers: "차단 항목 있음",
  failed: "실패",
  blocked: "차단",
  needs_recalculation: "재계산 필요",
};

const APPROVAL_LABELS: Record<string, string> = {
  not_requested: "요청 전",
  pending: "검토 중",
  approved: "승인",
  conditional: "조건부 승인",
  conditionally_approved: "조건부 승인",
  rejected: "반려",
  superseded: "대체됨",
};

export function RecentWorkPanel({
  onNewAnalysis,
  canCreateAnalysis = true,
}: {
  onNewAnalysis: () => void;
  canCreateAnalysis?: boolean;
}) {
  const [feed, setFeed] = useState<RecentWorkFeed | null>(null);
  const [loading, setLoading] = useState(true);
  const [slowLoading, setSlowLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<WorkFilter>("all");
  const [reloadKey, setReloadKey] = useState(0);

  const reload = useCallback(() => setReloadKey((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const slowTimer = window.setTimeout(() => {
      if (active) setSlowLoading(true);
    }, 4_000);
    setLoading(true);
    setSlowLoading(false);
    setError(null);
    void fetchRecentWorkFeed(12, controller.signal)
      .then((nextFeed) => {
        if (!active) return;
        setFeed(nextFeed);
      })
      .catch((reason: unknown) => {
        if (!active || controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "최근 작업을 불러오지 못했습니다.");
      })
      .finally(() => {
        window.clearTimeout(slowTimer);
        if (active) {
          setLoading(false);
          setSlowLoading(false);
        }
      });
    return () => {
      active = false;
      window.clearTimeout(slowTimer);
      controller.abort();
    };
  }, [reloadKey]);

  const counts = useMemo(() => {
    const values: Record<RecentWorkKind, number> = { project: 0, review: 0, analysis: 0 };
    for (const item of feed?.items ?? []) values[item.kind ?? "project"] += 1;
    return values;
  }, [feed]);
  const visibleItems = useMemo(
    () => (feed?.items ?? []).filter((item) => filter === "all" || (item.kind ?? "project") === filter),
    [feed, filter],
  );

  return (
    <section
      aria-labelledby="recent-work-title"
      data-testid="recent-work-panel"
      className="overflow-hidden rounded-2xl border border-slate-700/80 bg-slate-900/80 shadow-2xl shadow-black/10 backdrop-blur"
    >
      <div className="border-b border-slate-700/80 px-4 py-4 sm:px-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-[11px] font-black uppercase tracking-[0.18em] text-sky-300">Tenant work history</p>
            <h2 id="recent-work-title" className="mt-1 text-lg font-black tracking-tight text-white sm:text-xl">
              최근 프로젝트와 심사 이어가기
            </h2>
            <p className="mt-1 text-xs leading-5 text-slate-400">
              현재 조직에서 저장한 프로젝트·검토·분석 기록을 최근 작업순으로 불러옵니다.
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            <button
              type="button"
              onClick={reload}
              disabled={loading}
              aria-label="최근 작업 새로고침"
              className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg border border-slate-700 bg-slate-950/60 px-3 text-xs font-bold text-slate-300 hover:border-slate-500 hover:text-white disabled:cursor-wait disabled:opacity-60"
            >
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />}
              새로고침
            </button>
            <button
              type="button"
              onClick={onNewAnalysis}
              disabled={!canCreateAnalysis}
              title={!canCreateAnalysis ? "심사 전용 역할 · 필요 권한: project:write" : undefined}
              className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-sky-400 px-3 text-xs font-black text-slate-950 hover:bg-sky-300 disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              {canCreateAnalysis ? "새 분석" : "심사 전용"}
            </button>
          </div>
        </div>

        <div className="mt-4 flex gap-1 overflow-x-auto" role="group" aria-label="최근 작업 유형 필터">
          <FilterButton active={filter === "all"} count={feed?.items.length ?? 0} onClick={() => setFilter("all")}>
            전체
          </FilterButton>
          {(Object.keys(KIND_META) as RecentWorkKind[]).map((kind) => (
            <FilterButton key={kind} active={filter === kind} count={counts[kind]} onClick={() => setFilter(kind)}>
              {KIND_META[kind].label}
            </FilterButton>
          ))}
        </div>
      </div>

      {feed?.warnings.length ? (
        <div className="flex items-start gap-2 border-b border-amber-400/20 bg-amber-400/10 px-4 py-3 text-xs leading-5 text-amber-100" role="status">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" aria-hidden="true" />
          <div>
            <p className="font-black">일부 기록만 표시할 수 있습니다.</p>
            <p className="text-amber-100/75">{feed.warnings.join(" · ")}</p>
          </div>
        </div>
      ) : null}

      <div className="min-h-64">
        {loading && !feed ? (
          <RecentWorkLoading slow={slowLoading} canCreateAnalysis={canCreateAnalysis} />
        ) : error && !feed ? (
          <RecentWorkError message={error} onRetry={reload} />
        ) : visibleItems.length ? (
          <div className="max-h-[35rem] divide-y divide-slate-800 overflow-y-auto" aria-live="polite">
            {error ? (
              <div className="flex items-center gap-2 bg-amber-400/10 px-4 py-2.5 text-xs font-semibold text-amber-200" role="status">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                새로고침에 실패하여 직전에 불러온 기록을 표시합니다. {error}
              </div>
            ) : null}
            {visibleItems.map((item) => (
              <RecentWorkRow key={`${item.kind ?? "project"}:${item.itemId ?? `${item.projectId}:${item.updatedAt ?? ""}`}`} item={item} />
            ))}
          </div>
        ) : feed?.items.length ? (
          <div className="flex min-h-64 flex-col items-center justify-center px-6 py-10 text-center" aria-live="polite">
            <FileClock className="h-7 w-7 text-slate-500" aria-hidden="true" />
            <p className="mt-3 text-sm font-black text-white">이 유형의 최근 작업은 없습니다.</p>
            <button type="button" onClick={() => setFilter("all")} className="mt-3 text-xs font-bold text-sky-300 hover:text-sky-200">
              전체 작업 보기
            </button>
          </div>
        ) : (
          <RecentWorkEmpty
            canCreateAnalysis={canCreateAnalysis}
            onNewAnalysis={onNewAnalysis}
          />
        )}
      </div>

      <div className="flex flex-col gap-1 border-t border-slate-800 bg-slate-950/40 px-4 py-3 text-[11px] text-slate-500 sm:flex-row sm:items-center sm:justify-between">
        <span>표시 {visibleItems.length.toLocaleString("ko-KR")}건{feed && feed.totalCount > feed.items.length ? ` · 전체 ${feed.totalCount.toLocaleString("ko-KR")}건 중 최근 기록` : ""}</span>
        <span>실행 상태와 승인 상태는 별도로 표시됩니다.</span>
      </div>
    </section>
  );
}

function FilterButton({
  active,
  count,
  onClick,
  children,
}: {
  active: boolean;
  count: number;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-2.5 text-xs font-bold transition ${
        active ? "bg-white text-slate-950" : "text-slate-400 hover:bg-white/5 hover:text-white"
      }`}
    >
      {children}
      <span className={`rounded px-1.5 py-0.5 text-[10px] ${active ? "bg-slate-200 text-slate-700" : "bg-slate-800 text-slate-400"}`}>
        {count.toLocaleString("ko-KR")}
      </span>
    </button>
  );
}

function RecentWorkRow({ item }: { item: RecentWorkItem }) {
  const kind = item.kind ?? "project";
  const meta = KIND_META[kind];
  const Icon = meta.icon;
  const href = item.href ?? `/project/${encodeURIComponent(item.projectId)}`;
  const action = item.nextActionLabel ?? ACTION_LABELS[item.nextAction] ?? "작업 열기";
  const updatedAt = item.updatedAt ?? item.lastWorkedAt;
  return (
    <Link
      href={href}
      data-testid={`recent-work-${kind}`}
      className="group block px-4 py-3.5 transition hover:bg-slate-800/70 focus-visible:relative focus-visible:z-10 sm:px-5"
    >
      <article className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
        <div className="flex min-w-0 items-start gap-3">
          <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-700 bg-slate-950/60 text-sky-300">
            <Icon className="h-4 w-4" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
              <span className="text-[10px] font-black uppercase tracking-[0.12em] text-sky-300">{meta.label}</span>
              <span className="truncate text-[11px] font-semibold text-slate-500">{item.projectName}</span>
            </div>
            <h3 className="mt-1 truncate text-sm font-black text-white group-hover:text-sky-200">{item.title ?? item.projectName}</h3>
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400">{displaySummary(item.summary)}</p>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <StatusChip axis="실행" value={item.executionStatus ?? item.latestRunStatus} />
              <StatusChip axis="승인" value={item.approvalStatus} approval />
              {item.openReviewCount > 0 ? (
                <span className="rounded-md border border-amber-400/20 bg-amber-400/10 px-1.5 py-0.5 text-[10px] font-bold text-amber-200">
                  미해결 {item.openReviewCount.toLocaleString("ko-KR")}건
                </span>
              ) : null}
            </div>
          </div>
        </div>
        <div className="flex items-center justify-between gap-3 pl-12 sm:flex-col sm:items-end sm:justify-center sm:pl-0">
          <time dateTime={updatedAt} title={absoluteTimestamp(updatedAt)} className="text-[11px] font-semibold text-slate-500">
            {relativeTimestamp(updatedAt)}
          </time>
          <span className="inline-flex items-center gap-1 text-xs font-black text-sky-300 group-hover:text-sky-200">
            {action}
            <ArrowRight className="h-3.5 w-3.5 transition group-hover:translate-x-0.5" aria-hidden="true" />
          </span>
        </div>
      </article>
    </Link>
  );
}

function StatusChip({ axis, value, approval = false }: { axis: "실행" | "승인"; value?: string; approval?: boolean }) {
  const normalized = String(value ?? "").trim().toLowerCase();
  const label = approval
    ? APPROVAL_LABELS[normalized] ?? (normalized ? normalized.replaceAll("_", " ") : "요청 전")
    : EXECUTION_LABELS[normalized] ?? (normalized ? normalized.replaceAll("_", " ") : "기록 없음");
  const negative = ["failed", "blocked", "rejected"].includes(normalized);
  const warning = ["pending", "queued", "retrying", "completed_with_warnings", "completed_with_blockers", "conditional", "conditionally_approved", "needs_recalculation"].includes(normalized);
  const positive = ["completed", "approved"].includes(normalized);
  const tone = negative
    ? "border-rose-400/25 bg-rose-400/10 text-rose-200"
    : warning
      ? "border-amber-400/25 bg-amber-400/10 text-amber-200"
      : positive
        ? "border-emerald-400/25 bg-emerald-400/10 text-emerald-200"
        : "border-slate-700 bg-slate-950/60 text-slate-400";
  return (
    <span className={`rounded-md border px-1.5 py-0.5 text-[10px] font-bold ${tone}`}>
      {axis} · {label}
    </span>
  );
}

function RecentWorkLoading({
  slow,
  canCreateAnalysis,
}: {
  slow: boolean;
  canCreateAnalysis: boolean;
}) {
  return (
    <div className="space-y-1 p-3" aria-busy="true" aria-label="최근 작업을 불러오는 중">
      {Array.from({ length: 4 }).map((_, index) => (
        <div key={index} className="flex animate-pulse items-center gap-3 rounded-xl px-2 py-3">
          <div className="h-9 w-9 shrink-0 rounded-lg bg-slate-800" />
          <div className="min-w-0 flex-1">
            <div className="h-3 w-28 rounded bg-slate-800" />
            <div className="mt-2 h-4 w-3/5 rounded bg-slate-800" />
            <div className="mt-2 h-3 w-4/5 rounded bg-slate-800/70" />
          </div>
        </div>
      ))}
      <p className={`px-2 pb-2 text-center text-xs ${slow ? "text-sky-200" : "sr-only"}`} role={slow ? "status" : undefined}>
        {slow
          ? canCreateAnalysis
            ? "조직 권한과 저장된 프로젝트를 확인하고 있습니다. 새 분석은 바로 시작할 수 있습니다."
            : "조직 권한과 저장된 프로젝트를 확인하고 있습니다. 심사 전용 역할은 기존 작업을 계속 열람할 수 있습니다."
          : "최근 프로젝트와 심사 기록을 불러오고 있습니다."}
      </p>
    </div>
  );
}

function RecentWorkError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div data-testid="recent-work-error" className="flex min-h-64 flex-col items-center justify-center px-6 py-10 text-center" role="alert">
      <AlertTriangle className="h-7 w-7 text-rose-300" aria-hidden="true" />
      <p className="mt-3 text-sm font-black text-white">저장된 최근 작업을 불러오지 못했습니다.</p>
      <p className="mt-1 max-w-sm text-xs leading-5 text-slate-400">{message}</p>
      <button type="button" onClick={onRetry} className="mt-4 inline-flex h-9 items-center gap-2 rounded-lg border border-slate-600 px-3 text-xs font-bold text-white hover:border-sky-400">
        <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
        다시 불러오기
      </button>
    </div>
  );
}

function RecentWorkEmpty({
  onNewAnalysis,
  canCreateAnalysis,
}: {
  onNewAnalysis: () => void;
  canCreateAnalysis: boolean;
}) {
  return (
    <div className="flex min-h-64 flex-col items-center justify-center px-6 py-10 text-center" aria-live="polite">
      <CheckCircle2 className="h-7 w-7 text-emerald-300" aria-hidden="true" />
      <p className="mt-3 text-sm font-black text-white">저장된 최근 작업이 없습니다.</p>
      <p className="mt-1 max-w-sm text-xs leading-5 text-slate-400">
        {canCreateAnalysis
          ? "파일을 올려 첫 프로젝트를 만들면 다음 로그인부터 이곳에서 바로 이어갈 수 있습니다."
          : "심사 전용 역할에는 아직 열람 가능한 프로젝트가 없습니다. Tenant 관리자에게 프로젝트 배정 여부를 확인해 주세요."}
      </p>
      <button
        type="button"
        onClick={onNewAnalysis}
        disabled={!canCreateAnalysis}
        title={!canCreateAnalysis ? "필요 권한: project:write" : undefined}
        className="mt-4 inline-flex h-9 items-center gap-2 rounded-lg bg-sky-400 px-3 text-xs font-black text-slate-950 hover:bg-sky-300 disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
      >
        <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        {canCreateAnalysis ? "새 분석 시작" : "심사 전용 · 새 분석 불가"}
      </button>
    </div>
  );
}

function relativeTimestamp(value?: string) {
  if (!value) return "작업 시각 없음";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const delta = Date.now() - parsed.getTime();
  if (delta < 0) return absoluteTimestamp(value);
  const minutes = Math.floor(delta / 60_000);
  if (minutes < 1) return "방금 전";
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}일 전`;
  return new Intl.DateTimeFormat("ko-KR", { month: "2-digit", day: "2-digit" }).format(parsed);
}

function absoluteTimestamp(value?: string) {
  if (!value) return "작업 시각 없음";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function displaySummary(value?: string) {
  if (!value) return "저장된 작업 요약이 없습니다.";
  return value
    .split(" · ")
    .map((part) => {
      const execution = part.match(/^실행\s+(.+)$/);
      if (execution?.[1]) {
        const status = execution[1].trim().toLowerCase();
        return `실행 ${EXECUTION_LABELS[status] ?? status.replaceAll("_", " ")}`;
      }
      const approval = part.match(/^승인\s+(.+)$/);
      if (approval?.[1]) {
        const status = approval[1].trim().toLowerCase();
        return `승인 ${APPROVAL_LABELS[status] ?? status.replaceAll("_", " ")}`;
      }
      return part;
    })
    .join(" · ");
}
