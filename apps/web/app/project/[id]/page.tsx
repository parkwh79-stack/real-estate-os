import Link from "next/link";
import {
  ArrowLeft, MapPin, BarChart3, Wallet, Layers, ShieldCheck, FileText, Upload, GitBranch,
} from "lucide-react";
import FileUpload from "../../components/FileUpload";

interface PfTranche {
  amount?: number;
}

interface ProjectDetailResponse {
  project?: {
    name?: string;
  };
  land?: {
    address?: string;
  };
  feasibility_input?: {
    total_revenue?: number;
    total_cost?: number;
  };
  pf_structure?: PfTranche[];
}

function fmtEok(val: number) {
  const eok = val / 100_000_000;
  return `${eok.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}억`;
}

const steps = [
  { href: "land", label: "토지 분석", icon: MapPin, desc: "필지, 용도지역, 건폐율/용적률" },
  { href: "feasibility", label: "사업성 분석", icon: BarChart3, desc: "매출, 비용, IRR" },
  { href: "cashflow", label: "Cash Flow", icon: Wallet, desc: "월별 CF, Max Minus" },
  { href: "pf-structure", label: "PF 구조", icon: Layers, desc: "Tranche, LTV, LTC" },
  { href: "risk", label: "리스크 평가", icon: ShieldCheck, desc: "위험요인, 심사 점수" },
  { href: "orchestration", label: "AI 오케스트레이션", icon: GitBranch, desc: "Agent 실행, 산출물, 승인 흐름" },
  { href: "report", label: "심사 보고서", icon: FileText, desc: "PF 보고서 생성" },
];

export default async function ProjectDetailPage({ params }: { params: Promise<{ id: string }> | { id: string } }) {
  const resolvedParams = await params;
  let data: ProjectDetailResponse = {};
  try {
    const res = await fetch(`http://127.0.0.1:8000/projects/${resolvedParams.id}`, { cache: "no-store" });
    data = (await res.json()) as ProjectDetailResponse;
  } catch { /* ignore */ }

  const proj = data.project ?? {};
  const feas = data.feasibility_input ?? {};
  const pfTotal = (data.pf_structure ?? []).reduce((a, t) => a + (t.amount ?? 0), 0);
  const profit = (feas.total_revenue ?? 0) - (feas.total_cost ?? 0);

  return (
    <div className="space-y-6 pb-8">
      {/* Header */}
      <div className="flex items-start gap-3">
        <Link
          href="/dashboard"
          className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
          aria-label="프로젝트 목록으로"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div>
          <p className="text-sm font-semibold text-slate-500">프로젝트 #{resolvedParams.id}</p>
          <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            {proj.name || "Unnamed Project"}
          </h2>
          <p className="mt-1 text-sm text-slate-500">{data.land?.address ?? "—"}</p>
        </div>
      </div>

      {/* KPI Cards */}
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Kpi label="총매출" value={feas.total_revenue ? fmtEok(feas.total_revenue) : "—"} />
        <Kpi label="총사업비" value={feas.total_cost ? fmtEok(feas.total_cost) : "—"} />
        <Kpi label="PF 금액" value={pfTotal > 0 ? fmtEok(pfTotal) : "—"} />
        <Kpi label="사업이익" value={profit !== 0 ? fmtEok(profit) : "—"} tone={profit > 0 ? "good" : "bad"} />
      </section>

      {/* File Upload */}
      <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center gap-3 mb-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-50 dark:bg-indigo-950/30">
            <Upload className="h-5 w-5 text-indigo-600" />
          </div>
          <div>
            <h3 className="font-bold text-slate-950 dark:text-white">사업계획서 업로드</h3>
            <p className="text-xs text-slate-500">PDF를 업로드하면 AI가 자동으로 사업 데이터를 추출합니다</p>
          </div>
        </div>
        <FileUpload projectId={resolvedParams.id} />
      </div>

      {/* Analysis Steps */}
      <section>
        <h3 className="mb-4 text-lg font-bold text-slate-950 dark:text-white">분석 단계</h3>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {steps.map((s, i) => {
            const Icon = s.icon;
            return (
              <Link
                key={s.href}
                href={`/project/${resolvedParams.id}/${s.href}`}
                className="group flex items-center gap-4 rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition-all hover:border-slate-300 hover:shadow-md dark:border-slate-800 dark:bg-slate-900 dark:hover:border-slate-700"
              >
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-600 transition-colors group-hover:bg-indigo-50 group-hover:text-indigo-600 dark:bg-slate-800 dark:text-slate-300">
                  <Icon className="h-5 w-5" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-bold text-slate-950 dark:text-white">
                    <span className="mr-1 text-slate-400">{i + 1}.</span>{s.label}
                  </p>
                  <p className="mt-0.5 text-xs text-slate-500">{s.desc}</p>
                </div>
              </Link>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: "good" | "bad" }) {
  const color = tone === "good" ? "text-emerald-600 dark:text-emerald-300" : tone === "bad" ? "text-rose-600 dark:text-rose-300" : "text-slate-950 dark:text-white";
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-sm font-semibold text-slate-500">{label}</p>
      <p className={`mt-3 text-2xl font-black tracking-tight ${color}`}>{value}</p>
    </div>
  );
}
