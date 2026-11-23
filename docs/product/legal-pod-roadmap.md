# Legal Pod Operating Model

Updated: 2026-07-13

## Purpose

The Legal Pod is the legal and permit review capability of the Land-Legal Pod. It is a preliminary, evidence-aware review system for development and PF work. It does not issue a legal opinion and must never invent registry, permit, statute, or contract facts.

## Execution

`POST /projects/{project_id}/legal-pod/run` is the independent execution endpoint. It reads the project Shared Deal State, persisted land evidence, parser results, and Legal Intelligence results. A supplied `lawContext` is accepted only as an untrusted discovery candidate.

The existing `POST /projects/{project_id}/legal/review` remains a backwards-compatible precheck endpoint. Chief Deal Orchestrator uses the Legal Pod use case when executing `land_legal`.

## Persisted ledgers

- `legal_reviews`: preliminary review status, Legal Intelligence evidence state, compatibility transport state, parcel review rows, evidence locators, risk issues, human review history, and PF handoff.
- `permit_reviews`: permit check, parser availability, source locators, and missing-document reason.
- `deal_state_records`: legal risk issues, evidence-backed handoff, and future first-class legal metrics.

SQLite creates `legal_reviews` and `permit_reviews`; PostgreSQL migration `0001_initial_schema.sql` creates the same tables. Postgres currently also retains the full application state document for compatibility.

## Evidence locator policy

Legal output must use one of `document_page`, `excel_cell`, `api_row`, `land_evidence`, `artifact`, or `legal_review`. A locator may contain a document ID/page, workbook sheet/cell, API row, evidence ID, or artifact key. Missing locators remain visible and cannot be represented as verified legal conclusions.

## Developability and statutory review

`LegalDevelopabilityAgent` produces a deterministic `areaLawTable` for zoning, site area, statutory/planned FAR, statutory/planned BCR, district-unit-plan status, and permit schedule. It links the review to the Land Use Planning Act, its Enforcement Decree, the Urban Development Act, and the Building Act. If the project contains a district-unit-plan or urban-development flag, the relevant review is explicitly required. If a confirmed planned FAR/BCR exceeds a confirmed legal limit, the result is `blocked`; if the legal limit or required area evidence is missing, the result is `not_assessable`, not an inferred pass.

## Land collaboration

The Legal Pod consumes the land ledger and persisted land evidence by PNU. Each parcel receives a status, owner-match status, encumbrance status, registry evidence count, evidence IDs, and source locators. Missing registry evidence, owner mismatch, mortgage, seizure, provisional registration, easement, or trust-registration confirmation produces a legal risk issue.

## PF handoff

The output contains `LegalReadinessPatchV1` from `LegalReviewAgent` / `land_legal` to `PF-Credit Pod`. It includes blocking codes, evidence IDs, locators, and whether human review is required. The handoff is also persisted as a `deal_state_records` row.

## Legal Intelligence and optional MCP

The review exposes `legalIntelligence.evidenceStatus`, verified/candidate reference counts, freshness, conflicts, exact locators, hashes, provider trace, and human-review status. The local verified corpus is searched first, the official Law.go.kr API is the authoritative gap-fill path, and MCP is optional. Legacy `mcp` fields remain a non-authoritative compatibility projection. `not_connected` is never evidence that a legal issue does not exist.

## Approval Readiness

Gate v2 now checks that a Legal Pod review exists, unresolved blocking legal risks are absent, a permit review is available, legal evidence is verified/non-stale/conflict-free, and an authorized human review is complete. A transport connection flag cannot clear any gate.

## Remaining production work

- Add document, contract, permit, and statute parsers with page/cell-level provenance.
- Exercise the normalized corpus/overlay migration, least-privilege role, backup/restore, and RLS checks in a live PostgreSQL environment.
- Connect the approved object store and scheduled official-source sync worker.
- Build and approve the retrieval golden corpus and counsel-reviewed thresholds.
# Legal Pod lifecycle and law-change policy

Legal Pod is a participant in the development lifecycle, not a one-time
checklist. It reviews the legal inputs required by each relevant lifecycle
stage and hands blocking conditions to the next Pod.

## Asset-aware law coverage

The Legal Intelligence query planner selects additional search questions from the asset
type. Residential and mixed-use projects include the Housing Act, Housing
Supply rules, Building Act, and sale-advertising rules. Officetel and
commercial projects include the Building Act and building-sale law. Logistics
projects include logistics-facility law and Building Act requirements. REIT
and fund tracks include the REIT Act and Capital Markets Act requirements.

The result is normalized into `lawReferences` and persisted as
`deal_state_records` with `recordType=legal_evidence`, API-row locator, article,
effective date, and source URL.

## Law change tracking

The normalized legal reference contract accepts promulgation date, amendment
date, effective date, legislative-notice date, proposal date, change type,
change summary, law ID, and status. A connected law source is required before
these fields are presented as verified. Missing history remains an explicit
review condition; it is never inferred by the LLM.

## LLM role

The Legal Pod LLM is an evidence-aware reviewer. It may classify the asset,
select applicable law questions, summarize verified provisions, compare dated
references, and explain impact on the lifecycle. It may not invent an article,
effective date, legislative history, zoning limit, or developability result.
Deterministic checks remain authoritative for area, FAR/BCR, permit schedule,
rights, and approval gates.

Each Legal Pod rerun compares the new source-backed law set with the previous
snapshot. Added, removed, and changed references are persisted as
`legal_law_change` records and exposed through
`GET /projects/{project_id}/legal-pod/law-changes`. A change remains a review
item until its source and effective-date evidence are confirmed.
