"use client";

import { useState } from "react";
import { GitBranch, PlayCircle, TrendingUp } from "lucide-react";
import { IntentPlanMenu } from "./intent-plan-menu";
import { AsyncRunPanel } from "./async-run-panel";
import { EngineMetricsPanel } from "./engine-metrics-panel";

type TabKey = "plan" | "async" | "metrics";

const TABS: { key: TabKey; label: string; icon: typeof GitBranch }[] = [
  { key: "plan", label: "AI 작업 선택", icon: GitBranch },
  { key: "async", label: "백그라운드 실행", icon: PlayCircle },
  { key: "metrics", label: "수익·Stress·신용보강", icon: TrendingUp },
];

/**
 * Groups the orchestration AI panels (intent router, async run, engine metrics)
 * into a single tabbed toolbar so they don't stack into a long column.
 */
export function OrchestrationToolbar({
  projectId,
  onRefresh,
}: {
  projectId: string;
  onRefresh?: () => void;
}) {
  const [tab, setTab] = useState<TabKey>("plan");

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="AI 작업 도구">
        {TABS.map(({ key, label, icon: Icon }) => {
          const active = tab === key;
          return (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(key)}
              className={`inline-flex h-9 items-center gap-2 rounded-lg border px-3 text-sm font-bold transition-colors ${
                active
                  ? "border-slate-950 bg-slate-950 text-white dark:border-white dark:bg-white dark:text-slate-950"
                  : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
              }`}
            >
              <Icon className="h-4 w-4" />
              {label}
            </button>
          );
        })}
      </div>

      {tab === "plan" ? <IntentPlanMenu projectId={projectId} onWorkflowComplete={onRefresh} /> : null}
      {tab === "async" ? <AsyncRunPanel projectId={projectId} onComplete={onRefresh} /> : null}
      {tab === "metrics" ? <EngineMetricsPanel projectId={projectId} /> : null}
    </div>
  );
}
