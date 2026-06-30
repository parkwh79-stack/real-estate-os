"use client";

import { useCallback, useEffect, useState } from "react";
import { Building2, Loader2, MapPin, Search } from "lucide-react";
import { MapIntelligence, type MapIntelligenceData } from "../../components/map/map-intelligence";
import { TransactionsPanel } from "../../components/map/transactions-panel";
import { ProjectPicker, getStoredSelectedProjectId } from "../../components/project/project-picker";
import { fetchProjectDetail } from "../../lib/project-client";
import { apiBase } from "../../lib/auth-client";

const TYPE_LABEL: Record<string, string> = {
  mixed_use: "주상복합 개발",
  logistics: "물류센터 개발",
  residential: "공동주택 개발",
  office: "오피스 개발",
  lodging: "숙박시설 개발",
  land_feasibility: "토지 사업성",
};

interface ProjectDetail {
  project?: { name?: string; type?: string };
  land?: { address?: string; pnu?: string; area_sqm?: number };
  regulation?: { applied_far?: number; applied_bcr?: number };
  architectural_plan?: { gross_floor_area_sqm?: number };
  expected_results?: { profit_margin?: number };
}

function sqm(v?: number): string {
  return typeof v === "number" ? `${v.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}㎡` : "—";
}
function pctOf(v?: number, alreadyPercent = false): string {
  if (typeof v !== "number") return "—";
  return `${(alreadyPercent ? v : v * 100).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%`;
}

type LookupResponse = {
  geocodeStatus?: string;
  center?: { lng: number; lat: number } | null;
  pnu?: string | null;
  lawdCd?: string | null;
  hasParcel?: boolean;
  restrictedRights?: string[];
  map?: MapIntelligenceData;
};

type Layer = { id: string; label: string; source: string; status: string; featureCount: number | null; detail?: string | null };

type RadiusInfo = { radius: number; avgPerSqm: number | null; medianPerSqm: number | null; count?: number; sampleCount?: number; landDealCount?: number };
type LandDeal = { address?: string; areaSqm?: number; totalPrice?: number; transactionDate?: string; distanceM: number };
type RadiusData = { radii: RadiusInfo[]; landDeals: LandDeal[] };

function man(won?: number | null): string {
  return won ? `${Math.round(won / 10_000).toLocaleString("ko-KR")}만` : "—";
}
function eokN(won?: number | null): string {
  return won ? `${(won / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억` : "—";
}

const STATUS_LABEL: Record<string, string> = {
  available: "표시 중",
  needs_geometry: "형상 필요",
  needs_data: "데이터 필요",
  planned: "예정",
};
const STATUS_TONE: Record<string, string> = {
  available: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200",
  needs_geometry: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200",
  needs_data: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  planned: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
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
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [address, setAddress] = useState("");
  const [mapData, setMapData] = useState<MapIntelligenceData | null>(null);
  const [lawdCd, setLawdCd] = useState<string | undefined>(undefined);
  const [pnu, setPnu] = useState<string>("");
  const [hasParcel, setHasParcel] = useState(false);
  const [restrictedRights, setRestrictedRights] = useState<string[]>([]);
  const [radiusData, setRadiusData] = useState<RadiusData | null>(null);
  const [selRadius, setSelRadius] = useState(500);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const analyze = useCallback(async (addr: string, seedPnu = "") => {
    if (!addr.trim()) return;
    setLoading(true);
    setError(null);
    setRadiusData(null);
    try {
      const qs = new URLSearchParams({ address: addr });
      if (seedPnu) qs.set("pnu", seedPnu);
      const res = await fetch(`${apiBase}/api/market/parcel-lookup?${qs}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`대상지 분석 실패 (${res.status})`);
      const d = (await res.json()) as LookupResponse;
      if (d.map) setMapData(d.map);
      setLawdCd(d.lawdCd ?? ((d.pnu ?? seedPnu).slice(0, 5) || undefined));
      setPnu(d.pnu ?? seedPnu);
      setHasParcel(Boolean(d.hasParcel));
      setRestrictedRights(Array.isArray(d.restrictedRights) ? d.restrictedRights : []);
      // 반경별 공시지가·토지 실거래
      const center = d.center ?? d.map?.center ?? null;
      const lawd = d.lawdCd ?? (d.pnu ?? seedPnu).slice(0, 5);
      if (center && lawd) {
        const rqs = new URLSearchParams({ lng: String(center.lng), lat: String(center.lat), lawd_cd: lawd });
        fetch(`${apiBase}/api/market/land-radius?${rqs}`, { cache: "no-store" })
          .then((r) => (r.ok ? r.json() : null))
          .then((rd) => setRadiusData(rd))
          .catch(() => setRadiusData(null));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "대상지 분석 중 오류가 발생했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadProject = useCallback(
    async (id: string) => {
      setSelectedId(id);
      setError(null);
      try {
        const detail = await fetchProjectDetail<ProjectDetail>(id);
        setProject(detail);
        const addr = detail.land?.address ?? "";
        const projPnu = detail.land?.pnu ?? "";
        setAddress(addr);
        setPnu(projPnu);
        if (addr) void analyze(addr, projPnu);
      } catch (e) {
        setError(e instanceof Error ? e.message : "프로젝트를 불러오지 못했습니다.");
      }
    },
    [analyze],
  );

  // Open on the previously-selected real project (no demo seed).
  useEffect(() => {
    const stored = getStoredSelectedProjectId();
    if (stored) void loadProject(stored);
  }, [loadProject]);

  const assetType = project?.project?.type ? TYPE_LABEL[project.project.type] ?? project.project.type : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm md:flex-row md:items-center dark:border-slate-800 dark:bg-slate-900">
        <div className="md:w-80">
          <ProjectPicker value={selectedId} onChange={(id) => void loadProject(id)} label="대상 프로젝트" />
        </div>
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
            <div className="flex min-h-[520px] items-center justify-center rounded-lg border border-dashed border-slate-300 text-sm text-slate-500 dark:border-slate-700">
              {loading ? (
                <span className="inline-flex items-center"><Loader2 className="mr-2 h-5 w-5 animate-spin" /> 지도 로딩 중</span>
              ) : (
                "상단에서 심사 프로젝트를 선택하거나 주소를 입력해 대상지를 분석하세요."
              )}
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
                {assetType ? (
                  <span className="rounded-md border border-indigo-200 bg-indigo-50 px-2 py-1 text-xs font-bold text-indigo-700 dark:border-indigo-900 dark:bg-indigo-950/40 dark:text-indigo-300">
                    {assetType}
                  </span>
                ) : null}
                <h2 className="mt-3 text-xl font-black text-slate-950 dark:text-white">
                  {project?.project?.name ?? "프로젝트를 선택하세요"}
                </h2>
                <p className="mt-1 text-sm text-slate-500">{address || "—"}</p>
                <p className="mt-1 font-mono text-xs text-slate-400">PNU {pnu || "—"}</p>
              </div>
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800">
                <Building2 className="h-5 w-5 text-slate-600 dark:text-slate-300" />
              </div>
            </div>
            <div className="mt-5 grid grid-cols-2 gap-3">
              <Fact label="대지면적" value={sqm(project?.land?.area_sqm)} />
              <Fact label="계획 용적률" value={pctOf(project?.regulation?.applied_far, true)} tone="good" />
              <Fact label="예상 연면적" value={sqm(project?.architectural_plan?.gross_floor_area_sqm)} />
              <Fact label="예상 수익률" value={pctOf(project?.expected_results?.profit_margin)} tone="warn" />
            </div>
          </section>

          <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <h3 className="font-black text-slate-950 dark:text-white">토지 분석 요약</h3>
            <p className="mb-3 text-[11px] font-semibold text-slate-500">공시지가·용도지역·시장성은 Marketability, 제한물권은 Legal 에이전트 연동</p>
            <div className="space-y-2">
              {((mapData?.layers ?? []) as Layer[])
                .filter((l) => l.id !== "parcel_boundary")
                .map((l) => (
                  <div key={l.id} className="flex items-start justify-between gap-2 text-sm">
                    <div className="min-w-0">
                      <p className="font-semibold text-slate-900 dark:text-white">{l.label}</p>
                      {l.detail ? <p className="text-[11px] text-slate-500">{l.detail}</p> : <p className="text-[11px] text-slate-400">{l.source}</p>}
                    </div>
                    <span className={`mt-0.5 shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold ${STATUS_TONE[l.status] ?? STATUS_TONE.planned}`}>
                      {STATUS_LABEL[l.status] ?? l.status}
                      {typeof l.featureCount === "number" && l.featureCount > 0 ? ` (${l.featureCount})` : ""}
                    </span>
                  </div>
                ))}
            </div>
            {restrictedRights.length ? (
              <div className="mt-3 rounded-lg bg-rose-50 p-3 text-xs leading-5 text-rose-800 dark:bg-rose-950/30 dark:text-rose-200">
                <p className="font-bold">제한물권 (등기 기준)</p>
                <ul className="mt-1 list-disc pl-4">
                  {restrictedRights.slice(0, 6).map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="mt-3 rounded-lg bg-slate-50 p-3 text-[11px] leading-5 text-slate-500 dark:bg-slate-800/60">
                등기부 증빙을 첨부하면 저당권·압류·지상권 등 제한물권을 Legal 에이전트가 자동 점검해 표시합니다.
              </p>
            )}
          </section>

          {radiusData ? (
            <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
              <h3 className="font-black text-slate-950 dark:text-white">반경별 공시지가 · 토지 실거래</h3>
              <p className="mb-3 text-[11px] font-semibold text-slate-500">대상지 기준 · 공시지가=VWorld 지적, 토지거래=국토부 (최근 3개월)</p>
              <div className="inline-flex rounded-lg border border-slate-200 p-0.5 text-xs font-bold dark:border-slate-700">
                {[500, 1000, 2000].map((r) => (
                  <button
                    key={r}
                    type="button"
                    onClick={() => setSelRadius(r)}
                    className={`rounded-md px-3 py-1.5 transition-colors ${
                      selRadius === r ? "bg-slate-900 text-white dark:bg-white dark:text-slate-950" : "text-slate-600 dark:text-slate-300"
                    }`}
                  >
                    {r >= 1000 ? `${r / 1000}km` : `${r}m`}
                  </button>
                ))}
              </div>
              {(() => {
                const info = radiusData.radii.find((x) => x.radius === selRadius);
                if (!info) return null;
                return (
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
                      <p className="text-[11px] font-semibold text-slate-500">평균 공시지가</p>
                      <p className="mt-1 font-black text-slate-950 dark:text-white">{man(info.avgPerSqm)}/㎡</p>
                    </div>
                    <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
                      <p className="text-[11px] font-semibold text-slate-500">중위 공시지가</p>
                      <p className="mt-1 font-black text-slate-950 dark:text-white">{man(info.medianPerSqm)}/㎡</p>
                    </div>
                    <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
                      <p className="text-[11px] font-semibold text-slate-500">필지 표본</p>
                      <p className="mt-1 font-black text-slate-950 dark:text-white">{(info.sampleCount ?? 0).toLocaleString("ko-KR")}/{(info.count ?? 0).toLocaleString("ko-KR")}</p>
                    </div>
                    <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/70">
                      <p className="text-[11px] font-semibold text-slate-500">반경내 토지거래</p>
                      <p className="mt-1 font-black text-slate-950 dark:text-white">{info.landDealCount ?? 0}건</p>
                    </div>
                  </div>
                );
              })()}
              <p className="mt-3 mb-1 text-[11px] font-bold text-slate-500">인근 토지 실거래 (가까운 순)</p>
              {radiusData.landDeals.length ? (
                <div className="max-h-56 space-y-1 overflow-y-auto">
                  {radiusData.landDeals.map((x, i) => (
                    <div key={i} className={`flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-xs ${x.distanceM <= selRadius ? "bg-emerald-50 dark:bg-emerald-950/30" : "bg-slate-50 dark:bg-slate-800/50"}`}>
                      <span className="min-w-0 truncate text-slate-700 dark:text-slate-200">{x.address ?? "—"}</span>
                      <span className="shrink-0 text-slate-500">
                        {x.distanceM < 1000 ? `${x.distanceM}m` : `${(x.distanceM / 1000).toFixed(1)}km`} · {eokN(x.totalPrice)}
                        {x.areaSqm ? ` · ${x.areaSqm}㎡` : ""}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-[11px] text-slate-400">최근 3개월 인근 토지 실거래가 없습니다.</p>
              )}
            </section>
          ) : null}

          <TransactionsPanel lawdCd={lawdCd} geocode aggregate />
        </aside>
      </div>
    </div>
  );
}
