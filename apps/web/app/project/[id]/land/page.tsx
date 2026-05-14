import Link from "next/link";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  BadgeCheck,
  Building,
  CheckCircle2,
  Database,
  FileCheck2,
  FileSearch,
  Landmark,
  MapPin,
  Ruler,
  ShieldCheck,
} from "lucide-react";

interface LandLedgerSummary {
  parcelCount?: number;
  ownerRecordCount?: number;
  totalAreaSqm?: number;
  totalAreaPy?: number;
  totalContractAmount?: number;
  missingPnuCount?: number;
  missingOfficialLandPriceCount?: number;
  registryEvidenceMissingCount?: number;
  workflowStatusCounts?: Record<string, number>;
}

interface LandAcquisitionStatus {
  totalRequiredParcels?: number;
  contractedParcels?: number;
  paidDepositParcels?: number;
  balancePaidParcels?: number;
  ownershipTransferredParcels?: number;
  trustedParcels?: number;
  acquisitionAreaRatio?: number;
  acquisitionAmountRatio?: number;
  publicLandDetected?: boolean;
  publicLandPurchaseStatus?: string;
}

interface LandLedgerValidation {
  status?: string;
  issues?: Array<{ code?: string; severity?: string; field?: string; message?: string }>;
}

interface LandLedgerImportMetadata {
  originalFileName?: string;
  originalFileHash?: string;
  parserVersion?: string;
  sheetName?: string;
  uploadedAt?: string;
  validationStatus?: string;
}

interface LandLedgerPfSummary {
  status?: string;
  pnuCoverageRatio?: number;
  officialLandPriceCoverageRatio?: number;
  registryEvidenceCoverageRatio?: number;
  depositPaidParcelRatio?: number;
  balanceDueAmount?: number;
  reviewMemo?: string;
}

interface LandLedgerParcel {
  sequence?: number;
  addressFragment?: string;
  rawLotNumber?: string;
  normalizedLotNumber?: string;
  landCategory?: string;
  areaSqm?: number;
  totalContractAmount?: number;
  workflowStatus?: string;
  pnu?: string;
  officialLandPriceBaseYear?: number;
  registryEvidence?: unknown;
  issues?: Array<{ code?: string; severity?: string; message?: string }>;
}

interface ProjectLandResponse {
  project?: {
    name?: string;
  };
  land?: {
    address?: string;
    pnu?: string;
    area_sqm?: number;
    official_land_price?: number;
    official_land_price_base_year?: number;
  };
  regulation?: {
    zoning?: string;
    legal_far?: number;
    applied_far?: number;
    legal_bcr?: number;
    applied_bcr?: number;
  };
  land_acquisition?: Array<{
    owner?: string;
    share?: number;
    area_sqm?: number;
    mortgage?: string;
  }>;
  landLedger?: {
    importMetadata?: LandLedgerImportMetadata;
    summary?: LandLedgerSummary;
    validation?: LandLedgerValidation;
    importConsistency?: { status?: string; failedChecks?: Array<{ name?: string; parsedValue?: number; sourceValue?: number }> };
    landAcquisitionStatus?: LandAcquisitionStatus;
    pfSummary?: LandLedgerPfSummary;
    parcels?: LandLedgerParcel[];
  };
}

const apiBase = "http://127.0.0.1:8000";

export default async function LandAnalysisPage({ params }: { params: Promise<{ id: string }> | { id: string } }) {
  const resolvedParams = await params;
  let data: ProjectLandResponse = {};
  try {
    const res = await fetch(`${apiBase}/projects/${resolvedParams.id}`, { cache: "no-store" });
    data = (await res.json()) as ProjectLandResponse;
  } catch {
    data = {};
  }

  const land = data.land ?? {};
  const reg = data.regulation ?? {};
  const proj = data.project ?? {};
  const ledger = data.landLedger;
  const summary = ledger?.summary;
  const acquisition = ledger?.landAcquisitionStatus;
  const pfSummary = ledger?.pfSummary;
  const validation = ledger?.validation;
  const importMetadata = ledger?.importMetadata;
  const parcels = ledger?.parcels ?? [];
  const fallbackLandAcquisition = data.land_acquisition ?? [];

  const devGfa =
    reg.applied_far && land.area_sqm
      ? `${((reg.applied_far / 100) * land.area_sqm).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}㎡`
      : "미확인";

  const pnuStatus = summary?.missingPnuCount === 0 || land.pnu ? "확보" : "미확인";
  const officialPriceStatus =
    summary?.missingOfficialLandPriceCount === 0 || land.official_land_price ? "확보" : "미확인";
  const registryStatus = summary?.registryEvidenceMissingCount === 0 && summary?.parcelCount ? "확보" : "미확인";

  return (
    <div className="space-y-6 pb-8">
      <div className="flex items-start gap-3">
        <Link
          href={`/project/${resolvedParams.id}`}
          className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
          aria-label="프로젝트로 돌아가기"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div>
          <p className="text-sm font-semibold text-slate-500">{proj.name || `프로젝트 #${resolvedParams.id}`}</p>
          <h2 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            <MapPin className="h-7 w-7 text-indigo-600" />
            토지 검토
          </h2>
          <p className="mt-1 text-sm text-slate-500">토지 기본정보, 취득률, 조서 검증, 증빙 준비상태를 분리해 확인합니다.</p>
        </div>
      </div>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatusCard icon={<FileSearch />} label="토지조서 상태" value={validation?.status ?? "미업로드"} tone={statusTone(validation?.status)} />
        <StatusCard icon={<MapPin />} label="PNU" value={pnuStatus} tone={pnuStatus === "확보" ? "good" : "warn"} />
        <StatusCard icon={<Database />} label="공시지가" value={officialPriceStatus} tone={officialPriceStatus === "확보" ? "good" : "warn"} />
        <StatusCard icon={<ShieldCheck />} label="등기/제한물권 증빙" value={registryStatus} tone={registryStatus === "확보" ? "good" : "warn"} />
      </section>

      <ReviewSection title="1. 토지 기본정보" icon={<Landmark className="h-5 w-5" />}>
        <div className="grid gap-4 md:grid-cols-3">
          <InfoCard icon={<MapPin className="h-5 w-5 text-sky-600" />} label="소재지" value={land.address || "미확인"} />
          <InfoCard icon={<Ruler className="h-5 w-5 text-violet-600" />} label="PNU" value={land.pnu || "미확인"} />
          <InfoCard icon={<Building className="h-5 w-5 text-emerald-600" />} label="대지면적" value={formatSqm(land.area_sqm ?? summary?.totalAreaSqm)} />
        </div>
      </ReviewSection>

      <ReviewSection title="2. 토지조서 원본 및 데이터 출처" icon={<FileCheck2 className="h-5 w-5" />}>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <InfoBlock label="원본 파일" value={importMetadata?.originalFileName ?? "토지조서 미업로드"} />
          <InfoBlock label="Parser Version" value={importMetadata?.parserVersion ?? "미확인"} />
          <InfoBlock label="Sheet" value={importMetadata?.sheetName ?? "미확인"} />
          <InfoBlock label="파일 Hash" value={shortHash(importMetadata?.originalFileHash)} />
        </div>
      </ReviewSection>

      <ReviewSection title="3. 토지취득 현황" icon={<BadgeCheck className="h-5 w-5" />}>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Metric label="대상 필지" value={formatCount(acquisition?.totalRequiredParcels ?? summary?.parcelCount, "필지")} />
          <Metric label="계약 필지" value={formatCount(acquisition?.contractedParcels, "필지")} />
          <Metric label="잔금 완료 필지" value={formatCount(acquisition?.balancePaidParcels, "필지")} />
          <Metric label="소유권 이전 필지" value={formatCount(acquisition?.ownershipTransferredParcels, "필지")} />
        </div>
        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <Progress label="면적 기준 확보율" value={acquisition?.acquisitionAreaRatio} />
          <Progress label="금액 기준 확보율" value={acquisition?.acquisitionAmountRatio} />
        </div>
      </ReviewSection>

      <ReviewSection title="4. 용도지역 및 건축한도" icon={<Ruler className="h-5 w-5" />}>
        <div className="grid gap-4 md:grid-cols-2">
          <InfoBlock label="용도지역" value={reg.zoning || "미확인"} strong />
          <InfoBlock label="적용 가능 연면적" value={devGfa} strong />
        </div>
        <div className="mt-5 space-y-4">
          <RatioBar label="용적률" legal={reg.legal_far} applied={reg.applied_far} unit="%" />
          <RatioBar label="건폐율" legal={reg.legal_bcr} applied={reg.applied_bcr} unit="%" />
        </div>
      </ReviewSection>

      <ReviewSection title="5. Import Consistency 및 검증 결과" icon={<AlertTriangle className="h-5 w-5" />}>
        <div className="grid gap-4 md:grid-cols-4">
          <Metric label="필지 수" value={formatCount(summary?.parcelCount, "필지")} />
          <Metric label="소유자/지급 Record" value={formatCount(summary?.ownerRecordCount, "건")} />
          <Metric label="총 면적" value={formatSqm(summary?.totalAreaSqm)} />
          <Metric label="총 매매대금" value={formatKrw(summary?.totalContractAmount)} />
        </div>
        <IssueList issues={validation?.issues ?? []} />
      </ReviewSection>

      <ReviewSection title="6. PNU·공시지가·등기 증빙 준비상태" icon={<Database className="h-5 w-5" />}>
        <div className="grid gap-4 md:grid-cols-3">
          <EvidenceCard label="PNU 확보율" value={pfSummary?.pnuCoverageRatio} missing={summary?.missingPnuCount} />
          <EvidenceCard label="공시지가 확보율" value={pfSummary?.officialLandPriceCoverageRatio} missing={summary?.missingOfficialLandPriceCount} />
          <EvidenceCard label="등기 증빙 확보율" value={pfSummary?.registryEvidenceCoverageRatio} missing={summary?.registryEvidenceMissingCount} />
        </div>
      </ReviewSection>

      <ReviewSection title="7. 필지별 검토" icon={<FileSearch className="h-5 w-5" />}>
        {parcels.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase text-slate-500 dark:border-slate-800">
                <tr>
                  <th className="px-3 py-2">필지</th>
                  <th className="px-3 py-2">지목</th>
                  <th className="px-3 py-2">면적</th>
                  <th className="px-3 py-2">매매대금</th>
                  <th className="px-3 py-2">상태</th>
                  <th className="px-3 py-2">Issue</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {parcels.map((parcel) => (
                  <tr key={`${parcel.sequence}-${parcel.addressFragment}`}>
                    <td className="px-3 py-3 font-semibold text-slate-950 dark:text-white">{parcel.addressFragment ?? parcel.rawLotNumber ?? "미확인"}</td>
                    <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{parcel.landCategory ?? "미확인"}</td>
                    <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{formatSqm(parcel.areaSqm)}</td>
                    <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{formatKrw(parcel.totalContractAmount)}</td>
                    <td className="px-3 py-3"><StatusPill status={parcel.workflowStatus ?? "미확인"} /></td>
                    <td className="px-3 py-3 text-slate-500">{parcel.issues?.[0]?.code ?? "없음"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <FallbackParcels items={fallbackLandAcquisition} />
        )}
      </ReviewSection>

      <ReviewSection title="8. PF 심사 연결 요약" icon={<CheckCircle2 className="h-5 w-5" />}>
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          {pfSummary?.reviewMemo ??
            "토지조서 import는 토지취득 검토의 시작점입니다. PNU, 지적특성, 공시지가, 등기부 증빙, 소유권 및 제한물권 검증이 완료되기 전까지는 법무검토 완료 또는 PF 심사 확정자료로 간주하지 않습니다."}
        </div>
      </ReviewSection>
    </div>
  );
}

function ReviewSection({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-4 flex items-center gap-2 text-slate-950 dark:text-white">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200">{icon}</div>
        <h3 className="text-lg font-bold">{title}</h3>
      </div>
      {children}
    </section>
  );
}

function StatusCard({ icon, label, value, tone }: { icon: ReactNode; label: string; value: string; tone: "good" | "warn" | "bad" | "neutral" }) {
  const toneClass = {
    good: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100",
    warn: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100",
    bad: "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-100",
    neutral: "border-slate-200 bg-white text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200",
  }[tone];
  return (
    <div className={`rounded-lg border p-4 ${toneClass}`}>
      <div className="flex items-center gap-2">
        <div className="h-5 w-5">{icon}</div>
        <p className="text-xs font-semibold">{label}</p>
      </div>
      <p className="mt-3 text-xl font-black">{value}</p>
    </div>
  );
}

function InfoCard({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center gap-3 rounded-lg bg-slate-50 p-4 dark:bg-slate-800/70">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white dark:bg-slate-900">{icon}</div>
      <div className="min-w-0">
        <p className="text-xs font-semibold text-slate-500">{label}</p>
        <p className="mt-0.5 break-words text-sm font-bold text-slate-950 dark:text-white">{value}</p>
      </div>
    </div>
  );
}

function InfoBlock({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <p className={`mt-1 break-words ${strong ? "text-xl font-black" : "text-sm font-bold"} text-slate-950 dark:text-white`}>{value}</p>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-4 dark:bg-slate-800/70">
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <p className="mt-2 text-xl font-black text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function RatioBar({ label, legal, applied, unit }: { label: string; legal?: number; applied?: number; unit: string }) {
  if (!legal || !applied) {
    return <InfoBlock label={label} value="법정/적용 기준 미확인" />;
  }
  const pct = Math.min(100, (applied / legal) * 100);
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between gap-3 text-sm">
        <span className="font-semibold text-slate-700 dark:text-slate-300">{label}</span>
        <span className="text-xs text-slate-500">
          적용 <span className="font-bold text-slate-950 dark:text-white">{applied}{unit}</span> / 법정 {legal}{unit}
        </span>
      </div>
      <div className="h-3 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div className="h-3 rounded-full bg-indigo-600 transition-all" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Progress({ label, value }: { label: string; value?: number }) {
  const pct = Math.max(0, Math.min(100, (value ?? 0) * 100));
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-sm">
        <span className="font-semibold text-slate-700 dark:text-slate-300">{label}</span>
        <span className="font-bold text-slate-950 dark:text-white">{pct.toFixed(1)}%</span>
      </div>
      <div className="h-3 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div className="h-3 rounded-full bg-emerald-600" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function EvidenceCard({ label, value, missing }: { label: string; value?: number; missing?: number }) {
  const pct = Math.max(0, Math.min(100, (value ?? 0) * 100));
  return (
    <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
      <p className="text-sm font-bold text-slate-950 dark:text-white">{label}</p>
      <p className="mt-2 text-2xl font-black text-indigo-600 dark:text-indigo-300">{pct.toFixed(1)}%</p>
      <p className="mt-1 text-xs text-slate-500">누락 {missing ?? "미확인"}건</p>
    </div>
  );
}

function IssueList({ issues }: { issues: Array<{ code?: string; severity?: string; field?: string; message?: string }> }) {
  if (!issues.length) {
    return (
      <div className="mt-4 flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm font-semibold text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100">
        <CheckCircle2 className="h-5 w-5" />
        현재 표시할 검증 이슈가 없습니다.
      </div>
    );
  }
  return (
    <div className="mt-4 grid gap-3 md:grid-cols-2">
      {issues.slice(0, 8).map((issue, index) => (
        <div key={`${issue.code}-${index}`} className="rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950/30">
          <p className="text-sm font-bold text-amber-900 dark:text-amber-100">{issue.code ?? "issue"}</p>
          <p className="mt-1 text-xs text-amber-800 dark:text-amber-200">{issue.message ?? issue.field ?? "검토 필요"}</p>
        </div>
      ))}
    </div>
  );
}

function FallbackParcels({ items }: { items: Array<{ owner?: string; share?: number; area_sqm?: number; mortgage?: string }> }) {
  if (!items.length) {
    return <p className="rounded-lg bg-slate-50 p-4 text-sm text-slate-500 dark:bg-slate-800/70">필지별 토지조서가 아직 업로드되지 않았습니다.</p>;
  }
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {items.map((item, index) => (
        <div key={`${item.owner}-${index}`} className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
          <p className="font-bold text-slate-950 dark:text-white">{item.owner ?? `필지 ${index + 1}`}</p>
          <p className="mt-1 text-sm text-slate-500">지분 {formatPercent(item.share)} · 면적 {formatSqm(item.area_sqm)}</p>
          <p className="mt-1 text-xs text-slate-500">담보/제한물권: {item.mortgage ?? "미확인"}</p>
        </div>
      ))}
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const tone = statusTone(status);
  const toneClass = {
    good: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200",
    warn: "bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-200",
    bad: "bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-200",
    neutral: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200",
  }[tone];
  return <span className={`inline-flex rounded px-2 py-1 text-xs font-bold ${toneClass}`}>{status}</span>;
}

function statusTone(status?: string): "good" | "warn" | "bad" | "neutral" {
  if (!status) return "neutral";
  if (status.includes("ready") || status === "enriched" || status === "passed") return "good";
  if (status.includes("review") || status.includes("warning")) return "bad";
  if (status.includes("enrichment") || status.includes("evidence") || status.includes("missing")) return "warn";
  return "neutral";
}

function formatSqm(value?: number) {
  return value ? `${value.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}㎡` : "미확인";
}

function formatKrw(value?: number) {
  return value ? `${(value / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억원` : "미확인";
}

function formatPercent(value?: number) {
  return value !== undefined ? `${(value * 100).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%` : "미확인";
}

function formatCount(value: number | undefined, unit: string) {
  return value !== undefined ? `${value.toLocaleString("ko-KR")}${unit}` : "미확인";
}

function shortHash(value?: string) {
  return value ? `${value.slice(0, 12)}...` : "미확인";
}
