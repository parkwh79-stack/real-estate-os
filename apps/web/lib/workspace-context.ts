import { apiBase, authHeaders } from "./auth-client";

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

export async function fetchWorkspaceContext(projectId: string, page = "overview", track?: string): Promise<WorkspacePageContext> {
  const params = new URLSearchParams({ page });
  if (track) params.set("track", track);
  const res = await fetch(`${apiBase}/projects/${projectId}/workspace/context?${params.toString()}`, {
    cache: "no-store",
    headers: authHeaders("web"),
  });
  if (!res.ok) {
    throw new Error(`Workspace context request failed (${res.status})`);
  }
  return (await res.json()) as WorkspacePageContext;
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
