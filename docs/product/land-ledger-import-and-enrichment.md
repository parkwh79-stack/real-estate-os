# Land Ledger Import, Validation, and Enrichment

Updated: 2026-10-14

## Current contract and source comparison

`POST /projects/{project_id}/land-ledger/analyze` archives the original and saves
the parsed ledger on the existing project. Reopen it through
`GET /projects/{project_id}/workspace/context?page=land`. No new table is required.

- Parser `land-ledger-agent-v1.3` reads explicit XLS/XLSX merged ranges. A merged
  contract amount spanning multiple parcels is stored once in `contractGroups`.
  Members retain `contractGroupId` and the original anchor in `source.rawCells`;
  their parcel amounts remain null. Blank cells without a merge do not create a group.
- Group-level owner amounts reconcile to the shared contract amount. Shares in
  those rows are source context, not verified legal ownership of each parcel.
  Unallocated groups remain under review, including after enrichment.
- Missing individual contract amounts produce `individual_contract_amount_missing`
  and `importConsistency.incompleteChecks`. Actual numerical mismatches remain in
  `failedChecks`, even if another contract has missing details. Missing is not zero.
- The Bonri workbook importer stores the D19 purchase budget and D26 inclusive
  budget in `raw_import.landCostBreakdown`, with cells and source names. These are
  not extra additive cost items. E22 is retained as `raw_import.designFar`; it no
  longer populates `site.legal_far`. Previously saved studies are not overwritten.
- The Land screen compares persisted ledger contracts, purchase budget, inclusive
  budget, ancillary difference and purchase-minus-contract difference. It displays
  source cells, missing detail, candidate/error status and required legacy reimport.
  This comparison grants no approval and does not change calculation inputs.
- `PUT /projects/{project_id}/land-cost-review` accepts `comment` (1–5,000 chars)
  and `expectedComparisonHash`. It requires `project:review`, tenant scope and a
  server-verified actor. Changed source basis returns 409; blank notes return 400.
  The existing project JSON retains the note, source comparison snapshot and history.
  Generic project writes cannot forge `landCostReview`. On source changes, the old
  note remains visible as a previous-source note and is not reused automatically.
  Failed saves preserve the typed note. Cross-user atomic CAS remains a limit of
  the existing project JSON update path; this change does not claim to solve it.

Actual-source check: 9 parcels / 22 owner records; one I7:I12 contract group of
KRW 12,500,000,000; three missing individual amounts; no false total mismatch.
Contracts KRW 41,374,000,000 versus purchase budget KRW 42,000,000,000 differ by
KRW 626,000,000. Inclusive budget is KRW 46,032,000,000, with ancillary difference
KRW 4,032,000,000. The cause of the budget difference, legal ownership and original
37 formula errors remain unresolved. All eight source file hashes were preserved.

Payment correction (2026-10-14): only the explicitly labelled `depositPaid`
column counts as recorded paid money. Unpaid deposits, interim payments and
balances remain scheduled amounts, regardless of due date. A missing individual
contract amount, unallocated joint contract or incomplete payment record cannot
establish full payment; inconsistent/negative amounts require review. All owners
must individually qualify, and their contract sum must reconcile, before the
parcel can be labelled fully paid in the ledger. This is not bank-transfer or
legal ownership verification.

Each parcel now carries `paymentReviewVersion`, source sheet/rows, known paid and
scheduled subtotals and missing-cell counts. All-blank amounts stay null; explicit
zero stays zero. Legacy raw column totals remain reconciliation subtotals, not a
missing-data assertion. Modern layouts do not guess unlabelled payment columns.
The pilot adapter uses these same fields instead of counting scheduled interim
payments as deposits or treating absent balance aliases as paid in full.

The Land screen shows the two subtotals and gaps after save/reopen. Older saved
ledgers request reimport and do not display their old paid-status claims. The
former payment-string-derived acquisition percentage is replaced by a clearly
labelled registry-owner-match parcel percentage. The legacy API acquisition ratio
still describes contract coverage, not payment or ownership transfer.

Actual Bonri source: J28 recorded paid KRW 462,400,000; K28 unpaid deposit
KRW 3,675,000,000; L28 scheduled interim KRW 400,000,000; M28 scheduled balance
KRW 36,836,600,000. Three parcels have positive paid entries; none qualifies as
recorded fully paid. Thirteen owner rows have blank paid entries. Source SHA256
remains `d55a3793149bb0d7878762b5be44484b722570043d994c825c6b3b3b60c31be8`.
The source remains unchanged, and existing saved/approved projects were not rewritten.

Verification results are recorded in the [current roadmap](realytics-os-next-work-roadmap.md).
The sections below retain the earlier parser audit and expansion ideas as history;
their proposed routes and P0 list do not supersede this current contract.

## Earlier sample review (v1.1)

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

New enrichment endpoint:

- `POST /projects/{project_id}/land-ledger/enrich`

The enrichment endpoint accepts a parsed `landLedger` payload plus connector/manual options:

- `legalDongCode`
- `pnuResolutions`
- `cadastralCharacteristics`
- `officialLandPrices`
- `registryEvidence`

Implemented enrichment behavior:

- Generates 19-digit PNU from `legalDongCode`, mountain flag, normalized main lot number, and normalized sub lot number when a direct PNU resolution record is not provided.
- Stores PNU confidence, match method, candidate count, warning, and evidence metadata.
- Attaches cadastral characteristics with source/evidence metadata.
- Attaches official land price only when a connector/manual record provides price, base year, base date, and source/evidence metadata.
- Attaches registry/encumbrance evidence only when evidence is provided; registry verification is not fabricated.
- Rebuilds parcel QA, lifecycle status, land acquisition status, PF summary, and evidence coverage after enrichment.

Data policy:

- PNU can be deterministically generated from a confirmed legal dong code and normalized lot number.
- Official land price must come from a public/official connector or manually supplied evidence record with base year and source.
- Registry and encumbrance evidence must come from an attached document or licensed/official source.
- Missing official price or registry evidence remains a blocker for legal/PF completion.

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
- Connect backend batch PNU resolver to official public/address APIs where credentials are available.
- Connect official land price adapter to public/official API with base year/source evidence.
- Connect registry/evidence attachment model and legal review checklist to persisted evidence tables.
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
