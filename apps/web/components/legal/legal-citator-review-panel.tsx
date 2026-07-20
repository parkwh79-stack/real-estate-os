"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Gavel,
  Loader2,
  RefreshCw,
  RotateCcw,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getLegalCitatorReviewQueue,
  reviewLegalCitation,
  reviewLegalRelation,
} from "../../lib/legal-citator-client";
import type {
  LegalCitatorQueueItem,
  LegalCitatorReviewQueue,
  LegalCitatorReviewStatus,
} from "../../lib/legal-citator-types";
import { EmptyState, StatusBadge, type Tone } from "../ui/primitives";

interface ReviewDraft {
  reason: string;
  evidenceExcerpt: string;
  objectEvidenceItemId: string;
  legalApprovalDelegationId: string;
}

const EMPTY_DRAFT: ReviewDraft = {
  reason: "",
  evidenceExcerpt: "",
  objectEvidenceItemId: "",
  legalApprovalDelegationId: "",
};

export function LegalCitatorReviewPanel({
  projectId,
  onCountChanged,
}: {
  projectId: string;
  onCountChanged?: (count: number) => void;
}) {
  const [queue, setQueue] = useState<LegalCitatorReviewQueue | null>(null);
  const [drafts, setDrafts] = useState<Record<string, ReviewDraft>>({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const load = useCallback(
    async (signal: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const result = await getLegalCitatorReviewQueue(projectId, {
          limit: 200,
          signal,
        });
        if (!signal.aborted) {
          setQueue(result);
          onCountChanged?.(result.totalCount);
        }
      } catch (nextError) {
        if (signal.aborted) return;
        setError(
          nextError instanceof Error
            ? nextError.message
            : "Citator 검토 대기열을 불러오지 못했습니다.",
        );
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    [onCountChanged, projectId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, reloadKey]);

  const items = useMemo(
    () => [...(queue?.citations ?? []), ...(queue?.relations ?? [])],
    [queue],
  );

  const updateDraft = (
    recordId: string,
    key: keyof ReviewDraft,
    value: string,
  ) => {
    setDrafts((current) => ({
      ...current,
      [recordId]: {
        ...(current[recordId] ?? EMPTY_DRAFT),
        [key]: value,
      },
    }));
  };

  const review = async (
    item: LegalCitatorQueueItem,
    reviewStatus: LegalCitatorReviewStatus,
  ) => {
    const draft = drafts[item.recordId] ?? EMPTY_DRAFT;
    setSubmitting(item.recordId);
    setError(null);
    setSuccess(null);
    try {
      const input = {
        reviewStatus,
        reason: draft.reason,
        evidenceExcerpt: draft.evidenceExcerpt || undefined,
        objectEvidenceItemId:
          item.recordKind === "relation"
            ? draft.objectEvidenceItemId || undefined
            : undefined,
        supersedesReviewEventId: item.latestReviewEventId,
        legalApprovalDelegationId:
          draft.legalApprovalDelegationId || undefined,
      };
      const outcome =
        item.recordKind === "citation"
          ? await reviewLegalCitation(projectId, item.recordId, input)
          : await reviewLegalRelation(projectId, item.recordId, input);
      setSuccess(
        `${recordKindLabel(item)} 검토 v${outcome.reviewVersion}을 append-only overlay에 기록했습니다.`,
      );
      setDrafts((current) => {
        const next = { ...current };
        delete next[item.recordId];
        return next;
      });
      setReloadKey((value) => value + 1);
    } catch (nextError) {
      setError(
        nextError instanceof Error
          ? nextError.message
          : "Citator 검토 결과를 저장하지 못했습니다.",
      );
    } finally {
      setSubmitting(null);
    }
  };

  if (loading && !queue) {
    return (
      <div className="animate-pulse space-y-3">
        <div className="h-16 rounded-md bg-slate-100 dark:bg-slate-800" />
        <div className="h-80 rounded-md bg-slate-100 dark:bg-slate-800" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-100">
        <p className="max-w-4xl">
          공식 Corpus의 citation·relation 후보는 불변입니다. 이 화면의 사람
          검토는 tenant/project overlay에 append-only로 추가되며, Corpus 후보
          상태와 프로젝트 검토 상태를 서로 다른 배지로 표시합니다.
        </p>
        <button
          type="button"
          onClick={() => setReloadKey((value) => value + 1)}
          disabled={loading}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-amber-300 bg-white px-2.5 font-black text-amber-900 disabled:opacity-50 dark:border-amber-800 dark:bg-slate-950 dark:text-amber-100"
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          대기열 새로고침
        </button>
      </div>

      {error ? (
        <p
          role="alert"
          className="flex items-start gap-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-bold text-rose-800 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-100"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </p>
      ) : null}
      {success ? (
        <p
          role="status"
          className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-100"
        >
          {success}
        </p>
      ) : null}

      {!items.length ? (
        <EmptyState
          title="프로젝트 Citator 검토 대기 항목이 없습니다"
          body="현재 프로젝트 evidence snapshot lineage에 연결된 citation 또는 relation 후보가 없습니다."
          icon={<Gavel className="h-7 w-7" />}
        />
      ) : (
        <div className="space-y-4">
          {items.map((item) => {
            const draft = drafts[item.recordId] ?? EMPTY_DRAFT;
            return (
              <article
                key={`${item.recordKind}-${item.recordId}`}
                className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-950/30"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <StatusBadge
                        tone="warn"
                        label={`Corpus · ${item.corpusStatus}`}
                      />
                      <StatusBadge
                        tone={overlayTone(item)}
                        label={`Project overlay · ${
                          item.reviewStatus ?? "미검토"
                        }`}
                      />
                      <StatusBadge
                        tone={humanReviewTone(item.humanReviewStatus)}
                        label={`사람 검토 · ${item.humanReviewStatus}`}
                      />
                    </div>
                    <h3 className="mt-2 break-all text-sm font-black text-slate-950 dark:text-white">
                      {item.recordKind === "citation"
                        ? item.identifier
                        : `${item.subjectResourceId} ${item.relationType} ${item.objectResourceId}`}
                    </h3>
                    <p className="mt-1 text-xs font-semibold text-slate-500">
                      {recordKindLabel(item)} · {item.assertionMethod} ·
                      confidence {formatConfidence(item.confidence)} · 현재 overlay
                      효과 {item.status}
                    </p>
                  </div>
                  <div className="text-right text-[11px] font-semibold text-slate-500">
                    <p>{formatDateTime(item.assertedAt)}</p>
                    <p className="mt-1 font-mono">
                      {item.reviewVersion
                        ? `overlay v${item.reviewVersion}`
                        : "overlay 없음"}
                    </p>
                  </div>
                </div>

                {item.latestReviewVersionId ? (
                  <div className="mt-3 rounded-md border border-indigo-200 bg-indigo-50/50 px-3 py-2 text-xs text-indigo-900 dark:border-indigo-900 dark:bg-indigo-950/20 dark:text-indigo-100">
                    <p className="font-black">
                      최신 project overlay 검토: {item.reviewStatus}
                    </p>
                    <p className="mt-1 break-all font-mono text-[11px]">
                      다음 결정은 event {item.latestReviewEventId}를 정확히
                      supersede합니다.
                    </p>
                    <p className="mt-1">
                      {item.reviewedBy ?? "검토자 미확인"} ·{" "}
                      {formatDateTime(item.reviewedAt)}
                    </p>
                  </div>
                ) : null}

                <div className="mt-4 grid gap-3 lg:grid-cols-2">
                  <label className="text-xs font-black text-slate-700 dark:text-slate-200">
                    검토 사유
                    <textarea
                      value={draft.reason}
                      onChange={(event) =>
                        updateDraft(
                          item.recordId,
                          "reason",
                          event.target.value,
                        )
                      }
                      rows={4}
                      aria-label={`${recordKindLabel(item)} 검토 사유`}
                      placeholder="판단 근거와 보완 요청을 기록하세요."
                      className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 font-normal leading-5 dark:border-slate-700 dark:bg-slate-900 dark:text-white"
                    />
                  </label>
                  <label className="text-xs font-black text-slate-700 dark:text-slate-200">
                    원문 exact excerpt (승인 필수)
                    <textarea
                      value={draft.evidenceExcerpt}
                      onChange={(event) =>
                        updateDraft(
                          item.recordId,
                          "evidenceExcerpt",
                          event.target.value,
                        )
                      }
                      rows={4}
                      aria-label={`${recordKindLabel(item)} 원문 exact excerpt`}
                      placeholder="공식 원문에서 직접 확인한 구절을 그대로 붙여 넣으세요."
                      className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 font-normal leading-5 dark:border-slate-700 dark:bg-slate-900 dark:text-white"
                    />
                    <span className="mt-1 block font-semibold text-slate-500">
                      승인 시 브라우저 Web Crypto가 trim된 exact excerpt의
                      SHA-256을 계산합니다.
                    </span>
                  </label>
                </div>

                {item.recordKind === "relation" ? (
                  <label className="mt-3 block text-xs font-black text-slate-700 dark:text-slate-200">
                    대상 근거 evidence item ID (승인 필수)
                    <input
                      value={draft.objectEvidenceItemId}
                      onChange={(event) =>
                        updateDraft(
                          item.recordId,
                          "objectEvidenceItemId",
                          event.target.value,
                        )
                      }
                      aria-label={`${recordKindLabel(item)} 대상 evidence item ID`}
                      placeholder="현재 project snapshot의 object resource evidence item ID"
                      className="mt-1 h-10 w-full rounded-md border border-slate-300 bg-white px-3 font-mono text-xs font-normal dark:border-slate-700 dark:bg-slate-900 dark:text-white"
                    />
                    <span className="mt-1 block font-semibold text-slate-500">
                      서버와 DB가 relation의 object resource, version, provision,
                      provider, fetch, locator 및 해시를 현재 snapshot에서 다시
                      검증합니다.
                    </span>
                  </label>
                ) : null}

                <label className="mt-3 block text-xs font-black text-slate-700 dark:text-slate-200">
                  외부 법률자문 delegation ID (선택)
                  <input
                    value={draft.legalApprovalDelegationId}
                    onChange={(event) =>
                      updateDraft(
                        item.recordId,
                        "legalApprovalDelegationId",
                        event.target.value,
                      )
                    }
                    aria-label={`${recordKindLabel(item)} delegation ID`}
                    placeholder="external_counsel인 경우 유효한 citator_review 위임 ID"
                    className="mt-1 h-10 w-full rounded-md border border-slate-300 bg-white px-3 font-mono text-xs font-normal dark:border-slate-700 dark:bg-slate-900 dark:text-white"
                  />
                </label>

                <div className="mt-3 flex flex-wrap gap-2">
                  <ReviewButton
                    label="승인"
                    icon={<CheckCircle2 className="h-3.5 w-3.5" />}
                    tone="good"
                    busy={submitting === item.recordId}
                    disabled={
                      submitting !== null ||
                      (item.recordKind === "relation" &&
                        !draft.objectEvidenceItemId.trim())
                    }
                    onClick={() => void review(item, "approved")}
                  />
                  <ReviewButton
                    label="수정 요청"
                    icon={<RotateCcw className="h-3.5 w-3.5" />}
                    tone="warn"
                    busy={submitting === item.recordId}
                    disabled={submitting !== null}
                    onClick={() => void review(item, "needs_revision")}
                  />
                  <ReviewButton
                    label="기각"
                    icon={<XCircle className="h-3.5 w-3.5" />}
                    tone="bad"
                    busy={submitting === item.recordId}
                    disabled={submitting !== null}
                    onClick={() => void review(item, "rejected")}
                  />
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ReviewButton({
  label,
  icon,
  tone,
  busy,
  disabled,
  onClick,
}: {
  label: string;
  icon: React.ReactNode;
  tone: "good" | "warn" | "bad";
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  const classes = {
    good: "bg-emerald-700 text-white",
    warn: "bg-amber-600 text-white",
    bad: "bg-rose-700 text-white",
  }[tone];
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex h-9 items-center gap-1.5 rounded-md px-3 text-xs font-black disabled:cursor-not-allowed disabled:opacity-50 ${classes}`}
    >
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : icon}
      {label}
    </button>
  );
}

function recordKindLabel(item: LegalCitatorQueueItem): string {
  return item.recordKind === "citation" ? "Citation" : "해석 관계";
}

function humanReviewTone(
  status: LegalCitatorQueueItem["humanReviewStatus"],
): Tone {
  return status === "approved"
    ? "good"
    : status === "rejected"
      ? "bad"
      : status === "pending"
        ? "warn"
        : "neutral";
}

function overlayTone(item: LegalCitatorQueueItem): Tone {
  return item.reviewStatus === "approved"
    ? "good"
    : item.reviewStatus === "rejected"
      ? "bad"
      : item.reviewStatus === "needs_revision"
        ? "warn"
        : "neutral";
}

function formatConfidence(value?: number): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "미확인";
}

function formatDateTime(value?: string): string {
  if (!value) return "미지정";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}
