"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Database,
  FileSearch,
  Landmark,
  Loader2,
  MapPin,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import { displayText, fetchWorkspaceContext, type WorkspacePageContext } from "../../../../lib/workspace-context";
import { PermitHeightReview, type PermitHeightAnalysis } from "../../../../components/orchestration/permit-height-review";
import { LandLegalSummaryActions } from "../../../../components/orchestration/land-legal-summary-actions";

type Parcel = {
  sequence?: number;
  addressFragment?: string;
  rawLotNumber?: string;
  normalizedLotNumber?: string;
  landCategory?: string;
  areaSqm?: number;
  totalContractAmount?: number | null;
  contractGroupId?: string;
  workflowStatus?: string;
  paymentStatus?: string;
  paymentReviewVersion?: string;
  paymentSummary?: Record<string, unknown>;
  paymentSource?: { sheet?: string; rows?: number[] };
  registryEvidence?: { ownerMatchStatus?: string };
  acquisitionStatus?: string;
  ownershipTransferred?: boolean;
  trusted?: boolean;
  pnu?: string;
  resolvedPnu?: string;
  officialLandPricePerSqm?: number;
  officialLandPriceBaseYear?: string | number;
  pnuResolution?: {
    status?: string;
    lotVerification?: string;
    reason?: string;
    attemptedAddress?: string;
    resolvedAddress?: string;
    constructedPnu?: string;
  };
};

type LandEvidenceRecord = {
  evidenceId?: string;
  evidence_id?: string;
  evidenceType?: string;
  evidence_type?: string;
  pnu?: string;
  sourceName?: string;
  source_name?: string;
  baseYear?: number;
  base_year?: number;
  verificationStatus?: string;
  verification_status?: string;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function formatCount(value: unknown, unit = "건") {
  const n = Number(value ?? 0);
  return `${Number.isFinite(n) ? n.toLocaleString("ko-KR") : "0"}${unit}`;
}

function formatSqm(value: unknown) {
  const n = Number(value ?? 0);
  return n > 0 ? `${n.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}㎡` : "미확인";
}

function formatKrw(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? `${(value / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 8 })}억원` : "미확인";
}

function sourceLabel(value: unknown) {
  const field = asRecord(value);
  const locator = asRecord(field.sourceLocator);
  const provenance = asRecord(field.provenance);
  const excel = asRecord(field.excel);
  return [locator.sourceName ?? provenance.source_document, locator.sheet ?? excel.sheet_name,
    locator.range ?? locator.cell ?? excel.cell].filter((part) => typeof part === "string" && part).join(" · ") || "출처 미확인";
}

function pct(value: number, total: number) {
  return total > 0 ? Math.round((value / total) * 100) : 0;
}

function paymentStatusLabel(parcel: Parcel) {
  if (parcel.paymentReviewVersion !== "ledger-recorded-payments-v1") return "지급 구분 재검토 필요";
  const labels: Record<string, string> = {
    fully_paid: "조서상 전액 기지급", deposit_paid: "조서상 일부 기지급", balance_due: "잔금 예정액 있음",
    interim_due: "중도금 예정액 있음", deposit_due: "계약금 미지급액 있음",
    payment_not_started: "조서상 기지급액 0", payment_unknown: "지급 상태 확인 필요", payment_needs_review: "지급 금액 대사 필요",
  };
  return labels[parcel.paymentStatus ?? ""] ?? "지급 상태 확인 필요";
}

export default function LandAnalysisPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewComment, setReviewComment] = useState("");
  const [reviewMessage, setReviewMessage] = useState<string | null>(null);
  const [legalPreview, setLegalPreview] = useState<{ projectId: string; previewHash: string; legalReview: Record<string, unknown> } | null>(null);
  const activeProject = useRef(projectId);
  const reviewDirty = useRef(false);

  useEffect(() => {
    activeProject.current = projectId; reviewDirty.current = false;
    setContext(null); setReviewComment(""); setReviewMessage(null); setRunning(null); setLegalPreview(null);
  }, [projectId]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchWorkspaceContext(projectId, "land");
      if (activeProject.current !== projectId) return;
      setContext(next);
      const comparison = asRecord(asRecord(next.pageData).landCostReconciliation);
      if (!reviewDirty.current) setReviewComment(comparison.reviewMatchesCurrentSources ? String(asRecord(comparison.review).comment ?? "") : "");
    } catch (err) {
      if (activeProject.current === projectId) setError(err instanceof Error ? err.message : "토지 저장 상태를 불러오지 못했습니다.");
    } finally {
      if (activeProject.current === projectId) setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const pageData = asRecord(context?.pageData);
  const land = asRecord(pageData.land);
  const landLedger = asRecord(pageData.landLedger);
  const preview = legalPreview?.projectId === projectId ? legalPreview : null;
  const legalReview = asRecord(preview?.legalReview);
  const legalAnalysis = asRecord(legalReview.developabilityAnalysis) as PermitHeightAnalysis;
  const legalIntelligence = asRecord(legalReview.legalIntelligence);
  const ledgerSummary = asRecord(pageData.landLedgerSummary);
  const ledgerInnerSummary = asRecord(landLedger.summary);
  const summary = { ...ledgerSummary, ...ledgerInnerSummary };
  const pnuValidation = asRecord(pageData.pnuValidation);
  const landEvidenceSummary = asRecord(pageData.landEvidenceSummary);
  const evidenceRecords = asArray<LandEvidenceRecord>(pageData.landEvidenceRecords);
  const parcels = asArray<Parcel>(landLedger.parcels);
  const contractGroups = asArray<Record<string, unknown>>(landLedger.contractGroups);
  const reconciliation = asRecord(pageData.landCostReconciliation);
  const contract = asRecord(reconciliation.ledgerContract);
  const purchaseBudget = asRecord(reconciliation.purchaseBudget);
  const inclusiveBudget = asRecord(reconciliation.inclusiveBudget);
  const savedReview = asRecord(reconciliation.review);

  async function saveReview() {
    if (!reviewComment.trim() || running) return;
    setRunning("review"); setReviewMessage(null);
    try {
      const response = await fetch(`${apiBase}/projects/${projectId}/land-cost-review`, {
        method: "PUT", headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ comment: reviewComment, expectedComparisonHash: reconciliation.comparisonHash }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : `검토 메모 저장 실패 (${response.status})`);
      }
      const body = await response.json();
      if (activeProject.current !== projectId) return;
      reviewDirty.current = false;
      setReviewComment(String(body.review.comment));
      setContext((previous) => previous ? { ...previous, pageData: { ...previous.pageData,
        landCostReconciliation: { ...reconciliation, review: body.review, reviewMatchesCurrentSources: true },
      } } : previous);
      setReviewMessage("검토 메모를 저장했습니다.");
    } catch (err) {
      if (activeProject.current === projectId) setReviewMessage(err instanceof Error ? err.message : "검토 메모 저장에 실패했습니다.");
    } finally {
      if (activeProject.current === projectId) setRunning(null);
    }
  }

  const parcelCount = Number(summary.parcelCount ?? pnuValidation.parcelCount ?? parcels.length ?? 0);
  const pnuCount = Number(pnuValidation.pnuCount ?? summary.pnuCount ?? parcels.filter((p) => p.pnu || p.resolvedPnu).length);
  const missingPnuCount = Math.max(0, parcelCount - pnuCount);
  const evidenceCount = Number(landEvidenceSummary.count ?? evidenceRecords.length ?? 0);
  const landEvidenceUnavailable = context?.unavailableSections?.includes("landEvidence");
  const hasOfficialLandPrice = Boolean(landEvidenceSummary.hasOfficialLandPrice);
  const hasRegistryEvidence = Boolean(landEvidenceSummary.hasRegistryEvidence);
  const hasPnuResolution = Boolean(landEvidenceSummary.hasPnuResolution) || pnuCount > 0;
  const acquiredCount = parcels.filter((parcel) => parcel.registryEvidence?.ownerMatchStatus === "matched").length;
  const acquisitionRatio = parcelCount > 0 ? acquiredCount / parcelCount : 0;

  const projectName = displayText(context?.projectMemory?.projectName, `프로젝트 ${projectId}`);
  const nextAction = missingPnuCount > 0
    ? "토지조서 지번을 기준으로 PNU 일괄 해석을 먼저 실행하세요."
    : parcelCount === 0
      ? "자료 입력에서 토지조서를 업로드하세요."
      : landEvidenceUnavailable
        ? "필지별 인허가 근거는 토지·인허가 검토에서 확인하세요."
        : !hasOfficialLandPrice
          ? "PNU는 확보되었으나 공시지가 증빙이 부족합니다."
          : !hasRegistryEvidence
            ? "등기부 및 제한물권 증빙을 추가해 법무·PF 검토에 연결하세요."
            : "토지 증빙을 필지별로 확인하고 인허가 검토를 실행하세요.";

  async function runResolvePnu() {
    setRunning("pnu");
    setError(null);
    setMessage(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/land/parcels/resolve`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ persistEvidence: true, limit: 500 }),
      });
      if (!res.ok) throw new Error(`PNU 일괄 해석 실패 (${res.status})`);
      const body = await res.json();
      if (activeProject.current !== projectId) return;
      setLegalPreview(null);
      const resolved = body?.summary?.resolvedCount ?? body?.resolvedCount ?? 0;
      const unresolved = body?.summary?.unresolvedCount;
      setMessage(`PNU 일괄 해석을 마쳤습니다. 새로 해석 ${resolved}건${typeof unresolved === "number" && unresolved > 0 ? ` · 재확인 ${unresolved}건` : ""}`);
      await load();
    } catch (err) {
      if (activeProject.current === projectId) setError(err instanceof Error ? err.message : "PNU 일괄 해석에 실패했습니다.");
    } finally {
      if (activeProject.current === projectId) setRunning(null);
    }
  }

  async function runLandPod() {
    setRunning("pod");
    setError(null);
    setMessage(null);
    setLegalPreview(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/legal-pod/preview`, {
        method: "POST",
        cache: "no-store",
        headers: authHeaders("web"),
      });
      if (!res.ok) throw new Error(`토지·인허가 검토 실패 (${res.status})`);
      const result = await res.json();
      if (activeProject.current !== projectId) return;
      if (result.previewOnly !== true || typeof result.previewHash !== "string") throw new Error("미리보기 결과를 불러오지 못했습니다.");
      setLegalPreview({ projectId, previewHash: result.previewHash, legalReview: asRecord(result.legalReview) });
    } catch (err) {
      if (activeProject.current === projectId) setError(err instanceof Error ? err.message : "토지·인허가 검토에 실패했습니다.");
    } finally {
      if (activeProject.current === projectId) setRunning(null);
    }
  }

  const sampleParcels = useMemo(() => parcels.slice(0, 120), [parcels]);

  return (
    <div className="space-y-6 pb-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <Link
            href={`/project/${projectId}`}
            className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
            aria-label="프로젝트로 돌아가기"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <p className="text-sm font-semibold text-slate-500">{projectName}</p>
            <h1 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
              <Landmark className="h-7 w-7 text-indigo-600" />
              토지 원장 및 PNU 검증
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              이 화면은 Shared Deal State Graph에서 같은 프로젝트명, 업로드 파일, 토지조서, PNU, 공시지가, 등기 증빙을 읽습니다.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => void runResolvePnu()} disabled={running !== null || loading} className="inline-flex h-10 items-center gap-2 rounded-lg bg-indigo-600 px-4 text-sm font-bold text-white disabled:opacity-50">
            {running === "pnu" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
            PNU 일괄 해석
          </button>
          <button type="button" onClick={() => void runLandPod()} disabled={running !== null || loading} className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-bold text-slate-700 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200">
            {running === "pod" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            토지·인허가 미리보기
          </button>
        </div>
      </header>

      {loading ? (
        <div className="flex min-h-[240px] items-center justify-center rounded-lg border border-slate-200 bg-white text-sm font-bold text-slate-500 dark:border-slate-800 dark:bg-slate-900">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          토지 원장을 불러오는 중입니다.
        </div>
      ) : (
        <>
          {error ? <Notice tone="bad">{error}</Notice> : null}
          {message ? <Notice tone="good">{message}</Notice> : null}

          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
            <StatusCard label="토지조서 필지" value={formatCount(parcelCount, "필지")} tone={parcelCount > 0 ? "good" : "warn"} icon={<FileSearch />} />
            <StatusCard label="PNU 확보" value={`${pnuCount}/${parcelCount || 0} (${pct(pnuCount, parcelCount)}%)`} tone={missingPnuCount === 0 && parcelCount > 0 ? "good" : "warn"} icon={<MapPin />} />
            <StatusCard label="공시지가 증빙" value={landEvidenceUnavailable ? "조회 불가" : hasOfficialLandPrice ? "확보" : "보강 필요"} tone={hasOfficialLandPrice ? "good" : "warn"} icon={<Database />} />
            <StatusCard label="등기/제한물권 증빙" value={landEvidenceUnavailable ? "조회 불가" : hasRegistryEvidence ? "확보" : "보강 필요"} tone={hasRegistryEvidence ? "good" : "warn"} icon={<ShieldCheck />} />
            <StatusCard label="등기 소유자 일치 필지" value={`${Math.round(acquisitionRatio * 100)}%`} tone={acquisitionRatio >= 0.8 ? "good" : "warn"} icon={<CheckCircle2 />} />
          </section>

          <section className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm font-semibold text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <p className="font-black">다음 조치</p>
                <p className="mt-1">{nextAction}</p>
              </div>
            </div>
          </section>

          <section className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
            <Panel title="토지 기본 정보">
              <InfoRow label="주소" value={displayText(land.address, "미확인")} />
              <InfoRow label="대표 PNU" value={displayText(land.pnu, hasPnuResolution ? "증빙 레코드에서 확인" : "미확인")} />
              <InfoRow label="대지면적" value={formatSqm(land.area_sqm ?? summary.totalAreaSqm)} />
              <InfoRow label="토지 매입금액" value={formatKrw(summary.totalContractAmount)} />
            </Panel>
            <Panel title="저장된 증빙">
              <InfoRow label="토지 증빙 레코드" value={landEvidenceUnavailable ? "조회 불가" : formatCount(evidenceCount)} />
              <InfoRow label="PNU 해석 증빙" value={hasPnuResolution ? "있음" : "없음"} />
              <InfoRow label="공시지가 증빙" value={landEvidenceUnavailable ? "조회 불가" : hasOfficialLandPrice ? "있음" : "없음"} />
              <InfoRow label="등기/제한물권 증빙" value={landEvidenceUnavailable ? "조회 불가" : hasRegistryEvidence ? "있음" : "없음"} />
            </Panel>
          </section>

          <Panel title="토지·인허가 검토">
            {preview ? (
              <>
                <p className="mb-3 text-sm text-slate-500">화면 전용 예비 검토입니다. 새로고침하면 사라집니다.</p>
                {typeof legalIntelligence.verifiedReferenceCount === "number" ? (
                  <p className="mb-3 text-sm font-semibold text-slate-600 dark:text-slate-300">
                    {legalIntelligence.verifiedReferenceCount > 0 ? `검증된 법령·조례 근거 ${legalIntelligence.verifiedReferenceCount}건` : "검증된 법령·조례 원문 근거 미확보"}
                  </p>
                ) : null}
                <PermitHeightReview analysis={legalAnalysis} />
              </>
            ) : (
              <p className="text-sm text-slate-500">미리보기를 실행하면 계획 용도에 따른 인허가 경로와 필지별 높이 근거를 확인할 수 있습니다.</p>
            )}
            <LandLegalSummaryActions key={`${projectId}:${preview?.previewHash ?? ""}`} projectId={projectId} previewHash={preview?.previewHash} analysis={legalAnalysis} />
            <Link href={`/project/${projectId}/input-package`} className="mt-3 inline-block text-sm font-semibold text-indigo-700 underline dark:text-indigo-300">토지이용계획·인허가 자료 입력</Link>
          </Panel>

          <Panel title="계약·수지 금액 대사">
            <p className="mb-3 text-sm text-slate-500">저장된 원본 금액을 비교합니다. 차액의 원인은 별도 확인이 필요하며, 계산 입력이나 승인 상태는 변경되지 않습니다.</p>
            <InfoRow label="토지조서 계약 합계" value={formatKrw(contract.value)} />
            <p className="break-words text-xs text-slate-500">{sourceLabel(contract)}</p>
            <InfoRow label="수지 순수 매입예산" value={formatKrw(purchaseBudget.value)} />
            <p className="break-words text-xs text-slate-500">{sourceLabel(purchaseBudget)}</p>
            <InfoRow label="수지 부대비 포함 대지비" value={formatKrw(inclusiveBudget.value)} />
            <p className="break-words text-xs text-slate-500">{sourceLabel(inclusiveBudget)}</p>
            <InfoRow label="수지 부대비 (포함 금액 − 순수 매입예산)" value={formatKrw(reconciliation.ancillaryBudgetKrw)} />
            <InfoRow label="차액 (순수 매입예산 − 계약 합계)" value={formatKrw(reconciliation.purchaseMinusContractKrw)} />
            {typeof reconciliation.individualAmountMissingCount === "number" && reconciliation.individualAmountMissingCount > 0 ? (
              <p className="mt-3 text-sm text-amber-700 dark:text-amber-300">개인별 계약금액 {reconciliation.individualAmountMissingCount}건이 원본에 미기재되어 있습니다. 합계 불일치와 구분하여 보완하세요.</p>
            ) : null}
            {reconciliation.studyStatus === "candidate" ? <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">수지 검토 초안의 원본값입니다. 승인된 계산 입력을 뜻하지 않습니다.</p> : null}
            {reconciliation.workbookValidationStatus === "failed" ? <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">수지 원본의 수식·검증 오류가 남아 있습니다.</p> : null}
            {reconciliation.requiresLedgerReimport ? <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">기존 토지조서는 공동계약 구분을 위해 다시 가져와야 합니다.</p> : null}
            {reconciliation.status === "incomplete" ? <p className="mt-2 text-sm text-slate-500">비교할 원본 금액이 부족합니다. 토지조서와 사업수지를 가져와 저장하세요.</p> : null}
            <Link className="mt-3 inline-block text-sm font-semibold text-indigo-600 underline" href={`/project/${projectId}/input-package`}>자료 입력 및 원본 확인</Link>
            {savedReview.comment && !reconciliation.reviewMatchesCurrentSources ? (
              <p className="mt-3 whitespace-pre-wrap text-sm text-amber-700 dark:text-amber-300">이전 원본의 검토 메모입니다. 현재 자료와 다시 대조하세요.{"\n"}{String(savedReview.comment)}</p>
            ) : null}
            <label className="mt-4 block text-sm font-semibold" htmlFor="land-cost-review">검토 이유와 다음 확인 사항</label>
            <textarea id="land-cost-review" aria-label="토지비 검토 메모" value={reviewComment}
              onChange={(event) => { reviewDirty.current = true; setReviewComment(event.target.value); }} maxLength={5000} rows={3} disabled={running !== null}
              className="mt-2 w-full rounded-lg border border-slate-300 bg-transparent p-3 text-sm dark:border-slate-600" />
            <button type="button" onClick={() => void saveReview()} disabled={running !== null || !reviewComment.trim() || typeof reconciliation.comparisonHash !== "string" || contract.value == null}
              className="mt-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">{running === "review" ? "저장 중…" : "검토 메모 저장"}</button>
            {reviewMessage ? <p role="status" className="mt-2 text-sm">{reviewMessage}</p> : null}
            {savedReview.reviewedAt ? <p className="mt-2 text-xs text-slate-500">저장된 메모 {asArray(savedReview.reviewHistory).length}건 · 마지막 저장 {String(savedReview.reviewedAt)}</p> : null}
          </Panel>

          {contractGroups.length ? (
            <Panel title="공동계약">
              {contractGroups.map((group, index) => (
                <div key={String(group.contractGroupId)} className="mb-3 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
                  <p className="font-bold">공동계약 {index + 1}: {formatKrw(group.totalContractAmount)}</p>
                  <p className="mt-1 text-sm">필지 번호 {asArray<number>(group.parcelSequences).join(", ")} · 필지별 금액 미배분</p>
                  <p className="mt-1 break-words text-xs text-slate-500">{sourceLabel(group)}</p>
                  <p className="mt-1 text-sm text-slate-500">전체 계약 합계에 한 번만 반영했습니다. 법적 소유지분과 필지별 가격은 별도 확인이 필요합니다.</p>
                </div>
              ))}
            </Panel>
          ) : null}

          <Panel title="조서상 지급 내역">
            <p className="mb-3 text-sm text-slate-500">기지급으로 기재된 금액과 미지급 계약금·중도금·잔금 예정액을 구분합니다. 조서의 기재는 이체 증빙이나 소유권 이전 확인을 대신하지 않습니다. 공란은 0으로 확정하지 않습니다.</p>
            {sampleParcels.map((parcel, index) => {
              const current = parcel.paymentReviewVersion === "ledger-recorded-payments-v1";
              const payment = asRecord(parcel.paymentSummary);
              return <div key={parcel.sequence ?? index} className="mb-3 rounded-xl border border-slate-200 p-3 dark:border-slate-800">
                <p className="font-bold">{displayText(parcel.addressFragment, `필지 ${index + 1}`)} · {paymentStatusLabel(parcel)}</p>
                {current ? <>
                  <InfoRow label="기지급 기재액 소계" value={formatKrw(payment.knownRecordedPaidAmount)} />
                  <InfoRow label="미지급·예정 기재액 소계" value={formatKrw(payment.knownScheduledAmount)} />
                  <p className="text-xs text-slate-500">{parcel.paymentSource?.sheet ?? "원본 시트 미확인"} · 원본 행 {parcel.paymentSource?.rows?.join(", ") || "미확인"}</p>
                  {Number(payment.recordedPaidMissingCount) > 0 || Number(payment.scheduledMissingCount) > 0 ? <p className="mt-1 text-sm text-amber-700 dark:text-amber-300">미기재: 기지급 {formatCount(payment.recordedPaidMissingCount)} · 미지급·예정 {formatCount(payment.scheduledMissingCount)}. 기재된 금액만 합산했습니다.</p> : null}
                </> : <p className="mt-1 text-sm text-amber-700 dark:text-amber-300">원본 토지조서를 다시 가져와 지급 구분을 확인하세요. 이전 지급 완료 표시는 사용하지 않습니다.</p>}
              </div>;
            })}
            {!sampleParcels.length ? <p className="text-sm text-slate-500">토지조서를 가져오면 지급 기재액을 확인할 수 있습니다.</p> : null}
          </Panel>

          <Panel title="필지별 PNU 검증 현황">
            {sampleParcels.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[860px] text-left text-sm">
                  <thead className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-800">
                    <tr>
                      <th className="px-3 py-2">필지</th>
                      <th className="px-3 py-2">지목</th>
                      <th className="px-3 py-2">면적</th>
                      <th className="px-3 py-2">매입금액</th>
                      <th className="px-3 py-2">PNU</th>
                      <th className="px-3 py-2">공시지가(원/㎡)</th>
                      <th className="px-3 py-2">상태</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {sampleParcels.map((parcel, index) => {
                      const pnu = parcel.pnuResolution?.status === "unresolved" ? undefined : parcel.pnu || parcel.resolvedPnu;
                      return (
                        <tr key={`${parcel.sequence ?? index}-${parcel.addressFragment ?? parcel.rawLotNumber ?? index}`}>
                          <td className="px-3 py-3 font-bold text-slate-950 dark:text-white">
                            {displayText(parcel.addressFragment ?? parcel.normalizedLotNumber ?? parcel.rawLotNumber, `필지 ${index + 1}`)}
                          </td>
                          <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{displayText(parcel.landCategory, "미확인")}</td>
                          <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{formatSqm(parcel.areaSqm)}</td>
                          <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{parcel.contractGroupId ? "공동계약 · 필지별 미배분" : formatKrw(parcel.totalContractAmount)}</td>
                          <td className="px-3 py-3 font-mono text-xs text-slate-600 dark:text-slate-300">{pnu || "미해석"}</td>
                          <td className="px-3 py-3 text-slate-600 dark:text-slate-300">
                            {typeof parcel.officialLandPricePerSqm === "number"
                              ? `${parcel.officialLandPricePerSqm.toLocaleString("ko-KR")}${parcel.officialLandPriceBaseYear ? ` (${parcel.officialLandPriceBaseYear})` : ""}`
                              : "—"}
                          </td>
                          <td className="px-3 py-3">
                            {(() => {
                              // 지번 대조 가드 결과까지 반영: 검증됨 / 지번 미대조 / lot_mismatch 거부 / 미해석
                              const res = parcel.pnuResolution;
                              if (pnu && res?.lotVerification === "verified") {
                                return <span className="rounded-md bg-emerald-50 px-2 py-1 text-xs font-bold text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200">PNU 확보 · 지번 검증됨</span>;
                              }
                              if (pnu) {
                                return <span className="rounded-md bg-emerald-50 px-2 py-1 text-xs font-bold text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200" title={res?.lotVerification === "unverified" ? "해석은 됐으나 지번 문자열 대조가 불가한 케이스" : undefined}>PNU 확보{res?.lotVerification === "unverified" ? " · 지번 미대조" : ""}</span>;
                              }
                              if (res?.status === "unresolved" && (res.reason || "").includes("lot_mismatch")) {
                                return <span className="rounded-md bg-rose-50 px-2 py-1 text-xs font-bold text-rose-700 dark:bg-rose-950/30 dark:text-rose-200" title={`시도 주소: ${res.attemptedAddress ?? "-"} — 해석된 지번이 달라 오필지 PNU 부착을 차단했습니다`}>지번 불일치 거부</span>;
                              }
                              if (res?.status === "unresolved" && res.reason === "not_in_current_cadastre") {
                                return <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-600 dark:bg-slate-800 dark:text-slate-300" title={`구성 PNU ${res.constructedPnu ?? "-"} — 현 지적도에 없는 지번(재개발 합병·재번지 추정). 옛 토지조서 지번일 수 있어 대사가 필요합니다`}>현 지적도에 없음</span>;
                              }
                              if (res?.reason === "conflicting_existing_pnu") {
                                return <span className="rounded-md bg-rose-50 px-2 py-1 text-xs font-bold text-rose-700 dark:bg-rose-950/30 dark:text-rose-200">기존 PNU 충돌 · 지번 대사 필요</span>;
                              }
                              if (res?.status === "unresolved") {
                                return <span className="rounded-md bg-amber-50 px-2 py-1 text-xs font-bold text-amber-700 dark:bg-amber-950/30 dark:text-amber-200" title={res.reason ?? undefined}>해석 실패</span>;
                              }
                              return <span className="rounded-md bg-amber-50 px-2 py-1 text-xs font-bold text-amber-700 dark:bg-amber-950/30 dark:text-amber-200">PNU 보강 필요</span>;
                            })()}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty text="아직 토지조서 필지 목록이 저장되어 있지 않습니다. 자료 입력 화면에서 토지조서 Excel을 업로드한 뒤 분류대로 처리하세요." />
            )}
          </Panel>

          <Panel title="증빙 레코드">
            {evidenceRecords.length ? (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {evidenceRecords.slice(0, 12).map((record, index) => (
                  <div key={`${record.evidenceId ?? record.evidence_id ?? index}`} className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
                    <p className="text-xs font-bold text-slate-500">{displayText(record.evidenceType ?? record.evidence_type, "land_evidence")}</p>
                    <p className="mt-1 break-all text-sm font-black text-slate-950 dark:text-white">{displayText(record.pnu, "PNU 미확인")}</p>
                    <p className="mt-2 text-xs text-slate-500">출처: {displayText(record.sourceName ?? record.source_name, "미확인")}</p>
                    <p className="mt-1 text-xs text-slate-500">기준연도: {record.baseYear ?? record.base_year ?? "미확인"}</p>
                  </div>
                ))}
              </div>
            ) : (
              <Empty text="저장된 토지 증빙이 없습니다. PNU 일괄 해석 또는 공시지가/등기 증빙 업로드가 필요합니다." />
            )}
          </Panel>
        </>
      )}
    </div>
  );
}

function StatusCard({ icon, label, value, tone }: { icon: ReactNode; label: string; value: string; tone: "good" | "warn" }) {
  return (
    <div className={`rounded-lg border p-4 shadow-sm ${tone === "good" ? "border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/30" : "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/30"}`}>
      <div className="flex items-center gap-2 text-sm font-bold text-slate-700 dark:text-slate-200">
        <span className="h-5 w-5">{icon}</span>
        {label}
      </div>
      <p className="mt-3 text-2xl font-black text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-4 flex items-center gap-2">
        <CheckCircle2 className="h-5 w-5 text-slate-700 dark:text-slate-200" />
        <h2 className="text-lg font-black text-slate-950 dark:text-white">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-slate-100 py-3 text-sm last:border-0 dark:border-slate-800">
      <span className="font-semibold text-slate-500">{label}</span>
      <span className="text-right font-black text-slate-950 dark:text-white">{value}</span>
    </div>
  );
}

function Notice({ tone, children }: { tone: "good" | "bad"; children: ReactNode }) {
  const klass = tone === "good"
    ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200"
    : "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200";
  return <div className={`rounded-lg border p-4 text-sm font-bold ${klass}`}>{children}</div>;
}

function Empty({ text }: { text: string }) {
  return <div className="rounded-lg bg-slate-50 p-5 text-sm text-slate-500 dark:bg-slate-800/70">{text}</div>;
}
