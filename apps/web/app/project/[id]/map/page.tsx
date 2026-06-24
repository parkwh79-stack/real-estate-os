"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { ArrowLeft, Loader2, Map as MapIcon, RefreshCw } from "lucide-react";
import { authHeaders } from "../../../../lib/auth-client";
import { MapIntelligence, type MapIntelligenceData } from "../../../../components/map/map-intelligence";

const apiBase = "http://127.0.0.1:8000";

type ParcelFeature = {
  properties: {
    pnu?: string;
    addressFragment?: string;
    landCategory?: string;
    areaSqm?: number;
    officialLandPricePerSqm?: number;
    officialLandPriceBaseYear?: string;
    owner?: string;
  };
};

export default function ProjectMapPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [data, setData] = useState<MapIntelligenceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/map?resolve_geometry=true`, {
        cache: "no-store",
        headers: authHeaders("web"),
      });
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

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link
            href={`/project/${projectId}`}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div className="flex items-center gap-2">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-900 text-white dark:bg-white dark:text-slate-950">
              <MapIcon className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-xl font-black text-slate-950 dark:text-white">Map Intelligence</h1>
              <p className="text-xs font-semibold text-slate-500">필지 경계 · 공시지가 · 용도지역 공간 분석</p>
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

      {loading && !data ? (
        <div className="flex min-h-[400px] items-center justify-center text-sm text-slate-500">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" /> 지도 데이터 로딩 중
        </div>
      ) : data ? (
        <>
          <MapIntelligence data={data} />

          <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <div className="border-b border-slate-200 px-5 py-3 dark:border-slate-800">
              <h2 className="font-black text-slate-950 dark:text-white">필지 명세 ({parcels.length})</h2>
            </div>
            {parcels.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-sm">
                  <thead className="bg-slate-50 text-left text-xs font-bold text-slate-500 dark:bg-slate-950/40">
                    <tr>
                      <th className="px-4 py-2">지번</th>
                      <th className="px-4 py-2">PNU</th>
                      <th className="px-4 py-2">지목</th>
                      <th className="px-4 py-2">면적</th>
                      <th className="px-4 py-2">공시지가(원/㎡)</th>
                      <th className="px-4 py-2">소유자</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {parcels.map((p, i) => {
                      const pr = p.properties;
                      return (
                        <tr key={pr.pnu ?? i} className="hover:bg-slate-50 dark:hover:bg-slate-950/40">
                          <td className="px-4 py-3 font-semibold text-slate-900 dark:text-white">{pr.addressFragment ?? "—"}</td>
                          <td className="px-4 py-3 font-mono text-xs text-slate-500">{pr.pnu ?? "—"}</td>
                          <td className="px-4 py-3 text-slate-700 dark:text-slate-200">{pr.landCategory ?? "—"}</td>
                          <td className="px-4 py-3 text-slate-700 dark:text-slate-200">
                            {pr.areaSqm ? `${pr.areaSqm.toLocaleString("ko-KR")}㎡` : "—"}
                          </td>
                          <td className="px-4 py-3 text-slate-700 dark:text-slate-200">
                            {pr.officialLandPricePerSqm ? pr.officialLandPricePerSqm.toLocaleString("ko-KR") : "—"}
                            {pr.officialLandPriceBaseYear ? (
                              <span className="ml-1 text-[11px] text-slate-400">({pr.officialLandPriceBaseYear})</span>
                            ) : null}
                          </td>
                          <td className="px-4 py-3 text-slate-700 dark:text-slate-200">{pr.owner ?? "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="px-5 py-8 text-center text-sm text-slate-500">
                토지대장/필지 데이터가 없습니다. 토지조서를 업로드하면 필지가 표시됩니다.
              </p>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}
