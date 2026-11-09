"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronRight,
  Database,
  Download,
  ExternalLink,
  FileCheck2,
  FileQuestion,
  FileSpreadsheet,
  FileText,
  GitCompareArrows,
  History,
  ListChecks,
  Loader2,
  LocateFixed,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Tags,
  XCircle,
} from "lucide-react";
import {
  errorMessage,
  fetchDocumentReviewData,
  fetchGlossaryTermsForField,
  fetchPdfPagePreview,
  pdfLocatorOverlay,
  ParserReviewSaveError,
  recordParserV2Review,
  resolveParserV2Conflict,
  safeSourceUrl,
  sourceLocatorMatchesDocument,
  sourceLocatorSemanticKey,
  sourceDocumentContentUrl,
  type ApprovedSnapshotDiff,
  type ApprovedInputSnapshotRecord,
  type CanonicalSelectionRecord,
  type DocumentReviewData,
  type DocumentReviewQueueItem,
  type GlossaryCandidateRecord,
  type GlossaryTermRecord,
  type ParsedFactCandidate,
  type ParserV2Run,
  type ParserV2RunPackage,
  type ReviewDecisionRecord,
  type SourceConflictRecord,
  type SourceDocumentRecord,
  type SourceLocatorRecord,
} from "../../lib/document-review-workspace";
import { displayText } from "../../lib/workspace-context";
import {
  ApprovalStatusBadge,
  ExecutionStatusBadge,
  ReviewStatusBadge,
  normalizeApprovalStatus,
  normalizeExecutionStatus,
  normalizeReviewStatus,
} from "../ui/primitives";
import { ProjectWorkspaceLink } from "../layout/project-workspace-link";
import { PdfPageReadingPanel } from "./pdf-page-reading-panel";
import { ArchiveExtractionAction } from "./archive-extraction-action";
import { WorkbookOriginalPreview } from "./workbook-original-preview";
import { DashboardAtmosphere } from "../dashboard/dashboard-atmosphere";
import heroStyles from "../orchestration/chief-deal-room-workspace.module.css";

type InspectorTab = "facts" | "evidence" | "issues" | "conflicts" | "glossary" | "review" | "changes" | "audit";

type WorkspaceDocument = {
  key: string;
  archiveId?: string;
  documentId?: string;
  documentVersionId?: string;
  versionNumber?: number;
  fileName: string;
  documentType?: string;
  mediaType?: string;
  sizeBytes?: number;
  uploadedAt?: string;
  validationStatus?: string;
  integrityStatus?: string;
  sourceAuthority?: string;
  downloadUrl?: string;
  runIds: string[];
  parserRegistered: boolean;
  latestRun?: ParserV2Run;
  candidateCount?: number;
};

type WorkspaceFact = {
  key: string;
  candidate: ParsedFactCandidate;
  package: ParserV2RunPackage;
  run: ParserV2Run;
  candidateId: string;
  documentVersionId: string;
  runId: string;
  fieldKey: string;
  latestDecision?: ReviewDecisionRecord;
  reviewStatus: string;
  reviewApprovalStatus: string;
  snapshotApprovalStatus: string;
  authorityStage: "candidate" | "reviewed" | "canonical_selected" | "snapshot_approved" | "superseded";
  activeSelection?: CanonicalSelectionRecord;
  activeSnapshot?: ApprovedInputSnapshotRecord;
  validationStatus: string;
  validationOutcome: string;
  conflictStatus: string;
  conflicts: SourceConflictRecord[];
  issueCodes: string[];
};

type LocatorAnchor = {
  key: string;
  label: string;
  detail: string;
  locator: SourceLocatorRecord;
  factKeys: string[];
};

type AuditItem = {
  key: string;
  at?: string;
  category: string;
  title: string;
  detail?: string;
  status?: string;
};

const TABS: Array<{ key: InspectorTab; label: string }> = [
  { key: "facts", label: "파싱값" },
  { key: "evidence", label: "출처 상세" },
  { key: "issues", label: "검토할 항목" },
  { key: "conflicts", label: "값 충돌" },
  { key: "glossary", label: "용어 확인" },
  { key: "review", label: "검토 결정" },
  { key: "changes", label: "승인본 변경" },
  { key: "audit", label: "검토 이력" },
];

function identifier(value: unknown) {
  return String(value ?? "").trim();
}

function archiveIdOf(document: SourceDocumentRecord) {
  return identifier(document.archiveId ?? document.originalFileHash ?? document.sha256);
}

function sourceFileName(document: SourceDocumentRecord) {
  return displayText(document.originalFileName ?? document.fileName, "파일명 미확인");
}

function sourceSize(document: SourceDocumentRecord) {
  const value = Number(document.fileSizeBytes ?? document.sizeBytes);
  return Number.isFinite(value) && value >= 0 ? value : undefined;
}

function sourceDate(document: SourceDocumentRecord) {
  return identifier(document.uploadedAt ?? document.createdAt) || undefined;
}

function packageRunId(runPackage: ParserV2RunPackage) {
  return identifier(runPackage.parsingRun.parsingRunId);
}

function candidateMetadata(fact: WorkspaceFact): Record<string, unknown> {
  const metadata = fact.candidate.metadata;
  return metadata && typeof metadata === "object" && !Array.isArray(metadata)
    ? metadata as Record<string, unknown>
    : {};
}

function preferredFactLocator(fact: WorkspaceFact, evidence: SourceLocatorRecord[]) {
  const metadata = candidateMetadata(fact);
  const table = metadata.sourceTable;
  if (metadata.extractionRule === "explicit_bridge_financial_table_literal_v1"
    && table && typeof table === "object" && !Array.isArray(table)) {
    const sourceTable = table as Record<string, unknown>;
    const row = sourceTable.row;
    const headerRow = sourceTable.headerRow;
    const sheetName = sourceTable.sheetName;
    if (typeof row === "number" && Number.isInteger(row)
      && typeof headerRow === "number" && Number.isInteger(headerRow) && headerRow > 0 && row > headerRow
      && typeof sheetName === "string" && sheetName.trim()) {
      const version = fact.package.documentVersion;
      const document = { ...version, documentVersionId: fact.documentVersionId,
        archiveId: version.archiveId ?? version.sha256 ?? version.sourceHash };
      const rowLocators = evidence.filter((locator) => {
        const cell = identifier(locator.cellReference ?? locator.cellRange ?? locator.cell);
        const match = cell.match(/^\$?[A-Z]{1,3}\$?([1-9]\d*)$/i);
        return identifier(locator.sheetName ?? locator.sheet) === sheetName
          && match && Number(match[1]) === row && sourceLocatorMatchesDocument(locator, document);
      });
      if (rowLocators.length === 1) return rowLocators[0];
    }
  }
  const primary = fact.candidate.sourceLocator;
  const semanticKey = primary && sourceLocatorSemanticKey(primary, fact.documentVersionId);
  return (semanticKey && evidence.find((locator) => sourceLocatorSemanticKey(locator, fact.documentVersionId) === semanticKey))
    || evidence.at(0);
}

function reviewDraftKey(projectId: string, fact: WorkspaceFact) {
  return JSON.stringify([projectId, fact.documentVersionId, fact.runId, fact.candidateId]);
}

function factLabel(fact: WorkspaceFact) {
  const labels: Record<string, string> = {
    "financing.tranche.loan_amount": "대출금",
    "financing.tranche.name": "대주명",
    "financing.tranche.interest_rate": "금리",
    "financing.tranche.interest_rate_type": "금리 유형",
    "financing.tranche.maturity_date": "만기",
  };
  return candidateMetadata(fact).sourceFinancialContext === "current_bridge_snapshot"
    ? labels[fact.fieldKey] ?? fact.fieldKey : fact.fieldKey;
}

function bridgeReference(fact: WorkspaceFact) {
  const metadata = candidateMetadata(fact);
  return metadata.sourceFinancialContext === "current_bridge_snapshot" && metadata.referenceOnly === true;
}

function factBasisRequired(fact: WorkspaceFact) {
  return fact.candidate.validationCodes?.includes("FACT_BASIS_REQUIRED")
    || fact.issueCodes.includes("VALIDATION:FACT_BASIS_REQUIRED");
}

function factValue(fact: WorkspaceFact) {
  const value = fact.candidate.normalizedValue ?? fact.candidate.value;
  if (bridgeReference(fact)) {
    if (fact.fieldKey === "financing.tranche.loan_amount" && fact.candidate.unit?.toLowerCase() === "krw" && typeof value === "number") {
      return `${value.toLocaleString("ko-KR", { maximumFractionDigits: 0 })} 원`;
    }
    if (fact.fieldKey === "financing.tranche.interest_rate" && fact.candidate.unit === "ratio" && typeof value === "number") {
      return `${(value * 100).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}%`;
    }
    if (fact.fieldKey === "financing.tranche.interest_rate_type") {
      if (value === "floating") return "변동";
      if (value === "fixed") return "고정";
    }
    if (fact.fieldKey === "financing.tranche.name" && typeof value === "string") return value || "-";
    if (fact.fieldKey === "financing.tranche.maturity_date" && typeof value === "string" && /^\d{4}-\d{2}-\d{2}(?:T|$)/.test(value)) {
      return value.slice(0, 10);
    }
  }
  return formatValue(value, fact.candidate.unit);
}

function mergeWorkspaceDocuments(data: DocumentReviewData): WorkspaceDocument[] {
  const sourceByArchiveId = new Map<string, SourceDocumentRecord>();
  for (const source of data.sourceDocuments) {
    const archiveId = archiveIdOf(source);
    if (archiveId) sourceByArchiveId.set(archiveId, source);
  }

  const byVersion = new Map<string, WorkspaceDocument>();
  const matchedArchives = new Set<string>();
  for (const runPackage of data.packages) {
    const version = runPackage.documentVersion ?? {};
    const documentVersionId = identifier(version.documentVersionId ?? runPackage.parsingRun.documentVersionId);
    if (!documentVersionId) continue;
    const archiveId = identifier(version.archiveId ?? version.sha256 ?? version.sourceHash);
    const source = archiveId ? sourceByArchiveId.get(archiveId) : undefined;
    if (source && archiveId) matchedArchives.add(archiveId);
    const existing = byVersion.get(documentVersionId);
    const run = runPackage.parsingRun;
    const previousRun = existing?.latestRun;
    const isLatest = !previousRun || dateValue(run.requestedAt ?? run.createdAt) >= dateValue(previousRun.requestedAt ?? previousRun.createdAt);
    const runId = packageRunId(runPackage);
    const runIds = Array.from(new Set([...(existing?.runIds ?? []), ...(runId ? [runId] : [])]));
    byVersion.set(documentVersionId, {
      key: `version:${documentVersionId}`,
      archiveId: archiveId || undefined,
      documentId: identifier(version.documentId) || undefined,
      documentVersionId,
      versionNumber: finiteNumber(version.versionNumber),
      fileName: displayText(
        source?.originalFileName ?? source?.fileName ?? version.originalFileName ?? version.fileName,
        documentVersionId,
      ),
      documentType: identifier(source?.documentType) || undefined,
      mediaType: identifier(source?.contentType ?? source?.mediaType ?? version.mediaType) || undefined,
      sizeBytes: source ? sourceSize(source) : finiteNumber(version.sizeBytes),
      uploadedAt: source ? sourceDate(source) : identifier(version.ingestedAt) || undefined,
      validationStatus: identifier(source?.validationStatus) || undefined,
      integrityStatus: identifier(source?.archiveIntegrityStatus) || undefined,
      sourceAuthority: identifier(source?.sourceAuthority ?? version.sourceAuthority) || undefined,
      downloadUrl: safeSourceUrl(source?.downloadUrl) ?? undefined,
      runIds,
      parserRegistered: true,
      latestRun: isLatest ? run : previousRun,
      candidateCount: isLatest ? runPackage.candidates?.length : existing?.candidateCount,
    });
  }

  const documents = Array.from(byVersion.values());
  for (const source of data.sourceDocuments) {
    const archiveId = archiveIdOf(source);
    if (archiveId && matchedArchives.has(archiveId)) continue;
    documents.push({
      key: `archive:${archiveId || `${sourceFileName(source)}:${sourceDate(source) ?? "unknown"}`}`,
      archiveId: archiveId || undefined,
      fileName: sourceFileName(source),
      documentType: identifier(source.documentType) || undefined,
      mediaType: identifier(source.contentType ?? source.mediaType) || undefined,
      sizeBytes: sourceSize(source),
      uploadedAt: sourceDate(source),
      validationStatus: identifier(source.validationStatus) || undefined,
      integrityStatus: identifier(source.archiveIntegrityStatus) || undefined,
      sourceAuthority: identifier(source.sourceAuthority) || undefined,
      downloadUrl: safeSourceUrl(source.downloadUrl) ?? undefined,
      runIds: [],
      parserRegistered: false,
    });
  }

  return documents.sort((left, right) => dateValue(right.uploadedAt) - dateValue(left.uploadedAt));
}

function collectWorkspaceFacts(data: DocumentReviewData): WorkspaceFact[] {
  const facts: WorkspaceFact[] = [];
  const queueByCandidate = new Map<string, DocumentReviewQueueItem>();
  for (const item of data.reviewQueue ?? []) {
    const candidateId = identifier(item.candidateId);
    if (candidateId) queueByCandidate.set(candidateId, item);
  }
  for (const runPackage of data.packages) {
    const run = runPackage.parsingRun ?? {};
    const packageId = packageRunId(runPackage);
    for (const candidate of runPackage.candidates ?? []) {
      const candidateId = identifier(candidate.candidateId);
      if (!candidateId) continue;
      const decisions = (runPackage.reviewDecisions ?? [])
        .filter((item) => identifier(item.candidateId) === candidateId)
        .sort((left, right) => Number(left.decisionSequence ?? 0) - Number(right.decisionSequence ?? 0));
      const latestDecision = decisions.at(-1);
      const conflicts = (runPackage.conflicts ?? []).filter((item) =>
        (item.candidateIds ?? []).some((value) => identifier(value) === candidateId),
      );
      const effectiveConflict = conflicts.find((item) => identifier(item.conflictStatus) === "open")
        ?? conflicts.find((item) => identifier(item.conflictStatus) === "waived")
        ?? conflicts.at(-1);
      const documentVersionId = identifier(candidate.documentVersionId ?? run.documentVersionId);
      const runId = identifier(candidate.parsingRunId ?? packageId);
      const fieldKey = identifier(candidate.fieldKey ?? candidate.canonicalField) || "unmapped_field";
      const candidateSelections = (runPackage.canonicalSelections ?? [])
        .filter((item) => identifier(item.candidateId) === candidateId)
        .sort((left, right) => Number(left.selectionVersion ?? 0) - Number(right.selectionVersion ?? 0));
      const activeSelection = candidateSelections
        .filter((item) => identifier(item.validityStatus) === "active" && identifier(item.approvalStatus) === "approved")
        .at(-1);
      const candidateSelectionIds = new Set(candidateSelections.map((item) => identifier(item.selectionId)).filter(Boolean));
      const snapshotHistory = (runPackage.approvedInputSnapshots ?? []).filter((snapshot) =>
        (snapshot.selectionIds ?? []).map(identifier).some((selectionId) => candidateSelectionIds.has(selectionId)),
      );
      const activeSnapshot = snapshotHistory
        .filter((snapshot) =>
          identifier(snapshot.validityStatus) === "active"
          && identifier(snapshot.approvalStatus) === "approved"
          && Boolean(activeSelection)
          && (snapshot.selectionIds ?? []).map(identifier).includes(identifier(activeSelection?.selectionId)),
        )
        .sort((left, right) => Number(left.snapshotVersion ?? 0) - Number(right.snapshotVersion ?? 0))
        .at(-1);
      const supersededSnapshot = snapshotHistory.some((snapshot) =>
        identifier(snapshot.validityStatus) === "invalidated" || identifier(snapshot.approvalStatus) === "superseded",
      );
      const reviewApprovalStatus = identifier(latestDecision?.resultingApprovalStatus ?? candidate.approvalStatus) || "not_requested";
      const authorityStage = activeSnapshot
        ? "snapshot_approved"
        : supersededSnapshot
          ? "superseded"
          : activeSelection
            ? "canonical_selected"
            : reviewApprovalStatus === "approved"
              ? "reviewed"
              : "candidate";
      const queueItem = queueByCandidate.get(candidateId);
      facts.push({
        key: `${runId}:${candidateId}`,
        candidate,
        package: runPackage,
        run,
        candidateId,
        documentVersionId,
        runId,
        fieldKey,
        latestDecision,
        reviewStatus: identifier(latestDecision?.resultingReviewStatus ?? candidate.reviewStatus) || "pending",
        reviewApprovalStatus,
        snapshotApprovalStatus: activeSnapshot ? "approved" : supersededSnapshot ? "superseded" : "not_requested",
        authorityStage,
        activeSelection,
        activeSnapshot,
        validationStatus: identifier(candidate.validationStatus) || "unknown",
        validationOutcome: identifier(queueItem?.validationOutcome) || (identifier(candidate.validationStatus) === "passed" ? "passed" : "unknown"),
        conflictStatus: identifier(effectiveConflict?.conflictStatus ?? candidate.conflictStatus) || "none",
        conflicts,
        issueCodes: (queueItem?.issueCodes ?? []).map(identifier).filter(Boolean),
      });
    }
  }
  return facts.sort((left, right) => left.fieldKey.localeCompare(right.fieldKey));
}

function dedupeConflicts(data: DocumentReviewData) {
  const conflicts = new Map<string, SourceConflictRecord>();
  for (const runPackage of data.packages) {
    for (const conflict of runPackage.conflicts ?? []) {
      const id = identifier(conflict.conflictId);
      if (id) conflicts.set(id, conflict);
    }
  }
  return Array.from(conflicts.values()).sort((left, right) => {
    const openDelta = Number(identifier(right.conflictStatus) === "open") - Number(identifier(left.conflictStatus) === "open");
    if (openDelta) return openDelta;
    return dateValue(right.detectedAt) - dateValue(left.detectedAt);
  });
}

function collectLocatorAnchors(facts: WorkspaceFact[]): LocatorAnchor[] {
  const anchors = new Map<string, LocatorAnchor>();
  for (const fact of facts) {
    const evidence = fact.candidate.evidence?.length
      ? fact.candidate.evidence
      : fact.candidate.sourceLocator
        ? [fact.candidate.sourceLocator]
        : [];
    for (const [index, locator] of evidence.entries()) {
      const semanticKey = sourceLocatorSemanticKey(locator, fact.documentVersionId);
      const key = semanticKey
        ? `${fact.documentVersionId}:${semanticKey}`
        : `${fact.documentVersionId}:unkeyed:${fact.key}:${index}`;
      const existing = anchors.get(key);
      if (existing) {
        existing.factKeys = Array.from(new Set([...existing.factKeys, fact.key]));
        existing.detail = `관련 Fact ${existing.factKeys.length}건`;
      } else {
        anchors.set(key, {
          key,
          label: locatorLabel(locator),
          detail: "관련 Fact 1건",
          locator,
          factKeys: [fact.key],
        });
      }
    }
  }
  return Array.from(anchors.values());
}

function factHasLocator(fact: WorkspaceFact, semanticKey: string) {
  const evidence = fact.candidate.evidence?.length
    ? fact.candidate.evidence
    : fact.candidate.sourceLocator
      ? [fact.candidate.sourceLocator]
      : [];
  return evidence.some(
    (locator) =>
      sourceLocatorSemanticKey(locator, fact.documentVersionId) === semanticKey,
  );
}

function sameLocator(
  left: SourceLocatorRecord,
  right: SourceLocatorRecord,
  fallbackDocumentVersionId?: string,
) {
  if (left === right) return true;
  const leftKey = sourceLocatorSemanticKey(left, fallbackDocumentVersionId);
  const rightKey = sourceLocatorSemanticKey(right, fallbackDocumentVersionId);
  return Boolean(leftKey && rightKey && leftKey === rightKey);
}

function locatorLabel(locator: SourceLocatorRecord) {
  const page = finiteNumber(locator.pageNumber ?? locator.page);
  const sheet = identifier(locator.sheetName ?? locator.sheet);
  const cell = identifier(locator.cellReference ?? locator.cellRange ?? locator.cell);
  const table = identifier(locator.tableId);
  const row = finiteNumber(locator.rowIndex ?? locator.row);
  const column = finiteNumber(locator.columnIndex ?? locator.column);
  if (sheet && cell) return `${sheet}!${cell}`;
  if (page !== undefined && table) {
    const suffix = [row !== undefined ? `행 ${row}` : "", column !== undefined ? `열 ${column}` : ""].filter(Boolean).join(" · ");
    return `p.${page} · ${table}${suffix ? ` · ${suffix}` : ""}`;
  }
  if (page !== undefined) return `p.${page}`;
  if (identifier(locator.jsonPath)) return identifier(locator.jsonPath);
  if (identifier(locator.apiRowId)) return `API row ${identifier(locator.apiRowId)}`;
  if (identifier(locator.artifactKey)) return `Artifact ${identifier(locator.artifactKey)}`;
  return identifier(locator.evidenceId) || "정확 위치 식별자";
}

function finiteNumber(value: unknown) {
  const parsed = Number(value);
  return value !== null && value !== "" && Number.isFinite(parsed) ? parsed : undefined;
}

function dateValue(value: unknown) {
  const parsed = Date.parse(identifier(value));
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatDate(value: unknown) {
  const raw = identifier(value);
  if (!raw) return "-";
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? raw : date.toLocaleString("ko-KR");
}

function formatBytes(value?: number) {
  if (value === undefined) return "-";
  if (value < 1024) return `${value.toLocaleString("ko-KR")} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatValue(value: unknown, unit?: string) {
  let rendered = "-";
  if (typeof value === "number") rendered = value.toLocaleString("ko-KR", { maximumFractionDigits: 6 });
  else if (typeof value === "string") rendered = value || "-";
  else if (typeof value === "boolean") rendered = value ? "true" : "false";
  else if (value !== null && value !== undefined) rendered = JSON.stringify(value);
  return unit && rendered !== "-" ? `${rendered} ${unit}` : rendered;
}

function statusTone(status: string): "neutral" | "info" | "good" | "warn" | "bad" {
  const normalized = status.toLowerCase();
  if (["approved", "completed", "passed", "resolved", "verified", "active", "snapshot_approved"].includes(normalized)) return "good";
  if (["pending", "running", "in_review", "not_requested", "none", "unknown"].includes(normalized)) return "neutral";
  if (["needs_revision", "completed_with_warnings", "warning", "waived", "draft", "reviewed", "canonical_selected", "medium", "changed"].includes(normalized)) return "warn";
  if (["failed", "rejected", "blocked", "blocking", "high", "open", "critical", "invalid", "removed"].includes(normalized)) return "bad";
  return "info";
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    approved: "승인",
    completed: "완료",
    completed_with_warnings: "경고 포함 완료",
    passed: "통과",
    resolved: "해결",
    verified: "검증",
    active: "유효",
    pending: "대기",
    running: "실행 중",
    in_review: "검토 중",
    not_requested: "미요청",
    none: "없음",
    unknown: "미확인",
    needs_revision: "보완 필요",
    warning: "경고",
    waived: "Waiver",
    failed: "실패",
    rejected: "반려",
    blocked: "차단",
    open: "열림",
    superseded: "대체됨",
    snapshot_approved: "Snapshot 승인",
    canonical_selected: "Canonical 선택",
    reviewed: "검토 승인만",
    candidate: "후보",
    blocking: "차단",
    high: "높음",
    medium: "중간",
    added: "추가",
    removed: "삭제",
    changed: "변경",
  };
  return labels[status] ?? status;
}

export function DocumentReviewWorkspace({
  projectId,
  requestedArchiveId,
}: {
  projectId: string;
  requestedArchiveId?: string | null;
}) {
  const [data, setData] = useState<DocumentReviewData | null>(null);
  const activeLoad = useRef<AbortController | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [documentSelection, setDocumentSelection] = useState<Pick<WorkspaceDocument, "key" | "archiveId"> | null>(null);
  const [selectedFactKey, setSelectedFactKey] = useState<string | null>(null);
  const [selectedLocatorKey, setSelectedLocatorKey] = useState<string | null>(null);
  const [sourceFactFilterKey, setSourceFactFilterKey] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<InspectorTab>("facts");
  const [factQuery, setFactQuery] = useState("");
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [reviewDrafts, setReviewDrafts] = useState<Record<string, string>>({});
  const [reviewBusy, setReviewBusy] = useState(false);
  const reviewWriteInFlight = useRef<string | null>(null);
  const [reviewDataFresh, setReviewDataFresh] = useState(false);
  const [reviewRefreshRequired, setReviewRefreshRequired] = useState<"saved" | "missing" | "uncertain" | null>(null);
  const savedReviewReceipt = useRef<{
    projectId: string; candidateId: string; decisionId: string; draftKey: string; reason: string;
  } | null>(null);
  const activeProject = useRef(projectId);
  activeProject.current = projectId;
  const [conflictBusyId, setConflictBusyId] = useState<string | null>(null);
  const [glossaryTerms, setGlossaryTerms] = useState<GlossaryTermRecord[]>([]);
  const [glossaryLoading, setGlossaryLoading] = useState(false);
  const [glossaryError, setGlossaryError] = useState<string | null>(null);

  const load = useCallback(async () => {
    activeLoad.current?.abort();
    const controller = new AbortController();
    activeLoad.current = controller;
    setLoading(true);
    setReviewDataFresh(false);
    setError(null);
    setNotice(null);
    try {
      const reviewData = await fetchDocumentReviewData(projectId, controller.signal);
      if (controller.signal.aborted || activeProject.current !== projectId) return false;
      setData(reviewData);
      const receipt = savedReviewReceipt.current?.projectId === projectId ? savedReviewReceipt.current : null;
      if (receipt) {
        const confirmed = reviewData.packages.some((runPackage) => runPackage.reviewDecisions.some((decision) =>
          identifier(decision.reviewDecisionId) === receipt.decisionId && identifier(decision.candidateId) === receipt.candidateId));
        if (!confirmed) {
          setReviewRefreshRequired("missing");
          return false;
        }
        setReviewDrafts((drafts) => {
          if (drafts[receipt.draftKey]?.trim() !== receipt.reason) return drafts;
          const next = { ...drafts };
          delete next[receipt.draftKey];
          return next;
        });
        savedReviewReceipt.current = null;
        setNotice("저장된 검토 결정을 최신 기록에서 확인했습니다.");
      }
      setReviewDataFresh(true);
      setReviewRefreshRequired(null);
      return true;
    } catch {
      if (!controller.signal.aborted && activeProject.current === projectId) setError("문서 목록과 파싱 결과를 불러오지 못했습니다.");
      return false;
    } finally {
      if (!controller.signal.aborted && activeProject.current === projectId) setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    return () => activeLoad.current?.abort();
  }, [load]);

  const documents = useMemo(() => (data ? mergeWorkspaceDocuments(data) : []), [data]);
  const facts = useMemo(() => (data ? collectWorkspaceFacts(data) : []), [data]);
  const conflicts = useMemo(() => (data ? dedupeConflicts(data) : []), [data]);

  const linkedDocument = requestedArchiveId
    && data?.sourceDocuments.some((source) => archiveIdOf(source).toLowerCase() === requestedArchiveId)
    ? documents.find((item) => item.archiveId?.toLowerCase() === requestedArchiveId)
    : undefined;
  const selectedDocument = documents.find((item) => item.key === documentSelection?.key)
    ?? (documentSelection?.archiveId ? documents.find((item) => item.archiveId === documentSelection.archiveId) : undefined)
    ?? linkedDocument
    ?? documents.at(0);
  const documentFacts = useMemo(() => {
    if (!selectedDocument?.documentVersionId) return [];
    return facts.filter((fact) => fact.documentVersionId === selectedDocument.documentVersionId);
  }, [facts, selectedDocument?.documentVersionId]);

  useEffect(() => {
    const available = documentFacts;
    if (!available.length) {
      setSelectedFactKey(null);
      return;
    }
    if (!selectedFactKey || !available.some((item) => item.key === selectedFactKey)) {
      setSelectedFactKey(available.at(0)?.key ?? null);
    }
  }, [documentFacts, selectedFactKey]);

  const selectedFact = documentFacts.find((item) => item.key === selectedFactKey) ?? documentFacts.at(0);
  const selectedReviewKey = selectedFact ? reviewDraftKey(projectId, selectedFact) : null;
  const selectedReviewKeyRef = useRef(selectedReviewKey);
  selectedReviewKeyRef.current = selectedReviewKey;
  const reviewReason = selectedReviewKey ? reviewDrafts[selectedReviewKey] ?? "" : "";
  const changeReviewReason = useCallback((reason: string) => {
    if (selectedReviewKey) setReviewDrafts((drafts) => ({ ...drafts, [selectedReviewKey]: reason }));
  }, [selectedReviewKey]);
  const documentAnchors = useMemo(() => collectLocatorAnchors(documentFacts), [documentFacts]);
  const selectedEvidence = useMemo(() => {
    if (!selectedFact) return [];
    if (selectedFact.candidate.evidence?.length) return selectedFact.candidate.evidence;
    return selectedFact.candidate.sourceLocator ? [selectedFact.candidate.sourceLocator] : [];
  }, [selectedFact]);
  const selectedLocator = useMemo(() => {
    if (!selectedEvidence.length) return undefined;
    if (selectedLocatorKey) {
      const matched = selectedEvidence.find((locator, index) => locatorKey(selectedFact?.key ?? "fact", locator, index) === selectedLocatorKey);
      if (matched) return matched;
    }
    return selectedFact ? preferredFactLocator(selectedFact, selectedEvidence) : undefined;
  }, [selectedEvidence, selectedFact, selectedLocatorKey]);

  useEffect(() => {
    if (!selectedFact || !selectedEvidence.length) {
      setSelectedLocatorKey(null);
      return;
    }
    const stillPresent = selectedLocatorKey
      ? selectedEvidence.some((locator, index) => locatorKey(selectedFact.key, locator, index) === selectedLocatorKey)
      : false;
    const preferred = preferredFactLocator(selectedFact, selectedEvidence);
    if (!stillPresent && preferred) setSelectedLocatorKey(locatorKey(selectedFact.key, preferred, selectedEvidence.indexOf(preferred)));
  }, [selectedEvidence, selectedFact, selectedLocatorKey]);

  useEffect(() => {
    const fieldKey = selectedFact?.fieldKey ?? "";
    if (!fieldKey) {
      setGlossaryTerms([]);
      return;
    }
    let active = true;
    setGlossaryLoading(true);
    setGlossaryError(null);
    void fetchGlossaryTermsForField(fieldKey)
      .then((terms) => {
        if (active) setGlossaryTerms(terms);
      })
      .catch((caught) => {
        if (active) {
          setGlossaryTerms([]);
          setGlossaryError(errorMessage(caught));
        }
      })
      .finally(() => {
        if (active) setGlossaryLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedFact?.fieldKey]);

  const selectFact = useCallback((fact: WorkspaceFact, locator?: SourceLocatorRecord, locatorIndex = 0) => {
    setSelectedFactKey(fact.key);
    const document = documents.find((item) => item.documentVersionId === fact.documentVersionId);
    if (document) {
      if (document.key !== selectedDocument?.key) {
        setSourceFactFilterKey(null);
        setFactQuery("");
        setIssuesOnly(false);
      }
      setDocumentSelection({ key: document.key, archiveId: document.archiveId });
    }
    const evidence = fact.candidate.evidence?.length
      ? fact.candidate.evidence
      : fact.candidate.sourceLocator ? [fact.candidate.sourceLocator] : [];
    const preferred = locator ?? preferredFactLocator(fact, evidence);
    setSelectedLocatorKey(preferred ? locatorKey(fact.key, preferred, locator ? locatorIndex : evidence.indexOf(preferred)) : null);
  }, [documents, selectedDocument?.key]);

  const showRelatedFacts = useCallback((locator: SourceLocatorRecord) => {
    const semanticKey = sourceLocatorSemanticKey(
      locator,
      selectedFact?.documentVersionId,
    );
    if (!semanticKey) return;
    setSourceFactFilterKey(semanticKey);
    const related = facts.filter((fact) => factHasLocator(fact, semanticKey));
    const first = related.at(0);
    if (first) {
      const evidence = first.candidate.evidence?.length
        ? first.candidate.evidence
        : first.candidate.sourceLocator
          ? [first.candidate.sourceLocator]
          : [];
      const locatorIndex = Math.max(
        0,
        evidence.findIndex(
          (item) =>
            sourceLocatorSemanticKey(item, first.documentVersionId) === semanticKey,
        ),
      );
      selectFact(first, locator, locatorIndex);
    }
    setActiveTab("facts");
  }, [facts, selectFact, selectedFact?.documentVersionId]);

  const saveReview = useCallback(async (action: "approve" | "reject" | "request_revision") => {
    if (!selectedFact || reviewWriteInFlight.current || loading || !reviewDataFresh
      || reviewRefreshRequired || data?.projectId !== projectId) return;
    const draftKey = reviewDraftKey(projectId, selectedFact);
    const reason = reviewReason.trim();
    if (!reason) {
      setError("검토 결정에는 구체적인 근거 또는 보완 사유가 필요합니다.");
      return;
    }
    reviewWriteInFlight.current = draftKey;
    setReviewBusy(true);
    setError(null);
    setNotice(null);
    try {
      const decisions = selectedFact.package.reviewDecisions.filter(
        (item) => identifier(item.candidateId) === selectedFact.candidateId,
      );
      const savedDecision = await recordParserV2Review({
        projectId,
        candidateId: selectedFact.candidateId,
        expectedDecisionSequence: decisions.length,
        action,
        reason,
        conflictIds: selectedFact.conflicts.map((item) => identifier(item.conflictId)).filter(Boolean),
      });
      savedReviewReceipt.current = { projectId, candidateId: selectedFact.candidateId,
        decisionId: identifier(savedDecision.reviewDecisionId), draftKey, reason };
      if (activeProject.current !== projectId) return;
      setReviewRefreshRequired("saved");
      setReviewDataFresh(false);
      const savedNotice =
        action === "approve"
          ? "검토 승인 결정이 append-only 원장에 저장되었습니다. Canonical selection과 승인 snapshot은 별도 단계입니다."
          : action === "request_revision"
            ? "보완 요청 결정이 append-only 원장에 저장되었습니다."
            : "반려 결정이 append-only 원장에 저장되었습니다.";
      const reloaded = await load();
      if (activeProject.current !== projectId) return;
      if (reloaded) {
        setNotice(savedNotice);
        if (selectedReviewKeyRef.current === draftKey) setActiveTab("audit");
      }
    } catch (caught) {
      if (activeProject.current === projectId) {
        setError(errorMessage(caught));
        if (caught instanceof ParserReviewSaveError && caught.requiresReload) {
          setReviewRefreshRequired("uncertain");
          setReviewDataFresh(false);
        }
      }
    } finally {
      reviewWriteInFlight.current = null;
      setReviewBusy(false);
    }
  }, [data?.projectId, load, loading, projectId, reviewDataFresh, reviewReason, reviewRefreshRequired, selectedFact]);

  const resolveConflict = useCallback(async (conflict: SourceConflictRecord) => {
    const conflictId = identifier(conflict.conflictId);
    const decision = selectedFact?.latestDecision;
    const decisionId = identifier(decision?.reviewDecisionId);
    const sequence = Number(decision?.decisionSequence ?? 0);
    if (!selectedFact || !conflictId || !decisionId || decision?.action !== "approve" || sequence < 1) {
      setError("충돌 해결에는 해당 후보의 최신 승인 ReviewDecision이 필요합니다.");
      return;
    }
    setConflictBusyId(conflictId);
    setError(null);
    setNotice(null);
    try {
      await resolveParserV2Conflict({
        projectId,
        conflictId,
        reviewDecisionId: decisionId,
        expectedDecisionSequence: sequence,
        notes: `문서 검토 화면에서 승인 decision ${decisionId}을 winning candidate로 적용`,
      });
      await load();
      setNotice("승인된 winning-candidate ReviewDecision으로 SourceConflict 해결 이벤트를 저장했습니다.");
      setActiveTab("audit");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setConflictBusyId(null);
    }
  }, [load, projectId, selectedFact]);

  const filteredFacts = useMemo(() => {
    const documentSource = selectedDocument ? documentFacts : facts;
    const source = sourceFactFilterKey
      ? documentSource.filter((fact) => factHasLocator(fact, sourceFactFilterKey))
      : documentSource;
    const query = factQuery.trim().toLowerCase();
    return source.filter((fact) => {
      if (issuesOnly && !fact.issueCodes.length) return false;
      if (!query) return true;
      return [fact.fieldKey, factLabel(fact), fact.candidateId, factValue(fact)]
        .join(" ")
        .toLowerCase()
        .includes(query);
    });
  }, [documentFacts, factQuery, facts, issuesOnly, selectedDocument, sourceFactFilterKey]);

  const glossaryCandidates = useMemo(
    () => (data?.glossaryCandidates ?? []).filter((candidate) => {
      if (!selectedFact) return false;
      return [candidate.sourceField, candidate.suggestedCanonicalField]
        .map(identifier)
        .includes(selectedFact.fieldKey);
    }),
    [data?.glossaryCandidates, selectedFact],
  );

  const auditItems = useMemo(() => collectAuditItems(data), [data]);
  const openConflictCount = conflicts.filter((item) => identifier(item.conflictStatus) === "open").length;
  const reviewPendingCount = facts.filter((item) => ["pending", "in_review"].includes(item.reviewStatus)).length;
  const approvedCount = facts.filter((item) => item.snapshotApprovalStatus === "approved").length;
  const extractionNotStarted = documents.filter((item) => !item.latestRun).length;
  const extractionEmpty = documents.filter((item) => ["completed", "completed_with_warnings"].includes(identifier(item.latestRun?.executionStatus)) && item.candidateCount === 0).length;
  const extractionFailed = documents.filter((item) => ["failed", "cancelled"].includes(identifier(item.latestRun?.executionStatus))).length;

  if (loading && !data) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center rounded-xl border border-slate-200 bg-white text-sm font-bold text-slate-500 dark:border-slate-800 dark:bg-slate-950">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" /> 문서 검토 패키지를 불러오는 중입니다.
      </div>
    );
  }

  return (
    <div className="space-y-4 pb-8">
      <header className={heroStyles.hero}>
        <DashboardAtmosphere className={heroStyles.atmosphere} testId="document-review-parallax" strength={0.06} />
        <div className="relative grid gap-5 p-5 sm:p-6 lg:grid-cols-[minmax(0,1fr)_20rem] lg:items-center">
          <div className="min-w-0">
            <ProjectWorkspaceLink projectId={projectId} path="input-package" className="inline-flex items-center gap-2 text-sm text-slate-200 hover:text-white" aria-label="자료 입력 패키지로 돌아가기">
              <ArrowLeft className="h-4 w-4" aria-hidden="true" /> 자료 입력
            </ProjectWorkspaceLink>
            <h1 className="mt-3 text-2xl font-bold tracking-tight text-white">문서 검토</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-200">
              {documents.length
                ? documentFacts.length
                  ? "원본과 파싱값을 대조한 뒤, 확인할 항목의 검토 의견을 남기세요."
                  : "문서를 선택해 원본을 읽고, 필요한 값을 추출해 검토하세요. Excel은 시트·셀, PDF는 페이지별로 확인할 수 있습니다."
                : "자료를 올리면 이곳에서 원본과 파싱 결과를 함께 확인할 수 있습니다."}
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              {documents.length ? <a href="#document-source-view" className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-white px-4 text-sm font-bold text-slate-950 hover:bg-slate-100">
                <FileText className="h-4 w-4" aria-hidden="true" /> 원본 확인하기
              </a> : <ProjectWorkspaceLink projectId={projectId} path="input-package" className="inline-flex min-h-10 items-center rounded-lg bg-white px-4 text-sm font-bold text-slate-950">자료 올리기</ProjectWorkspaceLink>}
              <button type="button" onClick={() => void load()} disabled={loading || reviewBusy} className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-white/30 px-3 text-sm text-white hover:bg-white/10 disabled:opacity-50">
                {loading ? <Loader2 className="h-4 w-4 motion-safe:animate-spin" /> : <RefreshCw className="h-4 w-4" />} 최신 원장 새로고침
              </button>
            </div>
          </div>
          <div className={heroStyles.glass} data-testid="document-review-glass">
            <dl className="grid grid-cols-2 gap-4 text-white">
              <div><dt className="text-xs text-slate-200">보관 문서</dt><dd className="mt-1 text-2xl font-semibold">{countLabel(data?.sourceDocuments.length)}</dd>{!data ? <p className="text-xs text-slate-200">조회 불가</p> : null}</div>
              <div><dt className="text-xs text-slate-200">미검토 후보</dt><dd className="mt-1 text-2xl font-semibold">{countLabel(data ? reviewPendingCount : undefined)}</dd></div>
            </dl>
            <p className="mt-3 border-t border-white/20 pt-3 text-xs leading-5 text-slate-100">
              {data ? "파싱값 " + facts.length + "건 · 미검토 " + reviewPendingCount + "건 · 승인된 입력값 " + approvedCount + "건" : "저장 상태를 다시 확인해 주세요."}
              {openConflictCount ? " · 값 충돌 " + openConflictCount + "건" : ""}
            </p>
            {data && !facts.length ? <p className="mt-2 text-xs leading-5 text-slate-100">
              저장된 검토 후보가 아직 없습니다.{extractionNotStarted ? ` 후보 추출 전 ${extractionNotStarted}개.` : ""}{extractionEmpty ? ` 읽기 완료·검토 후보 없음 ${extractionEmpty}개.` : ""}{extractionFailed ? ` 추출 실패 ${extractionFailed}개.` : ""} 승인된 입력값은 검토·승인 후 생성됩니다.
            </p> : null}
          </div>
        </div>
      </header>
      {data ? <details className="rounded-lg border border-slate-200 bg-white text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-950">
        <summary className="cursor-pointer px-4 py-3 font-semibold">조회 기준·진단 정보</summary>
        <div className="flex flex-wrap gap-3 border-t border-slate-200 px-4 py-3 dark:border-slate-800">
          <span>서버 Read Model {data.viewModelVersion}</span><span>기준 시각 {formatDate(data.asOfDate)}</span>
          <span className="break-all font-mono">snapshot {data.snapshotHash.slice(0, 16)}…</span>
          <span>freshness {identifier(data.freshness?.status) || "unknown"}</span>
        </div>
      </details> : null}

      {error && !reviewRefreshRequired ? <div role="alert"><Notice tone="bad">
        {error} {data ? "마지막으로 확인한 결과를 표시합니다." : "새로고침해서 원본과 파싱 상태를 다시 확인해 주세요."}
      </Notice></div> : null}
      {notice ? <Notice tone="good">{notice}</Notice> : null}
      {reviewRefreshRequired ? <div role={loading || reviewBusy ? "status" : "alert"}><Notice tone="warn">
        {reviewRefreshRequired === "uncertain" && error ? <p>{error}</p> : null}
        <p>{reviewRefreshRequired === "saved"
          ? loading || reviewBusy ? "검토 결정이 저장되었습니다. 최신 검토 상태를 확인하고 있습니다."
            : "검토 결정은 저장되었지만 최신 검토 상태를 다시 불러오지 못했습니다. 저장 상태를 다시 확인해 주세요."
          : reviewRefreshRequired === "missing" ? "검토 결정은 저장되었지만 최신 목록에서 해당 결정을 아직 확인하지 못했습니다. 저장 상태를 다시 확인해 주세요."
            : "검토 결정의 최신 저장 상태를 확인해야 합니다. 중복 제출을 막기 위해 다시 불러온 뒤 검토를 이어갑니다."}</p>
        <button type="button" onClick={() => void load()} disabled={loading || reviewBusy}
          className="mt-2 min-h-9 rounded-lg border border-amber-400 px-3 text-xs font-bold disabled:opacity-40">저장 상태 다시 확인</button>
      </Notice></div> : null}
      {data?.projectId === projectId && data.sourceDocuments.length > 0 ? (
        <ArchiveExtractionAction compact projectId={projectId} originals={data}
          disabled={loading || Boolean(error) || reviewBusy} onComplete={() => void load()} />
      ) : null}
      {data && documents.some((item) => item.mediaType === "application/pdf" && item.latestRun?.executionStatus === "completed" && item.candidateCount === 0) ? (
        <p className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300">
          파싱이 완료되어도 검토 후보가 없는 PDF가 있습니다. 원본을 열고 필요한 페이지에서 글자 읽기를 사용할 수 있습니다.
        </p>
      ) : null}
      {data?.warnings.filter((warning) => warning !== "DOCUMENT_GLOSSARY_UNAVAILABLE").map((warning) => <Notice key={warning} tone="warn">부분 로드: {warning}</Notice>)}
      {requestedArchiveId === null ? (
        <Notice tone="warn">문서 링크 형식이 올바르지 않습니다. 목록에서 검토할 문서를 선택하세요.</Notice>
      ) : requestedArchiveId && data && !linkedDocument ? (
        <Notice tone="warn">이 프로젝트의 문서 목록에서 링크된 문서를 찾을 수 없습니다.</Notice>
      ) : null}

      {data && !documents.length && !facts.length ? (
        <ControlledEmpty
          icon={<FileQuestion className="h-7 w-7" />}
          title="검토할 문서와 Parser V2 후보값이 없습니다."
          body="자료 입력에서 문서를 올리면 원본과 추출한 값을 이곳에서 확인할 수 있습니다."
        />
      ) : data ? (
        <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(19rem,25rem)]">
          <DocumentNavigator
            documents={documents}
            selectedDocument={selectedDocument}
            anchors={documentAnchors}
            selectedLocator={selectedLocator}
            onSelectDocument={(document) => {
              setSourceFactFilterKey(null);
              setFactQuery("");
              setIssuesOnly(false);
              setDocumentSelection({ key: document.key, archiveId: document.archiveId });
              const firstFact = facts.find((fact) => fact.documentVersionId === document.documentVersionId);
              if (firstFact) selectFact(firstFact);
            }}
            onSelectAnchor={(anchor) => {
              showRelatedFacts(anchor.locator);
            }}
          />

          <SourcePreview
            projectId={projectId}
            document={selectedDocument}
            fact={selectedFact}
            locator={selectedLocator}
            onShowEvidence={() => setActiveTab("evidence")}
            onSelectSourceLocator={showRelatedFacts}
          />

          <Inspector
            activeTab={activeTab}
            onTabChange={setActiveTab}
            facts={filteredFacts}
            selectedFact={selectedFact}
            onSelectFact={(fact) => {
              setSourceFactFilterKey(null);
              selectFact(fact);
            }}
            factQuery={factQuery}
            onFactQueryChange={setFactQuery}
            issuesOnly={issuesOnly}
            onIssuesOnlyChange={setIssuesOnly}
            sourceFilterLabel={sourceFactFilterKey && selectedLocator ? locatorLabel(selectedLocator) : undefined}
            onClearSourceFilter={() => setSourceFactFilterKey(null)}
            selectedEvidence={selectedEvidence}
            selectedLocator={selectedLocator}
            onSelectLocator={(locator, index) => selectedFact && setSelectedLocatorKey(locatorKey(selectedFact.key, locator, index))}
            conflicts={conflicts}
            conflictBusyId={conflictBusyId}
            onResolveConflict={resolveConflict}
            reviewQueue={data?.reviewQueue ?? []}
            allFacts={facts}
            glossaryTerms={glossaryTerms}
            glossaryCandidates={glossaryCandidates}
            glossaryLoading={glossaryLoading}
            glossaryError={glossaryError ?? (data.warnings.includes("DOCUMENT_GLOSSARY_UNAVAILABLE") ? "용어집 후보는 현재 서버에서 조회할 수 없습니다." : null)}
            reviewReason={reviewReason}
            onReviewReasonChange={changeReviewReason}
            reviewBusy={reviewBusy}
            reviewDisabled={loading || !reviewDataFresh || Boolean(reviewRefreshRequired) || data.projectId !== projectId}
            onSaveReview={saveReview}
            snapshotDiffs={data?.snapshotDiffs ?? []}
            auditItems={auditItems}
          />
        </div>
      ) : null}
    </div>
  );
}

function locatorKey(factKey: string, locator: SourceLocatorRecord, index: number) {
  return `${factKey}:${identifier(locator.evidenceId) || sourceLocatorSemanticKey(locator) || index}`;
}

function DocumentNavigator({
  documents,
  selectedDocument,
  anchors,
  selectedLocator,
  onSelectDocument,
  onSelectAnchor,
}: {
  documents: WorkspaceDocument[];
  selectedDocument?: WorkspaceDocument;
  anchors: LocatorAnchor[];
  selectedLocator?: SourceLocatorRecord;
  onSelectDocument: (document: WorkspaceDocument) => void;
  onSelectAnchor: (anchor: LocatorAnchor) => void;
}) {
  return (
    <aside className="min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-950 xl:col-span-2" aria-label="문서 및 위치 탐색">
      <div className="border-b border-slate-200 px-4 py-3 dark:border-slate-800">
        <p className="text-sm font-semibold text-slate-950 dark:text-white">1. 검토할 문서를 선택하세요 <span className="ml-2 text-xs font-normal text-slate-500">{documents.length}개</span></p>
      </div>
      <div className="grid max-h-56 gap-2 overflow-y-auto p-3 sm:grid-cols-2 xl:grid-cols-3">
        {documents.map((document) => {
          const selected = selectedDocument?.key === document.key;
          const spreadsheet = /spreadsheet|excel|sheet/i.test(`${document.mediaType} ${document.fileName}`);
          return (
            <button
              key={document.key}
              type="button"
              aria-pressed={selected}
              onClick={() => onSelectDocument(document)}
              className={`mb-1 flex w-full items-start gap-2 rounded-lg border px-3 py-3 text-left transition ${
                selected
                  ? "border-indigo-300 bg-indigo-50 text-indigo-950 dark:border-indigo-700 dark:bg-indigo-950/40 dark:text-indigo-100"
                  : "border-transparent text-slate-700 hover:border-slate-200 hover:bg-slate-50 dark:text-slate-200 dark:hover:border-slate-800 dark:hover:bg-slate-900"
              }`}
            >
              {spreadsheet ? <FileSpreadsheet className="mt-0.5 h-4 w-4 shrink-0" /> : <FileText className="mt-0.5 h-4 w-4 shrink-0" />}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-black">{document.fileName}</span>
                <span className="mt-1 block text-[11px] text-slate-500">
                  {document.mediaType === "application/pdf" ? "PDF 문서" : /spreadsheet|excel|sheet/i.test(`${document.mediaType} ${document.fileName}`) ? "엑셀 문서" : document.documentType || "보관 문서"}
                  {document.versionNumber ? ` · v${document.versionNumber}` : ""}
                </span>
                <span className="mt-1 flex flex-wrap gap-1">
                  {document.latestRun ? (
                    <><StatusPill label={document.latestRun.profileId === "realytics.source_review_candidates" ? "값 추출" : "문서 읽기"} status={identifier(document.latestRun.executionStatus) || "unknown"} />
                    {document.candidateCount !== undefined ? <span className="self-center text-[11px] text-slate-500">{document.candidateCount ? `검토 후보 ${document.candidateCount}건` : "검토 후보 없음"}</span> : null}</>
                  ) : <span className="text-[11px] text-slate-500">원본 보관 · 값 추출 전</span>}
                </span>
              </span>
            </button>
          );
        })}
      </div>

      <details className="border-t border-slate-200 dark:border-slate-800">
        <summary className="cursor-pointer px-4 py-3 text-xs font-semibold text-slate-600">문서 안의 근거 위치 {anchors.length}곳</summary>
      <div className="grid max-h-52 gap-2 overflow-y-auto p-3 sm:grid-cols-2 lg:grid-cols-3">
        {anchors.length ? (
          anchors.map((anchor) => {
            const selected = selectedLocator
              ? sameLocator(
                  selectedLocator,
                  anchor.locator,
                  selectedDocument?.documentVersionId,
                )
              : false;
            return (
              <button
                key={anchor.key}
                type="button"
                onClick={() => onSelectAnchor(anchor)}
                style={{ contentVisibility: "auto", containIntrinsicSize: "56px" }}
                className={`mb-1 flex w-full items-start gap-2 rounded-lg px-3 py-2 text-left ${
                  selected ? "bg-slate-950 text-white dark:bg-white dark:text-slate-950" : "hover:bg-slate-50 dark:hover:bg-slate-900"
                }`}
              >
                <LocateFixed className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span className="min-w-0">
                  <span className="block text-xs font-black">{anchor.label}</span>
                  <span className={`mt-0.5 block truncate text-[11px] ${selected ? "text-slate-300 dark:text-slate-600" : "text-slate-500"}`}>
                    {anchor.detail}
                  </span>
                </span>
              </button>
            );
          })
        ) : (
          <div className="m-2 rounded-lg border border-dashed border-slate-300 p-4 text-xs leading-5 text-slate-500 dark:border-slate-700">
            연결된 파싱 근거가 아직 없습니다. 아래 원본에서 내용을 확인할 수 있습니다.
          </div>
        )}
      </div>
      </details>
    </aside>
  );
}

function SourcePreview({
  projectId,
  document,
  fact,
  locator,
  onShowEvidence,
  onSelectSourceLocator,
}: {
  projectId: string;
  document?: WorkspaceDocument;
  fact?: WorkspaceFact;
  locator?: SourceLocatorRecord;
  onShowEvidence: () => void;
  onSelectSourceLocator: (locator: SourceLocatorRecord) => void;
}) {
  const archiveId = identifier(document?.archiveId);
  const mediaIdentity = `${document?.mediaType ?? ""} ${document?.fileName ?? ""}`;
  const isPdf = /application\/pdf|\.pdf$/i.test(mediaIdentity);
  const isWorkbook = /spreadsheet|excel|\.xlsx$|\.xlsm$/i.test(mediaIdentity);
  const contentUrl = archiveId && (isPdf || isWorkbook)
    ? sourceDocumentContentUrl(projectId, archiveId)
    : null;
  const locatorBound = locator && document
    ? sourceLocatorMatchesDocument(locator, document)
    : false;
  return (
    <main id="document-source-view" className="flex min-w-0 scroll-mt-20 flex-col overflow-hidden rounded-xl border border-slate-200 bg-slate-100 shadow-sm dark:border-slate-800 dark:bg-slate-900" aria-label="원본 위치 미리보기">
      <div className="flex flex-col gap-3 border-b border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-950 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p className="truncate text-sm font-black text-slate-950 dark:text-white">{document?.fileName ?? "선택 문서 없음"}</p>
          <p className="mt-0.5 text-[11px] text-slate-500">
            {document?.parserRegistered ? "파싱 결과 연결됨" : "보관 원본 · 파싱값 미등록"}
            {document?.sizeBytes !== undefined ? ` · ${formatBytes(document.sizeBytes)}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {contentUrl ? (
            <a
              href={contentUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-9 items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 text-xs font-black text-indigo-700 hover:bg-indigo-100 dark:border-indigo-800 dark:bg-indigo-950/40 dark:text-indigo-200"
            >
              <ExternalLink className="h-3.5 w-3.5" /> 원본 새 창으로 열기
            </a>
          ) : null}
          {document?.downloadUrl ? (
            <a
              href={document.downloadUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-black text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200 dark:hover:bg-slate-900"
            >
              <Download className="h-3.5 w-3.5" /> 다운로드
            </a>
          ) : null}
          <button
            type="button"
            onClick={onShowEvidence}
            disabled={!locator}
            className="inline-flex h-9 items-center gap-2 rounded-lg bg-indigo-600 px-3 text-xs font-black text-white disabled:opacity-40"
          >
            <LocateFixed className="h-3.5 w-3.5" /> 근거 상세
          </button>
        </div>
      </div>

      <div className="flex flex-1 items-start justify-center overflow-auto p-4 sm:p-6">
        <div className="w-full max-w-4xl space-y-4">
          {!contentUrl ? <Notice tone="warn">원본 미리보기를 열 수 없습니다. 다운로드로 확인하거나 자료 입력에서 원본을 다시 확인해 주세요.</Notice> : null}

          {document && locator && !locatorBound ? (
            <ControlledEmpty
              icon={<ShieldAlert className="h-7 w-7" />}
              title="locator와 선택 원본의 lineage가 일치하지 않습니다."
              body="file SHA-256, documentVersionId 또는 documentId가 다른 locator는 페이지나 셀 위에 표시하지 않습니다. Parser V2 원장을 다시 확인하세요."
            />
          ) : null}
          {document && contentUrl && isPdf ? (
            <PdfLocatorPreview
              key={`${archiveId}:${locator?.pageNumber ?? locator?.page ?? "source"}`}
              projectId={projectId}
              archiveId={archiveId}
              locator={locatorBound ? locator : undefined}
              contentUrl={contentUrl}
              onSelectSourceLocator={onSelectSourceLocator}
            />
          ) : document && contentUrl && isWorkbook ? (
            <WorkbookOriginalPreview
              key={`${archiveId}:${locatorBound ? sheetCellLabel(locator!) : "original"}`}
              projectId={projectId}
              archiveId={archiveId}
              locator={locatorBound ? locator : undefined}
              onSelectSourceLocator={onSelectSourceLocator}
            />
          ) : locator && document ? (
            <ControlledEmpty
              icon={<FileQuestion className="h-7 w-7" />}
              title="이 문서 유형의 locator overlay를 지원하지 않습니다."
              body="PDF page/bbox와 OpenXML Excel sheet/cell만 실제 원본 위에 표시합니다. 미지원 형식은 원본 다운로드와 locator 상세만 제공합니다."
            />
          ) : null}

          {locator ? (
            <details className="group"><summary className="cursor-pointer rounded-lg border border-slate-200 bg-white px-4 py-3 text-xs font-semibold text-slate-600 dark:border-slate-800 dark:bg-slate-950">선택한 값의 출처·위치 상세</summary>
            <article className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-950">
              <div className="flex flex-col gap-3 border-b border-slate-200 px-5 py-4 dark:border-slate-800 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <p className="text-xs font-black uppercase tracking-[0.15em] text-indigo-600">Selected evidence locator</p>
                  <h2 className="mt-1 text-xl font-black text-slate-950 dark:text-white">{locatorLabel(locator)}</h2>
                  <p className="mt-1 break-all font-mono text-xs text-slate-500">{fact?.fieldKey ?? "연결 필드 미확인"}</p>
                </div>
                <LocatorPrecisionBadge locator={locator} />
              </div>

              <div className="grid gap-px bg-slate-200 sm:grid-cols-2 xl:grid-cols-3 dark:bg-slate-800">
                <LocatorCell label="문서 버전" value={identifier(locator.documentVersionId) || document?.documentVersionId || "-"} />
                <LocatorCell label="페이지" value={finiteNumber(locator.pageNumber ?? locator.page) !== undefined ? `p.${finiteNumber(locator.pageNumber ?? locator.page)}` : "-"} />
                <LocatorCell label="시트 · 셀" value={sheetCellLabel(locator)} />
                <LocatorCell label="표 · 행 · 열" value={tableCoordinateLabel(locator)} />
                <LocatorCell label="문단 · JSON" value={paragraphJsonLabel(locator)} />
                <LocatorCell label="BBox" value={bboxLabel(locator)} />
              </div>

              {identifier(locator.rawTextExcerpt) ? (
                <div className="border-t border-slate-200 p-5 dark:border-slate-800">
                  <p className="text-xs font-black text-slate-500">보관된 원문 발췌</p>
                  <blockquote className="mt-2 whitespace-pre-wrap rounded-lg bg-slate-50 p-4 text-sm leading-6 text-slate-800 dark:bg-slate-900 dark:text-slate-200">
                    {identifier(locator.rawTextExcerpt)}
                  </blockquote>
                  {identifier(locator.rawTextSha256) ? (
                    <p className="mt-2 break-all font-mono text-[10px] text-slate-400">SHA-256 {identifier(locator.rawTextSha256)}</p>
                  ) : null}
                </div>
              ) : (
                <div className="border-t border-slate-200 p-5 text-sm text-slate-500 dark:border-slate-800">
                  이 locator에는 원문 발췌가 저장되지 않았습니다. 페이지·표·행·셀 좌표와 해시만 표시합니다.
                </div>
              )}
            </article></details>
          ) : !contentUrl ? (
            <ControlledEmpty
              icon={<LocateFixed className="h-7 w-7" />}
              title="아직 연결된 파싱값이 없습니다."
              body="원본을 확인한 뒤 자료 입력 화면에서 파싱 상태를 확인해 주세요."
            />
          ) : null}

          {document ? (
            <details><summary className="cursor-pointer px-1 py-2 text-xs text-slate-500">원본 보관 정보</summary><section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <MetaCard label="콘텐츠 유형" value={document.mediaType || "미확인"} />
              <MetaCard label="원본 무결성" value={document.integrityStatus || "미확인"} />
              <MetaCard label="출처 권위" value={document.sourceAuthority || "미확인"} />
              <MetaCard label="등록 시각" value={formatDate(document.uploadedAt)} />
            </section></details>
          ) : null}
        </div>
      </div>
    </main>
  );
}

function PdfLocatorPreview({
  projectId,
  archiveId,
  locator,
  contentUrl,
  onSelectSourceLocator,
}: {
  projectId: string;
  archiveId: string;
  locator?: SourceLocatorRecord;
  contentUrl: string;
  onSelectSourceLocator: (locator: SourceLocatorRecord) => void;
}) {
  const parsedPageNumber = finiteNumber(locator?.pageNumber ?? locator?.page);
  const evidencePageNumber = parsedPageNumber && Number.isInteger(parsedPageNumber) && parsedPageNumber > 0
    ? parsedPageNumber
    : undefined;
  const [pageNumber, setPageNumber] = useState(evidencePageNumber ?? 1);
  const activeLocator = pageNumber === evidencePageNumber ? locator : undefined;
  const bbox = activeLocator ? locatorBbox(activeLocator) : null;
  const [preview, setPreview] = useState<{
    url: string;
    pageCount: number;
    pageWidth: number;
    pageHeight: number;
    coordinateSpace: string;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setLoading(true);
    setError(null);
    void fetchPdfPagePreview({ projectId, archiveId, pageNumber })
      .then((result) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(result.blob);
        setPreview({
          url: objectUrl,
          pageCount: result.pageCount,
          pageWidth: result.pageWidth,
          pageHeight: result.pageHeight,
          coordinateSpace: result.coordinateSpace,
        });
      })
      .catch((caught) => {
        if (active) {
          setPreview(null);
          setError(errorMessage(caught));
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [archiveId, pageNumber, projectId]);

  const overlay = preview && activeLocator ? pdfLocatorOverlay(activeLocator, preview) : null;
  const bboxSupported = Boolean(overlay);
  const overlayStyle = overlay ? {
    left: `${overlay.leftPercent}%`,
    top: `${overlay.topPercent}%`,
    width: `${overlay.widthPercent}%`,
    height: `${overlay.heightPercent}%`,
  } : undefined;

  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-950" aria-label="PDF 근거 페이지 및 BBox">
      <div className="flex flex-col gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-800 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-black text-slate-950 dark:text-white">실제 PDF 페이지 · p.{pageNumber}</p>
          <p className="mt-0.5 text-[11px] text-slate-500">원본 페이지와 추출한 항목의 위치를 함께 확인하세요.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2" aria-label="PDF 페이지 선택">
            <button type="button" disabled={loading || pageNumber <= 1} onClick={() => setPageNumber((page) => page - 1)} className="min-h-9 rounded border border-slate-300 px-2 text-xs font-bold disabled:opacity-40 dark:border-slate-700" aria-label="이전 PDF 페이지">이전</button>
            <span className="text-xs text-slate-600 dark:text-slate-300">{pageNumber}{preview ? ` / ${preview.pageCount}` : ""}</span>
            <button type="button" disabled={loading || !preview || pageNumber >= preview.pageCount} onClick={() => setPageNumber((page) => page + 1)} className="min-h-9 rounded border border-slate-300 px-2 text-xs font-bold disabled:opacity-40 dark:border-slate-700" aria-label="다음 PDF 페이지">다음</button>
          </div>
          <a href={`${contentUrl}#page=${pageNumber}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs font-black text-indigo-600 hover:underline">
            전체 PDF 열기 <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </div>
      {loading ? (
        <div className="flex min-h-72 items-center justify-center text-xs font-bold text-slate-500"><Loader2 className="mr-2 h-4 w-4 animate-spin" /> 원본 페이지 렌더링 중</div>
      ) : error ? (
        <div className="p-4"><Notice tone="bad">{error}</Notice></div>
      ) : preview ? (
        <div className="space-y-3 bg-slate-100 p-4 dark:bg-slate-900">
          <div className="relative mx-auto w-fit max-w-full overflow-hidden rounded border border-slate-300 bg-white shadow-lg dark:border-slate-700">
            {/* eslint-disable-next-line @next/next/no-img-element -- authenticated object URL is not compatible with next/image */}
            <img src={preview.url} alt={`PDF ${pageNumber}페이지`} className="block h-auto max-h-[720px] max-w-full" />
            {bboxSupported && overlayStyle ? (
              <button
                type="button"
                onClick={() => { if (activeLocator) onSelectSourceLocator(activeLocator); }}
                className="absolute cursor-pointer border-2 border-rose-600 bg-rose-400/20 shadow-[0_0_0_1px_rgba(255,255,255,0.9)] focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2"
                style={overlayStyle}
                aria-label={`근거 BBox ${overlay?.bbox.join(", ")}와 연결된 Fact 보기`}
              >
                <span className="absolute -top-6 left-0 whitespace-nowrap rounded bg-rose-600 px-1.5 py-0.5 text-[10px] font-black text-white">근거 위치</span>
              </button>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
            <span>페이지 {pageNumber} / {preview.pageCount} · {Math.round(preview.pageWidth)} × {Math.round(preview.pageHeight)} pt</span>
            {bboxSupported ? (
              <span className="font-black text-emerald-700 dark:text-emerald-300">선택 항목의 위치 표시 중</span>
            ) : bbox ? (
              <span className="font-black text-amber-700 dark:text-amber-300">항목의 위치가 페이지와 맞지 않아 표시하지 않았습니다.</span>
            ) : (
              <span className="font-black text-slate-500">페이지 전체 표시</span>
            )}
          </div>
        </div>
      ) : null}
      <PdfPageReadingPanel key={`${projectId}:${archiveId}:${pageNumber}`} projectId={projectId} archiveId={archiveId} pageNumber={pageNumber} />
    </section>
  );
}


function Inspector({
  activeTab,
  onTabChange,
  facts,
  selectedFact,
  onSelectFact,
  factQuery,
  onFactQueryChange,
  issuesOnly,
  onIssuesOnlyChange,
  sourceFilterLabel,
  onClearSourceFilter,
  selectedEvidence,
  selectedLocator,
  onSelectLocator,
  conflicts,
  conflictBusyId,
  onResolveConflict,
  reviewQueue,
  allFacts,
  glossaryTerms,
  glossaryCandidates,
  glossaryLoading,
  glossaryError,
  reviewReason,
  onReviewReasonChange,
  reviewBusy,
  reviewDisabled,
  onSaveReview,
  snapshotDiffs,
  auditItems,
}: {
  activeTab: InspectorTab;
  onTabChange: (tab: InspectorTab) => void;
  facts: WorkspaceFact[];
  selectedFact?: WorkspaceFact;
  onSelectFact: (fact: WorkspaceFact) => void;
  factQuery: string;
  onFactQueryChange: (value: string) => void;
  issuesOnly: boolean;
  onIssuesOnlyChange: (value: boolean) => void;
  sourceFilterLabel?: string;
  onClearSourceFilter: () => void;
  selectedEvidence: SourceLocatorRecord[];
  selectedLocator?: SourceLocatorRecord;
  onSelectLocator: (locator: SourceLocatorRecord, index: number) => void;
  conflicts: SourceConflictRecord[];
  conflictBusyId: string | null;
  onResolveConflict: (conflict: SourceConflictRecord) => Promise<void>;
  reviewQueue: DocumentReviewQueueItem[];
  allFacts: WorkspaceFact[];
  glossaryTerms: GlossaryTermRecord[];
  glossaryCandidates: GlossaryCandidateRecord[];
  glossaryLoading: boolean;
  glossaryError: string | null;
  reviewReason: string;
  onReviewReasonChange: (value: string) => void;
  reviewBusy: boolean;
  reviewDisabled: boolean;
  onSaveReview: (action: "approve" | "reject" | "request_revision") => Promise<void>;
  snapshotDiffs: ApprovedSnapshotDiff[];
  auditItems: AuditItem[];
}) {
  return (
    <aside className="flex min-w-0 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-950" aria-label="문서 검토 Inspector">
      <div className="overflow-x-auto border-b border-slate-200 dark:border-slate-800">
        <div className="flex min-w-max px-2 pt-2" role="tablist" aria-label="검토 정보 탭">
          {TABS.filter((tab) => tab.key === "facts" || tab.key === "issues").map((tab) => (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.key}
              onClick={() => onTabChange(tab.key)}
              className={`border-b-2 px-3 py-2 text-xs font-black ${
                activeTab === tab.key
                  ? "border-indigo-600 text-indigo-700 dark:text-indigo-300"
                  : "border-transparent text-slate-500 hover:text-slate-900 dark:hover:text-white"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <details open={!["facts", "issues"].includes(activeTab) || undefined} className="border-t border-slate-100 dark:border-slate-800">
          <summary className="cursor-pointer px-4 py-3 text-xs font-semibold text-slate-500">검토 더보기</summary>
          <div className="flex flex-wrap gap-1 px-3 pb-3" role="tablist" aria-label="상세 검토 탭">
            {TABS.filter((tab) => tab.key !== "facts" && tab.key !== "issues").map((tab) => <button
              key={tab.key} type="button" role="tab" aria-selected={activeTab === tab.key}
              onClick={() => onTabChange(tab.key)}
              className={"min-h-9 rounded-lg px-3 py-2 text-xs font-semibold transition-colors " + (activeTab === tab.key ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-200" : "text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-900")}
            >{tab.label}</button>)}
          </div>
        </details>
      </div>
      <div className="max-h-[900px] overflow-y-auto p-4">
        {activeTab === "facts" ? (
          <FactsPanel
            facts={facts}
            selectedFact={selectedFact}
            onSelectFact={onSelectFact}
            query={factQuery}
            onQueryChange={onFactQueryChange}
            issuesOnly={issuesOnly}
            onIssuesOnlyChange={onIssuesOnlyChange}
            sourceFilterLabel={sourceFilterLabel}
            onClearSourceFilter={onClearSourceFilter}
            onReview={() => onTabChange("review")}
          />
        ) : null}
        {activeTab === "evidence" ? (
          <EvidencePanel evidence={selectedEvidence} selectedLocator={selectedLocator} onSelectLocator={onSelectLocator} />
        ) : null}
        {activeTab === "issues" ? (
          <IssueQueuePanel items={reviewQueue} facts={allFacts} selectedFact={selectedFact} onSelectFact={onSelectFact} />
        ) : null}
        {activeTab === "conflicts" ? (
          <ConflictsPanel
            conflicts={conflicts}
            selectedFact={selectedFact}
            onSelectFact={onSelectFact}
            busyId={conflictBusyId}
            onResolve={onResolveConflict}
          />
        ) : null}
        {activeTab === "glossary" ? (
          <GlossaryPanel
            fieldKey={selectedFact?.fieldKey}
            terms={glossaryTerms}
            candidates={glossaryCandidates}
            loading={glossaryLoading}
            error={glossaryError}
          />
        ) : null}
        {activeTab === "review" ? (
          <ReviewPanel
            fact={selectedFact}
            locator={selectedLocator}
            reason={reviewReason}
            onReasonChange={onReviewReasonChange}
            busy={reviewBusy}
            disabled={reviewDisabled}
            onSave={onSaveReview}
          />
        ) : null}
        {activeTab === "changes" ? <SnapshotChangesPanel diffs={snapshotDiffs} /> : null}
        {activeTab === "audit" ? <AuditPanel items={auditItems} selectedFact={selectedFact} /> : null}
      </div>
      <div className="border-t border-slate-200 px-3 py-2 text-[10px] text-slate-400 dark:border-slate-800">
        검토 결정과 계산에 사용할 승인 입력값은 별도로 관리됩니다.
      </div>
    </aside>
  );
}

function FactsPanel({
  facts,
  selectedFact,
  onSelectFact,
  query,
  onQueryChange,
  issuesOnly,
  onIssuesOnlyChange,
  sourceFilterLabel,
  onClearSourceFilter,
  onReview,
}: {
  facts: WorkspaceFact[];
  selectedFact?: WorkspaceFact;
  onSelectFact: (fact: WorkspaceFact) => void;
  query: string;
  onQueryChange: (value: string) => void;
  issuesOnly: boolean;
  onIssuesOnlyChange: (value: boolean) => void;
  sourceFilterLabel?: string;
  onClearSourceFilter: () => void;
  onReview: () => void;
}) {
  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-black text-slate-950 dark:text-white">파싱 후보값</p>
        <p className="mt-1 text-xs leading-5 text-slate-500">선택한 문서에서 추출한 값을 원본과 비교하세요.</p>
        {selectedFact ? <button type="button" onClick={onReview} className="mt-2 min-h-9 rounded-lg bg-indigo-600 px-3 text-xs font-bold text-white">선택한 후보 검토 결정</button> : null}
      </div>
      <label className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-800">
        <Search className="h-3.5 w-3.5 text-slate-400" />
        <input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="항목·값 검색"
          className="min-w-0 flex-1 bg-transparent text-xs outline-none"
        />
      </label>
      <label className="flex items-center gap-2 text-xs font-bold text-slate-600 dark:text-slate-300">
        <input type="checkbox" checked={issuesOnly} onChange={(event) => onIssuesOnlyChange(event.target.checked)} />
        확인이 필요한 값만
      </label>
      {sourceFilterLabel ? (
        <div className="flex items-start justify-between gap-2 rounded-lg border border-indigo-200 bg-indigo-50 p-3 text-xs text-indigo-950 dark:border-indigo-900/60 dark:bg-indigo-950/30 dark:text-indigo-100">
          <div>
            <p className="font-black">원문 위치 → 관련 Fact 역조회</p>
            <p className="mt-1 break-all font-mono text-[10px]">{sourceFilterLabel}</p>
          </div>
          <button type="button" onClick={onClearSourceFilter} className="shrink-0 font-black text-indigo-700 underline dark:text-indigo-200">전체 보기</button>
        </div>
      ) : null}
      {facts.length ? (
        <div className="space-y-2">
          {facts.map((fact) => (
            <button
              key={fact.key}
              type="button"
              onClick={() => onSelectFact(fact)}
              style={{ contentVisibility: "auto", containIntrinsicSize: "150px" }}
              className={`w-full rounded-lg border p-3 text-left ${
                selectedFact?.key === fact.key
                  ? "border-indigo-300 bg-indigo-50 dark:border-indigo-700 dark:bg-indigo-950/40"
                  : "border-slate-200 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-900"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-xs font-bold text-slate-500">{factLabel(fact)}</p>
                  <p className="mt-1 break-words text-sm font-black text-slate-950 dark:text-white">
                    {factValue(fact)}
                  </p>
                  {bridgeReference(fact) ? <p className="mt-1 text-[11px] font-semibold text-amber-800 dark:text-amber-300">현재 브리지 현황 · 참조용</p> : null}
                </div>
                <span className="shrink-0 text-right text-[10px] font-bold leading-4 text-slate-500" title="Parser 추출 confidence이며 검토 또는 승인 신뢰도가 아닙니다.">
                  추출 confidence<br />
                  <strong className="text-[11px]">{fact.candidate.confidence === undefined ? "-" : `${Math.round(fact.candidate.confidence * 100)}%`}</strong>
                </span>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-1.5">
                <StatusPill label="검증" status={fact.validationStatus} />
                <SharedAxisBadge axis="review" status={fact.reviewStatus} />
                <StatusPill label="Selection" status={fact.activeSelection ? "active" : "none"} />
                <SharedAxisBadge axis="approval" status={fact.snapshotApprovalStatus} />
                <StatusPill label="충돌" status={fact.conflictStatus} />
                <StatusPill label="권위" status={fact.authorityStage} />
              </div>
              {factBasisRequired(fact) ? <p className="mt-2 text-xs font-semibold text-amber-800 dark:text-amber-300">금액 기준 미확인 · 계산용 승인 입력 선택 불가</p>
                : fact.issueCodes.length ? <p className="mt-2 text-[11px] font-bold text-rose-600">원본·검증 상태 확인 필요</p> : null}
              <p className="mt-2 truncate text-[10px] text-slate-400">Candidate {fact.candidateId}</p>
            </button>
          ))}
        </div>
      ) : (
        <ControlledEmpty
          icon={<Database className="h-6 w-6" />}
          title="표시할 파싱값이 없습니다."
          body="원본을 먼저 확인하세요. 파싱이 끝난 문서의 검토할 값이 이곳에 표시됩니다."
          compact
        />
      )}
    </div>
  );
}

function IssueQueuePanel({
  items,
  facts,
  selectedFact,
  onSelectFact,
}: {
  items: DocumentReviewQueueItem[];
  facts: WorkspaceFact[];
  selectedFact?: WorkspaceFact;
  onSelectFact: (fact: WorkspaceFact) => void;
}) {
  const [filter, setFilter] = useState("all");
  const categories = [
    { key: "all", label: "전체" },
    { key: "OPEN_SOURCE_CONFLICT", label: "열린 충돌" },
    { key: "MISSING_EVIDENCE_LOCATOR", label: "근거 없음" },
    { key: "WEAK_EVIDENCE_LOCATOR", label: "위치 불충분" },
    { key: "VALIDATION", label: "검증 이슈" },
    { key: "REVIEW", label: "검토 필요" },
    { key: "WEAK_SOURCE_AUTHORITY", label: "출처 권위" },
    { key: "MISSING_VALUE", label: "값 누락" },
  ];
  const filtered = items.filter((item) => {
    if (filter === "all") return true;
    if (filter === "VALIDATION") {
      return (item.issueCodes ?? []).some((code) => code === "VALIDATION_NOT_PASSED" || code.startsWith("VALIDATION:"));
    }
    if (filter === "REVIEW") {
      return (item.issueCodes ?? []).some((code) => code.startsWith("REVIEW_"));
    }
    return (item.issueCodes ?? []).includes(filter);
  });
  const factByCandidate = new Map(facts.map((fact) => [fact.candidateId, fact]));
  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-black text-slate-950 dark:text-white">서버 검토 Queue</p>
        <p className="mt-1 text-xs leading-5 text-slate-500">동일 Read Model에서 판정한 충돌·검증·evidence locator 이슈입니다. Queue 포함 여부는 승인 상태가 아닙니다.</p>
      </div>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="검토 Queue 필터">
        {categories.map((category) => {
          const count = items.filter((item) => {
            if (category.key === "all") return true;
            if (category.key === "VALIDATION") return (item.issueCodes ?? []).some((code) => code.startsWith("VALIDATION"));
            if (category.key === "REVIEW") return (item.issueCodes ?? []).some((code) => code.startsWith("REVIEW_"));
            return (item.issueCodes ?? []).includes(category.key);
          }).length;
          return (
            <button
              key={category.key}
              type="button"
              onClick={() => setFilter(category.key)}
              className={`rounded-md border px-2 py-1 text-[11px] font-black ${filter === category.key ? "border-indigo-600 bg-indigo-600 text-white" : "border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"}`}
            >
              {category.label} {count}
            </button>
          );
        })}
      </div>
      {filtered.length ? filtered.map((item) => {
        const fact = factByCandidate.get(identifier(item.candidateId));
        return (
          <button
            key={identifier(item.queueItemId) || `${identifier(item.parsingRunId)}:${identifier(item.candidateId)}`}
            type="button"
            disabled={!fact}
            onClick={() => fact && onSelectFact(fact)}
            className={`w-full rounded-lg border p-3 text-left disabled:cursor-not-allowed ${selectedFact?.candidateId === identifier(item.candidateId) ? "border-indigo-300 bg-indigo-50 dark:border-indigo-700 dark:bg-indigo-950/40" : "border-slate-200 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-900"}`}
            style={{ contentVisibility: "auto", containIntrinsicSize: "112px" }}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="break-all font-mono text-[11px] font-bold text-slate-500">{identifier(item.fieldKey) || "unmapped_field"}</p>
                <p className="mt-1 text-xs font-black text-slate-950 dark:text-white">{identifier(item.candidateId) || "candidate 미확인"}</p>
              </div>
              <StatusPill label="심각도" status={identifier(item.severity) || "unknown"} />
            </div>
            <div className="mt-3 flex flex-wrap gap-1">
              {(item.issueCodes ?? []).map((code) => <IssueCodePill key={code} code={code} />)}
            </div>
            <p className="mt-2 text-[10px] text-slate-500">검증 {identifier(item.validationOutcome) || "unknown"} · locator {item.evidenceLocatorCount ?? 0}건 · 열린 충돌 {item.openConflictIds?.length ?? 0}건</p>
          </button>
        );
      }) : (
        <ControlledEmpty
          icon={<ListChecks className="h-6 w-6" />}
          title={items.length ? "선택한 분류의 이슈가 없습니다." : "서버 Review Queue가 비어 있습니다."}
          body="Queue가 비어 있어도 사람 검토나 ApprovedInputSnapshot 승인을 의미하지 않습니다. 독립 상태축을 확인하세요."
          compact
        />
      )}
    </div>
  );
}

function IssueCodePill({ code }: { code: string }) {
  const labels: Record<string, string> = {
    OPEN_SOURCE_CONFLICT: "열린 SourceConflict",
    MISSING_EVIDENCE_LOCATOR: "Evidence locator 없음",
    WEAK_EVIDENCE_LOCATOR: "Evidence 위치 불충분",
    VALIDATION_NOT_PASSED: "검증 미통과",
    REVIEW_PENDING: "사람 검토 대기",
    REVIEW_NEEDS_REVISION: "검토 보완 필요",
    WEAK_SOURCE_AUTHORITY: "약한 SourceAuthority",
    MISSING_VALUE: "값 누락",
  };
  return <span className="rounded border border-rose-200 bg-rose-50 px-1.5 py-0.5 text-[10px] font-black text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">{labels[code] ?? code}</span>;
}

function SnapshotChangesPanel({ diffs }: { diffs: ApprovedSnapshotDiff[] }) {
  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-black text-slate-950 dark:text-white">ApprovedInputSnapshot 변경 비교</p>
        <p className="mt-1 text-xs leading-5 text-slate-500">서버가 `(fieldKey, entityId)` 기준으로 materialized 승인 입력을 비교합니다. 프로젝트 Snapshot diff와 다른 Parser 승인 원장 비교입니다.</p>
      </div>
      {diffs.length ? diffs.slice().reverse().map((diff) => (
        <article key={`${identifier(diff.fromSnapshotId)}:${identifier(diff.toSnapshotId)}`} className="rounded-lg border border-slate-200 p-3 dark:border-slate-800" style={{ contentVisibility: "auto", containIntrinsicSize: "220px" }}>
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="text-xs font-black text-slate-950 dark:text-white">v{diff.toSnapshotVersion ?? "-"} · 변경 {diff.changeCount ?? 0}건</p>
              <p className="mt-1 break-all font-mono text-[10px] text-slate-400">{identifier(diff.fromSnapshotId) || "이전 snapshot"} → {identifier(diff.toSnapshotId) || "현재 snapshot"}</p>
            </div>
            <StatusPill label="비교" status={diff.comparable ? "verified" : "blocked"} />
          </div>
          <p className="mt-2 text-[10px] text-slate-500">승인 시각 {formatDate(diff.approvedAt)}</p>
          {diff.comparable ? (
            <div className="mt-3 space-y-2">
              {(diff.changes ?? []).map((change, index) => (
                <div key={`${change.fieldKey}:${change.entityId ?? ""}:${index}`} className="rounded-md bg-slate-50 p-2 dark:bg-slate-900">
                  <div className="flex items-center justify-between gap-2">
                    <p className="break-all font-mono text-[10px] font-bold text-slate-500">{change.fieldKey || "필드 미확인"}{change.entityId ? ` · ${change.entityId}` : ""}</p>
                    <StatusPill label="변경" status={change.changeType || "unknown"} />
                  </div>
                  <div className="mt-2 grid grid-cols-[1fr_auto_1fr] items-center gap-2 text-xs">
                    <SnapshotDiffValueView label="이전" value={change.before} />
                    <ChevronRight className="h-3.5 w-3.5 text-slate-400" />
                    <SnapshotDiffValueView label="변경" value={change.after} />
                  </div>
                </div>
              ))}
              {!diff.changes?.length ? <p className="text-xs text-slate-500">material value·unit·candidate·selection·source hash 변경이 없습니다.</p> : null}
            </div>
          ) : (
            <Notice tone="warn">{(diff.issueCodes ?? ["SNAPSHOT_INPUT_ITEMS_UNAVAILABLE"]).join(" · ")} — 입력 항목이 없어 값을 추정 비교하지 않습니다.</Notice>
          )}
          <div className="mt-3 grid gap-1 font-mono text-[9px] text-slate-400">
            <p className="break-all">from {identifier(diff.fromSnapshotHash) || "hash 미확인"}</p>
            <p className="break-all">to {identifier(diff.toSnapshotHash) || "hash 미확인"}</p>
          </div>
        </article>
      )) : (
        <ControlledEmpty
          icon={<GitCompareArrows className="h-6 w-6" />}
          title="비교할 승인 Snapshot 계보가 없습니다."
          body="승인 Snapshot이 두 버전 이상 존재해야 before/after 비교를 표시합니다. 없는 변경을 임의로 생성하지 않습니다."
          compact
        />
      )}
    </div>
  );
}

function SnapshotDiffValueView({ label, value }: { label: string; value?: { normalizedValue?: unknown; unit?: string; candidateId?: string } | null }) {
  return (
    <div className="min-w-0">
      <p className="text-[9px] font-black text-slate-400">{label}</p>
      <p className="mt-0.5 break-words font-black text-slate-800 dark:text-slate-100">{value ? formatValue(value.normalizedValue, value.unit) : "—"}</p>
      {value?.candidateId ? <p className="mt-0.5 truncate font-mono text-[9px] text-slate-400">{value.candidateId}</p> : null}
    </div>
  );
}

function EvidencePanel({
  evidence,
  selectedLocator,
  onSelectLocator,
}: {
  evidence: SourceLocatorRecord[];
  selectedLocator?: SourceLocatorRecord;
  onSelectLocator: (locator: SourceLocatorRecord, index: number) => void;
}) {
  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-black text-slate-950 dark:text-white">Evidence locators</p>
        <p className="mt-1 text-xs leading-5 text-slate-500">내부 record 위치가 아니라 후보값에 포함된 외부 원문 locator입니다.</p>
      </div>
      {evidence.length ? (
        evidence.map((locator, index) => {
          const selected = selectedLocator && sameLocator(selectedLocator, locator);
          return (
            <button
              key={`${identifier(locator.evidenceId) || sourceLocatorSemanticKey(locator) || "unkeyed"}:${index}`}
              type="button"
              onClick={() => onSelectLocator(locator, index)}
              className={`w-full rounded-lg border p-3 text-left ${
                selected ? "border-indigo-300 bg-indigo-50 dark:border-indigo-700 dark:bg-indigo-950/40" : "border-slate-200 dark:border-slate-800"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-black text-slate-950 dark:text-white">{locatorLabel(locator)}</p>
                  <p className="mt-1 break-all font-mono text-[10px] text-slate-400">
                    {identifier(locator.evidenceId) || "evidenceId 미발행"}
                  </p>
                </div>
                <LocatorPrecisionBadge locator={locator} />
              </div>
              <div className="mt-3 grid gap-1 text-[11px] text-slate-500">
                <p>Parser: {identifier(locator.parserMethod) || "-"}</p>
                <p className="truncate">Document: {identifier(locator.documentVersionId ?? locator.documentId) || "-"}</p>
                <p>Raw text hash: {identifier(locator.rawTextSha256) ? "있음" : "없음"}</p>
              </div>
            </button>
          );
        })
      ) : (
        <ControlledEmpty
          icon={<ShieldAlert className="h-6 w-6" />}
          title="외부 원문 근거가 없습니다."
          body="근거가 없는 present fact는 Parser V2 계약상 승인 대상이 될 수 없습니다."
          compact
        />
      )}
    </div>
  );
}

function ConflictsPanel({
  conflicts,
  selectedFact,
  onSelectFact,
  busyId,
  onResolve,
}: {
  conflicts: SourceConflictRecord[];
  selectedFact?: WorkspaceFact;
  onSelectFact: (fact: WorkspaceFact) => void;
  busyId: string | null;
  onResolve: (conflict: SourceConflictRecord) => Promise<void>;
}) {
  const relevant = selectedFact
    ? conflicts.filter((conflict) => (conflict.candidateIds ?? []).includes(selectedFact.candidateId))
    : conflicts;
  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-black text-slate-950 dark:text-white">Source conflicts</p>
        <p className="mt-1 text-xs leading-5 text-slate-500">선택 후보와 관련된 cross-source 충돌을 우선 표시합니다.</p>
      </div>
      {relevant.length ? relevant.map((conflict) => {
        const conflictId = identifier(conflict.conflictId);
        const winningDecision = selectedFact?.latestDecision;
        const canResolve = identifier(conflict.conflictStatus) === "open"
          && Boolean(selectedFact)
          && (conflict.candidateIds ?? []).includes(selectedFact?.candidateId ?? "")
          && winningDecision?.action === "approve"
          && Boolean(identifier(winningDecision.reviewDecisionId))
          && Number(winningDecision.decisionSequence ?? 0) >= 1;
        return (
        <article key={conflictId} className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="break-all font-mono text-[11px] font-bold text-slate-500">
                {identifier(conflict.fieldKey ?? conflict.canonicalField) || "필드 미확인"}
              </p>
              <p className="mt-1 text-sm font-black text-slate-950 dark:text-white">
                {identifier(conflict.conflictType ?? conflict.conflictCode) || "source_conflict"}
              </p>
            </div>
            <StatusPill label="충돌" status={identifier(conflict.conflictStatus) || "open"} />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-500">
            <p>심각도 <strong className="text-slate-800 dark:text-slate-200">{identifier(conflict.severity) || "-"}</strong></p>
            <p>후보 <strong className="text-slate-800 dark:text-slate-200">{conflict.candidateIds?.length ?? 0}건</strong></p>
          </div>
          {identifier(conflict.notes) ? <p className="mt-3 rounded-md bg-slate-50 p-2 text-xs leading-5 text-slate-600 dark:bg-slate-900 dark:text-slate-300">{identifier(conflict.notes)}</p> : null}
          <p className="mt-2 break-all font-mono text-[10px] text-slate-400">{identifier(conflict.conflictId)}</p>
          {selectedFact && (conflict.candidateIds ?? []).includes(selectedFact.candidateId) ? (
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" onClick={() => onSelectFact(selectedFact)} className="inline-flex items-center gap-1 text-xs font-black text-indigo-600">
                선택 후보 근거 재확인 <ChevronRight className="h-3 w-3" />
              </button>
              {identifier(conflict.conflictStatus) === "open" ? (
                <button
                  type="button"
                  disabled={!canResolve || busyId === conflictId}
                  onClick={() => void onResolve(conflict)}
                  className="inline-flex items-center gap-1 rounded-md border border-emerald-300 px-2 py-1 text-[11px] font-black text-emerald-700 disabled:cursor-not-allowed disabled:opacity-40 dark:border-emerald-800 dark:text-emerald-200"
                  title={canResolve ? "최신 승인 decision을 winning candidate로 기록합니다." : "먼저 이 후보에 승인 ReviewDecision을 저장해야 합니다."}
                >
                  {busyId === conflictId ? <Loader2 className="h-3 w-3 animate-spin" /> : <ShieldCheck className="h-3 w-3" />}
                  승인 decision으로 해결
                </button>
              ) : null}
            </div>
          ) : null}
        </article>
      );}) : (
        <ControlledEmpty
          icon={<ShieldCheck className="h-6 w-6" />}
          title="연결된 SourceConflict가 없습니다."
          body="충돌 없음은 근거 검증 또는 승인을 의미하지 않습니다. 각 상태축을 별도로 확인하세요."
          compact
        />
      )}
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100">
        충돌 해결은 선택 후보의 최신 <strong>approve ReviewDecision</strong>을 winning candidate로 참조하고 append-only resolution event를 저장합니다. Waiver는 별도 권한·조건 검토가 필요하므로 이 화면에서 제공하지 않습니다.
      </div>
    </div>
  );
}

function GlossaryPanel({
  fieldKey,
  terms,
  candidates,
  loading,
  error,
}: {
  fieldKey?: string;
  terms: GlossaryTermRecord[];
  candidates: GlossaryCandidateRecord[];
  loading: boolean;
  error: string | null;
}) {
  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-black text-slate-950 dark:text-white">표준 용어 매핑</p>
        <p className="mt-1 break-all font-mono text-[11px] text-slate-500">{fieldKey || "선택 필드 없음"}</p>
      </div>
      {loading ? <p className="flex items-center gap-2 text-xs text-slate-500"><Loader2 className="h-3.5 w-3.5 animate-spin" /> 실제 glossary 원장을 조회하는 중입니다.</p> : null}
      {error ? <Notice tone="warn">{error}</Notice> : null}
      {terms.map((term) => (
        <article key={identifier(term.termId)} className="rounded-lg border border-emerald-200 bg-emerald-50/60 p-3 dark:border-emerald-900/60 dark:bg-emerald-950/20">
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="text-sm font-black text-slate-950 dark:text-white">{displayText(term.displayNameKo ?? term.preferredTerm, "표준어 이름 미확인")}</p>
              <p className="mt-1 font-mono text-[10px] text-slate-500">{identifier(term.termId)}</p>
            </div>
            <SharedAxisBadge axis="review" status={identifier(term.reviewStatus) || "unknown"} />
          </div>
          <p className="mt-2 text-xs text-slate-500">분류 {identifier(term.category) || "-"} · 별칭 {term.aliases?.length ?? 0}개</p>
        </article>
      ))}
      {candidates.map((candidate) => (
        <article key={identifier(candidate.candidateId)} className="rounded-lg border border-amber-200 bg-amber-50/60 p-3 dark:border-amber-900/60 dark:bg-amber-950/20">
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="text-sm font-black text-slate-950 dark:text-white">{displayText(candidate.surface, "용어 후보")}</p>
              <p className="mt-1 text-xs text-slate-500">표준어 미확정 후보</p>
            </div>
            <StatusPill label="용어" status={identifier(candidate.status) || "pending"} />
          </div>
        </article>
      ))}
      {!loading && !terms.length && !candidates.length ? (
        <ControlledEmpty
          icon={<Tags className="h-6 w-6" />}
          title="이 필드의 승인 용어 또는 프로젝트 후보가 없습니다."
          body="매핑이 없는 상태를 그대로 유지합니다. 자동으로 임의 표준 용어를 생성하지 않습니다."
          compact
        />
      ) : null}
      <Link href="/glossary/candidates" className="inline-flex items-center gap-1 text-xs font-black text-indigo-600">
        용어 후보 검토함 열기 <ExternalLink className="h-3 w-3" />
      </Link>
    </div>
  );
}

function ReviewPanel({
  fact,
  locator,
  reason,
  onReasonChange,
  busy,
  disabled,
  onSave,
}: {
  fact?: WorkspaceFact;
  locator?: SourceLocatorRecord;
  reason: string;
  onReasonChange: (value: string) => void;
  busy: boolean;
  disabled: boolean;
  onSave: (action: "approve" | "reject" | "request_revision") => Promise<void>;
}) {
  if (!fact) {
    return <ControlledEmpty icon={<FileCheck2 className="h-6 w-6" />} title="검토할 후보값을 선택하세요." body="검토 결정은 Parser V2 candidate ID에 append-only로 기록됩니다." compact />;
  }
  const runStatus = identifier(fact.run.executionStatus) || "unknown";
  const runCompleted = ["completed", "completed_with_warnings"].includes(runStatus);
  const seal = fact.package.candidateSetSeal;
  const sealPresent = Boolean(seal && Object.keys(seal).length);
  const canSubmit = runCompleted && sealPresent && !busy && !disabled;
  const decisions = fact.package.reviewDecisions
    .filter((item) => identifier(item.candidateId) === fact.candidateId)
    .sort((left, right) => Number(left.decisionSequence ?? 0) - Number(right.decisionSequence ?? 0));

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-black text-slate-950 dark:text-white">사람 검토 결정</p>
        <p className="mt-2 text-sm font-bold text-slate-900 dark:text-white">{factLabel(fact)} · {factValue(fact)}</p>
        {locator ? <p className="mt-1 text-xs text-slate-500">선택 근거 · {locatorLabel(locator)}</p> : null}
      </div>

      {bridgeReference(fact) ? <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
        <p className="font-bold">현재 브리지 현황 · 참조용</p>
        <p className="mt-1">원본 대조에 대한 사람의 검토 결정을 기록합니다. 기준일과 잔액·약정 기준은 미확인 상태입니다.</p>
        {factBasisRequired(fact) ? <p className="mt-1">금액 기준이 확인되기 전에는 검토 결정을 저장해도 계산용 승인 입력으로 선택할 수 없습니다.</p> : null}
        {candidateMetadata(fact).noFuturePfProjection === true ? <p className="mt-1">이 후보는 미래 PF 약정이나 현금흐름 계산 입력에 반영되지 않습니다.</p> : null}
      </div> : null}

      <details className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
      <summary className="cursor-pointer text-xs font-semibold text-slate-500">검토 상태와 제출 조건</summary>
      <p className="mt-2 break-all font-mono text-[11px] text-slate-500">{fact.fieldKey} · {fact.candidateId}</p>
      <div className="grid grid-cols-2 gap-2">
        <StateAxis label="실행" status={runStatus} />
        <StateAxis label="검증" status={fact.validationStatus} />
        <StateAxis label="검토" status={fact.reviewStatus} />
        <StateAxis label="후보 승인" status={fact.reviewApprovalStatus} />
        <StateAxis label="Canonical selection" status={fact.activeSelection ? "active" : "none"} />
        <StateAxis label="입력 Snapshot" status={fact.snapshotApprovalStatus} />
        <StateAxis label="충돌" status={fact.conflictStatus} />
        <StateAxis label="Presence" status={identifier(fact.candidate.presenceStatus) || "unknown"} />
      </div>

      <div className="rounded-lg border border-slate-200 p-3 text-xs leading-5 text-slate-600 dark:border-slate-800 dark:text-slate-300">
        <p className="font-black text-slate-950 dark:text-white">서버 제출 전 확인</p>
        <GateLine ok={runCompleted} label={`Parsing run 완료 · ${statusLabel(runStatus)}`} />
        <GateLine ok={sealPresent} label="Candidate/conflict set seal 존재" />
        <GateLine ok={fact.validationStatus === "passed"} label={`Ontology 검증 · ${statusLabel(fact.validationStatus)}`} advisory />
        <GateLine ok={fact.conflictStatus !== "open"} label={`열린 충돌 없음 · ${statusLabel(fact.conflictStatus)}`} advisory />
        <p className="mt-2 text-[11px] text-slate-500">엄격 RBAC, allowlist, 사람 principal, OIDC/JWKS, MFA, maker-checker는 서버가 최종 강제합니다.</p>
        {fact.issueCodes.length ? <p className="mt-2 break-all font-mono text-[10px]">{fact.issueCodes.join(" · ")}</p> : null}
        {fact.candidate.validationCodes?.length ? <p className="mt-1 break-all font-mono text-[10px]">{fact.candidate.validationCodes.join(" · ")}</p> : null}
      </div>
      </details>

      <label className="block">
        <span className="text-xs font-black text-slate-700 dark:text-slate-200">결정 근거 또는 보완 사유</span>
        <textarea
          value={reason}
          onChange={(event) => onReasonChange(event.target.value)}
          rows={5}
          placeholder="원문 locator와 후보값을 대조한 결과를 구체적으로 기록하세요."
          className="mt-2 w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm leading-6 outline-none focus:border-indigo-400 dark:border-slate-700 dark:bg-slate-900"
        />
      </label>

      <div className="grid gap-2 sm:grid-cols-3">
        <button type="button" disabled={!canSubmit || !reason.trim()} onClick={() => void onSave("request_revision")} className="inline-flex h-10 items-center justify-center gap-1 rounded-lg border border-amber-300 text-xs font-black text-amber-800 disabled:opacity-40 dark:border-amber-800 dark:text-amber-200">
          <AlertTriangle className="h-3.5 w-3.5" /> 보완 요청
        </button>
        <button type="button" disabled={!canSubmit || !reason.trim()} onClick={() => void onSave("reject")} className="inline-flex h-10 items-center justify-center gap-1 rounded-lg border border-rose-300 text-xs font-black text-rose-700 disabled:opacity-40 dark:border-rose-800 dark:text-rose-200">
          <XCircle className="h-3.5 w-3.5" /> 반려
        </button>
        <button type="button" disabled={!canSubmit || !reason.trim()} onClick={() => void onSave("approve")} className="inline-flex h-10 items-center justify-center gap-1 rounded-lg bg-emerald-600 text-xs font-black text-white disabled:opacity-40">
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />} 검토 승인
        </button>
      </div>

      <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-3 text-xs leading-5 text-indigo-950 dark:border-indigo-900/60 dark:bg-indigo-950/30 dark:text-indigo-100">
        검토 승인은 Canonical selection이나 ApprovedInputSnapshot 생성을 대신하지 않습니다. 현재 권위 단계는 <strong>{statusLabel(fact.authorityStage)}</strong>이며, 값의 authoritative projection도 이 화면에서 수행하지 않습니다.
      </div>

      <div>
        <p className="text-xs font-black text-slate-700 dark:text-slate-200">기존 결정 {decisions.length}건</p>
        <div className="mt-2 space-y-2">
          {decisions.map((decision) => (
            <div key={identifier(decision.reviewDecisionId)} className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-black">#{decision.decisionSequence ?? "-"} {identifier(decision.action)}</span>
                <SharedAxisBadge axis="review" status={identifier(decision.resultingReviewStatus) || "unknown"} />
              </div>
              <p className="mt-2 text-xs leading-5 text-slate-600 dark:text-slate-300">{displayText(decision.reason, "사유 없음")}</p>
              <p className="mt-1 text-[10px] text-slate-400">{formatDate(decision.decidedAt ?? decision.createdAt)}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function AuditPanel({ items, selectedFact }: { items: AuditItem[]; selectedFact?: WorkspaceFact }) {
  const scoped = selectedFact
    ? items.filter((item) => item.detail?.includes(selectedFact.candidateId) || item.detail?.includes(selectedFact.runId) || item.category === "snapshot")
    : items;
  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-black text-slate-950 dark:text-white">Append-only lineage</p>
        <p className="mt-1 text-xs leading-5 text-slate-500">실행, 검토, canonical selection, 승인 snapshot을 시간순으로 표시합니다.</p>
      </div>
      {scoped.length ? scoped.map((item) => (
        <div key={item.key} className="relative border-l-2 border-slate-200 pl-4 dark:border-slate-700">
          <span className="absolute -left-[5px] top-1.5 h-2 w-2 rounded-full bg-indigo-500" />
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="text-xs font-black text-slate-950 dark:text-white">{item.title}</p>
              <p className="mt-0.5 text-[10px] uppercase tracking-wide text-slate-400">{item.category}</p>
            </div>
            {item.status ? <StatusPill label="상태" status={item.status} /> : null}
          </div>
          {item.detail ? <p className="mt-1 break-all font-mono text-[10px] leading-4 text-slate-500">{item.detail}</p> : null}
          <p className="mt-1 text-[10px] text-slate-400">{formatDate(item.at)}</p>
        </div>
      )) : (
        <ControlledEmpty icon={<History className="h-6 w-6" />} title="표시할 감사 이벤트가 없습니다." body="이 화면은 run package에 포함된 실제 lineage 이벤트만 표시합니다." compact />
      )}
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs leading-5 text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
        <strong>범위:</strong> Parser V2 run package lineage입니다. 사용자 활동 전체 감사 로그는 프로젝트 Audit 화면에서 별도로 확인합니다.
      </div>
    </div>
  );
}

function collectAuditItems(data: DocumentReviewData | null): AuditItem[] {
  if (!data) return [];
  const items = new Map<string, AuditItem>();
  for (const runPackage of data.packages) {
    const run = runPackage.parsingRun;
    const runId = packageRunId(runPackage);
    if (runId) {
      items.set(`run:${runId}`, {
        key: `run:${runId}`,
        at: identifier(run.completedAt ?? run.startedAt ?? run.requestedAt ?? run.createdAt) || undefined,
        category: "parsing run",
        title: `Parser 실행 ${statusLabel(identifier(run.executionStatus) || "unknown")}`,
        detail: `${runId} · documentVersion=${identifier(run.documentVersionId) || "-"}`,
        status: identifier(run.executionStatus) || "unknown",
      });
    }
    for (const decision of runPackage.reviewDecisions ?? []) addReviewAudit(items, decision);
    for (const selection of runPackage.canonicalSelections ?? []) addSelectionAudit(items, selection);
    for (const snapshot of runPackage.approvedInputSnapshots ?? []) addSnapshotAudit(items, snapshot);
  }
  return Array.from(items.values()).sort((left, right) => dateValue(right.at) - dateValue(left.at));
}

function addReviewAudit(items: Map<string, AuditItem>, decision: ReviewDecisionRecord) {
  const id = identifier(decision.reviewDecisionId);
  if (!id) return;
  items.set(`review:${id}`, {
    key: `review:${id}`,
    at: identifier(decision.decidedAt ?? decision.createdAt) || undefined,
    category: "review decision",
    title: `검토 결정 #${decision.decisionSequence ?? "-"} · ${identifier(decision.action) || "unknown"}`,
    detail: `${id} · candidate=${identifier(decision.candidateId)}`,
    status: identifier(decision.resultingReviewStatus) || "unknown",
  });
}

function addSelectionAudit(items: Map<string, AuditItem>, selection: CanonicalSelectionRecord) {
  const id = identifier(selection.selectionId);
  if (!id) return;
  items.set(`selection:${id}`, {
    key: `selection:${id}`,
    at: identifier(selection.selectedAt ?? selection.createdAt) || undefined,
    category: "canonical selection",
    title: `Canonical selection v${selection.selectionVersion ?? "-"}`,
    detail: `${id} · candidate=${identifier(selection.candidateId)}`,
    status: identifier(selection.validityStatus ?? selection.approvalStatus) || "unknown",
  });
}

function addSnapshotAudit(items: Map<string, AuditItem>, snapshot: ApprovedInputSnapshotRecord) {
  const id = identifier(snapshot.snapshotId);
  if (!id) return;
  items.set(`snapshot:${id}`, {
    key: `snapshot:${id}`,
    at: identifier(snapshot.approvedAt ?? snapshot.createdAt) || undefined,
    category: "snapshot",
    title: `승인 입력 Snapshot · ${identifier(snapshot.purpose) || "purpose 미확인"}`,
    detail: `${id} · hash=${identifier(snapshot.snapshotHash) || "-"}`,
    status: identifier(snapshot.validityStatus ?? snapshot.approvalStatus) || "unknown",
  });
}

function countLabel(count: number | undefined) {
  return count === undefined ? "—" : count + "건";
}


function StatusPill({ label, status }: { label: string; status: string }) {
  const normalized = identifier(status) || "unknown";
  const tone = statusTone(normalized);
  const classes = {
    neutral: "border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300",
    info: "border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-900 dark:bg-sky-950/30 dark:text-sky-200",
    good: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200",
    warn: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100",
    bad: "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200",
  }[tone];
  const customLabel = normalized === "registered" ? "등록" : normalized === "not_registered" ? "미등록" : statusLabel(normalized);
  return <span className={`inline-flex min-w-0 items-center rounded-md border px-1.5 py-0.5 text-[10px] font-black ${classes}`}><span className="mr-1 opacity-60">{label}</span><span className="truncate">{customLabel}</span></span>;
}

function StateAxis({ label, status }: { label: string; status: string }) {
  const axis = label === "실행"
    ? "execution"
    : label === "검토"
      ? "review"
      : label.includes("승인") || label.includes("Snapshot")
        ? "approval"
        : null;
  return (
    <div className="rounded-lg border border-slate-200 p-2 dark:border-slate-800">
      <p className="text-[10px] font-bold text-slate-400">{label}</p>
      <div className="mt-1">{axis ? <SharedAxisBadge axis={axis} status={status} /> : <StatusPill label="" status={status} />}</div>
    </div>
  );
}

function SharedAxisBadge({ axis, status }: { axis: "execution" | "review" | "approval"; status: string }) {
  if (axis === "execution") {
    return <ExecutionStatusBadge status={normalizeExecutionStatus(status)} />;
  }
  if (axis === "review") {
    return <ReviewStatusBadge status={normalizeReviewStatus(status)} />;
  }
  return <ApprovalStatusBadge status={normalizeApprovalStatus(status)} />;
}

function GateLine({ ok, label, advisory = false }: { ok: boolean; label: string; advisory?: boolean }) {
  return (
    <p className="mt-2 flex items-start gap-2">
      {ok ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" /> : advisory ? <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" /> : <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-600" />}
      <span>{label}{advisory && !ok ? " · 서버 정책에 따라 차단될 수 있음" : ""}</span>
    </p>
  );
}

function LocatorPrecisionBadge({ locator }: { locator: SourceLocatorRecord }) {
  const precise = Boolean(
    finiteNumber(locator.pageNumber ?? locator.page) !== undefined
      || (identifier(locator.sheetName ?? locator.sheet) && identifier(locator.cellReference ?? locator.cellRange ?? locator.cell))
      || identifier(locator.jsonPath)
      || (identifier(locator.tableId) && finiteNumber(locator.rowIndex ?? locator.row) !== undefined)
      || (identifier(locator.apiEndpoint) && identifier(locator.apiRowId))
      || identifier(locator.artifactKey),
  );
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[10px] font-black ${precise ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200" : "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-100"}`}>
      <LocateFixed className="h-3 w-3" /> {precise ? "정확 위치" : "제한 위치"}
    </span>
  );
}

function LocatorCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 bg-white px-4 py-3 dark:bg-slate-950">
      <p className="text-[10px] font-black text-slate-400">{label}</p>
      <p className="mt-1 break-all font-mono text-xs font-bold text-slate-800 dark:text-slate-200">{value}</p>
    </div>
  );
}

function MetaCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-950">
      <p className="text-[10px] font-black text-slate-400">{label}</p>
      <p className="mt-1 break-all text-xs font-bold text-slate-700 dark:text-slate-200">{value}</p>
    </div>
  );
}

function Notice({ tone, children }: { tone: "good" | "warn" | "bad"; children: ReactNode }) {
  const classes = tone === "good"
    ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100"
    : tone === "warn"
      ? "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"
      : "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-100";
  return <div className={`rounded-lg border px-4 py-3 text-xs font-bold leading-5 ${classes}`}>{children}</div>;
}

function ControlledEmpty({ icon, title, body, compact = false }: { icon: ReactNode; title: string; body: string; compact?: boolean }) {
  return (
    <div className={`rounded-xl border border-dashed border-slate-300 bg-white text-center dark:border-slate-700 dark:bg-slate-950 ${compact ? "p-5" : "p-10"}`}>
      <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-slate-100 text-slate-500 dark:bg-slate-900">{icon}</div>
      <p className="mt-3 text-sm font-black text-slate-950 dark:text-white">{title}</p>
      <p className="mx-auto mt-1 max-w-xl text-xs leading-5 text-slate-500">{body}</p>
    </div>
  );
}

function sheetCellLabel(locator: SourceLocatorRecord) {
  const sheet = identifier(locator.sheetName ?? locator.sheet);
  const cell = identifier(locator.cellReference ?? locator.cellRange ?? locator.cell);
  return sheet || cell ? `${sheet || "시트 미확인"}${cell ? `!${cell}` : ""}` : "-";
}

function tableCoordinateLabel(locator: SourceLocatorRecord) {
  const table = identifier(locator.tableId);
  const row = finiteNumber(locator.rowIndex ?? locator.row);
  const column = finiteNumber(locator.columnIndex ?? locator.column);
  const values = [table || "", row !== undefined ? `행 ${row}` : "", column !== undefined ? `열 ${column}` : ""].filter(Boolean);
  return values.length ? values.join(" · ") : "-";
}

function paragraphJsonLabel(locator: SourceLocatorRecord) {
  const paragraph = finiteNumber(locator.paragraphIndex);
  const path = identifier(locator.jsonPath);
  const values = [paragraph !== undefined ? `문단 ${paragraph}` : "", path].filter(Boolean);
  return values.length ? values.join(" · ") : "-";
}

function bboxLabel(locator: SourceLocatorRecord) {
  const bbox = locatorBbox(locator);
  if (!bbox) return "-";
  return `${bbox.map((value) => value.toFixed(2)).join(", ")} · ${locatorCoordinateSpace(locator) || "좌표계 미확인"}`;
}

function locatorMetadata(locator: SourceLocatorRecord): Record<string, unknown> {
  return locator.metadata && typeof locator.metadata === "object" && !Array.isArray(locator.metadata)
    ? locator.metadata
    : {};
}

function locatorBbox(locator: SourceLocatorRecord): [number, number, number, number] | null {
  const metadata = locatorMetadata(locator);
  const candidate = Array.isArray(locator.bbox) ? locator.bbox : metadata.bbox;
  if (!Array.isArray(candidate) || candidate.length !== 4) return null;
  const numbers = candidate.map(Number);
  if (!numbers.every(Number.isFinite)) return null;
  const [left, top, right, bottom] = numbers as [number, number, number, number];
  if (right < left || bottom < top) return null;
  return [left, top, right, bottom];
}

function locatorCoordinateSpace(locator: SourceLocatorRecord) {
  const metadata = locatorMetadata(locator);
  return identifier(locator.coordinateSpace ?? metadata.bboxCoordinateSystem);
}
