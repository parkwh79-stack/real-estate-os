"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
import {
  ChevronDown,
  ChevronRight,
  Database,
  FileSearch,
  Landmark,
  Loader2,
  MapPin,
  Plus,
  RefreshCw,
  X,
} from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import { projectWorkspaceHref } from "../../../../lib/project-navigation";
import { displayText, fetchWorkspaceContext, type WorkspacePageContext } from "../../../../lib/workspace-context";
import { isWorkflowTrack } from "../../../../lib/workflow-track";
import { PermitHeightReview, type PermitHeightAnalysis } from "../../../../components/orchestration/permit-height-review";
import { LandLegalSummaryActions } from "../../../../components/orchestration/land-legal-summary-actions";

type Parcel = {
  sequence?: number;
  addressFragment?: string;
  rawLotNumber?: string;
  lotNumber?: string;
  normalizedLotNumber?: string;
  mainLotNo?: string | number;
  main_lot_no?: string | number;
  subLotNo?: string | number;
  sub_lot_no?: string | number;
  isMountain?: boolean;
  is_mountain?: boolean;
  landCategory?: string;
  areaSqm?: number;
  totalContractAmount?: number | null;
  contractGroupId?: string;
  paymentStatus?: string;
  paymentReviewVersion?: string;
  paymentSummary?: Record<string, unknown>;
  paymentSource?: { sheet?: string; rows?: number[] };
  registryEvidence?: { ownerMatchStatus?: string };
  pnu?: string;
  resolvedPnu?: string;
  PNU?: string;
  pnuMatchMethod?: string;
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

type PreviewParcelRow = {
  parcelIndex: number;
  parcelLabel?: string;
  address?: string;
  pnu?: string | null;
  status: "confirmed" | "ambiguous" | "not_queried" | "failed";
  landUseStatus: "confirmed" | "not_queried" | "failed";
  zoning?: string | null;
  districtUnitPlan?: string | null;
  landUseSourceLocator?: { sourceName?: string; sourceUrl?: string; sourceType?: string } | null;
};

type PreviewParcelBatch = {
  parcelRevision: string;
  totalParcels: number;
  nextOffset: number | null;
  rows: PreviewParcelRow[];
  jurisdictions?: Array<{
    jurisdictionCode: string;
    jurisdictionName?: string;
    zoning?: string | string[];
    parcelIndexes: number[];
    permitRoutes?: Array<{ label?: string }>;
    ordinanceReferences?: Array<{ title?: string; lawName?: string; citation?: string; article?: string; effectiveDate?: string; url?: string }>;
  }>;
};

type PreviewRequest = { parcelOffset?: number; parcelRevision?: string; parcelIndexes?: number[] };
type PreviewBatchResult = {
  previewHash: string;
  legalReview: Record<string, unknown>;
  request: PreviewRequest;
  parcelBatch?: PreviewParcelBatch;
};
type PreviewSession = {
  projectId: string;
  batches: PreviewBatchResult[];
  active: number;
  rows: Record<number, PreviewParcelRow>;
  nextOffset: number | null;
};

const PNU_PREVIEW_LABEL = {
  confirmed: "PNU 확인",
  ambiguous: "후보 확인 필요",
  not_queried: "미조회",
  failed: "조회 실패",
};
const LAND_USE_PREVIEW_LABEL = {
  confirmed: "토지이용 근거 확인",
  not_queried: "토지이용 미조회",
  failed: "토지이용 조회 실패",
};

function mergeParcelPreview(projectId: string, previous: PreviewSession | null, result: PreviewBatchResult): PreviewSession {
  const batch = result.parcelBatch;
  const continuing = Boolean(result.request.parcelRevision) && previous?.projectId === projectId;
  const batches = continuing ? [...previous.batches] : [];
  const requestKey = JSON.stringify(result.request.parcelIndexes ?? result.request.parcelOffset ?? 0);
  const position = batches.findIndex(({ request }) => JSON.stringify(request.parcelIndexes ?? request.parcelOffset ?? 0) === requestKey);
  const active = position < 0 ? batches.length : position;
  batches[active] = result;
  const rows = continuing ? { ...previous.rows } : {};
  for (const row of batch?.rows ?? []) rows[row.parcelIndex] = row;
  return {
    projectId, batches, active, rows,
    nextOffset: continuing && (result.request.parcelIndexes || position >= 0) ? previous.nextOffset : batch?.nextOffset ?? null,
  };
}

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
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function confirmedCount(...values: unknown[]) {
  return values.find((value): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0) ?? null;
}

function formatCount(value: unknown, unit = "건") {
  const count = confirmedCount(value);
  return count === null ? "미확인" : `${count.toLocaleString("ko-KR")}${unit}`;
}

function formatSqm(value: unknown) {
  const n = Number(value ?? 0);
  return Number.isFinite(n) && n > 0 ? `${n.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}㎡` : "미확인";
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

function parcelPnu(parcel: Parcel) {
  if (parcel.pnuResolution?.status === "unresolved" || parcel.pnuResolution?.reason === "conflicting_existing_pnu"
    || parcel.pnuMatchMethod === "legal_dong_code_and_normalized_lot") return null;
  const candidates = new Set([parcel.pnu, parcel.resolvedPnu, parcel.PNU]
    .filter((value): value is string => typeof value === "string").map((value) => value.trim()).filter(Boolean));
  const pnu = candidates.values().next().value;
  if (candidates.size !== 1 || !pnu || !/^[0-9]{10}[12][0-9]{8}$/.test(pnu)) return null;
  const mainLot = parcel.mainLotNo ?? parcel.main_lot_no;
  const subLot = parcel.subLotNo ?? parcel.sub_lot_no;
  const mountain = parcel.isMountain === true || parcel.is_mountain === true;
  const lot = parcel.normalizedLotNumber || parcel.lotNumber || parcel.rawLotNumber ||
    (mainLot == null || mainLot === "" ? "" : `${mountain ? "산" : ""}${mainLot}${subLot ? `-${subLot}` : ""}`);
  const match = String(lot).match(/(산)?\s*([0-9]+)(?:-([0-9]+))?/);
  if (match && (Boolean(match[1]) !== (pnu[10] === "2") ||
    Number(match[2]) !== Number(pnu.slice(11, 15)) ||
    Number(match[3] ?? 0) !== Number(pnu.slice(15)))) return null;
  return pnu;
}

function parcelPnuStatus(parcel: Parcel, pnu: string | null) {
  const resolution = parcel.pnuResolution;
  if (resolution?.reason === "conflicting_existing_pnu") return { label: "기존 PNU 충돌", tone: "text-re-danger" };
  if (resolution?.status === "unresolved") {
    if (resolution.reason?.includes("lot_mismatch")) return { label: "지번 불일치", tone: "text-re-danger" };
    if (resolution.reason === "not_in_current_cadastre") return { label: "현 지적도에 없음", tone: "text-re-secondary" };
    return { label: "해석 실패", tone: "text-re-warning" };
  }
  if (pnu && resolution?.lotVerification === "verified") return { label: "PNU 확보 · 지번 검증됨", tone: "text-re-success" };
  if (pnu && resolution?.lotVerification === "unverified") return { label: "PNU 확보 · 지번 미대조", tone: "text-re-secondary" };
  return { label: pnu ? "PNU 확보" : "PNU 확인 필요", tone: "text-re-secondary" };
}

function parcelName(parcel: Parcel, index: number) {
  return displayText(parcel.addressFragment ?? parcel.normalizedLotNumber ?? parcel.rawLotNumber, `필지 ${index + 1}`);
}

function parcelContractAmount(parcel: Parcel) {
  return parcel.contractGroupId ? "공동계약 · 필지별 미배분" : formatKrw(parcel.totalContractAmount);
}

function ownerComparison(parcel: Parcel) {
  const status = parcel.registryEvidence?.ownerMatchStatus;
  if (status === "matched") return "일치";
  if (status === "mismatch" || status === "unmatched" || status === "not_matched") return "불일치";
  return "미확인";
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
  const queryTrack = useSearchParams().get("track");
  const explicitTrack = isWorkflowTrack(queryTrack) ? queryTrack : undefined;
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewComment, setReviewComment] = useState("");
  const [reviewMessage, setReviewMessage] = useState<string | null>(null);
  const [legalPreview, setLegalPreview] = useState<PreviewSession | null>(null);
  const [visibleParcelLimit, setVisibleParcelLimit] = useState(120);
  const pendingPreview = useRef<AbortController | null>(null);
  const [selectedParcelKey, setSelectedParcelKey] = useState<string | null>(null);
  const activeProject = useRef(projectId);
  const reviewDirty = useRef(false);
  const loadVersion = useRef(0);
  const parcelOpener = useRef<HTMLButtonElement | null>(null);
  const previewHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    activeProject.current = projectId;
    reviewDirty.current = false;
    setContext(null);
    setReviewComment("");
    setReviewMessage(null);
    setRunning(null);
    setMessage(null);
    setError(null);
    setLegalPreview(null);
    setSelectedParcelKey(null);
    setVisibleParcelLimit(120);
    return () => pendingPreview.current?.abort();
  }, [projectId]);

  const load = useCallback(async () => {
    const version = ++loadVersion.current;
    setLoading(true);
    setLoadError(null);
    try {
      const next = await fetchWorkspaceContext(projectId, "land", explicitTrack);
      if (activeProject.current !== projectId || version !== loadVersion.current) return;
      setContext(next);
      const comparison = asRecord(asRecord(next.pageData).landCostReconciliation);
      if (!reviewDirty.current) setReviewComment(comparison.reviewMatchesCurrentSources ? String(asRecord(comparison.review).comment ?? "") : "");
    } catch (err) {
      if (activeProject.current === projectId && version === loadVersion.current) {
        setLoadError(err instanceof Error ? err.message : "토지 저장 상태를 불러오지 못했습니다.");
      }
    } finally {
      if (activeProject.current === projectId && version === loadVersion.current) setLoading(false);
    }
  }, [projectId, explicitTrack]);

  useEffect(() => {
    void load();
  }, [load]);

  const activeContext = context?.projectId === projectId ? context : null;
  const pageData = asRecord(activeContext?.pageData);
  const activeTrack = explicitTrack ?? (isWorkflowTrack(activeContext?.workflowTrack) ? activeContext.workflowTrack : undefined);
  const intakeUrl = `${projectWorkspaceHref(projectId, "input-package", activeTrack)}#intake-upload`;
  const mapUrl = projectWorkspaceHref(projectId, "map", activeTrack);
  const land = asRecord(pageData.land);
  const landLedger = asRecord(pageData.landLedger);
  const previewSession = legalPreview?.projectId === projectId ? legalPreview : null;
  const preview = previewSession?.batches[previewSession.active];
  const previewBatch = preview?.parcelBatch;
  const supersededBatch = Boolean(previewBatch?.rows.some((row) => previewSession?.rows[row.parcelIndex] !== row));
  const legalReview = asRecord(preview?.legalReview);
  const legalAnalysis = asRecord(legalReview.developabilityAnalysis) as PermitHeightAnalysis;
  const legalIntelligence = asRecord(legalReview.legalIntelligence);
  const ledgerSummary = asRecord(pageData.landLedgerSummary);
  const ledgerInnerSummary = asRecord(landLedger.summary);
  const summary = { ...ledgerSummary, ...ledgerInnerSummary };
  const pnuValidation = asRecord(pageData.pnuValidation);
  const landEvidenceSummary = asRecord(pageData.landEvidenceSummary);
  const evidenceRecords = asArray<LandEvidenceRecord>(pageData.landEvidenceRecords);
  const ledgerUnavailable = !activeContext || activeContext.unavailableSections?.includes("landLedger");
  const parcels = ledgerUnavailable ? [] : asArray<Parcel>(landLedger.parcels);
  const contractGroups = asArray<Record<string, unknown>>(landLedger.contractGroups);
  const reconciliation = asRecord(pageData.landCostReconciliation);
  const contract = asRecord(reconciliation.ledgerContract);
  const purchaseBudget = asRecord(reconciliation.purchaseBudget);
  const inclusiveBudget = asRecord(reconciliation.inclusiveBudget);
  const savedReview = asRecord(reconciliation.review);

  const hasPreview = Boolean(preview);
  useEffect(() => {
    if (hasPreview) previewHeading.current?.focus();
  }, [hasPreview]);

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

  const hasParcelList = Array.isArray(landLedger.parcels);
  const parcelCount = ledgerUnavailable ? null : confirmedCount(
    summary.parcelCount,
    pnuValidation.parcelCount,
    hasParcelList ? parcels.length : pageData.landLedger && !Object.keys(landLedger).length ? 0 : null,
  );
  const pnuCount = ledgerUnavailable ? null : confirmedCount(
    pnuValidation.pnuCount, summary.pnuCount, hasParcelList ? parcels.filter(parcelPnu).length : null,
  );
  const evidenceCount = confirmedCount(landEvidenceSummary.count, Array.isArray(pageData.landEvidenceRecords) ? evidenceRecords.length : null);
  const landEvidenceUnavailable = !activeContext || Boolean(activeContext.unavailableSections?.includes("landEvidence"));
  const hasOfficialLandPrice = landEvidenceSummary.hasOfficialLandPrice;
  const hasRegistryEvidence = landEvidenceSummary.hasRegistryEvidence;
  const missingPnuCount = parcelCount !== null && pnuCount !== null ? Math.max(0, parcelCount - pnuCount) : null;
  const ownerComparisons = parcels.map(ownerComparison);
  const matchedOwners = ownerComparisons.filter((status) => status === "일치").length;
  const comparedOwners = ownerComparisons.filter((status) => status !== "미확인").length;
  const ownerMatchLabel = comparedOwners > 0 ? `${matchedOwners}/${comparedOwners}필지 일치` : "— · 대조 전";
  const pnuLabel = parcelCount === 0 ? "— · 산정 전" : missingPnuCount === null ? "미확인" : `${pnuCount}/${parcelCount}필지`;
  const primaryAction = loadError || parcelCount === null || (parcelCount > 0 && pnuCount === null)
    ? "retry" : parcelCount === 0 ? "upload" : (missingPnuCount ?? 0) > 0 ? "pnu" : "preview";
  const nextAction = {
    retry: "저장된 필지와 PNU 상태를 다시 불러오세요.",
    upload: "토지조서를 추가하면 필지별 지번과 증빙을 검토할 수 있습니다.",
    pnu: `PNU를 확인할 필지가 ${formatCount(missingPnuCount, "필지")} 있습니다.`,
    preview: "필지별 증빙을 확인하고 용도와 인허가 조건을 미리 검토하세요.",
  }[primaryAction];

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
      setMessage(body?.vworld?.configured === false
        ? "VWorld 연결이 필요해 새 PNU를 조회하지 못했습니다. 이미 확인된 PNU는 계속 볼 수 있습니다."
        : `PNU 일괄 해석을 마쳤습니다. 새로 해석 ${resolved}건${typeof unresolved === "number" && unresolved > 0 ? ` · 재확인 ${unresolved}건` : ""}`);
      await load();
    } catch (err) {
      if (activeProject.current === projectId) setError(err instanceof Error ? err.message : "PNU 일괄 해석에 실패했습니다.");
    } finally {
      if (activeProject.current === projectId) setRunning(null);
    }
  }

  async function runLandPod(request: PreviewRequest = {}, continueAll = !request.parcelIndexes) {
    pendingPreview.current?.abort();
    const controller = new AbortController();
    pendingPreview.current = controller;
    setRunning("pod");
    setError(null);
    setMessage(null);
    try {
      let batchRequest = request;
      while (!controller.signal.aborted) {
        const res = await fetch(apiBase + "/projects/" + encodeURIComponent(projectId) + "/legal-pod/preview", {
          method: "POST",
          cache: "no-store",
          headers: { ...authHeaders("web"), "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify(batchRequest),
        });
        if (res.status === 409 && !controller.signal.aborted && activeProject.current === projectId) {
          setLegalPreview(null);
          throw new Error("토지 자료가 바뀌었습니다. 최신 자료에서 미리보기를 다시 시작해 주세요.");
        }
        if (!res.ok) throw new Error("토지·인허가 검토 실패 (" + res.status + ")");
        const result = await res.json();
        if (controller.signal.aborted || activeProject.current !== projectId) return;
        if (result.previewOnly !== true || typeof result.previewHash !== "string") throw new Error("미리보기 결과를 불러오지 못했습니다.");
        const parcelBatch = result.parcelBatch as PreviewParcelBatch | undefined;
        if (batchRequest.parcelRevision && parcelBatch?.parcelRevision !== batchRequest.parcelRevision) {
          throw new Error("필지 묶음을 확인할 수 없습니다. 미리보기를 다시 시작해 주세요.");
        }
        const nextBatch = { previewHash: result.previewHash, legalReview: asRecord(result.legalReview), request: batchRequest, parcelBatch };
        setLegalPreview((previous) => mergeParcelPreview(projectId, previous, nextBatch));
        if (!continueAll || !parcelBatch || parcelBatch.nextOffset === null) break;

        const { nextOffset, parcelRevision, totalParcels } = parcelBatch;
        if (!Number.isSafeInteger(nextOffset) || nextOffset <= (batchRequest.parcelOffset ?? 0) || nextOffset >= totalParcels || !parcelRevision) {
          throw new Error("다음 필지 묶음을 확인할 수 없습니다. 조회 결과를 확인한 뒤 다시 시도해 주세요.");
        }
        batchRequest = { parcelOffset: nextOffset, parcelRevision };
      }
    } catch (err) {
      if (!controller.signal.aborted && activeProject.current === projectId) setError(err instanceof Error ? err.message : "토지·인허가 검토에 실패했습니다.");
    } finally {
      if (pendingPreview.current === controller) {
        pendingPreview.current = null;
        if (activeProject.current === projectId) setRunning(null);
      }
    }
  }

  function pauseLandPod() {
    pendingPreview.current?.abort();
    pendingPreview.current = null;
    setRunning(null);
    setMessage("필지 조회를 중지했습니다. 확인한 결과는 이 화면에서 이어볼 수 있습니다.");
  }

  const visibleParcels = parcels.slice(0, visibleParcelLimit).map((parcel, index) => {
    const name = parcelName(parcel, index);
    return { parcel, name, key: `${parcel.sequence ?? index}-${name}` };
  });
  const selectedParcel = visibleParcels.find(({ key }) => key === selectedParcelKey);

  return (
    <div className="space-y-4 pb-8">
      <header className="flex min-w-0 items-center gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-re-control bg-re-subtle text-re-action">
          <Landmark className="h-5 w-5" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight text-re-text">토지 원장</h1>
          <p className="mt-0.5 text-sm text-re-secondary">필지·증빙 확인과 인허가 검토</p>
        </div>
      </header>

      {loading && !activeContext ? (
        <div role="status" className="flex min-h-20 items-center gap-2 rounded-re-panel border border-re-border bg-re-surface p-4 text-sm font-medium text-re-secondary">
          <Loader2 className="h-4 w-4 motion-safe:animate-spin" aria-hidden="true" />
          토지 원장 불러오는 중…
        </div>
      ) : (
        <>
          {loadError ? <Notice tone="bad">{loadError}</Notice> : null}
          {error ? <Notice tone="bad">{error}</Notice> : null}
          {message ? <Notice tone="good">{message}</Notice> : null}

          <section aria-labelledby="land-next-action" className="overflow-hidden rounded-re-panel border border-re-border bg-re-surface shadow-re-panel">
            <div className="flex flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-5">
              <div className="min-w-0">
                <h2 id="land-next-action" className="text-base font-semibold text-re-text">다음 조치</h2>
                <p className="mt-1 text-sm leading-5 text-re-secondary">{nextAction}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {primaryAction === "retry" ? (
                  <button type="button" onClick={() => void load()} disabled={loading || running !== null} className={actionClass(true)}>
                    <RefreshCw className={`h-4 w-4 ${loading ? "motion-safe:animate-spin" : ""}`} aria-hidden="true" />
                    토지 상태 다시 불러오기
                  </button>
                ) : null}
                {primaryAction === "upload" ? (
                  <Link href={intakeUrl} className={actionClass(true)}>
                    <Plus className="h-4 w-4" aria-hidden="true" />
                    토지조서 추가
                  </Link>
                ) : null}
                <button type="button" onClick={() => void runLandPod()} disabled={running !== null || loading} className={actionClass(primaryAction === "preview" || primaryAction === "pnu")}>
                  {running === "pod" ? <Loader2 className="h-4 w-4 motion-safe:animate-spin" aria-hidden="true" /> : <FileSearch className="h-4 w-4" aria-hidden="true" />}
                  {running === "pod" ? "필지·인허가 조회 중…" : "토지·인허가 미리보기"}
                </button>
                {running === "pod" && !preview ? (
                  <button type="button" onClick={pauseLandPod} className={actionClass(false)}>필지 조회 중지</button>
                ) : null}
              </div>
            </div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-t border-re-border bg-re-subtle px-4 py-3 sm:flex sm:flex-wrap sm:gap-x-8 sm:px-5">
              <SummaryItem label="토지조서 필지" value={parcelCount === 0 ? "등록 자료 없음" : formatCount(parcelCount, "필지")} />
              <SummaryItem label="PNU 확보" value={pnuLabel} />
              <SummaryItem label="토지 증빙" value={landEvidenceUnavailable ? "조회 불가" : formatCount(evidenceCount)} />
            </dl>
          </section>

          {visibleParcels.length ? (
            <Panel title="필지별 검토">
              <ul className="divide-y divide-re-border overflow-hidden rounded-re-control border border-re-border">
                {visibleParcels.map(({ parcel, name, key }) => {
                  const status = parcelPnuStatus(parcel, parcelPnu(parcel));
                  return (
                    <li key={key}>
                      <button type="button" aria-haspopup="dialog"
                        onClick={(event) => { parcelOpener.current = event.currentTarget; setSelectedParcelKey(key); }}
                        className="flex min-h-14 w-full min-w-0 items-center gap-3 px-3 py-3 text-left transition-colors duration-150 hover:bg-re-subtle focus-visible:-outline-offset-2 motion-reduce:transition-none sm:px-4">
                        <span className="grid min-w-0 flex-1 gap-1 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] sm:items-center sm:gap-3">
                          <span className="break-words text-sm font-semibold text-re-text">{name}</span>
                          <span className={`break-words text-xs font-medium ${status.tone}`}>{status.label}</span>
                          <span className="break-words text-xs text-re-secondary">{parcelContractAmount(parcel)}</span>
                        </span>
                        <span className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-re-action">
                          상세 보기 <ChevronRight className="h-4 w-4" aria-hidden="true" />
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
              {parcels.length > visibleParcels.length ? (
                <button type="button" className={`mt-3 ${actionClass(false)}`} onClick={() => setVisibleParcelLimit((limit) => limit + 120)}>
                  필지 더 보기 ({formatCount(visibleParcels.length, "필지")} / {formatCount(parcels.length, "필지")})
                </button>
              ) : null}
            </Panel>
          ) : null}

          <Panel title="토지·인허가 검토" titleRef={previewHeading}>
            {preview ? (
              <>
                <p className="mb-3 text-sm text-re-secondary">화면 전용 예비 검토입니다. 새로고침하면 사라집니다.</p>
                {typeof legalIntelligence.verifiedReferenceCount === "number" ? (
                  <p className="mb-3 text-sm font-medium text-re-secondary">
                    {legalIntelligence.verifiedReferenceCount > 0 ? `검증된 법령·조례 근거 ${legalIntelligence.verifiedReferenceCount}건` : "검증된 법령·조례 원문 근거 미확보"}
                  </p>
                ) : null}
                <PermitHeightReview analysis={legalAnalysis} />
              </>
            ) : (
              <p className="text-sm text-re-secondary">미리보기를 실행하면 계획 용도에 따른 인허가 경로와 필지별 높이 근거를 확인할 수 있습니다.</p>
            )}
            {previewSession && previewBatch ? (
              <ParcelPreviewResults session={previewSession} batch={previewBatch} mapUrl={mapUrl}
                storedPnus={new Set(parcels.map(parcelPnu).filter((pnu): pnu is string => Boolean(pnu)))}
                busy={running !== null || loading} onRequest={(request) => void runLandPod(request)}
                onPause={running === "pod" ? pauseLandPod : undefined}
                onRefreshBatch={supersededBatch && preview ? () => void runLandPod({ ...preview.request, parcelRevision: previewBatch.parcelRevision }, false) : undefined}
                onSelectBatch={(active) => setLegalPreview((current) => current ? { ...current, active } : null)} />
            ) : null}
            <LandLegalSummaryActions key={`${projectId}:${preview?.previewHash ?? ""}`} projectId={projectId} previewHash={preview?.previewHash} analysis={legalAnalysis} disabled={running !== null || loading || supersededBatch}
              batch={previewBatch ? {
                parcelRevision: previewBatch.parcelRevision,
                ...(preview?.request.parcelIndexes ? { reviewedParcelIndexes: preview.request.parcelIndexes } : { parcelOffset: preview?.request.parcelOffset ?? 0 }),
                rows: previewBatch.rows.filter((row) => row.status === "confirmed" && row.pnu),
              } : undefined} />
            <Link href={intakeUrl} className="mt-3 inline-block text-sm font-semibold text-re-action underline">토지이용계획·인허가 자료 입력</Link>
          </Panel>

          <Panel title="계약·수지 금액 대사">
            <p className="mb-3 text-sm text-re-secondary">저장된 원본 금액과 차액을 대조하는 검토입니다.</p>
            <dl>
              <InfoRow label="토지조서 계약 합계" value={formatKrw(contract.value)} source={sourceLabel(contract)} />
              <InfoRow label="수지 순수 매입예산" value={formatKrw(purchaseBudget.value)} source={sourceLabel(purchaseBudget)} />
              <InfoRow label="수지 부대비 포함 대지비" value={formatKrw(inclusiveBudget.value)} source={sourceLabel(inclusiveBudget)} />
              <InfoRow label="수지 부대비 (포함 금액 − 순수 매입예산)" value={formatKrw(reconciliation.ancillaryBudgetKrw)} />
              <InfoRow label="차액 (순수 매입예산 − 계약 합계)" value={formatKrw(reconciliation.purchaseMinusContractKrw)} />
            </dl>
            {typeof reconciliation.individualAmountMissingCount === "number" && reconciliation.individualAmountMissingCount > 0 ? (
              <p className="mt-3 text-sm text-amber-700 dark:text-amber-300">개인별 계약금액 {reconciliation.individualAmountMissingCount}건이 원본에 미기재되어 있습니다. 합계 불일치와 구분하여 보완하세요.</p>
            ) : null}
            {reconciliation.studyStatus === "candidate" ? <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">수지 검토 초안의 원본값입니다. 승인된 계산 입력을 뜻하지 않습니다.</p> : null}
            {reconciliation.workbookValidationStatus === "failed" ? <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">수지 원본의 수식·검증 오류가 남아 있습니다.</p> : null}
            {reconciliation.requiresLedgerReimport ? <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">기존 토지조서는 공동계약 구분을 위해 다시 가져와야 합니다.</p> : null}
            {reconciliation.status === "incomplete" ? <p className="mt-2 text-sm text-slate-500">비교할 원본 금액이 부족합니다. 토지조서와 사업수지를 가져와 저장하세요.</p> : null}
            <Link className="mt-3 inline-block text-sm font-semibold text-re-action underline" href={intakeUrl}>자료 입력 및 원본 확인</Link>
            {savedReview.comment && !reconciliation.reviewMatchesCurrentSources ? (
              <p className="mt-3 whitespace-pre-wrap text-sm text-amber-700 dark:text-amber-300">이전 원본의 검토 메모입니다. 현재 자료와 다시 대조하세요.{"\n"}{String(savedReview.comment)}</p>
            ) : null}
            <label className="mt-4 block text-sm font-semibold" htmlFor="land-cost-review">검토 이유와 다음 확인 사항</label>
            <textarea id="land-cost-review" aria-label="토지비 검토 메모" value={reviewComment}
              onChange={(event) => { reviewDirty.current = true; setReviewComment(event.target.value); }} maxLength={5000} rows={3} disabled={running !== null}
              className="mt-2 w-full rounded-re-control border border-re-border-strong bg-re-surface p-3 text-sm text-re-text" />
            <button type="button" onClick={() => void saveReview()} disabled={running !== null || !reviewComment.trim() || typeof reconciliation.comparisonHash !== "string" || contract.value == null}
              className={`mt-2 ${actionClass(false)}`}>{running === "review" ? "저장 중…" : "검토 메모 저장"}</button>
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

          <details className="group rounded-re-panel border border-re-border bg-re-surface shadow-re-panel">
            <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-3 rounded-re-panel px-4 py-3 text-sm font-semibold text-re-text transition-colors duration-150 hover:bg-re-subtle motion-reduce:transition-none sm:px-5">
              토지 기본 정보·저장된 증빙
              <ChevronDown className="h-4 w-4 shrink-0 text-re-secondary group-open:rotate-180" aria-hidden="true" />
            </summary>
            <div className="space-y-4 border-t border-re-border px-4 py-4 sm:px-5">
              <div className="grid gap-x-8 gap-y-4 md:grid-cols-2">
                <dl>
                  <InfoRow label="주소" value={displayText(land.address, "미확인")} />
                  <InfoRow label="대표 PNU" value={displayText(land.pnu, "미확인")} />
                  <InfoRow label="대지면적" value={formatSqm(land.area_sqm ?? summary.totalAreaSqm)} />
                  <InfoRow label="토지 매입금액" value={formatKrw(summary.totalContractAmount)} />
                </dl>
                <dl>
                  <InfoRow label="공시지가 증빙" value={evidenceAvailability(landEvidenceUnavailable, hasOfficialLandPrice)} />
                  <InfoRow label="등기·제한물권 증빙" value={evidenceAvailability(landEvidenceUnavailable, hasRegistryEvidence)} />
                  <InfoRow label="등기 소유자 대조" value={landEvidenceUnavailable && comparedOwners === 0 ? "조회 불가" : ownerMatchLabel} />
                </dl>
              </div>
              {landEvidenceUnavailable ? (
                <p className="text-sm text-re-secondary">토지 증빙을 불러오지 못했습니다. 상태를 다시 불러와 확인하세요.</p>
              ) : evidenceRecords.length ? (
                <EvidenceRecordList records={evidenceRecords.slice(0, 12)} />
              ) : (
                <p className="text-sm text-re-secondary">{evidenceCount === 0 ? "저장된 토지 증빙이 없습니다." : "증빙 목록 미확인"}</p>
              )}
              {evidenceRecords.length > 12 ? (
                <p className="text-xs text-re-secondary">저장된 증빙 중 처음 12건을 표시합니다.</p>
              ) : null}
              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={() => void load()} disabled={loading || running !== null} className={actionClass(false)}>
                  <RefreshCw className="h-4 w-4" aria-hidden="true" />
                  토지 상태 갱신
                </button>
                {(parcelCount ?? 0) > 0 && primaryAction !== "retry" ? (
                  <button type="button" onClick={() => void runResolvePnu()} disabled={loading || running !== null} className={actionClass(false)}>
                    <Database className="h-4 w-4" aria-hidden="true" />
                    {running === "pnu" ? "PNU 원장에 반영 중…" : "PNU 조회 후 원장에 저장"}
                  </button>
                ) : null}
              </div>
            </div>
          </details>
        </>
      )}
      <ParcelDetailDrawer
        parcel={selectedParcel?.parcel}
        name={selectedParcel?.name ?? ""}
        mapUrl={mapUrl}
        evidenceRecords={landEvidenceUnavailable ? [] : evidenceRecords}
        evidenceUnavailable={landEvidenceUnavailable || !Array.isArray(pageData.landEvidenceRecords)}
        opener={parcelOpener}
        onClose={() => setSelectedParcelKey(null)}
      />
    </div>
  );
}

function actionClass(primary: boolean) {
  return `inline-flex min-h-11 items-center justify-center gap-2 rounded-re-control border px-3.5 text-sm font-semibold transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-none ${primary
    ? "border-transparent bg-re-action text-re-on-action hover:bg-re-action-hover"
    : "border-re-border bg-re-surface text-re-text hover:border-re-border-strong hover:bg-re-subtle"}`;
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-re-secondary">{label}</dt>
      <dd className="mt-0.5 break-words text-sm font-semibold text-re-text">{value}</dd>
    </div>
  );
}

function evidenceAvailability(unavailable: boolean, available: unknown) {
  if (unavailable) return "조회 불가";
  if (available === true) return "확보";
  if (available === false) return "보강 필요";
  return "미확인";
}

const EVIDENCE_TYPE_LABELS: Record<string, string> = {
  official_land_price: "공시지가 증빙",
  registry_record: "등기·제한물권 증빙",
  pnu_resolution: "PNU 해석 증빙",
};

function EvidenceRecordList({ records }: { records: LandEvidenceRecord[] }) {
  return (
    <ul className="divide-y divide-re-border rounded-re-control border border-re-border">
      {records.map((record, index) => (
        <li key={record.evidenceId ?? record.evidence_id ?? index} className="min-w-0 px-3 py-2.5">
          <p className="text-sm font-semibold text-re-text">{EVIDENCE_TYPE_LABELS[record.evidenceType ?? record.evidence_type ?? ""] ?? "토지 증빙"}</p>
          <p className="mt-0.5 break-words text-xs leading-5 text-re-secondary">
            {displayText(record.sourceName ?? record.source_name, "출처 미확인")}
            {record.pnu ? ` · ${record.pnu}` : ""}
            {record.baseYear ?? record.base_year ? ` · 기준연도 ${record.baseYear ?? record.base_year}` : ""}
          </p>
        </li>
      ))}
    </ul>
  );
}

function ParcelPreviewResults({ session, batch, mapUrl, storedPnus, busy, onRequest, onPause, onSelectBatch, onRefreshBatch }: {
  session: PreviewSession;
  batch: PreviewParcelBatch;
  mapUrl: string;
  storedPnus: ReadonlySet<string>;
  busy: boolean;
  onRequest: (request: PreviewRequest) => void;
  onPause?: () => void;
  onSelectBatch: (index: number) => void;
  onRefreshBatch?: () => void;
}) {
  const rows = Object.values(session.rows);
  const confirmed = rows.filter((row) => row.status === "confirmed").length;
  const ambiguous = rows.filter((row) => row.status === "ambiguous").length;
  const failed = rows.filter((row) => row.status === "failed").length;
  const zoned = rows.filter((row) => row.landUseStatus === "confirmed").length;
  const landUseFailed = rows.filter((row) => row.landUseStatus === "failed").length;
  const retryIndexes = batch.rows.map((row) => session.rows[row.parcelIndex] ?? row)
    .filter((row) => row.status !== "confirmed" || row.landUseStatus !== "confirmed").map((row) => row.parcelIndex);

  return (
    <div className="mb-4 space-y-3 rounded-re-panel border border-re-border bg-re-subtle p-3 sm:p-4">
      <div>
        <h3 className="text-sm font-bold text-re-text">필지별 조회 진행</h3>
        <p role="status" className="mt-1 text-xs leading-5 text-re-secondary">
          전체 {formatCount(batch.totalParcels, "필지")} 중 {formatCount(rows.length, "필지")} 상태 확인
          {" · "}PNU 확인 {formatCount(confirmed, "필지")} · 후보 확인 {formatCount(ambiguous, "필지")}
          {" · "}미조회 {formatCount(Math.max(0, batch.totalParcels - confirmed - ambiguous - failed), "필지")}
          {" · "}실패 {formatCount(failed, "필지")} · 토지이용 근거 {formatCount(zoned, "필지")}
          {landUseFailed ? ` · 토지이용 조회 실패 ${formatCount(landUseFailed, "필지")}` : null}
        </p>
      </div>
      {session.batches.length > 1 ? (
        <label className="flex flex-wrap items-center gap-2 text-xs font-semibold text-re-text">
          검토한 필지 묶음
          <select
            value={session.active}
            disabled={busy}
            onChange={(event) => onSelectBatch(Number(event.target.value))}
            className="min-h-11 max-w-full rounded-re-control border border-re-border bg-re-surface px-2 text-sm text-re-text"
          >
            {session.batches.map(({ parcelBatch, request }, index) => {
              const indexes = parcelBatch?.rows.map((row) => row.parcelIndex).join(", ");
              return <option key={index} value={index}>{request.parcelIndexes ? "재조회" : "필지"} {indexes || index + 1}</option>;
            })}
          </select>
        </label>
      ) : null}
      {batch.rows.length ? (
        <ul className="divide-y divide-re-border rounded-re-control border border-re-border bg-re-surface">
          {batch.rows.map((row) => {
            const pnu = row.status === "confirmed" && row.pnu && /^[0-9]{10}[12][0-9]{8}$/.test(row.pnu) ? row.pnu : null;
            const source = row.landUseSourceLocator;
            let sourceHost = "";
            try { if (source?.sourceUrl) sourceHost = new URL(source.sourceUrl).hostname; } catch { /* Unstructured locators stay text-only. */ }
            const sourceName = source?.sourceName || sourceHost || (source ? "공공자료 연결" : "공식 출처 미확인");
            return (
              <li key={row.parcelIndex} className="flex flex-wrap items-start justify-between gap-2 p-3">
                <div className="min-w-0 space-y-1 text-xs leading-5 text-re-secondary">
                  <p className="break-words text-sm font-bold text-re-text">{displayText(row.address ?? row.parcelLabel, `필지 ${row.parcelIndex}`)}</p>
                  <p>{PNU_PREVIEW_LABEL[row.status]}{pnu ? ` · ${pnu}` : ""} · {LAND_USE_PREVIEW_LABEL[row.landUseStatus]}</p>
                  {row.landUseStatus === "confirmed" ? (
                    <p className="break-words">용도지역 {displayText(row.zoning, "미확인")} · 지구단위계획 {displayText(row.districtUnitPlan, "미확인")} · 출처 {sourceName}</p>
                  ) : null}
                </div>
                {pnu && storedPnus.has(pnu) ? (
                  <Link href={`${mapUrl}${mapUrl.includes("?") ? "&" : "?"}pnu=${pnu}`} className={actionClass(false)}>
                    <MapPin className="h-4 w-4" aria-hidden="true" /> 지도에서 보기
                  </Link>
                ) : <p className="self-center text-xs text-re-secondary">{pnu ? "미리보기 PNU · 저장된 지도에는 미반영" : "지도 표시용 PNU 미확인"}</p>}
              </li>
            );
          })}
        </ul>
      ) : null}
      {batch.jurisdictions?.length ? (
        <details className="rounded-re-control border border-re-border bg-re-surface px-3 py-2 text-xs text-re-secondary">
          <summary className="min-h-9 cursor-pointer py-2 font-semibold text-re-text">이 묶음의 관할·용도지역 근거</summary>
          <ul className="space-y-2 pb-2">
            {batch.jurisdictions.map((group, index) => (
              <li key={`${group.jurisdictionCode}-${index}`} className="space-y-1 break-words border-t border-re-border pt-2">
                <p className="font-semibold text-re-text">
                  {displayText(group.jurisdictionName, `관할 코드 ${group.jurisdictionCode}`)}
                  {" · "}{displayText(Array.isArray(group.zoning) ? group.zoning.join(", ") : group.zoning, "용도지역 미확인")}
                  {" · 필지 "}{group.parcelIndexes.join(", ")}
                </p>
                {group.permitRoutes?.length ? (
                  <p>검토할 인허가 경로 · {group.permitRoutes.map((route) => route.label).filter(Boolean).join(", ")}</p>
                ) : null}
                {group.ordinanceReferences?.length ? (
                  <ul className="space-y-1">
                    {group.ordinanceReferences.map((reference, referenceIndex) => {
                      const title = [reference.title || reference.lawName, reference.citation || reference.article].filter(Boolean).join(" ");
                      return <li key={referenceIndex}>{reference.url && /^https?:\/\//i.test(reference.url)
                        ? <a href={reference.url} target="_blank" rel="noreferrer" className="font-medium text-re-action underline">{displayText(title, "공식 조례 원문")}</a>
                        : displayText(title, "검증된 조례 근거")}
                        {reference.effectiveDate ? ` · 시행 ${reference.effectiveDate}` : null}</li>;
                    })}
                  </ul>
                ) : <p>이 관할에서 확인된 조례 원문이 없습니다.</p>}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {onRefreshBatch ? <p className="text-xs text-re-secondary">일부 필지가 이후에 다시 조회됐습니다. 이 묶음의 항목을 저장하려면 이 묶음을 다시 확인해 주세요.</p> : null}
      <div className="flex flex-wrap gap-2">
        {onRefreshBatch ? (
          <button type="button" className={actionClass(false)} disabled={busy} onClick={onRefreshBatch}>이 묶음 다시 확인</button>
        ) : null}
        {onPause ? <button type="button" className={actionClass(false)} onClick={onPause}>필지 조회 중지</button> : null}
        {session.nextOffset !== null && !onPause ? (
          <button type="button" className={actionClass(true)} disabled={busy}
            onClick={() => onRequest({ parcelOffset: session.nextOffset ?? 0, parcelRevision: batch.parcelRevision })}>
            다음 필지 확인
          </button>
        ) : null}
        {retryIndexes.length ? (
          <button type="button" className={actionClass(false)} disabled={busy}
            onClick={() => onRequest({ parcelIndexes: retryIndexes, parcelRevision: batch.parcelRevision })}>
            확인이 필요한 필지 다시 조회 ({retryIndexes.length})
          </button>
        ) : null}
      </div>
    </div>
  );
}

function ParcelDetailDrawer({ parcel, name, mapUrl, evidenceRecords, evidenceUnavailable, opener, onClose }: {
  parcel?: Parcel;
  name: string;
  mapUrl: string;
  evidenceRecords: LandEvidenceRecord[];
  evidenceUnavailable: boolean;
  opener: RefObject<HTMLButtonElement | null>;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const open = Boolean(parcel);

  function closeDrawer() {
    dialogRef.current?.close();
    onClose();
    if (opener.current?.isConnected) opener.current.focus({ preventScroll: true });
  }

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || !open) return;
    if (!dialog.open) dialog.showModal();
    dialog.scrollTop = 0;
    const motion = window.matchMedia("(prefers-reduced-motion: no-preference)");
    const animation = motion.matches ? dialog.animate(
      [{ transform: "translateX(24px)", opacity: 0.85 }, { transform: "translateX(0)", opacity: 1 }],
      { duration: 180, easing: "ease-out" },
    ) : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      animation?.cancel();
      if (dialog.open) dialog.close();
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  const pnu = parcel ? parcelPnu(parcel) : null;
  const pnuStatus = parcel ? parcelPnuStatus(parcel, pnu) : null;
  const resolution = parcel?.pnuResolution;
  const payment = asRecord(parcel?.paymentSummary);
  const missingPaid = confirmedCount(payment.recordedPaidMissingCount);
  const missingScheduled = confirmedCount(payment.scheduledMissingCount);
  const matchingEvidence = pnu ? evidenceRecords.filter((record) => record.pnu === pnu) : [];
  const baseYear = parcel?.officialLandPriceBaseYear;
  const landPrice = typeof parcel?.officialLandPricePerSqm === "number" && Number.isFinite(parcel.officialLandPricePerSqm)
    ? `${parcel.officialLandPricePerSqm.toLocaleString("ko-KR")}원/㎡${baseYear ? ` (${baseYear}년)` : ""}` : "미확인";

  return (
    <dialog ref={dialogRef} aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        closeDrawer();
      }}
      onClick={(event) => { if (event.target === event.currentTarget) closeDrawer(); }}
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), summary, [tabindex]"))
          .filter((element) => element.tabIndex >= 0 && element.getClientRects().length > 0);
        const first = controls[0];
        const last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first && last) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last && first) {
          event.preventDefault();
          first.focus();
        }
      }}
      className="fixed inset-y-0 left-auto right-0 m-0 h-dvh max-h-none w-full max-w-full border-0 bg-re-surface p-0 text-re-text shadow-xl backdrop:bg-slate-950/50 sm:w-[30rem]">
      {parcel ? (
        <div className="min-h-full">
          <header className="flex items-start justify-between gap-3 border-b border-re-border p-4 sm:px-6">
            <div className="min-w-0">
              <p className="text-xs font-semibold text-re-secondary">필지 상세</p>
              <h2 id={titleId} className="mt-1 break-words text-xl font-bold">{name}</h2>
            </div>
            <button type="button" autoFocus onClick={closeDrawer} aria-label="필지 상세 닫기"
              className="grid h-11 w-11 shrink-0 place-items-center rounded-re-control text-re-secondary transition-colors duration-150 hover:bg-re-subtle motion-reduce:transition-none">
              <X className="h-5 w-5" aria-hidden="true" />
            </button>
          </header>

          <div className="space-y-5 p-4 sm:px-6">
            <section>
              <h3 className="text-base font-semibold">필지 정보와 PNU</h3>
              <dl className="mt-1">
                <InfoRow label="지목" value={displayText(parcel.landCategory, "미확인")} />
                <InfoRow label="면적" value={formatSqm(parcel.areaSqm)} />
                <InfoRow label="매입금액" value={parcelContractAmount(parcel)} />
                <InfoRow label="PNU" value={pnu ?? "미해석"} />
                <InfoRow label="검증 상태" value={pnuStatus?.label ?? "미확인"} />
                <InfoRow label="공시지가" value={landPrice} />
                <InfoRow label="등기 소유자 대조" value={ownerComparison(parcel)} />
              </dl>
              {pnu ? (
                <Link href={`${mapUrl}${mapUrl.includes("?") ? "&" : "?"}pnu=${pnu}`} className={`mt-3 ${actionClass(false)}`}>
                  <MapPin className="h-4 w-4" aria-hidden="true" />
                  지도에서 보기
                </Link>
              ) : null}
              {resolution?.attemptedAddress || resolution?.resolvedAddress || resolution?.constructedPnu ? (
                <dl className="mt-2 rounded-re-control bg-re-subtle px-3">
                  {resolution.attemptedAddress ? <InfoRow label="해석 시도 주소" value={resolution.attemptedAddress} /> : null}
                  {resolution.resolvedAddress ? <InfoRow label="해석된 주소" value={resolution.resolvedAddress} /> : null}
                  {resolution.constructedPnu ? <InfoRow label="구성한 PNU 후보" value={resolution.constructedPnu} /> : null}
                </dl>
              ) : null}
            </section>

            <section className="border-t border-re-border pt-4">
              <h3 className="text-base font-semibold">조서상 지급 내역</h3>
              <p className="mt-1 text-sm text-re-secondary">{paymentStatusLabel(parcel)}</p>
              {parcel.paymentReviewVersion === "ledger-recorded-payments-v1" ? (
                <>
                  <dl className="mt-1">
                    <InfoRow label="기지급 기재액 소계" value={formatKrw(payment.knownRecordedPaidAmount)} />
                    <InfoRow label="미지급·예정 기재액 소계" value={formatKrw(payment.knownScheduledAmount)} />
                  </dl>
                  <p className="mt-2 break-words text-xs text-re-secondary">{parcel.paymentSource?.sheet ?? "원본 시트 미확인"} · 원본 행 {parcel.paymentSource?.rows?.join(", ") || "미확인"}</p>
                  {(missingPaid ?? 0) > 0 || (missingScheduled ?? 0) > 0 ? (
                    <p className="mt-1 text-xs text-re-warning">미기재: 기지급 {formatCount(missingPaid)} · 미지급·예정 {formatCount(missingScheduled)}. 기재된 금액만 합산했습니다.</p>
                  ) : null}
                  <p className="mt-2 text-xs leading-5 text-re-secondary">조서의 지급 기재액입니다. 이체 증빙과 소유권 이전은 별도 확인이 필요합니다.</p>
                </>
              ) : (
                <p className="mt-2 text-sm leading-5 text-re-secondary">원본 토지조서를 다시 가져와 지급 구분을 확인하세요. 이전 지급 완료 표시는 사용하지 않습니다.</p>
              )}
            </section>

            <section className="space-y-2 border-t border-re-border pt-4">
              <h3 className="text-base font-semibold">연결된 토지 증빙</h3>
              {evidenceUnavailable ? (
                <p className="text-sm text-re-secondary">토지 증빙을 불러오지 못했습니다.</p>
              ) : matchingEvidence.length ? (
                <EvidenceRecordList records={matchingEvidence} />
              ) : (
                <p className="text-sm text-re-secondary">{pnu ? "이 PNU에 연결된 증빙 목록이 비어 있습니다." : "PNU 확인 후 필지의 증빙 연결을 검토할 수 있습니다."}</p>
              )}
            </section>
          </div>
        </div>
      ) : null}
    </dialog>
  );
}

function Panel({ title, children, titleRef }: { title: string; children: ReactNode; titleRef?: RefObject<HTMLHeadingElement | null> }) {
  return (
    <section className="min-w-0 rounded-re-panel border border-re-border bg-re-surface p-4 shadow-re-panel sm:p-5">
      <h2 ref={titleRef} tabIndex={titleRef ? -1 : undefined} className="mb-3 text-lg font-semibold text-re-text">{title}</h2>
      {children}
    </section>
  );
}

function InfoRow({ label, value, source }: { label: string; value: string; source?: string }) {
  return (
    <div className="grid grid-cols-2 items-baseline gap-3 border-b border-re-border py-2.5 text-sm last:border-0">
      <dt className="min-w-0 break-words text-re-secondary">{label}</dt>
      <dd className="min-w-0 break-words text-right font-semibold text-re-text">
        <span>{value}</span>
        {source ? <p className="mt-0.5 text-xs font-normal text-re-secondary">{source}</p> : null}
      </dd>
    </div>
  );
}

function Notice({ tone, children }: { tone: "good" | "bad"; children: ReactNode }) {
  const klass = tone === "good"
    ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200"
    : "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200";
  return <div role={tone === "bad" ? "alert" : "status"} className={`rounded-re-control border px-4 py-3 text-sm font-medium ${klass}`}>{children}</div>;
}
