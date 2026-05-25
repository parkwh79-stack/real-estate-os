# Data Quality Agent

Updated: 2026-05-22

## Purpose

Data Quality Agent is the numeric conflict detection layer for REALYTICS x PLATFORM9.

Its job is to detect whether project data can be trusted before agents proceed to credit review, report generation, approval, or historical comparison.

It answers:

> Do the numbers from documents, Excel, project input, market evidence, land ledger, and PF structure reconcile?

## Current Schema

Current schema version:

```text
realytics-data-quality-v1
```

Current output:

| Field | Description |
|---|---|
| `status` | `clear`, `needs_review`, or `blocking` |
| `checks` | Deterministic data quality checks |
| `blockingIssues` | Issues that must be resolved before final PF approval |
| `dataLimitations` | Warnings or missing data that must remain visible |
| `nextActions` | Recommended analyst or agent follow-up |

## Implemented Checks

Baseline checks include:

| Check | Purpose |
|---|---|
| Project name consistency | Detect project name conflicts across source records |
| Land area consistency | Compare project land area, architectural plan area, land ledger area, and Land Agent area |
| GFA consistency | Compare architectural and program GFA where available |
| Sales revenue consistency | Compare feasibility revenue, developer feasibility revenue, product mix revenue, document product mix revenue, and Marketability revenue |
| PF amount consistency | Compare PF tranche sum, expected PF amount, developer feasibility PF amount, and report PF amount where available |
| Unit count consistency | Compare architectural unit count, product mix units, and document product mix units |
| SQM/PY conversion consistency | Detect incorrect sqm to pyeong conversion in product mix records |
| VAT basis consistency | Detect missing or mixed VAT included/excluded basis |
| Schedule sequence consistency | Detect date order issues in major project schedule |
| Tranche structure quality | Detect missing tranche amounts and duplicate tranche names |

## Orchestration Position

Data Quality Agent runs after Marketability Agent and before Cross-Agent Reconciliation.

```text
Market Evidence Agent
  -> Marketability Agent
  -> Data Quality Agent
  -> Cross-Agent Reconciliation Gate
  -> Legal Review
  -> Risk / Report / Evidence Graph
```

## Quality Gate Rule

If Data Quality Agent returns `blocking`, the orchestration quality gate should not pass.

Warnings and missing data do not automatically fail the run, but they must remain visible in:

- `dataQualityReview`
- `reconciliationGate`
- `evidenceGraph`
- `projectSnapshot`
- Phase 11 report limitations in future hardening

## Current Implementation

Implemented files:

- `services/api-server/agents/data_quality_agent.py`
- `services/api-server/agents/orchestration_agent.py`
- `services/api-server/tests/test_data_quality_agent.py`

Saved project snapshots now capture `dataQualityReview`.

## Important Rule

Data Quality Agent must not silently choose the most convenient value. If authoritative values conflict, it must flag the conflict and require either:

1. source correction,
2. rerun of upstream parsing/calculation,
3. explicit analyst assumption override,
4. or human approval with limitation retained.

## Next Steps

1. Add field-level source locators for non-market parser fields.
2. Add approved assumption override records.
3. Add UI panel for data quality checks.
4. Feed blocking issues directly into Risk Register Agent.
5. Add Excel named-range and cell-address references to each numeric conflict.
