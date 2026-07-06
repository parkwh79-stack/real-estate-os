"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { ArrowLeft, Loader2, Map as MapIcon, RefreshCw } from "lucide-react";
import { authHeaders } from "../../../../lib/auth-client";
import { displayText, fetchWorkspaceContext, type WorkspacePageContext } from "../../../../lib/workspace-context";
import { MapIntelligence, type MapIntelligenceData, type TxMarker } from "../../../../components/map/map-intelligence";
import { TransactionsPanel, type TxRecord } from "../../../../components/map/transactions-panel";

const apiBase = "http://127.0.0.1:8000";

type ParcelFeature = {
  geometry?: unknown;
  properties: {
    pnu?: string;
    addressFragment?: string;
    lotNumber?: string;
    landCategory?: string;
    areaSqm?: number;
    officialLandPricePerSqm?: number;
    officialLandPriceBaseYear?: string;
    owner?: string;
  };
};

function eok(value?: number): string {
  if (!value) return "미확인";
  return `${(value / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억원`;
}
function formatSqm(value?: number) {
  return value ? `${value.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}㎡` : "미확인";
}

export default function ProjectMapPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [context, setContext] = useState<WorkspacePageContext | null>(null);
  const [data, setData] = useState<MapIntelligenceData | null>(null);
  const [txRecords, setTxRecords] = useState<TxRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ctx, res] = await Promise.all([
        fetchWorkspaceContext(projectId, "land").catch(() => null),
        fetch(`${apiBase}/projects/${projectId}/map?resolve_geometry=true&geometry_limit=150`, {
          cache: "no-store",
          headers: authHeaders("web"),
        }),
      ]);
      if (ctx) setContext(ctx);
      if (!res.ok) throw new Error(`지도 데이터 조회 실패 (${res.status})`);
      setData(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "지도 데이터를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const parcels = (data?.parcelGeoJson?.features ?? []) as ParcelFeature[];
  const locatedCount = parcels.filter((parcel) => Boolean(parcel.geometry)).length;
  const firstPnu = parcels.find((parcel) => parcel.properties.pnu)?.properties.pnu;
  const lawdCd = firstPnu && firstPnu.length >= 5 ? firstPnu.slice(0, 5) : undefined;
  const projectName = displayText(context?.projectMemory?.projectName, `프로젝트 ${projectId}`);

  const markers: TxMarker[] = useMemo(
    () =>
      txRecords
        .filter((record) => typeof record.lat === "number" && typeof record.lng === "number")
        .map((record) => ({
          lat: record.lat as number,
          lng: record.lng as number,
          tone: record.dealKind === "rent" ? "rent" : "trade",
          label: record.dealKind === "rent" ? eok(record.depositKRW) : eok(record.totalPrice),
          value: record.dealKind === "rent" ? undefined : record.pricePerPy,
        })),
    [txRecords],
  );

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link href={`/project/${projectId}`} className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300" aria-label="프로젝트로 돌아가기">
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div className="flex items-center gap-2">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-900 text-white dark:bg-white dark:text-slate-950">
              <MapIcon className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs font-semibold text-slate-500">{projectName}</p>
              <h1 className="text-xl font-black text-slate-950 dark:text-white">지도 기반 토지·거래 분석</h1>
              <p className="text-xs font-semibold text-slate-500">필지 경계, PNU, 공시지가, 실거래 근거를 같은 화면에서 확인합니다.</p>
            </div>
          </div>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading} className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 px-3 text-sm font-bold text-slate-700 disabled:opacity-40 dark:border-slate-700 dark:text-slate-200">
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          새로고침
        </button>
      </header>

      {data ? (
        <section className="grid gap-3 md:grid-cols-4">
          <Metric label="필지 수" value={`${parcels.length.toLocaleString("ko-KR")}필지`} />
          <Metric label="지도 경계 확인" value={`${locatedCount.toLocaleString("ko-KR")}/${parcels.length.toLocaleString("ko-KR")}`} />
          <Metric label="공시지가 확인" value={`${data.summary?.pricedParcelCount ?? 0}/${parcels.length.toLocaleString("ko-KR")}`} />
          <Metric label="VWorld 조회" value={data.geometryResolution ? `신규 ${data.geometryResolution.fetchedFromVworld ?? 0}건 · 캐시 ${data.geometryResolution.cacheHits ?? 0}건` : "미실행"} />
        </section>
      ) : null}

      {error ? <p className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-bold text-rose-700 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-200">{error}</p> : null}

      {loading && !data ? (
        <div className="flex min-h-[400px] items-center justify-center text-sm text-slate-500"><Loader2 className="mr-2 h-5 w-5 animate-spin" /> 지도 데이터를 불러오는 중입니다.</div>
      ) : data ? (
        <>
          <MapIntelligence data={data} transactions={markers} />
          <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-5 py-3 dark:border-slate-800">
              <div>
                <h2 className="font-black text-slate-950 dark:text-white">필지 명세 ({parcels.length.toLocaleString("ko-KR")}필지)</h2>
                <p className="mt-1 text-xs font-semibold text-slate-500">지도 선택 패널에서 전체 필지 또는 일부 필지를 선택해 확인할 수 있습니다.</p>
              </div>
            </div>
            {parcels.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-sm">
                  <thead className="bg-slate-50 text-left text-xs font-bold text-slate-500 dark:bg-slate-950/40">
                    <tr>
                      <th className="px-4 py-2">지번</th>
                      <th className="px-4 py-2">PNU</th>
                      <th className="px-4 py-2">경계</th>
                      <th className="px-4 py-2">지목</th>
                      <th className="px-4 py-2">면적</th>
                      <th className="px-4 py-2">공시지가(㎡)</th>
                      <th className="px-4 py-2">소유자</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {parcels.map((parcel, index) => {
                      const props = parcel.properties;
                      return (
                        <tr key={props.pnu ?? props.lotNumber ?? index} className="hover:bg-slate-50 dark:hover:bg-slate-950/40">
                          <td className="px-4 py-3 font-semibold text-slate-900 dark:text-white">{displayText(props.addressFragment ?? props.lotNumber, "미확인")}</td>
                          <td className="px-4 py-3 font-mono text-xs text-slate-500">{props.pnu ?? "미확인"}</td>
                          <td className="px-4 py-3"><span className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${parcel.geometry ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>{parcel.geometry ? "확인" : "미확인"}</span></td>
                          <td className="px-4 py-3 text-slate-700 dark:text-slate-200">{displayText(props.landCategory, "미확인")}</td>
                          <td className="px-4 py-3 text-slate-700 dark:text-slate-200">{formatSqm(props.areaSqm)}</td>
                          <td className="px-4 py-3 text-slate-700 dark:text-slate-200">
                            {props.officialLandPricePerSqm ? props.officialLandPricePerSqm.toLocaleString("ko-KR") : "미확인"}
                            {props.officialLandPriceBaseYear ? <span className="ml-1 text-[11px] text-slate-400">({props.officialLandPriceBaseYear})</span> : null}
                          </td>
                          <td className="px-4 py-3 text-slate-700 dark:text-slate-200">{displayText(props.owner, "미확인")}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="px-5 py-8 text-center text-sm text-slate-500">저장된 필지 데이터가 없습니다. 토지조서 또는 PNU 자료를 업로드하면 지도 분석이 표시됩니다.</p>
            )}
          </section>
          <TransactionsPanel lawdCd={lawdCd} geocode onRecords={setTxRecords} />
        </>
      ) : null}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900"><p className="text-xs font-bold text-slate-500">{label}</p><p className="mt-2 text-xl font-black text-slate-950 dark:text-white">{value}</p></div>;
}
