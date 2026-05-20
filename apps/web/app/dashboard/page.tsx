import Link from "next/link";
import type { ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BadgeCheck,
  Briefcase,
  FileSearch,
  Landmark,
  MapPin,
  ShieldCheck,
} from "lucide-react";

const statusStyle: Record<string, string> = {
  pf_review: "bg-amber-100 text-amber-800 border-amber-200 dark:bg-amber-950/30 dark:text-amber-300 dark:border-amber-900",
  land_review: "bg-sky-100 text-sky-800 border-sky-200 dark:bg-sky-950/30 dark:text-sky-300 dark:border-sky-900",
  approved: "bg-emerald-100 text-emerald-800 border-emerald-200 dark:bg-emerald-950/30 dark:text-emerald-300 dark:border-emerald-900",
  rejected: "bg-rose-100 text-rose-800 border-rose-200 dark:bg-rose-950/30 dark:text-rose-300 dark:border-rose-900",
};

const statusLabel: Record<string, string> = {
  pf_review: "PF 검토중",
  land_review: "토지 검토중",
  approved: "승인",
  rejected: "보류",
};

const typeLabel: Record<string, string> = {
  mixed_use: "주상복합",
  logistics: "물류센터",
  residential: "공동주택",
  office: "오피스",
  lodging: "숙박시설",
  land_feasibility: "토지 사업성",
};

interface PfTranche {
  amount?: number;
}

interface ProjectSummaryResponse {
  project?: {
    id?: string | number;
    name?: string;
    status?: string;
    type?: string;
    review_date?: string;
  };
  land?: {
    address?: string;
    pnu?: string;
    area_sqm?: number;
    official_land_price?: number;
  };
  regulation?: {
    zoning?: string;
    legal_far?: number;
    legal_bcr?: number;
    applied_far?: number;
    applied_bcr?: number;
  };
  landLedger?: {
    summary?: {
      totalParcels?: number;
      missingPnuCount?: number;
      missingOfficialLandPriceCount?: number;
    };
    validation?: {
      status?: string;
      issueCount?: number;
    };
    pfSummary?: {
      pnuCoverageRatio?: number;
      officialLandPriceCoverageRatio?: number;
      registryEvidenceCoverageRatio?: number;
      status?: string;
    };
    landAcquisitionStatus?: {
      acquisitionAreaRatio?: number;
      acquisitionAmountRatio?: number;
      publicLandDetected?: boolean;
    };
  };
  pf_structure?: PfTranche[];
}

const fallbackProjects: ProjectSummaryResponse[] = [
  {
    project: { id: "p-001", name: "Daegu Bondong Mixed-Use PF", status: "pf_review", type: "mixed_use", review_date: "2026-05-15" },
    land: { address: "Daegu Dalseo-gu Bondong", pnu: "2729011700100010001", area_sqm: 5000 },
    regulation: { zoning: "commercial", legal_far: 800, legal_bcr: 70, applied_far: 750, applied_bcr: 65 },
    landLedger: {
      summary: { totalParcels: 24, missingPnuCount: 0, missingOfficialLandPriceCount: 4 },
      validation: { status: "needs_enrichment", issueCount: 4 },
      pfSummary: { pnuCoverageRatio: 0.83, officialLandPriceCoverageRatio: 0.67, registryEvidenceCoverageRatio: 0.25 },
      landAcquisitionStatus: { acquisitionAreaRatio: 0.92, acquisitionAmountRatio: 0.88, publicLandDetected: true },
    },
    pf_structure: [{ amount: 50_000_000_000 }, { amount: 15_000_000_000 }, { amount: 5_000_000_000 }],
  },
  {
    project: { id: "p-004", name: "Simple Land Feasibility", status: "land_review", type: "residential" },
    land: { address: "Seoul Gangnam-gu Yeoksam-dong", pnu: "1168010100100010001", area_sqm: 1000 },
    regulation: { zoning: "commercial", legal_far: 800, legal_bcr: 60, applied_far: 780, applied_bcr: 55 },
    landLedger: {
      summary: { totalParcels: 9, missingPnuCount: 0, missingOfficialLandPriceCount: 9 },
      validation: { status: "needs_review", issueCount: 6 },
      pfSummary: { pnuCoverageRatio: 1, officialLandPriceCoverageRatio: 0, registryEvidenceCoverageRatio: 0 },
      landAcquisitionStatus: { acquisitionAreaRatio: 0.76, acquisitionAmountRatio: 0.71, publicLandDetected: false },
    },
    pf_structure: [],
  },
];

function fmtEok(val: number) {
  const eok = val / 100_000_000;
  return eok >= 10000
    ? `${(eok / 10000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}조`
    : `${eok.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}억`;
}

function pct(value?: number) {
  if (value === undefined || Number.isNaN(value)) return "미확인";
  return `${Math.round(value * 100)}%`;
}

function landReadiness(project: ProjectSummaryResponse) {
  const pnuReady = Boolean(project.land?.pnu) || project.landLedger?.summary?.missingPnuCount === 0;
  const regulationReady = Boolean(project.regulation?.zoning);
  const officialPriceReady = Boolean(project.land?.official_land_price) || project.landLedger?.summary?.missingOfficialLandPriceCount === 0;
  const registryReady = (project.landLedger?.pfSummary?.registryEvidenceCoverageRatio ?? 0) >= 0.8;
  const acquisitionReady = (project.landLedger?.landAcquisitionStatus?.acquisitionAreaRatio ?? 0) >= 0.9;
  const score = [pnuReady, regulationReady, officialPriceReady, registryReady, acquisitionReady].filter(Boolean).length;
  return { pnuReady, regulationReady, officialPriceReady, registryReady, acquisitionReady, score };
}

async function loadProjects() {
  try {
    const res = await fetch("http://127.0.0.1:8000/projects", { cache: "no-store" });
    if (!res.ok) return fallbackProjects;
    const data = (await res.json()) as ProjectSummaryResponse[];
    return data.length > 0 ? data : fallbackProjects;
  } catch {
    return fallbackProjects;
  }
}

export default async function DashboardPage() {
  const projects = await loadProjects();
  const total = projects.length;
  const reviewing = projects.filter((p) => ["pf_review", "land_review"].includes(p.project?.status ?? "")).length;
  const totalPf = projects.reduce((s, p) => {
    const pfs = p.pf_structure ?? [];
    return s + pfs.reduce((a, t) => a + (t.amount ?? 0), 0);
  }, 0);
  const landReady = projects.filter((project) => landReadiness(project).score >= 4).length;
  const missingEvidence = projects.reduce((sum, project) => sum + (project.landLedger?.validation?.issueCount ?? 0), 0);

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-500">REALYTICS x PLATFORM9</p>
          <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            프로젝트 관리
          </h2>
          <p className="mt-2 text-sm text-slate-500">
            프로젝트별 PF 검토 상태와 Land Agent의 토지 분석 준비상태를 함께 확인합니다.
          </p>
        </div>
        <Link
          href="/analysis"
          className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
        >
          신규 토지 분석 <ArrowRight className="h-4 w-4" />
        </Link>
      </div>

      <section className="grid gap-4 md:grid-cols-4">
        <KpiCard icon={<Briefcase className="h-5 w-5 text-sky-600" />} label="전체 프로젝트" value={`${total}건`} sub={`${reviewing}건 검토중`} />
        <KpiCard icon={<Activity className="h-5 w-5 text-violet-600" />} label="총 PF 검토금액" value={fmtEok(totalPf)} sub="전체 프로젝트 합산" />
        <KpiCard icon={<Landmark className="h-5 w-5 text-emerald-600" />} label="토지 검토 준비" value={`${landReady}건`} sub="PNU·규제·취득·증빙 기준" />
        <KpiCard icon={<AlertTriangle className="h-5 w-5 text-amber-600" />} label="토지 보완 이슈" value={`${missingEvidence}개`} sub="공시지가·등기·조서 대사 포함" />
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h3 className="text-lg font-bold text-slate-950 dark:text-white">Land Agent 분석 항목</h3>
            <p className="mt-1 text-sm text-slate-500">토지 관련 에이전트가 대시보드에서 보여줄 수 있는 핵심 판단 항목입니다.</p>
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <AgentItem icon={<MapPin />} title="PNU·주소 정규화" text="지번, 산번지, 본번/부번, 법정동 기준 매칭 신뢰도를 확인합니다." />
          <AgentItem icon={<FileSearch />} title="토지조서 검증" text="필지 수, 면적, 매입금액, 지급스케줄의 원본-파싱값 대사를 봅니다." />
          <AgentItem icon={<BadgeCheck />} title="취득률·국공유지" text="면적/금액 기준 취득률과 국공유지·공유지분 등 예외필지를 표시합니다." />
          <AgentItem icon={<ShieldCheck />} title="공시지가·등기증빙" text="기준연도, 출처, 등기부 evidence, 제한물권 해소 필요성을 점검합니다." />
        </div>
      </section>

      {projects.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 p-12 text-center dark:border-slate-700">
          <p className="text-sm text-slate-500">프로젝트가 없거나 API 서버가 오프라인입니다.</p>
        </div>
      ) : (
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {projects.map((p) => {
            const proj = p.project ?? {};
            const status = proj.status ?? "pf_review";
            const pfTotal = (p.pf_structure ?? []).reduce((a, t) => a + (t.amount ?? 0), 0);
            const projectType = proj.type;
            const projectTypeLabel = projectType ? (typeLabel[projectType] ?? projectType) : "미확인";
            const readiness = landReadiness(p);
            return (
              <Link
                key={String(proj.id ?? proj.name)}
                href={`/project/${proj.id}/land`}
                className="group rounded-lg border border-slate-200 bg-white p-5 shadow-sm transition-all hover:border-slate-300 hover:shadow-md dark:border-slate-800 dark:bg-slate-900 dark:hover:border-slate-700"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800">
                      <MapPin className="h-5 w-5 text-slate-600 dark:text-slate-300" />
                    </div>
                    <div className="min-w-0">
                      <p className="truncate font-bold text-slate-950 dark:text-white">{proj.name || "Unnamed"}</p>
                      <p className="mt-0.5 truncate text-xs text-slate-500">{p.land?.address ?? "소재지 미확인"}</p>
                    </div>
                  </div>
                  <span className={`shrink-0 rounded-md border px-2 py-0.5 text-xs font-semibold ${statusStyle[status] ?? statusStyle.pf_review}`}>
                    {statusLabel[status] ?? status}
                  </span>
                </div>

                <div className="mt-4 grid grid-cols-3 gap-2">
                  <MiniStat label="유형" value={projectTypeLabel} />
                  <MiniStat label="PF 금액" value={pfTotal > 0 ? fmtEok(pfTotal) : "미확인"} />
                  <MiniStat label="토지점검" value={`${readiness.score}/5`} />
                </div>

                <div className="mt-3 grid grid-cols-2 gap-2">
                  <LandCheck label="PNU" ok={readiness.pnuReady} />
                  <LandCheck label="규제" ok={readiness.regulationReady} />
                  <LandCheck label="취득률" ok={readiness.acquisitionReady} value={pct(p.landLedger?.landAcquisitionStatus?.acquisitionAreaRatio)} />
                  <LandCheck label="등기증빙" ok={readiness.registryReady} value={pct(p.landLedger?.pfSummary?.registryEvidenceCoverageRatio)} />
                </div>

                <div className="mt-3 flex items-center gap-1 text-xs font-semibold text-indigo-600 opacity-0 transition-opacity group-hover:opacity-100 dark:text-indigo-400">
                  토지 분석 상세 보기 <ArrowRight className="h-3 w-3" />
                </div>
              </Link>
            );
          })}
        </section>
      )}
    </div>
  );
}

function KpiCard({ icon, label, value, sub }: { icon: ReactNode; label: string; value: string; sub: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-slate-500">{label}</p>
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-50 dark:bg-slate-800">{icon}</div>
      </div>
      <p className="mt-4 text-3xl font-black tracking-tight text-slate-950 dark:text-white">{value}</p>
      <p className="mt-1 text-sm text-slate-500">{sub}</p>
    </div>
  );
}

function AgentItem({ icon, title, text }: { icon: ReactNode; title: string; text: string }) {
  return (
    <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200">{icon}</div>
        <p className="font-bold text-slate-950 dark:text-white">{title}</p>
      </div>
      <p className="mt-3 text-sm leading-6 text-slate-500">{text}</p>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-2 dark:bg-slate-800/70">
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <p className="mt-0.5 text-sm font-bold text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function LandCheck({ label, ok, value }: { label: string; ok: boolean; value?: string }) {
  return (
    <div className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-800/70">
      <span className="text-xs font-semibold text-slate-500">{label}</span>
      <span className={`text-xs font-bold ${ok ? "text-emerald-600 dark:text-emerald-300" : "text-amber-600 dark:text-amber-300"}`}>
        {value ?? (ok ? "확보" : "보완")}
      </span>
    </div>
  );
}
