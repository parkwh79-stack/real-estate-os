"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Building, Landmark, Loader2, ListChecks, RefreshCw, Users } from "lucide-react";
import { apiBase, authHeaders } from "../../lib/auth-client";

type ComparisonRow = {
  provider: string;
  label: string;
  proposedPricePerPy?: number | null;
  status?: string;
  deltaVsPublicMedian?: number | null;
  assessment?: string;
};

type DepthResult = {
  schemaVersion: string;
  sourceProvenance?: {
    byProvider?: Record<string, { label: string; count: number }>;
    archivedDocumentCount?: number;
    developerProvided?: number;
    salesAgentProvided?: number;
    publicData?: number;
    autoSupplemented?: number;
    unspecified?: number;
    partnerProvided?: number;
  };
  providerComparison?: {
    publicMedianPerPy?: number | null;
    rows?: ComparisonRow[];
    developerVsSalesAgent?: { deltaPct?: number | null; note?: string } | null;
  };
  productMarketabilityScores?: Array<{ productType: string; grade: string; score?: number | null }>;
  absorptionModel?: { monthlyAbsorptionRate?: number | null; rateBasis?: string; status?: string; estimatedMonthsToSellout?: number | null };
  aiRecommendedNextActions?: { actions?: Array<{ priority: string; action: string; category?: string }> };
};

export type PublicMarketLookup = {
  status: "available" | "empty" | "needs_input" | "unavailable" | "failed";
  count: number;
  sourceName?: string;
  lawdCd?: string;
  assetType?: string;
  month?: string;
  medianPerPy?: number | null;
};

const PUBLIC_LOOKUP_LABELS: Record<PublicMarketLookup["status"], string> = {
  available: "국토부 공개 실거래 조회 완료",
  empty: "선택 조건에 해당하는 공개 실거래가 없습니다.",
  needs_input: "실거래 조회에 필요한 사업지 지역 또는 상품유형을 확인해 주세요.",
  unavailable: "공개 실거래 조회 연결을 사용할 수 없습니다.",
  failed: "공개 실거래를 조회하지 못했습니다. 다시 시도해 주세요.",
};

function countLabel(value?: number) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) return "미확인";
  return value === 0 ? "확인된 자료 없음" : value.toLocaleString("ko-KR") + "건";
}

function man(value?: number | null) {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return "미확인";
  return (value / 10000).toLocaleString("ko-KR") + "만/평";
}

function pctDelta(value?: number | null) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return sign + (value * 100).toLocaleString("ko-KR", { maximumFractionDigits: 0 }) + "%";
}

function rateLabel(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1 ? (value * 100).toLocaleString("ko-KR", { maximumFractionDigits: 0 }) + "%" : null;
}

function monthsLabel(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value + "개월" : null;
}

const PRIORITY_LABEL: Record<string, string> = { high: "우선 확인", medium: "추가 확인", low: "참고" };
const ACTION_CLASS = "inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-re-border bg-re-surface px-3 py-2 text-sm font-semibold text-re-text hover:bg-re-subtle disabled:opacity-50";

export function MarketDepthPanel({
  projectId, refreshRevision, publicLookup, publicLookupBusy, onPublicLookup, intakeHref,
}: {
  projectId: string;
  refreshRevision: number;
  publicLookup: PublicMarketLookup | null;
  publicLookupBusy: boolean;
  onPublicLookup: () => void;
  intakeHref: string;
}) {
  const [result, setResult] = useState<DepthResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    void (async () => {
      try {
        const res = await fetch(apiBase + "/projects/" + encodeURIComponent(projectId) + "/market-product-depth/analyze", {
          method: "POST",
          cache: "no-store",
          signal: controller.signal,
          headers: { ...authHeaders("web"), "Content-Type": "application/json" },
          body: JSON.stringify({ options: { podAiReviewMode: "deterministic" } }),
        });
        if (!res.ok) throw new Error("Market depth unavailable");
        const next = (await res.json()) as DepthResult;
        if (next?.schemaVersion !== "realytics-market-product-depth-v1") throw new Error("Unexpected market depth response");
        if (!controller.signal.aborted) setResult(next);
      } catch {
        if (!controller.signal.aborted) {
          setResult(null);
          setError(true);
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [projectId, refreshRevision, reload]);

  const provenance = result?.sourceProvenance;
  const comparison = result?.providerComparison;
  const absorption = result?.absorptionModel;
  const actions = result?.aiRecommendedNextActions?.actions ?? [];
  const benchmark = absorption?.rateBasis === "planning_benchmark";
  const monthlyRate = rateLabel(absorption?.monthlyAbsorptionRate);
  const months = monthsLabel(absorption?.estimatedMonthsToSellout);
  const hasForecast = !benchmark && absorption?.rateBasis === "forecast" && monthlyRate !== null
    && absorption.status !== "insufficient_data" && absorption.status !== "invalid_input";

  return (
    <section aria-labelledby="market-depth-title" className="space-y-4 rounded-lg border border-re-border bg-re-surface p-5 shadow-sm">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="market-depth-title" className="flex items-center gap-2 text-lg font-bold text-re-text">
          <Users className="h-5 w-5 text-re-action" aria-hidden="true" />자료 제공주체 비교 · 시장성 심화
        </h2>
        <button type="button" className={ACTION_CLASS} onClick={() => setReload((value) => value + 1)} disabled={loading}>
          <RefreshCw className="h-4 w-4" aria-hidden="true" />저장 근거 다시 확인
        </button>
      </header>
      <p className="text-sm text-re-secondary">출처가 확인된 저장 자료와 제안 분양가를 비교합니다. 시장 분석에 필요한 공개 실거래는 아래에서 별도로 조회할 수 있습니다.</p>

      <section aria-label="공개 실거래 조회 결과" className="space-y-2 rounded-lg border border-re-border bg-re-subtle p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-bold text-re-text">공개 실거래 근거</h3>
          <button type="button" className={ACTION_CLASS} disabled={publicLookupBusy} onClick={onPublicLookup}>
            {publicLookupBusy ? "공개 실거래 확인 중…" : "공개 실거래 조회"}
          </button>
        </div>
        <p role="status" className="text-sm text-re-secondary">
          {publicLookupBusy ? "국토부 공개자료 조회 중입니다."
            : publicLookup ? PUBLIC_LOOKUP_LABELS[publicLookup.status] ?? "공개 실거래 조회 상태를 확인하지 못했습니다."
              : "공개 실거래는 아직 조회하지 않았습니다."}
        </p>
        {!publicLookupBusy && publicLookup?.status === "available" ? (
          <p className="text-sm text-re-text">
            {countLabel(publicLookup.count)} · 중위값 {man(publicLookup.medianPerPy)}
            {publicLookup.lawdCd ? " · 지역 코드 " + publicLookup.lawdCd : ""}
            {publicLookup.month ? " · 거래월 " + publicLookup.month : ""}
            {" · 출처 " + (publicLookup.sourceName || "국토교통부")}
          </p>
        ) : null}
        <p className="text-xs text-re-secondary">공개자료 조회 결과는 화면에서 확인합니다. 아래 제공주체 비교에는 저장된 근거가 표시됩니다.</p>
      </section>

      {loading ? (
        <div role="status" className="flex min-h-[120px] items-center justify-center text-sm font-semibold text-re-secondary">
          <Loader2 className="mr-2 h-5 w-5 motion-safe:animate-spin" aria-hidden="true" />저장된 시장 근거를 확인하는 중입니다.
        </div>
      ) : error ? (
        <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-semibold text-rose-900 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">저장된 시장 근거를 조회하지 못했습니다. 다시 확인해 주세요.</p>
      ) : result ? (
        <div className="space-y-5">
          {typeof provenance?.archivedDocumentCount === "number" ? (
            <p className="rounded-lg bg-re-subtle p-3 text-sm text-re-secondary">
              보관 원본 {countLabel(provenance.archivedDocumentCount)} · 제공주체가 확인된 자료를 아래에서 비교합니다.
            </p>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <Tile icon={<Building className="h-4 w-4 text-re-action" aria-hidden="true" />} label="시행사 제공" value={countLabel(provenance?.developerProvided)} />
            <Tile icon={<Users className="h-4 w-4 text-re-action" aria-hidden="true" />} label="매각 주관사 제공" value={countLabel(provenance?.salesAgentProvided)} />
            <Tile icon={<Landmark className="h-4 w-4 text-re-action" aria-hidden="true" />} label="저장된 공공자료" value={countLabel(provenance?.publicData)} />
            <Tile label="자동 보강" value={countLabel(provenance?.autoSupplemented)} />
            {typeof provenance?.partnerProvided === "number" && provenance.partnerProvided > 0
              ? <Tile label="제휴 제공" value={countLabel(provenance.partnerProvided)} /> : null}
            <Tile label="제공주체 미확인" value={countLabel(provenance?.unspecified ?? provenance?.byProvider?.unspecified?.count)} />
          </div>

          <div>
            <h3 className="mb-2 text-sm font-bold text-re-text">제안 분양가 비교</h3>
            <p className="mb-2 text-xs text-re-secondary">저장된 공공 실거래 중위값 · {man(comparison?.publicMedianPerPy)}</p>
            {comparison?.rows?.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[500px] text-left text-sm">
                  <thead className="border-b border-re-border text-xs text-re-secondary">
                    <tr><th className="px-3 py-2">제공주체</th><th className="px-3 py-2">제안 분양가</th><th className="px-3 py-2">공공 대비</th><th className="px-3 py-2">판단</th></tr>
                  </thead>
                  <tbody className="divide-y divide-re-border">
                    {comparison.rows.map((row) => (
                      <tr key={row.provider}>
                        <td className="px-3 py-3 font-semibold text-re-text">{row.label}</td>
                        <td className="px-3 py-3 text-re-secondary">{row.status === "not_submitted" ? "제안 자료 없음" : man(row.proposedPricePerPy)}</td>
                        <td className="px-3 py-3 font-semibold text-re-secondary">{pctDelta(row.deltaVsPublicMedian)}</td>
                        <td className="px-3 py-3 text-xs text-re-secondary">{row.assessment}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="text-sm text-re-secondary">비교할 제안 분양가를 확인하지 못했습니다.</p>}
            {comparison?.developerVsSalesAgent?.note ? <p className="mt-2 rounded-lg bg-re-subtle p-3 text-xs text-re-secondary">{comparison.developerVsSalesAgent.note}</p> : null}
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <h3 className="mb-2 text-sm font-bold text-re-text">상품유형별 분양성</h3>
              {result.productMarketabilityScores?.length ? (
                <div className="flex flex-wrap gap-2">
                  {result.productMarketabilityScores.map((score) => (
                    <span key={score.productType} className="rounded-lg border border-re-border px-3 py-1.5 text-xs font-semibold text-re-text">
                      {score.productType} · {score.grade}
                    </span>
                  ))}
                </div>
              ) : <p className="text-sm text-re-secondary">상품 구성과 가격 근거가 필요합니다.</p>}
            </div>
            <div>
              <h3 className="mb-2 text-sm font-bold text-re-text">월별 분양 속도</h3>
              {hasForecast ? (
                <p className="text-sm text-re-text">입력된 예측 근거 · 월 {monthlyRate} · 완판 예상 {months ?? "미산정"}</p>
              ) : (
                <p className="text-sm text-re-secondary">{absorption?.status === "invalid_input"
                  ? "흡수율 입력값과 근거를 확인해 주세요." : "분양 속도 근거가 부족해 완판 예상 기간을 산정하지 않았습니다."}</p>
              )}
              {benchmark && monthlyRate ? (
                <details className="mt-2 rounded-lg border border-re-border bg-re-subtle px-3 py-2 text-xs text-re-secondary">
                  <summary className="cursor-pointer font-semibold text-re-text">계획 참고 가정 보기</summary>
                  <p className="mt-2">일반 계획용 가정 · 월 {monthlyRate}{months ? " · 가정상 " + months : ""}. 사업장의 실제 분양 실적과 별도로 표시합니다.</p>
                </details>
              ) : null}
            </div>
          </div>

          <div>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <h3 className="flex items-center gap-2 text-sm font-bold text-re-text">
                <ListChecks className="h-4 w-4 text-re-action" aria-hidden="true" />다음 할 일
              </h3>
              <Link href={intakeHref} className={ACTION_CLASS}>시장 자료 보완</Link>
            </div>
            {actions.length ? (
              <ul className="space-y-2">
                {actions.map((action, index) => (
                  <li key={index} className="rounded-lg border border-re-border bg-re-subtle p-3 text-sm text-re-secondary">
                    <strong className="text-re-text">{PRIORITY_LABEL[action.priority] ?? "확인"}</strong> · {action.action}
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-re-secondary">현재 확인된 보완 안내가 없습니다.</p>}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function Tile({ icon, label, value }: { icon?: React.ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-lg border border-re-border bg-re-surface p-3">
      <p className="flex items-center gap-1.5 text-xs font-semibold text-re-secondary">{icon}{label}</p>
      <p className="mt-2 text-base font-bold text-re-text">{value}</p>
    </div>
  );
}
