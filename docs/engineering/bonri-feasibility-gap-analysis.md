# Bonri Feasibility Engine Gap Analysis

Updated: 2026-07-16

## Decision

The Bonri implementation prompt is directionally correct, but it must not be
copied into the platform as a second parsing, conflict, approval, or source
snapshot system. Parser Platform V2 remains the authority for evidence,
conflicts, review decisions, canonical selections, and approved input
snapshots. Feasibility adds deterministic formulas, workbook reconciliation,
formula traces, parcel/legal eligibility, and a target append-only
CalculationRun history on top of those approved inputs. Completed-run
immutability is not enforced yet: the current persistence path can still upsert
an existing run ID.

The first applied slice is therefore:

1. isolate workbook candidates from canonical project inputs;
2. require the project-scoped archive object to exist at approval time, read its
   bytes again, recompute SHA-256, and bind that digest to the server-issued
   validation and server-extracted draft hash before canonical promotion;
3. reject generic project CRUD attempts to inject server-managed source,
   canonical feasibility, approval, or regulatory authority fields;
4. send only an explicitly approved `canonicalFeasibilityStudyV2` directly to
   the Cash Flow mapper;
5. deep-copy externally supplied complete Cash Flow requests, bind their project
   identity to the route project, and downgrade caller-supplied approvals to
   `candidate`/`unapproved` so they cannot become report-grade;
6. normalize the Bonri cash-flow source from KRW millions to integer KRW and
   classify transaction rows by workbook meaning rather than numeric sign;
7. separate Equity funding from PF debt capacity and accept `mezzanine` as the
   canonical tranche name while retaining `middle` as a legacy adapter alias;
8. introduce Decimal/Excel rounding primitives and formula lineage references;
9. calculate a multi-parcel FAR candidate as
   `sum(parcel_area_sqm * parcel_legal_far / 100)`, never as the maximum parcel
   FAR multiplied by the whole site;
10. exclude legally ineligible programs from product-value ranking and require
    a separately verified regulatory approval decision before Deal Optimization
    can clear its legal gate.

## Source Workbook Audit

The source workbook was read only. It remains outside the repository and must
not be committed. Its reproducible source inventory and golden import contract
are versioned in
`services/api-server/tests/fixtures/bonri/source_manifest.json`.

| Item | Result |
|---|---|
| Source | `../sample/19.12.11 본리동 주상복합 사업수지 (PF검토용).xlsx` |
| SHA-256 | `9214afc891530bb369049f9497888d16c4dbeecae69ec1b4c54637d3a61df92f` |
| Sheets | 12 |
| Formula cells | 11,982 |
| Formula text containing `#REF!` | 13 |
| Cached `#REF!` values | 25 |
| Cached `#DIV/0!` values | 12 |
| `OFFSET` formulas | 68 |
| External workbook formula references | 0 |
| Core total reconciliations | 4/4 passed within 1 source-unit tolerance |
| Canonical approval status | `failed` because blocking formula errors remain |

Passing headline totals does not neutralize broken formulas elsewhere. The
workbook can be stored as a candidate and used for repair analysis, but it must
not be promoted as an authoritative feasibility model in its current state.

## What Reuses Existing Platform Contracts

The following prompt concepts map to existing Parser V2 contracts and must not
create duplicate tables:

| Prompt concept | Platform authority |
|---|---|
| Source snapshot | `ApprovedInputSnapshot` and immutable document versions |
| Source conflict | `SourceConflict` |
| Reconciliation decision | `ReviewDecision` and `CanonicalSelection` |
| Approved input lineage | `ApprovedInputItem` with evidence locators and hashes |
| Workbook bytes/archive | Existing source-document and upload-archive repositories |
| Scenario waterfall, Unsold LTV, recovery | Existing calculation/scenario core; extend rather than replace |

FeasibilityStudyV2 now carries references to the approved input snapshot,
parcel snapshot, regulatory constraint snapshot, buildable envelope, formula
versions, and formula traces. These are references, not a duplicate evidence
store, and a reference alone does not prove that the authoritative approval
repository was queried.

## Applied Authority Boundary

```text
uploaded workbook
  -> read-only validation
  -> project-scoped archive object
  -> approval-time byte read and SHA-256 recomputation
  -> FeasibilityStudyV2 candidate draft
  -> candidate persistence only
  -> reviewer resolves workbook formula/reconciliation/embedded conflicts
  -> source/hash-bound explicit approval
  -> canonicalFeasibilityStudyV2
  -> CashFlowRunRequest
  -> CalculationRun
```

Candidate persistence no longer writes `feasibilityStudyV2`,
`canonicalFeasibilityStudyV2`, `product_mix`, `pf_structure`, `schedule`, or
`feasibility_input`. It returns a non-authoritative preview for review.
Generic project create/update endpoints also reject server-managed authority
fields. A client cannot manufacture archived source metadata, canonical
feasibility state, approval records, or a report-grade regulatory envelope by
placing those fields in an ordinary project CRUD payload.

Approval requires the actual project-scoped archive object, not only mutable
source-document metadata. The gate reopens the archived object, hashes the
stored bytes, and requires the recomputed SHA-256 to match the archive/source
identifier and server validation before checking the server-extracted draft
hash. A submitted `passed` value cannot mask
`raw_import.validationStatus=warning|failed`. The gate also checks blocking
formula errors, external workbook references, missing required cells/sheets,
failed reconciliation rows, and conflicts embedded in the validation or raw
import payload. It does **not** yet query the Parser V2 `SourceConflict`
repository for the exact document/candidate set; repository-wide open-conflict
enforcement remains deferred.

An API-supplied `requestType=cash_flow_run_request_v1`,
`cashFlowRunRequest`, or `cashFlowRunRequestDraft` is a calculation candidate,
not an approval carrier. The use case deep-copies it, replaces caller project
identity with the route project, and downgrades `sourceMetadata`, monthly cost
schedule, collection-allocation, and other embedded approval/report-eligibility
claims. The server-generated request mapped from the approved canonical study
retains its approved provenance.

Deal Optimization likewise cannot clear its legal gate from a project or
artifact that merely says `reportGrade=true` or contains a self-described
passing binding check. It requires a separately verified regulatory approval
decision. Resolving that decision from a production approval repository is
still deferred, so the normal production path remains non-report-grade until
that repository binding is completed.

## Bonri Cash-flow Normalization

The legacy workbook is not sign-coded:

- revenue/funding rows can be positive;
- cost and debt-service rows are also positive;
- rows 120 onward contain subtotals, cumulative balances, and assumptions.

The prior importer read rows 100:158, treated positive values as inflows, kept
the source unit as if it were KRW, and included accounting totals. The applied
profile instead uses:

- period/event columns: `F:BQ`, exactly 64 periods (four pre-month events plus
  months 1-60);
- transaction range: `F35:BQ119`;
- operating buckets: `cash_in` and `cash_out`;
- financing buckets: `pf_draw` and `loan_repayment`;
- sponsor capital buckets: `equity_contribution` and `equity_recovery`;
- unmatched financing transfers: `other_financing_inflow` and
  `other_financing_outflow`;
- current-period cash check: row 122;
- legacy cumulative cash check: row 130, bridged by carry row 123 and financing
  adjustment row 129;
- source unit: `KRW_MILLION`;
- canonical unit: integer `KRW`.

Each normalized period keeps the exact source range, normalization factor,
transaction-row policy, current-period reconciliation, and cumulative source
cell in its locator metadata. This preserves auditable source buckets; it does
not complete the tranche-level monthly debt ledger or financing replay engine.

## Parcel, Legal, and Optimization Orchestration

The financial prompt did not define the 112-parcel or legal eligibility
contract. The platform now applies the following preliminary path:

```text
fresh same-PNU public-evidence reuse
  -> live fetch for missing or expired public evidence
  -> SiteParcelSnapshotV1 candidate
  -> PNU/area/FAR/BCR/site-total coverage checks
  -> parcel-weighted BuildableEnvelopeV1 candidate
  -> RegulatoryConstraintSnapshot binding checks
  -> FeasibilityScenarioEligibilityV1
  -> Product Planning / Feasibility / Cash Flow
```

The applied same-PNU cache reuses only unexpired records explicitly marked
`publicData=true` and `reuseEligible=true` for cadastral characteristics,
land-use planning, building use, land characteristics, and official land
price. Reuse also requires an allowlisted public-API source type,
`source_verified` status, and a recomputed payload hash match. It strips the
originating project ID and tenant/user fields and deliberately excludes
registry, ownership, contracts, private uploads, and manually supplied
evidence. This is an operational public-evidence cache, not yet a
typed/versioned PostgreSQL knowledge base.

The envelope is `not_assessable` when an included parcel has a missing/invalid
or duplicate PNU, missing area, missing legal FAR, or when parcel area does not
reconcile to the declared site area. A numerically complete envelope remains
`conditional` and `reportGrade=false` unless its snapshot ID, parcel-set hash,
source SHA-256, parcel counts, effective date, policy identity/version, and
approval decision all match a separately verified decision. The structural and
hash checks are implemented, but production call sites do not yet load that
decision from an authoritative approval repository. Applied FAR above legal FAR
is `ineligible`, scores zero in the regulatory dimension, and is excluded from
product-value ranking.

This parcel-weighted result is still a preliminary development-rights
candidate. Final capacity may also depend on joint-development eligibility,
district-unit plans, road/setback/height/parking constraints, incentives,
donation requirements, and permit conditions.

## Source Authority by Domain

A single global source-priority list is unsafe. Use field/domain authority:

| Domain | Primary authority |
|---|---|
| Legal limits | Effective law, ordinance, notice, district plan, and official decision document |
| Permit conditions | Final permit/approval document |
| Planned program | Approved design/program scenario |
| Finance | Approved workbook, financing agreement, or reasoned override |
| Interpretation | Legal opinion as interpretive evidence, not a replacement for the law |

An Excel FAR or tax rate must not silently override a newer official rule.
Conflicting values remain unresolved until review.

## Deferred Work

The following prompt items are valid but not safe to claim complete in this
slice. In particular, Parser V2 conflict-store enforcement, full monthly
tranche replay, and production regulatory approval-store binding remain
unimplemented follow-up work:

1. map all 11,982 legacy formulas to versioned domain formulas and approved
   golden fixtures;
2. repair or explicitly exclude every broken/volatile Bonri formula before
   canonical approval;
3. implement mixed-use revenue/VAT, detailed cost allocation, and statutory
   charge formulas using verified effective-date legal sources;
4. add cohort/event-based sales collections, construction retention, tranche
   opening/draw/interest/fee/repayment/ending ledgers, account waterfall, and
   post-financing Max Minus;
5. make completed CalculationRuns append-only and create a new run ID with
   `supersedesRunId` for recalculation;
6. connect feasibility-workbook approval to the Parser V2 conflict repository
   and reject open `SourceConflict` records for the exact document version,
   candidate set, and project;
7. persist regulatory decisions in an authoritative approval repository and
   resolve the verified decision there before granting report-grade status;
8. promote same-PNU reuse from the current public cache to a typed/versioned
   PostgreSQL knowledge base with effective-date and authority selection,
   retention, access policy for non-public facts, and downstream invalidation
   when parcel or legal snapshot hashes change;
9. reconcile Parser V2 projection paths with the Pydantic V2 contract before a
   full V3 schema is introduced;
10. add the ten-tab review UI only after the calculation and approval contracts
   are stable.

No 2019 Bonri rate, broken formula, or hard-coded row count may be promoted as
an industry-standard rule without an approved basis and effective date.

## Verification Added

Focused regression tests cover:

- candidate/canonical isolation and explicit approval promotion;
- project-scoped archive existence, approval-time byte SHA-256 recomputation,
  server validation, source hash, and draft-hash binding;
- rejection of server-managed authority fields through generic project CRUD;
- rejection of broken formulas, blank required cells, and embedded open
  conflicts;
- approved V2 direct Cash Flow mapping;
- route-project binding and forced non-report-grade status for caller-supplied
  complete Cash Flow requests;
- the `F:BQ` 64-period source manifest, KRW million to KRW normalization,
  separated operating/financing/equity buckets, and row-profile reconciliation;
- Equity exclusion from PF capacity and mezzanine contract support;
- Excel midpoint, up, and down rounding;
- fresh same-PNU public-evidence reuse and exclusion of private/manual evidence;
- public-cache source verification and payload-tamper rejection;
- parcel-weighted FAR (`1,000㎡×200% + 2,000㎡×300% = 8,000㎡`);
- missing/duplicate parcel facts and unapproved aggregation;
- legal FAR breach scoring and product-ranking exclusion;
- rejection of self-attested legal-gate verification in Deal Optimization;
- input snapshot, constraint, scenario eligibility, and formula trace schema.
