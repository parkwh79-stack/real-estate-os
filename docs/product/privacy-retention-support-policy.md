# Privacy, Retention, Recovery, and Support Policy

Updated: 2026-07-22
Status: Approved operating architecture; statutory notices and contractual SLA require counsel/business approval
Scope: tenant data, project documents, evidence, calculations, reports, audit records, Auth profiles, and support records

Implementation note (2026-07-22): the general source-document repository now
implements tenant/project quarantine and active prefixes, SHA-256 verification,
a ClamAV INSTREAM scanner boundary, clean-only promotion, encrypted S3 writes,
and explicit Object Lock retention parameters. Hardened environments reject a
missing scanner, tenant, encryption mode, retention duration, or lock mode.
The local scanner bypass is visibly `skipped` and cannot make an upload
report-eligible. The exact production retention schedule, dedicated bucket/IAM,
versioning, legal hold, rejected-object audit ledger, signed downloads,
backup/restore and deletion receipts remain release gates.

## 1. Purpose and limitation

This document defines the technical and operating controls REALYTICS must
implement. It is not a substitute for a counsel-approved privacy notice, terms
of service, data-processing agreement, records-retention opinion, or customer
contract.

Exact statutory retention periods, cross-border transfer disclosures,
refund/cancellation language, and external SLA commitments are
`NEEDS-DOMAIN-DECISION` items.

## 2. Data classification

| Class | Examples | Default handling |
|---|---|---|
| Public/reference | Published laws, public land price, public transaction data | Source/license tracking; integrity/version controls |
| Tenant confidential | Project assumptions, feasibility, cash flow, PF terms, internal comments | Tenant isolation, encryption, least privilege |
| Restricted deal evidence | Contracts, registry, appraisal, business plans, legal opinions, approval materials | Strong access control, private object storage, download audit |
| Personal information | Names, email, phone, ownership/participant records, provider profile | Purpose limitation, minimization, access/deletion workflow |
| Credentials/secrets | OAuth secret, DB/S3 keys, webhook secrets | Secret manager only; never DB document fields or logs |
| Audit/security | Login, role, approval, export, security and incident events | Append-only/immutable target, restricted access |
| Derived/AI | Parsed candidates, embeddings, summaries, findings, prompts | Bound to source and tenant; no authority without review |

Classification is stored as metadata and controls storage, logging, access,
provider routing, retention, export, and deletion.

## 3. Data minimization

- Collect only fields required for authentication, tenant operations, project
  analysis, evidence, approval, billing, or support.
- Do not place document text, PNU-linked private facts, legal opinions, personal
  identifiers, access tokens, or secrets in telemetry.
- Object keys avoid names, email addresses, resident/business identifiers, and
  raw document titles where an opaque ID suffices.
- Search indexes and embeddings preserve tenant and source lineage and contain
  only the minimum required content.
- Support tickets use references/correlation IDs instead of copied deal data
  whenever possible.
- External AI/provider requests follow an allow-listed field policy and record
  the selected provider, purpose, residency decision, and retention setting.

## 4. Tenant isolation and access

- PostgreSQL RLS is enabled and forced on tenant-bearing records.
- Repositories repeat tenant/project predicates.
- Identity lookup uses verified `issuer + subject`; membership comes from the
  live PostgreSQL directory.
- S3/MinIO objects are private and use tenant/project-scoped opaque prefixes.
- Signed URLs are short-lived and issued only after current authorization and
  retention-state checks.
- Worker and legal roles use distinct least-privilege credentials.
- Tenant-wide export, deletion, legal hold, and high-impact access require
  dedicated permissions, step-up, and audit.
- Support and operations impersonation, if introduced, requires explicit
  approval, reason, expiry, banner, and immutable event; silent impersonation is
  prohibited.

## 5. Object lifecycle

Recommended lifecycle states:

```text
upload_received
  -> quarantined
  -> scan_passed | scan_failed
  -> active_version
  -> superseded
  -> archived
  -> deletion_pending
  -> deleted_with_receipt
```

An independent `legal_hold` flag may block archive expiration or deletion.

### 5.1 Upload quarantine

- Validate declared and detected type, size, archive structure, and hash.
- Write to a quarantine prefix with no parser or user download permission.
- Perform malware scanning and document-safety inspection.
- Promote by immutable copy/version operation and persist the result.
- Scan failure remains isolated; it is never silently treated as empty content.
- Re-scanning is required when scanner policy/version changes materially.

### 5.2 Active and superseded objects

- Metadata binds tenant, project, document version, storage version, SHA-256,
  size, type, uploader, scan result/version, and timestamps.
- A new document version does not overwrite the reviewed version.
- Approved snapshots and reports reference exact immutable object versions.
- Superseded objects remain readable only according to retention and audit
  policy.

### 5.3 Legal hold and Object Lock

- Legal hold is an explicit, authorized, audited state with reason, scope,
  issuer, effective time, and release decision.
- Where contractual or regulatory immutability is required, use S3 Object Lock
  or equivalent version retention on the dedicated protected bucket.
- Object Lock is not applied blindly to all temporary uploads because it can
  prevent lawful deletion; bucket/profile selection follows data class.

## 6. Retention schedule contract

Retention is policy-driven, versioned, and attached to the data class and
customer contract. No production code may hard-code a universal duration.

The approved schedule must define, for each class:

- business/legal purpose;
- retention start event;
- active retention period;
- archive period;
- legal-hold behavior;
- deletion/anonymization method;
- backup expiry behavior;
- responsible owner and approval version.

Required rows include:

- Auth profile and principal directory;
- tenant membership and invitation;
- source document and version;
- rejected/quarantined upload;
- evidence and approved snapshot;
- calculation and report;
- legal matter, waiver, opinion, citation and delegation;
- audit/security log;
- support ticket;
- billing contract, invoice, payment and tax evidence;
- telemetry and temporary processing artifact;
- backup and disaster-recovery copy.

Until counsel/business approval supplies durations, the system must expose the
policy as unapproved rather than inventing a retention period.

## 7. Tenant termination and deletion

Tenant termination is not immediate physical deletion.

```text
termination requested
  -> requester/authority verified
  -> legal hold and unpaid/dispute checks
  -> export window and scope confirmed
  -> tenant access restricted
  -> deletion plan generated
  -> primary DB/object deletion or anonymization
  -> derived index/cache cleanup
  -> backup expiry tracking
  -> deletion receipt
```

Rules:

- Payment failure alone does not delete data.
- A tenant administrator cannot delete records subject to legal hold or a
  stronger maker-checker policy.
- Deletion covers databases, object storage, caches, search/vector indexes,
  worker scratch space, and provider copies where contractually controllable.
- Immutable audit/legal records are retained or pseudonymized according to the
  approved legal basis; exceptions are explicit.
- The deletion receipt records scope, policy version, actor/checker, times,
  completed systems, exceptions, and backup-expiry date without reproducing
  deleted content.

## 8. Individual data requests

The operating workflow must support access, correction, export, restriction,
and deletion requests where applicable.

- Verify requester identity and authority before disclosure.
- Search by controlled identity references, not broad document-text exposure.
- Separate provider Auth data from REALYTICS tenant/project records.
- Notify the tenant controller when the contractual model requires it.
- Record request, scope, decision, deadline, exports, denials, and completion.
- Do not allow a personal request to remove business/audit records when a valid
  retention obligation applies; document the legal basis and response.

## 9. Backup and recovery

### 9.1 Required coverage

- PostgreSQL point-in-time/daily recovery appropriate to the selected plan.
- Versioned object storage and protected backup copies.
- Infrastructure/configuration/migration manifests.
- Secret recovery/rotation procedure without backing up plaintext secrets into
  application archives.
- Mapping needed to reconcile DB metadata with object versions.

### 9.2 Restore drills

Restore is not considered available until rehearsed. A drill verifies:

1. isolated recovery environment;
2. database restore to a declared recovery point;
3. object-version restore and hash verification;
4. migration/checksum reconciliation;
5. tenant/RLS and role isolation after restore;
6. outbox/job reconciliation without duplicate execution;
7. evidence locator and report retrieval;
8. documented RPO/RTO result and corrective actions.

External contractual RPO/RTO values require business approval. Internal drills
record measured values rather than claiming untested targets.

## 10. Logging, telemetry, and audit

- Security/audit events are separated from product analytics.
- Audit records identify actor principal, service principal, tenant, project,
  action, target/version, result, reason category, time, and correlation ID.
- Logs exclude JWTs, cookies, OAuth codes, secrets, full request bodies, source
  document text, and sensitive financial cells.
- Error telemetry uses allow-listed metadata and stable error codes.
- Product analytics measures workflow outcomes such as upload-to-approved-
  snapshot time, blocker resolution, PF run completion, and report delivery;
  it does not copy project content.
- Audit export is permissioned, rate-limited, watermarked where appropriate, and
  itself audited.

## 11. External providers and subprocessors

Maintain a reviewed inventory containing:

- provider/service and purpose;
- data classes and fields sent;
- region/residency and transfer basis;
- retention/training settings;
- credentials and owner;
- availability/fallback behavior;
- contract/DPA status;
- deletion/export mechanism;
- last security review.

Sensitive PF/legal content routes to approved providers only. Provider outage
does not authorize a less secure fallback. LLM output remains non-authoritative
and source-bound under `AGENTS.md`.

## 12. Support model

### 12.1 Channels and ownership

Before external pilot, name:

- customer support owner and monitored channel;
- security/privacy escalation owner;
- database/storage/worker on-call owner;
- domain escalation owner for PF, legal, and calculation questions;
- billing/contract owner when charging begins.

The UI must expose a support route with environment, tenant, project, run, and
correlation IDs that can be copied without exposing secrets.

### 12.2 Severity model

| Severity | Meaning | Examples |
|---|---|---|
| `SEV-1` | Security, cross-tenant exposure, widespread outage, data corruption | unauthorized disclosure, authority bypass, unrecoverable write failure |
| `SEV-2` | Major workflow unavailable or authoritative result suspect | PF runs failing, incorrect report-grade calculation risk, object retrieval outage |
| `SEV-3` | Degraded function with workaround | one connector unavailable, delayed worker queue |
| `SEV-4` | Question, minor defect, enhancement | copy issue, non-blocking visual defect |

Response and resolution commitments are contractual decisions. Internal targets
may be stricter, but unapproved targets must not be published as customer SLA.

### 12.3 Ticket safety

- Capture correlation IDs and controlled diagnostics first.
- Attach deal documents only through the protected object workflow.
- Do not request passwords, refresh tokens, database URLs, or provider secrets.
- Support access is time-bounded and least-privilege.
- Resolution identifies whether data, calculation, approval, or report artifacts
  require supersession/recalculation.

## 13. Incident response

Minimum lifecycle:

```text
detect -> classify -> contain -> preserve evidence -> eradicate
       -> recover -> validate tenant integrity -> notify/communicate
       -> post-incident actions
```

Runbooks must cover:

- credential/token compromise;
- cross-tenant authorization failure;
- malicious upload;
- PostgreSQL corruption/unavailability;
- S3/MinIO loss or policy error;
- worker duplication/stuck lease/DLQ growth;
- calculation/report integrity defect;
- OAuth/provider outage;
- external AI/legal/market provider data incident.

Notification timing and recipients follow applicable law and contract after
counsel approval. Operators record facts and timestamps without making
unsupported legal conclusions.

## 14. Pre-pilot acceptance gates

1. Counsel-approved privacy notice, terms, DPA/processing roles, retention
   schedule, and subprocessors list exist.
2. Tenant export, restriction, termination, legal hold, and deletion workflows
   have named approvers.
3. S3/MinIO private access, encryption, quarantine, malware scan, versioning,
   lifecycle, and signed download tests pass.
4. PostgreSQL and object restore drills pass with measured RPO/RTO.
5. Cross-tenant, disabled-user, expired-link, and support-access tests pass.
6. Logs and telemetry pass a secret/PII/document-content review.
7. Support channels, severity owners, incident contacts, and runbooks are active.
8. Audit, evidence locator, calculation version, and report retrieval survive a
   restore.

## 15. Related contracts

- `docs/architecture/architecture-boundary.md`
- `docs/architecture/postgres-runtime-authority.md`
- `docs/product/identity-tenant-access-contract.md`
- `docs/product/billing-entitlement-contract.md`
- `docs/product/server-storage-and-user-audit-log.md`
- `docs/product/realytics-sdd-fullstack-operating-standard.md`
