import {
  Activity,
  ArrowDownToLine,
  Clock3,
  Download,
  FileText,
  LogIn,
  Server,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import type { ReactNode } from "react";

const apiBase = process.env.REALYTICS_API_BASE_URL ?? "http://127.0.0.1:8000";

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

interface ActivityLogPanelProps {
  projectId?: string;
  limit?: number;
  title?: string;
  description?: string;
}

export async function ActivityLogPanel({
  projectId,
  limit = 100,
  title = "User Activity Log",
  description = "Login, API request, document access, and report export events recorded by the audit middleware.",
}: ActivityLogPanelProps) {
  const logs = await loadActivityLogs({ projectId, limit });
  const summary = summarizeLogs(logs);

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
        <AuditMetric icon={<FileText />} label="Sensitive events" value={String(summary.sensitiveCount)} />
        <AuditMetric icon={<Clock3 />} label="Slow requests" value={String(summary.slowRequestCount)} />
      </section>

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

async function loadActivityLogs({ projectId, limit }: { projectId?: string; limit: number }) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (projectId) params.set("project_id", projectId);

  try {
    const res = await fetch(`${apiBase}/audit/user-activity?${params.toString()}`, {
      cache: "no-store",
      headers: {
        "x-realytics-user-id": "web-dashboard",
      },
    });
    if (!res.ok) return [];
    const data = (await res.json()) as ActivityLogResponse;
    return data.logs ?? [];
  } catch {
    return [];
  }
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
  if (eventType === "login") return <LogIn className={className} />;
  if (eventType === "api_request") return <Server className={className} />;
  if (eventType === "source_document_download") return <ArrowDownToLine className={className} />;
  if (eventType?.startsWith("report_export")) return <Download className={className} />;
  return <Activity className={className} />;
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
