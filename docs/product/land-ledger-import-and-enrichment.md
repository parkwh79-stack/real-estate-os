# Land Ledger Import and Enrichment

Updated: 2026-05-14

## Sample Reviewed

File:

`01.본리동토지조서.xls`

Workbook profile:

- Format: legacy Excel `.xls`
- Sheet count: 1
- Sheet name: `190927토지조서(원본)`
- Size: 28 rows x 20 columns

## Current Pipeline

```text
Excel land ledger import
-> parcel, owner, and payment parsing
-> area and purchase amount summary
-> PNU and official land price missing-data detection
-> needs_enrichment status assignment
```

## Parsed Sample Summary

Land Ledger Agent result on the sample workbook:

- Parcel count: 9
- Owner/payment record count: 22
- Total area: 4,691.9 sqm / 1,419.29975 py
- Total contract amount: KRW 41,374,000,000
- Missing PNU count: 9
- Missing official land price count: 9
- Validation status: `needs_enrichment`

The sample contains dong, lot number, land category, official cadastral area, owner/share text, purchase amount, payment schedule, bank/account fields, contract dates, deadlines, and remarks.

The sample does not contain PNU or official land price. Those must be enriched after import.

## Land Ledger QA v1

Land Ledger QA v1 implements the 10 required review gates:

| No | Gate | Code field |
|---:|---|---|
| 1 | Original-vs-parsed reconciliation | `qa.sourceReconciled` |
| 2 | Parcel-level consistency | `qa.parcelUnitConsistent` |
| 3 | Owner and ownership share validation | `qa.ownershipShareValid` |
| 4 | Amount and payment schedule validation | `qa.paymentScheduleValid` |
| 5 | PNU resolve confidence score | `qa.pnuResolveConfidence` |
| 6 | Official land price base-year management | `qa.officialPriceBaseYear` |
| 7 | Registry and encumbrance evidence management | `qa.registryEvidenceStatus` |
| 8 | Public/shared/special parcel exception handling | `qa.specialParcelException` |
| 9 | Personal data and account security policy | `qa.securityRedactionStatus` |
| 10 | PF review output auto-linking | `qa.pfOutputReady` |

Each parcel also receives:

- `issues`: standardized issue code, severity, field, and message.
- `workflowStatus`: current parcel lifecycle status.
- `pfReviewMapping`: output links for `landSecuring`, legal/permit review, and risk matrix.

## Workflow Statuses

Parcel-level workflow statuses:

```text
imported
parsed
parcel_validated
ownership_validated
needs_pnu_resolution
needs_official_price
needs_registry_evidence
enriched
ready_for_pf_review
```

The reviewed sample generally stops before `ready_for_pf_review` because PNU, official land price, and registry evidence are not present.

## Current Code Coverage

Implemented:

- `LandLedgerAgent`
- Legacy Bonri-style `.xls` parser
- Parcel and owner/payment record extraction
- Area and contract amount summary
- Ownership share parsing from owner text such as `(1/5)`
- Sensitive account masking
- Missing PNU and official land price detection
- Land Ledger QA v1 gates
- Standardized parcel issues
- Parcel workflow status
- PF review mapping output
- Enrichment plan generation
- API endpoint: `POST /projects/{project_id}/land-ledger/analyze`

Already existed before:

- Frontend-only single PNU lookup route at `apps/web/app/api/pnu-lookup/route.ts`
- Basic project `land.pnu`, `land.area_sqm`, and `land_acquisition` fields consumed by reports, risk, and legal review

Not implemented yet:

- Backend batch PNU resolver from dong/lot number
- Backend official land price connector
- Backend cadastral land characteristics connector
- Registry/encumbrance evidence connector
- Persistent storage of imported land ledger artifacts
- UI upload screen for land ledger files

## Required Enrichment Pipeline

1. `resolve_pnu`
   - Input: dong, lot number, address fragment
   - Output: 19-digit PNU, legal address, PNU confidence score

2. `fetch_land_characteristics`
   - Input: PNU
   - Output: land category, cadastral area, zoning, land-use zones

3. `fetch_official_land_price`
   - Input: PNU and base year
   - Output: official land price per sqm, base year, base date

4. `verify_registry_and_encumbrances`
   - Input: PNU and owner name
   - Output: registered owner, share, mortgage, seizure, easement, trust transfer status

## Agent Rule

Do not treat a land ledger import as complete when PNU, official land price, or registry evidence is missing.

The correct status is:

- `needs_enrichment` at the import-level when enrichment blockers exist.
- `ready_for_pf_review` only after PNU, cadastral characteristics, official land price, registry evidence, security redaction, and PF output mapping are complete.
