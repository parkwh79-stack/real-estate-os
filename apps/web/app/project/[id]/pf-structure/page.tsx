"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ArrowLeft, Landmark, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import {
  projectChiefArtifactResult,
  selectChiefPodArtifact,
  type ChiefPodArtifactRecord,
} from "../../../../lib/financial-decision-package";
import { displayText, fetchWorkspaceContext, type WorkspacePageContext } from "../../../../lib/workspace-context";
import { CovenantEodPanel } from "../../../../components/pf/covenant-eod-panel";

type Tranche = {
  tranche?: string;
  name?: string;
  amount?: number;
  interestRate?: number;
  feeRate?: number;
  repaymentPriority?: number;
  drawPriority?: number;
  maturityMonths?: number;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const normalized = Number(value);
  return Number.isFinite(normalized) ? normalized : null;
}

function formatEok(value: unknown) {
  const n = finiteNumber(value);
  return n === null
    ? "미확인"
    : `${(n / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억원`;
}

function formatRate(value: unknown) {
  const n = finiteNumber(value);
  if (n === null) return "미확인";
  return `${(n * 100).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}%`;
}

export default function PfStructurePage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setContext(await fetchWorkspaceContext(projectId, "pf"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "금융구조 원장을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
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
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "PF-Credit Pod 실행에 실패했습니다.");
    } finally {
      setRunning(false);
    }
  }

  const pageData = asRecord(context?.pageData);
  const artifactSummary = asRecord(pageData.latestPfCreditArtifact);
  const artifactKey = displayText(artifactSummary.artifactKey, "");
  const artifactRecords = asArray<ChiefPodArtifactRecord>(pageData.chiefArtifacts);
  const selectedArtifact = selectChiefPodArtifact(artifactRecords, {
    artifactKey,
    podKey: "pf_credit",
  });
  const artifactPackage = projectChiefArtifactResult(selectedArtifact, [
    "pfStructure",
    "riskRegister",
    "creditReviewDecision",
    "covenantEod",
  ] as const);
  // Do not fill a missing artifact field from the mutable project projection.
  const pfStructure = asRecord(artifactPackage?.values.pfStructure);
  const riskRegister = asRecord(artifactPackage?.values.riskRegister);
  const creditDecision = asRecord(artifactPackage?.values.creditReviewDecision);
  const covenantEod = artifactPackage?.values.covenantEod;
  const tranches = asArray<Tranche>(pfStructure.tranches ?? pfStructure.pfStructure ?? pfStructure);
  const projectName = displayText(context?.projectMemory?.projectName, `프로젝트 ${projectId}`);
  const trancheAmounts = tranches.map((item) => finiteNumber(item.amount));
  const totalPf =
    trancheAmounts.length > 0 && trancheAmounts.every((amount) => amount !== null)
      ? trancheAmounts.reduce<number>((sum, amount) => sum + (amount ?? 0), 0)
      : null;

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
              <Landmark className="h-7 w-7 text-indigo-600" />
              PF 금융구조
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              하나의 PF-Credit artifact에 함께 저장된 Tranche, Risk, Credit Decision,
              Covenant만 표시합니다. 현재 프로젝트 projection은 누락값 보충에 사용하지 않습니다.
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void runPfPod()}
          disabled={loading || running}
          className="inline-flex h-10 items-center gap-2 rounded-lg bg-indigo-600 px-4 text-sm font-bold text-white disabled:opacity-50"
        >
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          PF-Credit Pod 실행
        </button>
      </header>

      {loading ? (
        <Loading text="금융구조 원장을 불러오는 중입니다." />
      ) : (
        <>
          {error ? <Notice>{error}</Notice> : null}
          {!artifactPackage ? (
            <Notice>
              최신 PF artifact의 전체 record를 정확히 확인할 수 없어 mutable 프로젝트
              projection을 표시하지 않습니다. PF-Credit Pod를 다시 실행해 하나의 패키지를
              생성하세요.
            </Notice>
          ) : null}
          {artifactPackage?.missingFields.length ? (
            <Notice>
              선택한 artifact에 다음 필드가 없습니다: {artifactPackage.missingFields.join(", ")}.
              다른 실행 결과로 보충하지 않습니다.
            </Notice>
          ) : null}
          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Kpi label="총 PF 금액" value={formatEok(totalPf)} />
            <Kpi label="Tranche 수" value={`${tranches.length.toLocaleString("ko-KR")}개`} />
            <Kpi label="Credit Decision" value={displayText(creditDecision.decisionLabel ?? creditDecision.decision, "미실행")} />
            <Kpi label="PF artifact" value={artifactPackage?.artifactKey ?? "미확인"} />
          </section>

          <Panel title="Tranche 구조">
            {tranches.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-left text-sm">
                  <thead className="border-b border-slate-200 text-xs text-slate-500 dark:border-slate-800">
                    <tr>
                      <th className="px-3 py-2">Tranche</th>
                      <th className="px-3 py-2">구분</th>
                      <th className="px-3 py-2">대출금액</th>
                      <th className="px-3 py-2">금리</th>
                      <th className="px-3 py-2">수수료</th>
                      <th className="px-3 py-2">인출순위</th>
                      <th className="px-3 py-2">상환순위</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {tranches.map((row, index) => (
                      <tr key={`${row.tranche ?? row.name ?? index}`}>
                        <td className="px-3 py-3 font-black text-slate-950 dark:text-white">{displayText(row.tranche, `Tr.${index + 1}`)}</td>
                        <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{displayText(row.name, "미확인")}</td>
                        <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{formatEok(row.amount)}</td>
                        <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{formatRate(row.interestRate)}</td>
                        <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{formatRate(row.feeRate)}</td>
                        <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{row.drawPriority ?? "미확인"}</td>
                        <td className="px-3 py-3 text-slate-600 dark:text-slate-300">{row.repaymentPriority ?? "미확인"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty text="저장된 PF Tranche 구조가 없습니다. 사업수지/CF와 토지·시장성 근거를 보강한 뒤 PF-Credit Pod를 실행하세요." />
            )}
          </Panel>

          <section className="grid gap-4 lg:grid-cols-2">
            <Panel title="Risk Register 연결">
              <InfoRow label="Risk rows" value={`${asArray((riskRegister as { risks?: unknown[] }).risks).length}건`} />
              <InfoRow label="Review status" value={displayText(riskRegister.status ?? riskRegister.reviewStatus, "미확인")} />
              <InfoRow label="Blocking 여부" value={displayText(riskRegister.blocking ? "blocking" : "미확인")} />
            </Panel>
            <Panel title="승인 판단">
              <InfoRow label="Decision" value={displayText(creditDecision.decisionLabel ?? creditDecision.decision, "미실행")} />
              <InfoRow label="Artifact" value={artifactPackage?.artifactKey ?? "미저장"} />
              <InfoRow label="Calculation Runs" value={artifactPackage?.calculationRunIds.join(", ") || "미확인"} />
              <InfoRow label="Input hash" value={artifactPackage?.inputHash ?? "미확인"} />
              <InfoRow label="Output hash" value={artifactPackage?.outputHash ?? "미확인"} />
            </Panel>
          </section>

          <CovenantEodPanel
            projectId={projectId}
            artifactResult={covenantEod}
            sourceArtifactKey={artifactPackage?.artifactKey}
          />
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

function Kpi({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900"><p className="text-sm font-semibold text-slate-500">{label}</p><p className="mt-3 text-2xl font-black tracking-tight text-slate-950 dark:text-white">{value}</p></div>;
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900"><div className="mb-4 flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-slate-700 dark:text-slate-200" /><h2 className="text-lg font-black text-slate-950 dark:text-white">{title}</h2></div>{children}</section>;
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return <div className="flex items-center justify-between gap-4 border-b border-slate-100 py-3 text-sm last:border-0 dark:border-slate-800"><span className="font-semibold text-slate-500">{label}</span><span className="text-right font-black text-slate-950 dark:text-white">{value}</span></div>;
}

function Empty({ text }: { text: string }) {
  return <div className="rounded-lg bg-amber-50 p-5 text-sm font-semibold text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">{text}</div>;
}
