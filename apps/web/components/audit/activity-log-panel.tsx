import Link from "next/link";
import {
  Activity,
  ArrowDownToLine,
  Bot,
  Download,
  KeyRound,
  LogIn,
  Server,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import type { ReactNode } from "react";
import { fetchAuthenticatedBackend } from "../../lib/authenticated-backend-fetch";

interface ActivityLog {
  eventId?: string;
  eventType?: string;
  createdAt?: string;
  userId?: string;
  projectId?: string;
  runId?: string;
  method?: string;
  path?: string;
  statusCode?: number;
  durationMs?: number;
  clientHost?: string;
  userAgent?: string;
  requestId?: string;
  metadata?: Record<string, unknown>;
}

interface ActivityLogResponse {
  logs?: ActivityLog[];
}

type ActivityLogLoadFailure = {
  state: "login_required" | "forbidden" | "authority_unavailable" | "request_failed";
  status?: number;
};

type ActivityLogLoadResult =
  | { state: "ready"; logs: ActivityLog[] }
  | ActivityLogLoadFailure;

interface ActivityLogPanelProps {
  projectId?: string;
  limit?: number;
  title?: string;
  description?: string;
  eventType?: string;
  showAiBrainAudit?: boolean;
}

export async function ActivityLogPanel({
  projectId,
  limit = 100,
  title = "User Activity Log",
  description = "Login, API request, document access, and report export events recorded by the audit middleware.",
  eventType,
  showAiBrainAudit = true,
}: ActivityLogPanelProps) {
  const result = await loadActivityLogs({ projectId, limit, eventType });
  if (result.state !== "ready") {
    return (
      <ActivityLogFailurePanel
        failure={result}
        projectId={projectId}
        title={title}
        description={description}
      />
    );
  }

  const logs = result.logs;
  const summary = summarizeLogs(logs);
  const aiBrainLogs = logs.filter((log) => log.eventType === "ai_brain_used");
  const tokenSummary = summarizeAiBrainTokens(aiBrainLogs);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-500">REALYTICS Audit</p>
          <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            {title}
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">{description}</p>
        </div>
        <span className="inline-flex w-fit items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
          <ShieldCheck className="h-4 w-4" />
          {projectId ? `Project ${projectId}` : "All projects"}
        </span>
      </div>

      <section className="grid gap-4 md:grid-cols-4">
        <AuditMetric icon={<Activity />} label="Total events" value={String(logs.length)} />
        <AuditMetric icon={<UserRound />} label="Users" value={String(summary.userCount)} />
        <AuditMetric icon={<Bot />} label="AI Brain 사용" value={String(aiBrainLogs.length)} />
        <AuditMetric icon={<KeyRound />} label="AI 토큰" value={formatTokenCount(tokenSummary.totalTokens)} />
      </section>
      {showAiBrainAudit && (
        <section className="rounded-lg border border-sky-200 bg-sky-50/70 p-5 shadow-sm dark:border-sky-900/60 dark:bg-sky-950/20">
          <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
            <div>
              <p className="text-sm font-bold text-sky-700 dark:text-sky-200">AI Brain Used Audit</p>
              <h3 className="mt-1 text-xl font-black text-slate-950 dark:text-white">에이전트별 AI 사용 이력</h3>
              <p className="mt-1 text-sm leading-6 text-slate-600 dark:text-slate-300">
                어떤 Agent가 어떤 provider/model/key 출처로 판단했는지와 tokenUsage를 확인합니다. API Key 원문은 저장하지 않습니다.
              </p>
            </div>
            <div className="grid grid-cols-3 gap-2 text-center text-xs font-bold text-slate-600 dark:text-slate-300">
              <div className="rounded-lg bg-white px-3 py-2 dark:bg-slate-900">
                <p>Prompt</p>
                <p className="mt-1 text-lg text-slate-950 dark:text-white">{formatTokenCount(tokenSummary.promptTokens)}</p>
              </div>
              <div className="rounded-lg bg-white px-3 py-2 dark:bg-slate-900">
                <p>Completion</p>
                <p className="mt-1 text-lg text-slate-950 dark:text-white">{formatTokenCount(tokenSummary.completionTokens)}</p>
              </div>
              <div className="rounded-lg bg-white px-3 py-2 dark:bg-slate-900">
                <p>Total</p>
                <p className="mt-1 text-lg text-slate-950 dark:text-white">{formatTokenCount(tokenSummary.totalTokens)}</p>
              </div>
            </div>
          </div>
          <div className="mt-4 overflow-x-auto rounded-lg border border-sky-100 bg-white dark:border-slate-800 dark:bg-slate-950/60">
            <table className="w-full min-w-[980px] text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase text-slate-500 dark:border-slate-800">
                <tr>
                  <th className="px-4 py-3">Time</th>
                  <th className="px-4 py-3">Agent</th>
                  <th className="px-4 py-3">Provider / Model</th>
                  <th className="px-4 py-3">Brain Source</th>
                  <th className="px-4 py-3">Key</th>
                  <th className="px-4 py-3">Token Usage</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Prompt Hash</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {aiBrainLogs.map((log) => {
                  const metadata = log.metadata ?? {};
                  const usage = asRecord(metadata.tokenUsage);
                  return (
                    <tr key={log.eventId ?? `${log.createdAt}-${metadata.promptHash}`} className="align-top">
                      <td className="px-4 py-3 text-slate-600 dark:text-slate-300">{formatDate(log.createdAt)}</td>
                      <td className="px-4 py-3 font-bold text-slate-950 dark:text-white">{asText(metadata.agentId) || "AIConnection"}</td>
                      <td className="px-4 py-3">
                        <p className="font-semibold text-slate-800 dark:text-slate-100">{asText(metadata.provider) || "-"}</p>
                        <p className="mt-1 text-xs text-slate-500">{asText(metadata.model) || "-"}</p>
                      </td>
                      <td className="px-4 py-3">
                        <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                          {formatBrainSource(asText(metadata.brainSource))}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs font-semibold text-slate-600 dark:text-slate-300">{asText(metadata.keyMode) || "no_key"}</td>
                      <td className="px-4 py-3 text-xs text-slate-600 dark:text-slate-300">
                        {formatUsage(usage)}
                      </td>
                      <td className="px-4 py-3">
                        <AiStatusPill status={asText(metadata.status)} />
                      </td>
                      <td className="px-4 py-3">
                        <code className="block max-w-[180px] truncate rounded bg-slate-50 px-2 py-1 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                          {asText(metadata.promptHash) || "-"}
                        </code>
                      </td>
                    </tr>
                  );
                })}
                {aiBrainLogs.length === 0 && (
                  <tr>
                    <td className="px-4 py-8 text-center text-sm text-slate-500" colSpan={8}>
                      아직 기록된 AI Brain 사용 이벤트가 없습니다. LLM-capable Agent가 실행되면 여기에 provider, model, key source, tokenUsage가 표시됩니다.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="grid gap-4 xl:grid-cols-[1fr_2fr]">
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <h3 className="text-lg font-bold text-slate-950 dark:text-white">Event Mix</h3>
          <div className="mt-4 space-y-3">
            {summary.eventRows.map((row) => (
              <div key={row.eventType} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-800/70">
                <div className="flex items-center gap-2">
                  <EventIcon eventType={row.eventType} />
                  <span className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                    {formatEventType(row.eventType)}
                  </span>
                </div>
                <span className="text-sm font-black text-slate-950 dark:text-white">{row.count}</span>
              </div>
            ))}
            {summary.eventRows.length === 0 && (
              <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-500 dark:bg-slate-800/70">
                No activity has been recorded yet.
              </p>
            )}
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="border-b border-slate-200 p-5 dark:border-slate-800">
            <h3 className="text-lg font-bold text-slate-950 dark:text-white">Recent Events</h3>
            <p className="mt-1 text-sm text-slate-500">
              Use this list to diagnose failed pilot runs, blocked reports, missing evidence access, and user actions.
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[920px] text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase text-slate-500 dark:border-slate-800">
                <tr>
                  <th className="px-4 py-3">Time</th>
                  <th className="px-4 py-3">Event</th>
                  <th className="px-4 py-3">User</th>
                  <th className="px-4 py-3">Project</th>
                  <th className="px-4 py-3">Request</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Metadata</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {logs.map((log) => (
                  <tr key={log.eventId ?? `${log.createdAt}-${log.path}`} className="align-top">
                    <td className="px-4 py-3 text-slate-600 dark:text-slate-300">{formatDate(log.createdAt)}</td>
                    <td className="px-4 py-3">
                      <span className="inline-flex items-center gap-2 rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                        <EventIcon eventType={log.eventType} />
                        {formatEventType(log.eventType)}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-semibold text-slate-950 dark:text-white">{log.userId ?? "unknown"}</td>
                    <td className="px-4 py-3 text-slate-600 dark:text-slate-300">{log.projectId ?? "-"}</td>
                    <td className="px-4 py-3">
                      <p className="font-mono text-xs text-slate-700 dark:text-slate-200">
                        {[log.method, log.path].filter(Boolean).join(" ") || "-"}
                      </p>
                      <p className="mt-1 text-xs text-slate-500">
                        {log.durationMs !== undefined ? `${log.durationMs} ms` : "duration n/a"}
                      </p>
                    </td>
                    <td className="px-4 py-3">
                      <StatusPill statusCode={log.statusCode} />
                    </td>
                    <td className="px-4 py-3">
                      <MetadataPreview metadata={log.metadata} />
                    </td>
                  </tr>
                ))}
                {logs.length === 0 && (
                  <tr>
                    <td className="px-4 py-10 text-center text-sm text-slate-500" colSpan={7}>
                      No audit events found for the selected scope.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  );
}

async function loadActivityLogs({
  projectId,
  limit,
  eventType,
}: {
  projectId?: string;
  limit: number;
  eventType?: string;
}): Promise<ActivityLogLoadResult> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (projectId) params.set("project_id", projectId);
  if (eventType) params.set("event_type", eventType);

  try {
    const res = await fetchAuthenticatedBackend(
      `/audit/user-activity?${params.toString()}`,
    );
    if (res.status === 401) return { state: "login_required", status: 401 };
    if (res.status === 403) return { state: "forbidden", status: 403 };
    if (res.status === 503 || res.status >= 500) {
      return { state: "authority_unavailable", status: res.status };
    }
    if (!res.ok) return { state: "request_failed", status: res.status };

    const data = (await res.json()) as ActivityLogResponse;
    if (!Array.isArray(data.logs)) {
      return { state: "authority_unavailable", status: 502 };
    }
    return { state: "ready", logs: data.logs };
  } catch {
    return { state: "authority_unavailable" };
  }
}

function ActivityLogFailurePanel({
  failure,
  projectId,
  title,
  description,
}: {
  failure: ActivityLogLoadFailure;
  projectId?: string;
  title: string;
  description: string;
}) {
  const content = failure.state === "login_required"
    ? {
      eyebrow: "세션 확인 필요",
      heading: "감사 로그를 보려면 다시 로그인해 주세요.",
      body: "세션이 만료되었거나 인증 정보를 확인할 수 없습니다. 로그인 후 동일한 감사 범위를 다시 열 수 있습니다.",
      tone: "border-sky-200 bg-sky-50/70 dark:border-sky-900/60 dark:bg-sky-950/20",
      icon: <LogIn className="h-5 w-5" />,
    }
    : failure.state === "forbidden"
      ? {
        eyebrow: "감사 권한 제한",
        heading: "현재 역할에는 이 감사 로그를 볼 권한이 없습니다.",
        body: "전체 감사 로그에는 audit:read 권한이 필요합니다. 개인 활동만 허용된 역할이면 조직 관리자에게 적절한 범위를 요청하세요.",
        tone: "border-amber-200 bg-amber-50/70 dark:border-amber-900/60 dark:bg-amber-950/20",
        icon: <ShieldCheck className="h-5 w-5" />,
      }
      : failure.state === "authority_unavailable"
        ? {
          eyebrow: "권위 저장소 연결 장애",
          heading: "감사 기록을 불러오지 못했습니다.",
          body: "인증 또는 PostgreSQL 감사 권위에 일시적으로 연결할 수 없습니다. 이 상태는 ‘기록 없음’으로 간주되지 않습니다.",
          tone: "border-rose-200 bg-rose-50/70 dark:border-rose-900/60 dark:bg-rose-950/20",
          icon: <Server className="h-5 w-5" />,
        }
        : {
          eyebrow: "감사 조회 실패",
          heading: "감사 API 응답을 확인할 수 없습니다.",
          body: "요청이 정상 처리되지 않았습니다. 잠시 후 다시 시도하고, 계속되면 관리자에게 응답 상태를 알려 주세요.",
          tone: "border-rose-200 bg-rose-50/70 dark:border-rose-900/60 dark:bg-rose-950/20",
          icon: <Activity className="h-5 w-5" />,
        };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-500">REALYTICS Audit</p>
          <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            {title}
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">{description}</p>
        </div>
        <span className="inline-flex w-fit items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
          <ShieldCheck className="h-4 w-4" />
          {projectId ? `Project ${projectId}` : "All projects"}
        </span>
      </div>

      <section className={`rounded-xl border p-5 shadow-sm ${content.tone}`} role="alert">
        <div className="flex items-start gap-4">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white text-slate-700 shadow-sm dark:bg-slate-900 dark:text-slate-200">
            {content.icon}
          </div>
          <div>
            <p className="text-xs font-black uppercase tracking-[0.16em] text-slate-500">{content.eyebrow}</p>
            <h3 className="mt-2 text-lg font-black text-slate-950 dark:text-white">{content.heading}</h3>
            <p className="mt-2 max-w-3xl text-sm font-semibold leading-6 text-slate-600 dark:text-slate-300">{content.body}</p>
            {failure.status ? (
              <p className="mt-3 text-xs font-bold text-slate-500">응답 상태 {failure.status}</p>
            ) : null}
            {failure.state === "login_required" ? (
              <Link href="/login" className="mt-4 inline-flex rounded-lg bg-slate-950 px-4 py-2.5 text-sm font-black text-white dark:bg-white dark:text-slate-950">
                로그인 화면으로 이동
              </Link>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}

function summarizeLogs(logs: ActivityLog[]) {
  const users = new Set(logs.map((log) => log.userId).filter(Boolean));
  const sensitiveTypes = new Set([
    "source_document_download",
    "report_export_created",
    "report_export_download",
    "raw_evidence_access",
  ]);
  const eventCounts = new Map<string, number>();

  for (const log of logs) {
    const type = log.eventType ?? "unknown";
    eventCounts.set(type, (eventCounts.get(type) ?? 0) + 1);
  }

  return {
    userCount: users.size,
    sensitiveCount: logs.filter((log) => sensitiveTypes.has(log.eventType ?? "")).length,
    slowRequestCount: logs.filter((log) => (log.durationMs ?? 0) >= 1000).length,
    eventRows: Array.from(eventCounts.entries())
      .map(([eventType, count]) => ({ eventType, count }))
      .sort((a, b) => b.count - a.count || a.eventType.localeCompare(b.eventType)),
  };
}

function summarizeAiBrainTokens(logs: ActivityLog[]) {
  return logs.reduce(
    (acc, log) => {
      const usage = asRecord(log.metadata?.tokenUsage);
      acc.promptTokens += asNumber(usage.promptTokens);
      acc.completionTokens += asNumber(usage.completionTokens);
      acc.totalTokens += asNumber(usage.totalTokens);
      return acc;
    },
    { promptTokens: 0, completionTokens: 0, totalTokens: 0 },
  );
}

function AuditMetric({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-slate-500">{label}</p>
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-50 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
          {icon}
        </div>
      </div>
      <p className="mt-4 text-3xl font-black tracking-tight text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function EventIcon({ eventType }: { eventType?: string }) {
  const className = "h-4 w-4";
  if (eventType === "ai_brain_used") return <Bot className={className} />;
  if (eventType === "login") return <LogIn className={className} />;
  if (eventType === "api_request") return <Server className={className} />;
  if (eventType === "source_document_download") return <ArrowDownToLine className={className} />;
  if (eventType?.startsWith("report_export")) return <Download className={className} />;
  return <Activity className={className} />;
}

function AiStatusPill({ status }: { status?: string }) {
  const normalized = status || "unknown";
  const className =
    normalized === "success"
      ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200"
      : normalized === "fallback_used"
        ? "bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-200"
        : "bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-200";
  return <span className={`rounded px-2 py-1 text-xs font-bold ${className}`}>{formatEventType(normalized)}</span>;
}

function StatusPill({ statusCode }: { statusCode?: number }) {
  if (statusCode === undefined) {
    return <span className="rounded bg-slate-100 px-2 py-1 text-xs font-bold text-slate-600 dark:bg-slate-800 dark:text-slate-300">n/a</span>;
  }
  const good = statusCode < 400;
  return (
    <span className={`rounded px-2 py-1 text-xs font-bold ${
      good
        ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200"
        : "bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-200"
    }`}
    >
      {statusCode}
    </span>
  );
}

function MetadataPreview({ metadata }: { metadata?: Record<string, unknown> }) {
  if (!metadata || Object.keys(metadata).length === 0) {
    return <span className="text-xs text-slate-400">-</span>;
  }
  const text = JSON.stringify(metadata);
  return (
    <code className="block max-w-[360px] truncate rounded bg-slate-50 px-2 py-1 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
      {text}
    </code>
  );
}

function formatDate(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatEventType(eventType?: string) {
  return (eventType ?? "unknown").replaceAll("_", " ");
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : value === undefined || value === null ? "" : String(value);
}

function asNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function formatTokenCount(value: number) {
  return value > 0 ? value.toLocaleString("ko-KR") : "-";
}

function formatUsage(usage: Record<string, unknown>) {
  const prompt = asNumber(usage.promptTokens);
  const completion = asNumber(usage.completionTokens);
  const total = asNumber(usage.totalTokens);
  if (!prompt && !completion && !total) return "n/a";
  return `P ${formatTokenCount(prompt)} / C ${formatTokenCount(completion)} / T ${formatTokenCount(total)}`;
}

function formatBrainSource(value: string) {
  const labels: Record<string, string> = {
    user_profile: "사용자 API",
    request_override: "요청별 API",
    system_fallback: "시스템 fallback",
    tenant_policy: "조직 정책",
    deterministic_fallback: "계산/규칙 fallback",
  };
  return labels[value] ?? (value.replaceAll("_", " ") || "-");
}
