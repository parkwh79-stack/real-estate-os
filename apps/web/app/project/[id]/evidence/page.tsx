"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  FileSearch,
  GitBranch,
  Link2,
  Loader2,
  Search,
} from "lucide-react";
import {
  financeTerm,
  severityLabel,
  translateFinancialTerm,
} from "../../../../lib/financial-terms";
import { authHeaders } from "../../../../lib/auth-client";

const apiBase = "http://127.0.0.1:8000";

interface EvidenceGraphSummary {
  graphId?: string;
  runId?: string;
  nodeCount?: number;
  edgeCount?: number;
  riskNodeCount?: number;
  sourceLocatorCount?: number;
  updatedAt?: string;
}

interface EvidenceGraphList {
  records?: EvidenceGraphSummary[];
}

interface EvidenceNode {
  id?: string;
  type?: string;
  label?: string;
  metadata?: Record<string, unknown>;
}

interface EvidenceEdge {
  from?: string;
  to?: string;
  relationship?: string;
  label?: string;
}

interface EvidenceGraph {
  graphId?: string;
  runId?: string;
  summary?: Record<string, number>;
  nodes?: EvidenceNode[];
  edges?: EvidenceEdge[];
  auditWarnings?: string[];
}

type NodeFilter = "all" | "risk" | "report" | "claim" | "source";

function nodeId(node: EvidenceNode) {
  return node.id ?? "";
}

function nodeTitle(node: EvidenceNode) {
  const meta = node.metadata ?? {};
  return node.label
    ?? asString(meta.title)
    ?? asString(meta.riskId)
    ?? asString(meta.sectionKey)
    ?? node.id
    ?? "-";
}

function asString(value: unknown) {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function nodeMatchesFilter(node: EvidenceNode, filter: NodeFilter) {
  if (filter === "all") return true;
  const type = node.type ?? "";
  if (filter === "risk") return type === "risk";
  if (filter === "report") return type === "report_section";
  if (filter === "claim") return type === "claim" || type === "agent_artifact";
  return type === "source_document" || type === "source_locator" || type === "evidence";
}

function locatorLike(node: EvidenceNode) {
  const meta = node.metadata ?? {};
  return node.type === "source_locator"
    || meta.source !== undefined
    || meta.path !== undefined
    || meta.page !== undefined
    || meta.cell !== undefined
    || meta.apiRowId !== undefined;
}

function compactDate(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
}

async function fetchJson<T>(url: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(url, {
      cache: "no-store",
      headers: authHeaders("web-auditor"),
    });
    if (!res.ok) return fallback;
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

export default function EvidenceGraphAuditorPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [records, setRecords] = useState<EvidenceGraphSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [graph, setGraph] = useState<EvidenceGraph | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [filter, setFilter] = useState<NodeFilter>("risk");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);

  const loadList = useCallback(async () => {
    setLoading(true);
    const body = await fetchJson<EvidenceGraphList>(`${apiBase}/projects/${projectId}/evidence-graphs`, { records: [] });
    const nextRecords = body.records ?? [];
    setRecords(nextRecords);
    const firstRunId = nextRecords[0]?.runId ?? "";
    setSelectedRunId((prev) => prev || firstRunId);
    setLoading(false);
  }, [projectId]);

  const loadGraph = useCallback(async (runId: string) => {
    if (!runId) {
      setGraph(null);
      setSelectedNodeId("");
      return;
    }
    setLoading(true);
    const nextGraph = await fetchJson<EvidenceGraph | null>(
      `${apiBase}/projects/${projectId}/orchestrations/${runId}/evidence-graph`,
      null,
    );
    setGraph(nextGraph);
    const preferred = nextGraph?.nodes?.find((node) => node.type === "risk")
      ?? nextGraph?.nodes?.find((node) => node.type === "report_section")
      ?? nextGraph?.nodes?.[0];
    setSelectedNodeId(preferred?.id ?? "");
    setLoading(false);
  }, [projectId]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    void loadGraph(selectedRunId);
  }, [loadGraph, selectedRunId]);

  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];
  const selectedNode = nodes.find((node) => nodeId(node) === selectedNodeId) ?? null;
  const connectedEdges = useMemo(() => edges.filter((edge) => edge.from === selectedNodeId || edge.to === selectedNodeId), [edges, selectedNodeId]);
  const connectedNodeIds = new Set(connectedEdges.flatMap((edge) => [edge.from, edge.to]).filter(Boolean) as string[]);
  const connectedNodes = nodes.filter((node) => connectedNodeIds.has(nodeId(node)) && nodeId(node) !== selectedNodeId);
  const sourceLocatorNodes = connectedNodes.filter(locatorLike);

  const visibleNodes = nodes.filter((node) => {
    const haystack = `${node.id ?? ""} ${node.type ?? ""} ${nodeTitle(node)} ${JSON.stringify(node.metadata ?? {})}`.toLowerCase();
    return nodeMatchesFilter(node, filter) && (!query.trim() || haystack.includes(query.toLowerCase()));
  });

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-3">
          <Link
            href={`/project/${projectId}`}
            className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
            aria-label="프로젝트 홈으로 돌아가기"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <p className="text-sm font-semibold text-slate-500">프로젝트 #{projectId}</p>
            <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
              {financeTerm("evidenceGraph")}
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              보고서 문구, 계산값, 리스크가 어떤 문서·엑셀 셀·API row·에이전트 결과에서 왔는지 감사 관점으로 확인합니다.
            </p>
          </div>
        </div>
        <Link
          href={`/project/${projectId}/orchestration`}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 dark:bg-white dark:text-slate-950"
        >
          <GitBranch className="h-4 w-4" />
          AI 통합심사로 이동
        </Link>
      </div>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi label="노드" value={String(graph?.summary?.nodeCount ?? graph?.nodes?.length ?? 0)} />
        <Kpi label="연결선" value={String(graph?.summary?.edgeCount ?? graph?.edges?.length ?? 0)} />
        <Kpi label="리스크 노드" value={String(graph?.summary?.riskCount ?? graph?.summary?.riskNodeCount ?? 0)} />
        <Kpi label="근거 위치" value={String(graph?.summary?.sourceLocatorCount ?? 0)} />
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-2">
            <FileSearch className="h-5 w-5 text-amber-600" />
            <h3 className="font-bold text-slate-950 dark:text-white">감사 대상 그래프 선택</h3>
          </div>
          <select
            value={selectedRunId}
            onChange={(event) => setSelectedRunId(event.target.value)}
            className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
          >
            {records.length === 0 ? <option value="">저장된 근거 연결도 없음</option> : null}
            {records.map((record) => (
              <option key={record.runId} value={record.runId}>
                {record.runId} · {compactDate(record.updatedAt)} · {record.nodeCount ?? 0} nodes
              </option>
            ))}
          </select>
        </div>
      </section>

      {loading ? (
        <div className="flex min-h-[280px] items-center justify-center rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
        </div>
      ) : !graph ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-5 text-sm font-semibold leading-6 text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200">
          아직 저장된 근거 연결도가 없습니다. AI 통합심사를 먼저 실행하면 Evidence Graph가 생성되고 서버에 저장됩니다.
        </div>
      ) : (
        <section className="grid gap-4 xl:grid-cols-[0.85fr_1fr_0.9fr]">
          <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <div className="flex items-center justify-between gap-2">
              <h3 className="font-bold text-slate-950 dark:text-white">리스크·문구·계산값</h3>
              <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-500 dark:bg-slate-800">
                {visibleNodes.length}
              </span>
            </div>
            <div className="mt-3 flex gap-2 overflow-x-auto">
              {([
                ["risk", "리스크"],
                ["report", "보고서"],
                ["claim", "계산·판단"],
                ["source", "근거"],
                ["all", "전체"],
              ] as Array<[NodeFilter, string]>).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setFilter(value)}
                  className={`h-8 shrink-0 rounded-lg px-3 text-xs font-bold ${
                    filter === value
                      ? "bg-slate-950 text-white dark:bg-white dark:text-slate-950"
                      : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <label className="mt-3 flex h-10 items-center gap-2 rounded-lg border border-slate-200 px-3 dark:border-slate-700">
              <Search className="h-4 w-4 text-slate-400" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="리스크 코드, 보고서 섹션, 근거 검색"
                className="w-full bg-transparent text-sm font-semibold outline-none placeholder:text-slate-400"
              />
            </label>
            <div className="mt-3 max-h-[620px] space-y-2 overflow-y-auto pr-1">
              {visibleNodes.map((node) => (
                <button
                  key={node.id}
                  type="button"
                  onClick={() => setSelectedNodeId(nodeId(node))}
                  className={`w-full rounded-lg border p-3 text-left transition-colors ${
                    selectedNodeId === nodeId(node)
                      ? "border-slate-950 bg-slate-50 dark:border-white dark:bg-slate-800"
                      : "border-slate-200 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="truncate text-sm font-bold text-slate-950 dark:text-white">{nodeTitle(node)}</p>
                    <span className="shrink-0 rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-bold text-slate-500 dark:bg-slate-800">
                      {translateFinancialTerm(node.type)}
                    </span>
                  </div>
                  <p className="mt-1 truncate text-xs text-slate-500">{node.id}</p>
                </button>
              ))}
            </div>
          </div>

          <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <h3 className="font-bold text-slate-950 dark:text-white">선택 항목 상세</h3>
            {selectedNode ? (
              <div className="mt-4 space-y-4">
                <div className="rounded-lg bg-slate-50 p-4 dark:bg-slate-800/70">
                  <p className="text-xs font-bold uppercase text-slate-500">{translateFinancialTerm(selectedNode.type)}</p>
                  <p className="mt-2 text-lg font-black text-slate-950 dark:text-white">{nodeTitle(selectedNode)}</p>
                  <p className="mt-1 break-all text-xs text-slate-500">{selectedNode.id}</p>
                </div>
                <MetadataTable metadata={selectedNode.metadata ?? {}} />
                <div>
                  <div className="mb-2 flex items-center gap-2">
                    <Link2 className="h-4 w-4 text-slate-500" />
                    <p className="text-sm font-bold text-slate-950 dark:text-white">연결된 노드</p>
                  </div>
                  <div className="space-y-2">
                    {connectedEdges.length === 0 ? (
                      <p className="rounded-lg bg-slate-50 p-3 text-sm font-semibold text-slate-500 dark:bg-slate-800/70">연결된 노드가 없습니다.</p>
                    ) : connectedEdges.map((edge, index) => {
                      const targetId = edge.from === selectedNodeId ? edge.to : edge.from;
                      const targetNode = nodes.find((node) => nodeId(node) === targetId);
                      return (
                        <button
                          key={`${edge.from}-${edge.to}-${index}`}
                          type="button"
                          onClick={() => targetId && setSelectedNodeId(targetId)}
                          className="w-full rounded-lg border border-slate-200 p-3 text-left hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800"
                        >
                          <p className="text-xs font-bold text-slate-500">{edge.relationship ?? "linked"}</p>
                          <p className="mt-1 text-sm font-bold text-slate-950 dark:text-white">{targetNode ? nodeTitle(targetNode) : targetId}</p>
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
            ) : (
              <p className="mt-4 rounded-lg bg-slate-50 p-4 text-sm font-semibold text-slate-500 dark:bg-slate-800/70">왼쪽에서 항목을 선택하세요.</p>
            )}
          </div>

          <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <h3 className="font-bold text-slate-950 dark:text-white">원천 근거 위치</h3>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              문서 페이지, 엑셀 셀, API row, 증빙 ID처럼 감사자가 재확인할 수 있는 위치 정보입니다.
            </p>
            <div className="mt-4 space-y-3">
              {sourceLocatorNodes.length === 0 ? (
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm font-semibold leading-6 text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200">
                  이 항목에는 직접 연결된 source locator가 없습니다. Risk Register의 evidence/source locator 보강이 필요합니다.
                </div>
              ) : sourceLocatorNodes.map((node) => (
                <div key={node.id} className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
                  <p className="text-sm font-bold text-slate-950 dark:text-white">{nodeTitle(node)}</p>
                  <MetadataTable metadata={node.metadata ?? {}} compact />
                </div>
              ))}
            </div>
            {graph.auditWarnings?.length ? (
              <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs font-semibold leading-5 text-rose-800 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-200">
                {graph.auditWarnings.map((warning) => (
                  <p key={warning}>- {warning}</p>
                ))}
              </div>
            ) : null}
          </div>
        </section>
      )}
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-sm font-semibold text-slate-500">{label}</p>
      <p className="mt-3 text-2xl font-black tracking-tight text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function MetadataTable({ metadata, compact = false }: { metadata: Record<string, unknown>; compact?: boolean }) {
  const entries = Object.entries(metadata).filter(([, value]) => value !== undefined && value !== null && value !== "");
  if (entries.length === 0) {
    return <p className="rounded-lg bg-slate-50 p-3 text-sm font-semibold text-slate-500 dark:bg-slate-800/70">표시할 메타데이터가 없습니다.</p>;
  }
  return (
    <div className={compact ? "mt-2 space-y-1" : "space-y-2"}>
      {entries.map(([key, value]) => (
        <div key={key} className="grid grid-cols-[110px_1fr] gap-2 rounded-lg bg-slate-50 p-2 text-xs dark:bg-slate-800/70">
          <p className="font-bold text-slate-500">{metadataLabel(key)}</p>
          <p className="break-words font-semibold text-slate-800 dark:text-slate-200">{metadataValue(key, value)}</p>
        </div>
      ))}
    </div>
  );
}

function metadataLabel(key: string) {
  const labels: Record<string, string> = {
    riskId: "리스크 ID",
    code: "코드",
    category: "구분",
    severity: "중요도",
    sourceAgent: "담당 에이전트",
    sourceArtifact: "산출물",
    reviewStatus: "검토상태",
    sectionKey: "보고서 섹션",
    artifactKey: "산출물",
    path: "경로",
    value: "값",
    source: "출처",
    page: "페이지",
    cell: "엑셀 셀",
    apiRowId: "API row",
    label: "라벨",
    confidence: "신뢰도",
  };
  return labels[key] ?? translateFinancialTerm(key);
}

function metadataValue(key: string, value: unknown) {
  if (key === "severity") return severityLabel(String(value));
  if (typeof value === "string") return translateFinancialTerm(value);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}
