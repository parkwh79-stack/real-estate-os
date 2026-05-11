import Link from "next/link";
import type { ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  FileText,
  Landmark,
  MapPin,
  TrendingUp,
} from "lucide-react";
import {
  demoProject,
  formatEok,
  getMaxMinusPoint,
  projects,
} from "../lib/platform-data";

const riskTone = {
  Low: "text-emerald-600 bg-emerald-50 border-emerald-200 dark:bg-emerald-950/30 dark:border-emerald-900 dark:text-emerald-300",
  Medium:
    "text-amber-700 bg-amber-50 border-amber-200 dark:bg-amber-950/30 dark:border-amber-900 dark:text-amber-300",
  High: "text-rose-700 bg-rose-50 border-rose-200 dark:bg-rose-950/30 dark:border-rose-900 dark:text-rose-300",
};

export default function DashboardPage() {
  const maxMinus = getMaxMinusPoint();
  const approved = projects.filter((project) => project.status === "조건부 승인").length;
  const averageScore = Math.round(
    projects.reduce((sum, project) => sum + project.pfScore, 0) / projects.length,
  );
  const totalLoan = projects.reduce((sum, project) => sum + project.totalLoan, 0);

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-500">REALYTICS x PLATFORM9</p>
          <h2 className="mt-1 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
            PF 심사 운영 대시보드
          </h2>
        </div>
        <Link
          href="/analysis"
          className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
        >
          신규 프로젝트 분석
          <ArrowRight className="h-4 w-4" />
        </Link>
      </div>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label="진행 프로젝트"
          value={`${projects.length}건`}
          sub={`${approved}건 조건부 승인`}
          icon={<Activity className="h-5 w-5 text-sky-600" />}
        />
        <KpiCard
          label="평균 PF 가능성"
          value={`${averageScore}점`}
          sub="Alpha scoring model"
          icon={<CheckCircle2 className="h-5 w-5 text-emerald-600" />}
        />
        <KpiCard
          label="총 PF 검토 금액"
          value={formatEok(totalLoan, 0)}
          sub="선순위/중순위/후순위 합산"
          icon={<Landmark className="h-5 w-5 text-violet-600" />}
        />
        <KpiCard
          label="Max Minus"
          value={formatEok(Math.abs(maxMinus.value))}
          sub={`${maxMinus.month} 최저 누적 CF`}
          icon={<AlertTriangle className="h-5 w-5 text-amber-600" />}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.4fr_0.9fr]">
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-bold text-slate-950 dark:text-white">
                최근 분석 프로젝트
              </h3>
              <p className="text-sm text-slate-500">PF 점수, 리스크, 업데이트 상태</p>
            </div>
            <Link
              href="/report"
              className="hidden items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 sm:inline-flex dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              <FileText className="h-4 w-4" />
              보고서 보기
            </Link>
          </div>

          <div className="space-y-3">
            {projects.map((project) => (
              <Link
                key={project.id}
                href="/analysis"
                className="grid gap-3 rounded-lg border border-slate-200 p-4 transition-colors hover:bg-slate-50 md:grid-cols-[1fr_auto] dark:border-slate-800 dark:hover:bg-slate-800/70"
              >
                <div className="flex min-w-0 gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800">
                    <MapPin className="h-5 w-5 text-slate-600 dark:text-slate-300" />
                  </div>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="truncate font-bold text-slate-950 dark:text-white">
                        {project.name}
                      </p>
                      <span
                        className={`rounded-md border px-2 py-0.5 text-xs font-semibold ${riskTone[project.risk]}`}
                      >
                        {project.risk}
                      </span>
                    </div>
                    <p className="mt-1 truncate text-sm text-slate-500">
                      {project.address}
                    </p>
                    <p className="mt-1 font-mono text-xs text-slate-400">
                      PNU {project.pnu}
                    </p>
                  </div>
                </div>
                <div className="flex items-center justify-between gap-5 md:justify-end">
                  <Metric label="PF 점수" value={`${project.pfScore}점`} />
                  <Metric label="검토 금액" value={formatEok(project.totalLoan, 0)} />
                  <Metric label="상태" value={project.status} />
                </div>
              </Link>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">
                <TrendingUp className="h-5 w-5" />
              </div>
              <div>
                <h3 className="font-bold text-slate-950 dark:text-white">
                  오늘의 기준 프로젝트
                </h3>
                <p className="text-sm text-slate-500">{demoProject.id}</p>
              </div>
            </div>
            <div className="mt-5 grid grid-cols-2 gap-3">
              <MetricBox label="총매출" value={formatEok(demoProject.totalRevenue)} />
              <MetricBox label="총사업비" value={formatEok(demoProject.totalCost)} />
              <MetricBox label="PF 조달" value={formatEok(demoProject.totalLoan, 0)} />
              <MetricBox
                label="사업이익"
                value={formatEok(demoProject.totalRevenue - demoProject.totalCost)}
              />
            </div>
          </div>

          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <h3 className="font-bold text-slate-950 dark:text-white">다음 작업 큐</h3>
            <div className="mt-4 space-y-3">
              {[
                ["토지 규제 확인", "용도지역/건폐율/용적률 체크"],
                ["CF 민감도 업데이트", "분양률 50/70/90% 회수 시나리오"],
                ["PF 보고서 출력", "조건부 승인 의견 및 EOD 정리"],
              ].map(([title, body]) => (
                <div
                  key={title}
                  className="rounded-lg border border-slate-200 p-3 dark:border-slate-800"
                >
                  <p className="text-sm font-semibold text-slate-900 dark:text-white">
                    {title}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">{body}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

function KpiCard({
  label,
  value,
  sub,
  icon,
}: {
  label: string;
  value: string;
  sub: string;
  icon: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-slate-500">{label}</p>
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-50 dark:bg-slate-800">
          {icon}
        </div>
      </div>
      <p className="mt-4 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
        {value}
      </p>
      <p className="mt-1 text-sm text-slate-500">{sub}</p>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-[82px] text-right">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-0.5 text-sm font-bold text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}

function MetricBox({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-black text-slate-950 dark:text-white">{value}</p>
    </div>
  );
}
