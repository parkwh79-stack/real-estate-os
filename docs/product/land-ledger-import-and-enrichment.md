# Land Ledger Import, Validation, and Enrichment

Updated: 2026-05-14

## Sample Reviewed

File:

`01.본리동토지조서.xls`

Workbook profile:

- Format: legacy Excel `.xls`
- Sheet count: 1
- Size: 28 rows x 20 columns
- Parser profile: `legacy-bonri-land-ledger-v1`
- Agent/parser version: `land-ledger-agent-v1.1`

## Current Pipeline

```text
Excel land ledger import
-> parcel, owner, and payment parsing
-> original-vs-parsed total reconciliation
-> parcel lot/category/area/payment validation
-> PNU and official land price missing-data detection
-> registry/evidence readiness check
-> land acquisition status and PF summary generation
```

## Parsed Sample Summary

Land Ledger Agent result on the sample workbook:

- Parcel count: 9
- Owner/payment record count: 22
- Total area: 4,691.9 sqm / 1,419.29975 py
- Total contract amount: KRW 41,374,000,000
- Missing PNU count: 9
- Missing official land price count: 9
- Registry evidence missing count: 9
- Import consistency status: `needs_review`
- Validation status: `needs_review`
- Parcel workflow counts: `needs_review` 3, `needs_enrichment` 6

The sample reconciles parcel count, area, and total purchase amount, but the parsed parcel purchase amount and owner-level individual amount totals do not fully reconcile. Therefore the import must not be treated as final. This is intentional: if an Excel ledger reads successfully but the source totals and parsed totals differ, the correct lifecycle status is `needs_review`.

The sample does not contain PNU, official land price, cadastral characteristics, or registry evidence. Those must be enriched and attached before legal review or PF review.

## Import Consistency Validation

`validate_import_consistency` compares source workbook totals against parsed results.

Implemented checks:

- Parcel count, through parsed parcel records
- Owner/payment record count, through parsed owner records
- Total land area in sqm
- Total land area in py
- Parcel-level purchase amount total
- Owner-level individual purchase amount total
- Deposit paid, deposit unpaid, interim payment, and balance payment totals when source totals are available

Status rule:

- All available totals match within tolerance: `passed`
- Any available total fails reconciliation: `needs_review`

This means an import can be syntactically parsed but still blocked from legal or PF use.

## Parcel Integrity Validation

Each parcel now stores both raw and normalized lot values:

- `rawLotNumber`
- `isMountain`
- `mainLotNo`
- `subLotNo`
- `normalizedLotNumber`

The parser handles common Korean lot forms such as:

- `142`
- `142-1`
- `산 142`
- `산142-1`
- `본리동 142번지`
- `본리동 142-1번지`
- `본리동 산142-1`

Parcel QA checks:

- Lot number can be normalized
- Land category maps to a standard Korean land category code
- sqm and py conversion is within tolerance
- Parcel amount exists
- Owner share is complete when multiple owners exist
- Payment totals reconcile to purchase amount

## PNU Resolution Model

PNU is not considered valid just because a value exists. The parcel model reserves:

- `resolvedPnu`
- `resolvedLegalAddress`
- `pnuResolveConfidence`
- `pnuMatchMethod`
- `pnuCandidateCount`
- `pnuResolverWarning`

Status rule:

- No PNU: `needs_enrichment`
- PNU exists but confidence is below threshold: `needs_review`
- PNU exists and confidence is acceptable: continue to official price and evidence enrichment

Default confidence threshold: `0.85`.

## Official Land Price Model

Official land price fields:

- `officialLandPricePerSqm`
- `officialLandPriceBaseYear`
- `officialLandPriceBaseDate`
- `officialTotalLandValue`
- `officialLandPriceSource`

Status rule:

- Missing official price: `needs_enrichment`
- Official price exists but base year is missing or outdated: `needs_enrichment`
- Official price exists with current base year/source: continue to legal evidence review

Current freshness baseline in code: `CURRENT_OFFICIAL_PRICE_BASE_YEAR = 2026`.

## Evidence Model

The parcel output now reserves evidence fields for registry and legal review:

- `registryEvidence`
- `evidence`

Evidence should be persisted later with:

- `evidence_id`
- `project_id`
- `parcel_id`
- `evidence_type`
- `source`
- `issued_at`
- `fetched_at`
- `file_path`
- `hash`
- `verified_by`
- `verification_status`

Supported evidence types should include:

- `registry_record`
- `cadastral_record`
- `land_use_plan`
- `official_land_price`
- `sales_contract`
- `owner_id_document`
- `trust_agreement`
- `escrow_confirmation`
- `payment_receipt`

Registry verification must not be marked complete without attached evidence.

## Land Acquisition Status

`landAcquisitionStatus` is generated from parsed parcels and payments:

- `totalRequiredParcels`
- `contractedParcels`
- `paidDepositParcels`
- `balancePaidParcels`
- `ownershipTransferredParcels`
- `trustedParcels`
- `missingParcels`
- `privateLandArea`
- `publicLandArea`
- `acquisitionAreaRatio`
- `acquisitionAmountRatio`
- `publicLandDetected`
- `publicLandPurchaseStatus`

This gives PF review a direct view of land securing progress instead of only raw ledger rows.

## Privacy and Security

Implemented:

- User-facing account numbers are masked.
- Raw account numbers are not retained in the parsed API response.
- Owner records include a security policy block:
  - `accountNumberStored`
  - `accountNumberEncrypted`
  - `accountNumberMasked`
  - `rawAccountRetained`
  - `auditRequiredForRawAccess`

Future persistence must add encrypted storage, RBAC, download controls, and raw-data access audit logs.

## Workflow Status Lifecycle

Supported lifecycle statuses:

```text
uploaded
parsed
parse_failed
parsed_with_warnings
needs_review
needs_enrichment
enrichment_in_progress
enrichment_failed
enriched
needs_legal_evidence
ready_for_legal_review
legal_review_in_progress
legal_review_completed
ready_for_pf_review
```

Current parcel status rules:

- Source cannot be reconciled: `uploaded`
- Parcel lot/category/area is inconsistent: `parsed_with_warnings`
- Ownership share, payment schedule, or low-confidence PNU requires human review: `needs_review`
- PNU or official price is missing/outdated: `needs_enrichment`
- Registry evidence is missing after enrichment: `needs_legal_evidence`
- All PF output gates pass: `ready_for_pf_review`

## PF Review Summary

`pfSummary` is generated automatically:

- Total parcel count
- Total area
- Total purchase amount
- PNU coverage ratio
- Official land price coverage ratio
- Registry evidence coverage ratio
- Deposit-paid parcel ratio
- Balance due amount
- Ownership transferred parcel count
- Restriction review required parcel count
- Review memo

The review memo explicitly warns that the ledger can support preliminary parcel, owner, area, purchase amount, and payment schedule review, but cannot be used as final legal or PF approval evidence until PNU, official land price, cadastral characteristics, and registry/encumbrance evidence are attached.

## API

Implemented:

- `POST /projects/{project_id}/land-ledger/analyze`

Current response includes:

- `importMetadata`
- `sourceTotals`
- `summary`
- `parcels`
- `validation`
- `importConsistency`
- `landAcquisitionStatus`
- `pfSummary`
- `qaProcess`
- `enrichmentPlan`
- `supportedWorkflowStatuses`

Recommended next persistent workflow endpoints:

- `POST /projects/{project_id}/land-ledger/import`
- `GET /projects/{project_id}/land-ledger/imports`
- `GET /projects/{project_id}/land-ledger/{import_id}`
- `POST /projects/{project_id}/land-ledger/{import_id}/validate`
- `POST /projects/{project_id}/land-ledger/{import_id}/enrich`
- `GET /projects/{project_id}/land-ledger/{import_id}/enrichment-status`
- `POST /projects/{project_id}/land-ledger/{import_id}/approve`
- `GET /projects/{project_id}/land-ledger/{import_id}/pf-summary`

## Required Next Work

P0:

- Persist original land ledger file hash, parser version, parsed output version, and approval status.
- Add first-class import records instead of only one-shot analyze responses.
- Add backend batch PNU resolver with confidence score and candidate warnings.
- Add official land price connector with base year/source evidence.
- Add registry/evidence attachment model and legal review checklist.
- Add UI review/approval workflow after upload.

P1:

- Add async enrichment jobs for slow PNU/official price/registry operations.
- Add public land/shared ownership/special parcel workflow screens.
- Add encrypted raw account storage only when explicitly needed, with RBAC and audit logs.
- Add PF report auto-linking from approved land ledger versions.

## Agent Rules

- Do not treat a land ledger import as complete when PNU or official land price is missing.
- Do not treat resolved PNU as valid when match confidence is below threshold.
- Do not treat official land price as valid without base year and source evidence.
- Do not treat registry verification as complete without attached registry evidence.
- Do not mark `ready_for_legal_review` if ownership share sum is inconsistent.
- Do not mark `ready_for_pf_review` if land acquisition amount, parcel count, or area totals fail reconciliation.
- Always preserve original imported file identity and parser version.
- Always mask sensitive account numbers in user-facing responses, logs, and exports.
- Flag public land, shared ownership, trust registration, seizure, mortgage, provisional registration, easement, or unresolved encumbrance as legal review issues.
