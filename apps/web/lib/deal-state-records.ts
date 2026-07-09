"use client";

import { apiBase, authHeaders } from "./auth-client";

export type DealStateRecordType =
  | "metric"
  | "risk_issue"
  | "evidence"
  | "assumption"
  | "parsed_fact"
  | "parsed_fact_validation"
  | "auto_supplement_attempt";

export type DealStateRecord = {
  recordId?: string;
  recordType?: DealStateRecordType | string;
  key?: string;
  value?: unknown;
  unit?: string;
  label?: string;
  sourceType?: string;
  sourceLocator?: Record<string, unknown>;
  calculationRunId?: string;
  riskRegisterRunId?: string;
  evidenceGraphRunId?: string;
  evidenceGraphNodeId?: string;
  confidence?: number;
  severity?: string;
  reviewStatus?: string;
  updatedAt?: string;
  [key: string]: unknown;
};

export type DealStateRecordList = {
  schemaVersion?: string;
  projectId?: string;
  recordType?: string | null;
  count?: number;
  records?: DealStateRecord[];
};

export async function fetchDealStateRecords(
  projectId: string,
  options: {
    recordType?: DealStateRecordType;
    calculationRunId?: string;
    limit?: number;
    actor?: string;
  } = {},
): Promise<DealStateRecord[]> {
  const params = new URLSearchParams();
  if (options.recordType) params.set("record_type", options.recordType);
  if (options.calculationRunId) params.set("calculation_run_id", options.calculationRunId);
  params.set("limit", String(options.limit ?? 300));
  const suffix = params.toString();
  const res = await fetch(`${apiBase}/projects/${projectId}/deal-state-records?${suffix}`, {
    cache: "no-store",
    headers: authHeaders(options.actor ?? "web"),
  });
  if (!res.ok) return [];
  const body = (await res.json()) as DealStateRecordList;
  return body.records ?? [];
}

export async function fetchDealStateRecordGroups(projectId: string, limit = 500) {
  const [metrics, risks, evidence, assumptions, parsedFacts, parsedFactValidations, autoSupplementAttempts] = await Promise.all([
    fetchDealStateRecords(projectId, { recordType: "metric", limit, actor: "web-auditor" }),
    fetchDealStateRecords(projectId, { recordType: "risk_issue", limit, actor: "web-auditor" }),
    fetchDealStateRecords(projectId, { recordType: "evidence", limit, actor: "web-auditor" }),
    fetchDealStateRecords(projectId, { recordType: "assumption", limit, actor: "web-auditor" }),
    fetchDealStateRecords(projectId, { recordType: "parsed_fact", limit, actor: "web-auditor" }),
    fetchDealStateRecords(projectId, { recordType: "parsed_fact_validation", limit, actor: "web-auditor" }),
    fetchDealStateRecords(projectId, { recordType: "auto_supplement_attempt", limit, actor: "web-auditor" }),
  ]);
  return { metrics, risks, evidence, assumptions, parsedFacts, parsedFactValidations, autoSupplementAttempts };
}

export function hasSourceLocator(record: DealStateRecord) {
  const locator = record.sourceLocator;
  if (!locator || typeof locator !== "object") return false;
  return Object.values(locator).some((value) => value !== undefined && value !== null && value !== "" && value !== 0);
}

export function locatorSummary(record: DealStateRecord) {
  const locator = record.sourceLocator ?? {};
  const preferred = [
    locator.documentName ? `문서 ${locator.documentName}` : "",
    locator.documentId ? `문서 ${locator.documentId}` : "",
    locator.page ? `p.${locator.page}` : "",
    locator.pageNumber ? `p.${locator.pageNumber}` : "",
    locator.sheet ? `시트 ${locator.sheet}` : "",
    locator.cell ? `셀 ${locator.cell}` : "",
    locator.row ? `행 ${locator.row}` : "",
    locator.apiRowId ? `API row ${locator.apiRowId}` : "",
    locator.evidenceId ? `증빙 ${locator.evidenceId}` : "",
    locator.pnu ? `PNU ${locator.pnu}` : "",
    locator.sourceName ? `${locator.sourceName}` : "",
    locator.artifactKey ? `Artifact ${locator.artifactKey}` : "",
    locator.nodeId ? `Node ${locator.nodeId}` : "",
  ].filter(Boolean);
  return preferred.length ? preferred.join(" · ") : "-";
}

export function formatRecordValue(record: DealStateRecord) {
  const value = record.value;
  if (typeof value === "number") {
    return `${value.toLocaleString("ko-KR")}${record.unit ? ` ${record.unit}` : ""}`;
  }
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return "-";
  return JSON.stringify(value);
}
