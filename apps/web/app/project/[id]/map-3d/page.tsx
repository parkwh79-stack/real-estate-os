"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ArrowLeft, Box } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { MapIntelligenceData } from "../../../../components/map/map-intelligence";
import { apiBase, authHeaders } from "../../../../lib/auth-client";
import { projectWorkspaceHref } from "../../../../lib/project-navigation";

const ParcelMap = dynamic(() => import("../../../../components/map/map-intelligence").then((module) => module.MapIntelligence), {
  ssr: false,
  loading: () => <p role="status" className="text-sm text-slate-600 dark:text-slate-300">필지 지도를 준비하고 있습니다.</p>,
});
const browser3DConfigured = Boolean(process.env.NEXT_PUBLIC_VWORLD_API_KEY?.trim());
const actionClass = "inline-flex items-center justify-center rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800";

type MapState = { state: "loading" } | { state: "ready"; data: MapIntelligenceData } | { state: "unavailable" };

function validPosition(value: unknown): value is number[] {
  return Array.isArray(value) && value.length >= 2
    && value.every((item) => typeof item === "number" && Number.isFinite(item))
    && Math.abs(value[0] as number) <= 180 && Math.abs(value[1] as number) <= 90;
}

/** Frame only geometry returned for this exact project parcel; never borrow another PNU's center. */
function parcelCameraCenter(data: MapIntelligenceData, pnu: string): { lng: number; lat: number } | null {
  const matching = data.parcelGeoJson.features.filter((feature) => feature.properties?.pnu === pnu);
  const points: number[][] = [];
  const collect = (value: unknown, depth: number): boolean => {
    if (depth === 0) {
      if (!validPosition(value)) return false;
      points.push(value); return true;
    }
    return Array.isArray(value) && value.length > 0 && value.every((child) => collect(child, depth - 1));
  };
  if (!matching.length || !matching.every((feature) => {
    const geometry = feature.geometry;
    if (!geometry) return false;
    if (geometry.type === "Point") return collect(geometry.coordinates, 0);
    if (geometry.type === "Polygon") return collect(geometry.coordinates, 2);
    if (geometry.type === "MultiPolygon") return collect(geometry.coordinates, 3);
    return false;
  }) || !points.length) return null;
  const bounds = points.reduce((box, point) => [Math.min(box[0]!, point[0]!), Math.min(box[1]!, point[1]!),
    Math.max(box[2]!, point[0]!), Math.max(box[3]!, point[1]!)], [180, 90, -180, -90]);
  return { lng: (bounds[0]! + bounds[2]!) / 2, lat: (bounds[1]! + bounds[3]!) / 2 };
}

export default function Project3DMapPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const search = useSearchParams();
  const requestedPnu = search.get("pnu");
  const focusPnu = requestedPnu && /^[0-9]{10}[12][0-9]{8}$/.test(requestedPnu) ? requestedPnu : undefined;
  return <Project3DMap key={JSON.stringify([projectId, focusPnu])}
    projectId={projectId} focusPnu={focusPnu} track={search.get("track")} />;
}

function Project3DMap({ projectId, focusPnu, track }: { projectId: string; focusPnu?: string; track: string | null }) {
  const [mapState, setMapState] = useState<MapState>({ state: "loading" });
  const [sdkUnavailable, setSdkUnavailable] = useState(false);
  const [sdkReady, setSdkReady] = useState(false);
  const [sdkAttempt, setSdkAttempt] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const iframe = useRef<HTMLIFrameElement>(null);
  const mapPath = projectWorkspaceHref(projectId, "map", track);
  const mapHref = focusPnu ? `${mapPath}${mapPath.includes("?") ? "&" : "?"}pnu=${focusPnu}` : mapPath;

  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams({ resolve_geometry: "true", geometry_limit: focusPnu ? "1" : "30" });
    if (focusPnu) query.set("focus_pnu", focusPnu);
    void (async () => {
      try {
        const response = await fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}/map?${query}`, {
          cache: "no-store",
          headers: authHeaders("web"),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Project map unavailable");
        const data: MapIntelligenceData = await response.json();
        if (!Array.isArray(data?.parcelGeoJson?.features)) throw new Error("Invalid project map");
        if (!controller.signal.aborted) setMapState({ state: "ready", data });
      } catch {
        if (!controller.signal.aborted) setMapState({ state: "unavailable" });
      }
    })();
    return () => controller.abort();
  }, [attempt, focusPnu, projectId]);

  const data = mapState.state === "ready" ? mapState.data : null;
  const reportedCenter = data ? (focusPnu ? parcelCameraCenter(data, focusPnu) : data.center) : null;
  const projectCenter = reportedCenter && validPosition([reportedCenter.lng, reportedCenter.lat]) ? reportedCenter : null;
  const nationalReference = Boolean(data && !focusPnu && !projectCenter);
  // A national reference view is navigation context, never a fabricated project location.
  const center = projectCenter ?? (nationalReference ? { lng: 127.6, lat: 36.2 } : null);
  const iframeSrc = browser3DConfigured && !sdkUnavailable && center
    && Number.isFinite(center.lng) && Math.abs(center.lng) <= 180
    && Number.isFinite(center.lat) && Math.abs(center.lat) <= 90
    ? `/vworld-3d?${new URLSearchParams({ lng: String(center.lng), lat: String(center.lat), z: nationalReference ? "1000000" : "1800", attempt: String(sdkAttempt) })}`
    : null;

  useEffect(() => {
    if (!iframeSrc) return;
    const timeout = window.setTimeout(() => { setSdkReady(false); setSdkUnavailable(true); }, 45_000);
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.source !== iframe.current?.contentWindow) return;
      if (event.data === "realytics:vworld-3d-ready") { window.clearTimeout(timeout); setSdkReady(true); }
      if (event.data === "realytics:vworld-3d-unavailable") { window.clearTimeout(timeout); setSdkReady(false); setSdkUnavailable(true); }
    };
    window.addEventListener("message", onMessage);
    return () => {
      window.clearTimeout(timeout);
      window.removeEventListener("message", onMessage);
    };
  }, [iframeSrc]);

  const fallbackMessage = !browser3DConfigured || sdkUnavailable
    ? "현재 3D 지도를 불러올 수 없어 2D 필지 지도를 표시합니다."
    : focusPnu
      ? "선택한 필지의 확인된 위치가 없어 2D 지도에서 자료 상태를 표시합니다."
      : "프로젝트의 3D 지도 중심 위치를 확인할 수 없어 2D 필지 지도를 표시합니다.";

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link href={mapHref} aria-label="지도로 돌아가기" className={actionClass}>
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          </Link>
          <Box className="h-6 w-6 text-slate-700 dark:text-slate-200" aria-hidden="true" />
          <div>
            <h1 className="text-xl font-black text-slate-950 dark:text-white">3D 지도</h1>
            <p className="text-xs font-semibold text-slate-500">프로젝트 위치와 필지 경계를 확인합니다.</p>
          </div>
        </div>
        <Link href={mapHref} className={actionClass}>전체 지도·거래 분석 보기</Link>
      </header>

      {mapState.state === "loading" ? <p role="status" className="text-sm text-slate-600 dark:text-slate-300">필지 정보를 불러오고 있습니다.</p> : null}
      {mapState.state === "unavailable" ? (
        <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          <p role="status">프로젝트 지도 정보를 불러오지 못했습니다.</p>
          <button type="button" className={actionClass} onClick={() => { setMapState({ state: "loading" }); setAttempt(attempt + 1); }}>다시 불러오기</button>
        </div>
      ) : null}
      {iframeSrc ? (
        <section aria-label={nationalReference ? "전국 3D 참고지도" : "프로젝트 3D 지도"} className="space-y-2">
          {!sdkReady ? <p role="status" className="text-sm text-slate-600 dark:text-slate-300">3D 엔진과 지형을 불러오고 있습니다.</p> : null}
          {nationalReference ? <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">전국 3D 참고지도 · 사업 위치 미확인. 표시한 좌표는 사업장 위치가 아니며, 사업장 자료를 확인하면 해당 위치로 이동할 수 있습니다.</p> : null}
          {focusPnu ? <p className="text-sm text-slate-600 dark:text-slate-300">선택한 필지 주변의 3D 공간을 표시합니다. 필지 경계와 PNU 대조는 전체 지도에서 확인하세요.</p> : null}
          <div className="h-[440px] overflow-hidden rounded-lg border border-slate-200 sm:h-[640px] dark:border-slate-800">
            <iframe key={iframeSrc} ref={iframe} src={iframeSrc} title="VWorld 3D 지도" className="h-full w-full border-0" />
          </div>
        </section>
      ) : data ? (
        <section aria-label="2D 필지 지도" className="space-y-3">
          <p role="status" className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">{fallbackMessage}</p>
          {focusPnu && !data.parcelGeoJson.features.some((parcel) => parcel.properties?.pnu === focusPnu) ? (
            <p role="status" className="text-sm text-slate-600 dark:text-slate-300">이 프로젝트에서 요청한 필지를 확인할 수 없습니다.</p>
          ) : null}
          {browser3DConfigured && sdkUnavailable && center ? <button type="button" className={actionClass}
            onClick={() => { setSdkReady(false); setSdkUnavailable(false); setSdkAttempt((value) => value + 1); }}>3D 지도 다시 시도</button> : null}
          <ParcelMap data={data} focusPnu={focusPnu} compact />
        </section>
      ) : null}
    </div>
  );
}
