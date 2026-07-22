"use client";

import type { ReactNode } from "react";
import { FinancialMetric } from "@repo/ui/financial-metric";

export {
  ApprovalStatusBadge,
  DataQualityBadge,
  EvidenceStatusBadge,
  ExecutionStatusBadge,
  ReportEligibilityBadge,
} from "@repo/ui/status-axis";

export type Tone = "good" | "warn" | "bad" | "info" | "neutral";

const TONE_PILL: Record<Tone, string> = {
  good: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  warn: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  bad: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200",
  info: "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-200",
  neutral: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
};

/** Compact KPI/metric card used across orchestration, report, and mobile views. */
export function MetricCard({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return <FinancialMetric label={label} value={value} basis={hint} />;
}

/** Tone-based status pill. */
export function StatusBadge({ tone, label, icon }: { tone: Tone; label: string; icon?: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-black ${TONE_PILL[tone]}`}>
      {icon}
      {label}
    </span>
  );
}

/** Empty / no-data placeholder. */
export function EmptyState({ title, body, icon }: { title: string; body?: string; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-200 px-4 py-8 text-center dark:border-slate-800">
      {icon ? <div className="mb-2 text-slate-400">{icon}</div> : null}
      <p className="text-sm font-bold text-slate-700 dark:text-slate-200">{title}</p>
      {body ? <p className="mt-1 max-w-sm text-xs leading-5 text-slate-500">{body}</p> : null}
    </div>
  );
}

/** Animated loading placeholder block. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-slate-100 dark:bg-slate-800 ${className}`} />;
}

/** A small grid of skeleton metric cards for loading states. */
export function SkeletonMetrics({ count = 5 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
          <Skeleton className="h-3 w-12" />
          <Skeleton className="mt-2 h-4 w-16" />
        </div>
      ))}
    </div>
  );
}
