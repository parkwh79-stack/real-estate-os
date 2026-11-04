# Chief Pod Runtime Integrity

Updated: 2026-11-04

Status: Controlled Implementation

## Purpose

This contract turns the Chief Deal Room task graph into an enforceable runtime.
It prevents a downstream Pod from presenting stale or fabricated deal-review
results when an upstream Pod has not completed.

## 운영 AI 딜룸 진입

2026-11-04 운영 로그에서 딜룸 계획과 지도가 동일한 구형 `app_state` 조회에서
`InsufficientPrivilege`로 실패했다. 딜룸의 계획은 프로젝트 범위의 승인 가정을
기존 정규화 저장소에서 읽는다. 지도는 계획이나 승인 가정을 읽지 않는다.
이를 위해 운영 DB 역할이나 RLS 정책을 넓히지 않는다.

딜룸은 진입 시 계획과 저장된 결과를 불러온다. 6개 Chief Pod의 업무를 시작하려면
**전체 자동 실행** 또는 해당 Pod 실행을 선택한다. 계획 조회와 실행 실패를
구분하여, 실행을 시작할 수 없어도 계획과 보완할 자료는 화면에 남는다.
운영 실행은 프로젝트와 실행 이벤트를 정규화 저장소에 기록한다. 자료가 없는
프로젝트에서도 부족한 자료와 차단 상태를 반환한다. PostgreSQL 요청 검증은
계획과 자료 부족 상태를, 별도 합성 프로젝트 검증은 실행·저장·재열기를 확인했다.

## Runtime Guarantees

| Capability | Implementation | Status |
|---|---|---|
| Dependency enforcement | `PodExecutionCoordinator` validates the track-specific DAG and computes a topological run order. A single-Pod run fails when an upstream dependency is not complete. | Implemented |
| Calculation persistence | Feasibility-Cashflow and PF-Credit Pod deterministic outputs are written to `calculation_runs` immediately and referenced from the Pod artifact. | Implemented |
| Agent handoff persistence | Each completed Pod creates validated `AgentHandoffEnvelope` rows for its immediate downstream Pods. | Implemented |
| Hash Event Bus | Pod input and output snapshots use stable SHA-256 hashes. Recalculation is emitted only for explicit domain triggers or a changed output hash. | Implemented |
| Event lifecycle | Durable events move through `pending_recalculation`, `running`, `completed`, or `failed`. Storage failure is not silently ignored. | Implemented |
| Deal-state materialization | Nested numeric metrics, risk issues, evidence references, and assumption references are materialized into `deal_state_records` with an exact artifact path. Writes are batched. | Implemented |
| No fabricated PF fallback | Missing product mix or tranche structure produces `scenarioEngineReadiness.status=not_ready`; it no longer creates a synthetic 100-unit product or synthetic Senior tranche. | Implemented |
| PostgreSQL typed persistence | Critical runtime stores dual-write to `app_state` and typed JSONB tables for artifacts, AI reviews, handoffs, events, calculation runs, and deal-state records. | Implemented; live DB verification requires `REALYTICS_TEST_DATABASE_URL` |

## Pod Execution Contract

```text
load Shared Deal State
  -> resolve track-specific AgentPodPlan DAG
  -> verify dependency statuses
  -> build input snapshot and inputHash
  -> mark inbound recalculation events running
  -> execute deterministic Pod use case
  -> persist CalculationRun records
  -> persist AgentHandoffEnvelope records
  -> persist Pod artifact with outputHash and references
  -> recursively materialize Deal State records in one transaction
  -> complete or fail inbound events
  -> emit downstream event only when an explicit trigger or output change exists
```

## Event State Machine

```text
pending_recalculation
  -> running
     -> completed
     -> failed
```

An event keeps the same `eventId` through every transition. It also preserves
`inputHash`, `previousOutputHash`, `outputHash`, `sourceArtifactKey`, and related
`calculationRunIds`.

## Persistence Ownership

| Record | Typed table | Source of truth |
|---|---|---|
| Pod artifact | `chief_pod_artifacts` | Pod execution result and hashes |
| Pod AI review | `pod_ai_reviews` | Evidence-aware reviewer output |
| Pod handoff | `agent_handoffs` | Agent-to-Agent communication ledger |
| Recalculation event | `recalculation_events` | Event Bus lifecycle history |
| Calculation run | `calculation_runs` | Deterministic engine input/output snapshot |
| Evidence/Assumption/Metric/Risk | `deal_state_records` | Queryable Shared Deal State ledger |

The PostgreSQL adapter uses an allowlist for table and column identifiers. The
SQLite path remains the local controlled-implementation backend, and endpoint
E2E tests use a disposable SQLite file instead of the pilot runtime database.

## Verification

- DAG ordering, dependency blocking, and cycle detection tests
- Hash-event emission and event lifecycle tests
- CalculationRun and handoff API persistence tests
- Recursive materialization and source-path tests
- PostgreSQL typed SQL allowlist tests
- Optional isolated live PostgreSQL round trip using `REALYTICS_TEST_DATABASE_URL`

2026-07-14 verification completed with 42 focused runtime tests and 122 wider
Cash Flow, Approval Gate, Risk, Evidence, storage, and API regression tests.
The evidence-triggered reanalysis route now forwards the same CalculationRun and
handoff persistence sinks as direct Pod execution. Materialized metric keys omit
the structural `metrics` container while retaining the exact `artifactPath` in
the source locator. Web TypeScript and zero-warning ESLint checks also pass.

The live PostgreSQL round trip remains intentionally unverified on this machine
because neither `REALYTICS_TEST_DATABASE_URL` nor `REALYTICS_DATABASE_URL` is
configured. The optional test skips instead of writing to a developer or pilot
database.

## Remaining Production Work

1. Run the optional integration suite against a dedicated PostgreSQL CI service.
2. Apply migrations through a versioned deployment pipeline instead of runtime `ensure_schema` calls.
3. Move full-state `app_state` dual-write to a migration compatibility flag after typed-table parity is proven.
4. Add a worker/outbox consumer for distributed Event Bus retries and idempotency leases.
5. Add tenant keys and row-level ownership constraints to every typed table.
