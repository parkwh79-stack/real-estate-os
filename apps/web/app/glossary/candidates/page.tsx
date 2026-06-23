"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, BookMarked, Check, Loader2, RefreshCw, X } from "lucide-react";
import { apiBase, authHeaders } from "../../../lib/auth-client";
import { EmptyState, StatusBadge } from "../../../components/ui/primitives";

interface Candidate {
  candidateId?: string;
  candidate_id?: string;
  surface: string;
  category?: string;
  sourceField?: string;
  suggestedCanonicalField?: string | null;
  status?: string;
  projectId?: string | null;
  confidence?: number | null;
}

const STATUS_TONE: Record<string, "good" | "warn" | "bad" | "neutral"> = {
  approved: "good",
  pending_review: "warn",
  needs_revision: "warn",
  rejected: "bad",
  merged: "neutral",
};

function candId(c: Candidate): string {
  return String(c.candidateId ?? c.candidate_id ?? "");
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

  async function review(candidate: Candidate, status: "approved" | "rejected") {
    const id = candId(candidate);
    if (!id) return;
    const payload: Record<string, unknown> = { status };
    if (status === "approved") {
      const canon = (canonDraft[id] ?? candidate.suggestedCanonicalField ?? "").trim();
      if (!canon) {
        setError("승인하려면 canonicalField를 입력하세요.");
        return;
      }
      payload.canonicalField = canon;
      payload.category = candidate.category;
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
      setNotice(status === "approved"
        ? `'${candidate.surface}' 승인 → 표준 용어 ${termId ?? ""} 등록`
        : `'${candidate.surface}' 반려 처리`);
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
            문서·워크북에서 추출됐지만 표준 용어로 매핑되지 않은 후보를 검토하여 canonical 필드와 함께 글로서리에 승격합니다.
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
          <option value="approved">승인됨</option>
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
        <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="px-3 py-2">표면형</th>
                <th className="px-3 py-2">분류/출처</th>
                <th className="px-3 py-2">canonicalField (승인 시)</th>
                <th className="px-3 py-2">상태</th>
                <th className="px-3 py-2 text-right">검토</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((c) => {
                const id = candId(c);
                const isPending = (c.status ?? "pending_review") === "pending_review";
                return (
                  <tr key={id} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="px-3 py-2 font-semibold text-slate-900 dark:text-white">{c.surface}</td>
                    <td className="px-3 py-2 text-xs text-slate-500">
                      {c.category ?? "-"}
                      <span className="ml-1 font-mono text-slate-400">{c.sourceField}</span>
                    </td>
                    <td className="px-3 py-2">
                      <input
                        type="text"
                        defaultValue={c.suggestedCanonicalField ?? ""}
                        placeholder="예: legal.security.xxx"
                        onChange={(e) => setCanonDraft((d) => ({ ...d, [id]: e.target.value }))}
                        disabled={!isPending}
                        className="h-8 w-56 rounded-md border border-slate-200 bg-white px-2 font-mono text-xs text-slate-900 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
                      />
                    </td>
                    <td className="px-3 py-2">
                      <StatusBadge tone={STATUS_TONE[c.status ?? "pending_review"] ?? "neutral"} label={c.status ?? "pending_review"} />
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => void review(c, "approved")}
                          disabled={!isPending || busyId === id}
                          className="inline-flex h-8 items-center gap-1 rounded-md bg-emerald-600 px-2 text-xs font-bold text-white disabled:opacity-40"
                        >
                          {busyId === id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                          승인
                        </button>
                        <button
                          type="button"
                          onClick={() => void review(c, "rejected")}
                          disabled={!isPending || busyId === id}
                          className="inline-flex h-8 items-center gap-1 rounded-md border border-slate-200 px-2 text-xs font-bold text-slate-600 disabled:opacity-40 dark:border-slate-700 dark:text-slate-300"
                        >
                          <X className="h-3.5 w-3.5" />
                          반려
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
