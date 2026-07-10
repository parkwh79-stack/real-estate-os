"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, RefreshCw, Search, XCircle } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";
import { EmptyState, StatusBadge } from "../../components/ui/primitives";

type Candidate = {
  candidateId: string;
  surface: string;
  status: string;
  projectId?: string;
  runId?: string;
  sourceField?: string;
  sourceLocator?: Record<string, unknown>;
  category?: string;
  occurrenceCount?: number;
  lastSeenAt?: string;
};

type CandidateList = {
  count: number;
  candidates: Candidate[];
};

type Draft = {
  termId: string;
  preferredTerm: string;
  canonicalField: string;
  category: string;
  dataType: string;
  reason: string;
};

const DEFAULT_DRAFT: Draft = {
  termId: "",
  preferredTerm: "",
  canonicalField: "",
  category: "unclassified",
  dataType: "text",
  reason: "표준용어 후보 검토 승인",
};

function termIdFromSurface(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9가-힣]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

export default function GlossaryReviewPage() {
  const [status, setStatus] = useState("pending_review");
  const [query, setQuery] = useState("");
  const [data, setData] = useState<CandidateList | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});

  const candidates = useMemo(() => data?.candidates ?? [], [data]);
  const pendingCount = useMemo(
    () => candidates.filter((candidate) => candidate.status === "pending_review").length,
    [candidates],
  );

  async function loadCandidates() {
    setLoading(true);
    setMessage(null);
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (query.trim()) params.set("q", query.trim());
    params.set("limit", "200");
    try {
      const response = await fetch(`${apiBase}/glossary/candidates?${params.toString()}`, {
        headers: authHeaders("glossary-reviewer"),
      });
      if (!response.ok) throw new Error(await response.text());
      const body = (await response.json()) as CandidateList;
      setData(body);
      setDrafts((current) => {
        const next = { ...current };
        for (const candidate of body.candidates) {
          if (!next[candidate.candidateId]) {
            next[candidate.candidateId] = {
              ...DEFAULT_DRAFT,
              termId: termIdFromSurface(candidate.surface),
              preferredTerm: candidate.surface,
              category: candidate.category || "unclassified",
            };
          }
        }
        return next;
      });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "후보 목록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadCandidates();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  function updateDraft(candidateId: string, patch: Partial<Draft>) {
    setDrafts((current) => ({
      ...current,
      [candidateId]: { ...(current[candidateId] ?? DEFAULT_DRAFT), ...patch },
    }));
  }

  async function reviewCandidate(candidate: Candidate, reviewStatus: "approved" | "rejected" | "needs_revision") {
    setMessage(null);
    const draft = drafts[candidate.candidateId] ?? DEFAULT_DRAFT;
    const payload =
      reviewStatus === "approved"
        ? {
            status: "approved",
            termId: draft.termId,
            preferredTerm: draft.preferredTerm || candidate.surface,
            canonicalField: draft.canonicalField,
            category: draft.category,
            dataType: draft.dataType,
            reason: draft.reason,
          }
        : {
            status: reviewStatus,
            reason: draft.reason || (reviewStatus === "rejected" ? "표준용어로 채택하지 않음" : "추가 검토 필요"),
          };
    if (reviewStatus === "approved" && !draft.canonicalField.trim()) {
      setMessage("승인하려면 canonical field를 입력해야 합니다.");
      return;
    }
    const response = await fetch(`${apiBase}/glossary/candidates/${candidate.candidateId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders("glossary-reviewer") },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      setMessage(await response.text());
      return;
    }
    setMessage(reviewStatus === "approved" ? "표준용어로 승인했습니다." : "후보 상태를 업데이트했습니다.");
    await loadCandidates();
  }

  return (
    <main className="mx-auto max-w-7xl px-5 py-8">
      <div className="flex flex-col gap-4 border-b border-slate-200 pb-5 dark:border-slate-800 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-xs font-black uppercase tracking-[0.18em] text-indigo-600">Glossary Review</p>
          <h1 className="mt-2 text-3xl font-black text-slate-950 dark:text-white">표준용어 검토 큐</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">
            Agent가 새로 발견한 부동산·PF 용어를 검토하고, 승인된 후보를 REALYTICS 표준 용어사전과 canonical field에 편입합니다.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge tone={pendingCount > 0 ? "warn" : "good"} label={`검토대기 ${pendingCount}`} />
          <button
            type="button"
            onClick={loadCandidates}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-bold text-slate-700 hover:bg-slate-50 dark:border-slate-800 dark:text-slate-200 dark:hover:bg-slate-900"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            새로고침
          </button>
        </div>
      </div>

      <section className="mt-5 flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-950 md:flex-row">
        <label className="flex flex-1 items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-800">
          <Search className="h-4 w-4 text-slate-400" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void loadCandidates();
            }}
            placeholder="표면어, 출처 필드, canonical field 검색"
            className="w-full bg-transparent text-sm outline-none"
          />
        </label>
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-bold dark:border-slate-800 dark:bg-slate-950"
        >
          <option value="">전체 상태</option>
          <option value="pending_review">검토대기</option>
          <option value="needs_revision">수정필요</option>
          <option value="approved">승인됨</option>
          <option value="rejected">반려됨</option>
        </select>
        <button
          type="button"
          onClick={loadCandidates}
          className="rounded-lg bg-slate-950 px-4 py-2 text-sm font-black text-white dark:bg-white dark:text-slate-950"
        >
          검색
        </button>
      </section>

      {message ? (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-bold text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
          {message}
        </div>
      ) : null}

      <section className="mt-5 space-y-3">
        {candidates.length === 0 ? (
          <EmptyState title="검토할 후보가 없습니다" body="오케스트레이션 또는 용어 태깅 실행 중 unmapped candidate가 발생하면 이곳에 쌓입니다." />
        ) : (
          candidates.map((candidate) => {
            const draft = drafts[candidate.candidateId] ?? DEFAULT_DRAFT;
            return (
              <article key={candidate.candidateId} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-950">
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-lg font-black text-slate-950 dark:text-white">{candidate.surface}</h2>
                      <StatusBadge tone={candidate.status === "pending_review" ? "warn" : candidate.status === "approved" ? "good" : "neutral"} label={candidate.status} />
                      <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-bold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                        {candidate.category || "unclassified"}
                      </span>
                    </div>
                    <p className="mt-2 text-xs text-slate-500">
                      {candidate.sourceField || "source field 없음"} · 발생 {candidate.occurrenceCount ?? 0}회 · {candidate.projectId || "공통"}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => void reviewCandidate(candidate, "rejected")}
                      className="inline-flex items-center gap-1 rounded-lg border border-rose-200 px-3 py-2 text-xs font-black text-rose-700 hover:bg-rose-50 dark:border-rose-900/60 dark:text-rose-300"
                    >
                      <XCircle className="h-4 w-4" />
                      반려
                    </button>
                    <button
                      type="button"
                      onClick={() => void reviewCandidate(candidate, "approved")}
                      className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-black text-white hover:bg-emerald-700"
                    >
                      <CheckCircle2 className="h-4 w-4" />
                      승인
                    </button>
                  </div>
                </div>

                <div className="mt-4 grid gap-3 md:grid-cols-5">
                  <label className="space-y-1">
                    <span className="text-xs font-bold text-slate-500">Term ID</span>
                    <input
                      value={draft.termId}
                      onChange={(event) => updateDraft(candidate.candidateId, { termId: event.target.value })}
                      className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-800 dark:bg-slate-900"
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs font-bold text-slate-500">표준 표시명</span>
                    <input
                      value={draft.preferredTerm}
                      onChange={(event) => updateDraft(candidate.candidateId, { preferredTerm: event.target.value })}
                      className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-800 dark:bg-slate-900"
                    />
                  </label>
                  <label className="space-y-1 md:col-span-2">
                    <span className="text-xs font-bold text-slate-500">Canonical Field</span>
                    <input
                      value={draft.canonicalField}
                      onChange={(event) => updateDraft(candidate.candidateId, { canonicalField: event.target.value })}
                      placeholder="예: legal.conditions.leaseCommitment"
                      className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-800 dark:bg-slate-900"
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs font-bold text-slate-500">분류</span>
                    <input
                      value={draft.category}
                      onChange={(event) => updateDraft(candidate.candidateId, { category: event.target.value })}
                      className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-800 dark:bg-slate-900"
                    />
                  </label>
                </div>

                <details className="mt-3 rounded-lg bg-slate-50 p-3 text-xs dark:bg-slate-900">
                  <summary className="cursor-pointer font-bold text-slate-600 dark:text-slate-300">출처 locator 보기</summary>
                  <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-slate-500">
                    {JSON.stringify(candidate.sourceLocator || {}, null, 2)}
                  </pre>
                </details>
              </article>
            );
          })
        )}
      </section>
    </main>
  );
}
