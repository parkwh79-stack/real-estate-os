# Next Work Audit

Updated: 2026-07-03  
Scope: REALYTICS OS / PLATFORM9 monorepo

This audit collects the remaining implementation gaps found by scanning backlog, product docs, project pages, API routes, agents, and known mock/stub markers.

## 1. Immediate P0 Work

### 1.1 Project Page Data Contract Cleanup

Current status:

- Common project memory strip exists.
- `input-package` reads `GET /projects/{id}/workspace/context`.
- Many project pages still read their own endpoint directly or contain old display logic.

Findings:

- `apps/web/app/project/[id]/cashflow/page.tsx` is still a hard-coded mock page.
- `apps/web/app/project/[id]/land/page.tsx` has many mojibake markers in display text and should be rebuilt against the shared page context.
- `apps/web/app/project/[id]/marketability/page.tsx` is connected to live analysis, but display text still has mojibake markers and needs source/evidence clarity.

Required next implementation:

1. Define `workspace/context` page contracts for `land`, `marketability`, `cashflow`, `pf_structure`, `risk`, `report`, and `evidence`.
2. Replace mock Cash Flow page with persisted scenario/cash-flow run data.
3. Rebuild Land and Marketability page labels using controlled Korean financial terminology.
4. Ensure every page shows the same project name, uploaded files, evidence counts, missing inputs, and approval readiness status.

### 1.2 Cash Flow Page Must Stop Showing Hard-coded Financial Results

Current status:

- `cashflow/page.tsx` shows fixed values:
  - Total Months: 36
  - Max Minus: -15,000,000,000
  - PF Drawdown: 70,000,000,000
  - PF Repayment: 70,000,000,000

Risk:

- This is a production-critical area. Hard-coded financial metrics must not appear as real project analysis.

Required next implementation:

1. Read latest cash-flow/scenario artifact from project storage.
2. If no artifact exists, show an honest empty state and a button to run Feasibility-Cashflow Pod.
3. Show Max Minus, PF draw, repayment, waterfall, and monthly rows only when backed by a stored calculation artifact.
4. Link every displayed metric to calculation run ID and source evidence/assumption IDs where available.

### 1.3 Multi-page Shared Deal State Graph Adoption

Current status:

- Shared Deal State Graph persistence exists.
- Event Bus recalculation persistence exists.
- Chief Deal Room reads recalculation queue.

Gap:

- The graph is not yet the source of truth for all project pages.

Required next implementation:

1. Use `workspace/context?page=...` as the primary read path for project pages.
2. Add page-specific context blocks:
   - land evidence
   - market evidence
   - cash-flow metrics
   - PF structure
   - risk register
   - report exports
3. Keep domain endpoints for action/run workflows, but use the shared context for page hydration.

## 2. Orchestration / Agent Gaps

### 2.1 Pod AI Reviewer Layer

Current status:

- Six Chief Pods can produce deterministic artifacts.
- Not every Pod has a dedicated AI reviewer brain.

Required next implementation:

1. Add `PodAIReview` schema.
2. Persist `pod_ai_reviews` in JSON/SQLite/Postgres path.
3. Implement generic `PodAIReviewerAgent`.
4. Apply first to Land-Legal, Market-Product, and PF-Credit Pods.
5. Display review result in Chief Deal Room and AI Risk Committee.

### 2.2 Cost-Schedule Pod Depth

Current status:

- Pod exists in orchestration structure.
- Dedicated Construction Cost, Indirect Cost, Schedule, VE/Spec, Contractor Risk artifacts are not deep enough.

Required next implementation:

1. Create Cost-Schedule artifact schema.
2. Extract construction cost, indirect cost, schedule, and contractor risk assumptions.
3. Emit recalculation events when cost/schedule changes affect feasibility/cash-flow/PF.

### 2.3 Product Planning Agent

Current status:

- Product planning exists in workflow concepts.
- Standalone engine is not implemented.

Required next implementation:

1. Use land/regulation/market evidence to generate product options.
2. Return GFA program, unit mix, saleable/rentable area, parking assumptions, and recommended product.
3. Feed result into Development Feasibility and Marketability.

### 2.4 Covenant & EOD Agent

Current status:

- Covenant/EOD concepts exist in PF/credit artifacts.
- Standalone schema/API is not implemented.

Required next implementation:

1. Define covenant triggers:
   - sales rate
   - progress rate
   - LTV/LTC/DSCR
   - responsible completion
   - discount sale
   - waterfall/cash sweep
2. Generate covenant/EOD draft with source evidence and risk mapping.

## 3. Data / Evidence / Calculation Gaps

### 3.1 Evidence / Assumption / Metric / RiskIssue First-class Persistence

Current status:

- Evidence Graph exists.
- Risk Register exists.
- Many metrics/assumptions still live inside artifacts.

Required next implementation:

1. Promote evidence, assumptions, metrics, and risk issues into first-class storage.
2. Link each metric to source locator, calculation run, and human review state.
3. Use these records across reports and project pages.

### 3.2 CalculationRun Registry

Current status:

- Calculation Scenario Engine exists.
- Not all calculation outputs are versioned through a unified registry.

Required next implementation:

1. Store `calculationRunId`, engine name, engine version, input evidence IDs, assumption IDs, output metric IDs, scenario, status, and createdAt.
2. Use for feasibility, cash flow, waterfall, LTV/LTC/DSCR, Exit Sale Rate, REIT distribution, and fund return calculations.

### 3.3 Validation Agent Hardening

Current status:

- Data quality checks exist.
- Excel workbook validation exists.

Gap:

- Formula/source audit is not yet a hard gate across all financial outputs.

Required next implementation:

1. Recalculate formula/cell/source consistency for feasibility, cash flow, waterfall, tranche, and report export.
2. Block report-grade output when financial metrics lack source locator or calculation run.

## 4. Marketability Gaps

Current status:

- Manual comparables and live public transaction integration exist.
- Marketability Agent can score and generate report text.

Remaining gaps:

1. Subscription competition and contract-rate connector.
2. Unsold inventory statistics.
3. Absorption model v2.
4. Product-specific scoring weights.
5. Rent-yield based pricing for officetel, retail, KIC, and serviced residence.
6. Marketability map view for comparable supply and presale cases.

## 5. Integration / Security Gaps

### 5.1 Live Data Connectors

Current status:

- VWorld and MOLIT paths exist, but live verification depends on keys/config.
- Valuemap partner adapter is config-driven with labelled mock fallback.
- Registry evidence should remain manual/licensed evidence-first.

Required next implementation:

1. Finish configuration status screen for VWorld, data.go.kr, Valuemap.
2. Add clear user-facing diagnostics when a source is unavailable.
3. Never fabricate values when live connector is unavailable.

### 5.2 Production Auth / Tenant / RBAC

Current status:

- Baseline tenant/user/session/audit exists.
- Social login has pilot fallback.

Required next implementation:

1. OIDC JWKS/RS256 verification.
2. Tenant-level project ownership enforcement across all endpoints.
3. Secret manager integration for API keys.
4. Role-based permission enforcement for approve/waive/override/export/admin.

## 6. Architecture / Packaging Gaps

Required next work:

1. Add calculator package wrappers.
2. Add report and DB compatibility wrappers.
3. Add shared contracts packages after schemas stabilize.
4. Continue router thinning:
   - runtime archive operations
   - project lookup in all endpoints
   - async job state

## 7. Recommended Next Coding Order

1. Replace `cashflow/page.tsx` mock with Shared Deal State / stored calculation artifact view.
2. Convert `land/page.tsx` to `workspace/context?page=land` and clean Korean terminology.
3. Convert `marketability/page.tsx` display labels and source diagnostics.
4. Implement first-class `CalculationRun` registry.
5. Implement first-class Evidence/Assumption/Metric/RiskIssue storage.
6. Add Pod AI Review persistence and Land-Legal Pod reviewer.
7. Add Cost-Schedule Pod artifact depth.
8. Add Product Planning Agent.
9. Add Covenant/EOD Agent.
10. Harden tenant/RBAC/secret manager for pilot users.

