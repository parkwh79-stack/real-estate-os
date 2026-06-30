export type WorkflowTrack = "development" | "pf" | "finance" | "real_asset_finance" | "reit" | "fund";

export const WORKFLOW_TRACK_STORAGE_KEY = "realytics.workflow.track.v1";

export const TRACK_LABELS: Record<WorkflowTrack, string> = {
  development: "부동산개발",
  pf: "부동산 PF",
  finance: "부동산금융",
  real_asset_finance: "부동산 실물금융",
  reit: "리츠",
  fund: "펀드",
};

export function getStoredWorkflowTrack(): WorkflowTrack | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(WORKFLOW_TRACK_STORAGE_KEY);
  return isWorkflowTrack(raw) ? raw : null;
}

export function setStoredWorkflowTrack(track: WorkflowTrack) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(WORKFLOW_TRACK_STORAGE_KEY, track);
  window.dispatchEvent(new CustomEvent("realytics-workflow-track-changed", { detail: { track } }));
}

export function isWorkflowTrack(value: unknown): value is WorkflowTrack {
  return (
    value === "development"
    || value === "pf"
    || value === "finance"
    || value === "real_asset_finance"
    || value === "reit"
    || value === "fund"
  );
}
