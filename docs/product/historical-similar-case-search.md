# Historical Similar Case Search

Updated: 2026-05-25

## Purpose

Historical Similar Case Search compares the current project snapshot against prior saved PF review snapshots.

Its purpose is to answer:

> Which prior PF review cases looked similar, and how were similar risks handled?

This is the first step toward a historical review database and later similar-case RAG. The current implementation is deterministic and uses stored `realytics-project-snapshot-v1` records. It does not yet use vector embeddings or post-closing performance outcomes.

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
| Risk Register rows | risk code/category/severity overlap |

## Output

Each similar case returns:

| Field | Description |
|---|---|
| `similarityScore` | Weighted deterministic similarity score |
| `scoreBreakdown` | Per-dimension score, weight, method, and metadata |
| `matchedDimensions` | Strong matching dimensions |
| `riskMatches` | Matching Risk Register rows and historical treatment |
| `decisionOutcome` | Historical Credit Review decision |
| `approvalConditions` | Historical approval-condition candidates |
| `handlingSummary` | Matched risk count, review statuses, mitigants, and conditions |
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

## API

```text
POST /projects/{project_id}/snapshots/similar
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
- `services/api-server/tests/test_similar_case_search.py`

## Current Limits

- Baseline scoring is deterministic and field-based.
- It depends on snapshots having good `caseComparisonSeed`, `riskRegister`, and `creditReviewDecision` artifacts.
- It does not yet normalize Korean locations by legal dong/PNU hierarchy.
- It does not yet include actual outcome data such as realized sales rate, default, extension, or loss.
- It does not yet use vector search over full reports or RAG over similar memo paragraphs.

## Next Steps

1. Add exact PNU/legal-dong/location hierarchy matching.
2. Add actual outcome fields to snapshots.
3. Persist reviewed risk treatment as first-class historical risk records.
4. Add vector index over report sections and Evidence Graph claims.
5. Feed similar-case results into Credit Review Agent and Phase 11 report risk memo.
