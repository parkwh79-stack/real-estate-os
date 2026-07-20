# ADR: Legal Intelligence Core

Status: Accepted — implementation in progress

Decision date: 2026-07-18

Applies to: API server, Legal Pod, Approval Readiness, glossary, PostgreSQL, worker, web

Related documents:

- [`docs/product/legal-pod-roadmap.md`](../product/legal-pod-roadmap.md)
- [`docs/integrations/korean-law-mcp.md`](../integrations/korean-law-mcp.md)
- [`docs/product/term-standard-database-operating-model.md`](../product/term-standard-database-operating-model.md)
- [`docs/product/agent-interaction-operating-model.md`](../product/agent-interaction-operating-model.md)

## 1. Decision summary

REALYTICS will introduce a **Legal Intelligence Core** under the existing Legal
Pod. The core owns legal-source ingestion, versioning, retrieval, citation
verification, provenance, and project linkage. The Legal Pod continues to own
preliminary project review, permit/risk findings, PF handoff, and human review.

The architecture is:

1. Official legal systems are the source providers.
2. PostgreSQL holds a verified, versioned operational snapshot and search
   index. It is a replica/read model, not the original legal authority.
3. The official direct API is the authoritative refresh and gap-fill path.
4. MCP is an optional tool/orchestration transport. MCP is not a source
   authority and is not required for normal operation.
5. Remote MCP is disabled by default and cannot receive confidential project
   data without an explicit data-classification decision.
6. Official text, machine extraction, AI summary, human annotation, and counsel
   opinion are separate content classes.
7. A verified citation does not make an AI conclusion a verified legal opinion.
   Final legal conclusions require the configured human/counsel approval.

This ADR supersedes any earlier implication that `mcp.connected=true` is proof
that legal evidence is complete, or that every legal reference has
`sourceType=korean_law_mcp`.

## 2. Context and problem statement

The current Legal Pod already performs deterministic preliminary checks,
persists project reviews, creates risk issues, and hands readiness information
to PF-Credit. It can also consume a caller-supplied `lawContext` or attempt a
Korean Law MCP/Law.go.kr transport.

That integration is useful for a pilot, but it is not a production legal
knowledge system:

- a transient connection result is mixed with evidence authority;
- a caller can supply a context that has not been server-attested;
- a generic Law.go.kr search cannot model every resource type, version, article,
  precedent, interpretation, or effective date;
- there is no verified local corpus with immutable fetch history;
- source authority, transport, content type, and verification state are not
  first-class independent fields;
- legacy `legal_reviews`/`permit_reviews` projections and singleton state do
  not themselves provide a normalized tenant/RLS authority boundary;
- an MCP outage can unnecessarily remove legal research capability;
- exact legal locators and source hashes are not consistently required before a
  finding can affect an approval gate.

REALYTICS is a real-estate development and PF operating system, not a legal
opinion generator. The system must make legal evidence easier to find, date,
compare, link, and review while remaining fail-closed when authority or
provenance is missing.

## 3. Goals

- Search statutes, regulations, administrative rules, local ordinances,
  precedents, decisions, interpretations, and approved tenant documents without
  making an external MCP a runtime dependency.
- Reproduce which legal source version was used for a project review at a
  specified `asOfDate`.
- Link every material legal claim to an exact source version and locator.
- Preserve immutable source fetches and normalization lineage.
- Reuse the institutional REALYTICS glossary and its human approval workflow.
- Keep global public legal material separate from tenant/project-confidential
  overlays.
- Support deterministic exact/lexical retrieval first, then optional semantic
  recall and relation expansion.
- Keep the existing Legal Pod and PF handoff contract while replacing weak MCP
  connection checks with evidence-completeness checks.
- Allow a self-hosted MCP sidecar for complex research without granting it
  source-authority status.
- Provide measurable freshness, retrieval, citation, isolation, and operational
  acceptance gates.

## 4. Non-goals

- REALYTICS does not issue a final legal opinion.
- The first release does not automatically determine that a precedent is
  overruled, followed, distinguished, or otherwise still controlling.
- The first release does not require OpenSearch, Neo4j, or a separate
  microservice per parser/tool.
- The local database does not become an independent official source merely
  because its records were fetched from an official endpoint.
- AI summaries never overwrite official text.
- A stale last-known-good snapshot never clears an approval blocker by itself.
- Tenant-uploaded contracts and counsel opinions are not added to the global
  public corpus.

## 5. Terminology and normative language

The terms **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

| Term | Meaning |
|---|---|
| Source authority | The organization or document owner from which the content derives |
| Transport | How REALYTICS obtained or read the content |
| Resource | A statute, regulation, rule, ordinance, precedent, interpretation, decision, notice, or tenant document |
| Resource version | An immutable normalized representation for a legal/effective period and observed source payload |
| Provision | An addressable article, paragraph, item, sub-item, addendum, annex, or form inside a resource version |
| Verified snapshot | A local record whose official identifier, payload hash, schema validation, and source lineage passed verification |
| Freshness policy | Resource-specific policy deciding whether a verified snapshot may answer a query without refresh |
| Legal claim | A proposition produced by a rule, agent, reviewer, or counsel that must cite evidence |
| Citation check | Verification that a citation resolves to the asserted resource version and locator |
| Counsel approval | Human legal approval that is independent from citation verification |

## 6. Authority, transport, content class, and verification

These dimensions MUST remain independent.

### 6.1 `sourceAuthority`

Runtime serialized values:

- `law_go_kr`
- `court`
- `supreme_court`
- `constitutional_court`
- `central_government`
- `local_government`
- `public_agency`
- `national_tax_service`
- `tenant_document`
- `human_counsel`
- `caller_supplied`
- `unknown`

`sourceAuthority` identifies origin/ownership, not the HTTP client or MCP tool.
A resource distributed through Law.go.kr MUST use the more specific issuing
authority when the resource family or official metadata supports it.
`law_go_kr` is not a blanket authority for precedent, Constitutional Court
decision, central-government material, or local ordinance.

### 6.2 `transport`

Runtime serialized values:

- `local_verified_snapshot`
- `law_go_kr_direct_api`
- `self_hosted_mcp`
- `remote_mcp`
- `tenant_object_storage`
- `caller_context`

The same official source can be observed through different transports.
Transport precedence MUST NOT be interpreted as legal-authority precedence.

### 6.3 `contentClass`

Initial canonical values:

- `OFFICIAL_TEXT`
- `OFFICIAL_METADATA`
- `MACHINE_EXTRACTION`
- `AI_SUMMARY`
- `HUMAN_ANNOTATION`
- `COUNSEL_OPINION`

The UI and API MUST expose this value. Official text and AI summary MUST be
stored in separate records/fields and rendered with different badges.

### 6.4 Verification states

Transport/source trust is serialized separately as:

- `untrusted_candidate`
- `source_attested`
- `verified_snapshot`
- `human_reviewed`

Citation, review and lifecycle tables additionally use domain states such as
`unknown`, `verified`, `rejected`, and `superseded`. Counsel approval remains a
separate human decision, not another name for a source trust level.

Verification is monotonic only within an immutable record. A later correction
creates a superseding record; it does not silently rewrite the audit history.

`citation_verified` means that the cited resource/version/provision exists and
matches the stored locator. It does **not** mean that the legal interpretation
or project conclusion is correct.

## 7. Target architecture

```mermaid
flowchart TD
    Q["Legal query or Legal Pod request"] --> N["Query normalizer"]
    N --> G["REALYTICS glossary and approved aliases"]
    N --> L["Verified local legal snapshot"]
    L --> F{"Coverage and freshness pass?"}
    F -- "yes" --> R["Deterministic retrieval pipeline"]
    F -- "no" --> O["Official direct API adapter"]
    O --> I["Immutable fetch, normalize, hash, verify"]
    I --> L
    O -- "temporarily unavailable" --> S["Stale result with explicit warning"]
    S --> B["Approval blocker remains"]
    R --> C["Claim and citation verifier"]
    C --> P["Preliminary Legal Pod finding"]
    P --> H["Human/counsel review"]
    H --> A["Approval Readiness / PF handoff"]
    M["Optional self-hosted MCP"] --> O
    M --> C
    X["Remote MCP, disabled by default"] -. "classified, approved request only" .-> M
```

### 7.1 Logical components

1. **Legal Query Normalizer**
   - normalizes legal identifiers, case numbers, article references, spacing,
     Hangul/number forms, and approved aliases;
   - resolves terms through the existing REALYTICS glossary;
   - does not auto-approve newly discovered aliases.

2. **Legal Source Provider Adapters**
   - map source-specific API contracts to an internal fetch result;
   - have separate adapters per official resource family/target;
   - never return a project conclusion.

3. **Legal Corpus Repository**
   - stores immutable fetch metadata, normalized resource versions,
     provisions, citations, and sync checkpoints;
   - exposes only source-attested versions as verified local snapshots.

4. **Legal Retrieval Service**
   - applies deterministic staged retrieval;
   - records query, ranking-policy version, result IDs, freshness, and hashes;
   - returns abstention/partial results when coverage is not sufficient.

5. **Citation Verifier**
   - resolves a claim citation to an immutable resource version and provision;
   - rejects generic home-page URLs and non-addressable locators as complete
     evidence;
   - records verification rules/version.

6. **Legal Intelligence Orchestrator**
   - coordinates local retrieval, official refresh, optional MCP research,
     retry/circuit behavior, and evidence packaging;
   - treats every externally supplied `lawContext` as an untrusted candidate.

7. **Legal Pod**
   - remains the project-facing preliminary review workflow;
   - creates findings, risks, permit checks, evidence requests, review
     decisions, and PF-Credit handoffs;
   - cannot claim final legal approval without a counsel decision.

## 8. Read and refresh path

The canonical read path is:

1. Normalize query and requested `asOfDate`.
2. Resolve exact official identifiers and approved aliases.
3. Query local source-attested versions.
4. Evaluate resource-specific coverage and freshness.
5. If coverage/freshness passes, retrieve from the local snapshot.
6. If missing, incomplete, or stale, call the relevant official direct API
   adapter.
7. Persist the raw fetch immutably, validate schema, normalize, hash, and record
   lineage.
8. Publish a new local version only after source attestation succeeds.
9. Re-run retrieval and citation checks.
10. Return evidence with `asOfDate`, freshness, source authority, transport,
    identifiers, version ID, exact locator, and hashes.

If the official API is temporarily unavailable:

- a verified last-known-good version MAY be displayed as `stale`;
- the system MUST show `lastVerifiedAt`, requested `asOfDate`, and failure
  reason;
- a stale result MUST NOT resolve a new blocker or clear an approval gate;
- retries apply only to idempotent reads and MUST use bounded exponential
  backoff with jitter;
- a circuit breaker prevents repeated saturation and emits an operational
  alert.

Freshness is a versioned policy by resource type and project lifecycle. A
single hard-coded TTL for every legal source is prohibited. Event/checkpoint
sync is preferred when the official source supports it.

## 9. MCP policy

### 9.1 Self-hosted MCP

A self-hosted MCP MAY be used as an optional sidecar for multi-step research,
tool composition, citation checking, or source-specific conveniences.

Before production use it MUST have:

- a pinned commit and lockfile;
- a container image pinned by digest;
- an internally controlled artifact registry;
- an SBOM and dependency/vulnerability scan;
- retained license notices;
- reproducible build and rollback instructions;
- contract tests against the normalized Legal Intelligence interfaces;
- outbound network allowlists;
- request/response size limits and timeouts;
- prompt-injection and untrusted-output handling.

Forking is required only when REALYTICS must maintain changes that cannot be
supplied upstream or safely wrapped. Pinning and internal artifact control are
required even without a fork.

### 9.2 Remote MCP

Remote MCP is `disabled` by default in every production profile.

It MUST NOT receive:

- tenant-uploaded contracts or counsel opinions;
- project addresses/PNU lists unless explicitly classified and approved;
- borrower/sponsor/participant identities;
- financing terms, covenant details, or proprietary feasibility data;
- secrets, access tokens, raw audit logs, or unrestricted source payloads.

Enabling a remote MCP requires an approved data-flow record, purpose, fields,
retention policy, provider agreement, egress allowlist, and audit event. Remote
MCP output remains untrusted until official source attestation succeeds.

### 9.3 MCP availability

The following scenario MUST pass:

> Disable every MCP endpoint, keep the official direct API and local snapshot
> available, then run legal search, citation verification, Legal Pod review, and
> Approval Readiness without functional loss other than MCP-specific optional
> research.

MCP connection status MUST NOT be used as an approval criterion.

## 10. Persistence model

The database is divided into a global official corpus and tenant/project
overlay. They MUST NOT be combined by making `tenant_id` nullable in a single
table.

### 10.1 Global official corpus

Only the source-sync role may write published Corpus records. Application
runtime roles have read-only access to published, source-attested versions.
The legacy global review ledgers have no project-review write grant.

| Table | Purpose |
|---|---|
| `legal_source_providers` | Provider configuration identity and adapter contract version |
| `legal_resources` | Stable identity for a statute, rule, ordinance, precedent, interpretation, or decision |
| `legal_resource_identifiers` | Official IDs, case numbers, names, aliases, issuing authority |
| `legal_resource_versions` | Immutable normalized versions and legal/system time |
| `legal_provisions` | Article/paragraph/item/addendum/annex addressable nodes |
| `legal_source_fetches` | Immutable raw-object pointer, request metadata, payload hash, schema status |
| `legal_citations` | Source-to-source citation edges with exact locators |
| `legal_relation_assertions` | Versioned, evidence-backed relation candidates |
| `legal_sync_runs` | Run status, counts, adapter/rules version, checksum |
| `legal_sync_checkpoints` | Resumable provider/resource checkpoints |
| `legal_citation_review_events` | Legacy/institutional review ledger retained for compatibility; project review does not write it |
| `legal_relation_review_events` | Legacy/institutional relation-review ledger retained for compatibility; project review does not write it |

Raw source payloads SHOULD be stored as immutable objects. The database stores
the object key/version, SHA-256, byte size, content type, source endpoint
identity, adapter/schema version, and retention metadata. Large mutable raw
JSON duplicated across normalized rows is prohibited.

### 10.2 Tenant/project overlay

All overlay tables MUST include `tenant_id`. Project-scoped rows MUST have a
composite foreign key to `(project_id, tenant_id)`.

| Table | Purpose |
|---|---|
| `project_legal_matters` | Project/matter scope and lifecycle |
| `project_legal_resource_links` | Project link to exact global resource version/provision |
| `legal_query_runs` | Tenant query, normalized form, policy version, results, hashes |
| `legal_findings` | Preliminary deterministic/AI/human findings |
| `legal_evidence_snapshots` | Sealed evidence set used by a finding or gate |
| `legal_review_decisions` | Append-only approve/waive/needs-revision/reject decisions |
| `legal_approval_delegations` | Versioned, time-bounded approval delegation with explicit scope |
| `legal_waivers` | Time-bounded authorized exception with reason and evidence |
| `counsel_opinions` | Tenant document metadata and approval, never globalized |
| `legal_analysis_versions` | AI/deterministic analysis version, input/content hashes, and independent review state |
| `project_legal_annotations` | Project-specific notes and classifications |
| `tenant_legal_term_aliases` | Tenant-scoped legal alias candidates and approved/rejected versions |
| `tenant_legal_term_alias_review_events` | Append-only tenant alias review history |
| `project_legal_citation_review_versions` / `project_legal_citation_review_events` | Tenant/project-scoped, branch-free citation review versions and exact review events |
| `project_legal_relation_review_versions` / `project_legal_relation_review_events` | Tenant/project-scoped, branch-free interpretive-relation review versions and exact review events |

Sensitive tenant document bodies use the project document/object-storage
controls. The legal corpus may store a tenant-document reference and hash, but
must not copy it into the public corpus.

Provider operations are stored separately from both the global corpus and
tenant overlay. `legal_provider_quarantine`, `legal_provider_dead_letters`,
and `legal_provider_metrics` are global, privacy-safe operational metadata:
they contain no tenant payload, query text, response body, credential, or
credential-bearing URL. Replay authority is tenant-native.
`legal_provider_replay_objects`, `legal_provider_replay_approval_events`,
`legal_provider_replay_requests`, and `legal_provider_replay_events` use forced
RLS. A trusted provider/object worker must append a catalog receipt containing
the exact dead-letter, provider, object locator, object-store version ID,
SHA-256, Object Lock mode, retention deadline, and receipt hash before the
dead-letter is replayable. The public approval API accepts only that catalog
ID. PostgreSQL revalidates the copied binding on approval, request, and
`requested -> running` transition, including a deferred commit-time check.
Event visibility is inherited from the parent request, so changing
`realytics.tenant_id` cannot reveal another tenant's object receipt, approval,
request, or lifecycle events.

### 10.3 Bitemporal and version fields

`legal_resource_versions` MUST distinguish at least:

- `promulgated_at` or official publication date, where applicable;
- `valid_from` and `valid_to` for legal/effective time;
- `announced_effective_at` for a known future effective date;
- `observed_at` for the first system observation;
- `retrieved_at` for the concrete source fetch;
- `verified_at` for source attestation;
- `supersedes_version_id`;
- `source_payload_sha256`;
- `normalization_schema_version`;
- `normalization_rules_version`.

Corrections create a superseding version. They do not update a prior immutable
source version in place.

For a query with `asOfDate`, legal validity is evaluated against legal time,
while reproducibility/audit uses observed/retrieved system time. Both are
returned to the caller.

### 10.4 Relation assertions and precedent treatment

`OVERRULES`, `FOLLOWS`, `DISTINGUISHES`, `LIMITS`, and similar relations MUST
be stored as assertions, not unquestioned scalar truth.

Each assertion contains:

- source and target resource/version IDs;
- assertion type;
- exact supporting locator/excerpt hash;
- source authority and transport;
- algorithm/rule/model version;
- confidence;
- `asOfDate`;
- `humanReviewStatus`;
- reviewer and review time;
- superseding assertion ID.

The default subsequent-treatment status is `UNKNOWN`. Machine-inferred
treatment may prioritize research but cannot independently clear an approval
gate.

`machine_inferred + verified + human_review_pending` is invalid. A citation or
interpretive relation becomes verified only through an append-only review that
records the reviewer, review time, exact excerpt and excerpt hash. Re-review
creates a superseding tenant/project review version and its exact review event.
It never creates a project-specific successor in the institution-wide
`legal_citations` or `legal_relation_assertions` Corpus tables. The overlay
enforces project evidence scope, maker-checker separation, server-time and
source-time causality, exact delegation scope, and branch-free version/event
chains under forced RLS.

## 11. Glossary integration

The Legal Intelligence Core reuses the existing REALYTICS controlled
vocabulary:

- `glossary_terms` remains canonical identity;
- approved aliases remain the exact alias resolution layer;
- discovered unknown terms enter `glossary_term_candidates`;
- merge/new-term/free-text/reject remains a human decision;
- tenant aliases do not silently become global aliases.

Legal-specific information is added through extension tables rather than a
second competing term registry:

- `legal_term_definitions`
- `legal_term_source_links`
- `legal_term_provision_links`
- `legal_term_relations`
- `tenant_legal_term_aliases`

A definition is versioned and linked to its defining source/provision. An
official definition, industry/PF definition, user-friendly explanation, and
tenant usage are distinct definition types. Discovery never implies approval.

## 12. Retrieval and ranking policy

Search is a staged, lexicographic pipeline. Exact identifiers cannot be
demoted by a vector score.

1. **Identifier stage**
   - official law/resource ID;
   - case number;
   - canonical article/paragraph/item locator;
   - exact official name plus requested date.

2. **Approved alias stage**
   - canonical glossary term;
   - approved official/practical/user alias;
   - normalized Hangul/number/spacing form.

3. **Lexical stage**
   - PostgreSQL full-text search;
   - resource title/provision heading/body fields;
   - version/as-of filters applied before presentation.

4. **Similarity stage**
   - `pg_trgm` for typo, spacing, and close-form recovery;
   - it must not replace exact case-number or article matching.

5. **Semantic recall stage**
   - optional pgvector candidate expansion;
   - enabled only after golden-corpus evaluation;
   - source authority, version, and validity filters remain deterministic.

6. **Relation expansion**
   - explicit citations and approved/qualified relation assertions;
   - unreviewed inferred edges are visibly marked.

Each result includes:

- `retrievalStage`;
- `rankingPolicyVersion`;
- normalized query and `asOfDate`;
- resource/version/provision identifiers;
- source authority and transport;
- verification/freshness states;
- exact locator and source URL, when available;
- source and normalized-content hashes;
- reason/explanation for the match.

Raw scores from different stages MUST NOT be added without calibrated
normalization. Search evaluation determines weights inside a stage. Exact
identifier precedence is a contract, not an adjustable weight.

OpenSearch/Nori is deferred until PostgreSQL measurements show a documented
limit in corpus size, query latency, morphology/recall, or operational
requirements. A separate ADR is required before adoption. Neo4j or another
graph database likewise requires demonstrated query/scale need; initial
relations remain in PostgreSQL.

## 13. API and event contracts

### 13.1 Canonical query input

```json
{
  "tenantId": "tenant-id",
  "projectId": "optional-project-id",
  "query": "건축법 제11조",
  "asOfDate": "2026-07-18",
  "resourceTypes": ["STATUTE", "PRECEDENT", "INTERPRETATION"],
  "jurisdiction": "KR",
  "purpose": "PRELIMINARY_PROJECT_REVIEW"
}
```

`tenantId` is derived from authenticated runtime context, not trusted from a
caller body. Project access is authorized before retrieval history or tenant
overlays are read.

### 13.2 Canonical legal reference

```json
{
  "resourceId": "stable-resource-id",
  "resourceVersionId": "immutable-version-id",
  "provisionId": "optional-provision-id",
  "resourceType": "statute",
  "sourceAuthority": "central_government",
  "transport": "local_verified_snapshot",
  "contentClass": "official_text",
  "verificationStatus": "citation_verified",
  "freshnessStatus": "fresh",
  "officialIdentifiers": {},
  "citation": "건축법 제11조",
  "requestedAsOfDate": "2026-07-18",
  "verifiedEffectiveDate": "2026-01-01",
  "validFrom": "2026-01-01",
  "validTo": null,
  "observedAt": "2026-07-18T01:00:00Z",
  "verifiedAt": "2026-07-18T01:00:02Z",
  "sourceUrl": "exact-official-resource-url",
  "locator": {},
  "sourcePayloadSha256": "sha256",
  "normalizedContentSha256": "sha256"
}
```

`sourceAuthority` identifies the issuing body, not the delivery system.
Law.go.kr is normally `transport=law_go_kr_direct_api`; precedent,
constitutional-decision, central-government and local-ordinance results map to
their underlying court or government authority. Direct list search records
`requestedAsOfDate` and `observedAt`, but leaves `verifiedEffectiveDate` and
`verifiedAt` empty until a detail/history-backed verification path proves
historical applicability.

### 13.3 Untrusted legacy context

`realytics-law-context-v1` remains temporarily accepted for compatibility, but
it is ingested as `UNTRUSTED_CANDIDATE`. Caller-provided fields such as
`connected`, `verified`, and `citationVerification.status` are informational
only.

The server MUST:

1. strip or ignore caller authority assertions;
2. resolve official identifiers;
3. fetch or locate an official source-attested version;
4. re-run citation verification;
5. record candidate-to-verified lineage;
6. reject or abstain when verification cannot complete.

### 13.4 Execution and approval status

Legal sync/query/review execution status and approval status are separate:

- execution: `pending`, `running`, `completed`, `failed`, `cancelled`;
- approval: `not_requested`, `in_review`, `approved`, `needs_revision`,
  `rejected`, `waived`.

`completed` execution does not imply legal approval.

## 14. Evidence and Approval Readiness

Every material Legal Pod claim MUST be backed by a sealed evidence snapshot.
An evidence snapshot contains exact resource version/provision links, source
hashes, freshness state, validation results, and snapshot hash.

`recordLocator` and `evidenceLocator` are different contracts.
`recordLocator` locates the internal Legal Pod/review record. Only
`evidenceLocator`, which identifies the external source version, provision,
page/table/row/cell or equivalent exact basis, is eligible for a legal evidence
gate. A legal review cannot satisfy its own evidence requirement by pointing
back to itself.

The following are not sufficient evidence:

- `mcp.connected=true`;
- a generic Law.go.kr home-page URL;
- a Legal Pod review record citing itself;
- an AI-generated article number or effective date;
- a source title without version/provision identity;
- caller-supplied `verified=true`;
- a stale snapshot without an approved policy and required review.

A legal approval gate may pass only when:

- normalized PostgreSQL confirms the tenant/project/review/snapshot scope;
- the presented snapshot ID, version and SHA-256 match the stored snapshot;
- the snapshot has not been superseded and remains effective/fresh;
- every evidence item resolves to the same corpus provider, fetch, resource
  version and provision lineage stored in PostgreSQL;
- required claim categories are complete;
- every blocking claim has exact verified evidence;
- requested `asOfDate` is covered;
- no required source is stale beyond policy;
- no unresolved citation mismatch or source conflict remains;
- required tenant documents are present and approved;
- required counsel/human review is approved;
- all waivers are authorized, scoped, time-bounded, and auditable.

The gate always re-reads those facts and the latest decision directly from the
normalized PostgreSQL tables. A missing/unreadable normalized store fails
closed; no environment mode and no legacy JSON boolean/count can clear the
gate. Legacy projections remain display and migration inputs only.

Approval Gate final approve/reject/conditional-waive decisions require
`legal_counsel_approver` or an explicitly delegated `external_counsel`.
The snapshot creator cannot make the final decision. External counsel must
reference a same-tenant/project, active, unexpired, unsuperseded delegation
whose grantee, role and scope match the decision. `tenant_admin` may manage
tenant workflow or propose review/waiver work but cannot be the final legal
approver.

Fallback assumptions remain `analysisOnly=true` and
`reportEligible=false`. A citation verification pass cannot promote an AI
conclusion to counsel approval.

## 15. Security, RBAC, and RLS

### 15.1 Roles

Application user roles:

- `legal_reviewer`: append preliminary review decisions within authorized
  projects;
- `legal_counsel_approver`: append final counsel approval/rejection;
- `external_counsel`: exercise only an explicit, valid delegation;
- `compliance_officer`: operate the provider DLQ/replay workflow;
- `tenant_admin`: tenant administration and proposals, not final legal
  approval;
- `admin`: provider/freshness/ranking policy and controlled operations.

Minimum PostgreSQL principals:

- `realytics_runtime`: serve verified corpus reads and append authorized
  tenant overlays;
- `realytics_legal_sync`: append official fetch/sync/corpus records;
- `realytics_legal_review`: read global Corpus facts and required evidence
  lineage, and append only tenant-native legal-governance and Citator review
  versions/events; it has no global Corpus write privilege;
- `realytics_legal_operations`: read and append global
  quarantine/DLQ/metric metadata, and read/append tenant-RLS replay
  approval/request/event streams;
- migration owner: schema changes only, never used by application runtime.

Human Citator review requires
`REALYTICS_LEGAL_REVIEW_DATABASE_URL`; provider DLQ review/replay requires
`REALYTICS_LEGAL_OPERATIONS_DATABASE_URL`. Neither may fall back to the serving
runtime or legal-sync DSN. Role names are configured independently through
`REALYTICS_LEGAL_REVIEW_DATABASE_ROLE` and
`REALYTICS_LEGAL_OPERATIONS_DATABASE_ROLE`.

No application database role may own tables, inherit an elevated role, create
schema objects, or bypass tenant RLS. Review and operations roles have no
`UPDATE` or `DELETE`; all decisions and replay transitions are appended.

The HTTP boundary additionally requires an authenticated principal explicitly
typed as a human and derives reviewer identity/role from that principal rather
than accepting those fields from the request. This is not yet cryptographic
database-level actor binding. A compromised
`REALYTICS_LEGAL_REVIEW_DATABASE_URL` credential could still attempt direct SQL
using a permitted reviewer ID/role string. Before production, legal-review
writes MUST be brokered through an authenticated service or bound to signed,
transaction-local actor claims that PostgreSQL triggers verify against managed
role membership. Credential issuance, membership, connection identity, and
revocation evidence remain a P1 security hardening item.

### 15.2 RLS

- Every tenant overlay table has RLS enabled and forced.
- Policies use the authenticated tenant session context and fail closed when it
  is missing.
- Global provider quarantine/DLQ/metric metadata intentionally has no tenant
  identity. Replay approval/request rows are tenant/project native, and replay
  event policies resolve tenant identity through their parent request.
- Project rows use composite `(project_id, tenant_id)` foreign keys.
- Tenant A cannot observe Tenant B query text, annotations, documents,
  evidence, decisions, or identifiers through joins, errors, counts, search
  facets, or timing-sensitive endpoints.
- Global corpus reads expose only published/source-attested versions.

### 15.3 Threat controls

| Threat | Required control |
|---|---|
| Caller fabricates `lawContext` | Treat as candidate; server re-attests source |
| Prompt injection in legal text/MCP response | Treat source as data; schema validate; no tool instructions from content |
| Secret leakage in URL/error/audit | Central redaction; no raw credential-bearing URL logging |
| SSRF/host substitution | Fixed provider allowlist, TLS validation, bounded redirects |
| Cross-tenant search leakage | Forced RLS, authorized joins, negative tests |
| MCP exfiltration | Remote disabled, field-level data policy, egress allowlist |
| Source schema drift | Quarantine fetch; adapter contract alarm; do not publish |
| AI overwrites official source | Separate content class/table and immutable official version |
| Stale legal data clears a gate | Freshness-aware fail-closed gate |
| Duplicate/replayed source payload | Idempotency and payload hash uniqueness |
| Reviewer approves own snapshot | Python check plus deferred PostgreSQL maker-checker trigger |
| External counsel self-asserts authority | Explicit scoped, time-bounded delegation revalidated at decision time |
| Internal review record masquerades as evidence | Separate `recordLocator`/`evidenceLocator`; gate consumes evidence locator only |
| DLQ exposes raw query or credential URL | Privacy-safe metadata, restricted object locator, exact hash, dedicated operations role |

Official APIs that require credentials in a query parameter MUST have the full
request URL redacted from application logs, exception messages, traces, and
audit payloads. Secrets are stored in the production secret manager and are
never committed.

## 16. Observability and audit

Required metrics:

- query volume and latency by retrieval stage;
- exact-match success and abstention rate;
- local snapshot hit rate;
- freshness pass/fail by resource type;
- official adapter success, timeout, rate-limit, and schema-drift rates;
- sync lag and checkpoint age;
- citation pass/mismatch/unresolvable counts;
- stale last-known-good usage;
- MCP optional-tool availability and failure, separately from source health;
- RLS denial and authorization failure counts;
- human/counsel review turnaround;
- approval blockers by evidence cause.

Every query/sync/review event records:

- actor/service identity and tenant/project context;
- request ID, trace ID, idempotency key;
- policy, adapter, schema, ranking, rule, prompt/model versions where relevant;
- input/output hashes without exposing secrets or unauthorized document bodies;
- source authority, transport, resource/version identifiers;
- execution and approval status;
- created/occurred/observed/verified timestamps.

The provider boundary implements provider-specific token buckets, bounded
retry with jitter, `Retry-After` handling, circuit breaking, and explicit
partial-success status. Schema drift is quarantined and retry exhaustion is
dead-lettered. PostgreSQL sinks persist only privacy-safe hashes/codes/metrics.
The restricted replay API records `requested → running → completed|failed`
events against an approved immutable object reference; deploying an actual
object reader/executor, scheduler and alert exporter remains an operational
task.

## 17. Implementation phases

### 17.0 구현 현황 — 2026-07-20

이 표는 “코드가 존재함”과 “운영 환경에서 검증됨”을 구분한다. 아래의
운영 미완료 항목이 남아 있으므로 이 ADR의 전체 상태는 여전히
`implementation in progress`이며 production-ready로 판정하지 않는다.

| 영역 | 현재 판정 | 구현·검증 근거 |
|---|---|---|
| 권위/전송/검증 계약 | 코드 완료 | source authority, transport, trust, freshness, data classification을 독립 모델로 분리했다. Direct API 목록과 MCP·caller 결과는 candidate이며 로컬 검증 스냅샷·human-reviewed 근거만 authoritative evidence가 된다. |
| 저수준 `lawContext` 경계 | 코드 완료 | 일반 mapping은 `connected=true`, `source=korean-law-mcp`, `verified=true`를 보내도 항상 untrusted candidate다. in-process factory가 만든 immutable `ServerIssuedLawContext`만 verified reference를 전달할 수 있고 `mcp.connected`는 transport 상태로만 남는다. |
| Direct API 시점·권위 의미 | 코드 완료 / live provider 미검증 | `requestedAsOfDate`, `observedAt`, `verifiedEffectiveDate`, `verifiedAt`을 분리했다. Law.go.kr를 transport/aggregator로 취급하고 판례·헌재결정·중앙정부·자치법규의 실제 발행기관 계열을 authority로 매핑한다. 목록 응답은 historical applicability를 주장하지 않는다. |
| 기밀정보 egress | 코드 완료 | `PROJECT_CONFIDENTIAL`·`LEGALLY_PRIVILEGED` 질의는 공식 외부 API와 remote MCP 호출을 0회로 차단하고 진단을 남긴다. 프로젝트 주소·PNU는 법령 일반질의에 포함하지 않는다. 전역 법령검색만 `PUBLIC`으로 명시한다. |
| 공식 corpus 저장 계약 | 코드 완료 / live 미검증 | 2xx 응답, 원문 SHA, 공식 authority/identifier, parser schema, transport별 verification basis와 verifier를 확인한 뒤에만 게시한다. provider/scope checkpoint CAS, append-only fenced lease, 다중 record/page 원자 commit, replay, terminal page coordinator를 구현했다. 완료 시 checkpoint 연속성·페이지 hash·fetch/run·version manifest를 Python과 PostgreSQL deferred trigger가 재검증한 `legal_corpus_scope_completion_attestations`만 생성한다. |
| Law.go.kr boundary/page source | 기반 코드 완료 / 실제 initial·history 미완료 | 법령·조문·판례·법령해석·행정규칙·자치법규·헌재결정·법령용어의 list/detail/history 외부 응답 계약과 list/detail page source, pagination/checkpoint, partial-page fail-closed를 구현했다. 저장소 fixture는 계약 형태 검증용이며 실제 공식 응답 bytes를 보존한 승인 fixture, resource별 history HTTP acquisition/semantic normalizer, operating OC와 scheduler/backfill은 남아 있다. |
| 원본 object storage | 코드 완료 / live 미검증 | 전용 S3/MinIO adapter가 versioning과 Object Lock을 먼저 확인하고, content-addressed key·`If-None-Match`·SHA-256 checksum·version ID·보존기한을 검증한다. inventory cursor, delete-marker 거부, orphan 보존 및 append-only reconciliation worker를 구현했으며 삭제 API는 의도적으로 없다. 실제 bucket/IAM과 복구 훈련은 남아 있다. |
| PostgreSQL schema/RLS | 정적 구현·wrapper 동기화 완료 / live 미검증 | `0008` global corpus, `0009` Citator·maker-checker·tenant-native governance·provider operations, `0010` 8-target official-corpus coverage registry/contract/completion chain을 분리했다. tenant overlay forced RLS, append-only trigger와 runtime/readiness 권한 계약이 있고 Supabase wrapper/checksum도 `render_supabase_migrations.py --check`를 통과한다. 실제 `db push --dry-run`, PostgreSQL/PostGIS 적용, cross-tenant·pooling·다중 worker 시험은 남아 있다. |
| resource identity·유효시점 계보 | 코드 완료 / live 미검증 | 공식 resource identity를 provider revision과 분리하고 provider별 식별자는 crosswalk로 보존한다. predecessor, overlap, undocumented gap, same-window source correction을 fail-closed 검증하고 open-ended predecessor의 파생 종료일을 append-only lineage로 남긴다. runtime 검색·상세·증거 조회는 실제 version provider revision과 최신 source correction을 사용한다. |
| 로컬 검색 | 코드 완료 / live corpus 미적재 | exact identifier/name/approved alias → PostgreSQL FTS/`pg_trgm` 순위를 적용하고 exact provision 본문·hash·locator를 함께 반환한다. 모든 최신 active 공식 provider에 대해 DB가 검증한 full-corpus completion attestation이 있어야 coverage가 complete다. freshness는 attestation 작성 시간이 아니라 terminal checkpoint 게시 시각으로 계산한다. |
| 동기화 권한 경계 | 코드 완료 / live 미검증 | `REALYTICS_LEGAL_SYNC_DATABASE_URL`이 없으면 runtime/migration DSN으로 폴백하지 않는다. `realytics_legal_sync`는 coverage registration 3개 테이블에는 `SELECT`만, corpus publish ledger에는 `SELECT, INSERT`만 가지며 `UPDATE`, `DELETE`, DDL, schema `CREATE`, superuser, inheritance, `BYPASSRLS`를 금지한다. Registry+8개 contract 등록은 table owner인 migration role 전용 CLI에서만 원자적으로 수행한다. |
| Citation snapshot | 코드 완료 | exact provision ID, fetch/version lineage, normalized hash, verified time, locator, 공식 조문 본문과 excerpt의 일치, DB에서 파생한 freshness/open issue, citation coverage와 required-claim coverage를 원자적으로 봉인한다. 일부 근거만 봉인해 수를 부풀리는 snapshot은 gate-eligible이 될 수 없다. |
| Approval Readiness 정규화 결합 | 코드 완료 / live 미검증 | Gate는 항상 tenant/project/review/snapshot ID·version·hash, supersession/effective time, claim/citation coverage, 실제 corpus fetch/version/provision lineage, 최신 decision과 delegation을 normalized PostgreSQL에서 매번 재조회한다. DB 또는 계약 누락은 fail-closed이며 legacy JSON projection은 승인 권위가 아니다. |
| maker-checker·delegation | 코드 완료 / live 미검증 | snapshot과 required-claim-set의 maker/checker를 분리하고, Approval Gate·waiver 승인과 terminal cure·counsel opinion 승인·report-eligible analysis 검토·Citator 최종 검토를 `legal_counsel_approver` 또는 해당 scope의 유효한 delegation을 가진 `external_counsel`로 제한한다. delegation은 tenant/project/grantee/role/scope/기간/version/결정시각을 묶고 Python과 PostgreSQL trigger가 재검증한다. 실제 DB의 동시 revoke/decision 경합과 actor credential binding은 남아 있다. |
| Citator 검토 무결성 | DB·repository·API·Web 코드 완료 / live 미검증 | global citation/relation 후보는 read-only이고 사람 판단은 forced-RLS tenant/project overlay의 단일 successor version/event로 기록한다. machine inferred의 자동 verified 금지, 해석적 relation의 human review, maker-checker, exact excerpt/hash, server-derived actor/role, external-counsel delegation을 구현했다. Web은 queue, citation/relation 검토, delegation 선택과 브라우저 excerpt SHA-256 계산을 제공한다. 전용 `REALYTICS_LEGAL_REVIEW_DATABASE_URL` 없이는 API가 fail-closed한다. |
| tenant-native 법률 overlay | 코드 완료 / live 미검증 | project matter, waiver lifecycle/cure, counsel opinion, AI/deterministic analysis version, annotation, tenant alias, delegation을 typed append-only table/API와 workspace read model로 분리했다. opinion/analysis는 유일한 v1 root, immediate same-scope predecessor, version+1, 단일 successor 및 sealed content/input lineage를 DB에서도 강제한다. Citator·tenant alias review action은 Web에 연결됐지만 나머지 governance 작성/승인 화면은 API-first다. legacy `legal_reviews`/`permit_reviews`는 호환 projection으로 남아 있으며 backfill·dual-read parity는 live release 작업이다. |
| locator 분리 | 코드 완료 | Legal Pod 내부 저장 위치는 `recordLocator`, 외부 원문 근거는 `evidenceLocator`로 분리했다. Gate는 법률 레코드의 self locator를 evidence로 인정하지 않는다. |
| Provider resilience·DLQ/replay | 코드 완료 / worker 배포 미완료 | provider별 token bucket, bounded jitter retry, `Retry-After`, circuit breaker, partial status, privacy-safe PostgreSQL quarantine/DLQ/metrics와 admin/compliance 전용 replay request/event API를 구현했다. trusted worker의 version/hash/retention object receipt가 없으면 non-replayable이고, 승인 API는 catalog ID만 받으며 replay 시작 전에 DB가 exact binding을 재검증한다. 전용 operations DSN 없이는 fail-closed한다. 실제 executor/object reader·IAM 검증, scheduler, metric exporter/alert와 장애훈련은 남아 있다. |
| Legal Pod·Permit Gate | 코드 완료 | caller `lawContext`는 candidate로만 취급한다. 법률 상태, exact verified citation, 전체 쟁점 coverage, non-stale/non-conflict snapshot, 법무 human approval을 독립 gate로 검사한다. Permit gate는 승인된 parser output과 정확한 원문 locator가 없거나 parser가 unavailable이면 fail-closed다. |
| Web review surface | 핵심 trust/검토 action 코드 완료 / live 미검증 | 검색 결과를 공식 검색 후보·검증 corpus snapshot·사람 검토 완료로 각각 집계하고 MCP 연결을 “승인 근거 아님”으로 표시한다. Chief Deal Room은 normalized PostgreSQL readiness가 읽히고 완전한 경우에만 법무 승인 action을 활성화하며 legacy Legal Pod boolean을 사용하지 않는다. governance workspace는 tenant-native metadata와 Citator queue/action, tenant alias 검토를 제공한다. waiver·opinion·analysis·delegation의 전체 작성 UX는 API-first 후속 범위다. |
| 자동 회귀 | 변경 범위 녹색 / live·전역 장시간 스위트 미완료 | 2026-07-20 기준 법률·Law.go.kr·법률 전용 role 회귀 632 passed, 연관 API/권한/Supabase 회귀 119 passed·15 live-PostgreSQL skipped, Web trust/Citator/governance Playwright 36 passed, TypeScript·ESLint 통과다. 전체 1,846-test 단일 프로세스 실행은 10분 로컬 상한에서 완료되지 않았으므로 전역 완료 증거로 주장하지 않는다. |
| Golden corpus·법무 기준 | 평가기 코드 완료 / 실제 corpus 미완료 | versioned manifest/JSONL, label SHA-256, maker-checker 승인, exact lineage locator, claim precision/recall, abstention precision/recall/accuracy, locator accuracy와 fail-closed CLI를 구현했다. 실제 법령·판례·해석·인허가/PF corpus와 counsel-approved threshold/labels/predictions는 외부 운영 산출물로 남아 있다. |
| 운영·재해복구 | 부분 코드 완료 / live 미완료 | quarantine/DLQ/replay 계약은 구현됐지만 실제 provider 자격증명, secret manager, initial/backfill scheduler, metric exporter·알람, backup/restore, schema-drift replay, outage/rollback 훈련이 필요하다. |

이번 구현의 fail-closed 원칙은 다음과 같다.

1. 외부에서 받은 법률 텍스트나 MCP 결과는 자동으로 권위 근거가 되지 않는다.
2. 프로젝트 기밀 질의는 외부 provider로 전송하지 않는다.
3. 정확한 조문·버전·fetch 계보·원문 excerpt가 없으면 snapshot을 승인 근거로
   승격하지 않는다.
4. 검색된 인용 일부만으로 필수 법률 쟁점 전체를 충족한 것처럼 표시하지 않는다.
5. citation 검증과 법무 human/counsel 승인은 서로 대체할 수 없다.
6. 실제 DB·object store·provider·복구 훈련이 끝나기 전에는
   production-ready로 표시하지 않는다.

#### 17.0.1 구현 후에도 남은 구조적 작업

구조적 코드 잔여와 운영 검증을 구분해 관리한다.

1. Law.go.kr 각 resource family의 typed list/detail/history boundary는
   구현됐지만, 실제 공식 응답 bytes를 보존한 승인 fixture, history
   acquisition/semantic normalizer, 운영 OC·이용조건·호출량 기준을 적용한
   initial/backfill scheduler가 필요하다. 범용 search-list candidate를
   검증 corpus로 곧바로 승격해서는 안 된다.
2. provider별 rate limit·Retry-After·partial 상태와 영속
   quarantine/DLQ/replay/metrics 계약은 구현됐다. 이를 실제 worker의
   object reader/executor, scheduler, metric exporter/alert, 장애 대응
   runbook에 연결하고 replay 훈련을 수행해야 한다.
3. 실제 PostgreSQL/PostGIS/Supabase에 `0008`, `0009`, `0010`, migration/runtime/
   legal-sync/legal-review/legal-operations principal을 적용하고
   cross-tenant RLS, session pooling, maker-checker, delegation,
   stale checkpoint 경합, lease fencing, 다중 worker crash/retry를
   검증해야 한다.
4. 실제 S3/MinIO bucket에서 versioning, Object Lock, IAM, inventory,
   backup/restore와 orphan 보존 훈련을 수행해야 한다.
5. 평가기만으로는 release gate가 닫히지 않는다. 실제
   법령·판례·해석·인허가/PF golden corpus의 고정된 label bytes와
   counsel-approved threshold, 독립 maker-checker 승인, 실제 prediction
   결과를 확정해야 한다. semantic/OpenSearch 확대는 이 측정 이후 별도
   ADR로 한다.
6. waiver/opinion/analysis/Citator를 포함한 external-counsel delegation과
   append-only lifecycle은 코드에서 강제한다. 남은 일은 실제
   PostgreSQL에서 revoke/decision 동시성, server time, RLS 및 전용
   legal-review role의 허용·거부 경로를 재현하는 것이다.
7. waiver cure transition, Citator queue/review, tenant legal alias review와
   normalized readiness 기반 Chief Deal Room action은 구현됐다.
   matter·waiver·opinion·analysis·delegation의 전체 작성 UX와 legacy
   backfill/dual-read parity는 별도 제품·전환 작업으로 남는다.
8. API의 explicit-human 검사와 server-derived actor/role만으로는
   legal-review DB credential 탈취 시 direct SQL actor 위조를 완전히
   차단하지 못한다. transaction-local signed actor context 또는 동등한
   brokered-write 방식으로 DB trigger와 실제 IdP/role membership을
   결합하고 credential 발급·회수·감사를 검증해야 한다.

운영 절차와 증거 양식은
[`docs/engineering/legal-corpus-operations.md`](../engineering/legal-corpus-operations.md)
를 따른다.

#### 17.0.2 이전 기준선 검증 기록 — 2026-07-18

검증은 테스트 간 공유 SQLite 경합을 피하기 위해 파일군별 고유
`REALYTICS_SQLITE_FILE`과 `--basetemp`를 사용해 순차 또는 격리 실행했다.
공유 SQLite를 사용한 최초 오케스트레이션 실행의 timeout은 격리 재실행에서
재현되지 않았으며, 해당 23건은 모두 통과했다.

| 검증 | 결과 | 비고 |
|---|---|---|
| API 전체 수집 | 1,465 tests | `pytest tests --collect-only` |
| API 전체 분할 회귀 | 1,450 passed, 15 skipped, 0 failed | A–F 416 passed, G–O 453 passed + 최종 search-path 사례 2 passed, P–Z 578 passed + 수정된 wrapper 1 passed; 14건은 Parser V2 PostgreSQL live, 1건은 typed PostgreSQL integration이며 모두 `REALYTICS_TEST_DATABASE_URL` 미설정으로 skip |
| 최종 Legal Intelligence 집중 회귀 | 278 passed | 20개 legal/role/migration 파일의 corpus sync, authority/egress, runtime, router, migration, runtime privilege 계약 |
| Web type 검사 | 통과 | `next typegen && tsc --noEmit` |
| Web lint | 통과 | ESLint, warning 0 |
| Web production build | 통과 | Next.js optimized production build 및 25개 static page 생성 |
| Supabase migration wrapper | current | 저장소 가상환경 Python으로 `render_supabase_migrations.py --check` 실행 |

이 기록은 `0009` 이전 로컬 코드·계약 회귀의 기준선이다. 2026-07-20
변경은 별도 집중 테스트와 전체 API/Web 회귀를 다시 통과해야 하며, 위
수치를 현재 변경의 최종 통과 수치로 인용해서는 안 된다. 또한 실제
Supabase/PostgreSQL에 `0008_legal_intelligence_core.sql`과
`0009_legal_governance_and_tenant_overlay.sql`을 적용한 결과,
runtime/sync/review/operations role, RLS, connection pooling, 다중
worker, 공식 provider 자격증명, S3/MinIO Object Lock, backup/restore 및
장애 복구 훈련의 증거를 대신하지 않는다.

### Phase 0 — Authority and security contract

- Create this ADR and reconcile existing MCP/Legal Pod documentation.
- Add canonical enums for authority, transport, content class, verification,
  freshness, execution, and approval.
- Separate provider adapters from MCP tool adapters.
- Mark caller-supplied `lawContext` as untrusted candidate input.
- Remove `mcp.connected` from Approval Readiness evidence logic.
- Require exact evidence completeness for legal gates.
- Enforce RBAC on legacy and canonical Legal Pod routes.
- Add secret/URL redaction and outbound provider allowlist.
- Define feature flags and compatibility mapping.

Exit gate:

- fabricated caller context cannot clear a gate;
- no unauthenticated/permissive legal mutation path remains;
- direct API and MCP results expose independent authority/transport fields;
- secret-redaction regression tests pass.

### Phase 1 — Verified corpus and official adapters

- Add global corpus and tenant overlay migrations.
- Add immutable raw fetch/object hashing.
- Implement provider-specific official adapters for required resource families.
- Add sync run/checkpoint/retry/circuit behavior.
- Implement bitemporal version publishing and supersession.
- Implement exact identifier/name/article/case-number retrieval.
- Extend the existing glossary instead of duplicating it.
- Provision least-privilege PostgreSQL roles and forced RLS.

Controlled adapter status (2026-07-20):

- Direct list search records the requested as-of date and observation time but
  does not claim that a list row verifies historical applicability.
- Law.go.kr is modeled as transport/aggregator; the issuing authority is mapped
  by resource family (court, Constitutional Court, central government, or local
  government).
- Target-specific list/detail/history boundary contracts cover statutes,
  provisions, precedents, interpretations, administrative rules, local
  ordinances, Constitutional Court decisions, and legal terms. Contract-shape
  fixtures reject unknown envelopes, mismatched identity/page/display and
  malformed history rows.
- `LawGoKrOfficialCorpusPageSource` supplies list/detail pagination,
  scope-bound checkpoints, versioned target descriptors, and partial-page
  diagnostics. The completion coordinator refuses to attest a partial page.
- This is not the Phase 1 exit gate. Actual official response bytes under an
  approved fixture manifest, byte-preserving acquisition, resource-specific
  history acquisition/semantic normalizers, provider terms, operating
  credentials, scheduled initial/backfill jobs, and live PostgreSQL
  verification remain required.

Exit gate:

- same official payload is idempotent and hash-reproducible;
- a changed payload produces a new version, not an in-place rewrite;
- past/current/future-effective versions resolve correctly by date;
- official adapter fixture/contract tests pass;
- live PostgreSQL cross-tenant/RLS tests pass.

### Phase 2 — Retrieval, citation, viewer, and Legal Pod integration

- Add PostgreSQL FTS and `pg_trgm`.
- Build a representative legal/PF golden query corpus.
- Implement staged ranking and evaluation reports.
- Implement citation verifier and sealed evidence snapshots.
- Add statute/precedent/interpretation viewer with source/version badges.
- Show official text, AI summary, and counsel opinion separately.
- Replace legacy Legal Pod/MCP reference persistence with normalized links.
- Convert Approval Readiness to evidence/freshness/human-review checks.
- Add optional pgvector recall only after baseline measurement.

Exit gate:

- exact identifiers always rank first;
- every report-eligible claim has an exact verified locator;
- weak/unresolved/stale evidence fails closed;
- viewer reproduces the review's exact source version;
- golden-corpus precision/recall/abstention thresholds are approved.

### Phase 3 — Optional self-hosted MCP and operational hardening

- Package a pinned self-hosted MCP sidecar.
- Add SBOM, internal registry, license, scanning, and contract tests.
- Add field-level data classification and egress enforcement.
- Add provider/MCP outage, failover, and disaster-recovery drills.
- Add human-review workflow and counsel approval UI.

Exit gate:

- complete legal workflow passes while all MCP endpoints are disabled;
- MCP response cannot assert source authority without attestation;
- remote MCP remains disabled in production defaults;
- outage and rollback drills satisfy the runbook.

### Phase 4 — Measured scale expansion

- Evaluate OpenSearch/Nori only if PostgreSQL has a documented limit.
- Develop human-reviewed precedent-treatment/citator assertions.
- Add relation expansion and impact analysis.
- Evaluate a graph database only if PostgreSQL relation queries show a measured
  operational or performance need.

Exit gate:

- a separate benchmark and ADR justify every new infrastructure component;
- relation assertions expose evidence, date, confidence, and review state;
- no inferred precedent treatment is rendered as authoritative fact.

## 18. Test strategy and acceptance gates

### 18.1 Unit and contract tests

- source authority and transport cannot be conflated;
- provider target/resource mappings;
- schema validation and quarantine;
- identifier/article/case-number normalization;
- source and normalized-content hashes;
- bitemporal/as-of resolution;
- glossary candidate/approval behavior;
- staged search precedence;
- citation exact-locator verification;
- caller mapping versus server-issued `lawContext` trust;
- requested date versus verified effective date and issuing authority;
- normalized Approval Readiness snapshot/decision/lineage re-query;
- maker-checker, scoped delegation and final-role enforcement;
- Citator excerpt/hash/supersession/review-event integrity;
- provider retry classification, `Retry-After`, jitter, rate limit, partial
  state, quarantine, DLQ and replay state machine;
- golden-corpus manifest/label/prediction integrity and threshold failure;
- stale/freshness policy;
- secret redaction.

### 18.2 Integration tests

- local snapshot hit;
- missing/stale local snapshot followed by official refresh;
- official timeout and last-known-good behavior;
- official schema drift quarantine;
- sync replay/idempotency/checkpoint resume;
- MCP available/unavailable equivalence for core workflow;
- caller-supplied fake context rejection;
- Legal Pod evidence snapshot and PF handoff;
- Approval Readiness fail-closed behavior;
- legacy projection tamper with normalized PostgreSQL unchanged;
- tenant-native matter/waiver/opinion/analysis/annotation/alias workflows;
- dedicated legal-review and legal-operations DSN non-fallback behavior;
- immutable object hash and database lineage.

### 18.3 Live PostgreSQL tests

- global corpus write denied to runtime role;
- published global corpus readable to runtime role;
- overlay CRUD limited to the active tenant/project;
- cross-tenant joins, counts, search, and error paths reveal nothing;
- missing tenant context fails closed;
- migration owner and runtime roles are distinct;
- legal-sync, legal-review and legal-operations principals have only their
  allowlisted append/read privileges and cannot inherit elevated roles;
- final legal decision rejects self-approval, expired/superseded delegation and
  cross-project delegation;
- Citator verification is append-only and rejects machine-finalized
  interpretive relations;
- DLQ replay requires a worker-registered immutable object catalog receipt;
  callers cannot submit locator/hash claims, and the executor must attest the
  exact catalog ID, object version, and SHA-256 before completion;
- RLS remains effective under application connection pooling.

### 18.4 Retrieval golden corpus

The corpus includes at least:

- exact law IDs and official names;
- article/paragraph/item forms;
- Korean case-number variants;
- abbreviations, approved aliases, spacing, and common typos;
- effective-date and historical-version questions;
- real-estate development, zoning, permit, trust/security, PF covenant, sale,
  construction, and tenancy questions;
- adversarial ambiguous questions requiring abstention;
- citations that deliberately reference a wrong article/version;
- superseded and future-effective sources.

Measure:

- exact identifier accuracy;
- top-k lexical/semantic recall;
- locator accuracy;
- version/as-of accuracy;
- citation mismatch detection;
- appropriate abstention;
- freshness correctness;
- cross-tenant isolation.

Thresholds are versioned and approved before a retrieval policy is promoted.
The local-hit percentage is an observed KPI, not a promised fixed value.

## 19. Operational runbook requirements

Before production, the runbook MUST cover:

### 19.1 Official provider outage

- confirm circuit state and last successful checkpoint;
- expose last-known-good age and source-specific status;
- stop gate-clearing operations that require fresh evidence;
- continue permitted read-only stale display;
- retry with bounded backoff;
- notify operators and record recovery.

### 19.2 Source schema drift

- quarantine incompatible payloads;
- preserve raw object/hash;
- do not publish a normalized version;
- alert with adapter/schema versions and affected resource family;
- update fixture/contract tests;
- replay quarantined fetches after adapter release.

### 19.3 Sync backlog or partial run

- resume from durable checkpoint;
- verify idempotency and payload hashes;
- report per-resource success/failure rather than one misleading success;
- reconcile additions, changes, removals, and future-effective records;
- seal and audit the completed run.

### 19.4 Secret exposure or rotation

- revoke/rotate in the secret manager;
- purge or restrict affected logs/traces according to incident policy;
- verify URL/error redaction;
- restart only the dependent provider workers;
- run credential-negative and recovery smoke tests.

### 19.5 Tenant isolation incident

- disable affected endpoints/roles;
- preserve audit evidence;
- identify connection/session-context scope;
- rotate credentials if necessary;
- run full cross-tenant RLS regression before restoration.

### 19.6 MCP outage or compromise

- disable MCP feature flag and egress immediately;
- keep local/direct-official workflows running;
- quarantine unverified MCP outputs;
- compare pinned image/SBOM and audit calls;
- restore only after contract/security verification.

### 19.7 Search/index degradation

- fall back to exact/lexical retrieval;
- disable semantic or relation expansion independently;
- rebuild optional indexes without changing source records;
- compare golden-corpus results before promotion.

### 19.8 Rollback

- rollback application/provider adapters independently;
- do not delete immutable source fetches or audit events;
- unpublish a faulty normalized version by a superseding status/event;
- retain database migration forward-repair instructions;
- verify Legal Pod and gate behavior after rollback.

## 20. Migration and compatibility

- Existing `legal_reviews` and `permit_reviews` remain readable during
  transition.
- Migration `0009_legal_governance_and_tenant_overlay.sql` adds tenant-native
  matters, waivers, counsel opinions, analysis versions, annotations, tenant
  aliases, delegation, Citator review events and provider operation ledgers
  without changing the immutable checksum of `0008`.
- Migration `0010_legal_corpus_coverage_contracts.sql` adds a deployment-owned
  eight-target registry, exact query/adapter/boundary identities, and an
  append-only initial/incremental completion chain without rewriting legacy
  nullable completion rows.
- Approval Readiness always reads authority from normalized
  snapshot/corpus/decision records and fails closed when they are unavailable.
- Legacy legal/permit projections remain until backfill and dual-read parity
  prove that every supported workflow has a typed tenant-native equivalent.
- Existing `mcp` fields remain a compatibility projection only. They are not
  authoritative storage.
- Existing `realytics-law-context-v1` requests remain accepted as candidates
  during a defined deprecation window.
- Backfill creates links only where official identifiers and source hashes can
  be verified. Unverifiable legacy references remain visibly unverified.
- Data migration never upgrades a legacy `connected` or `verified` boolean into
  source attestation without re-verification.
- Approval Readiness switches only after dual-run results and blocker
  differences are reviewed.
- The Supabase wrapper/ledger includes `0009`; `db push --dry-run` and live
  application are still required before treating the schema as deployed.

## 21. Consequences

### Positive

- Legal research remains available without an MCP.
- Source authority and operational transport become auditable.
- Project decisions can reproduce the exact legal version and basis.
- Tenant data remains separated from the public legal corpus.
- The existing glossary and review workflows are reused.
- Search infrastructure grows only when measurements justify it.
- AI and tooling can assist research without masquerading as legal authority.

### Costs and trade-offs

- Source-specific adapters and versioning are more work than a single generic
  search call.
- Immutable fetches and bitemporal records require storage/retention planning.
- Fail-closed freshness and citation rules may surface more blockers during
  rollout.
- A useful golden corpus and counsel-reviewed acceptance thresholds require
  domain-expert time.
- Dual-write/backfill is required to preserve existing Legal Pod behavior.

These costs are accepted because legal evidence affects development
feasibility, permit risk, PF conditions precedent, approval gates, and final
reports.

## 22. Official and primary references

- Korean Ministry of Government Legislation, OPEN API list:
  <https://open.law.go.kr/LSO/openApi/guideList.do>
- Korean Ministry of Government Legislation, OPEN API usage guidance:
  <https://open.law.go.kr/LSO/information/guide.do>
- Korean Ministry of Government Legislation, precedent list API:
  <https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=precListGuide>
- Korean Ministry of Government Legislation, precedent detail API:
  <https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=precInfoGuide>
- PostgreSQL `pg_trgm`:
  <https://www.postgresql.org/docs/current/pgtrgm.html>
- pgvector:
  <https://github.com/pgvector/pgvector>
- Elasticsearch Nori analysis plugin:
  <https://www.elastic.co/docs/reference/elasticsearch/plugins/analysis-nori>
- Korean Law MCP upstream:
  <https://github.com/chrisryugj/korean-law-mcp>

Official provider terms, approval scope, attribution requirements, rate limits,
and endpoint contracts MUST be rechecked when an adapter version is released.

## 23. Definition of done

The Legal Intelligence Core is production-ready only when all of the following
are true:

- MCP can be fully disabled without breaking core legal retrieval or Legal Pod
  execution.
- Caller-supplied context cannot claim authoritative status or clear a gate.
- Every report-eligible material legal claim has an exact official
  resource/version/provision locator and source hash.
- Historical, current, and future-effective sources resolve correctly for the
  requested date.
- Stale, unverified, inferred, AI-generated, and counsel-approved states are
  visibly distinct.
- Official source text cannot be overwritten by AI or reviewer content.
- Global source corpus and tenant overlays satisfy live least-privilege/RLS
  tests.
- `0008`/`0009`/`0010` are applied to the actual
  PostgreSQL/PostGIS/Supabase target, and the reviewed eight-target coverage
  manifest is the current registered leaf,
  including pooler tenant context, concurrent worker fencing, concurrent
  delegation revocation/decision, and dedicated-principal deny tests.
- The legal-review write path binds the authenticated human identity to a
  PostgreSQL-verifiable actor context; possession of the shared review
  credential alone cannot select another reviewer ID/role.
- Exact legal identifiers always outrank fuzzy and semantic candidates.
- Open citation conflicts and unsupported precedent-treatment assertions block
  authoritative presentation.
- Machine-inferred citations/relations cannot become verified without a
  separate human review event, exact excerpt/hash and reviewer timestamp.
- Snapshot creators cannot make their own final decision, and delegated
  external counsel decisions reproduce the exact valid delegation.
- Human Citator review and provider DLQ replay use separately audited
  least-privilege DSNs with no runtime/sync fallback.
- Official adapter contract, sync replay, outage, schema-drift, secret
  redaction, and recovery tests pass.
- Real provider credentials and approved byte-preserving list/detail/history
  fixtures drive successful initial and incremental backfill; generic search
  rows or synthetic fixtures do not satisfy this condition.
- The S3/MinIO reader verifies exact object version/hash under separate IAM,
  versioning and Object Lock, and backup/restore plus replay drills reproduce
  the same immutable bytes.
- Approval Readiness evaluates evidence completeness, freshness, and required
  human approval rather than MCP connectivity.
- Operational monitoring, alerting, backup/restore, and incident runbooks have
  been exercised in a live-like environment.
- A real counsel-approved golden corpus passes the versioned claim precision,
  claim recall, abstention and locator thresholds; synthetic unit fixtures do
  not satisfy this release condition.
