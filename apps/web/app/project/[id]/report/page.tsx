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
  RefreshCw,
  Settings2,
} from "lucide-react";
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
}

interface ParagraphEvidenceLinks {
  schemaVersion?: string;
  linkMethod?: string;
  recordCount?: number;
  sectionCount?: number;
  linkedRecordCount?: number;
  sourceLocatorReadySectionCount?: number;
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
  const [dealState, setDealState] = useState<DealStateGroups>(EMPTY_GROUPS);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
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
  const paragraphLinks = report?.report?.paragraphEvidenceLinks ?? report?.paragraphEvidenceLinks;

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [projectRes, templateRes, recordGroups] = await Promise.all([
        fetch(`${apiBase}/projects/${projectId}`, { cache: "no-store", headers: authHeaders("web") }),
        fetch(`${apiBase}/projects/${projectId}/report-templates`, { cache: "no-store", headers: authHeaders("web") }),
        fetchDealStateRecordGroups(projectId, 700),
      ]);
      if (!projectRes.ok) throw new Error(await responseError(projectRes));
      if (!templateRes.ok) throw new Error(await responseError(templateRes));
      const templateBody = (await templateRes.json()) as { templates?: ReportTemplate[] };
      const loadedTemplates = templateBody.templates ?? [];
      setProject((await projectRes.json()) as ProjectResponse);
      setTemplates(loadedTemplates);
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
      setMessage(`보고서가 생성되었습니다. Report ID: ${body.report_id ?? "-"}`);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "보고서 생성에 실패했습니다.");
    } finally {
      setGenerating(false);
    }
  }

  const coverage = report?.report?.templateApplication?.sectionCoverage ?? [];
  const sectionRows: ReportSectionRow[] = coverage.length ? coverage : defaultSectionRows();
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
            <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">PF 심사보고서 생성</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">
              Report Studio는 Deal State 원장에 저장된 증거, 지표, 가정, 리스크를 기준으로 보고서를 생성하고,
              생성 시점의 문단별 근거 링크를 `paragraphEvidenceLinks`로 저장합니다.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={loadData}
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

      <section className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
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
                <Kpi label="섹션 수" value={`${selectedTemplate?.sections?.length ?? 20}개`} />
              </div>

              <button
                type="button"
                onClick={generateReport}
                disabled={generating}
                className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-white dark:text-slate-950"
              >
                {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                보고서 생성
              </button>
            </div>
          )}
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-4 flex items-center gap-2">
            <Database className="h-5 w-5 text-slate-700 dark:text-slate-200" />
            <h3 className="font-bold text-slate-950 dark:text-white">보고서 근거 원장</h3>
          </div>
          <div className="grid gap-3 md:grid-cols-5">
            <Kpi label="지표" value={`${dealState.metrics.length}건`} />
            <Kpi label="리스크" value={`${dealState.risks.length}건`} />
            <Kpi label="증거" value={`${dealState.evidence.length}건`} />
            <Kpi label="가정" value={`${dealState.assumptions.length}건`} />
            <Kpi label="근거 위치 부족" value={`${sourceLocatorMissing}건`} />
          </div>
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
                {allRecords.slice(0, 80).map((record) => (
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
              생성 시점에 저장된 `paragraphEvidenceLinks`를 기준으로 문단별 증거, 지표, 가정, 리스크를 추적합니다.
            </p>
          </div>
        </div>
        <div className="grid gap-2 text-xs md:grid-cols-3">
          <MiniStat label="링크 방식" value={links?.linkMethod ?? (hasGeneratedLinks ? "stored" : "보고서 생성 전")} />
          <MiniStat label="연결 레코드" value={`${links?.linkedRecordCount ?? 0}건`} />
          <MiniStat label="근거 위치 섹션" value={`${links?.sourceLocatorReadySectionCount ?? 0}개`} />
        </div>
      </div>

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
        <Kpi label="완성도" value={report.metadata?.complete ? "완료" : "확인 필요"} />
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
  return `요청 실패: ${res.status}`;
}
