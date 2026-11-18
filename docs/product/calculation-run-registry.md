# Calculation Run Registry

Updated: 2026-11-09

Project: REALYTICS x PLATFORM9

## Saved working-input budget and timing reviews (2026-11-09)

`POST /projects/{id}/cash-flow/run` accepts the explicit
`workingInputReview: {expectedReviewHash}` budget mode in addition to its existing
project-default and reviewed-monthly modes. It rereads stored working inputs,
rejects stale review hashes and mixed overrides, and stores the actual request
and result as a new analysis-only CalculationRun. An explicitly reviewed aggregate
PF amount can be used as an analysis limit only in this mode when no detailed
financing exists. It never creates tranche terms, a monthly policy or approval.
Project-default and canonical/original paths retain their existing behavior.

User-entered `terms.cashFlowTiming` is stored through the existing review CAS,
bound to the source period hash and captured with `sourceMetadata.inputReview`.
Only complete, consistent assumptions enter a new reviewed-monthly request;
partial or stale assumptions do not block independent budget calculations.

Saved comparison/report snapshots retain both complete calculations. Review-only
execution hashes/reasons are distinguished from actual business input changes;
changed dates, cost rows and collection amounts still count. Principal repaid is
reported only when the saved monthly engine actually calculated it, and remains
unavailable in budget results. Historical runs and reports are not rewritten.

## Goal

Financial calculations must be deterministic, auditable, and traceable.

REALYTICS OS now stores calculation outputs as first-class `CalculationRun` records instead of leaving them only inside report or orchestration artifacts.

## What Is Stored

Each calculation run stores:

- `calculationRunId`
- `engineKey`
- `engineVersion`
- `status`
- `inputEvidenceIds`
- `assumptionIds`
- `outputMetrics`
- `riskIssues`
- `artifactRef`
- `sourceLocator`
- `inputDigest`
- `createdAt`
- `updatedAt`

## APIs

```text
GET /projects/{project_id}/calculation-runs
GET /projects/{project_id}/calculation-runs?engine_key=pf_metrics
GET /projects/{project_id}/deal-state-records
GET /projects/{project_id}/deal-state-records?record_type=metric
GET /projects/{project_id}/deal-state-records?calculation_run_id={id}
```

## Persisted Engines

The following API paths persist calculation runs:

| API | Engine Key |
|---|---|
| `POST /projects/{project_id}/feasibility/calculate` | `feasibility` |
| `POST /projects/{project_id}/cashflow/calculate` | `cashflow` |
| `POST /projects/{project_id}/cash-flow/run` | `cash_flow_engine` |
| `POST /projects/{project_id}/pf/structure` | `pf_metrics` |
| `POST /projects/{project_id}/returns/calculate` | `return_metrics` |
| `POST /projects/{project_id}/scenarios/waterfall` | `waterfall` |
| `POST /projects/{project_id}/scenarios/unsold-ltv-matrix` | `unsold_ltv` |
| `POST /projects/{project_id}/scenarios/recovery` | `waterfall` + `unsold_ltv` + `tranche_recovery` (full set) |
| `POST /projects/{project_id}/stress-test/run` | `stress_test` (discount-stress stages 10%/20% 포함) |

Blocker traceability: `SENIOR_SHORTFALL`/`WATERFALL_SHORTFALL`/`STRESS_SENIOR_SHORTFALL`
flags are routed to the `waterfall` run's `riskIssues`; `UNSOLD_LTV_BREACH`/`UNSOLD_LTV_HIGH`
to the `unsold_ltv` run; `TRIGGER_REQUIRED` to `tranche_recovery`. Each blocker therefore
links to a durable `calculationRunId` and its `deal_state_records` rows.

Operational note: `POST /projects/{project_id}/stress-test/run` now persists under
the report-grade engine key `discount_stress` with engine version
`discount_stress_v1`. Older notes may refer to the workflow name
`stress_test`, but the durable `calculation_runs.engineKey` is
`discount_stress`.

Scenario-engine risk rows preserve their explicit source locator when available:

- Waterfall shortfall risks keep the scenario waterfall row locator.
- Unsold LTV breach/high risks keep the matrix-cell locator.
- Calculation-run metrics and risk issues inherit the run `sourceLocator` when a more specific locator is not available.

This is required for pilot blockers such as `SENIOR_SHORTFALL` and `UNSOLD_LTV_BREACH` to trace back to the exact calculation row or matrix cell.

## Automatic Deal-State Materialization

The CalculationRun registry automatically materializes selected outputs into
`deal_state_records` so Evidence, Risk, Report Studio, and Approval Readiness
screens can read one shared ledger.

| Engine | Calculation Output | Deal State Record |
|---|---|---|
| `waterfall` | Senior tranche shortfall risk flag | `risk_issue:SENIOR_SHORTFALL` |
| `unsold_ltv` | Unsold LTV breach risk flag | `risk_issue:UNSOLD_LTV_BREACH` |
| `tranche_recovery` | Conservative senior recovery ratio across the recovery matrix | `metric:tranche_recovery.senior` |
| `discount_stress` | 10% discount stress sale-rate/Exit assumption | `metric:discount_stress.discount10.exitSaleRate` |

The tranche recovery metric is derived from already-computed recovery matrix
rows by taking the minimum observed senior recovery ratio. The discount-stress
Exit sale rate is derived from the scenario row whose `discountRate` is 10%.
No new financial calculation is invented during materialization.

## First-Class Deal State Records

Calculation runs also materialize records into:

```text
deal_state_records
```

Supported `recordType` values:

- `metric`
- `risk_issue`
- `assumption`
- `evidence`

This allows reports, Risk Register, Evidence Graph, and audit screens to link back to exact calculation runs.

## Artifact Materialization

The same `deal_state_records` registry now receives first-class records from non-calculation artifacts as well.

| Source Artifact | Materialized Record Types | Source Type |
|---|---|---|
| Risk Register | `risk_issue` | `risk_register` |
| Evidence Graph | `evidence`, `metric`, `risk_issue`, `assumption` | `evidence_graph` |
| Chief Pod Artifact | `metric`, `risk_issue`, `evidence`, `assumption` | `chief_pod_artifact` |

This means a page, report, or reviewer tool should no longer scrape nested artifact JSON first. The preferred read path is:

```text
GET /projects/{project_id}/deal-state-records
```

Use filters when the screen needs a narrower view:

```text
GET /projects/{project_id}/deal-state-records?record_type=risk_issue
GET /projects/{project_id}/deal-state-records?record_type=evidence
GET /projects/{project_id}/deal-state-records?record_type=metric
GET /projects/{project_id}/deal-state-records?record_type=assumption
```

Nested artifacts remain the detailed source of truth for their original workflow output, but `deal_state_records` is the cross-page operating registry used by Report Studio, Evidence Auditor, Risk Register, and Chief Deal Room.

## Production Rule

LLM outputs must not replace deterministic calculation outputs.

LLMs may explain, challenge, classify, or draft narrative around a calculation, but the final numeric value must reference:

```text
calculationRunId + engineKey + engineVersion + inputEvidenceIds + assumptionIds + sourceLocator
```

## Next Hardening

- ~~Persist scenario/stress engines with report-grade keys and risk source locators.~~ (Done 2026-07-06 — waterfall / unsold_ltv / tranche_recovery / discount_stress)

- ~~Persist Waterfall and Unsold LTV scenario runs through the same registry.~~ (Done 2026-07-03 — waterfall / unsold_ltv / tranche_recovery / stress_test)
- ~~Add UI history panel for calculation run comparison.~~ (Done 2026-07-06 — `/project/{id}/risk` 하단 계산 실행 이력·비교 패널: 엔진별 run 목록 + 두 run 지표 delta·blocker diff)
- ~~Link report paragraphs directly to `deal_state_records`.~~ (Done — `build_paragraph_evidence_links` runs at report creation (`report_generation.py`) and saves `paragraphEvidenceLinks` (section→record map with per-type record ids + source-locator coverage) onto the report; `/project/{id}/report` surfaces it as the 근거 원장. Verified live on 585: 49 deal-state records → 20 sections, 15 linked, 31 unique records, 15 source-locator-ready.)
- ~~Add reviewer status and source locator completeness filters to `deal_state_records`.~~ (Done 2026-07-06 — `GET /projects/{id}/deal-state-records` supports `review_status` (incl. `unreviewed`) and `missing_locator` (true=근거 위치 부족만, false=있는 것만) plus a scope-wide `completenessSummary` {scopeCount, withSourceLocatorCount, missingSourceLocatorCount, reviewed/unreviewedCount}. Report page's 근거 원장 has a clickable "근거 위치 부족" filter toggle.)
- ~~Add PostgreSQL migration file for enterprise deployment.~~ (Done 2026-07-06 — `db/migrations/0001_initial_schema.sql` faithfully mirrors all SQLite stores in PostgreSQL/PostGIS (JSONB + TIMESTAMPTZ + composite PKs + indexes + GIN); `db/postgres.py.schema_ddl()` loads it as the single source of truth. See `docs/product/deployment-plan.md` for the migration/wiring steps.)
