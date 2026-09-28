# AGENTS.md — Read this first (any AI or developer)

This file is the **invariant contract** for REALYTICS x PLATFORM9 (Real Estate
OS). Any AI or developer making changes MUST keep the big-picture framework below
intact and follow the documentation convention. Implementation details may
evolve; **the invariants do not.**

> 큰 틀(불변 원칙)은 바뀌지 않습니다. 다른 AI가 코딩하더라도 이 계약을 먼저 읽고 따릅니다.

---

## 1. Invariant principles (the 큰 틀 — never break these)

1. **Deterministic & evidence-first.** All financial calculations (IRR, LTV/LTC,
   cash flow, waterfall, stress, credit decision) are deterministic Python in
   `services/api-server/core` and `agents/*`. **LLMs never produce or alter
   numbers or the credit decision.**
2. **No fabrication, no silent overwrite.** Missing evidence stays missing
   (rendered as `—` / surfaced as unresolved), never invented. Agents exchange
   typed `AgentHandoffEnvelope` patches; they do not silently mutate shared state.
3. **LLM = coordinator / narrator only.** The consensus loop and deal optimization
   may use an LLM to *sequence, judge convergence, and explain* — patches/values
   come from deterministic rules. AI is auto-selected by `agents/ai_router.py`
   (residency-aware: self-hosted/local first for sensitive PF data) with a
   deterministic fallback when no provider is configured.
4. **Provenance everywhere.** Every value carries a source locator. The UI shows
   a business-friendly source (`agents/source_attribution.py`: 사업수지/공공데이터/
   감정평가서/토지대장·등기 …) plus the evidence value and a document link — never
   raw field paths.
5. **Human-gated learning.** Recursive optimization and glossary promotion go
   through human review (`docs/product/recursive-optimization-loop.md`). **No
   automatic self-modification of calculations or credit logic.**
6. **Korean, lender-facing output.** Credit documents (IC Memo / Term Sheet) are
   localized via `agents/credit_document_i18n.py`. A term sheet is the lender's
   indicative conditions to the borrower.
7. **Transparent progressive implementation.** Fast experiments are allowed,
   including stubs, mocks, placeholders, and sample data in prototype lanes.
   They must be visibly marked and must never be presented as production-ready.
   Production-critical areas such as financial calculation, PF review,
   evidence, approval, permission, audit, report export, and AI Brain usage
   records require stricter implementation, persistence, evidence, tests, and
   review gates.

## 2. Architecture map

```
apps/web (Next.js)  →  routers/  →  use_cases/  →  agents · core(calculators) · db/repositories  →  schemas/
```
- **Persistence:** Normalized PostgreSQL/PostGIS is the production authority;
  SQLite/JSON (`storage.py`) remains a local compatibility lane only. The
  private runtime boundary and RLS/pool rules are defined in
  `docs/architecture/architecture-boundary.md` and
  `docs/architecture/postgres-runtime-authority.md`. Object storage uses
  `db/object_storage.py` (local development, S3/MinIO production). Durable
  asynchronous work uses the PostgreSQL outbox/external-worker path; legacy
  in-process jobs remain compatibility-only where not yet promoted.
- **Canonical schemas:** FeasibilityStudyV2, CashFlowRunRequest, MarketabilityAnalysisV2,
  ScenarioSetV1, AgentHandoffEnvelopeV1, Phase11ReportV1, CreditPolicyProfile, etc.
- **RAG (grounding only):** `agents/retrieval.py` + `use_cases/similar_case_rag.py`
  — retrieve cited prior cases; never generate numbers/decisions.

## 3. Orchestration (the canonical pipeline)

`schemas/orchestration.py :: PF_REVIEW_STEPS` is the single source of truth for
the PF review pipeline (currently 27 steps). Agents collaborate via:
- typed **handoffs** (`AgentHandoffEnvelope`),
- the **cross-agent reconciliation gate**, then the **iterative consensus loop**
  (`agents/consensus_engine.py`) — agents re-analyse until cross-checked numbers
  agree; evidence-required gaps stay unresolved,
- **deal optimization** (`agents/deal_optimization_agent.py`) — derive the
  borrower-side path to approval (공사비 절감 / 분양가 상향 / 자기자본·PF / 신용보강)
  or conclude 여신 취급 곤란.
If you add a step, update `PF_REVIEW_STEPS`, the stepCount tests, and the
full-run orchestration test.

## 4. Documentation & change convention (REQUIRED on every change)

Current product scope and next work are owned by
[`docs/product/realytics-os-next-work-roadmap.md`](docs/product/realytics-os-next-work-roadmap.md).
Use [`docs/README.md`](docs/README.md) to distinguish current contracts from
historical results and expansion designs. A dated audit is evidence for that
revision, not a new requirement to redo completed work.

1. **`docs/backlog.md` is the change log.** Every change adds a dated row
   (`| Priority | Module | Task | Status | Notes |`) at the top of the table with
   what/why/where. Bump the `Updated:` date.
2. **Keep the relevant doc current.** Behavior/architecture changes update the
   matching `docs/product/*.md` (or this AGENTS.md / `docs/architecture.md`).
   Validation runs are written up under `docs/product/`.
3. **Use the REALYTICS SDD gate for major features.** Before treating a feature
   as production-ready, connect Spec -> Screen Policy -> API Contract ->
   DB/Repository -> Agent/Pod workflow -> Test -> Audit/Monitoring. See
   `docs/product/realytics-sdd-fullstack-operating-standard.md`.
4. **Tests stay green.** Run `pytest` for the API and `tsc`/`lint` for the web;
   add tests for new behavior. Do not commit on a red suite.
5. **Respect the invariants in §1.** If a change would touch them, stop and raise
   it — do not quietly change the framework.

### Product delivery and deletion policy (2026-09-28 user direction)

- Preserve the existing Chief Pods, business Agents, typed handoffs and the
  27-step PF workflow. They are the product's working architecture, not cleanup
  candidates. Improve their integration and outputs. The user explicitly
  reaffirmed this boundary during the documentation review.
- Continue diagnosis, reference checks and relevant verification autonomously.
  Present proposed Agent/Pod structural changes or major feature additions and
  removals for user approval before implementation. This does not require a new
  approval for routine work already authorized in the conversation.
- Deliver a complete user action: input, useful result, save/reopen, and a
  comprehensible failure state. Prefer completing the existing path over adding
  another Agent, framework, screen shell, or roadmap.
- Remove demonstrably unused code, abandoned templates, duplicate implementations,
  and disposable generated outputs in the same change. Check imports, routes,
  dynamic registries, scripts, and tests first; remove obsolete wiring and update
  the lockfile. Keep regression tests, fixtures, original business evidence,
  active data and migrations. Unused runtime output is not unused source code.
- Do not retain speculative abstractions merely for possible future use. Name a
  current caller and user benefit before adding an abstraction or a new Agent.
  Dynamic registry entries and indirect Pod calls are callers; a missing direct
  import is not evidence that an Agent is unused.
- Keep local exploration moving with explicit user assumptions, partial results,
  and clear limitations. Missing approval evidence must not block unrelated
  analysis. Final approval, financial truth, tenant boundaries and publication
  still use their existing controls.
- Scale documentation and verification to the change. A routine cleanup or UI
  fix needs a short purpose and relevant checks; it does not require a new SDD
  package. Put the next feature's acceptance criteria in the existing roadmap.
- New paid infrastructure and paid provider calls remain deferred. This is a
  development constraint, not permission to bypass existing access controls.
- Clean temporary artifacts created by your own verification after recording
  the result. Do not accumulate new audit scripts or full test logs in source.

## 5. Progressive Stub / Placeholder / Mock Rule

REALYTICS OS uses progressive implementation. Do not block every uncertainty,
but do not disguise experiments as production deal-review logic.

Implementation lanes:

1. **Level 1. Prototype Lane**: fast UI/UX exploration, demo workflow, sample
   project data, placeholder UI, and local state are allowed. Mark with
   `PROTOTYPE-ONLY:` where user confusion is possible. Do not label as
   production-ready.
2. **Level 2. Controlled Implementation Lane**: partial but useful pilot work is
   allowed with `TODO-SDD:`, documented missing behavior, a test or manual QA
   path, source-of-truth decision, and backlog item.
3. **Level 3. Production-Critical Lane**: stubs are not allowed unless explicitly
   approved and registered. This lane includes business feasibility, Cash Flow /
   Max Minus, LTV / LTC / DSCR / ICR, Exit sale rate, Tranche / Waterfall,
   Evidence ID / Source Locator, approval / waive / override, permission,
   authentication, audit log, report export, and AI Brain usage records.

Proceed without asking for low-risk details such as UI copy, temporary layout,
non-critical filter defaults, demo data display, internal component naming, or
Storybook drafts.

Proceed with a documented assumption for medium-risk details such as temporary
API field names, temporary schema, sample project data, screen state
distinctions, or empty states.

Ask clarification before presenting work as production-ready when the decision
affects calculation formulas, permission policy, approval status, evidence
standards, report-grade text, PF review results, DB source of truth, external
API standards, or what an LLM is allowed to decide.

Required markers for incomplete implementation:

- `TODO-SDD:`
- `STUB-ONLY:`
- `PROTOTYPE-ONLY:`
- `MOCK-DATA:`
- `NEEDS-DOMAIN-DECISION:`
- `NEEDS-HUMAN-CLARIFICATION:`

Forbidden in production-critical paths:

- Hard-coded financial metrics.
- Fake Evidence IDs.
- Fake source locators.
- Mock-only APIs presented as real.
- Skipped DB persistence presented as real workflow state.
- Agent/Pod workflow that only logs to console.
- Always-true permission checks.
- Console-only audit logs.
- LLM output used as authoritative calculation.
- Incomplete behavior hidden behind TODO comments without backlog registration.

## 6. Dev commands

```bash
# API tests
cd services/api-server && ../../.runtime/api-venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
# Web checks
cd apps/web && npx tsc --noEmit && npm run lint
# Run locally
cd services/api-server && ../../.runtime/api-venv/Scripts/python.exe -m uvicorn main:app --port 8000
cd apps/web && npm run dev   # http://localhost:3000  (API http://127.0.0.1:8000, Swagger /docs)
```

See `docs/architecture.md` for the deeper structure and `docs/backlog.md` for
the full change history.
