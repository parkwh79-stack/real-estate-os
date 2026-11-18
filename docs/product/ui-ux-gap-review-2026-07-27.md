# UI/UX implementation gap review — 2026-07-27

Status: Implemented vertical slice with controlled remaining gates

## Decision

The five reference screens are design inputs, not templates to copy. The
implementation order remains:

1. document review;
2. feasibility/PF and approval;
3. role-aware work inbox and portfolio;
4. simplified institutional login;
5. optional dark monitoring mode.

The default product remains a light, evidence-first professional workspace.
No AUM, IRR, recommendation, locator, approval, or freshness value may be
invented to make a screen resemble a reference image.

## Implemented in this slice

| Requirement | Result | Authority rule |
|---|---|---|
| One document-review view model | Implemented | FastAPI authorizes the project once and returns a typed, sealed v1 response |
| Version identity | Implemented | `viewModelVersion`, deterministic `snapshotId`, SHA-256, as-of, observed-at, freshness |
| Review Queue | Implemented | Multiple issue codes may coexist; missing materialized items fail closed |
| Fact to evidence | Implemented | Exact PDF page/BBox or Excel sheet/cell is selected only from stored locators |
| Evidence to Facts | Implemented | Collision-safe semantic locator keys map a source location back to every related Fact; empty locators do not group |
| Authority stages | Implemented | candidate, reviewed, canonical selection, active snapshot approval, superseded are not collapsed |
| Snapshot diff | Implemented | Parser ApprovedInputSnapshots compare by `(fieldKey, entityId)` and show lineage changes |
| Conflict action | Implemented | Existing append-only API; an approved winning-candidate review is required |
| Validation contract | Implemented | `validationOutcome` is separate from concurrent `issueCodes[]` |
| Confidence wording | Implemented | Displayed as extraction confidence, never approval confidence |
| Readiness vs approval | Implemented | `approvalReadinessStatus` has its own shared badge and orchestration projection |
| Execution status adapter | Implemented for `recent_work_v2` | Raw technical state and stable mapping reason are preserved; unknown states fail closed |
| Financial package anti-mixing | Implemented as an interim guard | Exact run/snapshot ID/hash is required before approval inheritance; one PF artifact supplies all PF fields |

The read model avoids browser-side `Promise.all` assembly and N+1 run-package
requests. Its top-level Pydantic response contract rejects extra or inconsistent
fields; nested source/run/package structures still require typed contracts. The
Web client rejects schema/version/hash identity mismatches. Inverse locator
identity includes document version, file hash, API endpoint/row,
artifact/JSON path, paragraph, Excel sheet/cell, and PDF page/BBox as
applicable.

## Deliberately not simulated

### Full PDF and workbook navigation

The current exact-page PDF renderer revalidates the complete version-bound
object before rendering. Using it to request every page thumbnail would reread
the original once per page. A safe implementation therefore requires a
tenant-scoped render job/cache keyed by archive ID, object version, SHA-256,
renderer version, and page number. Excel similarly needs a bounded sheet and
used-range manifest. Until those contracts exist, navigation remains limited
to real stored locators; fake page or sheet entries are prohibited.

### Transaction-consistent workspace watermark

The response is assembled once on the server and carries a deterministic hash,
but not every underlying compatibility repository is read in one PostgreSQL
repeatable-read transaction. Production completion requires normalized tables
and one database watermark or equivalent immutable manifest.

### PF composite authority and Debt Ledger

The financial workspace must not independently choose the latest calculation,
latest orchestration, latest snapshot, and latest financing structure. Add an
exact decision-package binding before expanding the visual KPI surface:

```text
projectSnapshotId/hash
parserApprovedSnapshotId/hash
calculationRunId/version/inputHash/outputHash
scenarioSetId/version
financingStructureId/version
gateEvaluationId/asOfDate
reportVersion/publicationId
```

A formal monthly Debt Ledger must then reconcile opening balance, draw,
capitalized/cash interest, fees, scheduled repayment, cash sweep, closing
balance, and undrawn commitment by tranche. Existing Cash Sweep and Waterfall
views must not be labelled as that ledger.

As an interim fail-closed guard, the current UI no longer mixes mutable
feasibility fallbacks into a CalculationRun result and does not apply a latest
approval to an unbound calculation. PF Structure/Risk/Credit/Covenant values
come from one exact Chief artifact. This prevents false authority but does not
replace the required server-verified composite read model.

### Role-aware work inbox

The current Dashboard uses real APIs, but it is not yet a permission,
assignment, deadline, and maker-checker aware queue. A server read model must
add task type, owner/assignee, due date, required permission, project authority,
review status, readiness, and safe next action. Portfolio KPIs must be a
separate approved/report-eligible aggregation, not the operational inbox.

### Accessibility and scale

CSS resize and `content-visibility` are interim optimizations, not completion.
Keyboard-operable separators, focus management, saved widths, cursor
pagination, mounted browser tests, and proper PDF/Fact/Excel virtualization
remain required.

### AI explanation

No AI assistant tab was added. It may be introduced only after a grounded
response contract guarantees citations to the selected Fact's real evidence
locators, labels abstention, and cannot mutate review or approval state.

## Next implementation order

1. Normalize the document workspace stores and add one repeatable-read
   watermark plus version-bound render manifests.
2. Add the exact snapshot/scenario/calculation-bound PF read model.
3. Implement and reconcile the formal monthly Debt Ledger.
4. Add role/permission/assignment-aware inbox and navigation.
5. Add reviewed Agent Run, Outbox, and Coding Pod execution adapters.
6. Complete accessible pane controls, pagination, virtualization, and mounted
   multi-role browser tests.
7. Finish semantic-token migration and visual regression; evaluate dark
   monitoring mode last.

## Acceptance evidence for this slice

- API contract/read-model/orchestration/conflict focused tests pass.
- Web and shared UI TypeScript checks pass.
- Web lint passes.
- Document-review client tests cover the single read-model request, seal and
  version rejection, conflict-resolution payload, locator geometry, and
  collision-safe inverse keys.
- Financial lineage tests cover exact binding, missing/mismatched lineage,
  duplicate artifact identity, and single-artifact PF projection.

Live S3/MinIO, deployed Google/Kakao AAL2 maker-checker, multi-role tenant
isolation, and full visual/accessibility rehearsal remain operating gates and
are not claimed by these local tests.
