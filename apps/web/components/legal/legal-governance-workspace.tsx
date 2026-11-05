"use client";

import {
  AlertTriangle,
  Bot,
  FileLock2,
  FileSearch,
  Gavel,
  Loader2,
  RefreshCw,
  Scale,
  ShieldCheck,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  approveLegalRequiredClaimSet,
  getLegalGovernanceWorkspace,
  proposeLegalRequiredClaimSet,
  reviewTenantLegalTermAlias,
} from "../../lib/legal-governance-client";
import type {
  CounselOpinionMetadata,
  LegalApprovalDelegation,
  LegalAnalysisMetadata,
  LegalGovernanceAnnotation,
  LegalGovernanceMatter,
  LegalGovernanceWaiver,
  LegalGovernanceWorkspace,
  LegalRequiredClaimReviewScope,
  LegalRequiredClaimSet,
  TenantLegalTermAlias,
} from "../../lib/legal-governance-types";
import {
  EmptyState,
  MetricCard,
  SkeletonMetrics,
  StatusBadge,
  type Tone,
} from "../ui/primitives";
import { LegalCitatorReviewPanel } from "./legal-citator-review-panel";

type WorkspaceTab =
  | "matters"
  | "evidence"
  | "analysis"
  | "opinions"
  | "waivers"
  | "delegations"
  | "claim_sets"
  | "citator"
  | "glossary";

const TAB_LABELS: Record<WorkspaceTab, string> = {
  matters: "법률 이슈",
  evidence: "공식 원문·근거",
  analysis: "AI 분석",
  opinions: "법률의견",
  waivers: "Waiver",
  delegations: "대리승인",
  claim_sets: "필수 쟁점 정책",
  citator: "Citator 검토",
  glossary: "용어 사전",
};

export function LegalGovernanceWorkspace({
  projectId,
  className = "",
}: {
  projectId: string;
  className?: string;
}) {
  const [workspace, setWorkspace] = useState<LegalGovernanceWorkspace | null>(
    null,
  );
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("matters");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [citatorCount, setCitatorCount] = useState<number | null>(null);

  const load = useCallback(
    async (signal: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const result = await getLegalGovernanceWorkspace(projectId, { signal });
        if (!signal.aborted) setWorkspace(result);
      } catch (nextError) {
        if (signal.aborted) return;
        setWorkspace(null);
        setCitatorCount(null);
        setError(
          nextError instanceof Error
            ? nextError.message
            : "법률 거버넌스 워크스페이스를 불러오지 못했습니다.",
        );
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    [projectId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, reloadKey]);

  const counts = useMemo(
    () => ({
      matters:
        workspace?.matters.filter(
          (item) => !["closed", "resolved", "superseded"].includes(item.status),
        ).length ?? "—",
      evidence: workspace?.annotations.filter((item) => item.status === "active")
        .length ?? "—",
      analysis:
        workspace?.analysisVersions.filter(
          (item) => item.humanReviewStatus === "pending",
        ).length ?? "—",
      opinions:
        workspace?.counselOpinions.filter(
          (item) => !["approved", "withdrawn", "superseded"].includes(item.status),
        ).length ?? "—",
      waivers:
        workspace?.waivers.filter((item) =>
          ["proposed", "approved"].includes(item.status),
        ).length ?? "—",
      delegations:
        workspace?.approvalDelegations.filter(
          (item) =>
            item.status === "active" &&
            new Date(item.validUntil) >= new Date(),
        ).length ?? "—",
      claim_sets:
        workspace?.requiredClaimSets.filter((item) => item.status === "draft")
          .length ?? "—",
      citator: citatorCount ?? "—",
      glossary:
        workspace?.tenantTermAliases.filter(
          (item) => item.status === "candidate",
        ).length ?? "—",
    }),
    [citatorCount, workspace],
  );
  const readOnly = workspace !== null && workspace.reviewWriteAvailable !== true;

  return (
    <section
      className={`overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900 ${className}`}
      aria-labelledby="legal-governance-title"
    >
      <header className="border-b border-slate-200 bg-slate-50/80 p-4 dark:border-slate-800 dark:bg-slate-950/50">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-[11px] font-black uppercase tracking-[0.16em] text-indigo-700 dark:text-indigo-300">
              Tenant-native Legal Governance
            </p>
            <h2
              id="legal-governance-title"
              className="mt-1 text-lg font-black text-slate-950 dark:text-white"
            >
              프로젝트 법률 거버넌스 워크스페이스
            </h2>
            <p className="mt-1 max-w-3xl text-xs font-semibold leading-5 text-slate-600 dark:text-slate-300">
              공식 원문과 근거, AI 분석, 변호사 법률의견, Waiver를 서로 다른
              기록으로 관리합니다. AI 분석과 검색 후보는 사람의 법률 승인으로
              간주되지 않습니다.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setReloadKey((value) => value + 1)}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-2 text-xs font-black text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          >
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            새로고침
          </button>
        </div>
      </header>

      <div className="p-4">
        {loading && !workspace ? (
          <SkeletonMetrics count={5} />
        ) : (
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4 xl:grid-cols-9">
            <MetricCard label="진행 중 이슈" value={counts.matters} />
            <MetricCard label="활성 근거 연결" value={counts.evidence} />
            <MetricCard label="AI 검토 대기" value={counts.analysis} />
            <MetricCard label="의견 승인 대기" value={counts.opinions} />
            <MetricCard label="활성 Waiver" value={counts.waivers} />
            <MetricCard label="활성 대리승인" value={counts.delegations} />
            <MetricCard label="필수 쟁점 승인 대기" value={counts.claim_sets} />
            <MetricCard label="Citator 검토 대기" value={counts.citator} />
            <MetricCard label="용어 검토 대기" value={counts.glossary} />
          </div>
        )}

        {error ? (
          <div
            role="alert"
            className="mt-4 flex items-start gap-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-bold leading-5 text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        ) : null}

        {readOnly ? (
          <p role="status" className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100">
            저장된 법률 기록을 조회할 수 있습니다. 현재 서버에서는 법률 검토 결과를 저장하거나 승인할 수 없습니다.
          </p>
        ) : null}

        <div
          role="tablist"
          aria-label="법률 거버넌스 기록 유형"
          className="mt-4 flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800"
        >
          {(Object.keys(TAB_LABELS) as WorkspaceTab[]).map((tab) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={activeTab === tab}
              onClick={() => setActiveTab(tab)}
              className={`whitespace-nowrap border-b-2 px-3 py-2 text-xs font-black transition ${
                activeTab === tab
                  ? "border-indigo-600 text-indigo-700 dark:border-indigo-400 dark:text-indigo-300"
                  : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
              }`}
            >
              {TAB_LABELS[tab]}
              <span className="ml-1.5 rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] dark:bg-slate-800">
                {counts[tab]}
              </span>
            </button>
          ))}
        </div>

        <div
          role="tabpanel"
          className="min-h-64 pt-4"
          aria-label={TAB_LABELS[activeTab]}
        >
          {!workspace && loading ? (
            <ListSkeleton />
          ) : !workspace ? (
            <EmptyState
              title="법률 기록을 확인할 수 없습니다"
              body="새로고침을 눌러 서버의 법률 기록을 다시 불러올 수 있습니다."
              icon={<Scale className="h-7 w-7" />}
            />
          ) : activeTab === "matters" ? (
            <MatterList items={workspace.matters} />
          ) : activeTab === "evidence" ? (
            <EvidenceList items={workspace.annotations} />
          ) : activeTab === "analysis" ? (
            <AnalysisList items={workspace.analysisVersions} />
          ) : activeTab === "opinions" ? (
            <OpinionList items={workspace.counselOpinions} />
          ) : activeTab === "waivers" ? (
            <WaiverList items={workspace.waivers} />
          ) : activeTab === "delegations" ? (
            <DelegationList items={workspace.approvalDelegations} />
          ) : activeTab === "claim_sets" ? (
            <RequiredClaimSetPanel
              projectId={projectId}
              items={workspace.requiredClaimSets}
              readOnly={readOnly}
              onChanged={() => setReloadKey((value) => value + 1)}
            />
          ) : activeTab === "citator" ? (
            <LegalCitatorReviewPanel
              projectId={projectId}
              readOnly={readOnly}
              onCountChanged={setCitatorCount}
            />
          ) : (
            <GlossaryAliasPanel
              projectId={projectId}
              items={workspace.tenantTermAliases}
              readOnly={readOnly}
              onChanged={() => setReloadKey((value) => value + 1)}
            />
          )}
        </div>
      </div>
    </section>
  );
}

function RequiredClaimSetPanel({
  projectId,
  items,
  readOnly,
  onChanged,
}: {
  projectId: string;
  items: LegalRequiredClaimSet[];
  readOnly: boolean;
  onChanged: () => void;
}) {
  const [claimSetKey, setClaimSetKey] = useState("approval-gate");
  const [claimSetVersion, setClaimSetVersion] = useState("1");
  const [reviewScope, setReviewScope] =
    useState<LegalRequiredClaimReviewScope>("approval_gate");
  const [claimsText, setClaimsText] = useState("");
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const propose = async () => {
    if (readOnly) return;
    setError(null);
    setSuccess(null);
    const version = Number(claimSetVersion);
    if (!claimSetKey.trim() || !Number.isInteger(version) || version < 1) {
      setError("정책 key와 1 이상의 정수 버전을 입력해 주세요.");
      return;
    }
    const claims = claimsText
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [claimKey = "", claimTextSha256 = "", ...description] =
          line.split("|");
        return {
          claimKey: claimKey.trim(),
          claimTextSha256: claimTextSha256.trim(),
          description: description.join("|").trim() || undefined,
        };
      });
    if (
      claims.length === 0
      || claims.some(
        (claim) =>
          !/^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/.test(claim.claimKey)
          || !/^(?:sha256:)?[0-9a-fA-F]{64}$/.test(claim.claimTextSha256),
      )
    ) {
      setError(
        "각 줄을 claimKey|64자리 SHA-256|설명 형식으로 입력해 주세요.",
      );
      return;
    }
    if (new Set(claims.map((claim) => claim.claimKey)).size !== claims.length) {
      setError("한 정책 안에서 claimKey는 중복될 수 없습니다.");
      return;
    }
    setSubmitting("proposal");
    try {
      await proposeLegalRequiredClaimSet(projectId, {
        claimSetKey: claimSetKey.trim(),
        claimSetVersion: version,
        reviewScope,
        claims,
      });
      setClaimsText("");
      setSuccess("필수 쟁점 정책 초안을 append-only 기록으로 제안했습니다.");
      onChanged();
    } catch (nextError) {
      setError(
        nextError instanceof Error
          ? nextError.message
          : "필수 쟁점 정책을 제안하지 못했습니다.",
      );
    } finally {
      setSubmitting(null);
    }
  };

  const approve = async (claimSetId: string) => {
    if (readOnly) return;
    setSubmitting(claimSetId);
    setError(null);
    setSuccess(null);
    try {
      await approveLegalRequiredClaimSet(projectId, claimSetId);
      setSuccess(
        "독립 legal_counsel_approver 승인 버전을 append-only로 기록했습니다.",
      );
      onChanged();
    } catch (nextError) {
      setError(
        nextError instanceof Error
          ? nextError.message
          : "필수 쟁점 정책을 승인하지 못했습니다.",
      );
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <div className="space-y-4">
      <SeparationNotice tone="warn">
        Approval Gate의 required claim count는 이 PostgreSQL 정책에서
        재계산합니다. 작성자와 승인자는 달라야 하며, 승인 요청은 actor·role을
        화면 payload로 받지 않고 인증 세션에서 확정합니다.
      </SeparationNotice>
      <section className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-950/50">
        <h3 className="text-sm font-black text-slate-950 dark:text-white">
          필수 쟁점 정책 초안 제안
        </h3>
        <div className="mt-3 grid gap-2 md:grid-cols-[1.2fr_0.5fr_1fr]">
          <input
            value={claimSetKey}
            onChange={(event) => setClaimSetKey(event.target.value)}
            placeholder="정책 key (예: approval-gate)"
            className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-white"
          />
          <input
            value={claimSetVersion}
            onChange={(event) => setClaimSetVersion(event.target.value)}
            type="number"
            min={1}
            aria-label="정책 버전"
            className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-white"
          />
          <select
            value={reviewScope}
            onChange={(event) =>
              setReviewScope(
                event.target.value as LegalRequiredClaimReviewScope,
              )
            }
            aria-label="검토 범위"
            className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-white"
          >
            <option value="approval_gate">Approval Gate</option>
            <option value="report_eligibility">보고서 사용 적격성</option>
            <option value="counsel_opinion">법률의견</option>
          </select>
        </div>
        <textarea
          value={claimsText}
          onChange={(event) => setClaimsText(event.target.value)}
          rows={4}
          aria-label="필수 쟁점 목록"
          placeholder={"claimKey|64자리 SHA-256|설명\npermit.zoning|0123…abcd|용도지역 적용성"}
          className="mt-2 w-full rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-xs dark:border-slate-700 dark:bg-slate-900 dark:text-white"
        />
        <button
          type="button"
          onClick={() => void propose()}
          disabled={readOnly || submitting !== null}
          className="mt-2 inline-flex h-9 items-center gap-2 rounded-md bg-indigo-700 px-3 text-xs font-black text-white disabled:opacity-50"
        >
          {submitting === "proposal" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <FileSearch className="h-3.5 w-3.5" />
          )}
          정책 초안 제안
        </button>
        {error ? (
          <p role="alert" className="mt-2 text-xs font-bold text-rose-700">
            {error}
          </p>
        ) : null}
        {success ? (
          <p role="status" className="mt-2 text-xs font-bold text-emerald-700">
            {success}
          </p>
        ) : null}
      </section>
      {!items.length ? (
        <EmptyState
          title="등록된 필수 쟁점 정책이 없습니다"
          body="정책을 제안하고 독립 법무 승인자가 승인해야 새 evidence snapshot이 Gate 적격이 될 수 있습니다."
          icon={<ShieldCheck className="h-7 w-7" />}
        />
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <article
              key={item.claimSetId}
              className="rounded-lg border border-indigo-200 bg-indigo-50/30 p-3 dark:border-indigo-900 dark:bg-indigo-950/10"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <StatusBadge
                    tone={
                      item.status === "approved"
                        ? "good"
                        : item.status === "rejected"
                          ? "bad"
                          : "warn"
                    }
                    label={item.status}
                  />
                  <StatusBadge tone="info" label={item.reviewScope} />
                  <strong className="text-sm text-slate-950 dark:text-white">
                    {item.claimSetKey} · v{item.claimSetVersion}
                  </strong>
                </div>
                {item.status === "draft" ? (
                  <button
                    type="button"
                    onClick={() => void approve(item.claimSetId)}
                    disabled={readOnly || submitting !== null}
                    className="inline-flex h-8 items-center gap-1.5 rounded-md bg-emerald-700 px-2.5 text-xs font-black text-white disabled:opacity-50"
                  >
                    {submitting === item.claimSetId ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <ShieldCheck className="h-3.5 w-3.5" />
                    )}
                    독립 승인
                  </button>
                ) : null}
              </div>
              <ul className="mt-2 space-y-1">
                {item.claims.map((claim) => (
                  <li
                    key={`${item.claimSetId}-${claim.claimKey}`}
                    className="rounded-md bg-white/80 px-2 py-1.5 text-xs dark:bg-slate-950/60"
                  >
                    <span className="font-black">{claim.claimKey}</span>
                    <span className="ml-2 text-slate-500">
                      {shortHash(claim.claimTextSha256)}
                    </span>
                    {claim.description ? (
                      <span className="ml-2 text-slate-700 dark:text-slate-200">
                        {claim.description}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
              <dl className="mt-3 grid gap-2 text-xs md:grid-cols-4">
                <Metadata label="정책 hash" value={shortHash(item.claimSetSha256)} mono />
                <Metadata label="작성자" value={item.createdBy} />
                <Metadata label="승인자" value={item.approvedBy ?? "maker-checker 대기"} />
                <Metadata label="승인 시각" value={formatDateTime(item.approvedAt)} />
              </dl>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

function MatterList({ items }: { items: LegalGovernanceMatter[] }) {
  if (!items.length) {
    return (
      <EmptyState
        title="등록된 법률 이슈가 없습니다"
        body="법률·인허가 검토 항목은 프로젝트와 tenant에 귀속된 append-only 버전으로 기록됩니다."
        icon={<Gavel className="h-7 w-7" />}
      />
    );
  }
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {items.map((item) => (
        <article
          key={item.matterId}
          className="rounded-lg border border-slate-200 p-3 dark:border-slate-800"
        >
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusBadge
              tone={matterTone(item.status)}
              label={matterStatusLabel(item.status)}
            />
            <StatusBadge
              tone={severityTone(item.severity)}
              label={severityLabel(item.severity)}
            />
            <span className="text-[11px] font-bold text-slate-500">
              v{item.matterVersion}
            </span>
          </div>
          <h3 className="mt-2 text-sm font-black text-slate-950 dark:text-white">
            {item.title}
          </h3>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <Metadata label="유형" value={matterTypeLabel(item.matterType)} />
            <Metadata label="담당자" value={item.ownerUserId ?? "미지정"} />
            <Metadata label="개시" value={formatDateTime(item.openedAt)} />
            <Metadata label="기한" value={formatDateTime(item.dueAt)} />
          </dl>
        </article>
      ))}
    </div>
  );
}

function EvidenceList({ items }: { items: LegalGovernanceAnnotation[] }) {
  if (!items.length) {
    return (
      <EmptyState
        title="연결된 공식 원문·근거가 없습니다"
        body="내부 legal_review 레코드가 아니라 corpus 원문 버전과 정확한 evidence locator가 연결되어야 합니다."
        icon={<FileSearch className="h-7 w-7" />}
      />
    );
  }
  return (
    <div className="space-y-3">
      <SeparationNotice>
        이 목록은 공식 원문 자체가 아니라 검증 대상 원문 버전과 정확한
        locator의 연결 기록입니다. 내부 메모 위치는 승인 근거로 사용하지
        않습니다.
      </SeparationNotice>
      {items.map((item) => (
        <article
          key={item.annotationId}
          className="rounded-lg border border-emerald-200 bg-emerald-50/40 p-3 dark:border-emerald-900 dark:bg-emerald-950/10"
        >
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusBadge
              tone={item.status === "active" ? "good" : "neutral"}
              label={annotationStatusLabel(item.status)}
            />
            <StatusBadge tone="info" label={annotationTypeLabel(item.annotationType)} />
          </div>
          <p className="mt-2 break-words text-sm font-black text-slate-900 dark:text-white">
            {item.exactLocator}
          </p>
          <dl className="mt-3 grid gap-2 text-xs md:grid-cols-3">
            <Metadata label="Corpus resource" value={item.legalResourceId} mono />
            <Metadata label="검증 대상 버전" value={item.legalResourceVersionId} mono />
            <Metadata
              label="조문 식별자"
              value={item.legalProvisionId ?? "문서 단위"}
              mono
            />
          </dl>
        </article>
      ))}
    </div>
  );
}

function AnalysisList({ items }: { items: LegalAnalysisMetadata[] }) {
  if (!items.length) {
    return (
      <EmptyState
        title="등록된 AI·결정론적 분석 버전이 없습니다"
        body="분석 결과는 원문과 분리하여 version/hash와 입력 snapshot을 기록해야 합니다."
        icon={<Bot className="h-7 w-7" />}
      />
    );
  }
  return (
    <div className="space-y-3">
      <SeparationNotice tone="warn">
        AI 분석은 analysisOnly가 기본이며, 사람 검토 전에는 보고서 또는 승인
        근거가 아닙니다. 아래에는 내용 대신 무결성·검토 메타데이터만
        표시합니다.
      </SeparationNotice>
      {items.map((item) => (
        <article
          key={item.analysisVersionId}
          className="rounded-lg border border-violet-200 bg-violet-50/40 p-3 dark:border-violet-900 dark:bg-violet-950/10"
        >
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusBadge
              tone={reviewTone(item.humanReviewStatus)}
              label={`사람 검토 ${reviewLabel(item.humanReviewStatus)}`}
            />
            <StatusBadge
              tone={item.reportEligible ? "good" : "warn"}
              label={item.reportEligible ? "보고서 사용 가능" : "분석 전용"}
            />
            <span className="text-[11px] font-bold text-slate-500">
              {analysisKindLabel(item.analysisKind)} · v{item.analysisVersion}
            </span>
          </div>
          <dl className="mt-3 grid gap-2 text-xs md:grid-cols-3">
            <Metadata label="모델" value={item.modelId ?? "결정론적/미기재"} />
            <Metadata label="생성자" value={item.createdBy} />
            <Metadata label="생성일" value={formatDateTime(item.createdAt)} />
            <Metadata
              label="내용 hash"
              value={shortHash(item.contentSha256)}
              mono
            />
            <Metadata
              label="입력 snapshot hash"
              value={shortHash(item.inputSnapshotSha256)}
              mono
            />
            <Metadata
              label="보호 객체"
              value={item.protectedObjectPresent ? "저장됨 · 별도 권한 필요" : "없음"}
            />
          </dl>
        </article>
      ))}
    </div>
  );
}

function OpinionList({ items }: { items: CounselOpinionMetadata[] }) {
  if (!items.length) {
    return (
      <EmptyState
        title="등록된 법률의견 메타데이터가 없습니다"
        body="법률의견 원문은 권한이 분리된 객체 저장소에 보관하고 이 화면에는 lifecycle과 hash만 표시합니다."
        icon={<FileLock2 className="h-7 w-7" />}
      />
    );
  }
  return (
    <div className="space-y-3">
      <SeparationNotice tone="bad">
        변호사 법률의견은 privileged 자료입니다. 이 워크스페이스는 원문이나
        object locator를 노출하지 않고 승인 상태·버전·hash만 표시합니다.
      </SeparationNotice>
      {items.map((item) => (
        <article
          key={item.opinionId}
          className="rounded-lg border border-blue-200 bg-blue-50/40 p-3 dark:border-blue-900 dark:bg-blue-950/10"
        >
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusBadge
              tone={opinionTone(item.status)}
              label={opinionStatusLabel(item.status)}
              icon={<ShieldCheck className="h-3 w-3" />}
            />
            {item.privileged ? (
              <StatusBadge tone="bad" label="법률상 비밀·접근 제한" />
            ) : null}
            <span className="text-[11px] font-bold text-slate-500">
              v{item.opinionVersion}
            </span>
          </div>
          <dl className="mt-3 grid gap-2 text-xs md:grid-cols-3">
            <Metadata label="작성자" value={item.authoredBy} />
            <Metadata label="작성일" value={formatDateTime(item.authoredAt)} />
            <Metadata
              label="승인자"
              value={item.approvedBy ?? "독립 승인 대기"}
            />
            <Metadata
              label="내용 hash"
              value={shortHash(item.contentSha256)}
              mono
            />
            <Metadata
              label="근거 snapshot"
              value={item.evidenceSnapshotId}
              mono
            />
            <Metadata
              label="보호 원문"
              value={item.protectedObjectPresent ? "보관됨 · 별도 권한 필요" : "미확인"}
            />
          </dl>
        </article>
      ))}
    </div>
  );
}

function WaiverList({ items }: { items: LegalGovernanceWaiver[] }) {
  if (!items.length) {
    return (
      <EmptyState
        title="등록된 Waiver가 없습니다"
        body="Waiver는 법률 승인 자체가 아니며, 유효기간·조건·cure 증빙이 있는 append-only 결정으로 관리됩니다."
        icon={<ShieldCheck className="h-7 w-7" />}
      />
    );
  }
  return (
    <div className="space-y-3">
      <SeparationNotice tone="warn">
        승인된 Waiver도 법률의견이나 공식 원문을 대체하지 않습니다. 유효기간과
        cure 조건이 충족되는 동안에만 별도의 정책에 따라 평가됩니다.
      </SeparationNotice>
      {items.map((item) => (
        <article
          key={item.waiverId}
          className="rounded-lg border border-amber-200 bg-amber-50/40 p-3 dark:border-amber-900 dark:bg-amber-950/10"
        >
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusBadge
              tone={waiverTone(item)}
              label={waiverStatusLabel(item.status)}
            />
            <StatusBadge
              tone={item.cureStatus === "verified" ? "good" : "warn"}
              label={`Cure ${cureStatusLabel(item.cureStatus)}`}
            />
            <span className="text-[11px] font-bold text-slate-500">
              v{item.waiverVersion}
            </span>
          </div>
          <p className="mt-2 text-sm font-bold leading-6 text-slate-900 dark:text-white">
            {item.reason}
          </p>
          <ul className="mt-2 space-y-1">
            {item.conditions.map((condition) => (
              <li
                key={`${item.waiverId}-${condition.code}`}
                className="rounded-md bg-white/80 px-2.5 py-2 text-xs text-slate-700 dark:bg-slate-950/50 dark:text-slate-200"
              >
                <span className="font-black">{condition.code}</span>
                <span className="ml-2">{condition.description}</span>
                {condition.dueAt ? (
                  <span className="ml-2 text-slate-500">
                    기한 {formatDateTime(condition.dueAt)}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
          <dl className="mt-3 grid gap-2 text-xs md:grid-cols-3">
            <Metadata label="유효 시작" value={formatDateTime(item.validFrom)} />
            <Metadata label="유효 종료" value={formatDateTime(item.validUntil)} />
            <Metadata
              label="Cure 근거"
              value={item.cureEvidenceSnapshotId ?? "미제출"}
              mono
            />
          </dl>
        </article>
      ))}
    </div>
  );
}

function DelegationList({ items }: { items: LegalApprovalDelegation[] }) {
  if (!items.length) {
    return (
      <EmptyState
        title="등록된 대리승인이 없습니다"
        body="외부 법률자문 또는 대체 승인자가 최종 결정을 내리려면 기간·범위·위임자를 고정한 append-only delegation이 필요합니다."
        icon={<ShieldCheck className="h-7 w-7" />}
      />
    );
  }
  return (
    <div className="space-y-3">
      <SeparationNotice tone="warn">
        외부 법률자문의 최종 승인·기각·조건부 승인은 유효한 delegation ID와
        함께 기록됩니다. 만료되거나 철회된 위임은 새 결정에 사용할 수 없습니다.
      </SeparationNotice>
      {items.map((item) => {
        const active =
          item.status === "active" &&
          new Date(item.validUntil) >= new Date();
        return (
          <article
            key={item.delegationId}
            className="rounded-lg border border-indigo-200 bg-indigo-50/40 p-3 dark:border-indigo-900 dark:bg-indigo-950/10"
          >
            <div className="flex flex-wrap items-center gap-1.5">
              <StatusBadge
                tone={active ? "good" : "neutral"}
                label={active ? "유효" : item.status}
              />
              <StatusBadge tone="info" label={item.scope} />
              <span className="text-[11px] font-bold text-slate-500">
                v{item.delegationVersion}
              </span>
            </div>
            <p className="mt-2 text-sm font-black text-slate-950 dark:text-white">
              {item.granteeId} · {item.granteeRole}
            </p>
            <p className="mt-1 text-xs leading-5 text-slate-600 dark:text-slate-300">
              {item.reason}
            </p>
            <dl className="mt-3 grid gap-2 text-xs md:grid-cols-3">
              <Metadata label="위임자" value={item.grantorId} />
              <Metadata label="유효 시작" value={formatDateTime(item.validFrom)} />
              <Metadata label="유효 종료" value={formatDateTime(item.validUntil)} />
            </dl>
          </article>
        );
      })}
    </div>
  );
}

function GlossaryAliasPanel({
  projectId,
  items,
  readOnly,
  onChanged,
}: {
  projectId: string;
  items: TenantLegalTermAlias[];
  readOnly: boolean;
  onChanged: () => void;
}) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const supersededIds = useMemo(
    () =>
      new Set(
        items
          .map((item) => item.supersedesAliasId)
          .filter((value): value is string => Boolean(value)),
      ),
    [items],
  );

  const review = async (
    item: TenantLegalTermAlias,
    reviewStatus: "approved" | "rejected" | "needs_revision",
  ) => {
    if (readOnly) return;
    const reason = (reasons[item.aliasId] ?? "").trim();
    if (!reason) {
      setError("용어 alias 검토 사유를 입력해 주세요.");
      return;
    }
    setSubmitting(item.aliasId);
    setError(null);
    setSuccess(null);
    try {
      await reviewTenantLegalTermAlias(projectId, item.aliasId, {
        reviewStatus,
        reason,
      });
      setSuccess(
        reviewStatus === "approved"
          ? "tenant alias 승인 successor를 기록했습니다."
          : reviewStatus === "rejected"
            ? "tenant alias 기각 successor를 기록했습니다."
            : "tenant alias 수정 요청과 새 candidate successor를 기록했습니다.",
      );
      setReasons((current) => {
        const next = { ...current };
        delete next[item.aliasId];
        return next;
      });
      onChanged();
    } catch (nextError) {
      setError(
        nextError instanceof Error
          ? nextError.message
          : "tenant alias 검토를 저장하지 못했습니다.",
      );
    } finally {
      setSubmitting(null);
    }
  };

  if (!items.length) {
    return (
      <EmptyState
        title="등록된 tenant 법률용어 alias가 없습니다"
        body="새 용어는 canonical glossary term에 연결된 candidate로 저장한 뒤 maker-checker 검토를 거쳐야 검색·분류에 사용할 수 있습니다."
        icon={<FileSearch className="h-7 w-7" />}
      />
    );
  }
  return (
    <div className="space-y-3">
      <SeparationNotice tone="warn">
        후보 alias는 공식 정의가 아닙니다. 승인된 canonical term 연결과 별도의
        사람 검토가 완료된 버전만 tenant 검색 보조어로 사용할 수 있습니다.
      </SeparationNotice>
      {error ? (
        <p role="alert" className="text-xs font-bold text-rose-700">
          {error}
        </p>
      ) : null}
      {success ? (
        <p role="status" className="text-xs font-bold text-emerald-700">
          {success}
        </p>
      ) : null}
      {items.map((item) => {
        const isLeafCandidate =
          item.status === "candidate" && !supersededIds.has(item.aliasId);
        return (
          <article
            key={item.aliasId}
            className="rounded-lg border border-cyan-200 bg-cyan-50/40 p-3 dark:border-cyan-900 dark:bg-cyan-950/10"
          >
            <div className="flex flex-wrap items-center gap-1.5">
              <StatusBadge
                tone={
                  item.status === "approved"
                    ? "good"
                    : item.status === "rejected"
                      ? "bad"
                      : isLeafCandidate
                        ? "warn"
                        : "neutral"
                }
                label={
                  item.status === "candidate" && !isLeafCandidate
                    ? "검토 이력 있음"
                    : item.status
                }
              />
              <span className="text-[11px] font-bold text-slate-500">
                v{item.aliasVersion}
              </span>
            </div>
            <p className="mt-2 text-sm font-black text-slate-950 dark:text-white">
              {item.aliasText}
            </p>
            <dl className="mt-3 grid gap-2 text-xs md:grid-cols-3">
              <Metadata label="Canonical term" value={item.termId} mono />
              <Metadata label="정규화 alias" value={item.normalizedAlias} />
              <Metadata label="제안자" value={item.proposedBy} />
              <Metadata
                label="제안 시각"
                value={formatDateTime(item.proposedAt)}
              />
              <Metadata label="검토자" value={item.reviewedBy ?? "검토 대기"} />
              <Metadata
                label="검토 시각"
                value={formatDateTime(item.reviewedAt)}
              />
            </dl>
            {isLeafCandidate ? (
              <div className="mt-3 rounded-md border border-cyan-200 bg-white/80 p-3 dark:border-cyan-900 dark:bg-slate-950/50">
                <label className="text-xs font-black text-slate-700 dark:text-slate-200">
                  alias 검토 사유
                  <textarea
                    value={reasons[item.aliasId] ?? ""}
                    onChange={(event) =>
                      setReasons((current) => ({
                        ...current,
                        [item.aliasId]: event.target.value,
                      }))
                    }
                    rows={3}
                    aria-label={`${item.aliasText} alias 검토 사유`}
                    placeholder="승인·기각·수정 요청의 근거를 입력하세요."
                    className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 font-normal dark:border-slate-700 dark:bg-slate-900 dark:text-white"
                  />
                </label>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void review(item, "approved")}
                    disabled={readOnly || submitting !== null}
                    className="inline-flex h-8 items-center gap-1.5 rounded-md bg-emerald-700 px-2.5 text-xs font-black text-white disabled:opacity-50"
                  >
                    {submitting === item.aliasId ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <ShieldCheck className="h-3.5 w-3.5" />
                    )}
                    승인
                  </button>
                  <button
                    type="button"
                    onClick={() => void review(item, "needs_revision")}
                    disabled={readOnly || submitting !== null}
                    className="h-8 rounded-md bg-amber-600 px-2.5 text-xs font-black text-white disabled:opacity-50"
                  >
                    수정 요청
                  </button>
                  <button
                    type="button"
                    onClick={() => void review(item, "rejected")}
                    disabled={readOnly || submitting !== null}
                    className="h-8 rounded-md bg-rose-700 px-2.5 text-xs font-black text-white disabled:opacity-50"
                  >
                    기각
                  </button>
                </div>
              </div>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

function SeparationNotice({
  children,
  tone = "info",
}: {
  children: React.ReactNode;
  tone?: "info" | "warn" | "bad";
}) {
  const classes = {
    info: "border-cyan-200 bg-cyan-50 text-cyan-900 dark:border-cyan-900 dark:bg-cyan-950/20 dark:text-cyan-100",
    warn: "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-100",
    bad: "border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-100",
  }[tone];
  return (
    <div className={`rounded-md border px-3 py-2 text-xs font-semibold leading-5 ${classes}`}>
      {children}
    </div>
  );
}

function Metadata({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] font-bold text-slate-500">{label}</dt>
      <dd
        className={`mt-0.5 break-all font-bold text-slate-800 dark:text-slate-100 ${
          mono ? "font-mono text-[11px]" : ""
        }`}
      >
        {value || "미확인"}
      </dd>
    </div>
  );
}

function ListSkeleton() {
  return (
    <div className="animate-pulse space-y-3">
      <div className="h-12 rounded-md bg-slate-100 dark:bg-slate-800" />
      <div className="h-36 rounded-md bg-slate-100 dark:bg-slate-800" />
      <div className="h-36 rounded-md bg-slate-100 dark:bg-slate-800" />
    </div>
  );
}

function shortHash(value: string): string {
  const normalized = value.replace(/^sha256:/, "");
  if (!normalized) return "미확인";
  return normalized.length > 18
    ? `${normalized.slice(0, 10)}…${normalized.slice(-8)}`
    : normalized;
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

function matterTone(status: LegalGovernanceMatter["status"]): Tone {
  if (["resolved", "closed"].includes(status)) return "good";
  if (status === "blocked") return "bad";
  if (status === "under_review") return "warn";
  return "neutral";
}

function severityTone(severity: LegalGovernanceMatter["severity"]): Tone {
  if (["critical", "high"].includes(severity)) return "bad";
  if (severity === "medium") return "warn";
  return severity === "low" ? "info" : "neutral";
}

function reviewTone(status: LegalAnalysisMetadata["humanReviewStatus"]): Tone {
  return status === "approved" ? "good" : status === "rejected" ? "bad" : "warn";
}

function opinionTone(status: CounselOpinionMetadata["status"]): Tone {
  return status === "approved"
    ? "good"
    : ["withdrawn", "superseded"].includes(status)
      ? "neutral"
      : "warn";
}

function waiverTone(item: LegalGovernanceWaiver): Tone {
  if (item.status === "approved" && new Date(item.validUntil) > new Date()) {
    return "good";
  }
  if (["rejected", "expired", "revoked"].includes(item.status)) return "bad";
  if (["cured", "superseded"].includes(item.status)) return "neutral";
  return "warn";
}

const matterStatusLabel = (value: LegalGovernanceMatter["status"]) =>
  ({
    open: "열림",
    under_review: "검토 중",
    blocked: "차단",
    resolved: "해결",
    waived: "Waiver 적용",
    closed: "종결",
    superseded: "대체됨",
  })[value];

const severityLabel = (value: LegalGovernanceMatter["severity"]) =>
  ({
    info: "참고",
    low: "낮음",
    medium: "중간",
    high: "높음",
    critical: "중대",
  })[value];

const matterTypeLabel = (value: LegalGovernanceMatter["matterType"]) =>
  ({
    statutory: "법령",
    permit: "인허가",
    land_title: "토지 권리",
    contract: "계약",
    security: "담보",
    trust: "신탁",
    dispute: "분쟁",
    compliance: "컴플라이언스",
    other: "기타",
  })[value];

const reviewLabel = (value: LegalAnalysisMetadata["humanReviewStatus"]) =>
  ({ pending: "대기", approved: "승인", rejected: "반려" })[value];

const analysisKindLabel = (value: LegalAnalysisMetadata["analysisKind"]) =>
  ({
    ai_summary: "AI 요약",
    deterministic_analysis: "결정론적 분석",
    counsel_summary: "법률의견 요약",
  })[value];

const opinionStatusLabel = (value: CounselOpinionMetadata["status"]) =>
  ({
    draft: "초안",
    issued: "발행",
    approved: "독립 승인 완료",
    withdrawn: "철회",
    superseded: "대체됨",
  })[value];

const waiverStatusLabel = (value: LegalGovernanceWaiver["status"]) =>
  ({
    proposed: "제안",
    approved: "승인",
    rejected: "반려",
    expired: "만료",
    cured: "Cure 완료",
    revoked: "취소",
    superseded: "대체됨",
  })[value];

const cureStatusLabel = (value: LegalGovernanceWaiver["cureStatus"]) =>
  ({
    not_started: "미착수",
    in_progress: "진행 중",
    evidence_submitted: "증빙 제출",
    verified: "검증 완료",
    failed: "실패",
    not_applicable: "해당 없음",
  })[value];

const annotationTypeLabel = (value: LegalGovernanceAnnotation["annotationType"]) =>
  ({
    issue: "이슈",
    interpretation: "해석",
    counsel_note: "법무 메모",
    cure_evidence: "Cure 근거",
    cross_reference: "교차참조",
  })[value];

const annotationStatusLabel = (value: LegalGovernanceAnnotation["status"]) =>
  ({ active: "활성", resolved: "해결", superseded: "대체됨" })[value];
