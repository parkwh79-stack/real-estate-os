# Modular Document Parsing Platform

Updated: 2026-07-15
Project: REALYTICS x PLATFORM9
Status: Controlled foundation
Production readiness: **Not production-ready**

## 1. Decision

REALYTICS adopts Parser V2 as an evidence-first, modular document-intelligence
platform for real-estate development, investment, and PF workflows. It is not a
single large prompt and it is not an alternate project database. It is a governed
intake and approval boundary over the existing Real Estate OS data plane.

```text
Object Storage document version
  -> security/integrity inspection
  -> structural adapters
  -> document classification
  -> project-selected extraction/normalization modules
  -> immutable fact candidates + source locators
  -> reconciliation/conflicts/validation
  -> human review decisions
  -> versioned canonical selections
  -> approved input snapshot
  -> existing Pod DAG, deterministic engines, Evidence Graph and reports
```

The platform must improve parsing depth without allowing extracted or inferred
content to become an authoritative project value by accident.

## 2. Non-negotiable invariants

1. Parsing output is candidate-only. A parser, LLM, adapter, or Pod may not merge
   its output directly into authoritative project fields.
2. Only an active `ApprovedInputSnapshot` may feed report-grade calculations,
   approval gates, or authoritative Agent handoffs.
3. `executionStatus`, `approvalStatus`, `reviewStatus`, `originType`,
   `presenceStatus`, and `conflictStatus` are independent state dimensions.
4. Every present critical candidate has an explicit locator: document page and
   paragraph/table/bbox, Excel sheet and cell/range, API row, Evidence ID, or an
   existing Agent artifact reference. API-submitted document locators must match
   the sanitized structural artifact for that run by both `evidenceId` and the
   complete canonical locator; a client-synthesized cell/page is rejected.
5. Missing, not disclosed, not applicable, and pending are different states. Zero
   and an empty string are never substitutes for missing evidence.
6. LLMs may classify, interpret, map, compare, and draft review notes. They may not
   authoritatively calculate Cash Flow, Max Minus, LTV/LTC/DSCR, IRR, Waterfall,
   Tranche recovery, or Exit sales rates.
7. Conflicting sources are preserved as `SourceConflict` records. There is no
   silent source-priority merge.
8. Every document, run, profile, parser, ontology, prompt, model, formula, and
   approved snapshot carries a version or content hash sufficient for replay.
9. Tenant and project scope is checked at every repository and handoff boundary.
10. Source binaries and unredacted full text stay in access-controlled Object
    Storage. Business tables store structured payloads, redacted excerpts, and
    hashes rather than unrestricted document bodies.

## 3. Implemented controlled slice

The first slice establishes reusable contracts and composition mechanics:

- `parsing_platform/contracts.py`: typed document version, run, locator,
  candidate, conflict, decision, canonical selection, snapshot, and finding models;
- `parsing_platform/registry.py`: version-aware `ParserModuleRegistry` and module
  manifests with explicit execution/data classes, schema contracts, locator
  policy, bounded outputs, and conditional capabilities;
- `parsing_platform/orchestrator.py`: project profiles, dependency planning,
  deterministic ordering, explicit capability-provider bindings, fail-closed/
  preserve-partial policy, least-context module inputs, traces, and replay hashes;
- `parsing_platform/schema_contracts.py`: immutable, versioned input/output
  contract ownership with fail-closed validation at every module call boundary;
- `parsing_platform/adapters.py`: baseline native PDF, XLSX, DOCX, and text
  structural adapters plus document-version-bound locators, archive/integrity
  checks, and bounded page/chunk/XML/relationship processing;
- `parsing_platform/default_modules.py`: inspection, structural extraction, and
  classification modules for the candidate-only intake profile. Structural
  extraction is reachable only after inspection grants the conditional
  `document.safe` capability;
- `parsing_platform/field_registry.py`: a small governed field/type/unit/entity
  registry and explicit, non-overwriting projections into existing OS schemas;
- `use_cases/parser_v2_profiles.py`: immutable project-specific profiles whose
  resolved module IDs and versions are pinned at save time;
- `use_cases/parser_v2_records.py`: backend-neutral append-only Parser V2 records
  projected through `deal_state_records`, including field-validation findings,
  source conflicts, decisions, selections, snapshot lineage, invalidations, and
  the bounded sanitized orchestration artifact needed to attest source locators;
- `routers/project_parser_v2.py`: candidate/run/profile/catalog routes and a
  Phase 2 review/selection/snapshot surface that remains disabled by default and
  additionally requires strict RBAC, strict tenant isolation, verified OIDC/JWKS
  bearer identity, an allowlisted tenant/user, and verified MFA claims. Approved
  snapshot responses are projected into the strict public contract rather than
  exposing persistence/audit envelope fields.

This is a foundation, not a claim that real OCR, HWP conversion, full clause
extraction, all real-estate Ontology packs, malware scanning, review UI, or
production PostgreSQL repositories are complete.

## 4. Candidate-only authority boundary

The following path is prohibited:

```text
parse result -> deep merge into project JSON -> run PF review
```

The required path is:

```text
DocumentVersion
  -> ParsingRun
  -> ParsedFactCandidate[]
  -> ValidationFinding[] / SourceConflict[]
  -> ReviewDecision[]
  -> CanonicalSelection[]
  -> ApprovedInputSnapshot
  -> approved-snapshot projection adapter
```

`extracted` describes provenance, not approval. A correctly extracted value may
still be stale, legally non-final, scoped to the wrong parcel, expressed on a VAT
exclusive basis, or contradicted by an executed agreement. A `derived` value is
eligible for review only when its input candidate IDs, Formula ID/version, and
CalculationRun are preserved.

Candidate and decision rows are append-only. Reparse, correction, withdrawal,
invalidation, and supersession create a new row or validity event. They do not
rewrite the historical observation. An effective read model materializes the
latest valid decision while the audit history remains intact.

Before the first review, the producing worker must seal the complete candidate
and conflict membership with an immutable `candidateSetHash`. Additional
candidates are rejected after sealing; a conflict discovered later invalidates
active approvals and makes the old seal stale, requiring a new parsing or
reconciliation run. Candidate producers cannot review their own output, and the
actor approving an input snapshot must differ from every candidate reviewer.

A derived candidate is accepted only when its Formula/engine version, exact
CalculationRun result JSON Pointer, selected result-value hash, input candidate
set, and input-snapshot hash all match the persisted deterministic run. A
snapshot containing a derived selection must also contain active selections for
the complete transitive input-candidate closure. Its authoritative
`sourceLocator` is the document-bound CalculationRun artifact/JSON Pointer/output
hash locator, and that locator is preserved through selection, snapshot,
projection, and Pod handoff.

## 5. Modular Pod and Agent composition

### 5.1 Module manifest

Every parsing module declares at least:

- stable `moduleId` and semantic `version`;
- capabilities plus `requires` and `provides` contracts;
- supported document, asset, and deal types;
- deterministic/AI execution classification;
- failure policy and whether partial output is preservable;
- input/output schema version and required locator precision;
- security classification and permitted Brain/provider policy.

The registry rejects duplicate module versions and resolves explicit pins such as
`structural_extraction@1.0.0`. Production project profiles should pin versions;
unversioned latest-module resolution is appropriate only for development or an
approved promotion policy.

### 5.2 Project parsing profile

A `ProjectParsingProfile` selects, excludes, combines, and orders modules by
project. It permits the same independently deployable modules to run alone or as a
composed Pod without copying their business logic.

Example target profiles:

| Profile | Typical modules |
|---|---|
| Residential development PF | business-plan, land-ledger, feasibility-workbook, presale, PF-term, permit, reconciliation |
| Logistics development | site/zoning, tenant/lease, cap-rate, construction schedule, senior/mezzanine/junior PF |
| Rental/housing | lease schedule, deposit/rent, NOI, valuation, DSCR, public-policy conditions |
| Bridge-to-PF | title/encumbrance, acquisition, bridge terms, PF conversion CP, completion obligation |
| Due diligence only | inspection, classification, extraction, conflict report; no approved projection |

The Orchestrator builds a dependency DAG from declared capabilities and uses a
stable topological order. A module cannot read another module's private Python
state; it receives only versioned outputs and context. Module failure is captured
as trace data. `fail_closed` stops dependent stages, while `preserve_partial`
retains already-supported candidates without presenting the run as approved.

### 5.3 Target module families

```text
Intake Core
  inspection -> structural extraction -> classification

Document Packs
  business plan | feasibility workbook | land ledger | registry/permit
  PF term sheet/loan agreement | trust/security | presale/lease | appraisal

Normalization Packs
  Korean real-estate terminology | party/entity identity | units/VAT/dates
  parcel/PNU | product hierarchy | tranche priority | schedule semantics

Control Packs
  evidence locator QA | deterministic validation | cross-document conflict
  review-package assembly | approved snapshot projection
```

Each document pack emits the common contracts. It does not call a downstream PF
engine directly.

## 6. Real-estate and PF standardization model

Parser V2 standardizes the common language, not the economic judgement of every
project. The core Ontology should remain small and versioned; asset-, deal-, and
document-specific extension packs evolve independently.

| Standard domain | Core identities and facts |
|---|---|
| Project/deal | project, scenario, asset type, deal type, stage, base/as-of date |
| Parties/roles | owner, developer, borrower, sponsor, contractor, trustee, lender, tenant, buyer |
| Land/title | parcel/PNU, area, ownership share, acquisition, registry, encumbrance, trust rights |
| Planning/permit | zoning, FAR/BCR, use, height, permit type, legal finality, effective date |
| Product/market | product hierarchy, unit/area basis, price basis, comparable, absorption, vacancy, cap rate |
| Revenue/cost | VAT basis, quantity/rate/amount, cost code, escalation, contingency, payment timing |
| Schedule | milestone, planned/actual/approved date, dependency, progress basis, delay condition |
| Capital stack | equity, bridge, senior, mezzanine, junior, limit, rate, fee, maturity, priority |
| Security/covenant | collateral, trust beneficiary right, completion, debt assumption, CP, covenant, EOD, cure |
| Cash flow/waterfall | account, draw, repayment, reserve, sweep, distribution tier, hurdle, recovery |
| Risk/approval | finding, severity, blocker, mitigant, condition, waiver, reviewer, decision |

Canonical values require explicit unit and basis. Examples include KRW versus
KRW hundred-million, square metre versus pyeong, decimal rate versus percentage,
VAT inclusive/exclusive, gross/net area, appraisal/sales/unsold LTV, and monthly
date semantics. Tranche labels are normalized from Korean and English aliases, but
legal priority is stored as an explicit priority/order field; string labels alone
do not determine repayment rights.

Source authority is domain-specific. An executed contract may govern contractual
obligations, a registry may govern title, an approved scenario may govern a
calculation assumption, and dated market evidence may govern a comparable set.
One global numeric authority rank must not overwrite these separate policies.

## 7. Persistence and Repository model

### 7.1 Current runtime authority

The controlled slice currently uses the backend-neutral `deal_state_records`
projection through `ParserV2RecordStore`. This avoids a second runtime database
path while contract behavior and migration compatibility are tested.

This projection does not provide the transaction isolation needed for concurrent
reviewers or multiple workers: several operations are read-then-insert and a
snapshot spans multiple logical records. Therefore the Phase 2 approval surface
must remain off until the typed PostgreSQL repository performs decision,
selection, snapshot, invalidation, and outbox writes with database transactions,
locks/compare-and-swap semantics, tenant RLS, and concurrency tests.

Migration `0006_parser_v2_platform.sql` adds the normalized PostgreSQL target:

| Table | Purpose |
|---|---|
| `parser_document_versions` | immutable Object Storage identity, version, hash, authority, lineage |
| `parser_runs` / `parser_run_events` | immutable run identity and append-only execution/approval transitions |
| `parser_project_profiles` / `parser_candidate_set_seals` | immutable pinned profile/plan and sealed review-package membership |
| `parsed_fact_candidates` / `parser_candidate_inputs` | candidate values and derived-fact input graph |
| `parser_validation_findings` / event rows | immutable validation body and append-only resolution/reopen/waiver history |
| `parser_source_conflicts` / membership rows | preserved conflicting claims and their candidate set |
| `parser_review_decisions` | append-only human decision ledger |
| `parser_canonical_selections` | versioned, reviewed selection per field/entity |
| `parser_approved_snapshots` | immutable set of approved selections for downstream use |
| `parser_validity_events` | append-only withdrawal, invalidation, and supersession audit |

The normalized tables use typed scope/status/version/hash columns for operational
queries and JSONB for versioned contract payloads. Composite foreign keys prevent
cross-project references. Join tables preserve conflict membership, derivation
inputs, snapshot selections, and source runs.

They are **not yet the active runtime repository**. Promotion requires a typed
PostgreSQL Repository implementation, transaction tests, backfill/reconciliation,
dual-read comparison, tenant isolation tests, and an explicit cutover. The
`deal_state_records` projection remains authoritative until that work is complete.

### 7.2 Repository responsibilities

A Parser Repository must:

- require tenant and project on every method;
- verify the archived Object Storage identity and SHA-256 before creating a run;
- perform idempotent inserts using stable contract hashes;
- insert an entity and its membership rows in one transaction;
- prohibit in-place mutation of append-only rows;
- enforce legal state transitions and monotonic decision/selection versions;
- materialize effective validity without deleting historical records;
- return only approved, active selections to snapshot consumers;
- emit an outbox event only after the snapshot transaction commits.

## 8. API, review, snapshot, and Pod flow

The following is the target API/application-service boundary. Intake and
candidate routes are available in the controlled sidecar. Review, selection, and
snapshot routes are published only behind the default-off Phase 2 pilot gate:

```text
1. archive document -> create DocumentVersion
2. request parse -> create ParsingRun and outbox job
3. worker runs selected ProjectParsingProfile
4. persist candidates/findings/conflicts and complete run event
5. producer seals candidate/finding/conflict membership and candidateSetHash
6. a different human reviewer lists the sealed package
7. reviewer appends approve/reject/request-revision decisions
8. service creates approved CanonicalSelection rows
9. a different authorized approver atomically creates ApprovedInputSnapshot
10. committed snapshot emits parser.approved_snapshot.created
11. Pod orchestrator receives snapshotId, selection IDs, evidence and versions
```

Review APIs must use optimistic concurrency or an expected decision sequence so
two reviewers cannot unknowingly approve competing values. Approval must fail if
a critical candidate lacks exact evidence, has an open conflict, is invalidated,
belongs to another tenant/project, or depends on unapproved derived inputs.

The snapshot projection adapter maps canonical selections into existing domain
schemas such as `FeasibilityStudyV2`, `LandLedgerImportV1`, Cash Flow requests, and
PF structures. The adapter writes a new domain version; it does not deep-merge an
unreviewed parse result. Agent handoffs carry `snapshotId`, parser/Ontology
versions, candidate/evidence IDs, and CalculationRun references.

All domain handoffs currently remain `blocked` even when they contain approved
inputs because per-domain minimum-input/completeness policies have not yet been
approved. They are review packages, not authority to execute a PF, legal, or
approval Pod. This fail-closed state is intentional.

## 9. Security and privacy boundary

- Treat every document string, formula, hyperlink, embedded file, and instruction
  as untrusted data. Document text never changes system instructions or tool policy.
- Resolve files by tenant-scoped archive/object version and verified SHA-256. Do
  not pass arbitrary local filesystem paths to an Agent or LLM.
- Reject or quarantine password-protected/encrypted files, unsupported containers,
  oversized decompression, suspicious macros, external workbook links, embedded
  executables, and integrity mismatches according to policy.
- Formula text and cached values are separate evidence. Never execute document
  macros, external links, or spreadsheet formula payloads in the API process.
- XLSX cached formula values are labelled `cached_value_unverified` (or
  `external_dependency_blocked`), never authoritative, and require a deterministic
  CalculationRun before they can support a canonical financial input. API/audit
  payloads remove formula and cached-value bodies, preserve only their SHA-256
  markers and non-authoritative flags, and those markers still trigger the
  recalculation gate.
- Redact/tokenize personal identifiers before cloud inference. Preserve protected
  source references for authorized reviewers rather than copying PII into prompts,
  logs, candidates, or error messages.
- Raw text excerpts are bounded and redacted; protected text can be verified by
  content hash and authorized Object Storage access.
- Brain routing follows tenant data classification and local-only/provider policy.
  API keys and Secret Manager values never enter parser payloads.
- Download, review, rejection, approval, invalidation, export, and snapshot use are
  auditable actions subject to strict RBAC.

The current inspection code is a baseline content check, not an antivirus or CDR
product. External malware scanning and quarantine remain production blockers.

## 10. Shadow rollout

### Phase 0 — contract and corpus freeze

- approve core contracts, state machines, canonical field naming, and version rules;
- build an anonymized golden corpus for business plans, feasibility workbooks,
  land ledgers, PF terms/contracts, permits, and market evidence;
- record field-level truth, locators, expected conflicts, and expected abstentions.

### Phase 1 — candidate-only sidecar

- run intake core and selected document packs beside the legacy parser;
- persist only Parser V2 candidates and traces;
- prohibit project projection, Pod triggers, and report use;
- compare extraction accuracy, locator precision, latency, and reviewer effort.

### Phase 2 — review and snapshot pilot

- enable review package, decision ledger, canonical selection, and snapshot creation;
- project approved snapshots into an isolated pilot domain version;
- compare deterministic Cash Flow, Max Minus, LTV and Waterfall results against the
  reviewer-approved workbook and existing engines.

### Phase 3 — PostgreSQL Repository promotion

- apply migration to an isolated PostgreSQL/PostGIS environment;
- run dual-write or replay backfill and reconcile hashes/counts;
- prove transaction, idempotency, RLS/RBAC, multi-worker, retry, and recovery behavior;
- cut over reads only after shadow parity and rollback rehearsal pass.

### Phase 4 — controlled production adoption

- enable one document/profile/tenant cohort at a time;
- preserve legacy rollback and snapshot lineage;
- promote Ontology, prompt, model, and module versions only through evaluated,
  human-approved release policy.

## 11. Acceptance gates

Parser V2 may not become authoritative until all applicable gates pass:

- 100% explicit locator coverage for PF-critical approved fields;
- zero fabricated critical values and zero silent conflict merges;
- zero unapproved or invalidated candidates in an approved snapshot;
- deterministic replay produces the same normalized payload hash for the same
  source and version set, excluding generated IDs/timestamps;
- critical numeric exact-match target at least 99.5% on the approved corpus;
- blocker false-negative rate is zero on the release corpus;
- no plaintext regulated PII, credential, or unredacted source body in logs and
  business payloads;
- profile dependency, failure-policy, retry, cancellation, and partial-preservation
  behavior is tested;
- PostgreSQL tenant isolation, idempotency, concurrency, migration, backup/restore,
  and rollback tests pass;
- an authorized reviewer can trace every approved value and resulting report claim
  back to document version, locator, decision, and snapshot.

## 12. TODO-SDD — required design and implementation work

The following items are intentionally incomplete and must not be represented as
production capability:

| ID | Required work | Exit evidence |
|---|---|---|
| TODO-SDD-PARSE-001 | Real OCR pipeline for scanned PDF/images, layout/table/bbox preservation, Korean accuracy calibration | golden-corpus OCR and locator benchmark |
| TODO-SDD-PARSE-002 | Isolated HWP/HWPX conversion and native structure adapter | sandboxed conversion, malformed-file and fidelity tests |
| TODO-SDD-SEC-001 | Real malware scanner/CDR, macro and embedded-object quarantine, archive-bomb limits | scanner integration, quarantine audit, failure rehearsal |
| TODO-SDD-AUTH-001 | Domain authority matrix for title, permit, contract, appraisal, market, approved scenario, and effective dates | legal/credit/industry review and versioned policy tests |
| TODO-SDD-UI-001 | Reviewer workbench for side-by-side evidence, conflicts, revisions, bulk decisions, and snapshot diff | RBAC/E2E/accessibility tests and reviewer sign-off |
| TODO-SDD-OPS-001 | Production monitoring for module/run latency, failure, retry, dead letter, drift, locator coverage, review backlog, and cost | dashboards, alerts, SLOs, and incident Runbook |
| TODO-SDD-DB-001 | Typed PostgreSQL repositories, migration application, RLS/tenant tests, backfill, dual-read parity, and rollback | live PostgreSQL multi-worker report |
| TODO-SDD-STORAGE-001 | S3/MinIO IAM, versioning, retention, legal hold, backup/restore, and signed download policy | restore rehearsal and access audit |
| TODO-SDD-DOMAIN-001 | Versioned extraction/validation packs for business plan, feasibility Excel, land ledger, PF agreements, trust/security, permits, leases and appraisals | per-pack schemas, corpus metrics, reviewer acceptance |
| TODO-SDD-ONTOLOGY-001 | Core/extension Ontology registry, alias governance, migration rules, and candidate-term promotion | version compatibility and rollback tests |
| TODO-SDD-PRIVACY-001 | PII discovery/redaction/tokenization, retention and provider-routing controls | leakage tests and privacy review |
| TODO-SDD-INTEGRATION-001 | Approved snapshot projection into Pod DAG, CalculationRun, Evidence Graph, Risk/Approval Gate, and reports | end-to-end pilot with evidence trace |
| TODO-SDD-TX-001 | Seal a run's complete candidate/finding/conflict set and commit review, canonical selection, snapshot, validity events, and outbox records atomically | competing-review and worker-crash transaction tests |
| TODO-SDD-EVIDENCE-001 | Promote candidate-embedded locators to a first-class, tenant-scoped Evidence Graph record with resolvable IDs and retention policy | locator resolution, access-control, and deletion/retention tests |
| TODO-SDD-HANDOFF-001 | Version typed Pod handoff payloads and approve per-domain minimum-input/completeness rules before a handoff may set `canExecute=true` | domain-owner sign-off and missing-input/blocking tests |
| TODO-SDD-CALC-001 | Move the implemented exact CalculationRun/output/hash/input-snapshot binding into the typed PostgreSQL transaction and first-class Evidence Graph | live tamper/replay and output-lineage tests |
| TODO-SDD-SOD-001 | Extend the implemented producer/reviewer/snapshot-approver separation with delegated approval limits for critical financial/legal fields | role-conflict and approval-limit tests |
| TODO-SDD-PRODUCER-001 | Extend the implemented structural-artifact `evidenceId`/locator attestation by binding each extracted candidate to its candidate-emitting module output hash inside the sealed worker transaction | forged-candidate and module-output inclusion tests |

## 13. Immediate next implementation order

1. Freeze contracts and golden-corpus acceptance fixtures.
2. Remove or feature-gate any unreviewed parser-to-project deep merge.
3. Complete top-priority document packs: feasibility workbook, business plan, land
   ledger, and PF terms.
4. Add review decisions, canonical selections, and snapshot APIs/UI behind a
   candidate-only feature flag.
5. Connect approved snapshots—not raw candidates—to deterministic domain engines.
6. Promote the PostgreSQL Repository after live migration and isolation tests.
7. Expand Ontology and document packs through governed release cycles.

This ordering makes modularity useful without weakening financial, legal, or
approval controls while the remaining production infrastructure is completed.
