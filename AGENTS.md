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

## 2. Architecture map

```
apps/web (Next.js)  →  routers/  →  use_cases/  →  agents · core(calculators) · db/repositories  →  schemas/
```
- **Persistence:** SQLite/JSON today (`storage.py`); PostgreSQL/PostGIS scaffold
  in `db/postgres.py` (gated). Object storage abstraction in `db/object_storage.py`
  (local default, S3 gated). Durable async jobs + recovery in `use_cases/async_jobs.py`.
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

1. **`docs/backlog.md` is the change log.** Every change adds a dated row
   (`| Priority | Module | Task | Status | Notes |`) at the top of the table with
   what/why/where. Bump the `Updated:` date.
2. **Keep the relevant doc current.** Behavior/architecture changes update the
   matching `docs/product/*.md` (or this AGENTS.md / `docs/architecture.md`).
   Validation runs are written up under `docs/product/`.
3. **Tests stay green.** Run `pytest` for the API and `tsc`/`lint` for the web;
   add tests for new behavior. Do not commit on a red suite.
4. **Respect the invariants in §1.** If a change would touch them, stop and raise
   it — do not quietly change the framework.

## 5. Dev commands

```bash
# API tests
cd services/api-server && ../../.runtime/api-venv/Scripts/python.exe -m pytest -q
# Web checks
cd apps/web && npx tsc --noEmit && npm run lint
# Run locally
cd services/api-server && ../../.runtime/api-venv/Scripts/python.exe -m uvicorn main:app --port 8000
cd apps/web && npm run dev   # http://localhost:3000  (API http://127.0.0.1:8000, Swagger /docs)
```

See `docs/architecture.md` for the deeper structure and `docs/backlog.md` for
the full change history.
