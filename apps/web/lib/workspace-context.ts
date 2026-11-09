import { apiBase, authHeaders, getStoredAuthSession } from "./auth-client";

export interface WorkspaceProjectMemory {
  projectId?: string;
  projectName?: string;
  project?: {
    stage?: string;
    projectStage?: string;
    status?: string;
    asOfDate?: string;
    asOf?: string;
    freshness?: string;
    updatedAt?: string;
    [key: string]: unknown;
  };
  status?: string;
  stage?: string;
  asOfDate?: string;
  freshness?: string;
  sourceDocumentCount?: number;
  landEvidenceCount?: number;
  assumptionOverrideCount?: number;
  chiefArtifactCount?: number;
  updatedAt?: string;
}

export interface WorkspaceSummary {
  count?: number;
  byType?: Record<string, number>;
  hasOfficialLandPrice?: boolean;
  hasRegistryEvidence?: boolean;
  hasPnuResolution?: boolean;
  parcelCount?: number;
  pnuCount?: number;
  needsPnuEnrichment?: boolean;
}

export interface WorkspacePageContext {
  schemaVersion?: string;
  contextCompleteness?: "complete" | "partial";
  unavailableSections?: string[];
  projectId: string;
  page?: string;
  normalizedPage?: string;
  workflowTrack?: string;
  pageDataContract?: {
    contractVersion?: string;
    page?: string;
    sourceOfTruth?: string[];
    requiredPageDataFields?: string[];
  };
  projectMemory?: WorkspaceProjectMemory;
  dealStateGraph?: {
    updatedAt?: string;
    summary?: {
      evidenceCount?: number;
      missingInputCount?: number;
    };
    graph?: {
      conflicts?: Array<{ status?: string; [key: string]: unknown }>;
    };
    [key: string]: unknown;
  };
  evidenceVault?: unknown[];
  assumptionBook?: unknown[];
  missingInputRegister?: Array<{
    status?: string;
    severity?: string;
    [key: string]: unknown;
  }>;
  taskGraph?: Record<string, unknown>;
  agentPods?: unknown[];
  podRuns?: unknown[];
  decisionGate?: {
    decision?: string;
    status?: string;
    pass?: boolean;
  };
  pageData?: {
    sourceDocumentSummary?: WorkspaceSummary;
    landLedgerSummary?: WorkspaceSummary;
    landEvidenceSummary?: WorkspaceSummary;
    assumptionOverrideCount?: number;
    chiefArtifactSummary?: WorkspaceSummary;
    [key: string]: unknown;
  };
}

const pendingWorkspaceReads = new Map<string, Promise<WorkspacePageContext>>();

if (typeof window !== "undefined") {
  window.addEventListener("realytics-auth-session-changed", () => pendingWorkspaceReads.clear());
}

function browserWorkspaceScope(): string | null {
  if (typeof window === "undefined") return null;
  const identity = getStoredAuthSession();
  const tenantId = identity?.session.tenantId ?? identity?.user.tenantId;
  if (!identity?.user.userId || !tenantId) return null;
  return JSON.stringify([
    identity.authProvider,
    identity.user.userId,
    tenantId,
    identity.session.sessionId,
    identity.user.role,
  ]);
}

async function readWorkspaceContext(url: string): Promise<WorkspacePageContext> {
  const res = await fetch(url, {
    cache: "no-store",
    headers: authHeaders("web"),
  });
  if (!res.ok) {
    throw new Error(`Workspace context request failed (${res.status})`);
  }
  return (await res.json()) as WorkspacePageContext;
}

export function fetchWorkspaceContext(projectId: string, page = "overview", track?: string): Promise<WorkspacePageContext> {
  const params = new URLSearchParams({ page });
  if (track) params.set("track", track);
  const url = `${apiBase}/projects/${projectId}/workspace/context?${params.toString()}`;
  const scope = browserWorkspaceScope();
  if (!scope) return readWorkspaceContext(url);

  // Share only overlapping reads in this browser session. A completed read
  // must not satisfy a later refresh or a read after editing project data.
  const key = JSON.stringify([scope, url]);
  const pending = pendingWorkspaceReads.get(key);
  if (pending) return pending;

  const request: Promise<WorkspacePageContext> = readWorkspaceContext(url)
    .then((result) => {
      if (browserWorkspaceScope() !== scope || pendingWorkspaceReads.get(key) !== request) {
        throw new Error("Workspace session changed during request");
      }
      return result;
    })
    .finally(() => {
      if (pendingWorkspaceReads.get(key) === request) pendingWorkspaceReads.delete(key);
    });
  pendingWorkspaceReads.set(key, request);
  return request;
}

export function displayText(value: unknown, fallback = "-") {
  const text = String(value ?? "").trim();
  if (!text) return fallback;
  return repairUtf8Mojibake(text) || text;
}

function repairUtf8Mojibake(text: string) {
  if (!/[ÃÂìíëê]/.test(text)) return text;
  if (typeof TextDecoder === "undefined") return text;
  try {
    const bytes = new Uint8Array([...text].map((char) => char.charCodeAt(0) & 0xff));
    const repaired = new TextDecoder("utf-8", { fatal: false }).decode(bytes).trim();
    const originalHangul = countHangul(text);
    const repairedHangul = countHangul(repaired);
    return repairedHangul > originalHangul ? repaired : text;
  } catch {
    return text;
  }
}

function countHangul(text: string) {
  return [...text].filter((char) => char >= "\uac00" && char <= "\ud7a3").length;
}
