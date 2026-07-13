# Land Data API Connector

Updated: 2026-07-13
Project: REALYTICS x PLATFORM9

## Goal

The Land Data API Connector fills parcel-level evidence required by the Land Agent:

- PNU resolution
- cadastral characteristics
- land-use planning context: zoning, statutory FAR/BCR, district-unit-plan status
- parcel characteristics: area, land category, use districts, land-use situation, terrain, road frontage, and source-returned official land price
- building-use attributes: use, site/building/total floor area, FAR/BCR, floors, permit and approval dates
- regional land-price trend: monthly index, monthly change rate, cumulative change rate, and base date
- official land price with base year and source
- registry/encumbrance evidence status

The connector must not fabricate official land price or registry facts. If API configuration, API keys, or source records are missing, the run must return diagnostics and keep the land review blocked.

## Current Implementation

Code:

- `services/api-server/integrations/land_data_connectors.py`
- `services/api-server/agents/land_ledger_enrichment_agent.py`
- `services/api-server/scripts/fetch_land_evidence_from_connectors.py`
- `services/api-server/scripts/apply_land_evidence_to_pilot.py`

Current support:

| Evidence | Status | Notes |
|---|---|---|
| PNU | Done | Generated from legal dong code plus normalized lot number when available |
| Cadastral characteristics | Built-in VWorld fallback | If a VWorld/public-data key is present, exact-PNU cadastral properties are attempted before reporting missing config |
| Land-use planning | Built-in VWorld `getLandUseAttr` + configurable endpoint | With `VWORLD_API_KEY`, calls `https://api.vworld.kr/ned/data/getLandUseAttr` per PNU and stores returned designation, conflict status, registration date, and data base date. FAR/BCR are never inferred from a designation name. |
| Building use | Built-in VWorld `getBuildingUse` | Calls `https://api.vworld.kr/ned/data/getBuildingUse` per PNU and stores building use, areas, measured FAR/BCR, floors, permit date, and approval date as `building_use` evidence. |
| Land characteristics | Built-in VWorld `getLandCharacteristics` | Calls `https://api.vworld.kr/ned/data/getLandCharacteristics` per PNU and stores source-returned parcel area, land category, use districts, land-use situation, terrain, road frontage, official land price, base year, and data base date as `land_characteristics` evidence. |
| Regional land-price trend | Built-in VWorld `getByRegion` | Calls `https://api.vworld.kr/ned/data/getByRegion` once per enrichment run using the project legal-dong/administrative code and stores monthly/cumulative change rates as `land_price_trend` evidence. |
| Official land price | Configurable + VWorld jiga fallback | VWorld cadastral `jiga` is used only when it is actually returned with the PNU |
| Registry/encumbrance | Manual/licensed only | Live registry lookup is intentionally not implemented |

## Environment Variables

The connector provides an official VWorld land-use planning adapter and keeps URL templates for other approved public-data, licensed-provider, or internal proxy contracts.

| Variable | Purpose |
|---|---|
| `REALYTICS_CADASTRAL_URL_TEMPLATE` | Optional URL template for cadastral lookup |
| `REALYTICS_OFFICIAL_LAND_PRICE_URL_TEMPLATE` | URL template for official land price lookup |
| `REALYTICS_LAND_USE_URL_TEMPLATE` | URL template for land-use planning and statutory restriction lookup |
| `REALYTICS_VWORLD_LAND_USE_URL_TEMPLATE` | Alias for a VWorld/public land-use planning URL template |
| `REALYTICS_LAND_USE_API_KEY` | Optional dedicated key for the land-use planning endpoint |
| `REALYTICS_VWORLD_API_KEY` or `VWORLD_API_KEY` | API key candidate for VWorld-style connectors |
| `REALYTICS_VWORLD_DOMAIN` or `VWORLD_DOMAIN` | Optional registered browser domain passed to VWorld (`localhost` for the local pilot when registered) |
| `REALYTICS_LAND_CHARACTERISTICS_YEAR` | Optional base year for the VWorld land-characteristics lookup |
| `REALYTICS_PUBLIC_DATA_API_KEY` or `DATA_GO_KR_API_KEY` | API key candidate for public-data-style connectors |
| `REALYTICS_CADASTRAL_SOURCE_NAME` | Optional source name stored in evidence metadata |
| `REALYTICS_OFFICIAL_LAND_PRICE_SOURCE_NAME` | Optional source name stored in evidence metadata |

Supported placeholders:

```text
{pnu}
{service_key}
{base_year}
```

Example shape:

```text
REALYTICS_OFFICIAL_LAND_PRICE_URL_TEMPLATE=https://internal-or-public-api.example/official-price?pnu={pnu}&year={base_year}&serviceKey={service_key}
REALYTICS_CADASTRAL_URL_TEMPLATE=https://internal-or-public-api.example/cadastral?pnu={pnu}&serviceKey={service_key}
REALYTICS_LAND_USE_URL_TEMPLATE=https://internal-or-public-api.example/land-use?pnu={pnu}&serviceKey={service_key}
```

The built-in VWorld land-use adapter sends `pnu`, `format=json`, `numOfRows=1000`, `pageNo=1`, `key`, and optional `domain` to `getLandUseAttr`. It preserves `prposAreaDstrcCode(Nm)`, `cnflcAt(Nm)`, `registDt`, and `lastUpdtDt` as source-backed evidence. Those values are stored as planning designations, not automatically treated as the statutory use district: a response can be a restriction such as a river zone or livestock-restriction zone. Do not rely on unofficial scraping.

The built-in building-use adapter sends the same paging and authentication fields to `getBuildingUse`. It maps `buldPlotAr`, `buldBildngAr`, `buldTotar`, `measrmtRt`, `btlRt`, `mainPrposCode(Nm)`, `detailPrposCode(Nm)`, `groundFloorCo`, `undgrndFloorCo`, `prmisnDe`, `useConfmDe`, and `lastUpdtDt` into source-backed building records. These are existing-building attributes and must be reconciled with the proposed development program; they are not treated as approval of a new project.

The built-in land-characteristics adapter sends `pnu`, `stdrYear`, `format=json`, paging, `key`, and optional `domain` to `getLandCharacteristics`. It maps `lndpclAr` to parcel area, `lndcgrCode(Nm)` to land category, `prposArea1/2(Nm)` to use districts, `ladUseSittn(Nm)` to land-use situation, `tpgrphHgCode(Nm)` and `tpgrphFrmCode(Nm)` to terrain, `roadSideCode(Nm)` to road frontage, `pblntfPclnd` to source-returned official land price, and `lastUpdtDt` to the evidence base date. These values are attached to the matching parcel and persisted as `land_characteristics` evidence. They must be reconciled against the land ledger, cadastral records, and official-price records; the adapter does not infer a development approval, statutory FAR/BCR, or ownership status from them.

The regional trend adapter sends `stdrYear`, optional `stdrMt`, `reqLdCode`, `scopeDiv`, paging, `key`, and optional `domain` to `getByRegion`. `scopeDiv=A` uses the five-digit city/county code; `scopeDiv=B` can be used for the ten-digit town/village code. It maps `pclndIndex`, `pclndChgRt`, `acmtlPclndChgRt`, administrative names, and `lastUpdtDt`. This is a regional market indicator, not a parcel-specific appraisal or official land-price amount.

## Local Pilot Commands

Build parcel request and fill-in template:

```text
cd services/api-server
python scripts/build_land_evidence_request.py
```

Fetch through configured API connectors:

```text
cd services/api-server
python scripts/fetch_land_evidence_from_connectors.py --base-year 2026
```

Outputs:

- `data/runtime/pilot-input/outputs/land-evidence-connector-result.json`
- `data/runtime/pilot-input/outputs/land-evidence-from-connectors.json`

Fetch and apply in one step:

```text
cd services/api-server
python scripts/fetch_land_evidence_from_connectors.py --base-year 2026 --apply
```

Apply a manually filled template:

```text
cd services/api-server
python scripts/apply_land_evidence_to_pilot.py --evidence data/runtime/pilot-input/outputs/land-evidence-template.json
```

## Safety Rules

1. Generated PNU alone does not clear land review.
2. Official land price requires PNU, price per sqm, base year, source name, and evidence URL or ID.
3. Cadastral evidence requires PNU, source metadata, and at least one substantive field such as zoning or standard land category code.
4. Registry evidence requires PNU, source/evidence ID, source name, registered owner, and owner match status.
5. Registry completion remains manual or licensed-provider only.
6. If no source-backed records are returned, `LandAgent` must remain `needs_review` and `pfSummary` must remain `needs_enrichment`.

## Current Pilot Result

When run without configured API URL templates, the connector uses the configured VWorld key for cadastral/jiga lookup and for the official VWorld land-use planning endpoint. It returns only fields actually present in each response. The latest local legal-pod smoke run against project `753` recorded source-backed VWorld land-use planning records for returned parcels, while unresolved parcels remain visible in diagnostics.

This is correct behavior. The system did not fabricate evidence. Zoning-only context may be shown for preliminary review, while missing statutory FAR/BCR and district-unit-plan evidence keeps developability/approval readiness conditional.

## Next Work

1. Run connector fetch against the pilot parcels and review per-PNU diagnostics.
2. Attach a legally authoritative source for statutory FAR/BCR and district-unit-plan restrictions; `getLandUseAttr` itself does not return these numeric limits.
3. Apply returned evidence to the pilot payload.
4. Rerun PF orchestration and confirm Land Agent moves from `needs_review` only when evidence coverage is complete.
5. Add licensed registry evidence ingestion or manual registry upload workflow.
