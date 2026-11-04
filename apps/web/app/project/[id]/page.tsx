"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  Database,
  FileSearch,
  FileText,
  GitBranch,
  Landmark,
  Loader2,
  MapPin,
  ShieldCheck,
  Upload,
  Wallet,
} from "lucide-react";
import { displayText, fetchWorkspaceContext, type WorkspacePageContext } from "../../../lib/workspace-context";
import { useProjectName } from "../../../components/project/project-name-editor";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function count(value: unknown) {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
}

const MENU = [
  { href: "input-package", label: "자료 입력", desc: "사업계획서, 사업수지 Excel, 토지조서, 공시지가·등기 증빙 업로드", icon: Upload },
  { href: "chief-orchestration", label: "AI 딜룸", desc: "트랙별 Task Graph, Pod 실행, 재계산 이벤트, Risk Committee", icon: GitBranch },
  { href: "land", label: "토지 원장", desc: "필지, PNU, 공시지가, 등기/제한물권 증빙 상태", icon: Landmark },
  { href: "marketability", label: "시장·분양성", desc: "실거래가, 비교사례, 제안 분양가, 흡수율·Exit 근거", icon: BarChart3 },
  { href: "feasibility", label: "사업성", desc: "사업수지, 개발이익, 사업비 구조, FeasibilityStudyV2", icon: FileText },
  { href: "cashflow", label: "Cash Flow", desc: "월별 CF, Max Minus, PF Draw/Repayment, Excel formula export", icon: Wallet },
  { href: "pf-structure", label: "금융구조", desc: "Tranche, LTV/LTC, Waterfall, Cure scenario", icon: ShieldCheck },
  { href: "evidence", label: "근거 그래프", desc: "문서 페이지, Excel 셀, API row, Agent claim 연결", icon: FileSearch },
];

export default function ProjectDetailPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setContext(await fetchWorkspaceContext(projectId, "overview"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "프로젝트 원장을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const pageData = asRecord(context?.pageData);
  const sourceSummary = asRecord(pageData.sourceDocumentSummary);
  const landLedgerSummary = asRecord(pageData.landLedgerSummary);
  const landEvidenceSummary = asRecord(pageData.landEvidenceSummary);
  const chiefArtifactSummary = asRecord(pageData.chiefArtifactSummary);
  const missingInputs = asArray<Record<string, unknown>>(context?.missingInputRegister);
  const pods = asArray<Record<string, unknown>>(context?.agentPods);
  const podRuns = asArray<Record<string, unknown>>(context?.podRuns);
  const runByPod = useMemo(() => new Map(podRuns.map((run) => [String(run.podKey ?? run.pod_key), run])), [podRuns]);
  const projectName = useProjectName(projectId, displayText(context?.projectMemory?.projectName, `프로젝트 ${projectId}`));
  const workflowTrack = displayText(context?.workflowTrack, "트랙 미선택");
  const decision = context?.decisionGate?.decision || context?.decisionGate?.status || "검토 전";

  const parcelCount = count(landLedgerSummary.parcelCount);
  const pnuCount = count(landLedgerSummary.pnuCount);
  const evidenceCount = count(landEvidenceSummary.count);
  const sourceDocumentCount = count(sourceSummary.count);
  const artifactCount = count(chiefArtifactSummary.count);

  return (
    <div className="space-y-6 pb-8">
      <header className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-xs font-black uppercase tracking-wide text-indigo-600 dark:text-indigo-300">REALYTICS Shared Deal State</p>
            <h1 className="mt-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">{projectName}</h1>
            <p className="mt-2 text-sm font-semibold text-slate-500">
              이 프로젝트 홈은 DB/상태 저장소의 Shared Deal State Graph를 기준으로 화면을 구성합니다.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href={`/project/${projectId}/input-package`} className="inline-flex h-10 items-center gap-2 rounded-lg bg-indigo-600 px-4 text-sm font-bold text-white">
              <Upload className="h-4 w-4" />
              자료 보강
            </Link>
            <Link href={`/project/${projectId}/chief-orchestration`} className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-bold text-slate-700 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200">
              <GitBranch className="h-4 w-4" />
              AI 딜룸
            </Link>
          </div>
        </div>
      </header>

      {loading ? (
        <div className="flex min-h-[240px] items-center justify-center rounded-lg border border-slate-200 bg-white text-sm font-bold text-slate-500 dark:border-slate-800 dark:bg-slate-900">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          프로젝트 기억을 불러오는 중입니다.
        </div>
      ) : error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">
          {error}
        </div>
      ) : (
        <>
          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
            <Kpi label="저장 자료" value={`${sourceDocumentCount.toLocaleString("ko-KR")}건`} icon={<FileText />} />
            <Kpi label="토지조서 필지" value={`${parcelCount.toLocaleString("ko-KR")}필지`} icon={<Landmark />} />
            <Kpi label="PNU 확보" value={`${pnuCount}/${parcelCount || 0}`} icon={<MapPin />} tone={parcelCount > 0 && pnuCount === parcelCount ? "good" : "warn"} />
            <Kpi label="증빙 레코드" value={`${evidenceCount.toLocaleString("ko-KR")}건`} icon={<Database />} tone={evidenceCount > 0 ? "good" : "warn"} />
            <Kpi label="Pod artifact" value={`${artifactCount.toLocaleString("ko-KR")}건`} icon={<GitBranch />} />
          </section>

          <section className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
            <Panel title="현재 심사 상태">
              <InfoRow label="선택 트랙" value={workflowTrack} />
              <InfoRow label="승인 Gate" value={displayText(decision)} />
              <InfoRow label="공시지가" value={landEvidenceSummary.hasOfficialLandPrice ? "확보" : "보강 필요"} />
              <InfoRow label="등기/제한물권" value={landEvidenceSummary.hasRegistryEvidence ? "확보" : "보강 필요"} />
              <InfoRow label="보완 필요 항목" value={`${missingInputs.length.toLocaleString("ko-KR")}건`} />
            </Panel>

            <Panel title="다음 권장 작업">
              {missingInputs.length ? (
                <div className="space-y-2">
                  {missingInputs.slice(0, 5).map((item, index) => (
                    <div key={index} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                      <p className="font-black">{displayText(item.title ?? item.label ?? item.code, `보완 항목 ${index + 1}`)}</p>
                      <p className="mt-1 text-xs leading-5">{displayText(item.reason ?? item.description ?? item.message, "에이전트가 다음 단계 실행 전 보완이 필요하다고 판단했습니다.")}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm font-bold text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100">
                  현재 공통 원장 기준으로 즉시 표시되는 보완 항목은 없습니다. AI 딜룸에서 Pod를 실행해 최신 상태를 재평가하세요.
                </div>
              )}
            </Panel>
          </section>

          <Panel title="Agent Pod 상태">
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {pods.map((pod) => {
                const podKey = String(pod.podKey ?? pod.pod_key ?? pod.key ?? "");
                const run = runByPod.get(podKey);
                return (
                  <div key={podKey || String(pod.label)} className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
                    <div className="flex items-center justify-between gap-2">
                      <p className="font-black text-slate-950 dark:text-white">{displayText(pod.label ?? pod.name ?? podKey, podKey)}</p>
                      <PodStatus status={String(run?.status ?? pod.status ?? "not_started")} />
                    </div>
                    <p className="mt-2 text-xs leading-5 text-slate-500">{displayText(pod.mission ?? pod.description, "프로젝트 원장을 읽고 전용 분석 artifact를 생성합니다.")}</p>
                  </div>
                );
              })}
            </div>
          </Panel>

          <section>
            <h2 className="mb-3 text-lg font-black text-slate-950 dark:text-white">업무 화면</h2>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              {MENU.map((item) => {
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={`/project/${projectId}/${item.href}`}
                    className="group rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition-all hover:border-indigo-300 hover:shadow-md dark:border-slate-800 dark:bg-slate-900"
                  >
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-100 text-slate-700 group-hover:bg-indigo-50 group-hover:text-indigo-700 dark:bg-slate-800 dark:text-slate-200">
                      <Icon className="h-5 w-5" />
                    </div>
                    <p className="mt-3 font-black text-slate-950 dark:text-white">{item.label}</p>
                    <p className="mt-1 text-xs leading-5 text-slate-500">{item.desc}</p>
                    <span className="mt-3 inline-flex items-center gap-1 text-xs font-bold text-indigo-600">
                      열기 <ArrowRight className="h-3.5 w-3.5" />
                    </span>
                  </Link>
                );
              })}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function Kpi({ label, value, icon, tone }: { label: string; value: string; icon: ReactNode; tone?: "good" | "warn" }) {
  const toneClass = tone === "good" ? "text-emerald-600 dark:text-emerald-300" : tone === "warn" ? "text-amber-600 dark:text-amber-300" : "text-slate-950 dark:text-white";
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2 text-xs font-bold text-slate-500">
        <span className="h-4 w-4">{icon}</span>
        {label}
      </div>
      <p className={`mt-3 text-2xl font-black ${toneClass}`}>{value}</p>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <h2 className="mb-4 text-lg font-black text-slate-950 dark:text-white">{title}</h2>
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

function PodStatus({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const done = normalized.includes("completed");
  const blocked = normalized.includes("blocked") || normalized.includes("failed");
  const label = done ? "완료" : blocked ? "차단" : normalized === "not_started" ? "대기" : displayText(status);
  const klass = done
    ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200"
    : blocked
      ? "bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-200"
      : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200";
  return (
    <span className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-bold ${klass}`}>
      {done ? <CheckCircle2 className="h-3.5 w-3.5" /> : blocked ? <AlertTriangle className="h-3.5 w-3.5" /> : null}
      {label}
    </span>
  );
}
