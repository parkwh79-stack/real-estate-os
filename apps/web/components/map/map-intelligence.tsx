"use client";

import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Map as MlMap, MapLayerMouseEvent, Marker as MlMarker, StyleSpecification } from "maplibre-gl";

export type TxMarker = { lat: number; lng: number; label: string; sublabel?: string; tone?: "trade" | "rent"; value?: number };

// 평단가(원/평) 구간별 색 — 시세 색상도. (수도권 기준 대략적 구간)
const PRICE_TIERS: { max: number; color: string; label: string }[] = [
  { max: 50_000_000, color: "#16a34a", label: "~5천만" },
  { max: 100_000_000, color: "#84cc16", label: "5천만~1억" },
  { max: 150_000_000, color: "#f59e0b", label: "1억~1.5억" },
  { max: 200_000_000, color: "#f97316", label: "1.5억~2억" },
  { max: Infinity, color: "#dc2626", label: "2억~" },
];
function tierColor(value?: number): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "#e11d48";
  return PRICE_TIERS.find((t) => value < t.max)?.color ?? "#dc2626";
}

type Center = { lng: number; lat: number } | null;
type Layer = { id: string; label: string; source: string; status: string; featureCount: number | null };

export interface MapIntelligenceData {
  center: Center;
  parcelGeoJson: { type: "FeatureCollection"; features: unknown[] };
  summary?: { parcelCount?: number; locatedParcelCount?: number; hasGeometry?: boolean };
  layers?: Layer[];
  dataLimitations?: string[];
}

const KOREA_CENTER: [number, number] = [127.6, 36.2];

const LAYER_STATUS_LABEL: Record<string, string> = {
  available: "표시 중",
  needs_geometry: "형상 필요",
  needs_data: "데이터 필요",
  planned: "예정",
};
const LAYER_STATUS_TONE: Record<string, string> = {
  available: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  needs_geometry: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  needs_data: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  planned: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
};

function baseStyle(vworldKey?: string): StyleSpecification {
  if (vworldKey) {
    return {
      version: 8,
      sources: {
        vworld: {
          type: "raster",
          // Same-origin proxy (/api/vworld) — avoids CORS and keeps the key server-side.
          tiles: ["/api/vworld?kind=base&z={z}&x={x}&y={y}"],
          tileSize: 256,
          attribution: "© VWorld (국토교통부)",
        },
      },
      layers: [{ id: "vworld", type: "raster", source: "vworld" }],
    };
  }
  return {
    version: 8,
    sources: {
      osm: {
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution: "© OpenStreetMap contributors",
      },
    },
    layers: [{ id: "osm", type: "raster", source: "osm" }],
  };
}

/** MapLibre GL Map Intelligence — base map (VWorld when keyed, else OSM) with a
 *  parcel GeoJSON overlay and the deck's spatial layer legend. */
export function MapIntelligence({ data, transactions = [] }: { data: MapIntelligenceData; transactions?: TxMarker[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  const markersRef = useRef<MlMarker[]>([]);
  const [parcelsVisible, setParcelsVisible] = useState(true);
  const [cadastralVisible, setCadastralVisible] = useState(false);
  const [zoningVisible, setZoningVisible] = useState(false);
  const [ready, setReady] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);
  const vworldKey = process.env.NEXT_PUBLIC_VWORLD_API_KEY;

  // Init map once.
  useEffect(() => {
    let map: MlMap | null = null;
    if (typeof window === "undefined" || !containerRef.current) return;
    try {
      const center: [number, number] = data.center ? [data.center.lng, data.center.lat] : KOREA_CENTER;
      map = new maplibregl.Map({
        container: containerRef.current,
        style: baseStyle(vworldKey),
        center,
        zoom: data.center ? 16 : 6,
        attributionControl: { compact: true },
      });
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      // Force a resize after layout settles (container size can be 0 at create time).
      const mapInstance = map;
      requestAnimationFrame(() => mapInstance.resize());
      setTimeout(() => mapInstance.resize(), 400);
      map.on("error", (e: unknown) => {
        const msg = (e as { error?: { message?: string } })?.error?.message;
        if (msg) console.warn("[map]", msg); // tile errors shouldn't blank the map
      });
      map.on("load", () => {
        if (!map) return;
        // VWorld WMS 편집지적도(lt_c_landinfobasemap) overlay — toggled off by
        // default. MapLibre fills {bbox-epsg-3857}; VWorld needs WMS 1.3.0 + EPSG:3857.
        if (vworldKey) {
          map.addSource("cadastral-wms", {
            type: "raster",
            // Proxied (VWorld WMS has no CORS header → must go same-origin).
            tiles: ["/api/vworld?kind=wms&bbox={bbox-epsg-3857}"],
            tileSize: 256,
          });
          map.addLayer({
            id: "cadastral-wms",
            type: "raster",
            source: "cadastral-wms",
            layout: { visibility: "none" },
            paint: { "raster-opacity": 0.7 },
          });
          // VWorld 용도지역(lt_c_uq111) WMS overlay — toggled off by default.
          map.addSource("zoning-wms", {
            type: "raster",
            tiles: ["/api/vworld?kind=wms&layers=lt_c_uq111&bbox={bbox-epsg-3857}"],
            tileSize: 256,
          });
          map.addLayer({
            id: "zoning-wms",
            type: "raster",
            source: "zoning-wms",
            layout: { visibility: "none" },
            paint: { "raster-opacity": 0.55 },
          });
        }
        map.addSource("parcels", { type: "geojson", data: data.parcelGeoJson as never });
        map.addLayer({
          id: "parcel-fill",
          type: "fill",
          source: "parcels",
          filter: ["==", ["geometry-type"], "Polygon"],
          paint: {
            // 공시지가(원/㎡) 색상도 — 값이 있으면 단계색, 없으면(0) 기본 인디고.
            "fill-color": [
              "case",
              ["==", ["coalesce", ["get", "officialLandPricePerSqm"], 0], 0],
              "#6366f1",
              [
                "interpolate",
                ["linear"],
                ["get", "officialLandPricePerSqm"],
                1_000_000, "#16a34a",
                3_000_000, "#84cc16",
                6_000_000, "#f59e0b",
                10_000_000, "#f97316",
                20_000_000, "#dc2626",
              ],
            ],
            "fill-opacity": 0.45,
          },
        });
        map.addLayer({
          id: "parcel-line",
          type: "line",
          source: "parcels",
          filter: ["==", ["geometry-type"], "Polygon"],
          paint: { "line-color": "#4338ca", "line-width": 2 },
        });
        map.addLayer({
          id: "parcel-point",
          type: "circle",
          source: "parcels",
          filter: ["==", ["geometry-type"], "Point"],
          paint: { "circle-radius": 7, "circle-color": "#4f46e5", "circle-stroke-color": "#fff", "circle-stroke-width": 2 },
        });
        const popup = new maplibregl.Popup({ closeButton: true, closeOnClick: true });
        const showPopup = (e: MapLayerMouseEvent) => {
          const f = e.features?.[0];
          if (!f || !map) return;
          const p = (f.properties ?? {}) as Record<string, unknown>;
          const price = p.officialLandPricePerSqm
            ? `${Number(p.officialLandPricePerSqm).toLocaleString("ko-KR")}원/㎡`
            : "—";
          popup
            .setLngLat(e.lngLat)
            .setHTML(
              `<div style="font-size:12px;line-height:1.5">
                <strong>${p.addressFragment ?? p.pnu ?? "필지"}</strong><br/>
                PNU ${p.pnu ?? "—"}<br/>
                지목 ${p.landCategory ?? "—"} · ${p.areaSqm ? `${Number(p.areaSqm).toLocaleString("ko-KR")}㎡` : "—"}<br/>
                공시지가 ${price}
              </div>`,
            )
            .addTo(map);
        };
        map.on("click", "parcel-fill", showPopup);
        map.on("click", "parcel-point", showPopup);
        setReady(true);
      });
      mapRef.current = map;
    } catch (err) {
      setMapError(err instanceof Error ? err.message : "지도를 초기화하지 못했습니다.");
    }
    return () => {
      map?.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Toggle parcel overlay visibility.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const v = parcelsVisible ? "visible" : "none";
    for (const id of ["parcel-fill", "parcel-line", "parcel-point"]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", v);
    }
  }, [parcelsVisible, ready]);

  // Toggle VWorld WMS 지적도 overlay.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !map.getLayer("cadastral-wms")) return;
    map.setLayoutProperty("cadastral-wms", "visibility", cadastralVisible ? "visible" : "none");
  }, [cadastralVisible, ready]);

  // Toggle VWorld WMS 용도지역 overlay.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !map.getLayer("zoning-wms")) return;
    map.setLayoutProperty("zoning-wms", "visibility", zoningVisible ? "visible" : "none");
  }, [zoningVisible, ready]);

  // Transaction price markers (Naver-style pills) — rebuilt when data changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    markersRef.current.forEach((m) => m.remove());
    markersRef.current = [];
    const pts = transactions.filter((t) => Number.isFinite(t.lat) && Number.isFinite(t.lng));
    for (const t of pts) {
      const el = document.createElement("div");
      const bg = t.tone === "rent" ? "#0ea5e9" : tierColor(t.value);
      el.style.cssText = `display:inline-flex;flex-direction:column;align-items:center;padding:3px 7px;border-radius:8px;background:${bg};color:#fff;font-size:11px;font-weight:800;white-space:nowrap;box-shadow:0 6px 14px rgba(15,23,42,.28);cursor:default;`;
      el.innerHTML = `<span>${t.label}</span>${t.sublabel ? `<span style="font-size:9px;font-weight:600;opacity:.9">${t.sublabel}</span>` : ""}`;
      const marker = new maplibregl.Marker({ element: el, anchor: "bottom" }).setLngLat([t.lng, t.lat]).addTo(map);
      markersRef.current.push(marker);
    }
    // Fit to markers when the parcel has no geometry to anchor the view.
    if (pts.length && !data.center) {
      const lngs = pts.map((p) => p.lng);
      const lats = pts.map((p) => p.lat);
      map.fitBounds(
        [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]],
        { padding: 64, maxZoom: 15, duration: 600 },
      );
    }
  }, [transactions, ready, data.center]);

  const layers = data.layers ?? [];

  return (
    <div className="grid gap-4 lg:grid-cols-[1.6fr_0.7fr]">
      <div className="relative h-[560px] overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
        <div ref={containerRef} className="h-full w-full" />
        {mapError ? (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-rose-50/95 p-4 text-center text-sm font-semibold text-rose-700 dark:bg-rose-950/80 dark:text-rose-200">
            지도 초기화 오류: {mapError}
          </div>
        ) : null}
        {!vworldKey ? (
          <div className="absolute left-3 top-3 z-10 rounded-md bg-white/90 px-2.5 py-1 text-[11px] font-semibold text-slate-600 shadow dark:bg-slate-900/90 dark:text-slate-300">
            OSM 베이스맵 · VWorld 키 설정 시 지적·공시지가 레이어 활성
          </div>
        ) : null}
        {transactions.some((t) => t.tone !== "rent" && typeof t.value === "number") ? (
          <div className="absolute bottom-3 left-3 z-10 rounded-md bg-white/90 px-2.5 py-1.5 text-[10px] font-semibold text-slate-600 shadow dark:bg-slate-900/90 dark:text-slate-300">
            <p className="mb-1 font-bold">평단가</p>
            <div className="flex items-center gap-1.5">
              {PRICE_TIERS.map((t) => (
                <span key={t.label} className="flex items-center gap-0.5">
                  <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: t.color }} />
                  {t.label}
                </span>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <aside className="space-y-4">
        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between">
            <h3 className="font-black text-slate-950 dark:text-white">공간 레이어</h3>
            <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-600 dark:text-slate-300">
              <input type="checkbox" checked={parcelsVisible} onChange={(e) => setParcelsVisible(e.target.checked)} />
              필지 오버레이
            </label>
          </div>
          {vworldKey ? (
            <div className="mt-2 space-y-1">
              <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-600 dark:text-slate-300">
                <input type="checkbox" checked={cadastralVisible} onChange={(e) => setCadastralVisible(e.target.checked)} />
                VWorld 지적도(편집지적도)
              </label>
              <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-600 dark:text-slate-300">
                <input type="checkbox" checked={zoningVisible} onChange={(e) => setZoningVisible(e.target.checked)} />
                VWorld 용도지역
              </label>
            </div>
          ) : null}
          <div className="mt-3 space-y-2">
            {layers.map((layer) => (
              <div key={layer.id} className="flex items-center justify-between gap-2 text-sm">
                <div className="min-w-0">
                  <p className="font-semibold text-slate-900 dark:text-white">{layer.label}</p>
                  <p className="truncate text-[11px] text-slate-400">{layer.source}</p>
                </div>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold ${LAYER_STATUS_TONE[layer.status] ?? LAYER_STATUS_TONE.planned}`}>
                  {LAYER_STATUS_LABEL[layer.status] ?? layer.status}
                  {typeof layer.featureCount === "number" && layer.featureCount > 0 ? ` (${layer.featureCount})` : ""}
                </span>
              </div>
            ))}
          </div>
        </section>

        {data.dataLimitations?.length ? (
          <section className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
            <p className="font-bold">데이터 한계</p>
            <ul className="mt-1 list-disc space-y-1 pl-4">
              {data.dataLimitations.map((note, i) => (
                <li key={i}>{note}</li>
              ))}
            </ul>
          </section>
        ) : null}
      </aside>
    </div>
  );
}
