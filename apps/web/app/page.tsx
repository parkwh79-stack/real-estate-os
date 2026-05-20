import Link from "next/link";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  ArrowRight,
  BadgeCheck,
  Building2,
  Landmark,
  MapPin,
  Ruler,
  ShieldCheck,
} from "lucide-react";

interface LandLedgerSummary {
  totalParcels?: number;
  missingPnuCount?: number;
  missingOfficialLandPriceCount?: number;
}

interface LandLedgerValidation {
  status?: string;
  issueCount?: number;
}

interface LandLedgerPfSummary {
  pnuCoverageRatio?: number;
  officialLandPriceCoverageRatio?: number;
  registryEvidenceCoverageRatio?: number;
  status?: string;
}

interface ProjectDashboardResponse {
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
    official_land_price_base_year?: number;
  };
  regulation?: {
    zoning?: string;
    legal_far?: number;
    legal_bcr?: number;
    applied_far?: number;
    applied_bcr?: number;
  };
  landLedger?: {
    summary?: LandLedgerSummary;
    validation?: LandLedgerValidation;
    pfSummary?: LandLedgerPfSummary;
    landAcquisitionStatus?: {
      acquisitionAreaRatio?: number;
      acquisitionAmountRatio?: number;
      publicLandDetected?: boolean;
    };
  };
}

const fallbackProjects: ProjectDashboardResponse[] = [
  {
    project: { id: "p-001", name: "Daegu Bondong Mixed-Use PF", status: "pf_review", type: "mixed_use" },
    land: { address: "Daegu Dalseo-gu Bondong", pnu: "2729011700100010001", area_sqm: 5000 },
    regulation: { zoning: "commercial", legal_far: 800, legal_bcr: 70, applied_far: 750, applied_bcr: 65 },
    landLedger: {
      summary: { totalParcels: 24, missingPnuCount: 0, missingOfficialLandPriceCount: 4 },
      validation: { status: "needs_enrichment", issueCount: 4 },
      pfSummary: { pnuCoverageRatio: 0.83, officialLandPriceCoverageRatio: 0.67, registryEvidenceCoverageRatio: 0.25 },
      landAcquisitionStatus: { acquisitionAreaRatio: 0.92, acquisitionAmountRatio: 0.88, publicLandDetected: true },
    },
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
  },
];

async function loadProjects() {
  try {
    const res = await fetch("http://127.0.0.1:8000/projects", { cache: "no-store" });
    if (!res.ok) return fallbackProjects;
    const data = (await res.json()) as ProjectDashboardResponse[];
    return data.length > 0 ? data : fallbackProjects;
  } catch {
    return fallbackProjects;
  }
}

function pct(value?: number) {
  if (value === undefined || Number.isNaN(value)) return "미확인";
  return `${Math.round(value * 100)}%`;
}

function sqm(value?: number) {
  if (!value) return "미확인";
  return `${value.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}㎡`;
}

function landReadiness(project: ProjectDashboardResponse) {
  const pnuReady = Boolean(project.land?.pnu) || project.landLedger?.summary?.missingPnuCount === 0;
  const regulationReady = Boolean(project.regulation?.zoning);
  const officialPriceReady = Boolean(project.land?.official_land_price) || project.landLedger?.summary?.missingOfficialLandPriceCount === 0;
  const registryReady = (project.landLedger?.pfSummary?.registryEvidenceCoverageRatio ?? 0) >= 0.8;
  const acquisitionReady = (project.landLedger?.landAcquisitionStatus?.acquisitionAreaRatio ?? 0) >= 0.9;
  const score = [pnuReady, regulationReady, officialPriceReady, registryReady, acquisitionReady].filter(Boolean).length;
  return { pnuReady, regulationReady, officialPriceReady, registryReady, acquisitionReady, score };
}

export default async function DashboardPage() {
  const projects = await loadProjects();
  const landReadyCount = projects.filter((project) => landReadiness(project).score >= 4).length;
  const pnuCoverageCount = projects.filter((project) => landReadiness(project).pnuReady).length;
  const evidenceIssueCount = projects.reduce((sum, project) => sum + (project.landLedger?.validation?.issueCount ?? 0), 0);
  const publicLandCount = projects.filter((project) => project.landLedger?.landAcquisitionStatus?.publicLandDetected).length;

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-500">REALYTICS x PLATFORM9</p>
          <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            AI 부동산 개발 심사 대시보드
          </h2>
          <p className="mt-2 max-w-3xl text-sm text-slate-500">
            토지 식별, PNU, 용도지역, 토지취득률, 공시지가, 등기 증빙, PF 심사 연결 상태를 한 화면에서 점검합니다.
          </p>
        </div>
        <Link
          href="/analysis"
          className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
        >
          토지/거래 분석 실행
          <ArrowRight className="h-4 w-4" />
        </Link>
      </div>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <KpiCard icon={<Building2 className="h-5 w-5 text-sky-600" />} label="관리 프로젝트" value={`${projects.length}건`} sub="토지·사업성·PF 심사 대상" />
        <KpiCard icon={<MapPin className="h-5 w-5 text-emerald-600" />} label="PNU 확보" value={`${pnuCoverageCount}/${projects.length}`} sub="필지 식별 및 공적데이터 연동 기준" />
        <KpiCard icon={<BadgeCheck className="h-5 w-5 text-indigo-600" />} label="토지 검토 준비" value={`${landReadyCount}건`} sub="PNU·규제·취득·증빙 기준 4개 이상 통과" />
        <KpiCard icon={<AlertTriangle className="h-5 w-5 text-amber-600" />} label="확인 필요 이슈" value={`${evidenceIssueCount}개`} sub={`국공유지 감지 ${publicLandCount}건 포함`} />
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-4 flex items-start justify-between gap-3">
            <div>
              <h3 className="text-lg font-bold text-slate-950 dark:text-white">토지 분석 관제</h3>
              <p className="mt-1 text-sm text-slate-500">Land Agent가 PF 심사 전에 확인해야 하는 핵심 데이터입니다.</p>
            </div>
            <Link href="/dashboard" className="hidden items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 sm:inline-flex dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800">
              프로젝트별 보기
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <AgentCapability icon={<MapPin />} title="필지 식별 및 PNU" body="주소, 지번, 산번지, 본번/부번을 정규화하고 PNU 누락 및 매칭 신뢰도를 점검합니다." />
            <AgentCapability icon={<Ruler />} title="용도지역·건축가능성" body="용도지역, 법정/적용 건폐율·용적률, 예상 연면적과 인허가 검토 필요 항목을 표시합니다." />
            <AgentCapability icon={<Landmark />} title="토지취득률" body="필지 수, 면적, 매입금액, 계약금·중도금·잔금 지급 상태와 국공유지 여부를 요약합니다." />
            <AgentCapability icon={<ShieldCheck />} title="증빙·법무 준비상태" body="공시지가 기준연도, 등기부 증빙, 제한물권, 신탁·담보 설정 검토 필요 여부를 분리합니다." />
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <h3 className="text-lg font-bold text-slate-950 dark:text-white">PF 심사 연결 기준</h3>
          <div className="mt-4 space-y-3">
            {[
              ["법무검토 전제", "PNU, 지적특성, 공시지가, 등기부 증빙, 소유권·제한물권 검증이 필요합니다."],
              ["PF 심사 전제", "토지면적, 매입금액, 취득률, 잔금 지급일정이 사업수지와 대사되어야 합니다."],
              ["데이터 출처", "원본 업로드 파일, parser version, 공적데이터 기준일, evidence ID를 함께 보존합니다."],
            ].map(([title, body]) => (
              <div key={title} className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
                <p className="text-sm font-semibold text-slate-900 dark:text-white">{title}</p>
                <p className="mt-1 text-xs leading-5 text-slate-500">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h3 className="text-lg font-bold text-slate-950 dark:text-white">프로젝트별 토지 분석</h3>
            <p className="mt-1 text-sm text-slate-500">각 프로젝트의 토지 기본정보, 취득률, 증빙 준비상태를 바로 확인합니다.</p>
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {projects.map((project) => {
            const readiness = landReadiness(project);
            const projectId = project.project?.id;
            const href = projectId ? `/project/${projectId}/land` : "/analysis";
            return (
              <Link key={String(projectId ?? project.project?.name)} href={href} className="group rounded-lg border border-slate-200 p-4 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/70">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-bold text-slate-950 dark:text-white">{project.project?.name ?? "Unnamed Project"}</p>
                    <p className="mt-1 truncate text-sm text-slate-500">{project.land?.address ?? "소재지 미확인"}</p>
                  </div>
                  <span className={`shrink-0 rounded-md px-2 py-1 text-xs font-bold ${readiness.score >= 4 ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300" : "bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-300"}`}>
                    {readiness.score}/5
                  </span>
                </div>
                <div className="mt-4 grid grid-cols-2 gap-2">
                  <MiniStat label="PNU" value={readiness.pnuReady ? "확보" : "미확인"} />
                  <MiniStat label="대지면적" value={sqm(project.land?.area_sqm)} />
                  <MiniStat label="용도지역" value={project.regulation?.zoning ?? "미확인"} />
                  <MiniStat label="취득률" value={pct(project.landLedger?.landAcquisitionStatus?.acquisitionAreaRatio)} />
                  <MiniStat label="공시지가" value={readiness.officialPriceReady ? "확보" : "보완"} />
                  <MiniStat label="등기증빙" value={pct(project.landLedger?.pfSummary?.registryEvidenceCoverageRatio)} />
                </div>
                <div className="mt-3 flex items-center gap-1 text-xs font-semibold text-indigo-600 opacity-0 transition-opacity group-hover:opacity-100 dark:text-indigo-400">
                  토지 분석 상세 보기 <ArrowRight className="h-3 w-3" />
                </div>
              </Link>
            );
          })}
        </div>
      </section>
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

function AgentCapability({ icon, title, body }: { icon: ReactNode; title: string; body: string }) {
  return (
    <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200">
          {icon}
        </div>
        <p className="font-bold text-slate-950 dark:text-white">{title}</p>
      </div>
      <p className="mt-3 text-sm leading-6 text-slate-500">{body}</p>
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
