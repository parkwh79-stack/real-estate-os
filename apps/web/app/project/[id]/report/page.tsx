"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  FileText,
  Loader2,
  RefreshCw,
  Settings2,
} from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import { EngineMetricsPanel } from "../../../../components/orchestration/engine-metrics-panel";
import { CreditDocumentsPanel } from "../../../../components/orchestration/credit-documents-panel";
import { TermAnnotationsPanel } from "../../../../components/orchestration/term-annotations-panel";

interface ProjectResponse {
  project?: {
    name?: string;
    type?: string;
  };
  [key: string]: unknown;
}

interface ReportTemplate {
  templateId?: string;
  name?: string;
  version?: number;
  status?: string;
  locale?: string;
  sections?: Array<Record<string, unknown>>;
}

interface TemplateListResponse {
  templates?: ReportTemplate[];
}

interface GeneratedReportResponse {
  report_id?: string;
  version?: number;
  report_markdown?: string;
  metadata?: {
    section_count?: number;
    expected_section_count?: number;
    complete?: boolean;
    applied_template_id?: string;
    applied_template_version?: number;
    template_unmapped_section_count?: number;
  };
  report?: {
    appliedTemplate?: {
      templateId?: string;
      name?: string;
      version?: number;
    };
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
      unmappedSections?: Array<Record<string, unknown>>;
      disabledSections?: string[];
    };
  };
}

export default function ReportPreviewPage() {
  const params = useParams<{ id: string }>();
  const projectId = String(params.id);

  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [report, setReport] = useState<GeneratedReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const selectedTemplate = useMemo(() => {
    return templates.find((item) => item.templateId === selectedTemplateId);
  }, [selectedTemplateId, templates]);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [projectRes, templateRes] = await Promise.all([
        fetch(`${apiBase}/projects/${projectId}`, { cache: "no-store", headers: authHeaders("web") }),
        fetch(`${apiBase}/projects/${projectId}/report-templates`, { cache: "no-store", headers: authHeaders("web") }),
      ]);
      if (!projectRes.ok) throw new Error(await responseError(projectRes));
      if (!templateRes.ok) throw new Error(await responseError(templateRes));
      const projectBody = (await projectRes.json()) as ProjectResponse;
      const templateBody = (await templateRes.json()) as TemplateListResponse;
      const loadedTemplates = templateBody.templates ?? [];
      setProject(projectBody);
      setTemplates(loadedTemplates);
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
        headers: {
          "Content-Type": "application/json",
          ...authHeaders("report_editor"),
        },
        body: JSON.stringify({
          templateId: selectedTemplateId || undefined,
          options: { format: "json+markdown" },
        }),
      });
      if (!res.ok) throw new Error(await responseError(res));
      const body = (await res.json()) as GeneratedReportResponse;
      setReport(body);
      setMessage(`보고서가 생성되었습니다. Report ID: ${body.report_id ?? "-"}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "보고서 생성에 실패했습니다.");
    } finally {
      setGenerating(false);
    }
  }

  const coverage = report?.report?.templateApplication?.sectionCoverage ?? [];
  const appliedTemplate = report?.report?.appliedTemplate;
  const unmappedCount = report?.metadata?.template_unmapped_section_count ?? 0;

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
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
              저장된 보고서 템플릿을 선택해 Phase 11 PF 심사보고서를 생성합니다. 템플릿 섹션, 필요 증빙, 근거 Agent 매핑은 생성 결과의 감사 메타데이터에 함께 남습니다.
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
      </div>

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
                <p className="mt-1 text-sm font-black text-slate-950 dark:text-white">
                  {project?.project?.name ?? "사업명 미입력"}
                </p>
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
          <h3 className="font-bold text-slate-950 dark:text-white">템플릿 적용 결과</h3>
          {!report ? (
            <p className="mt-3 text-sm leading-6 text-slate-500">
              아직 생성된 보고서가 없습니다. 템플릿을 선택한 뒤 보고서를 생성하면 적용된 섹션, 미매핑 섹션, 버전 정보가 여기에 표시됩니다.
            </p>
          ) : (
            <div className="mt-4 space-y-4">
              <div className="grid gap-3 md:grid-cols-4">
                <Kpi label="보고서 버전" value={`v${report.version ?? "-"}`} />
                <Kpi label="완성도" value={report.metadata?.complete ? "완료" : "확인 필요"} />
                <Kpi label="적용 템플릿" value={appliedTemplate?.version ? `v${appliedTemplate.version}` : "기본"} />
                <Kpi label="미매핑" value={`${unmappedCount}개`} />
              </div>
              <div className="rounded-lg bg-slate-50 p-4 dark:bg-slate-800/70">
                <p className="text-xs font-bold text-slate-500">템플릿명</p>
                <p className="mt-1 text-sm font-bold text-slate-950 dark:text-white">
                  {appliedTemplate?.name ?? "기본 Phase 11 템플릿"}
                </p>
              </div>
              <div className="max-h-64 overflow-auto rounded-lg border border-slate-200 dark:border-slate-800">
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
                        <td className="px-3 py-2">
                          {row.enabled && row.hasReportSection ? "적용" : row.enabled ? "미매핑" : "제외"}
                        </td>
                        <td className="px-3 py-2 text-slate-500">{row.sourceAgents?.join(", ") || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </section>

      <EngineMetricsPanel projectId={projectId} />

      <CreditDocumentsPanel projectId={projectId} />

      <TermAnnotationsPanel projectId={projectId} />

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="mb-4 flex items-center gap-2">
          <FileText className="h-5 w-5 text-slate-700 dark:text-slate-200" />
          <h3 className="font-bold text-slate-950 dark:text-white">보고서 미리보기</h3>
        </div>
        <div className="min-h-[420px] rounded-lg bg-slate-50 p-4 dark:bg-slate-950">
          {report?.report_markdown ? (
            <pre className="whitespace-pre-wrap font-sans text-sm leading-7 text-slate-800 dark:text-slate-100">
              {report.report_markdown}
            </pre>
          ) : (
            <p className="text-sm leading-6 text-slate-500">보고서를 생성하면 Markdown 미리보기가 표시됩니다.</p>
          )}
        </div>
      </section>
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-black text-slate-950 dark:text-white">{value}</p>
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
