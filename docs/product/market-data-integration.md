# Market Data Integration (실거래가 · 제휴 데이터)

Updated: 2026-07-10

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

`components/map/transactions-panel.tsx` (used on the map and 시장성 pages) shows
주변 실거래가: asset/deal/month selectors, summary (건수·평균 평단가), and a
transaction table. `lawd_cd` is derived from the first parcel's PNU; when no key is
configured it shows a clear "키 설정 시 실데이터" notice. With `geocode`, it requests
coordinates and surfaces records via `onRecords` (for map pins).

### 시장성 (Marketability) page — `/project/[id]/marketability`

The map-centric, high-visibility page the deck's Marketability Agent points at:
- Top KPI strip: 시장성 등급(A–E) · 종합점수 · 제안가 적정성 · 주변 평균 평단가 · Exit 분양률.
- Left: MapLibre map with the parcel boundary **and 국토부 실거래가 가격 마커**
  (Naver-style price pills — 매매 red, 전월세 blue), geocoded via VWorld
  (`/api/market/transactions?geocode=true`). When the parcel has no geometry the
  view fits to the transaction markers.
- Right: 실거래가 panel + AI 시장성 의견 (요약 · 가격 코멘트 · 권고 · 데이터 한계),
  from `POST /projects/{id}/marketability/analyze`.

`integrations/vworld.py` `geocode_address` (주소→좌표, best-effort) powers the
pins; `market_data.attach_coordinates` geocodes top-N records with a per-call
address cache. Reachable from the project home ("시장성 분석").

**단지(complex) view (default):** `market_data.aggregate_by_complex` groups
transactions by 단지명+읍면동 into per-complex 중위/최소/최대 평단가, a centroid
coordinate, and the latest deal month. `/api/market/transactions?aggregate=true`
returns `complexes`; the page toggles 개별/단지 (Naver-style complex markers —
"중위 X만/평 · 단지명·N건") to cut visual noise from many individual pins.

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

## Live comparables in the PF review run

`use_cases/market_comparables.py` feeds the credit decision with real
transactions. During a PF run, `_maybe_inject_live_market_comparables` (in the
orchestration, just before the Marketability step) derives the project's
LAWD_CD (parcel PNU) + asset type, fetches recent 실거래가, and injects them into
`market.transactionComparables` — the slot `MarketabilityAgent` reads. The
agent then prices 분양가 적정성 / Exit 분양률 against actual closed transactions,
which flows into Credit Review and the Phase 11 report.

- **Gated by `REALYTICS_LIVE_MARKET_COMPARABLES`** — **enabled in the deployment
  `.env` as of 2026-07-10** (`=true`) so the classic PF-run auto-injects on every
  run. Tests/CI stay offline because `tests/conftest.py` force-deletes the flag
  during the suite (the enabled path is tested via an explicit `environ` dict).
  Also requires `DATA_GO_KR_API_KEY` (configured). Note: the Chief pod flow
  (`chief_pod_auto_supplement._supplement_market`) and `auto_evidence_supplement`
  inject live comparables **unconditionally** (not behind this flag).
- Non-fabricating & fail-safe: no flag / no key / no LAWD_CD / no data → nothing
  injected; any fetch error is recorded in `run.errors` and never breaks the run.
- The run records a `liveMarketComparables` artifact (injected count + diagnostic).
- Verified live 2026-07-10 (flag enabled): classic PF-review on project 529
  (서초구 11650, apartment) injected 40 live 실거래가 (months 202607/06/05) →
  `liveMarketComparables` artifact `count=40`, run completed, no errors.

### Surfaced in the lender output (provenance)

The Marketability analysis now carries `comparableProvenance`
(count / publicApiCount / partnerApiCount / manualCount / hasLiveTransaction /
sourceLabel), and the IC Memo's `marketabilityAssessment.pricing` block +
markdown §핵심지표 cite it: e.g. "분양가 적정성: 제안 평당 7,000만 vs 비교 중위
6,200만 (+12.9%) → 고평가 (국토부 실거래가 40건)". So a credit officer sees the
pricing judgement *and* that it was made against actual closed transactions.

## Next

- Wire `transactionComparables` / `listing_comparables` into the PF-run project
  hydration so the Marketability Agent prices against live comparables automatically.
- Geocode RTMS records (VWorld geocoder) to plot transaction markers on the map.
- Finalize the Valuemap partner payload mapping once the API spec is provided.
