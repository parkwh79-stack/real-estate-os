"use client";

import { useCallback, useEffect, useState } from "react";
import { Building2, Loader2, MapPin, Search } from "lucide-react";
import { MapIntelligence, type MapIntelligenceData } from "../../components/map/map-intelligence";
import { TransactionsPanel } from "../../components/map/transactions-panel";
import { demoProject } from "../../lib/platform-data";

const apiBase = "http://127.0.0.1:8000";

type LookupResponse = {
  geocodeStatus?: string;
  center?: { lng: number; lat: number } | null;
  pnu?: string | null;
  lawdCd?: string | null;
  hasParcel?: boolean;
  map?: MapIntelligenceData;
};

function Fact({ label, value, tone }: { label: string; value: string; tone?: "good" | "warn" }) {
  const color =
    tone === "good"
      ? "text-emerald-600 dark:text-emerald-300"
      : tone === "warn"
        ? "text-amber-600 dark:text-amber-300"
        : "text-slate-950 dark:text-white";
  return (
    <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <p className={`mt-1 font-black ${color}`}>{value}</p>
    </div>
  );
}

export default function AnalysisPage() {
  const [address, setAddress] = useState(demoProject.address);
  const [mapData, setMapData] = useState<MapIntelligenceData | null>(null);
  const [lawdCd, setLawdCd] = useState<string | undefined>(undefined);
  const [pnu, setPnu] = useState<string>(demoProject.pnu);
  const [hasParcel, setHasParcel] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const analyze = useCallback(async (addr: string) => {
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({ address: addr, pnu: demoProject.pnu });
      const res = await fetch(`${apiBase}/api/market/parcel-lookup?${qs}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`대상지 분석 실패 (${res.status})`);
      const d = (await res.json()) as LookupResponse;
      if (d.map) setMapData(d.map);
      setLawdCd(d.lawdCd ?? demoProject.pnu.slice(0, 5));
      setPnu(d.pnu ?? demoProject.pnu);
      setHasParcel(Boolean(d.hasParcel));
    } catch (e) {
      setError(e instanceof Error ? e.message : "대상지 분석 중 오류가 발생했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void analyze(demoProject.address);
  }, [analyze]);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm md:flex-row dark:border-slate-800 dark:bg-slate-900">
        <label className="flex h-11 flex-1 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 text-sm dark:border-slate-800 dark:bg-slate-950">
          <Search className="h-4 w-4 text-slate-400" />
          <input
            type="text"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void analyze(address);
            }}
            placeholder="주소 (예: 대구광역시 달서구 본리동)"
            className="w-full bg-transparent font-medium text-slate-900 outline-none dark:text-white"
          />
        </label>
        <button
          type="button"
          onClick={() => void analyze(address)}
          disabled={loading}
          className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-40 dark:bg-white dark:text-slate-950 dark:hover:bg-slate-200"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <MapPin className="h-4 w-4" />}
          대상지 분석
        </button>
      </div>

      {error ? <p className="text-sm font-bold text-rose-600 dark:text-rose-300">{error}</p> : null}

      <div className="grid gap-4 xl:grid-cols-[1.5fr_0.9fr]">
        <div className="space-y-2">
          {mapData ? (
            <MapIntelligence data={mapData} />
          ) : (
            <div className="flex min-h-[520px] items-center justify-center rounded-lg border border-slate-200 text-sm text-slate-500 dark:border-slate-800">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" /> 지도 로딩 중
            </div>
          )}
          <p className="text-xs text-slate-500">
            {hasParcel
              ? "지적 필지를 표시했습니다. 우측 “공간 레이어”에서 VWorld 지적도/용도지역을 켤 수 있습니다."
              : "해당 지번의 정확한 필지를 찾지 못해 지역 중심으로 표시합니다. 동 단위 주소로 다시 시도해 보세요."}
          </p>
        </div>

        <aside className="space-y-4">
          <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <span className="rounded-md border border-indigo-200 bg-indigo-50 px-2 py-1 text-xs font-bold text-indigo-700 dark:border-indigo-900 dark:bg-indigo-950/40 dark:text-indigo-300">
                  {demoProject.assetType}
                </span>
                <h2 className="mt-3 text-xl font-black text-slate-950 dark:text-white">{demoProject.name}</h2>
                <p className="mt-1 text-sm text-slate-500">{address}</p>
                <p className="mt-1 font-mono text-xs text-slate-400">PNU {pnu}</p>
              </div>
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800">
                <Building2 className="h-5 w-5 text-slate-600 dark:text-slate-300" />
              </div>
            </div>
            <div className="mt-5 grid grid-cols-2 gap-3">
              <Fact label="대지면적" value="2,586.8㎡" />
              <Fact label="계획 용적률" value="760%" tone="good" />
              <Fact label="예상 연면적" value="19,659㎡" />
              <Fact label="개발 가능성" value={`${demoProject.pfScore}점`} tone="warn" />
            </div>
          </section>

          <TransactionsPanel lawdCd={lawdCd} geocode aggregate />
        </aside>
      </div>
    </div>
  );
}
