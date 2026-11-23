# REALYTICS Decision Workspace UI

Updated: 2026-07-27
Status: Controlled implementation; production visual and multi-role smoke pending

## 1. Objective

The Decision Workspace turns the existing REALYTICS capabilities into a
professional review flow without changing the authority boundaries:

1. Supabase Auth establishes external identity.
2. FastAPI and PostgreSQL membership establish tenant, role, and permissions.
3. Parser V2 candidates remain separate from approved facts.
4. Calculation artifacts carry source and version lineage.
5. Execution completion does not imply human approval.
6. A generated report is an analysis-only draft until a separate final
   publication action passes every server-side gate.

The UI may summarize these records, but it cannot manufacture a value, locator,
approval, or eligibility state.

## 2. Implemented screen policy

| Surface | Primary question | Authoritative inputs | Allowed mutations |
|---|---|---|---|
| Login | Can this identity enter an invited tenant? | Supabase provider readiness and FastAPI membership boundary | Start configured Google/Kakao OAuth; development-only local pilot when explicitly enabled |
| Dashboard | What requires this user's attention next? | `/projects`, `/projects/recent-work` | Project/document intake through the existing uploader |
| Project context | Which project/version/Gate is being reviewed? | Project workspace context API | Navigation only |
| Document review | What did Parser V2 extract, where, and what remains unapproved? | Versioned document-review read model, protected source documents, exact locators, active ApprovedInputSnapshot | Append-only Parser V2 review decision and reviewed conflict resolution |
| Feasibility/PF | Which stored Cash Flow result is being considered and what blocks approval? | Latest Cash Flow artifact, workspace context, latest orchestration detail | Run the existing Feasibility-Cashflow Pod; navigate to evidence/Gate/report workflows |
| Orchestration approval | Did execution finish and may a different qualified person approve it? | Stored run plus freshly recomputed Approval Readiness Gate | Final approval with authenticated actor and AAL2 policy |
| Report | Is this an analysis draft or an immutable final decision package? | Saved report metadata, source run, Gate, immutable project snapshot | Generate draft; publish only through the final publication endpoint |

Every project-scoped read and mutation used by these surfaces must authorize the
request and verify the selected project against the authenticated tenant before
loading a document, run, report, export, template, risk/evidence artifact, or
Snapshot. Similar-case retrieval may compare projects inside the same tenant;
unknown or cross-tenant Snapshot lineage is excluded from its corpus.

## 3. Shared state and metric contract

The following axes are independent and must be shown independently:

- `executionStatus`: pending/running/completed/warnings/failed/cancelled/blocked;
- `reviewStatus`: not required/pending/in review/approved/rejected/revision;
- `approvalStatus`: not requested/pending/approved/rejected/superseded;
- `approvalReadinessStatus`: Gate not started/not ready/ready/needs review/hold;
- `evidenceStatus`: missing/candidate/located/unverified/verified/complete;
- `dataQualityStatus`: missing/partial/conflicting/reconciled/blocking;
- `reportEligibility`: analysis only/blocked/eligible;
- `valueNature`: reported/calculated/assumed/estimated.

An unknown runtime value is rendered as an explicit unknown/error state rather
than being coerced to success. A report-grade financial metric preserves, where
available:

- raw value and raw unit;
- display value and display unit;
- currency, basis, scenario, and as-of date;
- source record and source Snapshot;
- calculation Run and calculation version;
- data-quality, approval, and report-eligibility states.

Missing financial values remain `미확인`; they are never converted to zero.
This includes monthly Cash-In/Out, net and cumulative Cash Flow, Max Minus,
its occurrence month, and summary KPIs. An explicit numeric zero remains zero.

## 4. Source evidence viewer contract

The document-review workspace uses protected archive bytes, not reconstructed
or sample content:

1. Every source-document route authorizes the request before resolving the
   project through the request tenant's configured project repository. The
   document metadata reader is then bound to that exact returned project
   record; a normalized-PostgreSQL project can never fall back to the
   process-global compatibility projection. A project outside the request
   tenant is therefore not discoverable through source-document routes.
2. `GET/HEAD .../source-documents/{archiveId}/content` authorizes
   `evidence:read`, enforces project tenant scope, and records the view. For S3,
   `head_object` must bind the stored SHA-256, tenant, project, active lifecycle,
   size, ETag, and object version before a native `get_object(Range=...)`; the
   object version and `IfMatch` protect the interval read. PDF and OpenXML Excel
   originals support exactly one HTTP byte range with `Accept-Ranges`, `ETag`,
   `If-Range`, `206`, and explicit `416 Content-Range` behavior. Ambiguous
   multipart ranges are rejected. Local development uses content-addressed
   paths, actual stat size, ETag, and a bounded seek.
3. A selected PDF `pageNumber` is rendered from the same verified bytes. The
   full version-bound range is SHA-256 revalidated before rendering. The
   response carries authoritative page width, height, count, and coordinate
   space. The browser draws a BBox only when the stored locator contains four
   valid in-page coordinates in `pdf_points_top_left` and its file/document
   lineage matches the selected version; a missing, invalid, mismatched, or
   incompatible BBox remains visibly unoverlaid.
4. An Excel preview requires both stored `sheetName` and A1
   `cellReference/cellRange`. The API first applies the canonical Office ZIP
   entry/expansion/encryption/path safety gate, then returns at most 400 real
   cells around that locator. Formulas are displayed but never executed;
   external links are not resolved. Only the exact locator range is
   highlighted.
5. Missing archive IDs, pages, BBoxes, sheets, or cells are never inferred.
   Legacy binary `.xls` and non-PDF/non-OpenXML formats remain download-only.

The preview, PDF page, workbook-cell, and download actions have separate audit
event types. A live S3/MinIO byte-range and multi-role browser rehearsal is
still required before production acceptance.

### 4.1 Document-review read model

The browser does not assemble the decision state by racing document, run,
candidate, review, selection, conflict, and snapshot endpoints. One authorized
server endpoint returns `realytics-document-review-workspace-v1` with:

- `viewModelVersion`, deterministic `snapshotId`, SHA-256, `asOfDate`, and
  explicit freshness;
- source documents, Parser V2 run packages, candidates, reviews, selections,
  conflicts, active approved snapshots, and glossary candidates;
- a fail-closed review queue with `validationOutcome` and concurrent
  `issueCodes[]` rather than one overloaded validation status;
- a semantic evidence-locator inverse index used for source-to-Fact navigation;
- `(fieldKey, entityId)` Parser ApprovedInputSnapshot differences, including
  value, unit, document, source hash, locator, candidate/selection, authority,
  and effective-date changes.

A candidate-level approved review is not displayed as a snapshot-approved
Fact. Snapshot-approved counts require inclusion in the active
ApprovedInputSnapshot. The conflict action reuses the append-only server
contract and is enabled only when the selected winner has an applicable
approved review decision.

The response hash protects the assembled representation and allows the client
to reject a malformed/version-mismatched payload. It is not yet a claim of a
single repeatable-read PostgreSQL transaction across every underlying store;
that stronger database watermark remains a production gate.

### 4.2 Financial display binding

Feasibility and PF surfaces fail closed when an immutable decision-package
binding is absent:

- Feasibility metrics come from one stored Cash Flow result. Mutable workspace
  feasibility fields do not fill missing values.
- A calculation inherits a PF Review approval only when both sides explicitly
  carry the same orchestration Run ID, project Snapshot ID, and project
  Snapshot hash. Timestamp proximity and “latest” list order are not lineage.
- An unbound calculation remains `approvalStatus=unknown` and
  `reportEligibility=analysis_only`; the latest PF Review Gate is displayed as
  a separate package, not as the calculation's approval.
- PF Structure, Risk Register, Credit Decision, and Covenant/EOD are selected
  by one exact `artifactKey + podKey` identity and projected from that record's
  `result` only. Missing fields are not copied from a mutable project
  projection or another artifact.
- A standalone Covenant/EOD recomputation is a user-triggered analysis-only
  result. It neither replaces the PF artifact nor inherits approval.

This is an anti-mixing guard, not the final server authority. Existing records
usually lack the three exact lineage keys; the next contract must persist them
at CalculationRun/PF Review creation and validate them in one tenant-scoped,
snapshot-bound server read model.

## 5. Final approval and publication contract

Final PF approval is fail-closed:

1. The run must have a completed execution status.
2. Required handoff, risk, and assumption reviews must be closed.
3. The server recomputes Approval Readiness immediately before approval.
4. `approvalReadinessGate.pass` must be exactly `true`.
5. The actor comes from the authenticated request, not a form field.
6. The run requester cannot approve the same run.

Report generation is never publication. Every generated report starts with:

```text
status=draft
reportEligible=false
usageRestriction=analysis_only_not_final_approval
```

Final publication additionally requires an orchestration-bound report, an
approved source run, a freshly passing Gate, and a matching immutable project
Snapshot. The server recalculates the Snapshot's canonical-content SHA-256,
requires it to match the hash captured by the approved run, and places that
exact hash in the decision-package manifest. The publication record separately
seals the report content SHA-256. Repeating the exact publication is
idempotent; rebinding changed content, a modified Snapshot, or another decision
package is rejected.

## 6. Responsive and accessibility policy

- Desktop document review uses three panes: document/version navigation,
  source/locator workspace, and fact/evidence/review inspector.
- Small screens preserve every function through stacked regions or horizontal
  navigation; desktop-only information is not silently discarded.
- Dense financial tables retain explicit headers, units, tabular numerals, and
  lossless horizontal scrolling.
- State is expressed in text as well as color.
- Loading, empty, partial, error, blocked, and unknown states are durable page
  content, not transient toast-only messages.

## 7. Known controlled gaps

The following are deliberately displayed as limitations rather than simulated:

- version-bound PDF page-thumbnail and Excel sheet/used-range manifests backed
  by a render cache or controlled render job; calling the current full-byte
  page renderer once per thumbnail is prohibited;
- one repeatable-read PostgreSQL watermark for the complete document workspace;
- exact snapshot/scenario/calculation-bound PF composite read model and formal
  monthly Debt Ledger;
- accessible keyboard pane separators, persisted pane widths, cursor
  pagination, and full long-list/grid virtualization;
- role/permission/assignment/due-date aware work inbox and global navigation;
- grounded AI explanation tab; it remains absent until it can cite only the
  selected Fact's verified evidence locators;
- live S3/MinIO PDF/Excel byte-range, page-render, and multi-role audit smoke;
- organization-approved terms/privacy routes and final legal copy;
- complete migration of legacy local badges/raw color classes;
- deployed Google/Kakao, AAL2 maker-checker, tenant-isolation, accessibility,
  and visual regression rehearsal.

Until those gates close, the new screens are controlled review surfaces, not a
claim that production acceptance is complete.
