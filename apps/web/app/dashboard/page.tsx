import Link from "next/link";
import type { ReactNode } from "react";
import {
  Activity, ArrowRight, Building2, Briefcase, MapPin,
} from "lucide-react";

const statusStyle: Record<string, string> = {
  pf_review: "bg-amber-100 text-amber-800 border-amber-200 dark:bg-amber-950/30 dark:text-amber-300 dark:border-amber-900",
  approved: "bg-emerald-100 text-emerald-800 border-emerald-200 dark:bg-emerald-950/30 dark:text-emerald-300 dark:border-emerald-900",
  rejected: "bg-rose-100 text-rose-800 border-rose-200 dark:bg-rose-950/30 dark:text-rose-300 dark:border-rose-900",
};

const statusLabel: Record<string, string> = {
  pf_review: "PF 검토중",
  approved: "승인",
  rejected: "보류",
};

const typeLabel: Record<string, string> = {
  mixed_use: "주상복합",
  logistics: "물류센터",
  residential: "공동주택",
  office: "오피스",
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
  };
  pf_structure?: PfTranche[];
}

function fmtEok(val: number) {
  const eok = val / 100_000_000;
  return eok >= 10000
    ? `${(eok / 10000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}조`
    : `${eok.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}억`;
}

export default async function DashboardPage() {
  let projects: ProjectSummaryResponse[] = [];
  try {
    const res = await fetch("http://127.0.0.1:8000/projects", { cache: "no-store" });
    projects = (await res.json()) as ProjectSummaryResponse[];
  } catch {
    /* API offline */
  }

  const total = projects.length;
  const reviewing = projects.filter((p) => p.project?.status === "pf_review").length;
  const totalPf = projects.reduce((s, p) => {
    const pfs = p.pf_structure ?? [];
    return s + pfs.reduce((a, t) => a + (t.amount ?? 0), 0);
  }, 0);

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-500">REALYTICS × PLATFORM9</p>
          <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            프로젝트 관리
          </h2>
        </div>
        <Link
          href="/analysis"
          className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
        >
          신규 프로젝트 <ArrowRight className="h-4 w-4" />
        </Link>
      </div>

      {/* KPI */}
      <section className="grid gap-4 md:grid-cols-3">
        <KpiCard icon={<Briefcase className="h-5 w-5 text-sky-600" />} label="전체 프로젝트" value={`${total}건`} sub={`${reviewing}건 PF 검토중`} />
        <KpiCard icon={<Activity className="h-5 w-5 text-violet-600" />} label="총 PF 검토 금액" value={fmtEok(totalPf)} sub="전 프로젝트 합산" />
        <KpiCard icon={<Building2 className="h-5 w-5 text-emerald-600" />} label="자산 유형" value={`${new Set(projects.map((p) => p.project?.type)).size}개`} sub="물류/주상복합/주거 등" />
      </section>

      {/* Project Cards */}
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
            const projectTypeLabel = projectType ? (typeLabel[projectType] ?? projectType) : "—";
            return (
              <Link
                key={proj.id}
                href={`/project/${proj.id}`}
                className="group rounded-lg border border-slate-200 bg-white p-5 shadow-sm transition-all hover:border-slate-300 hover:shadow-md dark:border-slate-800 dark:bg-slate-900 dark:hover:border-slate-700"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800">
                      <MapPin className="h-5 w-5 text-slate-600 dark:text-slate-300" />
                    </div>
                    <div className="min-w-0">
                      <p className="truncate font-bold text-slate-950 dark:text-white">{proj.name || "Unnamed"}</p>
                      <p className="mt-0.5 text-xs text-slate-500">{p.land?.address ?? "—"}</p>
                    </div>
                  </div>
                  <span className={`shrink-0 rounded-md border px-2 py-0.5 text-xs font-semibold ${statusStyle[status] ?? statusStyle.pf_review}`}>
                    {statusLabel[status] ?? status}
                  </span>
                </div>
                <div className="mt-4 grid grid-cols-3 gap-2">
                  <MiniStat label="유형" value={projectTypeLabel} />
                  <MiniStat label="PF 금액" value={pfTotal > 0 ? fmtEok(pfTotal) : "—"} />
                  <MiniStat label="심의일" value={proj.review_date ?? "—"} />
                </div>
                <div className="mt-3 flex items-center gap-1 text-xs font-semibold text-indigo-600 opacity-0 transition-opacity group-hover:opacity-100 dark:text-indigo-400">
                  상세 보기 <ArrowRight className="h-3 w-3" />
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

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-2 dark:bg-slate-800/70">
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <p className="mt-0.5 text-sm font-bold text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}
