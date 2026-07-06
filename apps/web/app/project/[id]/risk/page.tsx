"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { AlertTriangle, ArrowLeft, CheckCircle2, Loader2, RefreshCw, ShieldCheck, XCircle } from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import {
  fetchDealStateRecords,
  hasSourceLocator,
  locatorSummary,
  type DealStateRecord,
} from "../../../../lib/deal-state-records";
import { displayText, fetchWorkspaceContext, type WorkspacePageContext } from "../../../../lib/workspace-context";

type ReviewStatus = "approved" | "waived" | "needs_revision" | "resolved";
type RiskAssessment = { overall?: Record<string, unknown> };

const RISK_CONTROLS = [
  "토지 소유권 및 매매계약",
  "지분합계 및 공유자 동의",
  "제한물권 말소",
  "국공유지·특수필지 처리",
  "인허가 일정",
  "용도지역·건축가능성",
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
  "상환·담보·Covenant",
  "EOD 및 법률문서 완결성",
  "미분양 LTV 및 Exit 시나리오",
];

function severityOf(record: DealStateRecord) {
  return String(record.severity ?? record.grade ?? record.riskLevel ?? "").toLowerCase();
}

function isClosed(record: DealStateRecord) {
  return ["approved", "waived", "resolved"].includes(String(record.reviewStatus ?? "").toLowerCase());
}

export default function RiskReviewPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [riskRecords, setRiskRecords] = useState<DealStateRecord[]>([]);
  const [assessment, setAssessment] = useState<RiskAssessment | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [reviewingId, setReviewingId] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState("credit_officer");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (runAssess = false) => {
    setLoading(true);
    setError(null);
    try {
      const ctxPromise = fetchWorkspaceContext(projectId, "pf");
      const risksPromise = fetchDealStateRecords(projectId, { recordType: "risk_issue", limit: 700, actor: "risk-reviewer" });
      const assessPromise = runAssess
        ? fetch(`${apiBase}/projects/${projectId}/risk/assess`, {
            method: "POST",
            cache: "no-store",
            headers: { ...authHeaders("risk-reviewer"), "Content-Type": "application/json" },
            body: "{}",
          }).then((res) => (res.ok ? res.json() : null))
        : Promise.resolve(null);
      const [ctx, risks, assessed] = await Promise.all([ctxPromise, risksPromise, assessPromise]);
      setContext(ctx);
      setRiskRecords(risks);
      setAssessment(assessed as RiskAssessment | null);
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
        headers: { ...authHeaders("risk-reviewer"), "Content-Type": "application/json" },
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

  async function reviewRisk(record: DealStateRecord, reviewStatus: ReviewStatus) {
    if (!record.recordId) return;
    setReviewingId(record.recordId);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/deal-state-records/${encodeURIComponent(record.recordId)}/review`, {
        method: "PATCH",
        headers: { ...authHeaders(reviewer || "credit_officer"), "Content-Type": "application/json" },
        body: JSON.stringify({
          reviewStatus,
          reviewedBy: reviewer || "credit_officer",
          comment: `Risk issue marked ${reviewStatus} from Risk Register screen.`,
          waiverReason: reviewStatus === "waived" ? "Reviewer waiver from Risk Register screen." : undefined,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null) as { detail?: string } | null;
        throw new Error(body?.detail ?? `Review update failed (${res.status})`);
      }
      await load(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "리스크 검토 상태 저장에 실패했습니다.");
    } finally {
      setReviewingId(null);
    }
  }

  const storedRegister = (context?.pageData?.riskRegister ?? {}) as { risks?: DealStateRecord[]; overallGrade?: string };
  const fallbackRisks = Array.isArray(storedRegister.risks) ? storedRegister.risks : [];
  const risks = riskRecords.length ? riskRecords : fallbackRisks;

  const summary = useMemo(() => {
    const critical = risks.filter((record) => severityOf(record).includes("critical")).length;
    const high = risks.filter((record) => severityOf(record).includes("high")).length;
    const unresolved = risks.filter((record) => !isClosed(record)).length;
    const locatorMissing = risks.filter((record) => !hasSourceLocator(record)).length;
    return { critical, high, unresolved, locatorMissing };
  }, [risks]);

  const projectName = displayText(context?.projectMemory?.projectName, `프로젝트 ${projectId}`);
  const overall = assessment?.overall ?? {};

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
              리스크 원장 / Risk Register
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              `deal_state_records`의 `risk_issue`를 기준으로 승인, waive, 보완요청, resolved 상태를 관리합니다.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="grid gap-1 text-xs font-bold text-slate-500">
            검토자
            <input value={reviewer} onChange={(event) => setReviewer(event.target.value)} className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-white" />
          </label>
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

          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
            <Kpi label="Risk rows" value={`${risks.length.toLocaleString("ko-KR")}건`} />
            <Kpi label="Critical" value={`${summary.critical.toLocaleString("ko-KR")}건`} tone={summary.critical ? "bad" : "good"} />
            <Kpi label="High" value={`${summary.high.toLocaleString("ko-KR")}건`} tone={summary.high ? "warn" : "good"} />
            <Kpi label="미해결" value={`${summary.unresolved.toLocaleString("ko-KR")}건`} tone={summary.unresolved ? "warn" : "good"} />
            <Kpi label="근거 위치 부족" value={`${summary.locatorMissing.toLocaleString("ko-KR")}건`} tone={summary.locatorMissing ? "warn" : "good"} />
          </section>

          <Panel title="Deal State 기반 리스크 검토">
            {risks.length ? (
              <div className="max-h-[720px] overflow-auto rounded-lg border border-slate-200 dark:border-slate-800">
                <table className="w-full min-w-[1180px] text-left text-xs">
                  <thead className="bg-slate-50 text-slate-500 dark:bg-slate-800">
                    <tr>
                      <th className="px-3 py-2">리스크</th>
                      <th className="px-3 py-2">등급</th>
                      <th className="px-3 py-2">검토상태</th>
                      <th className="px-3 py-2">출처</th>
                      <th className="px-3 py-2">근거 위치</th>
                      <th className="px-3 py-2">상태 처리</th>
                    </tr>
                  </thead>
                  <tbody>
                    {risks.map((risk, index) => (
                      <tr key={risk.recordId ?? `${risk.key}-${index}`} className="border-t border-slate-100 align-top dark:border-slate-800">
                        <td className="px-3 py-3">
                          <p className="font-black text-slate-950 dark:text-white">{displayText(risk.title ?? risk.item ?? risk.key, `Risk ${index + 1}`)}</p>
                          <p className="mt-1 max-w-xl text-slate-500">{displayText(risk.description ?? risk.mitigation, "설명 또는 완화방안이 아직 없습니다.")}</p>
                          <p className="mt-1 text-[11px] text-slate-400">{risk.recordId}</p>
                        </td>
                        <td className="px-3 py-3 font-bold text-slate-700 dark:text-slate-200">{displayText(risk.severity ?? risk.grade ?? risk.riskLevel, "-")}</td>
                        <td className={`px-3 py-3 font-bold ${isClosed(risk) ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"}`}>{displayText(risk.reviewStatus, "open")}</td>
                        <td className="px-3 py-3 text-slate-500">{displayText(risk.sourceType, "-")}</td>
                        <td className={`px-3 py-3 font-semibold ${hasSourceLocator(risk) ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"}`}>{locatorSummary(risk)}</td>
                        <td className="px-3 py-3">
                          <div className="flex flex-wrap gap-1">
                            <ActionButton label="승인" disabled={!risk.recordId || reviewingId === risk.recordId} onClick={() => void reviewRisk(risk, "approved")} />
                            <ActionButton label="Waive" disabled={!risk.recordId || reviewingId === risk.recordId} onClick={() => void reviewRisk(risk, "waived")} />
                            <ActionButton label="Resolved" disabled={!risk.recordId || reviewingId === risk.recordId} onClick={() => void reviewRisk(risk, "resolved")} />
                            <ActionButton label="보완요청" danger disabled={!risk.recordId || reviewingId === risk.recordId} onClick={() => void reviewRisk(risk, "needs_revision")} />
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty text="저장된 risk_issue 원장이 없습니다. PF-Credit Pod 또는 Risk 재평가를 실행하면 자동으로 적재됩니다." />
            )}
          </Panel>

          <section className="grid gap-4 xl:grid-cols-[1fr_0.8fr]">
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

            <Panel title="평가 요약">
              <div className="space-y-3">
                <Kpi label="종합 등급" value={displayText(overall.overall_grade ?? storedRegister.overallGrade, "미평가")} />
                <Kpi label="데이터 출처" value={riskRecords.length ? "deal_state_records" : fallbackRisks.length ? "Risk Register artifact" : "없음"} />
                <Kpi label="Source locator 완성도" value={risks.length ? `${Math.round(((risks.length - summary.locatorMissing) / risks.length) * 100)}%` : "-"} />
              </div>
            </Panel>
          </section>
        </>
      )}
    </div>
  );
}

function ActionButton({ label, disabled, danger, onClick }: { label: string; disabled?: boolean; danger?: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex h-8 items-center gap-1 rounded-md px-2 text-[11px] font-bold disabled:opacity-50 ${danger ? "bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-200" : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200"}`}
    >
      {danger ? <XCircle className="h-3 w-3" /> : <CheckCircle2 className="h-3 w-3" />}
      {label}
    </button>
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
  return <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900"><p className="text-sm font-semibold text-slate-500">{label}</p><p className={`mt-2 text-2xl font-black tracking-tight ${color}`}>{value}</p></div>;
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900"><div className="mb-4 flex items-center gap-2"><AlertTriangle className="h-5 w-5 text-slate-700 dark:text-slate-200" /><h2 className="text-lg font-black text-slate-950 dark:text-white">{title}</h2></div>{children}</section>;
}

function Empty({ text }: { text: string }) {
  return <div className="rounded-lg bg-amber-50 p-5 text-sm font-semibold text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">{text}</div>;
}
