# REALYTICS x PLATFORM9 Architecture

Updated: 2026-05-19

## 1. Purpose

This document records the current repository structure, the target architecture, and a safe migration plan.

The project already contains most of the core REALYTICS and PLATFORM9 functions, but the folder layout is still MVP-oriented. The immediate goal is not to move files aggressively. The goal is to create a clear map so future refactoring can be done in small, testable steps.

## 2. Current Repository Structure

Current root:

```text
realytics-platform9-monorepo/
|-- apps/
|   `-- web/
|       |-- app/
|       |-- components/
|       |-- lib/
|       `-- package.json
|
|-- services/
|   `-- api-server/
|       |-- agents/
|       |-- core/
|       |-- data/
|       |-- routers/
|       |-- schemas/
|       |-- tests/
|       |-- main.py
|       `-- storage.py
|
|-- packages/
|   |-- eslint-config/
|   |-- typescript-config/
|   `-- ui/
|
|-- docs/
|   |-- integrations/
|   |-- product/
|   |-- api.md
|   |-- architecture.md
|   `-- backlog.md
|
`-- README.md
```

Important current implementation locations:

| Domain | Current Location | Notes |
|---|---|---|
| Web app | `apps/web` | Next.js app with dashboard, project, land, risk, orchestration, report, feasibility, finance, and cash-flow screens |
| API routes | `services/api-server/routers` | Main project API router is currently concentrated in `projects.py` |
| Agents | `services/api-server/agents` | Development feasibility, cash flow, marketability, land ledger, legal, parser, orchestration, and report agents |
| Calculations | `services/api-server/core` | Pure calculation functions, logistics helpers, and risk assessment |
| Schemas | `services/api-server/schemas` | Feasibility Study v2, report, orchestration, and Valuemap schemas |
| Storage | `services/api-server/storage.py` | SQLite/JSON-backed MVP persistence |
| Runtime data | `services/api-server/data/runtime` | SQLite state and uploaded document archive; ignored by Git |
| Product docs | `docs/product` | Agent roadmaps and implementation status |
| Integration docs | `docs/integrations` | Korean Law MCP and external integration notes |

## 3. Target Architecture

The long-term target can be expressed as:

```text
realytics-ai-lab/
|-- apps/
|   `-- web/
|       |-- app/
|       |-- components/
|       |-- lib/
|       `-- package.json
|
|-- services/
|   `-- api-server/
|       |-- agents/
|       |-- schemas/
|       |-- calculators/
|       |-- integrations/
|       |-- reports/
|       |-- db/
|       |-- routers/
|       |-- tests/
|       `-- main.py
|
|-- packages/
|   |-- shared-types/
|   `-- realytics-contracts/
|
|-- docs/
|   |-- architecture.md
|   |-- feasibility_schema.md
|   |-- pf_cashflow_logic.md
|   `-- agent_design.md
|
|-- docker-compose.yml
`-- README.md
```

This target is directionally correct, but it should be reached through compatibility wrappers rather than large file moves.

## 4. Current vs Target Gap

| Target Item | Current Status | Recommended Action |
|---|---|---|
| `apps/web/app` | Exists | Keep |
| `apps/web/components` | Exists | Keep |
| `apps/web/lib` | Exists | Keep; later move shared API contracts to package |
| `services/api-server/agents` | Exists | Keep, but gradually split large agents into domain modules |
| `services/api-server/schemas` | Exists | Expand schema coverage for project, parcel, regulation, cashflow, PF, and DB DTOs |
| `services/api-server/calculators` | Missing | Add wrappers that re-export stable functions from `core` before moving implementation |
| `services/api-server/integrations` | Missing | Add sidecar/client adapters for VWorld, MOLEG/Korean Law MCP, Valuemap, and public data |
| `services/api-server/reports` | Missing | Move report generation behind wrappers from `agents/report_agent.py` and `agents/report_export_agent.py` |
| `services/api-server/db` | Missing | Move `storage.py` into DB package after introducing stable repository functions |
| `packages/shared-types` | Missing | Add later for generated or hand-maintained frontend/backend shared types |
| `packages/realytics-contracts` | Missing | Add later for canonical JSON schemas, workbook contracts, and API contracts |
| Root `docker-compose.yml` | Missing | Add after API/web/dev services are stable enough for root orchestration |
| `docs/feasibility_schema.md` | Missing | Can be generated from current Feasibility Study v2 docs |
| `docs/pf_cashflow_logic.md` | Missing | Can summarize Cash Flow Engine and Calculation Scenario Engine docs |
| `docs/agent_design.md` | Missing | Can summarize all agent boundaries and orchestration flow |

## 5. Recommended Migration Principles

1. Do not rename or move heavily imported files without compatibility wrappers.
2. Keep existing API responses stable while moving internals.
3. Move one domain at a time and run targeted tests after each move.
4. Prefer wrapper modules first, implementation moves second.
5. Update docs, backlog, tests, and import paths together.
6. Keep runtime data, uploaded documents, and SQLite files out of Git.
7. Keep calculation code deterministic and auditable.
8. Keep agent orchestration separate from pure calculation functions.

## 6. Migration Plan

### Phase A: Documentation Alignment

Status: In progress.

Deliverables:

- `docs/architecture.md`
- README link to architecture document
- Backlog item for structure migration
- Future docs:
  - `docs/feasibility_schema.md`
  - `docs/pf_cashflow_logic.md`
  - `docs/agent_design.md`

### Phase B: Calculator Package Wrappers

Goal:

Introduce `services/api-server/calculators` without breaking existing imports.

Planned files:

```text
services/api-server/calculators/
|-- feasibility_calculator.py
|-- cashflow_calculator.py
|-- ltv_ltc_calculator.py
|-- tranche_waterfall.py
`-- sensitivity.py
```

Initial implementation should re-export or thinly wrap functions from `core.calculations`.

Example:

```python
from core.calculations import calculate_feasibility_metrics
```

After wrappers are stable, routers and agents can gradually import from `calculators`.

### Phase C: Reports Package Wrappers

Goal:

Introduce `services/api-server/reports` while preserving current report agents.

Planned files:

```text
services/api-server/reports/
|-- ic_memo_generator.py
|-- pf_review_report_generator.py
`-- feasibility_report_generator.py
```

Initial mapping:

| Target File | Current Source |
|---|---|
| `pf_review_report_generator.py` | `agents/report_agent.py` |
| `ic_memo_generator.py` | Phase 11 report fragments and future lender memo generator |
| `feasibility_report_generator.py` | Development Feasibility Agent outputs |

### Phase D: Integrations Package

Goal:

Centralize external data clients and sidecar adapters.

Planned files:

```text
services/api-server/integrations/
|-- vworld_client.py
|-- moleg_client.py
|-- valuemap_client.py
|-- public_data_client.py
`-- korean_law_mcp_client.py
```

Current related code:

| Current Location | Future Direction |
|---|---|
| `schemas/valuemap.py` | Keep schema, add `integrations/valuemap_client.py` |
| `routers/valuemap.py` | Use Valuemap client wrapper |
| `agents/korean_law_context.py` | Keep normalization, add MCP client wrapper if direct client automation is needed |
| `docs/integrations/korean-law-mcp.md` | Keep sidecar deployment guide |

### Phase E: DB Package

Goal:

Move persistence behind a clearer DB package.

Planned files:

```text
services/api-server/db/
|-- models.py
|-- session.py
|-- repository.py
`-- migrations/
```

Current source:

- `services/api-server/storage.py`

Migration rule:

First create `db/repository.py` wrappers around `load_state` and `save_state`. Only later move implementation.

### Phase F: Shared Packages

Goal:

Separate shared contracts from application code.

Planned packages:

```text
packages/shared-types/
packages/realytics-contracts/
```

Recommended contents:

| Package | Contents |
|---|---|
| `shared-types` | TypeScript types consumed by web app |
| `realytics-contracts` | JSON schemas, workbook contracts, API payload contracts |

Do this after backend schema stabilizes, especially Feasibility Study v2, Cash Flow Run Request, Marketability Analysis v2, Land Ledger Import v1, and Phase 11 Report v1.

## 7. Agent Boundary Model

Current agent boundaries should remain conceptually stable:

```text
Parser Agent
  -> Development Feasibility Agent
     -> FeasibilityStudyV2
     -> CashFlowRunRequest
     -> Cash Flow Engine
     -> Calculation Scenario Engine
  -> Market Evidence Agent
     -> Marketability Agent
  -> Land Ledger Agent
  -> Legal / Permit Review Agent
  -> Risk Assessment
  -> Phase 11 Report Agent
```

Important rule:

Development Feasibility Agent is an orchestrator for feasibility readiness. It should not directly own every calculation. Cash Flow, Waterfall, Unsold LTV, and Tranche Recovery should remain specialized engines.

## 8. Immediate Next Steps

Recommended next coding sequence:

1. Add `services/api-server/calculators` wrappers.
2. Update a small number of tests to import calculators directly.
3. Keep old `core` imports working.
4. Add `docs/pf_cashflow_logic.md` from the Cash Flow Engine and Calculation Scenario Engine docs.
5. Add `docs/agent_design.md` from the current agent boundaries.
6. Add `services/api-server/reports` wrappers.
7. Add `services/api-server/db` repository wrappers.

The safest first code move is the calculator wrapper package because it is low risk and directly matches the target architecture.
