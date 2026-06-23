"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Copy,
  FileText,
  ListChecks,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  ToggleLeft,
  ToggleRight,
} from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";

interface ReportSection {
  sectionKey: string;
  title: string;
  enabled: boolean;
  order: number;
  sourceAgents: string[];
  templateText: string;
  requiredEvidence?: string[];
}

interface ReportTemplate {
  schemaVersion?: string;
  templateId?: string;
  name: string;
  reportType: string;
  status: string;
  locale: string;
  version?: number;
  sections: ReportSection[];
  style?: Record<string, unknown>;
  variables?: Record<string, unknown>;
  approvalBlock?: Record<string, unknown>;
  editedBy?: string;
  updatedAt?: string;
  createdAt?: string;
  changeMemo?: string;
  history?: Array<Record<string, unknown>>;
}

interface TemplateListResponse {
  templates?: ReportTemplate[];
}

const sectionLabels: Record<string, string> = {
  cover: "표지 및 심의 개요",
  financial_terms: "주요 금융조건",
  credit_protection: "주요 채권보전방안",
  discount_trigger: "할인분양 Trigger",
  sources_and_uses: "자금조달 구조",
  use_of_funds: "자금 사용 용도",
  land_acquisition: "사업부지 확보",
  schedule: "사업추진 일정",
  finance_structure: "금융구조도",
  sales_collection: "분양대금 납입 일정",
  sales_revenue_waterfall: "분양수입금 배분",
  summary_cash_flow: "요약 현금흐름표",
  borrower_analysis: "차주 분석",
  contractor_analysis: "시공사 분석",
  marketability_analysis: "분양성 분석",
  business_feasibility: "사업성 분석",
  exit_strategy_and_risk: "Exit 전략 및 리스크",
  conditions_precedent_subsequent: "인출 선행·후행조건",
  eod: "채무불이행 사유(EOD)",
  fund_execution_and_repayment_order: "자금집행 및 변제충당 순위",
};

const defaultSectionKeys = Object.keys(sectionLabels);

const agentOptions = [
  { id: "ParserAgent", label: "문서 파싱 Agent" },
  { id: "LandAgent", label: "토지 검토 Agent" },
  { id: "LegalReviewAgent", label: "법무·인허가 Agent" },
  { id: "MarketabilityAnalysisAgent", label: "분양성 검토 Agent" },
  { id: "DevelopmentFeasibilityAgent", label: "사업성 검토 Agent" },
  { id: "CashFlowEngine", label: "현금흐름 Engine" },
  { id: "PFStructuringAgent", label: "PF 구조화 Agent" },
  { id: "RiskRegisterAgent", label: "리스크 등록부 Agent" },
  { id: "CreditReviewAgent", label: "여신심사 Agent" },
  { id: "EvidenceGraphAgent", label: "근거 연결 Agent" },
];

function defaultTemplate(): ReportTemplate {
  return {
    name: "Phase 11 PF 심사보고서 표준 템플릿",
    reportType: "phase11_pf_review",
    status: "draft",
    locale: "ko-KR",
    sections: defaultSectionKeys.map((sectionKey, index) => ({
      sectionKey,
      title: sectionLabels[sectionKey] ?? sectionKey,
      enabled: true,
      order: index + 1,
      sourceAgents: suggestedAgents(sectionKey),
      templateText: defaultSectionText(sectionKey),
      requiredEvidence: defaultRequiredEvidence(sectionKey),
    })),
    style: {
      pageSize: "A4",
      tone: "금융기관 투자심사위원회 보고서",
      numberFormat: "KRW 억원 / 백만원 병행",
    },
    variables: {
      projectName: "{{project.name}}",
      reviewDate: "{{review.date}}",
      approvalReadiness: "{{approvalReadinessGate.status}}",
    },
    approvalBlock: {
      reviewers: ["심사역", "팀장", "리스크관리", "본부장"],
      requiredBeforeFinalExport: true,
    },
  };
}

function suggestedAgents(sectionKey: string) {
  const map: Record<string, string[]> = {
    cover: ["ParserAgent", "CreditReviewAgent"],
    financial_terms: ["PFStructuringAgent", "ParserAgent"],
    credit_protection: ["LegalReviewAgent", "PFStructuringAgent", "CreditReviewAgent"],
    discount_trigger: ["MarketabilityAnalysisAgent", "PFStructuringAgent"],
    sources_and_uses: ["DevelopmentFeasibilityAgent", "CashFlowEngine"],
    use_of_funds: ["DevelopmentFeasibilityAgent", "CashFlowEngine"],
    land_acquisition: ["LandAgent", "LegalReviewAgent", "EvidenceGraphAgent"],
    schedule: ["ParserAgent", "LegalReviewAgent", "CashFlowEngine"],
    finance_structure: ["PFStructuringAgent", "CreditReviewAgent"],
    sales_collection: ["MarketabilityAnalysisAgent", "CashFlowEngine"],
    sales_revenue_waterfall: ["CashFlowEngine", "PFStructuringAgent"],
    summary_cash_flow: ["CashFlowEngine"],
    borrower_analysis: ["CreditReviewAgent", "ParserAgent"],
    contractor_analysis: ["CreditReviewAgent", "ParserAgent"],
    marketability_analysis: ["MarketabilityAnalysisAgent"],
    business_feasibility: ["DevelopmentFeasibilityAgent", "CashFlowEngine"],
    exit_strategy_and_risk: ["PFStructuringAgent", "RiskRegisterAgent", "CreditReviewAgent"],
    conditions_precedent_subsequent: ["LegalReviewAgent", "PFStructuringAgent"],
    eod: ["LegalReviewAgent", "PFStructuringAgent"],
    fund_execution_and_repayment_order: ["PFStructuringAgent", "CashFlowEngine", "LegalReviewAgent"],
  };
  return map[sectionKey] ?? [];
}

function defaultRequiredEvidence(sectionKey: string) {
  const map: Record<string, string[]> = {
    land_acquisition: ["토지조서", "PNU", "개별공시지가", "등기부", "제한물권 말소조건"],
    marketability_analysis: ["실거래가", "분양사례", "분양가 산정근거", "흡수율 근거"],
    business_feasibility: ["사업수지 Excel", "상품별 분양수입", "공사비", "금융비용"],
    summary_cash_flow: ["월별 Cash Flow", "Max Minus", "상환계좌 배분"],
    exit_strategy_and_risk: ["Exit 분양률", "Unsold LTV Matrix", "Waterfall 결과"],
    eod: ["대출약정서 초안", "Covenant", "기한이익상실 조항"],
  };
  return map[sectionKey] ?? [];
}

function defaultSectionText(sectionKey: string) {
  const title = sectionLabels[sectionKey] ?? sectionKey;
  return `${title} 섹션은 관련 Agent 산출물과 Evidence Graph의 source locator를 기준으로 작성한다. 근거가 누락된 수치 또는 판단은 확정 의견으로 표시하지 않고, 추가 확인 필요 문구를 포함한다.`;
}

function sortSections(sections: ReportSection[]) {
  return [...sections].sort((a, b) => (a.order || 0) - (b.order || 0));
}

function normalizeTemplate(template: ReportTemplate): ReportTemplate {
  const sections = sortSections(template.sections?.length ? template.sections : defaultTemplate().sections)
    .map((section, index) => ({
      sectionKey: section.sectionKey,
      title: section.title || sectionLabels[section.sectionKey] || section.sectionKey,
      enabled: section.enabled !== false,
      order: index + 1,
      sourceAgents: section.sourceAgents ?? [],
      templateText: section.templateText ?? "",
      requiredEvidence: section.requiredEvidence ?? [],
    }));
  return { ...defaultTemplate(), ...template, sections };
}

function parseJsonObject(label: string, value: string): Record<string, unknown> {
  if (!value.trim()) return {};
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label}은 JSON 객체 형식이어야 합니다.`);
  }
  return parsed as Record<string, unknown>;
}

function jsonText(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2);
}

export default function ReportTemplateEditorPage() {
  const params = useParams<{ id: string }>();
  const projectId = String(params.id);

  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [draft, setDraft] = useState<ReportTemplate>(() => defaultTemplate());
  const [selectedSectionKey, setSelectedSectionKey] = useState("cover");
  const [styleJson, setStyleJson] = useState(jsonText(defaultTemplate().style));
  const [variablesJson, setVariablesJson] = useState(jsonText(defaultTemplate().variables));
  const [approvalJson, setApprovalJson] = useState(jsonText(defaultTemplate().approvalBlock));
  const [reviewer, setReviewer] = useState("report_editor");
  const [changeMemo, setChangeMemo] = useState("템플릿 문구 및 근거 Agent 업데이트");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedSection = useMemo(() => {
    return draft.sections.find((section) => section.sectionKey === selectedSectionKey) ?? draft.sections[0];
  }, [draft.sections, selectedSectionKey]);

  const enabledCount = draft.sections.filter((section) => section.enabled).length;
  const evidenceCount = draft.sections.reduce((sum, section) => sum + (section.requiredEvidence?.length ?? 0), 0);

  const applyDraft = useCallback((template: ReportTemplate) => {
    const normalized = normalizeTemplate(template);
    setDraft(normalized);
    setSelectedSectionKey(normalized.sections[0]?.sectionKey ?? "cover");
    setStyleJson(jsonText(normalized.style));
    setVariablesJson(jsonText(normalized.variables));
    setApprovalJson(jsonText(normalized.approvalBlock));
  }, []);

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/report-templates`, {
        cache: "no-store",
        headers: authHeaders("web"),
      });
      if (!res.ok) throw new Error(await responseError(res));
      const body = (await res.json()) as TemplateListResponse;
      const loaded = (body.templates ?? []).map(normalizeTemplate);
      setTemplates(loaded);
      applyDraft(loaded[0] ?? defaultTemplate());
    } catch (err) {
      setError(err instanceof Error ? err.message : "템플릿 목록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [applyDraft, projectId]);

  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);

  function updateDraft(patch: Partial<ReportTemplate>) {
    setDraft((current) => ({ ...current, ...patch }));
  }

  function updateSelectedSection(patch: Partial<ReportSection>) {
    if (!selectedSection) return;
    setDraft((current) => ({
      ...current,
      sections: current.sections.map((section) => (
        section.sectionKey === selectedSection.sectionKey ? { ...section, ...patch } : section
      )),
    }));
  }

  function toggleAgent(agentId: string) {
    const current = selectedSection?.sourceAgents ?? [];
    const next = current.includes(agentId)
      ? current.filter((item) => item !== agentId)
      : [...current, agentId];
    updateSelectedSection({ sourceAgents: next });
  }

  function moveSection(direction: -1 | 1) {
    if (!selectedSection) return;
    const sections = sortSections(draft.sections);
    const index = sections.findIndex((section) => section.sectionKey === selectedSection.sectionKey);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= sections.length) return;
    const swapped = [...sections];
    const currentSection = swapped[index];
    const targetSection = swapped[target];
    if (!currentSection || !targetSection) return;
    swapped[index] = targetSection;
    swapped[target] = currentSection;
    setDraft((current) => ({
      ...current,
      sections: swapped.map((section, nextIndex) => ({ ...section, order: nextIndex + 1 })),
    }));
  }

  function addCustomSection() {
    const sectionKey = `custom_section_${Date.now()}`;
    const section: ReportSection = {
      sectionKey,
      title: "사용자 정의 섹션",
      enabled: true,
      order: draft.sections.length + 1,
      sourceAgents: ["CreditReviewAgent"],
      templateText: "사용자 정의 섹션 문구를 입력하세요. 확정 수치에는 source locator 또는 evidence ID를 연결해야 합니다.",
      requiredEvidence: [],
    };
    setDraft((current) => ({ ...current, sections: [...current.sections, section] }));
    setSelectedSectionKey(sectionKey);
  }

  function newTemplateFromDefault() {
    applyDraft({
      ...defaultTemplate(),
      name: "새 PF 심사보고서 템플릿",
      changeMemo: "신규 템플릿 생성",
    });
    setChangeMemo("신규 템플릿 생성");
    setMessage("새 템플릿 초안을 만들었습니다. 저장하면 프로젝트에 등록됩니다.");
  }

  async function saveTemplate() {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const payload: ReportTemplate = {
        ...draft,
        style: parseJsonObject("편집 스타일", styleJson),
        variables: parseJsonObject("변수 매핑", variablesJson),
        approvalBlock: parseJsonObject("결재란", approvalJson),
        sections: sortSections(draft.sections),
        editedBy: reviewer.trim() || "report_editor",
        changeMemo,
      };
      const res = await fetch(`${apiBase}/projects/${projectId}/report-templates`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders(reviewer.trim() || "report_editor"),
        },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await responseError(res));
      const body = (await res.json()) as { template?: ReportTemplate };
      const saved = normalizeTemplate(body.template ?? payload);
      setTemplates((current) => {
        const exists = current.some((template) => template.templateId === saved.templateId);
        return exists
          ? current.map((template) => (template.templateId === saved.templateId ? saved : template))
          : [saved, ...current];
      });
      applyDraft(saved);
      setMessage(`저장되었습니다. 현재 버전: v${saved.version ?? 1}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "템플릿 저장에 실패했습니다.");
    } finally {
      setSaving(false);
    }
  }

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
            <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">보고서 템플릿 편집</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">
              Phase 11 PF 심사보고서 20개 섹션의 문구, 근거 Agent, 필요 증빙, 결재란을 프로젝트별로 관리합니다.
              수치와 판단은 Evidence Graph 또는 Agent 산출물에 연결되는 것을 전제로 작성합니다.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={newTemplateFromDefault}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          >
            <Plus className="h-4 w-4" />
            새 템플릿
          </button>
          <button
            type="button"
            onClick={saveTemplate}
            disabled={saving}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-white dark:text-slate-950"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            저장
          </button>
        </div>
      </div>

      {error && <Notice tone="bad" message={error} />}
      {message && <Notice tone="good" message={message} />}

      <section className="grid gap-4 lg:grid-cols-4">
        <Kpi label="저장된 템플릿" value={`${templates.length}건`} />
        <Kpi label="활성 섹션" value={`${enabledCount} / ${draft.sections.length}`} />
        <Kpi label="필요 증빙 항목" value={`${evidenceCount}개`} />
        <Kpi label="현재 버전" value={`v${draft.version ?? 0}`} />
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.9fr_1.35fr_0.9fr]">
        <div className="space-y-4">
          <Panel
            title="템플릿 목록"
            icon={<FileText className="h-5 w-5" />}
            action={(
              <button
                type="button"
                onClick={loadTemplates}
                disabled={loading}
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 px-2.5 text-xs font-bold text-slate-600 hover:bg-slate-50 disabled:opacity-60 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                새로고침
              </button>
            )}
          >
            {loading ? (
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-500">
                <Loader2 className="h-4 w-4 animate-spin" />
                불러오는 중
              </div>
            ) : templates.length ? (
              <div className="space-y-2">
                {templates.map((template) => (
                  <button
                    type="button"
                    key={template.templateId ?? template.name}
                    onClick={() => applyDraft(template)}
                    className={`w-full rounded-lg border p-3 text-left transition-colors ${
                      template.templateId && template.templateId === draft.templateId
                        ? "border-slate-950 bg-slate-50 dark:border-white dark:bg-slate-800"
                        : "border-slate-200 bg-white hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-900 dark:hover:bg-slate-800"
                    }`}
                  >
                    <p className="text-sm font-bold text-slate-950 dark:text-white">{template.name}</p>
                    <p className="mt-1 text-xs text-slate-500">
                      {template.status} · v{template.version ?? 1} · {template.locale}
                    </p>
                  </button>
                ))}
              </div>
            ) : (
              <p className="text-sm leading-6 text-slate-500">저장된 템플릿이 없습니다. 기본 초안을 저장해 시작하세요.</p>
            )}
          </Panel>

          <Panel title="섹션 순서" icon={<ListChecks className="h-5 w-5" />}>
            <div className="space-y-2">
              {sortSections(draft.sections).map((section) => (
                <button
                  type="button"
                  key={section.sectionKey}
                  onClick={() => setSelectedSectionKey(section.sectionKey)}
                  className={`flex w-full items-center justify-between gap-3 rounded-lg border p-3 text-left transition-colors ${
                    section.sectionKey === selectedSectionKey
                      ? "border-indigo-300 bg-indigo-50 dark:border-indigo-800 dark:bg-indigo-950/30"
                      : "border-slate-200 bg-white hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-900 dark:hover:bg-slate-800"
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block text-xs font-black text-slate-400">{section.order}</span>
                    <span className="block truncate text-sm font-bold text-slate-950 dark:text-white">{section.title}</span>
                  </span>
                  <span className={`rounded-md px-2 py-0.5 text-xs font-bold ${
                    section.enabled ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : "bg-slate-100 text-slate-500 dark:bg-slate-800"
                  }`}>
                    {section.enabled ? "사용" : "제외"}
                  </span>
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={addCustomSection}
              className="mt-3 inline-flex h-9 w-full items-center justify-center gap-2 rounded-lg border border-dashed border-slate-300 text-sm font-semibold text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              <Plus className="h-4 w-4" />
              사용자 정의 섹션 추가
            </button>
          </Panel>
        </div>

        <Panel title="섹션 문구 편집" icon={<FileText className="h-5 w-5" />}>
          {selectedSection && (
            <div className="space-y-4">
              <div className="grid gap-3 md:grid-cols-[1fr_0.45fr]">
                <label className="space-y-1">
                  <span className="text-xs font-bold text-slate-500">섹션 제목</span>
                  <input
                    value={selectedSection.title}
                    onChange={(event) => updateSelectedSection({ title: event.target.value })}
                    className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-slate-400 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-bold text-slate-500">섹션 Key</span>
                  <input
                    value={selectedSection.sectionKey}
                    onChange={(event) => updateSelectedSection({ sectionKey: event.target.value })}
                    className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-slate-400 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                  />
                </label>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => updateSelectedSection({ enabled: !selectedSection.enabled })}
                  className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 px-3 text-sm font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
                >
                  {selectedSection.enabled ? <ToggleRight className="h-4 w-4 text-emerald-600" /> : <ToggleLeft className="h-4 w-4 text-slate-400" />}
                  {selectedSection.enabled ? "보고서에 포함" : "보고서에서 제외"}
                </button>
                <button
                  type="button"
                  onClick={() => moveSection(-1)}
                  className="inline-flex h-9 items-center rounded-lg border border-slate-200 px-3 text-sm font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
                >
                  위로
                </button>
                <button
                  type="button"
                  onClick={() => moveSection(1)}
                  className="inline-flex h-9 items-center rounded-lg border border-slate-200 px-3 text-sm font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
                >
                  아래로
                </button>
              </div>

              <label className="space-y-1">
                <span className="text-xs font-bold text-slate-500">보고서 문구 템플릿</span>
                <textarea
                  value={selectedSection.templateText}
                  onChange={(event) => updateSelectedSection({ templateText: event.target.value })}
                  rows={15}
                  className="w-full rounded-lg border border-slate-200 bg-white p-3 text-sm font-semibold leading-6 text-slate-900 outline-none focus:border-slate-400 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                />
              </label>

              <label className="space-y-1">
                <span className="text-xs font-bold text-slate-500">필요 증빙</span>
                <textarea
                  value={(selectedSection.requiredEvidence ?? []).join("\n")}
                  onChange={(event) => updateSelectedSection({
                    requiredEvidence: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean),
                  })}
                  rows={4}
                  className="w-full rounded-lg border border-slate-200 bg-white p-3 text-sm font-semibold leading-6 text-slate-900 outline-none focus:border-slate-400 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                />
              </label>
            </div>
          )}
        </Panel>

        <div className="space-y-4">
          <Panel title="근거 Agent" icon={<Copy className="h-5 w-5" />}>
            <div className="space-y-2">
              {agentOptions.map((agent) => {
                const active = selectedSection?.sourceAgents?.includes(agent.id) ?? false;
                return (
                  <button
                    type="button"
                    key={agent.id}
                    onClick={() => toggleAgent(agent.id)}
                    className={`flex w-full items-center justify-between gap-3 rounded-lg border p-3 text-left text-sm font-semibold transition-colors ${
                      active
                        ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200"
                        : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
                    }`}
                  >
                    {agent.label}
                    {active && <CheckCircle2 className="h-4 w-4" />}
                  </button>
                );
              })}
            </div>
          </Panel>

          <Panel title="템플릿 속성" icon={<FileText className="h-5 w-5" />}>
            <div className="space-y-3">
              <label className="space-y-1">
                <span className="text-xs font-bold text-slate-500">템플릿명</span>
                <input
                  value={draft.name}
                  onChange={(event) => updateDraft({ name: event.target.value })}
                  className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-slate-400 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                />
              </label>
              <div className="grid gap-3 md:grid-cols-2">
                <label className="space-y-1">
                  <span className="text-xs font-bold text-slate-500">상태</span>
                  <select
                    value={draft.status}
                    onChange={(event) => updateDraft({ status: event.target.value })}
                    className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-slate-400 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                  >
                    <option value="draft">초안</option>
                    <option value="active">사용중</option>
                    <option value="archived">보관</option>
                  </select>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-bold text-slate-500">편집자</span>
                  <input
                    value={reviewer}
                    onChange={(event) => setReviewer(event.target.value)}
                    className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-slate-400 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                  />
                </label>
              </div>
              <label className="space-y-1">
                <span className="text-xs font-bold text-slate-500">변경 메모</span>
                <input
                  value={changeMemo}
                  onChange={(event) => setChangeMemo(event.target.value)}
                  className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 outline-none focus:border-slate-400 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                />
              </label>
            </div>
          </Panel>

          <Panel title="변수·스타일·결재란" icon={<ListChecks className="h-5 w-5" />}>
            <JsonTextarea label="변수 매핑 JSON" value={variablesJson} onChange={setVariablesJson} />
            <JsonTextarea label="편집 스타일 JSON" value={styleJson} onChange={setStyleJson} />
            <JsonTextarea label="결재란 JSON" value={approvalJson} onChange={setApprovalJson} />
          </Panel>
        </div>
      </section>
    </div>
  );
}

function Panel({
  title,
  icon,
  action,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="text-slate-700 dark:text-slate-200">{icon}</span>
          <h3 className="font-bold text-slate-950 dark:text-white">{title}</h3>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-sm font-semibold text-slate-500">{label}</p>
      <p className="mt-2 text-2xl font-black text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function JsonTextarea({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="mt-3 block space-y-1 first:mt-0">
      <span className="text-xs font-bold text-slate-500">{label}</span>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={5}
        className="w-full rounded-lg border border-slate-200 bg-white p-3 font-mono text-xs leading-5 text-slate-900 outline-none focus:border-slate-400 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
      />
    </label>
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
