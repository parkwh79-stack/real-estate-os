"use client";

import type { PodKey } from "../components/orchestration/chief-deal-room-map";

export type ChiefPreflightMaterial = {
  itemId: string;
  category: string;
  description: string;
  severity: "blocking" | "high" | "medium" | "info";
  requiredFor?: string[];
  acceptableSources?: string[];
  action?: "provide_material" | "review_document";
  purpose?: string;
  podKeys?: PodKey[];
};

export type ChiefPreflightAgent = {
  podKey: PodKey;
  agentId: string;
  status: string;
  round: number;
  reason?: string;
};

export type ChiefPreflightResult = {
  schemaVersion: "realytics-chief-pod-preflight-v1";
  projectId: string;
  workflowTrack: string;
  inputRevision: string;
  analysisOnly: true;
  receiptId: string;
  resumeState: Record<string, unknown>;
  pods: Array<{
    podKey: PodKey;
    status: "checked" | "partial" | "needs_material";
    executionStatus: "completed" | "needs_material" | "failed" | "pending";
    agentExecutions?: ChiefPreflightAgent[];
    checks: Array<{ key: string; label: string; status: "available" | "missing" | "needs_review" }>;
  }>;
  materialRequests: ChiefPreflightMaterial[];
  publicLookups: Array<{
    key: string;
    label: string;
    status: "available" | "checked" | "unavailable" | "needs_input" | "failed" | "deferred" | "empty";
    checkedCount?: number;
    requestedCount?: number;
    message: string;
  }>;
  reviewLoop: {
    status: "stable" | "needs_material" | "retry_needed" | "limit_reached";
    stopReason: string;
    rounds: number;
    executedCount: number;
    reusedCount: number;
    pendingPodKeys: PodKey[];
    canContinue: boolean;
    totalExecutedCount?: number;
    totalRounds?: number;
    agents?: ChiefPreflightAgent[];
  };
};

export type ChiefPreflightMemory = {
  expiresAt?: number;
  attemptedRevision: string;
  result: ChiefPreflightResult | null;
  settled: boolean;
  error: boolean;
};

const TTL_MS = 30 * 60 * 1000;
const MAX_ENTRIES = 4;
const memory = new Map<string, { value: ChiefPreflightMemory; expiresAt: number }>();
let activeActor = "";
let expirationTimer: ReturnType<typeof setTimeout> | undefined;
let listening = false;

export function clearChiefPreflightMemory() {
  memory.clear();
  activeActor = "";
  clearTimeout(expirationTimer);
  expirationTimer = undefined;
}

function expire() {
  clearTimeout(expirationTimer);
  const now = Date.now();
  for (const [key, entry] of memory) if (entry.expiresAt <= now) memory.delete(key);
  const next = Math.min(...[...memory.values()].map((entry) => entry.expiresAt));
  expirationTimer = Number.isFinite(next) ? setTimeout(expire, Math.max(1, next - now)) : undefined;
}

function bindActor(actor: string) {
  if (!listening && typeof window !== "undefined") {
    window.addEventListener("realytics-auth-session-changed", clearChiefPreflightMemory);
    listening = true;
  }
  if (activeActor !== actor) {
    clearChiefPreflightMemory();
    activeActor = actor;
  }
}

export function readChiefPreflightMemory(actor: string, scope: string): ChiefPreflightMemory | null {
  bindActor(actor);
  expire();
  return memory.get(scope)?.value ?? null;
}

export function rememberChiefPreflight(actor: string, scope: string, value: ChiefPreflightMemory) {
  bindActor(actor);
  const current = memory.get(scope);
  memory.delete(scope);
  const expiresAt = current?.expiresAt ?? Date.now() + TTL_MS;
  const stored = { ...value, expiresAt };
  memory.set(scope, { value: stored, expiresAt });
  while (memory.size > MAX_ENTRIES) memory.delete(memory.keys().next().value!);
  expire();
  return stored;
}

export function isChiefPreflightResult(value: unknown, projectId: string, track: string): value is ChiefPreflightResult {
  if (!value || typeof value !== "object") return false;
  const result = value as Partial<ChiefPreflightResult>;
  return result.schemaVersion === "realytics-chief-pod-preflight-v1" && result.projectId === projectId
    && result.workflowTrack === track && typeof result.inputRevision === "string" && /^[0-9a-f]{64}$/.test(result.inputRevision) && result.analysisOnly === true
    && typeof result.receiptId === "string" && /^[0-9a-f]{64}$/.test(result.receiptId)
    && Boolean(result.resumeState && typeof result.resumeState === "object" && !Array.isArray(result.resumeState))
    && Array.isArray(result.pods) && Array.isArray(result.materialRequests) && Array.isArray(result.publicLookups)
    && Boolean(result.reviewLoop && ["stable", "needs_material", "retry_needed", "limit_reached"].includes(result.reviewLoop.status)
      && Array.isArray(result.reviewLoop.pendingPodKeys) && typeof result.reviewLoop.canContinue === "boolean"
      && Number.isSafeInteger(result.reviewLoop.rounds) && Number.isSafeInteger(result.reviewLoop.executedCount)
      && Number.isSafeInteger(result.reviewLoop.reusedCount));
}
