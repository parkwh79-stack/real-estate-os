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

### API keys (gitignored)

- Frontend: `apps/web/.env.local` → `NEXT_PUBLIC_VWORLD_API_KEY` (base map tiles).
- Backend: `services/api-server/.env` → `VWORLD_API_KEY` (+ `VWORLD_DOMAIN`,
  default `localhost`). `main.py` loads this `.env` on startup (dependency-free;
  real environment variables always win). `vworld_status()` reports readiness
  without ever echoing the key; `/ops/infra-status` surfaces it.
- The VWorld dev key is domain-restricted and time-limited — regenerate for
  production and register the deployed domain.

### Live parcel boundaries (VWorld cadastral)

`integrations/vworld.py` `fetch_parcel_geometry_by_pnu(pnu)` calls the VWorld
data API (`LP_PA_CBND_BUBUN` 연속지적도, GeoJSON, EPSG:4326) and returns the
parcel boundary geometry — best-effort and non-fabricating (missing key, network
error, or empty result → `None` + a diagnostic, never a crash or invented shape).
`GET /projects/{id}/map?resolve_geometry=true` resolves up to 30 parcels missing
geometry by PNU and reports `geometryResolution { resolvedCount, attemptedCount }`.
The frontend map page requests this so real boundaries draw when PNUs resolve.

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

## Geometry cache (avoid re-fetching VWorld)

Resolved boundaries are cached by PNU (`db/parcel_geometry_cache.py`) so the map
does not re-call VWorld for parcels it has already seen:

- **LocalParcelGeometryCache** (default): a dedicated SQLite file under the
  runtime dir (`REALYTICS_PARCEL_CACHE_FILE` to override). Works today.
- **PostgresParcelGeometryCache** (gated): upserts into the scaffolded
  `land_parcels` `geom` column (`ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON,4326))`)
  and reads it back via `ST_AsGeoJSON`. Used when Postgres is configured and the
  driver is installed.

`build_caching_resolver(cache, vworld_resolver)` wraps the VWorld resolver
cache-first: a cache hit skips the VWorld call, a fresh fetch is written back.
Fail-safe — any cache backend error degrades to a miss, never breaking the map.
`geometryResolution` on the map payload reports
`{ cacheBackend, resolvedCount, cacheHits, fetchedFromVworld }`. Verified live:
2nd load of the same parcel makes **zero** VWorld calls.

## Next

- Serve geometry straight from PostGIS `land_parcels` for spatial queries (radius
  / intersect) once Postgres is live; backfill `official_land_price` into the cache.
- Additional layers: 용도지역 polygons, 공시지가 choropleth (WMS), 주변 공급/실거래
  overlays, radius comparable search (extend `/api/valuemap` to live data).
- PNU resolution for parcels that only have address/lot (geocode → PNU) so more
  projects get boundaries automatically.
