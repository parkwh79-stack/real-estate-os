"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Database,
  FileText,
  Link2,
  Loader2,
  LockKeyhole,
  Send,
  RefreshCw,
  Settings2,
} from "lucide-react";
import {
  ApprovalStatusBadge,
  ExecutionStatusBadge,
  ReportEligibilityBadge,
  normalizeApprovalStatus,
  normalizeExecutionStatus,
} from "../../../../components/ui/primitives";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import {
  fetchDealStateRecordGroups,
  hasSourceLocator,
  locatorSummary,
  type DealStateRecord,
} from "../../../../lib/deal-state-records";
import { EngineMetricsPanel } from "../../../../components/orchestration/engine-metrics-panel";
import { ConsensusPanel } from "../../../../components/orchestration/consensus-panel";
import { CreditDocumentsPanel } from "../../../../components/orchestration/credit-documents-panel";
import { SimilarCasesRagPanel } from "../../../../components/orchestration/similar-cases-rag-panel";
import { TermAnnotationsPanel } from "../../../../components/orchestration/term-annotations-panel";
import { ComparisonReportReview, type ComparisonReportSnapshot, type ReportCashFlowSnapshot, type ReportCalculationSnapshots, type ReportFinancingBasis } from "../../../../components/feasibility/comparison-report-review";
import type { ReportInputReconciliation } from "../../../../components/feasibility/report-input-reconciliation";

interface ProjectResponse {
  project?: { name?: string; type?: string };
  [key: string]: unknown;
}

interface ReportTemplate {
  templateId?: string;
  name?: string;
  version?: number;
  sections?: Array<Record<string, unknown>>;
}

interface ParagraphEvidenceLink {
  paragraphId?: string;
  sectionKey?: string;
  templateSectionKey?: string;
  title?: string;
  linkMethod?: string;
  linkedRecordCount?: number;
  sourceLocatorCoverage?: {
    linked?: number;
    linkedWithLocator?: number;
    missingLocator?: number;
  };
  linkedRecords?: DealStateRecord[];
  podAiReviewFindings?: PodAiReviewFinding[];
  podAiReviewBlocking?: boolean;
}

interface PodAiReviewFinding {
  findingId?: string;
  podKey?: string;
  category?: string;
  severity?: string;
  message?: string;
  recommendedAction?: string;
  sourceLocators?: Record<string, unknown>[];
  sourceArtifactKeys?: string[];
  runId?: string;
  reviewId?: string;
  artifactBindingStatus?: string;
}

interface ParagraphEvidenceLinks {
  schemaVersion?: string;
  linkMethod?: string;
  recordCount?: number;
  sectionCount?: number;
  linkedRecordCount?: number;
  sourceLocatorReadySectionCount?: number;
  podReviewReadStatus?: string;
  podReviewReadMessage?: string;
  links?: ParagraphEvidenceLink[];
}

interface GeneratedReportResponse {
  report_id?: string;
  version?: number;
  report_markdown?: string;
  paragraphEvidenceLinks?: ParagraphEvidenceLinks;
  metadata?: {
    complete?: boolean;
    template_unmapped_section_count?: number;
    paragraph_evidence_link_count?: number;
    paragraph_evidence_linked_record_count?: number;
    paragraph_evidence_source_locator_ready_section_count?: number;
  };
  report?: {
    cashFlowComparisonSnapshot?: ComparisonReportSnapshot;
    cashFlowCalculationSnapshot?: ReportCashFlowSnapshot;
    calculationSnapshots?: ReportCalculationSnapshots;
    calculationReconciliation?: ReportInputReconciliation;
    financingBasis?: ReportFinancingBasis;
    sectionOrder?: Array<{ key: string; title: string }>;
    paragraphEvidenceLinks?: ParagraphEvidenceLinks;
    appliedTemplate?: { name?: string; version?: number };
    templateApplication?: {
      sectionCoverage?: Array<{
        templateSectionKey?: string;
        reportSectionKey?: string;
        title?: string;
        enabled?: boolean;
        hasReportSection?: boolean;
        sourceAgents?: string[];
        requiredEvidence?: string[];
      }>;
    };
  };
}

interface SavedReportMetadata {
  report_id: string;
  project_id?: string;
  run_id?: string | null;
  version?: number;
  title?: string;
  status?: "draft" | "published" | string;
  created_at?: string;
  published_at?: string | null;
  published_by?: string | null;
  export_count?: number;
  metadata?: {
    complete?: boolean;
    report_eligible?: boolean;
    usage_restriction?: string | null;
    content_sha256?: string;
    template_unmapped_section_count?: number;
    paragraph_evidence_link_count?: number;
    paragraph_evidence_linked_record_count?: number;
    paragraph_evidence_source_locator_ready_section_count?: number;
  };
  publication?: {
    publishedBy?: string;
    publishedAt?: string;
    contentSha256?: string;
    decisionPackage?: {
      projectSnapshotId?: string;
      runId?: string;
      approvalStatus?: string;
    };
  } | null;
}

interface SavedReportDetail extends SavedReportMetadata {
  report_markdown?: string;
  report?: GeneratedReportResponse["report"] & {
    paragraphEvidenceLinks?: ParagraphEvidenceLinks;
  };
}

interface ApprovalReadinessGate {
  pass?: boolean;
  status?: string;
  summary?: {
    blockingIssueCount?: number;
    warningCount?: number;
  };
  blockers?: Array<{ code?: string; message?: string }>;
  requiredActions?: string[];
}

interface OrchestrationRun {
  run_id: string;
  status?: string;
  workflow?: string;
  started_at?: string;
  completed_at?: string;
  approvalStatus?: string;
  approval?: {
    approved_by?: string;
    approved_at?: string;
  } | null;
  artifacts?: {
    approvalReadinessGate?: ApprovalReadinessGate;
  };
}

interface OrchestrationSummary {
  run_id: string;
  status?: string;
  started_at?: string;
  completed_at?: string;
}

type DealStateGroups = {
  metrics: DealStateRecord[];
  risks: DealStateRecord[];
  evidence: DealStateRecord[];
  assumptions: DealStateRecord[];
};

type ReportSectionRow = {
  templateSectionKey?: string;
  reportSectionKey?: string;
  title?: string;
  enabled?: boolean;
  hasReportSection?: boolean;
  sourceAgents?: string[];
  requiredEvidence?: string[];
};

const EMPTY_GROUPS: DealStateGroups = { metrics: [], risks: [], evidence: [], assumptions: [] };

export default function ReportPreviewPage() {
  const params = useParams<{ id: string }>();
  const projectId = String(params.id);

  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [report, setReport] = useState<GeneratedReportResponse | null>(null);
  const [reports, setReports] = useState<SavedReportMetadata[]>([]);
  const [selectedReportId, setSelectedReportId] = useState("");
  const [latestRun, setLatestRun] = useState<OrchestrationRun | null>(null);
  const [boundRun, setBoundRun] = useState<OrchestrationRun | null>(null);
  const [dealState, setDealState] = useState<DealStateGroups>(EMPTY_GROUPS);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [selectingReport, setSelectingReport] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const selectedTemplate = useMemo(
    () => templates.find((item) => item.templateId === selectedTemplateId),
    [selectedTemplateId, templates],
  );
  const allRecords = useMemo(
    () => [...dealState.risks, ...dealState.metrics, ...dealState.evidence, ...dealState.assumptions],
    [dealState],
  );
  const [missingLocatorOnly, setMissingLocatorOnly] = useState(false);
  const visibleRecords = useMemo(
    () => (missingLocatorOnly ? allRecords.filter((record) => !hasSourceLocator(record)) : allRecords),
    [allRecords, missingLocatorOnly],
  );
  const paragraphLinks = report?.report?.paragraphEvidenceLinks ?? report?.paragraphEvidenceLinks;

  const selectedReport = useMemo(
    () => reports.find((item) => item.report_id === selectedReportId) ?? null,
    [reports, selectedReportId],
  );
  const latestRunApproval = normalizeApprovalStatus(
    latestRun?.approvalStatus ?? (latestRun?.approval ? "approved" : "not_requested"),
  );
  const boundRunApproval = normalizeApprovalStatus(
    boundRun?.approvalStatus ?? (boundRun?.approval ? "approved" : "not_requested"),
  );
  const selectedGate = boundRun?.artifacts?.approvalReadinessGate;
  const selectedReportEligibility = !selectedReport
    ? "unknown"
    : selectedReport.status === "published" || selectedReport.metadata?.report_eligible === true
      ? "eligible"
      : "analysis_only";
  const canPublish = Boolean(
    selectedReport
      && selectedReport.status === "draft"
      && selectedReport.run_id
      && boundRun?.run_id === selectedReport.run_id
      && boundRunApproval === "approved"
      && selectedGate?.pass === true,
  );
  const publishBlockReason = reportPublishBlockReason(selectedReport, boundRun, boundRunApproval, selectedGate);

  const loadData = useCallback(async (preferredReportId?: string) => {
    setLoading(true);
    setError(null);
    try {
      const [projectRes, templateRes, reportsRes, runsRes, recordGroups] = await Promise.all([
        fetch(`${apiBase}/projects/${projectId}`, { cache: "no-store", headers: authHeaders("web") }),
        fetch(`${apiBase}/projects/${projectId}/report-templates`, { cache: "no-store", headers: authHeaders("web") }),
        fetch(`${apiBase}/projects/${projectId}/reports`, { cache: "no-store", headers: authHeaders("web") }),
        fetch(`${apiBase}/projects/${projectId}/orchestrations`, { cache: "no-store", headers: authHeaders("web") }),
        fetchDealStateRecordGroups(projectId, 700),
      ]);
      if (!projectRes.ok) throw new Error(await responseError(projectRes));
      if (!templateRes.ok) throw new Error(await responseError(templateRes));
      if (!reportsRes.ok) throw new Error(await responseError(reportsRes));
      if (!runsRes.ok) throw new Error(await responseError(runsRes));
      const templateBody = (await templateRes.json()) as { templates?: ReportTemplate[] };
      const loadedTemplates = templateBody.templates ?? [];
      const reportBody = (await reportsRes.json()) as SavedReportMetadata[] | { reports?: SavedReportMetadata[] };
      const loadedReports = sortReports(Array.isArray(reportBody) ? reportBody : reportBody.reports ?? []);
      const runBody = (await runsRes.json()) as OrchestrationSummary[] | {
        runs?: OrchestrationSummary[];
        orchestrations?: OrchestrationSummary[];
      };
      const loadedRuns = Array.isArray(runBody) ? runBody : runBody.runs ?? runBody.orchestrations ?? [];
      const latestRunSummary = sortRuns(loadedRuns)[0];
      const targetReport = loadedReports.find((item) => item.report_id === preferredReportId) ?? loadedReports[0] ?? null;

      const latestRunDetail = latestRunSummary
        ? await fetchOrchestrationRun(projectId, latestRunSummary.run_id)
        : null;
      const selectedBoundRun = targetReport?.run_id
        ? targetReport.run_id === latestRunDetail?.run_id
          ? latestRunDetail
          : await fetchOrchestrationRun(projectId, targetReport.run_id)
        : null;
      const selectedDetail = targetReport
        ? await fetchSavedReport(projectId, targetReport.report_id)
        : null;

      setProject((await projectRes.json()) as ProjectResponse);
      setTemplates(loadedTemplates);
      setReports(loadedReports);
      setSelectedReportId(targetReport?.report_id ?? "");
      setReport(selectedDetail ? generatedResponseFromSavedReport(selectedDetail) : null);
      setLatestRun(latestRunDetail);
      setBoundRun(selectedBoundRun);
      setDealState(recordGroups);
      setSelectedTemplateId((current) => current || loadedTemplates[0]?.templateId || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "보고서 생성 정보를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  async function generateReport() {
    setGenerating(true);
    setError(null);
    setMessage(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/reports/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders("report_editor") },
        body: JSON.stringify({
          templateId: selectedTemplateId || undefined,
          options: { format: "json+markdown" },
        }),
      });
      if (!res.ok) throw new Error(await responseError(res));
      const body = (await res.json()) as GeneratedReportResponse;
      setReport(body);
      setMessage(`분석용 초안이 생성되었습니다. Report ID: ${body.report_id ?? "-"}`);
      await loadData(body.report_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "보고서 생성에 실패했습니다.");
    } finally {
      setGenerating(false);
    }
  }

  async function selectSavedReport(reportId: string) {
    if (!reportId || reportId === selectedReportId) return;
    const metadata = reports.find((item) => item.report_id === reportId);
    if (!metadata) return;
    setSelectingReport(true);
    setError(null);
    setMessage(null);
    try {
      const [detail, selectedBoundRun] = await Promise.all([
        fetchSavedReport(projectId, reportId),
        metadata.run_id ? fetchOrchestrationRun(projectId, metadata.run_id) : Promise.resolve(null),
      ]);
      setSelectedReportId(reportId);
      setReport(generatedResponseFromSavedReport(detail));
      setBoundRun(selectedBoundRun);
    } catch (err) {
      setError(err instanceof Error ? err.message : "선택한 보고서를 불러오지 못했습니다.");
    } finally {
      setSelectingReport(false);
    }
  }

  async function publishReport() {
    if (!selectedReport || !canPublish) return;
    setPublishing(true);
    setError(null);
    setMessage(null);
    try {
      const response = await fetch(
        `${apiBase}/projects/${projectId}/reports/${selectedReport.report_id}/publish`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders("web") },
        },
      );
      if (!response.ok) throw new Error(await responseError(response));
      const published = (await response.json()) as SavedReportMetadata;
      setMessage(`최종 보고서가 발행되었습니다. Report ID: ${published.report_id}`);
      await loadData(published.report_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "최종 보고서를 발행하지 못했습니다.");
    } finally {
      setPublishing(false);
    }
  }

  const coverage = report?.report?.templateApplication?.sectionCoverage ?? [];
  const sectionRows: ReportSectionRow[] = coverage.length ? coverage : report?.report?.cashFlowComparisonSnapshot
    ? (report.report.sectionOrder ?? []).map((section) => ({ reportSectionKey: section.key, title: section.title, enabled: true, hasReportSection: true }))
    : defaultSectionRows();
  const sourceLocatorMissing = allRecords.filter((record) => !hasSourceLocator(record)).length;

  return (
    <div className="space-y-6 pb-8">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-3">
          <Link
            href={`/project/${projectId}`}
            className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
            aria-label="프로젝트로 돌아가기"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <p className="text-sm font-semibold text-slate-500">프로젝트 #{projectId}</p>
            <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">PF 심사보고서</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">
              분석용 초안 생성과 최종 발행은 서로 다른 단계입니다. 초안은 Deal State 근거를 보존하지만,
              승인된 PF Review와 통과한 Approval Readiness Gate에 결합된 보고서만 최종 발행할 수 있습니다.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void loadData(selectedReportId || undefined)}
            disabled={loading}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            새로고침
          </button>
          <Link
            href={`/project/${projectId}/report-templates`}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          >
            <Settings2 className="h-4 w-4" />
            템플릿 편집
          </Link>
        </div>
      </header>

      {error && <Notice tone="bad" message={error} />}
      {message && <Notice tone="good" message={message} />}

      <ComparisonReportReview projectId={projectId} reportId={report?.report_id} snapshot={report?.report?.cashFlowComparisonSnapshot}
        cashFlowSnapshot={report?.report?.cashFlowCalculationSnapshot}
        calculationSnapshots={report?.report?.calculationSnapshots}
        reconciliation={report?.report?.calculationReconciliation}
        financingBasis={report?.report?.financingBasis}
        disabled={loading || generating || publishing || selectingReport} onCreated={loadData} onBusyChange={setGenerating} />

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[1.05fr_0.95fr]">
        <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="font-bold text-slate-950 dark:text-white">저장 보고서</h3>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                저장된 버전의 상태와 연결된 PF Review를 확인합니다. 파일 내보내기는 최종 승인이나 발행을 의미하지 않습니다.
              </p>
            </div>
            <ReportEligibilityBadge status={selectedReportEligibility} />
          </div>

          {loading ? (
            <div className="mt-4 flex items-center gap-2 text-sm font-semibold text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              보고서 원장을 불러오는 중
            </div>
          ) : reports.length ? (
            <div className="mt-4 space-y-4">
              <label className="block space-y-1">
                <span className="text-xs font-bold text-slate-500">검토할 보고서 버전</span>
                <div className="relative">
                  <select
                    value={selectedReportId}
                    onChange={(event) => void selectSavedReport(event.target.value)}
                    disabled={selectingReport}
                    className="h-11 w-full rounded-lg border border-slate-200 bg-white px-3 pr-10 text-sm font-semibold text-slate-900 outline-none focus:border-slate-400 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                  >
                    {reports.map((item) => (
                      <option key={item.report_id} value={item.report_id}>
                        v{item.version ?? "-"} · {item.status === "published" ? "최종 발행" : "분석용 초안"} · {formatDateTime(item.created_at)}
                      </option>
                    ))}
                  </select>
                  {selectingReport ? <Loader2 className="absolute right-3 top-3.5 h-4 w-4 animate-spin text-slate-400" /> : null}
                </div>
              </label>

              <dl className="grid gap-3 text-xs sm:grid-cols-2">
                <MetadataRow label="Report ID" value={selectedReport?.report_id ?? "-"} mono />
                <MetadataRow label="PF Review Run" value={selectedReport?.run_id ?? "연결 없음 (수동 초안)"} mono={Boolean(selectedReport?.run_id)} />
                <MetadataRow label="생성 시각" value={formatDateTime(selectedReport?.created_at)} />
                <MetadataRow label="Content hash" value={shortHash(selectedReport?.metadata?.content_sha256)} mono />
              </dl>
            </div>
          ) : (
            <div className="mt-4 rounded-lg border border-dashed border-slate-300 p-5 text-center dark:border-slate-700">
              <p className="text-sm font-bold text-slate-700 dark:text-slate-200">저장된 보고서가 없습니다.</p>
              <p className="mt-1 text-xs text-slate-500">아래에서 분석용 초안을 먼저 생성하세요.</p>
            </div>
          )}
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="font-bold text-slate-950 dark:text-white">최종 발행 통제</h3>
              <p className="mt-1 text-xs leading-5 text-slate-500">서버가 승인, 스냅샷 계보, Gate 및 보고서 해시를 발행 시점에 다시 검증합니다.</p>
            </div>
            <LockKeyhole className="h-5 w-5 text-slate-500" />
          </div>

          <div className="mt-4 rounded-lg bg-slate-50 p-4 dark:bg-slate-800/60">
            <p className="text-xs font-bold text-slate-500">최신 PF Review</p>
            {latestRun ? (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <ExecutionStatusBadge status={normalizeExecutionStatus(latestRun.status)} />
                <ApprovalStatusBadge status={latestRunApproval} />
                <span className="font-mono text-[11px] text-slate-500">{latestRun.run_id}</span>
              </div>
            ) : (
              <p className="mt-2 text-sm font-semibold text-slate-500">실행된 PF Review가 없습니다.</p>
            )}
          </div>

          {selectedReport?.run_id ? (
            <div className="mt-3 rounded-lg border border-slate-200 p-4 dark:border-slate-700">
              <p className="text-xs font-bold text-slate-500">선택 보고서에 결합된 PF Review</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <ExecutionStatusBadge status={normalizeExecutionStatus(boundRun?.status)} />
                <ApprovalStatusBadge status={boundRunApproval} />
                <ReportEligibilityBadge
                  status={selectedReport.status === "published" ? "eligible" : selectedGate?.pass === true ? "analysis_only" : "blocked"}
                />
              </div>
              <p className="mt-2 text-xs leading-5 text-slate-500">
                Approval Readiness Gate: {selectedGate?.pass === true ? "통과" : "미통과"}
                {typeof selectedGate?.summary?.blockingIssueCount === "number"
                  ? ` · 차단 ${selectedGate.summary.blockingIssueCount}건`
                  : ""}
              </p>
            </div>
          ) : (
            <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
              수동 생성 보고서는 PF Review 실행에 결합되지 않은 분석 전용 초안입니다. 내보낸 파일도 최종 승인 자료로 사용할 수 없습니다.
            </div>
          )}

          <button
            type="button"
            onClick={publishReport}
            disabled={!canPublish || publishing || loading}
            className="mt-4 inline-flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-blue-700 px-4 text-sm font-bold text-white shadow-sm hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-500 dark:disabled:bg-slate-800"
          >
            {publishing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            {selectedReport?.status === "published" ? "최종 발행 완료" : "최종 발행"}
          </button>
          {!canPublish ? <p className="mt-2 text-xs font-semibold leading-5 text-slate-500">{publishBlockReason}</p> : null}
          {selectedReport?.status === "published" ? (
            <p className="mt-2 text-xs leading-5 text-emerald-700 dark:text-emerald-300">
              {formatDateTime(selectedReport.published_at)} · 서버 발행자 {selectedReport.published_by ?? "확인 불가"}
            </p>
          ) : null}
        </div>
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-4 flex items-center gap-2">
            <FileText className="h-5 w-5 text-slate-700 dark:text-slate-200" />
            <h3 className="font-bold text-slate-950 dark:text-white">생성 기준</h3>
          </div>

          {loading ? (
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              불러오는 중
            </div>
          ) : (
            <div className="space-y-4">
              <div className="rounded-lg bg-slate-50 p-4 dark:bg-slate-800/70">
                <p className="text-xs font-bold text-slate-500">사업명</p>
                <p className="mt-1 text-sm font-black text-slate-950 dark:text-white">{project?.project?.name ?? "사업명 미입력"}</p>
                <p className="mt-1 text-xs text-slate-500">상품유형: {project?.project?.type ?? "-"}</p>
              </div>

              <label className="space-y-1">
                <span className="text-xs font-bold text-slate-500">적용 템플릿</span>
                <select
                  value={selectedTemplateId}
                  onChange={(event) => setSelectedTemplateId(event.target.value)}
                  className="h-11 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-slate-400 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                >
                  <option value="">기본 Phase 11 템플릿</option>
                  {templates.map((template) => (
                    <option key={template.templateId ?? template.name} value={template.templateId}>
                      {template.name ?? template.templateId} {template.version ? `(v${template.version})` : ""}
                    </option>
                  ))}
                </select>
              </label>

              <div className="grid gap-3 md:grid-cols-3">
                <Kpi label="저장 템플릿" value={`${templates.length}건`} />
                <Kpi label="선택 버전" value={selectedTemplate?.version ? `v${selectedTemplate.version}` : "-"} />
                <Kpi
                  label="섹션 수"
                  value={
                    Array.isArray(selectedTemplate?.sections)
                      ? `${selectedTemplate.sections.length}개`
                      : "미확인"
                  }
                />
              </div>

              <button
                type="button"
                onClick={generateReport}
                disabled={generating}
                className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-white dark:text-slate-950"
              >
                {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                분석용 초안 생성
              </button>
              <p className="text-xs leading-5 text-amber-700 dark:text-amber-300">
                이 작업은 승인 상태를 변경하지 않습니다. 생성 결과와 내보내기는 모두 분석용 초안입니다.
              </p>
            </div>
          )}
        </div>

        <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-4 flex items-center gap-2">
            <Database className="h-5 w-5 text-slate-700 dark:text-slate-200" />
            <h3 className="font-bold text-slate-950 dark:text-white">보고서 근거 원장</h3>
          </div>
          <div className="grid gap-3 md:grid-cols-5">
            <Kpi label="지표" value={`${dealState.metrics.length}건`} />
            <Kpi label="리스크" value={`${dealState.risks.length}건`} />
            <Kpi label="증거" value={`${dealState.evidence.length}건`} />
            <Kpi label="가정" value={`${dealState.assumptions.length}건`} />
            <button
              type="button"
              onClick={() => setMissingLocatorOnly((v) => !v)}
              aria-pressed={missingLocatorOnly}
              title="클릭하면 근거 위치가 부족한 record만 필터링합니다"
              className={`rounded-lg border p-3 text-left transition-colors ${
                missingLocatorOnly
                  ? "border-amber-400 bg-amber-50 ring-1 ring-amber-300 dark:border-amber-600 dark:bg-amber-950/30"
                  : "border-slate-200 hover:border-amber-300 hover:bg-amber-50/40 dark:border-slate-800 dark:hover:bg-amber-950/20"
              }`}
            >
              <p className="text-xs font-semibold text-slate-500">근거 위치 부족 {missingLocatorOnly ? "· 필터중" : ""}</p>
              <p className="mt-1 text-lg font-black text-amber-700 dark:text-amber-300">{sourceLocatorMissing}건</p>
            </button>
          </div>

          {missingLocatorOnly ? (
            <div className="mt-3 flex items-center justify-between rounded-md bg-amber-50 px-3 py-2 text-xs font-bold text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
              <span>근거 위치가 부족한 record {visibleRecords.length}건만 표시 중 — 문서/셀/API 위치를 보강하세요.</span>
              <button type="button" onClick={() => setMissingLocatorOnly(false)} className="underline">전체 보기</button>
            </div>
          ) : null}
          <div className="mt-4 max-h-72 overflow-auto rounded-lg border border-slate-200 dark:border-slate-800">
            <table className="w-full min-w-[760px] text-left text-xs">
              <thead className="bg-slate-50 text-slate-500 dark:bg-slate-800">
                <tr>
                  <th className="px-3 py-2">유형</th>
                  <th className="px-3 py-2">Key</th>
                  <th className="px-3 py-2">출처</th>
                  <th className="px-3 py-2">근거 위치</th>
                </tr>
              </thead>
              <tbody>
                {visibleRecords.slice(0, 80).map((record) => (
                  <tr key={record.recordId ?? `${record.recordType}-${record.key}`} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="px-3 py-2 font-bold text-slate-700 dark:text-slate-200">{recordTypeLabel(record.recordType)}</td>
                    <td className="px-3 py-2 font-black text-slate-950 dark:text-white">{record.key ?? record.label ?? "-"}</td>
                    <td className="px-3 py-2 text-slate-500">{record.sourceType ?? "-"}</td>
                    <td className={`px-3 py-2 font-semibold ${hasSourceLocator(record) ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"}`}>
                      {locatorSummary(record)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <ParagraphEvidenceLinkPanel links={paragraphLinks} fallbackSections={sectionRows} />

      {report ? <TemplateApplication report={report} /> : null}

      <EngineMetricsPanel projectId={projectId} />
      <ConsensusPanel projectId={projectId} />
      <CreditDocumentsPanel projectId={projectId} />
      <SimilarCasesRagPanel projectId={projectId} />
      <TermAnnotationsPanel projectId={projectId} />

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="mb-4 flex items-center gap-2">
          <FileText className="h-5 w-5 text-slate-700 dark:text-slate-200" />
          <h3 className="font-bold text-slate-950 dark:text-white">보고서 미리보기</h3>
        </div>
        <div className="min-h-[420px] rounded-lg bg-slate-50 p-4 dark:bg-slate-950">
          {report?.report_markdown ? (
            <pre className="whitespace-pre-wrap font-sans text-sm leading-7 text-slate-800 dark:text-slate-100">{report.report_markdown}</pre>
          ) : (
            <p className="text-sm leading-6 text-slate-500">보고서를 생성하면 Markdown 미리보기가 표시됩니다.</p>
          )}
        </div>
      </section>
    </div>
  );
}

function ParagraphEvidenceLinkPanel({
  links,
  fallbackSections,
}: {
  links?: ParagraphEvidenceLinks;
  fallbackSections: ReportSectionRow[];
}) {
  const persistedLinks = links?.links ?? [];
  const hasGeneratedLinks = persistedLinks.length > 0;
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-center gap-2">
          <Link2 className="h-5 w-5 text-slate-700 dark:text-slate-200" />
          <div>
            <h3 className="font-bold text-slate-950 dark:text-white">문단별 저장 근거</h3>
            <p className="mt-1 text-xs text-slate-500">
              보고서 생성 당시 저장한 근거와 검토 의견입니다.
            </p>
          </div>
        </div>
        <div className="grid gap-2 text-xs md:grid-cols-3">
          <MiniStat label="링크 방식" value={links?.linkMethod ?? (hasGeneratedLinks ? "stored" : "보고서 생성 전")} />
          <MiniStat label="연결 레코드" value={`${links?.linkedRecordCount ?? 0}건`} />
          <MiniStat label="근거 위치 섹션" value={`${links?.sourceLocatorReadySectionCount ?? 0}개`} />
        </div>
      </div>

      {links?.podReviewReadStatus === "unavailable" ? (
        <p role="alert" className="mb-3 rounded bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-100">{links.podReviewReadMessage}</p>
      ) : null}
      {!hasGeneratedLinks ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm font-semibold text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          아직 저장된 문단별 근거 링크가 없습니다. 보고서를 생성하면 서버가 Deal State 원장을 읽어
          `paragraphEvidenceLinks`를 저장합니다.
        </div>
      ) : null}

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        {(hasGeneratedLinks ? persistedLinks : fallbackSectionsToLinks(fallbackSections)).slice(0, 14).map((link) => (
          <div key={link.paragraphId ?? link.sectionKey ?? link.title} className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="font-black text-slate-950 dark:text-white">{link.title ?? link.sectionKey ?? "보고서 섹션"}</p>
                <p className="mt-1 text-xs text-slate-500">{link.sectionKey ?? link.templateSectionKey ?? "-"}</p>
              </div>
              <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                {link.linkedRecordCount ?? link.linkedRecords?.length ?? 0}건 근거
              </span>
            </div>
            {(link.podAiReviewFindings ?? []).length ? (
              <div className={`mt-3 rounded-lg border p-3 ${link.podAiReviewBlocking ? "border-rose-200 bg-rose-50 dark:border-rose-900 dark:bg-rose-950/30" : "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/30"}`}>
                <p className={`text-xs font-black ${link.podAiReviewBlocking ? "text-rose-800 dark:text-rose-200" : "text-amber-800 dark:text-amber-200"}`}>
                  Pod AI Review 지적 {link.podAiReviewBlocking ? "· 차단(blocking)" : ""}
                </p>
                <ul className="mt-1.5 space-y-1">
                  {(link.podAiReviewFindings ?? []).map((f, i) => (
                    <li key={f.findingId ?? i} className="text-xs text-slate-700 dark:text-slate-200">
                      <span className="font-mono text-[10px] text-slate-500">[{f.podKey}·{f.severity}]</span> {f.message}
                      {f.recommendedAction ? <span className="mt-0.5 block text-[11px] text-slate-500">→ {f.recommendedAction}</span> : null}
                      <span className="mt-1 block text-[11px] text-slate-500">{f.artifactBindingStatus === "matched" ? "보고서 생성 시점의 Pod 산출물과 연결됨" : f.artifactBindingStatus === "mismatch" ? "현재 Pod 산출물과 다른 과거 검토 — 현재 계산의 지적으로 확정하지 않음" : "Pod 산출물 연결 여부 미확인"}</span>
                      {(f.sourceLocators ?? []).map((locator, index) => <span key={index} className="mt-0.5 block break-words text-[11px] text-slate-500">근거: {locatorSummary({ sourceLocator: locator })}</span>)}
                      <details className="mt-1 break-all text-[11px] text-slate-500"><summary className="cursor-pointer">저장된 검토 식별정보</summary>검토: {f.reviewId || "미기록"}<br />실행: {f.runId || "미기록"}<br />산출물: {f.sourceArtifactKeys?.join(", ") || "미기록"}</details>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <div className="mt-3 space-y-2">
              {(link.linkedRecords ?? []).length ? (
                (link.linkedRecords ?? []).slice(0, 5).map((record) => (
                  <div key={record.recordId ?? `${record.recordType}-${record.key}`} className="rounded-lg bg-slate-50 p-3 text-xs dark:bg-slate-800/70">
                    <p className="font-bold text-slate-900 dark:text-white">
                      {recordTypeLabel(record.recordType)} · {record.key ?? record.label ?? "-"}
                    </p>
                    <p className="mt-1 text-slate-500">
                      {record.sourceType ?? "-"} · {locatorSummary(record)}
                    </p>
                  </div>
                ))
              ) : (
                <p className="rounded-lg bg-amber-50 p-3 text-xs font-semibold text-amber-800 dark:bg-amber-950/30 dark:text-amber-100">
                  연결된 저장 근거가 부족합니다. 해당 Pod 실행 또는 source locator 보강이 필요합니다.
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function fallbackSectionsToLinks(sections: ReportSectionRow[]): ParagraphEvidenceLink[] {
  return sections.map((section) => ({
    paragraphId: `pending:${section.reportSectionKey ?? section.templateSectionKey ?? section.title}`,
    sectionKey: section.reportSectionKey,
    templateSectionKey: section.templateSectionKey,
    title: section.title,
    linkedRecordCount: 0,
    linkedRecords: [],
  }));
}

function defaultSectionRows(): ReportSectionRow[] {
  return [
    { reportSectionKey: "landSecuring", title: "사업부지 확보 및 토지권리" },
    { reportSectionKey: "marketAnalysis", title: "분양성 및 시장가격" },
    { reportSectionKey: "businessFeasibility", title: "사업수지 및 사업성" },
    { reportSectionKey: "summaryCashFlow", title: "요약 Cash Flow" },
    { reportSectionKey: "financialTerms", title: "PF 금융조건 및 Exit" },
    { reportSectionKey: "exitStrategy", title: "주요 리스크 및 승인조건" },
  ];
}

function TemplateApplication({ report }: { report: GeneratedReportResponse }) {
  const coverage = report.report?.templateApplication?.sectionCoverage ?? [];
  const appliedTemplate = report.report?.appliedTemplate;
  const unmappedCount = report.metadata?.template_unmapped_section_count ?? 0;
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <h3 className="font-bold text-slate-950 dark:text-white">템플릿 적용 결과</h3>
      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <Kpi label="보고서 버전" value={`v${report.version ?? "-"}`} />
        <Kpi label="섹션 구성" value={report.metadata?.complete ? "전체 섹션 있음" : "구성 확인 필요"} />
        <Kpi label="적용 템플릿" value={appliedTemplate?.version ? `v${appliedTemplate.version}` : "기본"} />
        <Kpi label="미매핑" value={`${unmappedCount}개`} />
      </div>
      {coverage.length ? (
        <div className="mt-4 max-h-64 overflow-auto rounded-lg border border-slate-200 dark:border-slate-800">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="px-3 py-2">섹션</th>
                <th className="px-3 py-2">보고서 Key</th>
                <th className="px-3 py-2">상태</th>
                <th className="px-3 py-2">근거 Agent</th>
              </tr>
            </thead>
            <tbody>
              {coverage.map((row) => (
                <tr key={`${row.templateSectionKey}-${row.reportSectionKey}`} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="px-3 py-2 font-semibold text-slate-900 dark:text-white">{row.title}</td>
                  <td className="px-3 py-2 text-slate-500">{row.reportSectionKey}</td>
                  <td className="px-3 py-2">{row.enabled && row.hasReportSection ? "적용" : row.enabled ? "미매핑" : "제외"}</td>
                  <td className="px-3 py-2 text-slate-500">{row.sourceAgents?.join(", ") || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function recordTypeLabel(type?: string) {
  if (type === "metric") return "지표";
  if (type === "risk_issue") return "리스크";
  if (type === "evidence") return "증거";
  if (type === "assumption") return "가정";
  return type ?? "-";
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-black text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-800">
      <p className="font-semibold text-slate-500">{label}</p>
      <p className="mt-1 font-black text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function MetadataRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-800">
      <dt className="font-semibold text-slate-500">{label}</dt>
      <dd className={`mt-1 break-all font-bold text-slate-900 dark:text-white ${mono ? "font-mono text-[11px]" : ""}`}>
        {value}
      </dd>
    </div>
  );
}

function Notice({ tone, message }: { tone: "good" | "bad"; message: string }) {
  const klass = tone === "good"
    ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200"
    : "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200";
  const Icon = tone === "good" ? CheckCircle2 : AlertCircle;
  return (
    <div className={`flex items-start gap-2 rounded-lg border p-4 text-sm font-semibold ${klass}`}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" />
      {message}
    </div>
  );
}

async function responseError(res: Response) {
  const body = await res.json().catch(() => null) as { detail?: unknown } | null;
  if (typeof body?.detail === "string") return body.detail;
  if (body?.detail && typeof body.detail === "object") {
    const detail = body.detail as { error?: string; reason?: string; action?: string };
    return [detail.error, detail.reason, detail.action].filter(Boolean).join(" · ") || `요청 실패: ${res.status}`;
  }
  return `요청 실패: ${res.status}`;
}

function sortReports(items: SavedReportMetadata[]) {
  return [...items].sort((a, b) => {
    const byDate = String(b.created_at ?? "").localeCompare(String(a.created_at ?? ""));
    return byDate || Number(b.version ?? 0) - Number(a.version ?? 0);
  });
}

function sortRuns(items: OrchestrationSummary[]) {
  return [...items]
    .filter((item) => Boolean(item.run_id))
    .sort((a, b) =>
      String(b.completed_at ?? b.started_at ?? "").localeCompare(
        String(a.completed_at ?? a.started_at ?? ""),
      ),
    );
}

async function fetchSavedReport(projectId: string, reportId: string): Promise<SavedReportDetail> {
  const response = await fetch(`${apiBase}/projects/${projectId}/reports/${reportId}`, {
    cache: "no-store",
    headers: authHeaders("web"),
  });
  if (!response.ok) throw new Error(await responseError(response));
  return (await response.json()) as SavedReportDetail;
}

async function fetchOrchestrationRun(projectId: string, runId: string): Promise<OrchestrationRun> {
  const response = await fetch(`${apiBase}/projects/${projectId}/orchestrations/${runId}`, {
    cache: "no-store",
    headers: authHeaders("web"),
  });
  if (!response.ok) throw new Error(await responseError(response));
  return (await response.json()) as OrchestrationRun;
}

function generatedResponseFromSavedReport(detail: SavedReportDetail): GeneratedReportResponse {
  return {
    report_id: detail.report_id,
    version: detail.version,
    report_markdown: detail.report_markdown,
    report: detail.report,
    paragraphEvidenceLinks: detail.report?.paragraphEvidenceLinks,
    metadata: detail.metadata,
  };
}

function reportPublishBlockReason(
  report: SavedReportMetadata | null,
  run: OrchestrationRun | null,
  approvalStatus: string,
  gate?: ApprovalReadinessGate,
) {
  if (!report) return "발행할 저장 보고서를 선택하세요.";
  if (report.status === "published") return "이미 최종 발행된 불변 보고서입니다.";
  if (report.status !== "draft") return `지원하지 않는 보고서 상태입니다: ${report.status ?? "unknown"}`;
  if (!report.run_id) return "수동 생성 초안은 PF Review에 결합되지 않아 최종 발행할 수 없습니다.";
  if (!run || run.run_id !== report.run_id) return "결합된 PF Review 상세를 검증하지 못했습니다.";
  if (approvalStatus !== "approved") return "결합된 PF Review의 독립 승인 완료가 필요합니다.";
  if (gate?.pass !== true) {
    const blockers = gate?.blockers
      ?.map((item) => item.code || item.message)
      .filter(Boolean)
      .slice(0, 3)
      .join(", ");
    return blockers
      ? `Approval Readiness Gate 차단 항목을 해소하세요: ${blockers}`
      : "Approval Readiness Gate 통과가 필요합니다.";
  }
  return "서버 검증 후 최종 발행할 수 있습니다.";
}

function formatDateTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function shortHash(value?: string) {
  if (!value) return "-";
  return value.length > 30 ? `${value.slice(0, 18)}…${value.slice(-8)}` : value;
}
