# Map Intelligence (공간 데이터 OS)

Updated: 2026-06-24

The deck's "Map Intelligence / 6-layer spatial data OS". This is the first
working slice: a project-level map that overlays the project's parcels on a
Korean base map, with a layer-availability legend. It is **deterministic and
non-fabricating** — no coordinate or boundary is invented. A parcel with no
resolved geometry is shown as an unlocated GeoJSON feature (attributes only) and
flagged in `dataLimitations`; the boundary appears only once a real shape is
supplied.

## Map stack (decided 2026-06-24)

- **Renderer: MapLibre GL** (open-source, no key, no SDK lock-in). Renders any
  raster/vector base + our own GeoJSON overlays.
- **Korean base map + spatial data: VWorld** (국토교통부 공간정보 오픈플랫폼).
  Free API key (registration only, no partnership). Provides base tiles +
  지적도 + 공시지가 + 용도지역. The land connector already accepts
  `VWORLD_API_KEY` / `REALYTICS_VWORLD_API_KEY` for cadastral + official price.
- **Proprietary layers: our own PostGIS** `land_parcels` (geometry MultiPolygon
  4326 + GIST index) — already scaffolded in `db/postgres.py`.
- **Not base maps:** 밸류맵 / 부동산플래닛 are competing services / data vendors,
  not embeddable map SDKs. They are tracked as *future data-partner integrations*
  (🔒 partnership), not the rendering layer.

### Base map selection

The frontend (`components/map/map-intelligence.tsx`) uses VWorld raster tiles
when `NEXT_PUBLIC_VWORLD_API_KEY` is set, otherwise falls back to OpenStreetMap
raster tiles (with attribution). No key is required to run; VWorld unlocks the
지적·공시지가·용도지역 layers.

## Backend

- `use_cases/map_intelligence.py` `build_project_map_intelligence(project_data)`
  → `realytics-map-intelligence-v1`:
  - `parcelGeoJson`: a GeoJSON FeatureCollection. Each parcel carries
    `pnu / addressFragment / lotNumber / landCategory / areaSqm / areaPy /
    officialLandPricePerSqm / officialLandPriceBaseYear / owner`. Geometry comes
    from an embedded `geometry` (VWorld/PostGIS), a `centroid`, or `lng`+`lat`;
    otherwise `geometry: null`.
  - `center`: bounding-box midpoint of available geometries (stable initial view).
  - `layers`: the 6 deck layers (필지 경계 / 공시지가 / 용도지역 / 실거래 사례 /
    주변 공급 / 리스크) each with `status` = `available | needs_geometry |
    needs_data | planned` derived from what is actually present.
  - `dataLimitations`: explicit notes about missing geometry / 공시지가.
  - Parcel source priority: land ledger import (`landLedger`/`land_ledger`
    `.parcels`) → flat land-evidence package → `land_acquisition` list →
    top-level `parcels`.
- `GET /projects/{id}/map` — returns the payload for the hydrated project source.

## Frontend

- `components/map/map-intelligence.tsx` — MapLibre map (VWorld/OSM base),
  parcel fill/line/point overlays, click popups (PNU/지목/면적/공시지가), a layer
  legend with live status badges, and a parcel-overlay visibility toggle.
- `app/project/[id]/map/page.tsx` — project Map Intelligence page: the map plus a
  필지 명세 table and the data-limitation notes. Linked from the project home
  ("지도 분석").

## Next

- VWorld cadastral WFS/WMS → real parcel polygons by PNU (needs the free key).
- Persist parcel geometry into PostGIS `land_parcels` and serve from there.
- Additional layers: 용도지역 polygons, 주변 공급/실거래 heat overlays, radius
  comparable search (extend the existing `/api/valuemap` endpoint to live data).
