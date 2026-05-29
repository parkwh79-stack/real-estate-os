# Server-Side Project Snapshots

Updated: 2026-05-21

## Purpose

Project snapshots preserve the exact project state used by each PF review orchestration run. They are the first step toward institutional memory: future agents should be able to compare a new project against prior reviewed projects, assumptions, evidence, risks, approval conditions, and outcomes.

Snapshots are not editable working records. They are audit and comparison records.

## Why This Matters

REALYTICS should not only calculate one project at a time. It should remember:

- Which assumptions were used
- Which documents and evidence supported those assumptions
- Which agents produced which outputs
- Which handoffs required review
- Which reconciliation warnings existed
- Which report and approval package was generated
- Which metrics can later be used for similar-case search

This supports future:

- Similar PF review case retrieval
- Internal benchmarking
- Back-testing against actual sales and repayment outcomes
- Evidence graph construction
- Risk register and credit policy learning

## Snapshot Schema

Current schema version:

```text
realytics-project-snapshot-v1
```

Each snapshot includes:

| Field | Purpose |
|---|---|
| `snapshotId` | Stable snapshot ID, currently `snapshot-{run_id}` |
| `projectId` | Project ID |
| `runId` | Source orchestration run |
| `workflow` | Source workflow, usually `pf-review` |
| `status` | Run status at snapshot creation |
| `canonicalProject` | Selected project fields used by agents |
| `orchestration` | Step keys, timing, errors, and approval state |
| `agentProgress` | Per-agent progress ledger |
| `handoffs` | Typed agent handoffs with payloads, evidence, blockers, limitations, and review state |
| `agentArtifacts` | Key agent outputs needed for future comparison |
| `reconciliation` | Cross-agent reconciliation gate output |
| `quality` | Orchestration quality gate output |
| `report` | Phase 11 report metadata and section keys |
| `caseComparisonSeed` | Lightweight fields for future similar-case search |

## Captured Agent Artifacts

The baseline snapshot captures:

- `landAgentReview`
- `developerFeasibility`
- `feasibility`
- `pfStructure`
- `marketEvidenceReview`
- `marketabilityAnalysis`
- `dataQualityReview`
- `reconciliationGate`
- `legalPermitReview`
- `riskAssessment`
- `riskRegister`
- `creditReviewDecision`
- `scenariosEvaluation`
- `evidenceGraph`

The full Phase 11 report body is not duplicated inside the snapshot. Instead, the snapshot stores report metadata and section keys while the report repository remains the source for report content and exports.

## Case Comparison Seed

The snapshot includes a normalized seed for later comparison:

```json
{
  "projectType": "mixed_use",
  "location": "Seoul...",
  "landAreaSqm": 6240.5,
  "totalRevenue": 158500000000,
  "totalCost": 128800000000,
  "landCost": 35500000000,
  "pfAmount": 90000000000,
  "ltv": 0.56,
  "ltc": 0.70,
  "exitSaleRate": 0.57,
  "marketabilityGrade": "B",
  "marketabilityScore": 76.5,
  "landReadinessStatus": "ready_for_pf_review",
  "reconciliationStatus": "needs_review"
}
```

This is intentionally small. Later, a Similar Case Agent can index these seeds before reading full snapshots.

## API

Snapshots are created automatically when a PF review run is saved.

```text
GET /projects/{project_id}/snapshots
GET /projects/{project_id}/snapshots/{snapshot_id}
POST /projects/{project_id}/snapshots/similar
POST /projects/{project_id}/snapshots/diff
```

`POST /snapshots/similar` returns `realytics-similar-case-search-v1`, a deterministic comparison of stored snapshots that includes score breakdowns, matched dimensions, Risk Register overlap, historical review status, mitigants, approval conditions, and Credit Review decision outcome.

`POST /snapshots/diff` returns `realytics-snapshot-diff-v1`, a same-project comparison that highlights changed seed metrics, added/removed/changed risks, Credit Review decision changes, artifact changes, report-section changes, and re-review recommendations.

## Current Implementation

Implemented files:

- `services/api-server/db/repositories/project_snapshot_repository.py`
- `services/api-server/use_cases/project_snapshots.py`
- `services/api-server/storage.py`
- `services/api-server/routers/projects.py`
- `services/api-server/db/repositories/orchestration_repository.py`

The storage layer supports both JSON and SQLite MVP persistence through the `project_snapshots` state key and `project_snapshots` SQLite table.

## Agent Rule

Every major PF review orchestration run should create a server-side project snapshot before final human approval. Agents should use snapshots as immutable historical case records and must not silently mutate prior snapshots.

## Next Steps

1. Add actual outcome fields such as achieved sales rate, realized discount, repayment completion, and EOD events.
2. Promote reviewed risk treatments into first-class historical risk records.
3. Connect full report sentence IDs to Evidence Graph and similar-case retrieval.
4. Add UI panels for snapshot diff and similar-case search.
