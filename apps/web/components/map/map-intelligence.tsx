"use client";

/* eslint-disable react/prop-types -- component props are declared with TypeScript interfaces. */

import { useEffect, useMemo, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import { createParcelPopup } from "../../lib/parcel-popup";
import { vworldBrowserBaseTileTemplate } from "../../lib/vworld-request";
import "maplibre-gl/dist/maplibre-gl.css";
import type {
  GeoJSONSource,
  Map as MlMap,
  MapLayerMouseEvent,
  Marker as MlMarker,
  StyleSpecification,
} from "maplibre-gl";

export type TxMarker = {
  lat: number;
  lng: number;
  label: string;
  sublabel?: string;
  tone?: "trade" | "rent";
  value?: number;
};

type Center = { lng: number; lat: number } | null;
type Layer = { id: string; label: string; source: string; status: string; featureCount: number | null; detail?: string | null };
type ParcelProperties = {
  pnu?: string;
  addressFragment?: string;
  lotNumber?: string;
  landCategory?: string;
  areaSqm?: number;
  areaPy?: number;
  officialLandPricePerSqm?: number;
  officialLandPriceBaseYear?: string;
  owner?: string;
  selected?: boolean;
};
type ParcelFeature = GeoJSON.Feature<GeoJSON.Geometry | null, ParcelProperties> & { id?: string | number };
type ParcelFeatureCollection = GeoJSON.FeatureCollection<GeoJSON.Geometry | null, ParcelProperties>;

export interface MapIntelligenceData {
  center: Center;
  parcelGeoJson: ParcelFeatureCollection;
  summary?: { parcelCount?: number; locatedParcelCount?: number; boundaryParcelCount?: number; pointParcelCount?: number; pricedParcelCount?: number; hasGeometry?: boolean };
  layers?: Layer[];
  dataLimitations?: string[];
  geometryResolution?: {
    source?: string;
    status?: "not_configured" | "no_eligible_parcels" | "resolved" | "partial" | "unresolved";
    cacheBackend?: string;
    resolvedCount?: number;
    cacheHits?: number;
    fetchedFromVworld?: number;
  };
}

const KOREA_CENTER: [number, number] = [127.6, 36.2];

const PRICE_TIERS: { max: number; color: string; label: string }[] = [
  { max: 50_000_000, color: "#16a34a", label: "5천만 이하" },
  { max: 100_000_000, color: "#84cc16", label: "5천만~1억" },
  { max: 150_000_000, color: "#f59e0b", label: "1억~1.5억" },
  { max: 200_000_000, color: "#f97316", label: "1.5억~2억" },
  { max: Infinity, color: "#dc2626", label: "2억 이상" },
];

const LAYER_STATUS_LABEL: Record<string, string> = {
  available: "표시 가능",
  needs_geometry: "경계 필요",
  needs_data: "자료 필요",
  planned: "예정",
};

const LAYER_STATUS_TONE: Record<string, string> = {
  available: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  needs_geometry: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  needs_data: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  planned: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
};

function baseStyle(provider: "vworld" | "osm", publicKey?: string): StyleSpecification {
  if (provider === "vworld") {
    return {
      version: 8,
      sources: {
        vworld: {
          type: "raster",
          tiles: [vworldBrowserBaseTileTemplate(publicKey)],
          tileSize: 256,
          attribution: "VWorld",
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
        attribution: "OpenStreetMap contributors",
      },
    },
    layers: [{ id: "osm", type: "raster", source: "osm" }],
  };
}

function tierColor(value?: number): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "#e11d48";
  return PRICE_TIERS.find((tier) => value < tier.max)?.color ?? "#dc2626";
}

function parcelKey(feature: ParcelFeature, index: number): string {
  const p = feature.properties ?? {};
  return `${index}:${feature.id ?? p.pnu ?? p.lotNumber ?? p.addressFragment ?? "parcel"}`;
}

function parcelLabel(feature: ParcelFeature, index: number): string {
  const p = feature.properties ?? {};
  return p.addressFragment ?? p.lotNumber ?? p.pnu ?? `필지 ${index + 1}`;
}

export function parcelGeometryStatus(geometry: GeoJSON.Geometry | null | undefined) {
  if (geometry?.type === "Polygon" && validPolygon(geometry.coordinates)) return "boundary";
  if (geometry?.type === "MultiPolygon" && geometry.coordinates.length && geometry.coordinates.every(validPolygon)) return "boundary";
  if (geometry?.type === "Point" && validPosition(geometry.coordinates)) return "point";
  return "unlocated";
}

function validPosition(value: unknown): value is [number, number, number?] {
  return Array.isArray(value) && [2, 3].includes(value.length)
    && value.every((component) => typeof component === "number" && Number.isFinite(component))
    && Math.abs(value[0]) <= 180 && Math.abs(value[1]) <= 90;
}

function validPolygon(value: unknown): boolean {
  return Array.isArray(value) && value.length > 0 && value.every((ring: unknown) => {
    if (!Array.isArray(ring) || ring.length < 4 || !ring.every(validPosition)) return false;
    const [first, last] = [ring[0]!, ring[ring.length - 1]!];
    if (first[0] !== last[0] || first[1] !== last[1]
      || new Set(ring.slice(0, -1).map((position) => `${position[0]},${position[1]}`)).size < 3) return false;
    const area = ring.slice(1).reduce((sum, point, index) => {
      const previous = ring[index]!;
      return sum + (previous[0] - first[0]) * (point[1] - first[1]) - (point[0] - first[0]) * (previous[1] - first[1]);
    }, 0);
    return Math.abs(area) > 1e-18;
  });
}

export const PARCEL_GEOMETRY_LABEL = {
  boundary: "경계 확인",
  point: "참고 위치 · 경계 미확인",
  unlocated: "위치 미확인",
} as const;

function cloneWithSelection(data: ParcelFeatureCollection, selectedIds: Set<string>): ParcelFeatureCollection {
  return {
    type: "FeatureCollection",
    features: data.features.map((feature, index) => ({
      ...feature,
      geometry: parcelGeometryStatus(feature.geometry) === "unlocated" ? null : feature.geometry,
      id: feature.id ?? parcelKey(feature, index),
      properties: {
        ...(feature.properties ?? {}),
        selected: selectedIds.has(parcelKey(feature, index)),
      },
    })),
  };
}

function collectCoordinates(geometry: GeoJSON.Geometry | null | undefined): [number, number][] {
  if (!geometry) return [];
  if (!("coordinates" in geometry)) return [];
  const points: [number, number][] = [];
  const walk = (node: unknown) => {
    if (
      Array.isArray(node) &&
      validPosition(node)
    ) {
      points.push([node[0], node[1]]);
      return;
    }
    if (Array.isArray(node)) node.forEach(walk);
  };
  walk(geometry.coordinates);
  return points;
}

function fitFeatures(map: MlMap, features: ParcelFeature[], duration = 600) {
  const coords = features.flatMap((feature) => collectCoordinates(feature.geometry));
  if (!coords.length) return false;
  const lngs = coords.map((coord) => coord[0]);
  const lats = coords.map((coord) => coord[1]);
  map.fitBounds(
    [
      [Math.min(...lngs), Math.min(...lats)],
      [Math.max(...lngs), Math.max(...lats)],
    ],
    { padding: 80, maxZoom: 18, duration },
  );
  return true;
}

export function MapIntelligence({ data, transactions = [], compact = false, focusPnu }: {
  data: MapIntelligenceData;
  transactions?: TxMarker[];
  compact?: boolean;
  focusPnu?: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  const markersRef = useRef<MlMarker[]>([]);
  const [parcelsVisible, setParcelsVisible] = useState(true);
  const [cadastralVisible, setCadastralVisible] = useState(false);
  const [zoningVisible, setZoningVisible] = useState(false);
  const [ready, setReady] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);
  const [mapAttempt, setMapAttempt] = useState(0);
  const [mapNotice, setMapNotice] = useState<string | null>(null);
  const [layerNotice, setLayerNotice] = useState<string | null>(null);
  const [selection, setSelection] = useState<Set<string> | null>(null);
  const [showOnlySelected, setShowOnlySelected] = useState(Boolean(focusPnu));
  const vworldKey = process.env.NEXT_PUBLIC_VWORLD_API_KEY?.trim();
  const [baseProvider, setBaseProvider] = useState<"vworld" | "osm">(vworldKey ? "vworld" : "osm");

  const parcelFeatures = useMemo(() => data.parcelGeoJson.features as ParcelFeature[], [data.parcelGeoJson.features]);
  const allParcelIds = useMemo(() => parcelFeatures.map(parcelKey), [parcelFeatures]);
  const focusedFeatures = useMemo(
    () => focusPnu ? parcelFeatures.filter((feature) => feature.properties?.pnu === focusPnu) : [],
    [focusPnu, parcelFeatures],
  );
  const defaultSelection = useMemo(() => new Set(focusPnu
    ? parcelFeatures.flatMap((feature, index) => feature.properties?.pnu === focusPnu ? [parcelKey(feature, index)] : [])
    : allParcelIds), [allParcelIds, focusPnu, parcelFeatures]);
  const boundaryParcelIds = parcelFeatures.flatMap((feature, index) =>
    parcelGeometryStatus(feature.geometry) === "boundary" ? [parcelKey(feature, index)] : []);
  const selectedIds = selection ?? defaultSelection;
  const selectedFeatures = parcelFeatures.filter((feature, index) => selectedIds.has(parcelKey(feature, index)));
  const selectedGeoJson = useMemo(() => cloneWithSelection(data.parcelGeoJson, selectedIds), [data.parcelGeoJson, selectedIds]);
  const selectedCount = selectedFeatures.length;
  const boundarySelectedCount = selectedFeatures.filter((feature) => parcelGeometryStatus(feature.geometry) === "boundary").length;
  const canFitSelection = selectedFeatures.some((feature) => parcelGeometryStatus(feature.geometry) !== "unlocated");

  useEffect(() => {
    if (ready && focusPnu && selection === null && mapRef.current) fitFeatures(mapRef.current, focusedFeatures, 0);
  }, [focusPnu, focusedFeatures, ready, selection]);

  useEffect(() => {
    let map: MlMap | null = null;
    if (typeof window === "undefined" || !containerRef.current) return;
    setReady(false);
    setMapError(null);
    setMapNotice(null);
    setLayerNotice(null);
    let provider: "vworld" | "osm" = vworldKey ? "vworld" : "osm";
    setBaseProvider(provider);
    try {
      const candidateCenter = focusPnu ? null : data.center;
      const initialCenter = candidateCenter && Number.isFinite(candidateCenter.lng) && Number.isFinite(candidateCenter.lat)
        && Math.abs(candidateCenter.lng) <= 180 && Math.abs(candidateCenter.lat) <= 90 ? candidateCenter : null;
      const center: [number, number] = initialCenter ? [initialCenter.lng, initialCenter.lat] : KOREA_CENTER;
      map = new maplibregl.Map({
        container: containerRef.current,
        style: baseStyle(provider, vworldKey),
        center,
        zoom: initialCenter ? 16 : 6,
        attributionControl: { compact: true },
      });
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      requestAnimationFrame(() => map?.resize());
      setTimeout(() => map?.resize(), 400);
      map.on("error", (event: unknown) => {
        const sourceId = (event as { sourceId?: string })?.sourceId;
        if (sourceId === "cadastral-wms" || sourceId === "zoning-wms") {
          setLayerNotice("일부 지적도·용도지역 타일을 표시하지 못했습니다. 배경지도와 필지 경계는 계속 확인할 수 있습니다.");
          return;
        }
        if (sourceId === "vworld" && provider === "vworld" && map) {
          // Replace only the base source. Keep selected parcel GeoJSON and
          // overlays in place when the provider rejects or times out a tile.
          provider = "osm";
          setBaseProvider("osm");
          setMapNotice("VWorld 배경지도에 연결하지 못해 OpenStreetMap으로 전환했습니다.");
          if (map.getLayer("vworld")) map.removeLayer("vworld");
          if (map.getSource("vworld")) map.removeSource("vworld");
          const fallback = baseStyle("osm");
          map.addSource("osm", fallback.sources.osm!);
          map.addLayer(fallback.layers[0]!, map.getStyle().layers?.[0]?.id);
          return;
        }
        if (sourceId === "osm" && provider === "osm") {
          setMapError("배경지도를 불러오지 못했습니다. 필지 목록은 계속 확인할 수 있습니다.");
        }
      });
      map.on("load", () => {
        if (!map) return;
        if (vworldKey) {
          map.addSource("cadastral-wms", {
            type: "raster",
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
        map.addSource("parcels", { type: "geojson", data: selectedGeoJson as unknown as GeoJSON.GeoJSON });
        map.addLayer({
          id: "parcel-fill",
          type: "fill",
          source: "parcels",
          filter: ["==", ["geometry-type"], "Polygon"],
          paint: {
            "fill-color": [
              "case",
              ["==", ["get", "selected"], true],
              "#0ea5e9",
              ["==", ["coalesce", ["get", "officialLandPricePerSqm"], 0], 0],
              "#6366f1",
              [
                "interpolate",
                ["linear"],
                ["get", "officialLandPricePerSqm"],
                1_000_000,
                "#16a34a",
                3_000_000,
                "#84cc16",
                6_000_000,
                "#f59e0b",
                10_000_000,
                "#f97316",
                20_000_000,
                "#dc2626",
              ],
            ] as never,
            "fill-opacity": ["case", ["==", ["get", "selected"], true], 0.58, 0.28] as never,
          },
        });
        map.addLayer({
          id: "parcel-line",
          type: "line",
          source: "parcels",
          filter: ["==", ["geometry-type"], "Polygon"],
          paint: {
            "line-color": ["case", ["==", ["get", "selected"], true], "#0284c7", "#4338ca"] as never,
            "line-width": ["case", ["==", ["get", "selected"], true], 3, 1.5] as never,
          },
        });
        map.addLayer({
          id: "parcel-point",
          type: "circle",
          source: "parcels",
          filter: ["==", ["geometry-type"], "Point"],
          paint: {
            "circle-radius": ["case", ["==", ["get", "selected"], true], 8, 5] as never,
            "circle-color": ["case", ["==", ["get", "selected"], true], "#0ea5e9", "#4f46e5"] as never,
            "circle-stroke-color": "#fff",
            "circle-stroke-width": 2,
          },
        });
        const popup = new maplibregl.Popup({ closeButton: true, closeOnClick: true });
        const showPopup = (event: MapLayerMouseEvent) => {
          const feature = event.features?.[0];
          if (!feature || !map) return;
          const p = (feature.properties ?? {}) as Record<string, unknown>;
          popup
            .setLngLat(event.lngLat)
            .setDOMContent(createParcelPopup(p, document))
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
  }, [mapAttempt]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource("parcels") as GeoJSONSource | undefined;
    source?.setData(selectedGeoJson as unknown as GeoJSON.GeoJSON);
  }, [selectedGeoJson, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const visibility = parcelsVisible ? "visible" : "none";
    for (const id of ["parcel-fill", "parcel-line", "parcel-point"]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visibility);
    }
  }, [parcelsVisible, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    for (const [id, geometry] of [["parcel-fill", "Polygon"], ["parcel-line", "Polygon"], ["parcel-point", "Point"]] as const) {
      const geometryFilter = ["==", ["geometry-type"], geometry];
      const filter = showOnlySelected ? ["all", geometryFilter, ["==", ["get", "selected"], true]] : geometryFilter;
      if (map.getLayer(id)) map.setFilter(id, filter as never);
    }
  }, [showOnlySelected, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !map.getLayer("cadastral-wms")) return;
    map.setLayoutProperty("cadastral-wms", "visibility", cadastralVisible ? "visible" : "none");
  }, [cadastralVisible, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !map.getLayer("zoning-wms")) return;
    map.setLayoutProperty("zoning-wms", "visibility", zoningVisible ? "visible" : "none");
  }, [zoningVisible, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    markersRef.current.forEach((marker) => marker.remove());
    markersRef.current = [];
    const points = transactions.filter((tx) => Number.isFinite(tx.lat) && Number.isFinite(tx.lng));
    for (const tx of points) {
      const element = document.createElement("div");
      const bg = tx.tone === "rent" ? "#0ea5e9" : tierColor(tx.value);
      element.style.cssText = `display:inline-flex;flex-direction:column;align-items:center;padding:3px 7px;border-radius:8px;background:${bg};color:#fff;font-size:11px;font-weight:800;white-space:nowrap;box-shadow:0 6px 14px rgba(15,23,42,.28);`;
      const label = document.createElement("span");
      label.textContent = tx.label;
      element.append(label);
      if (tx.sublabel) {
        const sublabel = document.createElement("span");
        sublabel.textContent = tx.sublabel;
        sublabel.style.cssText = "font-size:9px;font-weight:600;opacity:.9";
        element.append(sublabel);
      }
      const marker = new maplibregl.Marker({ element, anchor: "bottom" }).setLngLat([tx.lng, tx.lat]).addTo(map);
      markersRef.current.push(marker);
    }
    if (points.length && !data.center && !focusPnu) {
      const lngs = points.map((point) => point.lng);
      const lats = points.map((point) => point.lat);
      map.fitBounds(
        [
          [Math.min(...lngs), Math.min(...lats)],
          [Math.max(...lngs), Math.max(...lats)],
        ],
        { padding: 64, maxZoom: 15, duration: compact ? 0 : 600 },
      );
    } else if (compact && !data.center) {
      map.jumpTo({ center: KOREA_CENTER, zoom: 6 });
    }
  }, [transactions, ready, data.center, compact, focusPnu]);

  const fitSelected = () => {
    const map = mapRef.current;
    if (!map) return;
    fitFeatures(map, selectedFeatures);
  };

  const toggleParcel = (id: string) => {
    setSelection((prev) => {
      const next = new Set(prev ?? defaultSelection);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const layers = data.layers ?? [];

  return (
    <div className={compact ? "min-w-0" : "grid gap-4 xl:grid-cols-[1.55fr_0.85fr]"}>
      <div className={`relative overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800 ${compact ? "h-80 sm:h-[360px]" : "h-[620px]"}`}>
        <div ref={containerRef} className="h-full w-full" />
        {mapError ? (
          <div role="alert" className="absolute left-3 right-3 top-12 z-20 rounded-md bg-rose-50/95 p-3 text-sm font-semibold text-rose-700 shadow dark:bg-rose-950/80 dark:text-rose-200">
            {mapError}
            <button type="button" onClick={() => setMapAttempt((attempt) => attempt + 1)} className="ml-3 rounded border border-current px-2 py-1">지도 다시 시도</button>
          </div>
        ) : null}
        <div className="absolute left-3 top-3 z-10 rounded-md bg-white/90 px-2.5 py-1 text-[11px] font-semibold text-slate-600 shadow dark:bg-slate-900/90 dark:text-slate-300">
          {baseProvider === "vworld" ? "VWorld · 배경지도" : "OpenStreetMap · 배경지도"}
        </div>
        {mapNotice && !mapError ? <div role="status" className="absolute left-3 right-12 top-12 z-10 rounded-md bg-amber-50/95 p-2 text-xs text-amber-900 shadow">
          {mapNotice}
          <button type="button" onClick={() => setMapAttempt((attempt) => attempt + 1)} className="ml-2 underline">VWorld 다시 시도</button>
        </div> : null}
        {layerNotice ? <p role="status" className="absolute bottom-20 left-3 right-3 z-10 rounded-md bg-white/95 p-2 text-xs text-slate-700 shadow">{layerNotice}</p> : null}
        {!compact ? <div className="absolute bottom-3 left-3 z-10 rounded-md bg-white/90 px-2.5 py-1.5 text-[10px] font-semibold text-slate-600 shadow dark:bg-slate-900/90 dark:text-slate-300">
          <p className="mb-1 font-bold">공시지가 색상</p>
          <div className="flex flex-wrap items-center gap-1.5">
            {PRICE_TIERS.map((tier) => (
              <span key={tier.label} className="flex items-center gap-0.5">
                <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: tier.color }} />
                {tier.label}
              </span>
            ))}
          </div>
        </div> : null}
      </div>

      {!compact ? <aside className="space-y-4">
        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="font-black text-slate-950 dark:text-white">필지 선택</h3>
              <p className="mt-1 text-xs font-semibold text-slate-500">
                선택 {selectedCount}/{allParcelIds.length}필지 · 선택한 필지 중 경계 확인 {boundarySelectedCount}필지
              </p>
            </div>
            <button
              type="button"
              onClick={fitSelected}
              disabled={!canFitSelection}
              className="rounded-md border border-slate-200 px-2.5 py-1 text-xs font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              선택 필지 보기
            </button>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs font-bold">
            <button type="button" className="rounded-md bg-slate-900 px-2 py-1.5 text-white dark:bg-white dark:text-slate-950" onClick={() => setSelection(new Set(allParcelIds))}>
              전체 선택
            </button>
            <button type="button" className="rounded-md border border-slate-200 px-2 py-1.5 text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setSelection(new Set())}>
              전체 해제
            </button>
            <button type="button" className="rounded-md border border-slate-200 px-2 py-1.5 text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setSelection(new Set(boundaryParcelIds))}>
              경계 있는 필지만
            </button>
            <button type="button" className="rounded-md border border-slate-200 px-2 py-1.5 text-slate-700 dark:border-slate-700 dark:text-slate-200" onClick={() => setShowOnlySelected((value) => !value)}>
              {showOnlySelected ? "전체 표시" : "선택만 표시"}
            </button>
          </div>
          <div className="mt-3 max-h-[270px] space-y-1 overflow-auto rounded-md border border-slate-100 p-2 dark:border-slate-800">
            {parcelFeatures.map((feature, index) => {
              const id = parcelKey(feature, index);
              const props = feature.properties ?? {};
              return (
                <label key={id} className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 hover:bg-slate-50 dark:hover:bg-slate-950/50">
                  <input type="checkbox" className="mt-1" checked={selectedIds.has(id)} onChange={() => toggleParcel(id)} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-bold text-slate-900 dark:text-white">{parcelLabel(feature, index)}</span>
                    <span className="block text-[11px] text-slate-500">PNU {props.pnu ?? "미확인"} · {PARCEL_GEOMETRY_LABEL[parcelGeometryStatus(feature.geometry)]}</span>
                  </span>
                </label>
              );
            })}
          </div>
        </section>

        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between">
            <h3 className="font-black text-slate-950 dark:text-white">공간 레이어</h3>
            <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-600 dark:text-slate-300">
              <input type="checkbox" checked={parcelsVisible} onChange={(event) => setParcelsVisible(event.target.checked)} />
              필지 오버레이
            </label>
          </div>
          {vworldKey ? (
            <div className="mt-2 space-y-1">
              <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-600 dark:text-slate-300">
                <input type="checkbox" checked={cadastralVisible} onChange={(event) => setCadastralVisible(event.target.checked)} />
                VWorld 지적도
              </label>
              <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-600 dark:text-slate-300">
                <input type="checkbox" checked={zoningVisible} onChange={(event) => setZoningVisible(event.target.checked)} />
                VWorld 용도지역
              </label>
            </div>
          ) : null}
          <div className="mt-3 space-y-2">
            {layers.map((layer) => (
              <div key={layer.id} className="flex items-start justify-between gap-2 text-sm">
                <div className="min-w-0">
                  <p className="font-semibold text-slate-900 dark:text-white">{layer.label}</p>
                  <p className="truncate text-[11px] text-slate-400">{layer.source}</p>
                  {layer.detail ? <p className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">{layer.detail}</p> : null}
                </div>
                <span className={`mt-0.5 shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold ${LAYER_STATUS_TONE[layer.status] ?? LAYER_STATUS_TONE.planned}`}>
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
      </aside> : null}
    </div>
  );
}
