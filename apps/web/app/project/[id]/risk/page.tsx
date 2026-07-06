"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { AlertTriangle, ArrowLeft, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import { displayText, fetchWorkspaceContext, type WorkspacePageContext } from "../../../../lib/workspace-context";

type RiskItem = {
  riskId?: string;
  code?: string;
  category?: string;
  item?: string;
  title?: string;
  description?: string;
  severity?: string | number;
  probability?: string | number;
  score?: number;
  grade?: string;
  mitigation?: string;
  reviewStatus?: string;
  evidenceIds?: string[];
  sourceLocators?: unknown[];
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

const RISK_CONTROLS = [
  "토지 소유권 및 매매계약",
  "지분합계 및 공유자 동의",
  "제한물권 말소",
  "국공유지·특수필지 처리",
  "인허가 일정",
  "용도지역·건폐율·용적률 적합성",
  "시행사 재무 및 신용도",
  "시공사 책임준공 및 채무인수",
  "공사비 증액 가능성",
  "시장 수요 및 경쟁공급",
  "분양가 적정성",
  "분양률 Trigger 및 할인분양",
  "PF 조달구조 및 Tranche",
  "LTV/LTC/DSCR/Exit 기준",
  "금리·수수료·All-in Cost",
  "Cash Flow 및 Max Minus",
  "자금집행 Waterfall",
  "신탁·담보·Covenant",
  "EOD 및 법률문서 완결성",
  "미분양 LTV 및 Exit 시나리오",
];

export default function RiskReviewPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [riskAssessment, setRiskAssessment] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (runAssess = false) => {
    setLoading(true);
    setError(null);
    try {
      const ctxPromise = fetchWorkspaceContext(projectId, "pf");
      const riskPromise = runAssess
        ? fetch(`${apiBase}/projects/${projectId}/risk/assess`, {
            method: "POST",
            cache: "no-store",
            headers: { ...authHeaders("web"), "Content-Type": "application/json" },
            body: "{}",
          }).then((res) => (res.ok ? res.json() : null))
        : Promise.resolve(null);
      const [ctx, assessed] = await Promise.all([ctxPromise, riskPromise]);
      setContext(ctx);
      setRiskAssessment((assessed as Record<string, unknown> | null) ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "리스크 원장을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load(false);
  }, [load]);

  async function runPfPod() {
    setRunning(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/chief-orchestration/run-pod`, {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ podKey: "pf_credit" }),
      });
      if (!res.ok) throw new Error(`PF-Credit Pod 실행 실패 (${res.status})`);
      await load(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "PF-Credit Pod 실행에 실패했습니다.");
    } finally {
      setRunning(false);
    }
  }

  const pageData = asRecord(context?.pageData);
  const storedRiskRegister = asRecord(pageData.riskRegister);
  const assessmentItems = asArray<RiskItem>(riskAssessment?.items);
  const registerItems = asArray<RiskItem>(storedRiskRegister.risks);
  const risks = registerItems.length ? registerItems : assessmentItems;
  const overall = asRecord(riskAssessment?.overall);
  const projectName = displayText(context?.projectMemory?.projectName, `프로젝트 ${projectId}`);
  const criticalCount = risks.filter((risk) => String(risk.severity ?? risk.grade ?? "").toLowerCase().includes("critical")).length;
  const unresolvedCount = risks.filter((risk) => !["approved", "waived", "resolved"].includes(String(risk.reviewStatus ?? "").toLowerCase())).length;

  return (
    <div className="space-y-6 pb-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <Link href={`/project/${projectId}`} className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" aria-label="프로젝트로 돌아가기">
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <p className="text-sm font-semibold text-slate-500">{projectName}</p>
            <h1 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
              <ShieldCheck className="h-7 w-7 text-indigo-600" />
              Risk Register
            </h1>
            <p className="mt-1 text-sm text-slate-500">Land, Marketability, Cash Flow, PF-Credit Pod에서 나온 리스크를 한 화면에서 검토합니다.</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => void load(true)} disabled={loading || running} className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-bold text-slate-700 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200">
            <RefreshCw className="h-4 w-4" />
            Risk 재평가
          </button>
          <button type="button" onClick={() => void runPfPod()} disabled={loading || running} className="inline-flex h-10 items-center gap-2 rounded-lg bg-indigo-600 px-4 text-sm font-bold text-white disabled:opacity-50">
            {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
            PF-Credit Pod 실행
          </button>
        </div>
      </header>

      {loading ? (
        <Loading text="리스크 원장을 불러오는 중입니다." />
      ) : (
        <>
          {error ? <Notice>{error}</Notice> : null}
          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Kpi label="저장 Risk rows" value={`${risks.length.toLocaleString("ko-KR")}건`} />
            <Kpi label="Critical" value={`${criticalCount.toLocaleString("ko-KR")}건`} tone={criticalCount ? "bad" : "good"} />
            <Kpi label="미검토/미해소" value={`${unresolvedCount.toLocaleString("ko-KR")}건`} tone={unresolvedCount ? "warn" : "good"} />
            <Kpi label="종합 등급" value={displayText(overall.overall_grade ?? storedRiskRegister.overallGrade, "미평가")} />
          </section>

          <Panel title="PF 심사 리스크 관리 20개 항목">
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
              {RISK_CONTROLS.map((item, index) => (
                <div key={item} className="rounded-lg border border-slate-200 p-3 text-sm dark:border-slate-800">
                  <p className="text-xs font-black text-slate-400">{String(index + 1).padStart(2, "0")}</p>
                  <p className="mt-1 font-bold text-slate-950 dark:text-white">{item}</p>
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="저장된 Risk Register rows">
            {risks.length ? (
              <div className="space-y-3">
                {risks.slice(0, 80).map((risk, index) => (
                  <div key={`${risk.riskId ?? risk.code ?? index}`} className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <p className="text-xs font-bold text-slate-500">{displayText(risk.category, "Risk")}</p>
                        <h3 className="mt-1 font-black text-slate-950 dark:text-white">{displayText(risk.title ?? risk.item ?? risk.code, `Risk ${index + 1}`)}</h3>
                      </div>
                      <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-bold text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                        {displayText(risk.reviewStatus, "open")}
                      </span>
                    </div>
                    <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-300">{displayText(risk.description ?? risk.mitigation, "설명 또는 완화방안이 아직 없습니다.")}</p>
                    <div className="mt-3 grid gap-2 text-xs text-slate-500 md:grid-cols-4">
                      <p>Severity: {displayText(risk.severity, "미확인")}</p>
                      <p>Probability: {displayText(risk.probability, "미확인")}</p>
                      <p>Score: {risk.score ?? "미확인"}</p>
                      <p>Evidence: {risk.evidenceIds?.length ?? 0}건</p>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <Empty text="저장된 Risk Register가 없습니다. PF-Credit Pod 또는 Risk 재평가를 실행하세요." />
            )}
          </Panel>
        </>
      )}
    </div>
  );
}

function Loading({ text }: { text: string }) {
  return <div className="flex min-h-[240px] items-center justify-center rounded-lg border border-slate-200 bg-white text-sm font-bold text-slate-500 dark:border-slate-800 dark:bg-slate-900"><Loader2 className="mr-2 h-5 w-5 animate-spin" />{text}</div>;
}
function Notice({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">{children}</div>;
}
function Kpi({ label, value, tone }: { label: string; value: string; tone?: "good" | "warn" | "bad" }) {
  const color = tone === "good" ? "text-emerald-600" : tone === "warn" ? "text-amber-600" : tone === "bad" ? "text-rose-600" : "text-slate-950 dark:text-white";
  return <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900"><p className="text-sm font-semibold text-slate-500">{label}</p><p className={`mt-3 text-2xl font-black tracking-tight ${color}`}>{value}</p></div>;
}
function Panel({ title, children }: { title: string; children: ReactNode }) {
  return <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900"><div className="mb-4 flex items-center gap-2"><AlertTriangle className="h-5 w-5 text-slate-700 dark:text-slate-200" /><h2 className="text-lg font-black text-slate-950 dark:text-white">{title}</h2></div>{children}</section>;
}
function Empty({ text }: { text: string }) {
  return <div className="rounded-lg bg-amber-50 p-5 text-sm font-semibold text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">{text}</div>;
}
