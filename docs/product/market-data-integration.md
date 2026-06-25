# Market Data Integration (실거래가 · 제휴 데이터)

Updated: 2026-06-24

Live market data for the map and the Marketability Agent. Two **legitimate**
sources — no scraping of proprietary listing sites (that would violate both the
sites' ToS and this project's no-fabrication/provenance invariants).

## Sources

| Source | What | Access | Comparable slot |
|---|---|---|---|
| **국토부 실거래가 (MOLIT RTMS)** | 실제 체결 매매/전월세 (아파트·오피스텔·연립다세대·단독다가구·토지·상업업무용) | data.go.kr 공개 OpenAPI, **무료 키** | `transactionComparables` → `public_api` / `transaction` |
| **밸류맵 (Valuemap)** | 제휴 comparable/매물 데이터 | **정식 API 제휴** (라이선스; 스크래핑 아님) | `listing_comparables` → `partner_api` / `listing` |
| 공시지가 · 지적 · 용도지역 | VWorld | VWorld 키 | (Map Intelligence) |

**Why 실거래가 over 호가:** for a lender-facing credit tool, actual closed
transactions are stronger evidence than listing asking prices (마케팅 숫자). The
legitimate public source is also the better source for the product's purpose.

## Backend

`integrations/market_data.py`:
- `MolitRtmsConnector.fetch(lawd_cd, deal_ym, asset_type, deal_kind)` → normalized
  records. Parses the RTMS XML (`parse_rtms_items`), converts 만원→원, derives
  평단가, classifies 전세/월세. Injectable `http_get` (unit-testable offline).
  Gated on `DATA_GO_KR_API_KEY` / `REALYTICS_PUBLIC_DATA_API_KEY` / `MOLIT_API_KEY`.
- `ValuemapConnector` — config-driven (`VALUEMAP_API_URL` + `VALUEMAP_API_KEY`);
  mock fallback **clearly labelled** (`source=valuemap_mock`) until the
  partnership spec/credentials land. The live payload mapping is deferred to the
  real spec — never fabricated from an unknown shape.
- `lawd_cd_from_pnu(pnu)` — RTMS LAWD_CD = first 5 digits of a parcel PNU.
- `market_data_status()` — secret-safe readiness (no keys echoed).

Records are shaped for `MarketabilityAgent._collect_market_comparables`, so the
agent's existing pricing/absorption logic consumes them directly.

### Endpoints (`/api/market`)

- `GET /api/market/status` — provider readiness + supported asset types.
- `GET /api/market/transactions?lawd_cd=&deal_ym=&asset_type=&deal_kind=` — 국토부
  실거래가 (summary + records + diagnostic). Non-fabricating: no key → 200 with a
  `skipped` diagnostic, never invented rows.
- `GET /api/market/comparables?lat=&lng=&radius_m=` — 밸류맵 partner comparables.

## Frontend

`components/map/transactions-panel.tsx` on the project map page (`/project/[id]/map`)
shows 주변 실거래가: asset/deal/month selectors, summary (건수·평균 평단가), and a
transaction table. `lawd_cd` is derived from the first parcel's PNU; when no key is
configured it shows a clear "키 설정 시 실데이터" notice.

## Keys (gitignored)

- `DATA_GO_KR_API_KEY` (or `MOLIT_API_KEY`) — 국토부 실거래가. Separate from the
  VWorld key. Set in `services/api-server/.env`. **Use the DECODING (raw) key** —
  the connector URL-encodes it once via `urlencode`; passing the pre-encoded form
  double-encodes and fails auth.
- `VALUEMAP_API_URL` + `VALUEMAP_API_KEY` — 밸류맵 제휴 API (pending spec).

### data.go.kr per-API subscription (활용신청)

Each RTMS service is authorized separately. A key approved for 아파트 매매 returns
**403** for 아파트 전월세 / 오피스텔 / etc. until you 활용신청 those specific APIs on
data.go.kr. The connector surfaces this as a `forbidden` diagnostic with an
actionable Korean message — it is a portal subscription matter, not a code bug.
Verified live 2026-06-24: 서초구(11650) 아파트 매매 returns real transactions.

## Next

- Wire `transactionComparables` / `listing_comparables` into the PF-run project
  hydration so the Marketability Agent prices against live comparables automatically.
- Geocode RTMS records (VWorld geocoder) to plot transaction markers on the map.
- Finalize the Valuemap partner payload mapping once the API spec is provided.
