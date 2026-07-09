"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { AlertTriangle, ArrowLeft, CheckCircle2, Database, FileSearch, GitBranch, Loader2, Search, XCircle } from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import {
  fetchDealStateRecordGroups,
  formatRecordValue,
  hasSourceLocator,
  locatorSummary,
  type DealStateRecord,
  type DealStateRecordType,
} from "../../../../lib/deal-state-records";
import { displayText, fetchWorkspaceContext, type WorkspacePageContext } from "../../../../lib/workspace-context";

type EvidenceGraphSummary = {
  graphId?: string;
  runId?: string;
  nodeCount?: number;
  edgeCount?: number;
  riskNodeCount?: number;
  sourceLocatorCount?: number;
  updatedAt?: string;
};

type EvidenceGraph = {
  graphId?: string;
  runId?: string;
  nodes?: Array<{ id?: string; type?: string; label?: string; metadata?: Record<string, unknown> }>;
  edges?: Array<{ from?: string; to?: string; relationship?: string }>;
  auditWarnings?: string[];
};

type Filter = "all" | DealStateRecordType | "weak_locator" | "review_needed";
type ReviewStatus = "approved" | "waived" | "needs_revision" | "resolved";
type SourceLocatorAuditResult = {
  auditId?: string;
  missingLocatorCount?: number;
  weakTraceCount?: number;
  weakParagraphCount?: number;
};

async function fetchJson<T>(url: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(url, { cache: "no-store", headers: authHeaders("web-auditor") });
    if (!res.ok) return fallback;
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

function compactDate(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
}

function needsReview(record: DealStateRecord) {
  const reviewStatus = String(record.reviewStatus ?? "").toLowerCase();
  const approvalStatus = String(record.approvalStatus ?? "").toLowerCase();
  return ["needs_revision", "needs_review", "candidate_needs_review", "unreviewed"].includes(reviewStatus)
    || ["candidate_needs_review", "needs_review"].includes(approvalStatus)
    || record.reviewRequired === true;
}

function isAutoSupplement(record: DealStateRecord) {
  return String(record.sourceType ?? "").toLowerCase() === "auto_evidence_supplement"
    || String(record.factOrigin ?? "").toLowerCase() === "auto_supplement"
    || String(record.recordType ?? "") === "auto_supplement_attempt";
}

export default function EvidenceGraphAuditorPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [records, setRecords] = useState<DealStateRecord[]>([]);
  const [graphs, setGraphs] = useState<EvidenceGraphSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [graph, setGraph] = useState<EvidenceGraph | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [auditRunning, setAuditRunning] = useState(false);
  const [reviewingId, setReviewingId] = useState<string | null>(null);
  const [auditResult, setAuditResult] = useState<SourceLocatorAuditResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ctx, groups, graphList] = await Promise.all([
        fetchWorkspaceContext(projectId, "evidence").catch(() => null),
        fetchDealStateRecordGroups(projectId, 900).catch(() => ({
          metrics: [],
          risks: [],
          evidence: [],
          assumptions: [],
          parsedFacts: [],
          parsedFactValidations: [],
          autoSupplementAttempts: [],
        })),
        fetchJson<{ records?: EvidenceGraphSummary[] }>(`${apiBase}/projects/${projectId}/evidence-graphs`, { records: [] }),
      ]);
      setContext(ctx);
      setRecords([
        ...groups.evidence,
        ...groups.assumptions,
        ...groups.metrics,
        ...groups.risks,
        ...groups.parsedFacts,
        ...groups.parsedFactValidations,
        ...groups.autoSupplementAttempts,
      ]);
      const nextGraphs = graphList.records ?? [];
      setGraphs(nextGraphs);
      setSelectedRunId((current) => current || nextGraphs[0]?.runId || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "근거 원장을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  const loadGraph = useCallback(async (runId: string) => {
    if (!runId) {
      setGraph(null);
      return;
    }
    const nextGraph = await fetchJson<EvidenceGraph | null>(`${apiBase}/projects/${projectId}/orchestrations/${runId}/evidence-graph`, null);
    setGraph(nextGraph);
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void loadGraph(selectedRunId);
  }, [loadGraph, selectedRunId]);

  async function runSourceLocatorAudit() {
    setAuditRunning(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/deal-state-records/source-locator-audit`, {
        method: "POST",
        headers: authHeaders("source-locator-auditor"),
      });
      if (!res.ok) throw new Error(`Source Locator audit failed (${res.status})`);
      setAuditResult((await res.json()) as SourceLocatorAuditResult);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "근거 위치 감사 실행에 실패했습니다.");
    } finally {
      setAuditRunning(false);
    }
  }

  async function reviewRecord(record: DealStateRecord, reviewStatus: ReviewStatus) {
    if (!record.recordId) return;
    setReviewingId(record.recordId);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/deal-state-records/${encodeURIComponent(record.recordId)}/review`, {
        method: "PATCH",
        headers: { ...authHeaders("evidence-reviewer"), "Content-Type": "application/json" },
        body: JSON.stringify({
          reviewStatus,
          reviewedBy: "evidence-reviewer",
          comment: `Deal State record marked ${reviewStatus} from Evidence/Assumption screen.`,
          waiverReason: reviewStatus === "waived" ? "Reviewer waiver from Evidence/Assumption screen." : undefined,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null) as { detail?: string } | null;
        throw new Error(body?.detail ?? `Review update failed (${res.status})`);
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "검토 상태 저장에 실패했습니다.");
    } finally {
      setReviewingId(null);
    }
  }

  const counts = useMemo(() => ({
    evidence: records.filter((record) => record.recordType === "evidence").length,
    metrics: records.filter((record) => record.recordType === "metric").length,
    risks: records.filter((record) => record.recordType === "risk_issue").length,
    assumptions: records.filter((record) => record.recordType === "assumption").length,
    parsedFacts: records.filter((record) => record.recordType === "parsed_fact").length,
    autoSupplement: records.filter((record) => isAutoSupplement(record)).length,
    reviewNeeded: records.filter((record) => needsReview(record)).length,
    weakLocators: records.filter((record) => !hasSourceLocator(record)).length,
  }), [records]);

  const visibleRecords = records.filter((record) => {
    const haystack = `${record.recordType ?? ""} ${record.key ?? ""} ${record.label ?? ""} ${record.sourceType ?? ""} ${record.recordId ?? ""} ${JSON.stringify(record.sourceLocator ?? {})}`.toLowerCase();
    const matchesFilter = filter === "all"
      || (filter === "weak_locator" ? !hasSourceLocator(record)
        : filter === "review_needed" ? needsReview(record)
          : record.recordType === filter);
    return matchesFilter && (!query.trim() || haystack.includes(query.toLowerCase()));
  });

  const projectName = displayText(context?.projectMemory?.projectName, `프로젝트 ${projectId}`);

  return (
    <div className="space-y-6 pb-8">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-3">
          <Link href={`/project/${projectId}`} className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" aria-label="프로젝트로 돌아가기">
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <p className="text-sm font-semibold text-slate-500">{projectName}</p>
            <h1 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">근거 원장 / Evidence & Assumption Review</h1>
            <p className="mt-1 text-sm text-slate-500">
              문서, 엑셀 셀, API row, Agent artifact, 계산 run, AI 자동 보강 후보를 하나의 Deal State 원장에서 검토합니다.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void runSourceLocatorAudit()}
            disabled={auditRunning}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 text-sm font-semibold text-amber-800 hover:bg-amber-100 disabled:opacity-60 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"
          >
            {auditRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <AlertTriangle className="h-4 w-4" />}
            근거 위치 감사
          </button>
          <Link href={`/project/${projectId}/chief-orchestration`} className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950">
            <GitBranch className="h-4 w-4" />
            AI 딜룸으로 이동
          </Link>
        </div>
      </header>

      {error ? <Notice>{error}</Notice> : null}

      {auditResult ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm font-semibold text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          Source Locator 감사 완료: 누락 {auditResult.missingLocatorCount ?? 0}건, 약한 추적 {auditResult.weakTraceCount ?? 0}건,
          문단 근거 보강 필요 {auditResult.weakParagraphCount ?? 0}건을 확인했습니다.
          결과는 Risk 원장에 SOURCE_LOCATOR_MISSING / EVIDENCE_TRACE_WEAK로 저장됩니다.
        </div>
      ) : null}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-6">
        <Kpi label="증빙" value={`${counts.evidence.toLocaleString("ko-KR")}건`} />
        <Kpi label="가정 후보" value={`${counts.assumptions.toLocaleString("ko-KR")}건`} />
        <Kpi label="파싱값" value={`${counts.parsedFacts.toLocaleString("ko-KR")}건`} />
        <Kpi label="AI 자동 보강" value={`${counts.autoSupplement.toLocaleString("ko-KR")}건`} tone={counts.autoSupplement ? "warn" : undefined} />
        <Kpi label="검토 필요" value={`${counts.reviewNeeded.toLocaleString("ko-KR")}건`} tone={counts.reviewNeeded ? "warn" : "good"} />
        <Kpi label="근거 위치 부족" value={`${counts.weakLocators.toLocaleString("ko-KR")}건`} tone={counts.weakLocators ? "warn" : "good"} />
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.25fr_0.75fr]">
        <Panel title="Deal State 원장" icon={<Database className="h-5 w-5" />}>
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="flex gap-2 overflow-x-auto">
              {([
                ["all", "전체"],
                ["evidence", "증빙"],
                ["assumption", "가정"],
                ["parsed_fact", "파싱값"],
                ["metric", "계산지표"],
                ["risk_issue", "리스크"],
                ["auto_supplement_attempt", "AI 보강시도"],
                ["review_needed", "검토 필요"],
                ["weak_locator", "위치 부족"],
              ] as Array<[Filter, string]>).map(([value, label]) => (
                <button key={value} type="button" onClick={() => setFilter(value)} className={`h-9 shrink-0 rounded-lg px-3 text-xs font-bold ${filter === value ? "bg-slate-950 text-white dark:bg-white dark:text-slate-950" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"}`}>
                  {label}
                </button>
              ))}
            </div>
            <label className="flex h-10 min-w-64 items-center gap-2 rounded-lg border border-slate-200 px-3 dark:border-slate-700">
              <Search className="h-4 w-4 text-slate-400" />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="key, source, locator 검색" className="w-full bg-transparent text-sm font-semibold outline-none placeholder:text-slate-400" />
            </label>
          </div>

          <div className="mt-4 max-h-[680px] overflow-auto rounded-lg border border-slate-200 dark:border-slate-800">
            {loading ? (
              <Loading text="Deal State 원장을 불러오는 중입니다." />
            ) : visibleRecords.length ? (
              <table className="w-full min-w-[1120px] text-left text-xs">
                <thead className="bg-slate-50 text-slate-500 dark:bg-slate-800">
                  <tr>
                    <th className="px-3 py-2">유형</th>
                    <th className="px-3 py-2">Key</th>
                    <th className="px-3 py-2">값</th>
                    <th className="px-3 py-2">검토</th>
                    <th className="px-3 py-2">출처</th>
                    <th className="px-3 py-2">근거 위치</th>
                    <th className="px-3 py-2">Run</th>
                    <th className="px-3 py-2">상태 처리</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRecords.map((record) => (
                    <tr key={record.recordId ?? `${record.recordType}-${record.key}`} className="border-t border-slate-100 align-top dark:border-slate-800">
                      <td className="px-3 py-3 font-bold text-slate-700 dark:text-slate-200">
                        <div className="flex flex-col gap-1">
                          <span>{typeLabel(record.recordType)}</span>
                          {isAutoSupplement(record) ? <Badge>AI 보강</Badge> : null}
                        </div>
                      </td>
                      <td className="px-3 py-3">
                        <p className="font-black text-slate-950 dark:text-white">{displayText(record.key ?? record.label, "-")}</p>
                        <p className="mt-1 break-all text-[11px] text-slate-400">{record.recordId}</p>
                      </td>
                      <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{formatRecordValue(record)}</td>
                      <td className={`px-3 py-3 font-bold ${needsReview(record) ? "text-amber-700 dark:text-amber-300" : "text-emerald-700 dark:text-emerald-300"}`}>
                        {displayText(record.reviewStatus ?? record.approvalStatus ?? record.validationStatus, needsReview(record) ? "review needed" : "ok")}
                      </td>
                      <td className="px-3 py-3 text-slate-500">{displayText(record.sourceType, "-")}</td>
                      <td className={`px-3 py-3 font-semibold ${hasSourceLocator(record) ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"}`}>{locatorSummary(record)}</td>
                      <td className="px-3 py-3 text-slate-500">{displayText(record.calculationRunId ?? record.evidenceGraphRunId ?? record.riskRegisterRunId, "-")}</td>
                      <td className="px-3 py-3">
                        <div className="flex flex-wrap gap-1">
                          <ActionButton label="승인" disabled={!record.recordId || reviewingId === record.recordId} onClick={() => void reviewRecord(record, "approved")} />
                          <ActionButton label="Resolved" disabled={!record.recordId || reviewingId === record.recordId} onClick={() => void reviewRecord(record, "resolved")} />
                          <ActionButton label="Waive" disabled={!record.recordId || reviewingId === record.recordId} onClick={() => void reviewRecord(record, "waived")} />
                          <ActionButton label="보완요청" danger disabled={!record.recordId || reviewingId === record.recordId} onClick={() => void reviewRecord(record, "needs_revision")} />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty text="조건에 맞는 Deal State 레코드가 없습니다. 파일 업로드, Pod 실행, 계산 실행을 하면 자동으로 적재됩니다." />
            )}
          </div>
        </Panel>

        <Panel title="Evidence Graph 원본" icon={<FileSearch className="h-5 w-5" />}>
          <select value={selectedRunId} onChange={(event) => setSelectedRunId(event.target.value)} className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200">
            {graphs.length === 0 ? <option value="">저장된 Evidence Graph 없음</option> : null}
            {graphs.map((item) => (
              <option key={item.runId} value={item.runId}>
                {item.runId} · {compactDate(item.updatedAt)} · {item.nodeCount ?? 0} nodes
              </option>
            ))}
          </select>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <Kpi label="Graph nodes" value={`${graph?.nodes?.length ?? 0}개`} />
            <Kpi label="Graph edges" value={`${graph?.edges?.length ?? 0}개`} />
          </div>
          <div className="mt-4 max-h-[420px] space-y-2 overflow-auto pr-1">
            {graph?.nodes?.slice(0, 80).map((node) => (
              <div key={node.id} className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
                <p className="text-xs font-bold text-slate-500">{displayText(node.type, "node")}</p>
                <p className="mt-1 text-sm font-black text-slate-950 dark:text-white">{displayText(node.label ?? node.id, "-")}</p>
                <p className="mt-1 break-all text-xs text-slate-500">{node.id}</p>
              </div>
            )) ?? <Empty text="선택된 Evidence Graph가 없습니다." />}
          </div>
          {graph?.auditWarnings?.length ? (
            <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs font-semibold leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              {graph.auditWarnings.map((warning) => <p key={warning}>- {warning}</p>)}
            </div>
          ) : null}
        </Panel>
      </section>
    </div>
  );
}

function typeLabel(type?: string) {
  if (type === "metric") return "계산지표";
  if (type === "risk_issue") return "리스크";
  if (type === "assumption") return "가정";
  if (type === "evidence") return "증빙";
  if (type === "parsed_fact") return "파싱값";
  if (type === "parsed_fact_validation") return "Pod 검증";
  if (type === "auto_supplement_attempt") return "AI 보강시도";
  return displayText(type, "-");
}

function ActionButton({ label, disabled, danger, onClick }: { label: string; disabled?: boolean; danger?: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex h-8 items-center gap-1 rounded-md px-2 text-[11px] font-bold disabled:opacity-50 ${danger ? "bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-200" : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200"}`}
    >
      {danger ? <XCircle className="h-3 w-3" /> : <CheckCircle2 className="h-3 w-3" />}
      {label}
    </button>
  );
}

function Badge({ children }: { children: ReactNode }) {
  return <span className="w-fit rounded-full bg-cyan-50 px-2 py-0.5 text-[10px] font-black text-cyan-700 dark:bg-cyan-950/30 dark:text-cyan-200">{children}</span>;
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: "good" | "warn" }) {
  const color = tone === "good" ? "text-emerald-600" : tone === "warn" ? "text-amber-600" : "text-slate-950 dark:text-white";
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-sm font-semibold text-slate-500">{label}</p>
      <p className={`mt-2 text-2xl font-black tracking-tight ${color}`}>{value}</p>
    </div>
  );
}

function Panel({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-4 flex items-center gap-2 text-slate-900 dark:text-white">
        {icon}
        <h2 className="text-lg font-black">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function Loading({ text }: { text: string }) {
  return <div className="flex min-h-[220px] items-center justify-center text-sm font-bold text-slate-500"><Loader2 className="mr-2 h-5 w-5 animate-spin" />{text}</div>;
}

function Notice({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">{children}</div>;
}

function Empty({ text }: { text: string }) {
  return <div className="rounded-lg bg-slate-50 p-5 text-sm font-semibold text-slate-500 dark:bg-slate-800/70">{text}</div>;
}
