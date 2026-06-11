# Historical Similar Case Search

Updated: 2026-05-25

## Purpose

Historical Similar Case Search compares the current project snapshot against prior saved PF review snapshots.

Its purpose is to answer:

> Which prior PF review cases looked similar, and how were similar risks handled?

This is the first step toward a historical review database and later similar-case RAG. The current implementation is deterministic and uses stored `realytics-project-snapshot-v1` records. It now returns post-approval `historicalOutcome` data when a prior case has actual performance fields attached. It does not yet use vector embeddings.

## Current Schema

```text
realytics-similar-case-search-v1
```

## Current Inputs

The search uses one query snapshot:

- explicit `snapshotId`, or
- latest saved snapshot for the project.

The search compares against all stored project snapshots except the query snapshot. By default, snapshots from the same project are excluded unless `include_same_project` is true.

## Compared Fields

The baseline scorer uses `caseComparisonSeed` fields:

| Field | Method |
|---|---|
| `projectType` | exact match |
| `location` | token overlap |
| `landAreaSqm` | numeric closeness |
| `totalRevenue` | numeric closeness |
| `totalCost` | numeric closeness |
| `landCost` | numeric closeness |
| `pfAmount` | numeric closeness |
| `ltv` | numeric closeness |
| `ltc` | numeric closeness |
| `exitSaleRate` | numeric closeness |
| `marketabilityGrade` | exact match |
| `landReadinessStatus` | exact match |
| `reconciliationStatus` | exact match |
| Risk Register rows | exact risk-code overlap when the query risk has `code`; category/severity fallback only when the query risk has no code |

## Output

Each similar case returns:

| Field | Description |
|---|---|
| `similarityScore` | Weighted deterministic similarity score |
| `scoreBreakdown` | Per-dimension score, weight, method, and metadata |
| `matchedDimensions` | Strong matching dimensions |
| `riskMatches` | Matching Risk Register rows and historical treatment |
| `decisionOutcome` | Historical Credit Review decision |
| `historicalOutcome` | Actual sales, discount, repayment, EOD, loss, and recovery outcome when available |
| `approvalConditions` | Historical approval-condition candidates |
| `handlingSummary` | Matched risk count, review statuses, mitigants, conditions, and outcome summary |
| `caseComparisonSeed` | Candidate snapshot seed values |

## Risk Handling Retrieval

For each matching risk, the search returns:

- current query risk ID and code,
- matched historical risk ID and code,
- severity and category,
- historical `reviewStatus`,
- historical mitigant,
- matching approval condition when the condition references the risk ID,
- `evidenceGraphLinks` if available.

This allows the Credit Review Agent or reviewer to ask:

```text
This case has PRICE_PREMIUM_HIGH.
How did prior similar cases handle PRICE_PREMIUM_HIGH?
Was it waived, approved with condition, resolved, or sent back for revision?
```

Risk matching is intentionally conservative. If a query Risk Register row has an explicit `code`, a historical risk must match the same code to be treated as the same risk. Category/severity fallback is only used for legacy or incomplete query risks that do not have a code. This prevents unrelated high-severity risks in the same category from being overcounted as historical treatment for the current risk.

## Actual Outcome Retrieval

When a candidate snapshot has `historicalOutcome`, similar-case results include:

- actual sales rate,
- actual discount rate,
- repayment completion and date,
- extension/default/EOD history,
- loss occurrence and loss amount,
- total and tranche-level recovery rates,
- actual exit source,
- source metadata and source locators.

This lets reviewers ask:

```text
The prior case waived PRICE_PREMIUM_HIGH.
Did that case repay, default, require discounting, or realize loss?
```

Outcome data is deliberately stored as a post-approval block. It does not rewrite the original credit decision; it creates back-testing data for future underwriting.

## Web Workflow

The project orchestration page includes a `Historical Outcome` panel. A reviewer can:

1. Select a saved `realytics-project-snapshot-v1` record.
2. Enter actual sales rate, realized discount rate, repayment completion, extension/EOD/loss status, loss amount, recovery rates, and actual exit source.
3. Add source metadata and source locators for repayment statements, sales reports, Excel cells, API rows, or uploaded evidence documents.
4. Save the outcome through `POST /projects/{project_id}/snapshots/{snapshot_id}/outcome`.

This makes the pilot workflow usable without scripts while preserving the same server-side outcome schema for later database migration.

The same orchestration page now includes a `Historical Similar Cases` panel. A reviewer can:

1. Select the query snapshot.
2. Set result limit and minimum similarity score.
3. Include same-project snapshots for pilot testing when there are not enough historical projects yet.
4. Search `POST /projects/{project_id}/snapshots/similar`.
5. Review similarity score, matched dimensions, exact risk-code matches, historical treatment status, approval conditions, Credit Review decision, and actual repayment/EOD/loss/recovery outcome fields.

## API

```text
POST /projects/{project_id}/snapshots/similar
POST /projects/{project_id}/snapshots/{snapshot_id}/outcome
```

Example request:

```json
{
  "snapshotId": "snapshot-run-123",
  "limit": 5,
  "minScore": 0.3,
  "includeSameProject": false
}
```

## Implemented Files

- `services/api-server/use_cases/project_snapshots.py`
- `services/api-server/db/repositories/project_snapshot_repository.py`
- `services/api-server/routers/projects.py`
- `apps/web/app/project/[id]/orchestration/page.tsx`
- `services/api-server/tests/test_similar_case_search.py`

## Current Limits

- Baseline scoring is deterministic and field-based.
- It depends on snapshots having good `caseComparisonSeed`, `riskRegister`, and `creditReviewDecision` artifacts.
- It does not yet normalize Korean locations by legal dong/PNU hierarchy.
- Outcome analysis depends on users or integrations attaching actual post-approval outcome data.
- It does not yet use vector search over full reports or RAG over similar memo paragraphs.

## Related Snapshot Diff

The same snapshot infrastructure now supports:

```text
realytics-snapshot-diff-v1
```

Snapshot Diff compares two saved snapshots from the same project and returns changed assumptions, Risk Register changes, Credit Review decision changes, artifact changes, report-section changes, material-change flags, and a re-review recommendation.

## Next Steps

1. Add exact PNU/legal-dong/location hierarchy matching.
2. Persist reviewed risk treatment as first-class historical risk records.
3. Add vector index over report sections and Evidence Graph claims.
4. Feed similar-case, snapshot-diff, and outcome data into Credit Review Agent and Phase 11 report risk memo.
5. Promote material snapshot diff findings into formal review tasks.
