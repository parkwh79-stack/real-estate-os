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
  const center = data?.center;
  // The SDK shows the project center. Keep exact parcel selection on the parcel map.
  const iframeSrc = browser3DConfigured && !focusPnu && !sdkUnavailable && center
    && Number.isFinite(center.lng) && Math.abs(center.lng) <= 180
    && Number.isFinite(center.lat) && Math.abs(center.lat) <= 90
    ? `/vworld-3d?${new URLSearchParams({ lng: String(center.lng), lat: String(center.lat) })}`
    : null;

  useEffect(() => {
    if (!iframeSrc) return;
    const timeout = window.setTimeout(() => setSdkUnavailable(true), 25_000);
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.source !== iframe.current?.contentWindow) return;
      if (event.data === "realytics:vworld-3d-ready") window.clearTimeout(timeout);
      if (event.data === "realytics:vworld-3d-unavailable") setSdkUnavailable(true);
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
      ? "선택한 필지의 위치와 경계는 2D 지도에서 확인할 수 있습니다."
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
        <div className="h-[440px] overflow-hidden rounded-lg border border-slate-200 sm:h-[640px] dark:border-slate-800">
          <iframe ref={iframe} src={iframeSrc} title="VWorld 3D 지도" className="h-full w-full border-0" />
        </div>
      ) : data ? (
        <section aria-label="2D 필지 지도" className="space-y-3">
          <p role="status" className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">{fallbackMessage}</p>
          {focusPnu && !data.parcelGeoJson.features.some((parcel) => parcel.properties?.pnu === focusPnu) ? (
            <p role="status" className="text-sm text-slate-600 dark:text-slate-300">이 프로젝트에서 요청한 필지를 확인할 수 없습니다.</p>
          ) : null}
          <ParcelMap data={data} focusPnu={focusPnu} compact />
        </section>
      ) : null}
    </div>
  );
}
