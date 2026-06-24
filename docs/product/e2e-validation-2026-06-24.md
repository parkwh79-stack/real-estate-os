# End-to-End Validation — 2026-06-24

Single integrated run of the full PF review pipeline plus every engine added in
this session, on the Daegu mixed-use fixture (discount-trigger project). Goal:
confirm the new capabilities work together end to end, not just in unit tests.

## Result

| Check | Outcome |
|---|---|
| Orchestration steps | **26 / 26 completed**, status `completed`, **0 errors** |
| Iterative consensus (`consensusResolution`) | present — status `partial`, coordinator `deterministic`, 1 round. Remaining conflicts are evidence-required (official price / registry), correctly surfaced as unresolved rather than auto-cleared |
| Term sheet stages | `bridge` + `main_pf` both generated |
| IC Memo | 여신의견 **조건부 승인 가능**, applied policy **표준 여신정책** |
| Developability score | grade **B**, 73.3 |
| Credit review | `policyProfile` attached; consumes stress base Project IRR (0.398) |
| Similar-case RAG | `GET /projects/{id}/similar-cases/rag` → status ok, backend `lexical`, cited hit (snapshotId/projectId/runId) |
| Infra status | `GET /ops/infra-status` → postgres/oidc not configured on dev → object storage `local` fallback (expected) |

## Notes
- Consensus resolving 0 numeric conflicts here is correct: this fixture's
  cross-agent numbers already agree; the open items are missing-evidence gates,
  which the loop must not fabricate.
- RAG returned the run's own snapshot because institutional memory currently
  holds one case; across a real multi-project corpus it ranks prior cases.
- Live PostgreSQL/PostGIS, OIDC RS256, and S3 remain deploy-time (deps + services);
  the dev run correctly reports them unconfigured and falls back to local/SQLite.

## How to reproduce
Run a PF review (`POST /projects/{id}/orchestrations/pf-review`) on a project
with covenants/discount triggers, then inspect `artifacts.consensusResolution`,
`artifacts.termSheetStages`, `artifacts.icMemo`, `artifacts.developabilityScore`,
and `GET /projects/{id}/similar-cases/rag`.
