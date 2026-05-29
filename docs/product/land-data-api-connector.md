# Land Data API Connector

Updated: 2026-05-28
Project: REALYTICS x PLATFORM9

## Goal

The Land Data API Connector fills parcel-level evidence required by the Land Agent:

- PNU resolution
- cadastral characteristics
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
| Cadastral characteristics | Configurable | Uses URL template only when configured |
| Official land price | Configurable | Uses URL template only when configured |
| Registry/encumbrance | Manual/licensed only | Live registry lookup is intentionally not implemented |

## Environment Variables

The connector is template-based so it can work with VWorld, public data portal, licensed providers, or internal proxy APIs without hardcoding one vendor contract.

| Variable | Purpose |
|---|---|
| `REALYTICS_CADASTRAL_URL_TEMPLATE` | URL template for cadastral or land-use lookup |
| `REALYTICS_OFFICIAL_LAND_PRICE_URL_TEMPLATE` | URL template for official land price lookup |
| `REALYTICS_VWORLD_API_KEY` or `VWORLD_API_KEY` | API key candidate for VWorld-style connectors |
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
```

Use the actual provider endpoint contract approved for the deployment environment. Do not rely on unofficial scraping.

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

When run without configured API URL templates, the connector returned:

- cadastral records: 0
- official land price records: 0
- registry records: 0
- diagnostics: `skipped_missing_config`

This is correct behavior. The system did not fabricate evidence and kept approval readiness blocked.

## Next Work

1. Configure approved VWorld/public-data/internal proxy URL templates.
2. Run connector fetch against the Bonri pilot parcels.
3. Review returned source metadata and base dates.
4. Apply returned evidence to the pilot payload.
5. Rerun PF orchestration and confirm Land Agent moves from `needs_review` only when evidence coverage is complete.
6. Add licensed registry evidence ingestion or manual registry upload workflow.
