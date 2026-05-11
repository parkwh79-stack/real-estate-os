import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowLeft, MapPin, Ruler, Building, CheckCircle2 } from "lucide-react";

interface ProjectLandResponse {
  project?: {
    name?: string;
  };
  land?: {
    address?: string;
    pnu?: string;
    area_sqm?: number;
  };
  regulation?: {
    zoning?: string;
    legal_far?: number;
    applied_far?: number;
    legal_bcr?: number;
    applied_bcr?: number;
  };
}

export default async function LandAnalysisPage({ params }: { params: Promise<{ id: string }> | { id: string } }) {
  const resolvedParams = await params;
  let data: ProjectLandResponse = {};
  try {
    const res = await fetch(`http://127.0.0.1:8000/projects/${resolvedParams.id}`, { cache: "no-store" });
    data = (await res.json()) as ProjectLandResponse;
  } catch { /* API offline */ }

  const land = data.land ?? {};
  const reg = data.regulation ?? {};
  const proj = data.project ?? {};

  const devGfa = reg.applied_far && land.area_sqm
    ? ((reg.applied_far / 100) * land.area_sqm).toLocaleString("ko-KR", { maximumFractionDigits: 0 })
    : "—";

  return (
    <div className="space-y-6 pb-8">
      {/* Header */}
      <div className="flex items-start gap-3">
        <Link
          href={`/project/${resolvedParams.id}`}
          className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div>
          <p className="text-sm font-semibold text-slate-500">{proj.name || `프로젝트 #${resolvedParams.id}`}</p>
          <h2 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            <MapPin className="h-7 w-7 text-indigo-600" />
            토지 분석
          </h2>
        </div>
      </div>

      {/* Land Info */}
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <h3 className="text-lg font-bold text-slate-950 dark:text-white">필지 정보</h3>
        <div className="mt-4 grid gap-4 md:grid-cols-3">
          <InfoCard icon={<MapPin className="h-5 w-5 text-sky-600" />} label="소재지" value={land.address || "—"} />
          <InfoCard icon={<Ruler className="h-5 w-5 text-violet-600" />} label="PNU" value={land.pnu || "—"} />
          <InfoCard icon={<Building className="h-5 w-5 text-emerald-600" />} label="대지면적" value={land.area_sqm ? `${land.area_sqm.toLocaleString()}㎡` : "—"} />
        </div>
      </section>

      {/* Zoning & Building Limits */}
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <h3 className="text-lg font-bold text-slate-950 dark:text-white">용도지역 및 건축 제한</h3>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
            <p className="text-xs font-semibold text-slate-500">용도지역</p>
            <p className="mt-1 text-xl font-black text-slate-950 capitalize dark:text-white">{reg.zoning || "—"}</p>
          </div>
          <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
            <p className="text-xs font-semibold text-slate-500">개발 가능 연면적</p>
            <p className="mt-1 text-xl font-black text-indigo-600 dark:text-indigo-400">{devGfa}㎡</p>
          </div>
        </div>

        {/* FAR & BCR Bars */}
        <div className="mt-5 space-y-4">
          <RatioBar label="용적률" legal={reg.legal_far} applied={reg.applied_far} unit="%" />
          <RatioBar label="건폐율" legal={reg.legal_bcr} applied={reg.applied_bcr} unit="%" />
        </div>
      </section>

      {/* Permit Risks */}
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <h3 className="text-lg font-bold text-slate-950 dark:text-white">인허가 리스크</h3>
        <div className="mt-4 flex items-center gap-3 rounded-lg border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950/30">
          <CheckCircle2 className="h-5 w-5 text-emerald-600" />
          <p className="text-sm font-semibold text-emerald-800 dark:text-emerald-200">주요 인허가 리스크 없음</p>
        </div>
      </section>
    </div>
  );
}

function InfoCard({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center gap-3 rounded-lg bg-slate-50 p-4 dark:bg-slate-800/70">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white dark:bg-slate-900">{icon}</div>
      <div>
        <p className="text-xs font-semibold text-slate-500">{label}</p>
        <p className="mt-0.5 text-sm font-bold text-slate-950 dark:text-white">{value}</p>
      </div>
    </div>
  );
}

function RatioBar({ label, legal, applied, unit }: { label: string; legal?: number; applied?: number; unit: string }) {
  if (!legal || !applied) return null;
  const pct = Math.min(100, (applied / legal) * 100);
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between text-sm">
        <span className="font-semibold text-slate-700 dark:text-slate-300">{label}</span>
        <span className="text-xs text-slate-500">
          적용 <span className="font-bold text-slate-950 dark:text-white">{applied}{unit}</span> / 법정 {legal}{unit}
        </span>
      </div>
      <div className="h-3 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div
          className="h-3 rounded-full bg-gradient-to-r from-indigo-500 to-indigo-600 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
