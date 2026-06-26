"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useParams } from "next/navigation";
import { ArrowLeft, BarChart3, Loader2, RefreshCw } from "lucide-react";
import { authHeaders } from "../../../../lib/auth-client";
import { MapIntelligence, type MapIntelligenceData, type TxMarker } from "../../../../components/map/map-intelligence";
import { TransactionsPanel, type TxRecord, type ComplexRecord } from "../../../../components/map/transactions-panel";

const apiBase = "http://127.0.0.1:8000";

type ParcelFeature = { properties: { pnu?: string } };
type Analysis = {
  applicable?: boolean;
  productType?: string;
  assetClass?: string;
  grade?: string;
  score?: number;
  summary?: string;
  dataStatus?: string;
  pricing?: { judgement?: string; comment?: string; pricePerPy?: number; comparableMedianPerPy?: number };
  exitSaleRate?: { baseSaleRate?: number; requiredSaleRate?: number };
  recommendations?: string[];
  dataLimitations?: string[];
};

const GRADE_TONE: Record<string, string> = {
  A: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  B: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200",
  C: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  D: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-200",
  E: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200",
};

function manPerPy(won?: number): string {
  return won ? `${Math.round(won / 10_000).toLocaleString("ko-KR")}만/평` : "—";
}
function eok(won?: number): string {
  return won ? `${(won / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억` : "—";
}

function Kpi({ label, value, tone }: { label: string; value: ReactNode; tone?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <p className="text-[11px] font-bold text-slate-500">{label}</p>
      <p className={`mt-1 text-lg font-black ${tone ?? "text-slate-950 dark:text-white"}`}>{value}</p>
    </div>
  );
}

export default function MarketabilityPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [mapData, setMapData] = useState<MapIntelligenceData | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [txRecords, setTxRecords] = useState<TxRecord[]>([]);
  const [complexes, setComplexes] = useState<ComplexRecord[]>([]);
  const [viewMode, setViewMode] = useState<"complex" | "individual">("complex");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [mapRes, anaRes] = await Promise.all([
        fetch(`${apiBase}/projects/${projectId}/map?resolve_geometry=true`, { cache: "no-store", headers: authHeaders("web") }),
        fetch(`${apiBase}/projects/${projectId}/marketability/analyze?live_comparables=true`, {
          method: "POST",
          cache: "no-store",
          headers: { ...authHeaders("web"), "Content-Type": "application/json" },
          body: "{}",
        }),
      ]);
      if (mapRes.ok) setMapData(await mapRes.json());
      if (anaRes.ok) setAnalysis(await anaRes.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "시장성 데이터를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const parcels = (mapData?.parcelGeoJson?.features ?? []) as ParcelFeature[];
  const firstPnu = parcels.find((p) => p.properties.pnu)?.properties.pnu;
  const lawdCd = firstPnu && firstPnu.length >= 5 ? firstPnu.slice(0, 5) : undefined;

  const individualMarkers: TxMarker[] = useMemo(
    () =>
      txRecords
        .filter((r) => typeof r.lat === "number" && typeof r.lng === "number")
        .map((r) => ({
          lat: r.lat as number,
          lng: r.lng as number,
          tone: r.dealKind === "rent" ? "rent" : "trade",
          label: r.dealKind === "rent" ? eok(r.depositKRW) : eok(r.totalPrice),
          sublabel: r.dealKind === "rent" ? undefined : manPerPy(r.pricePerPy),
          value: r.dealKind === "rent" ? undefined : r.pricePerPy,
        })),
    [txRecords],
  );

  const complexMarkers: TxMarker[] = useMemo(
    () =>
      complexes
        .filter((c) => typeof c.lat === "number" && typeof c.lng === "number" && c.medianPricePerPy)
        .map((c) => ({
          lat: c.lat as number,
          lng: c.lng as number,
          tone: "trade" as const,
          label: manPerPy(c.medianPricePerPy),
          sublabel: `${c.complexName ?? ""} · ${c.tradeCount ?? 0}건`,
          value: c.medianPricePerPy,
        })),
    [complexes],
  );

  const markers = viewMode === "complex" ? complexMarkers : individualMarkers;

  const avgPerPy = useMemo(() => {
    const prices = txRecords.filter((r) => r.dealKind === "trade" && r.pricePerPy).map((r) => r.pricePerPy as number);
    return prices.length ? Math.round(prices.reduce((a, b) => a + b, 0) / prices.length) : undefined;
  }, [txRecords]);

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link href={`/project/${projectId}`} className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300">
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div className="flex items-center gap-2">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-900 text-white dark:bg-white dark:text-slate-950">
              <BarChart3 className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-xl font-black text-slate-950 dark:text-white">시장성 분석 (Marketability)</h1>
              <p className="text-xs font-semibold text-slate-500">지도 · 실거래가 · 분양가 적정성 · Exit 분양률</p>
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 px-3 text-sm font-bold text-slate-700 disabled:opacity-40 dark:border-slate-700 dark:text-slate-200"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          새로고침
        </button>
      </header>

      {error ? <p className="text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      <section className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Kpi
          label="시장성 등급"
          value={<span className={`inline-flex rounded-full px-2.5 py-0.5 text-sm ${GRADE_TONE[analysis?.grade ?? ""] ?? "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"}`}>{analysis?.grade ?? "—"}</span>}
        />
        <Kpi label="종합점수" value={typeof analysis?.score === "number" ? analysis.score.toFixed(0) : "—"} />
        <Kpi label="제안가 적정성" value={analysis?.pricing?.judgement ?? "—"} />
        <Kpi label="주변 평균 평단가" value={manPerPy(avgPerPy)} tone="text-sky-700 dark:text-sky-300" />
        <Kpi label="Exit 분양률(기준)" value={analysis?.exitSaleRate?.baseSaleRate != null ? `${Math.round(analysis.exitSaleRate.baseSaleRate * 100)}%` : "—"} />
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.55fr_0.85fr]">
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div className="inline-flex rounded-lg border border-slate-200 p-0.5 text-xs font-bold dark:border-slate-700">
              {(["complex", "individual"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setViewMode(mode)}
                  className={`rounded-md px-3 py-1.5 transition-colors ${
                    viewMode === mode
                      ? "bg-slate-900 text-white dark:bg-white dark:text-slate-950"
                      : "text-slate-600 dark:text-slate-300"
                  }`}
                >
                  {mode === "complex" ? `단지 (${complexMarkers.length})` : `개별 거래 (${individualMarkers.length})`}
                </button>
              ))}
            </div>
            <span className="text-[11px] text-slate-400">국토부 실거래가 · VWorld 좌표화</span>
          </div>
          {loading && !mapData ? (
            <div className="flex min-h-[420px] items-center justify-center rounded-lg border border-slate-200 text-sm text-slate-500 dark:border-slate-800">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" /> 지도 로딩 중
            </div>
          ) : mapData ? (
            <MapIntelligence data={mapData} transactions={markers} />
          ) : null}
          {markers.length ? (
            <p className="text-xs text-slate-500">
              {viewMode === "complex"
                ? `단지별 중위 평단가 마커 (${complexMarkers.length}개 단지).`
                : `개별 실거래 가격 핀 (${individualMarkers.length}건). `}
              {viewMode === "individual" ? (
                <>
                  <span className="text-rose-500">●</span> 매매 · <span className="text-sky-500">●</span> 전월세
                </>
              ) : null}
            </p>
          ) : null}
        </div>

        <div className="space-y-4">
          <TransactionsPanel lawdCd={lawdCd} geocode aggregate onRecords={setTxRecords} onComplexes={setComplexes} />

          {analysis ? (
            <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
              <h2 className="font-black text-slate-950 dark:text-white">AI 시장성 의견</h2>
              {analysis.applicable === false ? (
                <p className="mt-2 text-sm text-slate-500">분양 대상 자산이 아니거나 시장성 분석 대상이 아닙니다.</p>
              ) : (
                <>
                  {analysis.summary ? <p className="mt-2 text-sm leading-6 text-slate-700 dark:text-slate-300">{analysis.summary}</p> : null}
                  {analysis.pricing?.comment ? (
                    <p className="mt-2 rounded-lg bg-slate-50 p-3 text-sm text-slate-700 dark:bg-slate-950/40 dark:text-slate-200">{analysis.pricing.comment}</p>
                  ) : null}
                  {analysis.recommendations?.length ? (
                    <ul className="mt-3 list-disc space-y-1 pl-4 text-sm text-slate-700 dark:text-slate-300">
                      {analysis.recommendations.slice(0, 5).map((rec, i) => (
                        <li key={i}>{rec}</li>
                      ))}
                    </ul>
                  ) : null}
                  {analysis.dataLimitations?.length ? (
                    <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                      {analysis.dataLimitations.join(" · ")}
                    </p>
                  ) : null}
                </>
              )}
            </section>
          ) : null}
        </div>
      </section>
    </div>
  );
}
