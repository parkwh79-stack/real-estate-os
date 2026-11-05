# Chief Pod Runtime Integrity

Updated: 2026-11-05

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

2026-11-05 운영 딜룸은 프로젝트 서버 기록으로 계획을 구성한다. 개별 Pod,
전체 실행, 재분석에서 인증된 사용자와 프로젝트 범위를 사용하고 실행 이력을
같은 PostgreSQL 권위에 기록한다. 주소 보강은 프로젝트의 서버 보관 원본만 읽는다.
대시보드는 미실행을 정상 상태로 표시하고, 자료 누락과 실패는 확인 대상으로 표시한다.

승인 준비는 별도 서버 조회에서 평가 전, 조회 불가, 평가 결과를 구분한다.
법률 목록은 프로젝트 범위의 정규화 기록을 조회한다. 법률의견, 면제, 승인 등
권위 있는 기록 저장은 전용 법률 검토 계정이 필요하다.
[전용 계정 설정과 검증 절차](../engineering/legal-corpus-operations.md)

운영 DB 권한 대조에서 Agent 실행, 단계, 인계, 계산, 결과 이력 저장을 확인했다.
독립 Agent 도구 실행은 서버에서 확인한 역할로 요청을 구성하며, 도구 감사 저장소의
권한이 없는 환경은 서비스 사용 불가를 반환한다. 법률 검토 전용 계정과 외부 PF Worker
활성화는 별도 운영 변경으로 검토한다.

증빙 보완 기록은 운영 역할에 조회와 저장 권한이 없다. 서버가 보완 저장 불가 상태를
반환하면 딜룸에서 **증빙 저장·재계산**을 비활성화한다. 보완 기록 API는 이 상태에서
503을 반환하고, 일반 계획·분석은 프로젝트에 저장된 입력을 사용한다.

## 대시보드·워크스페이스 초기 응답

Cloud Run의 로그인 확인 요청은 자동 확장으로 인스턴스가 시작된 경우 약 59.8초,
같은 리비전에서 인스턴스가 준비된 경우 약 0.063초였다. 시작 로그에서는
ClamAV 준비까지 약 44초, 이후 API 준비까지 약 15초가 걸렸다.
Cloud Run 설정에서 API와 ClamAV를 병렬로 시작한다. API 준비 상태는
정규화 DB 연결과 검사기 구성 유효성을 확인한다. 배포 검증 리비전은
인스턴스 시작 후 API 10.3초, ClamAV 33.2초에 각각 준비되었다.
이는 배포 시점의 컨테이너 준비 시간이다. 업로드 격리는 실제 검사에
성공한 파일만 해제한다. 파일 검사기가 연결되지 않으면 업로드 API가 503을 반환한다.
Vercel 서버 함수의 단일 리전은 API와 Supabase가 있는 도쿄로 설정한다.

대시보드는 최근 작업과 전체 목록을 각각 도착하는 대로 표시한다.
프로젝트 헤더와 본문의 동일한 컨텍스트 요청은 인증 범위 안에서 진행 중일 때만 공유한다.
새로고침과 인증 변경 후에는 새 요청을 한다. 서버의 프로젝트 권한 검사는 요청마다 수행한다.
딜룸은 계획을 먼저 표시하고 승인 준비·실행 이력·법률 기록은 별도로 조회한다.
승인 관련 입력과 제출은 해당 권위를 확인한 경우에 활성화한다.
첫 접속과 같은 계정에서 재접속한 시간을 별도로 측정한다.

## 사용자 AI 딜룸과 운영 진단

사용자 딜룸은 현재 상태, 다음 할 일, 검토 현황 순으로 표시한다. 자료 요청은
실행을 차단하는 항목부터 관련 화면별로 모으고, 보완 대상과 예시 자료는 펼쳐서 확인한다.
각 검토 카드는 완료·대기·보완·실패·재검토를 구분한다. 완료 결과가 0건이면
전체 실행 요청이 정상 응답해도 분석이 완료되었다고 표시하지 않는다.
사전 검토 전에는 가능한 자료 확인을 먼저 안내한다. 검토가 정리되면 남은 확인·제출
항목을 안내한다. 토지 미리보기는 해당 화면의 권한과 입력 조건을 따른다.

패럴랙스는 소개 배경에만 적용하고, 유리 효과는 검토 요약에 적용한다.
수치를 읽는 본문과 조작 요소는 고정된 불투명 표면에 표시한다. 모바일과
동작 감소 설정에서 배경 스크롤 효과를 중지한다. 검토 상세는 키보드로도 닫을 수
있는 오른쪽 서랍에서 읽는다.

실행 이벤트, 내부 Pod·Agent 명칭, 작업 그래프와 원본 오류 코드는
`/project/{id}/chief-orchestration/diagnostics`에서 확인한다. 플랫폼 운영자
여부와 기존 프로젝트 읽기 권한을 확인한 뒤 진단 화면을 열고, 각 API에서도
프로젝트 범위를 검증한다.


## 6개 Pod 사전 검토

사용자의 첫 행동은 저장 자료의 자동 확인이다.
`POST /projects/{id}/chief-orchestration/preflight`가 부분 입력으로 지원되는
실제 Agent를 실행하고, 확인한 결과를 `AgentHandoffEnvelope`로 전달한다.
입력과 선행 결과의 변경에 영향을 받는 Pod를 다시 검토한 뒤 남은 사용자 확인·제출
항목을 관련 화면과 사용 목적별로 모은다. 공통 원본 누락도 사전 검토의 시작을 막지
않는다. 공개 조회의 연결 실패·미구성, 조회 결과 없음, 입력 확인 필요를 구분한다.

`reviewLoop`는 `stable`, `needs_material`, `retry_needed`,
`limit_reached`와 실행·재사용 개수, 남은 Pod, 종료 이유를 반환한다.
사용자 확인·제출 항목은 검토가 정리된 뒤 표시한다. 요청 실패는 분석 시작 조건을
충족하지 않는다. 화면과 서랍에서는 사전 확인과 정식 분석 상태를 각각 읽을 수 있다.

재개 요청은 `previousReview`의 `receiptId`와 `resumeState`를 함께 보낸다.
서버는 인증된 사용자·테넌트·프로젝트·트랙, 실행기 버전, 원본 입력 개정값,
상태 해시와 유효시간을 대조한다. 같은 입력과 선행 결과는 재사용하며,
실패 항목은 명시적인 `retryFailed` 요청으로 재실행한다.
서버의 `canContinue`가 참인 시간 예산 종료에서만 자동으로 이어간다.
화면은 한 작업에서 최대 세 번 요청하고, 추가 이어가기는 사용자가 선택한다.
동일 입력의 누적 실행·라운드 한도에 도달하면 정지한다.

큰 재개 출력은 압축해 반환하고, 서버에는 인증 범위·상태 해시·만료 시각의 수신 확인
기록을 저장한다. 원문·공개 조회 결과와 Agent 출력은 검토 출력 DB에 보관하지 않는다.
전송 상태 512 KiB와 해제 상태 4 MiB 한도를 적용한다. 브라우저에서는 검증된
사용자·세션·테넌트·프로젝트·트랙별 메모리에 최대 네 항목을 30분 동안 유지하고,
인증 변경·만료 때 정리한다. SPA 이동 후 같은 상태를 사용할 수 있다.
브라우저 전체 새로고침 후에는 새 사전 검토를 시작한다.

현재 재개 실행기는 `realytics-chief-preflight-review-v2`다. 이전 버전의 상태는
새 검토로 전환한다. 주소로 확인한 PNU는 원래의 필지 식별 근거를 유지하며,
실제 요청 PNU와 일치하는 지적 속성 응답만 지적 근거로 전달한다. 주소 확인을
지적·권리·용도지역의 확인 완료로 격상하지 않는다. 공공 연결 미설정은 제한으로
표시하고 가능한 검토와 남은 자료 요청을 제공한다. 실제 조회 실패나 응답 PNU
불일치는 실패·재시도 상태를 유지한다.

정식 분석 시작 뒤에도 현재 입력과 일치하는 사전 검토를 유지하고 최신 계획을 다시
읽는다. 원본 입력 개정값이 바뀌면 이전 수신 확인을 사용해 영향을 받는 검토를 수행한다.
사전 검토는 정식 계산·심사·승인 기록과 별도 단계로 표시한다.

2026-11-05 로컬 실제 본리동 자료 검증에서 9필지·64개 월차 입력으로 6개 Pod의
실제 Agent 실행을 확인했다. 동일 입력 재개는 실행 0개·재사용 6개였으며,
압축 전 재개 상태는 440,854바이트였다. 운영 배포와 원격 CI 결과는 해당 배포 기록에서
별도로 확인한다. 공공성 Pod의 전용 정책 시뮬레이션·공공기여 최적화와 브라우저 전체
새로고침을 넘는 사전 검토 재개는 후속 개발 범위다.
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
| PostgreSQL typed persistence | Hosted requests use Tenant/project-scoped normalized repositories for artifacts, AI reviews, handoffs, events, calculations and deal state. Legacy full-state compatibility is not the hosted authority. | Implemented; isolated live DB verification uses `REALYTICS_TEST_DATABASE_URL` |

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

At the 2026-07-14 verification, the live PostgreSQL round trip remained unverified
because neither `REALYTICS_TEST_DATABASE_URL` nor `REALYTICS_DATABASE_URL` is
configured. The optional test skips instead of writing to a developer or pilot
database.

## Historical follow-up list (2026-07-14)

This list records the earlier runtime review. Current hosted authority, roles,
worker activation and remaining changes are governed by the dated sections above
and [the current architecture boundary](../architecture/architecture-boundary.md).

1. Run the optional integration suite against a dedicated PostgreSQL CI service.
2. Apply migrations through a versioned deployment pipeline instead of runtime `ensure_schema` calls.
3. Move full-state `app_state` dual-write to a migration compatibility flag after typed-table parity is proven.
4. Add a worker/outbox consumer for distributed Event Bus retries and idempotency leases.
5. Add tenant keys and row-level ownership constraints to every typed table.

## 운영 배선 검증 (2026-11-05)

문서·토지·시장성·딜룸·근거 화면의 수정 범위를 배포용 웹 빌드로 실행해 PC·모바일 회귀 100개와 임시 화면 캡처 검사 2개, API 통합 83개 검사를 통과했다. 웹 타입·변경 파일 린트·빌드도 통과했다. 새 토지·시장 조회의 외부 응답은 회귀 검사용 응답으로 검증했으며 실제 사용자 세션에서의 전 과정 완료를 의미하지 않는다. 운영에서는 기존 PDF 4건과 파싱 실행 기록, 서버 상태·운영 웹 주소·보호 API의 인증 차단을 확인했다.

PF 시장 근거 목록과 검토는 기존 `realytics.market_evidence`를 테넌트 범위로 사용한다. 검토 저장은 읽은 원문과 비교해 충돌 시 409를 반환한다. 운영 테이블의 기존 열과 런타임 SELECT·UPDATE 권한을 확인했으며 새 스키마 변경은 필요하지 않다. 직접 주소·호출, 운영 배포 환경, 저장소 권한이 실제 연결 상태의 기준이다. Markdown은 동작을 재현하기 위한 계약·검증 기록으로 관리한다.

운영 카카오 접수 계획은 인증된 프로젝트 권한과 정규화 감사 기록을 사용한다. 기존 로컬 DB 전용 시스템 통계는 운영에서 503으로 조회 불가를 알린다. 표준 용어 읽기는 운영 DB로 연결하고, 후보 읽기·용어집 편집의 부족한 권한은 별도 검토 대상으로 둔다.
