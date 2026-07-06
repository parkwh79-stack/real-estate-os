"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, BookMarked, Check, GitMerge, Loader2, RefreshCw, Type, X } from "lucide-react";
import { apiBase, authHeaders } from "../../../lib/auth-client";
import { EmptyState, StatusBadge } from "../../../components/ui/primitives";

interface NearestTerm {
  termId?: string | null;
  preferredTerm?: string | null;
  displayNameKo?: string | null;
  canonicalField?: string | null;
  similarityScore?: number | null;
}

interface Candidate {
  candidateId?: string;
  candidate_id?: string;
  surface: string;
  category?: string;
  sourceField?: string;
  suggestedCanonicalField?: string | null;
  suggestedTermId?: string | null;
  nearestTerm?: NearestTerm | null;
  status?: string;
  projectId?: string | null;
  confidence?: number | null;
}

type ReviewStatus = "approved" | "merged" | "free_text" | "rejected";

const STATUS_TONE: Record<string, "good" | "warn" | "bad" | "neutral"> = {
  approved: "good",
  pending_review: "warn",
  needs_revision: "warn",
  rejected: "bad",
  merged: "good",
  free_text: "neutral",
};

const STATUS_LABEL: Record<string, string> = {
  approved: "새 표준어",
  pending_review: "검토 대기",
  needs_revision: "보완 필요",
  rejected: "반려",
  merged: "병합됨",
  free_text: "자유문구",
};

function candId(c: Candidate): string {
  return String(c.candidateId ?? c.candidate_id ?? "");
}

/** AI가 판단한 후보 유형: same-as(유사 용어 있음) / new-term(제안 필드 있음) / free-text */
function aiSuggestion(c: Candidate): { kind: "same_as" | "new_term" | "free_text"; label: string } {
  const score = c.nearestTerm?.similarityScore ?? 0;
  if (c.suggestedTermId && c.nearestTerm) {
    return {
      kind: "same_as",
      label: `'${c.nearestTerm.displayNameKo ?? c.nearestTerm.preferredTerm}'와 동일 추정 (유사도 ${Math.round(score * 100)}%)`,
    };
  }
  if (c.suggestedCanonicalField) {
    return { kind: "new_term", label: `새 표준어 후보 → ${c.suggestedCanonicalField}` };
  }
  return { kind: "free_text", label: "매핑 근거 약함 — 자유문구 가능성" };
}

export default function GlossaryCandidatesPage() {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [statusFilter, setStatusFilter] = useState("pending_review");
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [canonDraft, setCanonDraft] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ limit: "200" });
      if (statusFilter) params.set("status", statusFilter);
      const res = await fetch(`${apiBase}/glossary/candidates?${params}`, {
        cache: "no-store",
        headers: authHeaders("web"),
      });
      if (!res.ok) throw new Error(`후보 조회 실패 (${res.status})`);
      const body = await res.json();
      setCandidates((body.candidates ?? body.terms ?? []) as Candidate[]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "후보를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  async function review(candidate: Candidate, status: ReviewStatus) {
    const id = candId(candidate);
    if (!id) return;
    const payload: Record<string, unknown> = { status };
    if (status === "approved") {
      const canon = (canonDraft[id] ?? candidate.suggestedCanonicalField ?? "").trim();
      if (!canon) {
        setError("새 표준어를 생성하려면 canonicalField를 입력하세요.");
        return;
      }
      payload.canonicalField = canon;
      payload.category = candidate.category;
    } else if (status === "merged") {
      if (!candidate.suggestedTermId) {
        setError("병합할 대상 용어가 없습니다. (AI 유사 용어 제안이 있을 때만 병합 가능)");
        return;
      }
      payload.termId = candidate.suggestedTermId;
      payload.reason = `'${candidate.nearestTerm?.preferredTerm ?? candidate.suggestedTermId}'의 동의어로 병합`;
    } else if (status === "free_text") {
      payload.reason = "표준 필드 매핑이 필요 없는 자유 서술 문구";
    } else {
      payload.reason = "검토 결과 표준 용어로 부적합";
    }
    setBusyId(id);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`${apiBase}/glossary/candidates/${id}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders("web") },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
        throw new Error(typeof body.detail === "string" ? body.detail : `검토 실패 (${res.status})`);
      }
      const body = await res.json();
      const termId = body?.term?.termId;
      const messages: Record<ReviewStatus, string> = {
        approved: `'${candidate.surface}' → 새 표준 용어 ${termId ?? ""} 등록`,
        merged: `'${candidate.surface}' → 기존 용어 ${termId ?? candidate.suggestedTermId}의 동의어로 병합 (다음부터 자동 매핑)`,
        free_text: `'${candidate.surface}' 자유문구 처리 (표준 매핑 제외)`,
        rejected: `'${candidate.surface}' 반려 처리`,
      };
      setNotice(messages[status]);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "검토 처리에 실패했습니다.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-6 pb-8">
      <div className="flex items-start gap-3">
        <Link
          href="/"
          className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
          aria-label="대시보드로"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div>
          <p className="flex items-center gap-2 text-sm font-semibold text-slate-500">
            <BookMarked className="h-4 w-4" /> 용어 표준화
          </p>
          <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">글로서리 후보 검토</h2>
          <p className="mt-2 max-w-3xl text-sm text-slate-500">
            AI는 새 용어를 바로 확정하지 않고 후보로만 판단합니다. 여기서 <b>같은 용어로 병합</b>(동의어 학습) ·{" "}
            <b>새 표준어 생성</b> · <b>자유문구 처리</b> · <b>반려</b>를 최종 결정하면 canonical 필드에 편입됩니다.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
          className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
        >
          <option value="pending_review">검토 대기</option>
          <option value="approved">새 표준어</option>
          <option value="merged">병합됨</option>
          <option value="free_text">자유문구</option>
          <option value="rejected">반려됨</option>
          <option value="">전체</option>
        </select>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 px-3 text-sm font-bold text-slate-700 disabled:opacity-40 dark:border-slate-700 dark:text-slate-200"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          새로고침
        </button>
        <span className="text-sm text-slate-500">{candidates.length}건</span>
      </div>

      {error ? <p className="text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}
      {notice ? <p className="text-sm font-bold text-emerald-700 dark:text-emerald-300">{notice}</p> : null}

      {candidates.length === 0 && !loading ? (
        <EmptyState title="검토할 후보가 없습니다" body="통합심사를 실행하면 미매핑 용어가 후보로 적재됩니다." />
      ) : (
        <div className="space-y-3">
          {candidates.map((c) => {
            const id = candId(c);
            const isPending = (c.status ?? "pending_review") === "pending_review";
            const suggestion = aiSuggestion(c);
            const busy = busyId === id;
            return (
              <div
                key={id}
                className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-base font-black text-slate-950 dark:text-white">{c.surface}</span>
                      <StatusBadge
                        tone={STATUS_TONE[c.status ?? "pending_review"] ?? "neutral"}
                        label={STATUS_LABEL[c.status ?? "pending_review"] ?? c.status ?? "pending_review"}
                      />
                    </div>
                    <p className="mt-1 text-xs text-slate-500">
                      {c.category ?? "미분류"}
                      {c.sourceField ? <span className="ml-2 font-mono text-slate-400">{c.sourceField}</span> : null}
                      {c.projectId ? <span className="ml-2 text-slate-400">프로젝트 {c.projectId}</span> : null}
                    </p>
                    <p
                      className={`mt-2 inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-bold ${
                        suggestion.kind === "same_as"
                          ? "bg-sky-50 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300"
                          : suggestion.kind === "new_term"
                            ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
                            : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                      }`}
                    >
                      AI 판단: {suggestion.label}
                    </p>
                  </div>

                  {isPending ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => void review(c, "merged")}
                        disabled={busy || !c.suggestedTermId}
                        title={c.suggestedTermId ? `'${c.nearestTerm?.preferredTerm}'의 동의어로 등록` : "AI 유사 용어 제안이 있을 때만 병합할 수 있습니다"}
                        className="inline-flex h-8 items-center gap-1 rounded-md bg-sky-600 px-2.5 text-xs font-bold text-white disabled:opacity-40"
                      >
                        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitMerge className="h-3.5 w-3.5" />}
                        같은 용어로 병합
                      </button>
                      <button
                        type="button"
                        onClick={() => void review(c, "approved")}
                        disabled={busy}
                        className="inline-flex h-8 items-center gap-1 rounded-md bg-emerald-600 px-2.5 text-xs font-bold text-white disabled:opacity-40"
                      >
                        <Check className="h-3.5 w-3.5" />
                        새 표준어 생성
                      </button>
                      <button
                        type="button"
                        onClick={() => void review(c, "free_text")}
                        disabled={busy}
                        className="inline-flex h-8 items-center gap-1 rounded-md border border-slate-200 px-2.5 text-xs font-bold text-slate-600 disabled:opacity-40 dark:border-slate-700 dark:text-slate-300"
                      >
                        <Type className="h-3.5 w-3.5" />
                        자유문구
                      </button>
                      <button
                        type="button"
                        onClick={() => void review(c, "rejected")}
                        disabled={busy}
                        className="inline-flex h-8 items-center gap-1 rounded-md border border-rose-200 px-2.5 text-xs font-bold text-rose-600 disabled:opacity-40 dark:border-rose-900 dark:text-rose-300"
                      >
                        <X className="h-3.5 w-3.5" />
                        반려
                      </button>
                    </div>
                  ) : null}
                </div>

                {isPending ? (
                  <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3 text-xs dark:border-slate-800">
                    <span className="font-semibold text-slate-500">새 표준어 생성 시 canonicalField:</span>
                    <input
                      type="text"
                      defaultValue={c.suggestedCanonicalField ?? ""}
                      placeholder="예: legal.security.trustBeneficiary"
                      onChange={(e) => setCanonDraft((d) => ({ ...d, [id]: e.target.value }))}
                      className="h-8 w-72 rounded-md border border-slate-200 bg-white px-2 font-mono text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                    />
                    {c.nearestTerm?.canonicalField ? (
                      <span className="text-slate-400">
                        병합 시 매핑: <span className="font-mono">{c.nearestTerm.canonicalField}</span>
                      </span>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
