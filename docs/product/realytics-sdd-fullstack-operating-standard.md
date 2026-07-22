# REALYTICS SDD Full-Stack Operating Standard

Updated: 2026-07-02

Project: REALYTICS x PLATFORM9

## 1. Purpose

This document adapts the Korean SDD full-stack standard prompt to the current REALYTICS OS architecture. The goal is not to replace the existing real-estate/PF domain model. The goal is to make every product change traceable across:

```text
Spec
  -> Screen Policy
  -> Design System
  -> API Contract
  -> DB / Repository
  -> Agent / Pod Workflow
  -> Test
  -> Audit Log
  -> Operation / Monitoring
```

REALYTICS is a real-estate development and PF review operating system. Therefore SDD must work like a deal-review control system: no major feature should move to implementation unless its inputs, evidence, assumptions, authorization, data model, workflow gate, test, and monitoring path are known.

## 2. Applicability

Applicable now:

| Area | How it applies to REALYTICS |
|---|---|
| Spec Driven | Feature requirements must state domain purpose, workflow track, required evidence, missing-data behavior, and human review gate. |
| Screen Driven | Each page must declare which project context it reads from the Shared Deal State Graph and which server state it can mutate. |
| Software Driven | API, repository, DB persistence, audit event, and test must be linked before the feature is treated as complete. |
| Design Driven | Korean financial UX labels, mobile states, long Korean text, empty/loading/error/permission states must be designed as first-class states. |
| AI Agent Workflow | Agent/Pod changes must declare whether AI is used, which brain/provider is used, and what deterministic engine remains the source of truth. |

Not directly adopted:

| Prompt Area | REALYTICS Decision |
|---|---|
| NestJS standard | Current backend is FastAPI. We adopt service/API/DTO/policy principles, not NestJS framework files. |
| Commerce-specific order/refund/point settlement chapters | Not core for current pilot. Reuse only if future platform billing/marketplace modules are added. |
| MySQL-first patterns | PostgreSQL/PostGIS is the production target because of GIS, JSONB, audit, and vector search needs. |

## 3. Required SDD Artifact Contract

Every significant feature should have these artifacts.

| Artifact | Required Fields | Stored / Referenced In |
|---|---|---|
| `Spec` | `specId`, goal, scope, non-goals, owner, evidence required, risks, acceptance criteria | `docs/product/*.md`, backlog |
| `ScreenPolicy` | `screenId`, route, user roles, states, empty/loading/error/success, mobile behavior, Korean UX labels | page/component docs or route source |
| `APIContract` | endpoint, request, response, auth scope, error shape, audit event, idempotency rule | router/use_case/schema |
| `DBContract` | table/store, primary key, tenant/project ownership, retention, source locator policy | repository/storage/postgres docs |
| `AgentContract` | agent/pod, deterministic vs LLM role, allowed inputs, forbidden outputs, handoff artifact | agent docs/schema |
| `TestContract` | unit, integration, UI smoke, regression fixture, failure gate | tests and CI/check command |
| `TraceabilityRow` | spec -> screen -> API -> DB -> test -> audit -> report/output | feature checklist |

## 4. REALYTICS Traceability Matrix

Use this matrix before marking a backlog item Done.

| Layer | Required Question | Example for REALYTICS |
|---|---|---|
| Track | Which workflow track owns it? | 부동산개발, 부동산 PF, 실물금융, 리츠, 펀드 |
| Shared State | Which graph section is read/written? | Evidence Vault, Assumption Book, Land Evidence, Market Evidence |
| Screen | Which route shows or edits it? | `/project/{id}/input-package`, `/chief-orchestration`, `/map` |
| API | Which endpoint is the source of truth? | `POST /projects/{id}/chief-orchestration/run-pod` |
| DB | Where is it persisted? | SQLite now, PostgreSQL JSONB/table contract later |
| Agent | Which Agent/Pod uses it? | Land-Legal Pod, Market-Product Pod, PF-Credit Pod |
| Evidence | Does it carry source locator? | document page, Excel cell, API row, PNU, evidence ID |
| Risk | Does it affect approval readiness? | blocker, warning, waived, resolved |
| AI | Is LLM used? | reviewer/narrator only; deterministic engine remains source of truth |
| Test | What proves it works? | pytest, web typecheck/lint, smoke test |
| Audit | What is logged? | user action, AI brain used, review decision, override |

## 5. Screen Policy Standard

Every route should define:

| Field | Meaning |
|---|---|
| `screenId` | Stable ID, e.g. `screen.project.input_package`. |
| `primaryUser` | 심사역, 시행사, 관리자, 리뷰어 등. |
| `sourceContext` | `GET /projects/{id}/workspace/context?page=...` or explicit API. |
| `mutations` | Upload, review, waive, approve, run pod, export, etc. |
| `states` | loading, empty, partially ready, blocker, success, error, permission denied. |
| `mobileBehavior` | Responsive density, drawer/modal behavior, long Korean text wrapping. |
| `businessTerms` | Labels must come from financial term map or glossary where possible. |

Rule: if a page displays project data, it should not read an unrelated mock object when project context exists. It should use the same Shared Deal State Graph and page-specific context contract.

## 6. API Standard

FastAPI endpoints should follow this pattern:

```text
router
  -> authorize_request
  -> use_case
  -> repository/storage
  -> audit event
  -> schemaVersion response
```

Required response fields for operating-system endpoints:

| Field | Required |
|---|---|
| `schemaVersion` | Yes |
| `projectId` | If project-scoped |
| `status` | For workflows/runs |
| `dataLimitations` | When source data is partial |
| `evidenceIds` / `sourceLocators` | When claims, metrics, or risks are returned |
| `nextActions` | When user work is required |

## 7. DB and Repository Standard

REALYTICS should avoid “screen-only state”. New server state must have a repository/store decision:

| State Type | Store Policy |
|---|---|
| Project/deal profile | PostgreSQL target, SQLite pilot |
| Source document metadata | DB metadata + object storage binary |
| Evidence, assumptions, metrics, risk issues | First-class DB records |
| Chief Pod artifacts | Separate artifact table/store with project reference |
| AI brain audit | Append-only audit log |
| Glossary/canonical field map | Term standard DB tables |
| User API keys | Never in code/logs; secret manager target, temporary browser key only for pilot |

DB-to-Pod boundary:

```text
DB raw/evidence state
  -> terminology normalization
  -> canonical field hydration
  -> Pod input contract
  -> artifact persistence
```

## 8. Design System Standard

Current UI should evolve toward a REALYTICS design system rather than ad hoc page styling.

The normative visual, financial-data, state-axis, component, accessibility,
and migration rules are defined in `docs/product/realytics-design-system.md`.
This section is the SDD entry point; the design-system document is the detailed
implementation contract.

Priority components:

| Component | Use |
|---|---|
| `ProjectContextHeader` | Project name, track, evidence status, user role |
| `WorkflowStepper` | Step-by-step intake and pod workflow |
| `EvidenceStatusBadge` | source / assumption / calculated / estimated / missing |
| `RiskStatusControl` | approved / waived / needs_revision / resolved |
| `AgentPodCard` | Pod status, AI brain status, artifacts, next action |
| `FileUploader` | source document upload with classification and evidence policy |
| `KoreanFinancialDataTable` | Long Korean labels, numbers, source locators |
| `AuditTimeline` | User, AI, Agent, override and review events |

Storybook is recommended before enterprise rollout, but not required for the current pilot.

## 9. AI Agent Development Standard

AI Agents must declare:

| Field | Meaning |
|---|---|
| `agentId` | Stable ID |
| `podKey` | Owning Pod if any |
| `usesLLM` | true/false |
| `allowedLLMRole` | classify, summarize, review, explain, draft text |
| `forbiddenLLMRole` | calculate final numbers, fabricate evidence, approve credit |
| `inputEvidenceIds` | Evidence read by the AI |
| `outputFindingIds` | Findings or drafts created |
| `aiBrainUsedEventId` | Audit event |

Rule: AI can assist reasoning and drafting, but deterministic engines own financial calculations and approval gates.

## 10. SDD Gates

| Gate | Must Pass Before |
|---|---|
| `SpecReadyGate` | Coding starts |
| `ScreenReadyGate` | UI implementation starts |
| `APIReadyGate` | Frontend depends on endpoint |
| `DBReadyGate` | Persistent workflow is advertised as real |
| `EvidenceGate` | Claim/metric/risk enters report or approval readiness |
| `SecurityPrivacyGate` | Auth, upload, secret, PII, audit changes ship |
| `CalculationGate` | Cash flow/PF/math result is shown as authoritative |
| `ReportGate` | PDF/DOCX/IC memo export is treated as deliverable |

## 11. Definition of Ready

A REALYTICS feature is ready for implementation when:

- It has a track or cross-track owner.
- The source of truth is known: DB, external API, uploaded document, or calculated artifact.
- The canonical schema or response shape is known.
- Missing-data behavior is defined.
- Required user role and audit event are known.
- At least one test path is defined.

## 12. Definition of Done

A feature is done when:

- Code is implemented without mock-only critical paths.
- Server state is persisted or explicitly documented as temporary.
- Korean business labels are stable and do not show raw internal field paths.
- Evidence/source locator policy is followed.
- Relevant API tests or frontend checks pass.
- `docs/backlog.md` and relevant product docs are updated.
- If LLM is used, `aiBrainUsed` audit is recorded.

## 13. Immediate Application to Current Backlog

| Priority | Apply Now | Reason |
|---|---|---|
| P0 | DB-to-Pod terminology boundary | Prevent Korean/English field drift and broken labels. |
| P0 | Shared Deal State page context for all project pages | Stop route-by-route mock drift. |
| P0 | Upload package as first-class workflow | User must be able to add missing evidence at every step. |
| P1 | Agent/Pod AI Reviewer contract | Makes AI role explicit per Pod. |
| P1 | Design system component inventory | Reduces UI complexity and repeated Korean label fixes. |
| P1 | Storybook or component QA harness | Needed before external pilot expansion. |

## 14. Agent Rule

Before coding a major feature, an AI coding agent must answer:

```text
1. Which workflow track owns this?
2. Which page/screen exposes this?
3. Which API is the source of truth?
4. Which DB/repository stores it?
5. Which Agent/Pod reads or writes it?
6. Which evidence/source locator is required?
7. Which tests and audit events prove it?
```

If any answer is unknown, the change may still be prototyped, but it must not be represented as production-ready.

## 15. Progressive Stub / Mock / Placeholder Control Standard

REALYTICS OS does not ban stubs, mocks, or placeholders during prototyping.

Fast experiments are allowed. However, unfinished behavior must be visible,
traceable, and separated from production-ready behavior.

The goal is not to block iteration. The goal is to prevent prototype code from
being mistaken as production-grade deal-review logic.

### 15.1 Implementation Lanes

REALYTICS uses progressive implementation rather than blocking all incomplete
work. A feature may move through three implementation levels.

#### Level 1. Prototype Lane

Purpose:

- Fast UI/UX exploration.
- Workflow demonstration.
- Internal concept validation.

Allowed:

- Mock data.
- Stub API.
- Placeholder UI.
- Temporary local state.
- Sample project data.

Required:

- `PROTOTYPE-ONLY:` marker where the temporary behavior is implemented.
- No production-ready label.
- Visible limitation if user-facing.
- Backlog item if the feature will continue.

Not required:

- Full DB persistence.
- Full audit log.
- Complete deterministic engine.
- Complete external API integration.

#### Level 2. Controlled Implementation Lane

Purpose:

- Partial but useful implementation.
- Internal pilot.
- Non-critical workflow validation.

Allowed:

- Temporary adapter.
- Partial repository.
- Limited API contract.
- Manual verification.
- Feature flag.

Required:

- `TODO-SDD:` marker for incomplete parts.
- Missing behavior documented.
- Test path or manual QA path.
- Source-of-truth decision.
- Known production blockers listed in backlog.

#### Level 3. Production-Critical Lane

Purpose:

- Real deal review.
- Report-grade output.
- Financial calculation.
- Approval readiness.
- External pilot or enterprise use.

Not allowed:

- Hard-coded financial results.
- Fake evidence IDs.
- Fake source locators.
- Mock-only API.
- Console-only audit.
- Always-true permission.
- LLM-generated final calculation.
- Untracked assumptions.

Required:

- Deterministic calculation where applicable.
- Evidence/source locator.
- DB persistence.
- Audit event.
- Permission check.
- Test coverage.
- Monitoring or runbook.
- Human review gate where needed.

### 15.2 Production-Critical Scope

The following areas are strict by default and should be treated as
Production-Critical unless explicitly downgraded for internal prototype work:

- Business feasibility calculations.
- Cash Flow / Max Minus.
- LTV / LTC / DSCR / ICR.
- Exit sale rate.
- Tranche / Waterfall.
- Evidence ID / Source Locator.
- Approval / Hold / Waive / Override.
- Permission / Authentication.
- Audit Log.
- Report export.
- AI Brain usage audit.

### 15.3 Flexible Prototype Scope

The following areas may proceed with reasonable assumptions or prototype
markers when the decision is not material to deal review:

- Initial UI layout.
- Empty state screens.
- Design-system component drafts.
- Storybook drafts.
- Temporary route wiring.
- Non-critical dummy chart.
- Onboarding text.
- Demo sample project.
- Admin convenience features that are not finalized.

### 15.4 Clarification Decision Rule

Do not stop for every uncertainty. Use this decision rule:

Proceed without asking when the uncertainty is limited to:

- UI copy.
- Temporary layout.
- Non-critical filter defaults.
- Demo data display.
- Internal component naming.
- Storybook draft.

Proceed with a documented assumption when the uncertainty affects:

- API response field naming.
- Temporary schema.
- Screen state distinction.
- Sample project data.
- Temporary empty state.

Ask clarification before presenting the work as production-ready when the
uncertainty affects:

- Calculation formula.
- Permission policy.
- Approval status.
- Evidence standard.
- Report-grade text.
- PF review result.
- DB source of truth.
- External API standard.
- What an LLM is allowed to decide.

### 15.5 Required Stub Markers

Any temporary implementation must include one of the following markers:

```txt
TODO-SDD:
STUB-ONLY:
PROTOTYPE-ONLY:
MOCK-DATA:
NEEDS-DOMAIN-DECISION:
NEEDS-HUMAN-CLARIFICATION:
```

### 15.6 Stub Registry Item

Every intentional Level 2 or Level 3 stub must be registered in backlog or a
dedicated stub registry with this structure:

```md
# Stub Registry Item

- Stub ID:
- Implementation Lane: Prototype / Controlled / Production-Critical
- File / Route / API:
- Related Spec ID:
- Related Screen ID:
- Related API:
- Related DB / Repository:
- Reason for Stub:
- Missing Production Behavior:
- Assumption Made:
- Clarification Question:
- Risk Level:
- Production Blocker: true / false
- Owner:
- Target Resolution:
```

### 15.7 Implementation Behavior Rule for AI Agents

When implementing a feature, do not stop for every uncertainty.

1. If the missing detail is non-critical, make a reasonable assumption, document
   it, and continue.
2. If the missing detail affects UI only, implement a prototype with a clear
   marker.
3. If the missing detail affects DB, API contract, Evidence, Agent/Pod handoff,
   permission, audit, financial calculation, or report-grade output, ask
   clarification before presenting it as production-ready.
4. If a stub is useful for progress, create it, but mark it clearly and register
   the missing production behavior.

The objective is fast iteration with clear boundaries, not over-blocking.

### 15.8 Implementation Completeness Label

Every major feature should be labeled:

```md
## Implementation Completeness

- Implementation Lane:
  - [ ] Level 1. Prototype
  - [ ] Level 2. Controlled Implementation
  - [ ] Level 3. Production-Critical

- Implementation Type:
  - [ ] Production-ready
  - [ ] Prototype-only
  - [ ] Stub-only
  - [ ] Mock-only
  - [ ] Needs clarification

- Stub / Mock Exists:
  - [ ] No
  - [ ] Yes

- If yes:
  - Stub ID:
  - Reason:
  - Missing production behavior:
  - Assumption made:
  - Clarification question:
  - Risk:
  - Owner:
  - Target resolution date:
```

Rule: fast experiments are allowed, but experiments must not be disguised as
production-ready implementation.
