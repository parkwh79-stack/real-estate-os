# Server Storage and User Audit Log Plan

Updated: 2026-06-11
Project: REALYTICS x PLATFORM9

## Goal

REALYTICS should not remain a local JSON-only pilot. A production Real Estate OS must preserve project inputs, source documents, agent outputs, evidence, risk decisions, user actions, and historical outcomes on the server so reviewers can answer:

- Which user uploaded or changed the data?
- Which source document, Excel cell, API row, or agent output produced each number?
- Which Agent blocked approval and why?
- Which risks were approved, waived, revised, or resolved?
- Which historical cases were similar and how did they perform after approval?

## Server Components Needed

| Server / Service | Why Needed | Current Status |
|---|---|---|
| API Server | Agent execution, project CRUD, evidence lookup, report generation, audit endpoints | Implemented for MVP |
| PostgreSQL / SQLite Runtime DB | Structured project, run, risk, evidence, snapshot, and user activity storage | SQLite MVP implemented; PostgreSQL migration recommended |
| Object Storage | Original PDFs, Excel files, DOCX reports, registry records, official price evidence, exports | Runtime archive wrapper exists; S3/MinIO/NAS recommended |
| Background Worker | Long-running parsing, API enrichment, report export, batch scenario runs | Planned |
| Connector Proxy Server | VWorld, public data, Korean Law MCP, Valuemap, licensed registry or market data | Configurable connector baseline exists |
| Auth / Session Service | Real login, tenant, user, role, session, permission, and secure API-key handling | Tenant/user/session MVP plus pilot web session UI implemented; production IdP/RBAC planned |
| Audit Log Service | API/user activity logs, raw evidence access logs, approval logs, troubleshooting | Baseline user activity log implemented |
| Search / Vector Store | Similar case search, Evidence Graph retrieval, RAG over past review packages | Planned |

## Data That Must Be Stored on the Server

### 1. Project / Deal Records

Store:

- `project`
- `site / land`
- `architectural_plan`
- `feasibility_input`
- `product_mix`
- `pf_structure`
- `schedule`
- `status`

Purpose:

- Canonical project state
- API, MCP, UI, batch job reuse
- Historical comparison and future snapshots

### 2. Source Documents

Store metadata in DB and original binaries in object storage.

Store:

- original file name
- file hash
- file size
- document type
- uploader
- upload time
- parser status
- archive ID
- download permissions

Examples:

- business plan PDF
- architectural summary PDF
- feasibility Excel
- land ledger XLS
- PF termsheet
- registry record
- official land price evidence
- final report exports

### 3. Land Evidence

Store per parcel/PNU:

- PNU
- cadastral characteristics
- official land price
- base year
- base date
- price per sqm
- source name
- source URL or evidence ID
- registry evidence
- registered owner
- owner match status
- ownership share
- mortgage, seizure, provisional registration, easement, trust registration flags
- release required
- release condition

Rule:

Generated PNU alone is not enough. Legal/PF readiness requires source-backed official land price, cadastral, and registry/encumbrance evidence.

Current implementation:

- `land_evidence` is stored in JSON/SQLite runtime state.
- SQLite table: `land_evidence`.
- APIs:
  - `GET /projects/{project_id}/land-evidence`
  - `POST /projects/{project_id}/land-evidence`
  - `POST /projects/{project_id}/land-ledger/enrich` with `persistEvidence: true`
- Supported evidence types:
  - `pnu_resolution`
  - `cadastral_record`
  - `official_land_price`
  - `registry_record`
  - `land_acquisition_status`
- `land_acquisition_status` records should include source-backed closing/acquisition fields such as:
  - `acquisitionAreaRatio`
  - `acquisitionAmountRatio`
  - `acquisitionStatus`
  - `closingStatus`
  - `sourceName`
  - `sourceUrlOrEvidenceId`
  - `verificationStatus`
  - `reviewedBy`
  - `reviewedAt`
- RBAC:
  - read requires `evidence:read` in strict mode
  - write requires `project:write` in strict mode
- PF Review integration:
  - persisted records are hydrated into the orchestration project payload before each PF review run
  - Land Agent uses these records for PNU, official price, registry, acquisition/closing readiness, and source-locator readiness
  - Legal Review uses registry evidence for land-rights and encumbrance checks
  - Risk Register receives land evidence IDs/source locators for audit-grade risk rows
  - Evidence Graph can link land risks back to stored evidence records
  - Approval Readiness Gate checks and clears the following land gates:
    - `LAND_PNU_READY`
    - `LAND_OFFICIAL_PRICE_READY`
    - `LAND_REGISTRY_EVIDENCE_READY`
    - `LAND_ACQUISITION_READY`

### 4. Orchestration Runs

Store:

- run ID
- project ID
- workflow
- step statuses
- `agentProgress`
- `handoffs`
- artifacts
- errors
- `qualityCheck`
- `approvalReadinessGate`
- user ID
- AI provider/runtime context

Purpose:

- Reproduce review state
- Explain why a deal was blocked
- Compare runs over time

### 5. Agent Handoffs

Store:

- from Agent
- to Agent
- payload schema
- payload
- evidence IDs
- confidence
- blocking issues
- review status
- reviewer
- reviewed at

Purpose:

Marketability, Land, Legal, Feasibility, Cash Flow, PF Structuring, and Credit Review Agents must communicate through auditable handoff envelopes, not hidden prompt text.

### 6. Risk Register

Store:

- risk ID
- risk code
- category
- severity
- probability
- financial impact
- source locators
- evidence IDs
- review status
- reviewer
- waiver reason
- comments
- review history

Purpose:

Final approval must remain blocked while material risks are unresolved.

Current implementation:

- `risk_registers` is stored in JSON/SQLite runtime state as a first-class server record set.
- SQLite table: `risk_registers`.
- PF review orchestration automatically upserts the `riskRegister` artifact by `projectId + runId`.
- Risk row review actions synchronize the updated review status, waiver reason, comment, reviewer, and review history back to the persisted Risk Register record.
- APIs:
  - `GET /projects/{project_id}/risk-registers`
  - `GET /projects/{project_id}/orchestrations/{run_id}/risk-register`
  - `POST /projects/{project_id}/orchestrations/{run_id}/risk-register/{risk_id}/review`
- RBAC:
  - list/get uses `project:read` in strict mode
  - row review uses `risk:review` in strict mode
- Audit:
  - each row review emits a `risk_register_row_review` user activity event.

### 7. Evidence Graph

Store:

- claim nodes
- source nodes
- calculation nodes
- risk nodes
- report section nodes
- edges

Purpose:

Reviewers and auditors can trace any report sentence, number, or risk row back to a document page, Excel cell, API record, or Agent output.

Current implementation:

- `evidence_graphs` is stored in JSON/SQLite runtime state as a first-class server record set.
- SQLite table: `evidence_graphs`.
- PF review orchestration automatically upserts the `evidenceGraph` artifact by `projectId + runId`.
- Persisted metadata includes graph ID, node count, edge count, risk node count, source locator count, and update time.
- APIs:
  - `GET /projects/{project_id}/evidence-graphs`
  - `GET /projects/{project_id}/orchestrations/{run_id}/evidence-graph`
- RBAC:
  - list/get uses `project:read` in strict mode.

### 8. Approved Assumption Overrides

Store:

- override ID
- project ID
- Data Quality check key
- canonical field
- approved value
- unit
- approving reviewer
- approval reason
- evidence IDs
- source locators
- status
- effective status
- expiry timestamp
- status change reviewer
- status change reason
- superseded-by override ID
- audit timestamps

Purpose:

When source documents or agents disagree on critical numbers, reviewers can choose an authoritative value without deleting the original conflict.

Current implementation:

- `assumption_overrides` is stored in JSON/SQLite runtime state.
- SQLite table: `assumption_overrides`.
- APIs:
  - `GET /projects/{project_id}/assumption-overrides`
  - `POST /projects/{project_id}/assumption-overrides`
- PF review orchestration attaches active overrides before Data Quality review.
- Matching Data Quality checks are marked `overridden`; original status, severity, values, and diff metadata are preserved.
- Saving an override emits an `assumption_override_approved` user activity event.
- Override lifecycle supports `active`, `revoked`, `expired`, and `superseded`.
- A new active override for the same `checkKey + canonicalField` automatically marks the previous active override as `superseded`.
- Records with expired `expiresAt` are exposed as `effectiveStatus=expired` and are excluded from active PF review hydration.
- Revoke/expire actions are saved through the same API and emit `assumption_override_status_changed` user activity events.
- The orchestration page lists stored overrides and provides reviewer actions to revoke or expire an approved value before rerunning PF Review.

### 9. Restructuring Cure Records

Store:

- cure ID
- project ID
- cure type
- status: `active`, `revoked`, `expired`, `superseded`
- target risk codes such as `SENIOR_SHORTFALL` or `UNSOLD_LTV_BREACH`
- cure amount and unit
- target metric and value
- reviewer and review timestamp
- source URL or evidence ID
- evidence IDs
- source locators
- applies-to run ID
- expiry timestamp
- audit timestamps

Purpose:

When a pilot run is rejected or marked for rework because of senior tranche shortfall, Unsold LTV breach, weak recovery, or missing financial mitigants, reviewers must be able to select the adopted cure before rerunning PF review. The system records the cure decision separately from the calculation result so a saved cure does not automatically clear approval blockers without source-backed evidence and a rerun.

Current implementation:

- `restructuring_cures` is stored in JSON/SQLite runtime state.
- SQLite table: `restructuring_cures`.
- APIs:
  - `GET /projects/{project_id}/restructuring-cures`
  - `POST /projects/{project_id}/restructuring-cures`
- PF review orchestration attaches active cure records as `adoptedRestructuringCures` before the restructuring step.
- `RestructuringAgent` returns `adoptedCures` summary, including active count and source-backed active count.
- The `restructuring_to_credit_review` handoff includes adopted cure metadata.
- Saving a cure emits a `restructuring_cure_selected` user activity event.
- The web orchestration page includes a cure selection panel for senior reduction, Equity/DSRA injection, additional collateral, bulk sale/forward sale, refinancing evidence, and repayment waterfall strengthening.

### 10. Market Evidence

Store:

- comparable cases
- presale cases
- actual transactions
- rent comparables
- source metadata
- base date
- license note
- confidence
- evidence locator

Purpose:

Marketability Agent and Feasibility Agent should share source-backed pricing and rent assumptions.

### 11. Feasibility Workbook Versions

Store:

- original workbook
- parsed schema version
- parser version
- named range map
- formula preservation status
- validation results
- change reason
- version history

Purpose:

Developer feasibility and PF review must know which Excel version was authoritative.

### 12. Report Exports

Store:

- report ID
- version
- Markdown
- JSON
- print-ready HTML
- PDF/DOCX export metadata
- source run ID
- generated by
- generated at

Purpose:

Investment committee packages must be reproducible.

### 12. Historical Similar Cases

Store immutable snapshots:

- project metrics
- land readiness
- marketability grade
- PF terms
- scenario results
- risk register
- credit decision
- approval conditions
- historical outcome fields

Implemented historical outcome fields:

- actual sales rate
- realized discount rate
- repayment completion and repayment date
- extension/default/EOD history
- loss amount and recovery rate
- senior/mezzanine/junior recovery rates
- actual exit source
- source metadata and source locators

Future outcome fields:

- actual land closing completion
- actual construction delay and completion date
- actual covenant breach history

### 13. User, Session, and Activity Logs

Store:

- user ID
- tenant/organization ID
- session ID
- provider
- role
- login events
- API request logs
- approval/waiver actions
- raw evidence access events
- report export/download events

Purpose:

When a pilot or production workflow fails, the operator can see who did what, when, and against which project/run/risk/evidence record.

## Current User Activity Log Implementation

Implemented baseline:

- `user_activity_logs` in JSON/SQLite runtime state
- API request middleware in `services/api-server/main.py`
- `POST /audit/login-event`
- `GET /audit/user-activity`
- explicit `source_document_download` events
- explicit `report_export_created` events
- explicit `report_export_download` events
- User ID extraction from:
  - `x-realytics-user-id`
  - `x-user-id`
  - `x-codex-user-id`
  - `user_id` query param
- Project ID extraction from `/projects/{project_id}` paths
- Run ID extraction from `/orchestrations/{run_id}` paths

Current pilot auth UI:

- `/settings` creates or reuses a local pilot tenant, user, and session.
- The browser stores the returned session ID in local storage under `realytics.auth.session.v1`.
- The shared web auth client sends `X-REALYTICS-SESSION-ID` and `X-REALYTICS-USER-ID` when a stored session exists.
- Pilot-critical client workflows now use the shared auth client:
  - document upload
  - real project input package upload
  - land evidence and assumption override upload
  - PF review orchestration run
  - evidence review
  - handoff review
  - Risk Register review
  - assumption override approve/revoke/expire
  - restructuring cure selection
  - historical outcome entry
  - snapshot similar-case/diff workflows
  - Evidence Graph auditor

Stored fields:

- event ID
- event type
- created at
- user ID
- project ID
- run ID
- method
- path
- status code
- duration ms
- client host
- user agent
- request ID
- metadata

Current endpoints:

```text
POST /audit/login-event
GET /audit/user-activity
GET /audit/user-activity/summary
GET /audit/user-activity/export?format=csv
```

Example:

```text
GET /audit/user-activity?user_id=analyst-1&limit=100
```

Frontend review views:

```text
/audit
/project/{project_id}/audit
```

These screens show total activity count, distinct users, sensitive document/report events, slow requests, event-type mix, and recent event rows with metadata preview.

## Important Limitation

This is not full authentication yet. It is activity logging, persisted identity/session records, local pilot session propagation, and troubleshooting infrastructure.

## Current Identity / Session Baseline

Implemented MVP:

- `tenants`, `users`, and `sessions` in JSON/SQLite runtime state
- `POST /auth/tenants`
- `POST /auth/users`
- `GET /auth/users`
- `POST /auth/session`
- `GET /auth/me`
- Session-aware audit middleware through `x-realytics-session-id`
- Role-derived permission hints for `admin`, `reviewer`, `analyst`, and `viewer`
- RBAC check baseline for audit log read/export, source document downloads, report export creation, and report export downloads
- `REALYTICS_RBAC_MODE=strict` to block unknown users and underprivileged persisted roles

Important rule:

The MVP session model stores who the user is and what role the system should assume. Strict mode enforces the first sensitive endpoints, but production deployment still needs a real identity provider, tenant isolation, session expiry policy, and broader endpoint coverage.

Still needed:

- real identity provider
- tenant isolation enforcement
- server-side session validation policy
- endpoint-level RBAC expansion
- API-key secret manager
- richer source-document raw access permissions
- role-based report download permissions
- role-based audit views

## Development Priority

P0:

1. Persist user activity logs. `Done`
2. Add login event endpoint. `Done`
3. Add API request middleware. `Done`
4. Add audit log query endpoint. `Done`
5. Document server storage boundaries. `Done`
6. Add source document download audit events. `Done`
7. Add report export/download audit events. `Done`
8. Add workspace and project frontend audit panels. `Done`
9. Add activity summary and JSON/CSV export endpoints. `Done`
10. Add persisted tenant, user, and session records. `Done`
11. Add first RBAC checks for audit/document/report sensitive endpoints. `Done`

P1:

1. Add production identity provider integration.
2. Expand RBAC checks to all evidence/document/report/project mutation endpoints.
3. Add run-scoped audit log panel.
4. Add raw evidence field-level access logs for account numbers and registry files.
5. Add audit retention policy.

P2:

1. Move runtime SQLite to PostgreSQL.
2. Move file archive to S3/MinIO/NAS.
3. Add security event alerting.
4. Add organization-level audit export.
5. Add immutable append-only audit table for regulated environments.
