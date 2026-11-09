"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, ArrowLeft, BarChart3, Database, Loader2, TrendingUp } from "lucide-react";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import { displayText, fetchWorkspaceContext, type WorkspacePageContext } from "../../../../lib/workspace-context";
import { MarketDepthPanel, type PublicMarketLookup } from "../../../../components/market/market-depth-panel";
import { projectWorkspaceHref } from "../../../../lib/project-navigation";

type MarketAnalysis = {
  applicable?: boolean;
  productType?: string;
  assetClass?: string;
  grade?: string;
  score?: number;
  summary?: string;
  dataStatus?: string;
  pricing?: {
    judgement?: string;
    pricingComment?: string;
    comment?: string;
    proposedPricePerPy?: number;
    comparableMedianPricePerPy?: number;
    comparableMedianPerPy?: number;
    premiumToMedian?: number;
  };
  exitSaleRate?: { base?: number; baseSaleRate?: number; requiredSaleRate?: number };
  riskFlags?: Array<{ code?: string; message?: string; severity?: string }>;
  recommendations?: string[];
  dataLimitations?: string[];
  dataSources?: Array<Record<string, unknown>>;
  comparableSummary?: Record<string, unknown>;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function formatManPerPy(value: unknown) {
  const n = Number(value ?? 0);
  return n ? `${Math.round(n / 10_000).toLocaleString("ko-KR")}만원/평` : "미확인";
}

function formatPct(value: unknown) {
  if (value === null || value === undefined || value === "") return "미확인";
  const n = Number(value);
  if (!Number.isFinite(n)) return "미확인";
  return `${(n * 100).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%`;
}

function riskLabel(code?: string) {
  if (code === "NO_COMPARABLE_DATA") return "비교사례 부족";
  if (code === "PROPOSED_PRICE_BASIS_MISSING") return "제안 분양가 기준 부족";
  if (code === "PRICE_PREMIUM_HIGH") return "제안가 프리미엄 높음";
  if (code === "EXIT_RATE_HIGH") return "Exit 분양률 높음";
  return displayText(code, "Risk");
}

export default function MarketabilityPage() {
  const { id } = useParams<{ id: string }>();
  const track = useSearchParams().get("track");
  return <MarketabilityWorkspace key={id} projectId={id} track={track} />;
}

function MarketabilityWorkspace({ projectId, track }: { projectId: string; track: string | null }) {
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [analysis, setAnalysis] = useState<MarketAnalysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [depthRevision, setDepthRevision] = useState(0);
  const [publicLookup, setPublicLookup] = useState<PublicMarketLookup | null>(null);
  const [publicLookupBusy, setPublicLookupBusy] = useState(false);
  const pendingPublic = useRef<AbortController | null>(null);
  const intakeHref = projectWorkspaceHref(projectId, "input-package", track) + "#intake-upload";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const ctx = await fetchWorkspaceContext(projectId, "marketability");
      setContext(ctx);
      const storedAnalysis = asRecord(asRecord(ctx.pageData).marketability) as MarketAnalysis;
      setAnalysis(Object.keys(storedAnalysis).length ? storedAnalysis : null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "시장성 저장 상태를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    return () => pendingPublic.current?.abort();
  }, [load]);

  async function runPublicLookup() {
    pendingPublic.current?.abort();
    const controller = new AbortController();
    pendingPublic.current = controller;
    setPublicLookupBusy(true);
    setPublicLookup(null);
    try {
      const response = await fetch(apiBase + "/projects/" + encodeURIComponent(projectId) + "/marketability/analyze?live_comparables=true", {
        method: "POST",
        cache: "no-store",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: "{}",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("Public market lookup unavailable");
      const body = await response.json() as { publicLookup?: PublicMarketLookup };
      if (!body.publicLookup || !["available", "empty", "needs_input", "unavailable", "failed"].includes(body.publicLookup.status)) {
        throw new Error("Unexpected public market lookup response");
      }
      if (!controller.signal.aborted) setPublicLookup(body.publicLookup);
    } catch {
      if (!controller.signal.aborted) setPublicLookup({ status: "failed", count: 0 });
    } finally {
      if (!controller.signal.aborted) setPublicLookupBusy(false);
    }
  }

  async function runMarketPod() {
    setRunning(true);
    setError(null);
    try {
      const res = await fetch(apiBase + "/projects/" + encodeURIComponent(projectId) + "/chief-orchestration/run-pod", {
        method: "POST",
        headers: { ...authHeaders("web"), "Content-Type": "application/json" },
        body: JSON.stringify({ podKey: "market_product" }),
      });
      if (!res.ok) throw new Error("시장성 검토 실행에 실패했습니다. 필요한 자료를 확인한 뒤 다시 시도해 주세요.");
      await load();
      setDepthRevision((revision) => revision + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "시장성 검토 실행에 실패했습니다.");
    } finally {
      setRunning(false);
    }
  }

  const pageData = asRecord(context?.pageData);
  const marketEvidence = asRecord(pageData.marketEvidence);
  const market = asRecord(pageData.market);
  const sources = asArray<Record<string, unknown>>(analysis?.dataSources);
  const riskFlags = asArray<{ code?: string; message?: string; severity?: string }>(analysis?.riskFlags);
  const dataLimitations = asArray<string>(analysis?.dataLimitations);
  const recommendations = asArray<string>(analysis?.recommendations);
  const projectName = displayText(context?.projectMemory?.projectName, `프로젝트 ${projectId}`);
  const pricing = analysis?.pricing ?? {};
  const proposed = pricing.proposedPricePerPy;
  const median = pricing.comparableMedianPricePerPy ?? pricing.comparableMedianPerPy;
  const exitRate = analysis?.exitSaleRate?.base ?? analysis?.exitSaleRate?.baseSaleRate ?? analysis?.exitSaleRate?.requiredSaleRate;

  const comparableCount = useMemo(() => {
    const recordedCount = analysis?.comparableSummary?.count;
    if (typeof recordedCount === "number" && Number.isSafeInteger(recordedCount) && recordedCount >= 0) return recordedCount;
    const storedGroups = ["transactionComparables", "presaleComparables", "listingComparables"]
      .map((key) => Math.max(
        Array.isArray(market[key]) ? market[key].length : -1,
        Array.isArray(marketEvidence[key]) ? marketEvidence[key].length : -1,
      ))
      .filter((count) => count >= 0);
    return storedGroups.length ? storedGroups.reduce((total, count) => total + count, 0) : null;
  }, [analysis, market, marketEvidence]);
  const hasComparables = comparableCount !== null && comparableCount > 0;

  const hasProposedPriceBasis = Number(proposed) > 0;
  const sourceQuality = sources.length > 0
    ? "출처 메타데이터 있음"
    : Object.keys(marketEvidence).length > 0
      ? "저장 근거 있음"
      : "근거 보강 필요";
  const pricingGap = !hasProposedPriceBasis
    ? "제안 분양가 기준이 부족합니다. 분양면적 기준인지, 계약면적 기준인지, VAT 포함 여부를 먼저 확인해야 합니다."
    : !hasComparables
      ? "비교사례가 부족합니다. 실거래가, 분양사례, 임대료, 수동 비교사례를 보강해야 합니다."
      : "제안 분양가와 비교사례를 함께 검토할 수 있습니다.";

  return (
    <div className="space-y-6 pb-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <Link href={`/project/${projectId}`} className="mt-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" aria-label="프로젝트로 돌아가기">
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <p className="text-sm font-semibold text-slate-500">{projectName}</p>
            <h1 className="mt-1 flex items-center gap-2 text-3xl font-black tracking-tight text-slate-950 dark:text-white">
              <BarChart3 className="h-7 w-7 text-indigo-600" />
              시장·분양성 분석
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              시장성 Agent가 비교사례, 실거래가, 제안 분양가 기준, 흡수율, Exit 분양률을 검토합니다. 출처가 없으면 확정 판단하지 않습니다.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => void runMarketPod()} disabled={loading || running} className="inline-flex h-10 items-center gap-2 rounded-lg bg-indigo-600 px-4 text-sm font-bold text-white disabled:opacity-50">
            {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <TrendingUp className="h-4 w-4" />}
            시장성 검토 실행
          </button>
        </div>
      </header>

      {loading ? (
        <div className="flex min-h-[240px] items-center justify-center rounded-lg border border-slate-200 bg-white text-sm font-bold text-slate-500 dark:border-slate-800 dark:bg-slate-900">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          시장성 저장 상태를 불러오는 중입니다.
        </div>
      ) : (
        <>
          {error ? <Notice>{error}</Notice> : null}

          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
            <Kpi label="분양성 등급" value={analysis?.grade ?? "미실행"} />
            <Kpi label="분양성 Score" value={typeof analysis?.score === "number" ? analysis.score.toFixed(0) : "미확인"} />
            <Kpi label="제안 분양가" value={formatManPerPy(proposed)} />
            <Kpi label="비교사례 중앙값" value={formatManPerPy(median)} />
            <Kpi label="Exit 분양률" value={formatPct(exitRate)} />
          </section>

          <section className={`rounded-lg border p-4 text-sm font-semibold ${!hasProposedPriceBasis || !hasComparables ? "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100" : "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100"}`}>
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <p className="font-black">가격 근거 진단</p>
                <p className="mt-1">{pricingGap}</p>
              </div>
            </div>
          </section>

          <section className="grid gap-4 lg:grid-cols-[1fr_0.9fr]">
            <Panel title="AI 시장성 의견">
              {analysis ? (
                <>
                  <InfoRow label="상품유형" value={displayText(analysis.productType ?? analysis.assetClass, "미확인")} />
                  <InfoRow label="데이터 상태" value={displayText(analysis.dataStatus, "미확인")} />
                  <InfoRow label="가격 판단" value={displayText(pricing.judgement, "근거 부족")} />
                  <InfoRow label="출처 상태" value={sourceQuality} />
                  <p className="mt-4 rounded-lg bg-slate-50 p-4 text-sm leading-6 text-slate-700 dark:bg-slate-800/70 dark:text-slate-200">
                    {displayText(analysis.summary ?? pricing.pricingComment ?? pricing.comment, "아직 저장된 시장성 검토 의견이 없습니다.")}
                  </p>
                </>
              ) : (
                <Empty icon={<AlertTriangle />} text="아직 저장된 시장성 분석이 없습니다. 아래에서 공개 실거래를 조회하거나 시장성 검토를 실행하세요." />
              )}
            </Panel>
            <Panel title="근거와 한계">
              <InfoRow label="비교사례" value={comparableCount === null ? "미확인" : comparableCount.toLocaleString("ko-KR") + "건"} />
              <InfoRow label="제안가 기준" value={hasProposedPriceBasis ? "확보" : "부족"} />
              <InfoRow label="Data source" value={sources.length ? `${sources.length}건` : sourceQuality} />
              <InfoRow label="Risk flags" value={`${riskFlags.length}건`} />
              {dataLimitations.length ? (
                <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                  {dataLimitations.join(" / ")}
                </div>
              ) : null}
            </Panel>
          </section>

          <section className="grid gap-4 lg:grid-cols-2">
            <Panel title="Risk Flags">
              {riskFlags.length ? (
                <div className="space-y-2">
                  {riskFlags.slice(0, 8).map((risk, index) => (
                    <div key={`${risk.code}-${index}`} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm dark:border-amber-900 dark:bg-amber-950/30">
                      <p className="font-black text-amber-900 dark:text-amber-100">{riskLabel(risk.code)}</p>
                      <p className="mt-1 text-xs leading-5 text-amber-800 dark:text-amber-200">{displayText(risk.message, "시장성 검토 리스크가 있습니다.")}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <Empty icon={<AlertTriangle />} text="표시할 Risk Flag가 없습니다. 단, 근거 데이터가 없으면 확정 판단으로 보지 않습니다." />
              )}
            </Panel>
            <Panel title="권고사항">
              {recommendations.length ? (
                <ol className="space-y-2 text-sm font-semibold leading-6 text-slate-700 dark:text-slate-200">
                  {recommendations.slice(0, 8).map((item, index) => (
                    <li key={`${item}-${index}`}>{index + 1}. {item}</li>
                  ))}
                </ol>
              ) : (
                <Empty icon={<Database />} text="권고사항을 생성하려면 비교사례, 제안 분양가 기준, 상품 구성 자료가 필요합니다." />
              )}
            </Panel>
          </section>
        </>
      )}

      <MarketDepthPanel projectId={projectId} refreshRevision={depthRevision} publicLookup={publicLookup}
        publicLookupBusy={publicLookupBusy} onPublicLookup={() => void runPublicLookup()} intakeHref={intakeHref} />
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-xs font-bold text-slate-500">{label}</p>
      <p className="mt-2 text-xl font-black text-slate-950 dark:text-white">{value}</p>
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

function Empty({ icon, text }: { icon: ReactNode; text: string }) {
  return (
    <div className="flex items-start gap-2 rounded-lg bg-slate-50 p-4 text-sm text-slate-500 dark:bg-slate-800/70">
      <span className="mt-0.5 h-4 w-4">{icon}</span>
      {text}
    </div>
  );
}

function Notice({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">{children}</div>;
}
