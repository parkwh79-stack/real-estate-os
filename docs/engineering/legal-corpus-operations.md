# Legal Intelligence Corpus 운영 가이드

Updated: 2026-07-20

Status: controlled code and `0009`/`0010` wrapper sync implemented; live
PostgreSQL/S3/provider/counsel validation pending

이 문서는 공식 법률 corpus의 수집, 완료 증명, 원문 보존, 최소권한,
장애 복구 절차를 정의한다. 권위·검색·승인 원칙은
[`docs/architecture/legal-intelligence-core.md`](../architecture/legal-intelligence-core.md)를
우선한다.

## 1. 운영 불변식

다음 조건은 배포 프로필과 무관하게 유지해야 한다.

1. 공식 원문 제공기관은 source authority이고, HTTP·MCP·로컬 DB는
   transport다.
2. Law.go.kr search-list와 MCP 결과는 discovery candidate다. 정확한
   공식 원문, 식별자, version, locator, hash 검증 전에는 global verified
   corpus로 게시하지 않는다.
3. raw object를 먼저 불변 저장하고, 한 provider page의 모든 normalized
   record와 checkpoint를 하나의 PostgreSQL transaction으로 게시한다.
4. checkpoint는 `provider_id + requested_scope_hash` 단위 append-only CAS다.
5. 동시 worker는 append-only lease와 fencing token으로 제어한다. 만료된
   lease 소유자는 이후 commit 권한이 없다.
6. full-corpus coverage는 일반 completed event가 아니라 DB가 재검증한
   `legal_corpus_scope_completion_attestations`로만 성립한다.
7. serving runtime은 corpus를 읽을 수 있지만 수정할 수 없다. legal-sync
   role은 coverage registry 3개 테이블에는 `SELECT`만, 허용된 publish
   ledger에는 `SELECT, INSERT`만 가진다.
8. 원문 reconciliation은 삭제하지 않는다. 고아 객체는 보존하고 사람이
   별도 승인한 disposition 절차로만 처리한다.
9. stale·unverified·conflicted 결과는 표시할 수 있으나 approval gate를
   해제하지 못한다.
10. Approval Readiness는 항상 tenant/project/snapshot,
    snapshot version/hash, 실제 corpus lineage, latest counsel decision과
    delegation을 normalized DB에서 매번 다시 읽는다. legacy JSON은 이
    결정을 대체할 수 없다.
11. `recordLocator`는 내부 기록 위치이고 `evidenceLocator`는 외부 원문
    근거다. Approval Gate는 legal review가 자기 자신을 가리키는
    `recordLocator`를 증거로 인정하지 않는다.
12. snapshot maker는 자기 snapshot의 final checker가 될 수 없으며,
    외부 법률자문의 Approval Gate·waiver·terminal cure·counsel opinion·
    report-eligible analysis·Citator 결정에는 각 workflow scope와 정확히
    일치하는 유효한 delegation ID가 필요하다.

## 2. 실행 구성

```text
official page source
  -> LegalCorpusBatchCoordinator
      -> LegalCorpusSyncService
          -> immutable S3/MinIO raw object
          -> PostgresLegalCorpusSyncRepository
              -> source fetch
              -> canonical resource/crosswalk
              -> immutable version/effective lineage
              -> provisions
              -> checkpoint CAS
      -> PostgresLegalCorpusCompletionRepository
          -> ordered checkpoint/fetch/version manifest rebuild
          -> completion attestation

serving API
  -> PostgresVerifiedLegalSnapshotProvider
      -> verified versions/provisions
      -> latest active provider completion attestation
      -> terminal checkpoint age
  -> Approval Readiness
      -> normalized snapshot/version/hash/lineage re-query
      -> latest counsel decision/delegation re-query

human Citator review
  -> dedicated legal-review DSN
      -> read-only global citation/relation candidate
      -> tenant/project review-version successor
      -> tenant/project append-only review event

provider operations
  -> dedicated legal-operations DSN
      -> append-only quarantine/DLQ/metrics sink
      -> trusted-worker immutable object receipt
      -> approved replay request
      -> requested/running/completed|failed events
```

운영 composition root는
`services/api-server/use_cases/legal_corpus_runtime.py`다. provider worker는
`build_legal_corpus_batch_coordinator()`를 호출하고
`OfficialCorpusPageSource` 구현을 넘긴다.

```python
coordinator = build_legal_corpus_batch_coordinator()
attestation = coordinator.run(
    official_page_source,
    initial_checkpoint=resume_checkpoint,
)
```

`official_page_source`의 한 page에 들어가는 모든
`LegalCorpusSyncRequest`는 동일한 provider revision, scope, raw response,
checkpoint before/after를 공유해야 한다. terminal page를 확인하지 못하면
completion attestation은 생성되지 않는다.

### 2.1 공식 source registry와 외부 worker

`use_cases/legal_corpus_registry.py`는 verified corpus 게시 허용목록이다.
Law.go.kr 검색 target이나 list/detail shape fixture가 존재한다는 사실만으로
게시를 활성화하지 않는다. 각 resource kind는 detail 응답, 공식 history
응답, semantic normalization의 세 경계를 증명하는 code-owned versioned
registration을 가져야 한다. 현재 production history fetcher와 semantic
normalizer가 승인되지 않았으므로 기본 registry의 모든 target은
`blocked`다. 검색 provider는 discovery candidate를 반환할 수 있지만 그
검색 행을 verified corpus로 승격할 수 없다.

외부 process entrypoint는 다음과 같다.

```powershell
# 스케줄 실행. runOnStartup=false이면 첫 interval 경과 후 1회차를 실행한다.
& '.\.runtime\api-venv\Scripts\python.exe' `
  services/api-server/scripts/run_legal_corpus_worker.py

# 운영자/CI 1회 실행. 이 모드에서만 schedule seconds를 생략할 수 있다.
& '.\.runtime\api-venv\Scripts\python.exe' `
  services/api-server/scripts/run_legal_corpus_worker.py --once
```

initial job은 partial 실패 후 시작점부터 idempotent replay하여 앞선
page까지 포함한 완전한 completion manifest를 다시 구성하고, 이미
verified completion attestation이 있으면 건너뛴다. 중간 checkpoint를
건너뛰고 나머지만 attestation하는 것은 금지한다. incremental mode는
target별로 검토된 incremental checkpoint 계약을 등록한 뒤에만 최신
append-only checkpoint에서 재개한다. 일반 list page 번호를 incremental
change feed로 간주하지 않는다. partial provider page는 마지막 완료
checkpoint와 diagnostics를 노출하지만 completion attestation은 만들지
않는다.

read-only 운영 endpoint `GET /ops/legal-corpus/runtime`은 sanitized
configuration, registry capability, target mode, query SHA-256, blocked
reason을 노출한다. Law.go.kr OC, query 원문, DSN, raw provider payload는
반환하지 않는다. ingestion은 별도 credential을 가진 worker process에서만
실행하며 API endpoint가 대신 실행하지 않는다. 각 job은 외부 공식
provider로 전송되는 query가 공개 법률 검색어임을
`publicQueryAcknowledged=true`로 명시해야 한다.

### 2.2 8-target coverage contract 등록

`0010_legal_corpus_coverage_contracts.sql`은 법령·조문·판례·법령해석·
행정규칙·자치법규·헌재결정·법령용어의 8개 lane을 하나의 immutable
registry로 묶는다. 각 manifest row는 provider revision, registry version,
resource kind, target, 공개 query SHA-256, source adapter version, boundary
contract version을 포함한다. 8개 중 하나라도 없거나 query/adapter/boundary
identity가 다르면 coverage는 complete가 아니다.

등록은 runtime 또는 legal-sync DSN이 아니라 table owner인 migration
role로만 수행한다.

```powershell
# 쓰기 없이 manifest·DB owner·required target·provider·현재 leaf 확인
& '.\.runtime\api-venv\Scripts\python.exe' `
  services/api-server/scripts/register_legal_corpus_coverage.py `
  '<reviewed-manifest.json>' --dry-run

# CI drift gate: 현재 leaf와 정확히 같으면 0, 등록 필요 시 3
& '.\.runtime\api-venv\Scripts\python.exe' `
  services/api-server/scripts/register_legal_corpus_coverage.py `
  '<reviewed-manifest.json>' --status

# 승인된 배포에서만 registry header와 8 contracts를 한 transaction으로 등록
& '.\.runtime\api-venv\Scripts\python.exe' `
  services/api-server/scripts/register_legal_corpus_coverage.py `
  '<reviewed-manifest.json>'
```

초기 completion은 sequence 1의 유일 root여야 한다. incremental completion은
동일 provider/scope/contract의 직전 verified leaf를 baseline으로 사용하고
`baseline.last_sequence + 1 = first_sequence`를 만족해야 한다. legacy NULL
binding이나 이전 initial root를 반복 참조한 fork는 readiness에 포함되지
않는다.

## 3. 비밀과 환경변수

비밀은 AWS Secrets Manager 또는 승인된 동등 저장소에서 process
environment로 주입한다. `.env`, 명령행 인자, URL, 로그, evidence metadata,
브라우저 번들에 실제 값을 남기지 않는다.

```dotenv
# DDL 전용
REALYTICS_MIGRATION_DATABASE_URL=<deployment-role-dsn>

# serving API 전용
REALYTICS_DATABASE_URL=<realytics-runtime-dsn>

# isolated live integration test 전용
REALYTICS_TEST_DATABASE_URL=<isolated-realytics-test-dsn>

# 공식 corpus worker/reconciliation 전용
REALYTICS_LEGAL_SYNC_DATABASE_URL=<realytics-legal-sync-dsn>

# human Citator review 전용
REALYTICS_LEGAL_REVIEW_DATABASE_URL=<realytics-legal-review-dsn>

# provider quarantine/DLQ 조회와 replay 승인 전용
REALYTICS_LEGAL_OPERATIONS_DATABASE_URL=<realytics-legal-operations-dsn>

REALYTICS_DATABASE_SCHEMA=realytics
REALYTICS_POSTGIS_SCHEMA=extensions
REALYTICS_RUNTIME_DATABASE_ROLE=realytics_runtime
REALYTICS_LEGAL_SYNC_DATABASE_ROLE=realytics_legal_sync
REALYTICS_LEGAL_REVIEW_DATABASE_ROLE=realytics_legal_review
REALYTICS_LEGAL_OPERATIONS_DATABASE_ROLE=realytics_legal_operations
REALYTICS_LEGAL_SYNC_SEARCH_PATH=realytics,extensions,public,pg_temp
REALYTICS_LEGAL_SYNC_MAX_PAGES=10000
REALYTICS_LEGAL_CORPUS_WORKER_ENABLED=true
REALYTICS_LEGAL_CORPUS_RUN_ON_STARTUP=false
REALYTICS_LEGAL_CORPUS_SCHEDULE_SECONDS=3600
REALYTICS_LEGAL_CORPUS_REQUESTED_BY=legal-sync-worker
REALYTICS_LEGAL_CORPUS_WORKER_ID=legal-sync-worker:production-1
REALYTICS_LEGAL_CORPUS_JOBS_JSON=[{"resourceKind":"statute","query":"<public-query>","requestedScope":"statutes:approved-scope","mode":"initial","publicQueryAcknowledged":true}]
REALYTICS_LAW_GO_KR_OC=<secret>
REALYTICS_ENVIRONMENT=staging

REALYTICS_LEGAL_RAW_OBJECT_STORAGE=s3
REALYTICS_LEGAL_RAW_BUCKET=<private-object-lock-bucket>
REALYTICS_LEGAL_RAW_PREFIX=realytics
REALYTICS_LEGAL_RAW_LOCK_MODE=COMPLIANCE
REALYTICS_LEGAL_RAW_RETENTION_DAYS=365

```

운영 다섯 DSN(migration, runtime, legal-sync, legal-review,
legal-operations)은 서로 대체하지 않으며 test DSN도 이들과 격리한다.
특히 sync repository, Citator API, provider replay API는 각 전용 DSN이
없으면 runtime/migration DSN으로 폴백하지 않고 즉시 실패한다.

## 4. PostgreSQL/Supabase 적용

### 4.1 사전조건

- `realytics`: 비공개 application schema
- `extensions`: PostGIS와 `pg_trgm`
- `public`: migration/sync가 명시적으로 참조하는 공유 객체만 허용
- migration, runtime, legal-sync, legal-review, legal-operations principal
  분리
- Supabase Data API의 exposed schema 목록에 `realytics` 미포함

### 4.2 wrapper와 dry-run

repository root에서 실행한다.

```powershell
& '.\.venv\Scripts\python.exe' `
  services/api-server/scripts/render_supabase_migrations.py
& '.\.venv\Scripts\python.exe' `
  services/api-server/scripts/render_supabase_migrations.py --check
git diff --check

npm run supabase:db:push:dry-run
```

dry-run에서 private schema, extensions 위치,
`0008_legal_intelligence_core.sql`,
`0009_legal_governance_and_tenant_overlay.sql`,
`0010_legal_corpus_coverage_contracts.sql`, role bootstrap, destructive
statement 유무를 검토한 뒤에만 적용한다.

2026-07-20 현재 `0009_legal_governance_and_tenant_overlay.sql`과
`0010_legal_corpus_coverage_contracts.sql` wrapper/source checksum은
동기화되었고 `render_supabase_migrations.py --check`가 통과한다. 이는
정적 패키징 증거이며, `db push --dry-run`, 실제 PostgreSQL 적용, 8-target
manifest 등록과 RLS 검증 전에는 운영 배포 완료로 간주하지 않는다.

```powershell
npm run supabase:db:push
```

### 4.3 역할 provisioning

```powershell
$env:REALYTICS_RUNTIME_DATABASE_ROLE = 'realytics_runtime'
$env:REALYTICS_LEGAL_SYNC_DATABASE_ROLE = 'realytics_legal_sync'
$env:REALYTICS_LEGAL_REVIEW_DATABASE_ROLE = 'realytics_legal_review'
$env:REALYTICS_LEGAL_OPERATIONS_DATABASE_ROLE = 'realytics_legal_operations'

& '.\.venv\Scripts\python.exe' `
  services/api-server/scripts/provision_postgres_runtime.py
& '.\.venv\Scripts\python.exe' `
  services/api-server/scripts/provision_legal_sync_role.py
```

runtime/sync 스크립트는 비밀번호가 없는 `NOLOGIN` 역할과 권한만 만든다.
review/operations는 현재 각각
`db/postgres_legal_review_privileges.py`와
`db/postgres_legal_operations_privileges.py`의 provision/audit helper만
있고 전용 provisioning CLI는 없다. 운영 자동화 또는 전용 CLI에 이 helper를
연결하고 audit 결과가 빈 목록임을 증거로 남기기 전에는 해당 principal을
배포 완료로 판정하지 않는다. 운영자가 승인된 IAM/secret-manager 절차로
`LOGIN`과 credential을 활성화한다.

legal-sync role의 필수 속성:

- `NOSUPERUSER`
- `NOCREATEDB`
- `NOCREATEROLE`
- `NOINHERIT`
- `NOREPLICATION`
- `NOBYPASSRLS`
- `realytics`, `extensions` schema `USAGE`
- coverage registration 3개 table의 `SELECT`
- 나머지 관리 대상 publish table의 `SELECT, INSERT`
- 모든 table의 `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES`, `TRIGGER` 금지
- schema `CREATE` 금지

serving runtime은 completion attestation과 checkpoint를 `SELECT`할 수
있지만 global corpus에 `INSERT`할 수 없다.

legal-review role은 global corpus와 evidence lineage를 `SELECT`하고,
allowlist에 포함된 tenant-native governance 및 project Citator
version/event만 `SELECT, INSERT`한다. global `legal_citations`와
`legal_relation_assertions`는 읽기 전용이다. legal-operations role은
quarantine/DLQ/metrics와 trusted-worker object receipt, replay
approval/request/event만 `SELECT, INSERT`한다. serving runtime은 이
provider-operations table들에 권한이 없다. 두 전용 역할 모두 corpus sync,
`UPDATE`, `DELETE`, DDL, schema `CREATE` 권한이 없어야 한다.

API는 법률 결정 route에서 explicit human principal을 요구하고 actor/role을
request body가 아니라 인증 context에서 파생한다. 다만 shared
legal-review DB credential 자체는 아직 actor 문자열을 실제 IdP 사용자나
PostgreSQL role membership에 암호학적으로 결합하지 않는다. 운영 전 P1
보강으로 signed transaction-local actor context 또는 동등한 brokered-write
경계를 도입하고, trigger가 actor·role membership을 검증해야 한다. 해당
credential 탈취·회전·회수 훈련도 acceptance evidence에 포함한다.

## 5. S3/MinIO 준비

법률 raw bucket은 일반 프로젝트 업로드 bucket과 분리하는 것을 권장한다.

필수 설정:

1. bucket versioning `Enabled`
2. bucket 생성 시 Object Lock `Enabled`
3. 기본 또는 object별 retention
4. server-side encryption
5. public access 전면 차단
6. legal-sync writer와 reconciliation inventory 권한 분리 가능
7. object access log와 CloudTrail/동등 audit
8. lifecycle가 retention/Object Lock보다 먼저 삭제하지 않도록 검토

writer 최소권한 예시:

- `s3:GetBucketVersioning`
- `s3:GetObjectLockConfiguration`
- `s3:PutObject`
- `s3:GetObject`
- `s3:GetObjectVersion`
- `s3:GetObjectRetention`
- `s3:PutObjectRetention`

reconciliation에는 추가로 `s3:ListBucketVersions`가 필요하다. 애플리케이션
role에 `s3:DeleteObject`, `s3:DeleteObjectVersion`,
`s3:BypassGovernanceRetention`을 부여하지 않는다.

adapter는 기동 또는 첫 작업 전에 versioning/Object Lock을 확인한다. 저장
시 content-addressed key, `If-None-Match: *`, SHA-256 checksum, version ID,
현재 유효한 Object Lock을 모두 검증한다. 어느 하나라도 증명되지 않으면
DB publish를 시작하지 않는다.

## 6. 동기화와 완료 증명

### 6.1 page 처리

각 page는 다음 순서를 따른다.

1. 현재 checkpoint head와 request의 `checkpoint_before` 비교
2. lease 획득 또는 동일 owner의 유효 lease 재개
3. raw response SHA-256 계산 및 불변 object 저장
4. 공식 source verification contract 검증
5. stable cross-revision resource identity와 provider crosswalk 생성
6. effective-time predecessor/overlap/gap/source-correction 검증
7. page의 모든 resource/version/provision을 원자적으로 append
8. fencing token 재검증
9. checkpoint event를 한 번 append
10. sync run completed event와 lease release append

transaction이 실패하면 normalized row와 checkpoint는 모두 rollback한다.
raw object만 남을 수 있으며 reconciliation worker가 이를 탐지하고
보존한다.

### 6.2 terminal completion

coordinator는 terminal page까지 다음을 확인한다.

- provider key/revision, scope, attestor 불변
- checkpoint before/after 연속성
- 매 page가 checkpoint를 전진
- page hash 중복 없음
- `max_pages` 이내 terminal 도달

completion repository는 checkpoint ledger에서 다음을 다시 읽는다.

- sequence의 누락·중복·역순
- scope와 scope hash
- page hash 배열
- page record count
- checkpoint별 source fetch와 sync run
- ordered legal resource version ID와 normalized hash
- distinct resource count
- terminal checkpoint
- 전체 ordered manifest SHA-256

검증 후에도 PostgreSQL deferred trigger가 checkpoint/fetch/version 구조를
독립 재검증한다. serving API의 coverage freshness는 attestation 작성 시각이
아니라 마지막 checkpoint의 `occurred_at`을 사용한다.

## 7. Raw object reconciliation

정기 작업 또는 DB publish 장애 후 실행한다.

```powershell
& '.\.venv\Scripts\python.exe' `
  services/api-server/scripts/reconcile_legal_raw_objects.py `
  --run-id "legal-raw-reconcile-20260718T120000Z"
```

대규모 inventory를 재개할 때는 직전 출력의 opaque cursor를 전달한다.

```powershell
& '.\.venv\Scripts\python.exe' `
  services/api-server/scripts/reconcile_legal_raw_objects.py `
  --run-id "legal-raw-reconcile-20260718T120000Z-part2" `
  --start-cursor "<opaque-cursor>"
```

분류:

| 상태 | 의미 | 자동 조치 |
|---|---|---|
| `referenced` | exact key/version/hash/size가 source fetch와 일치 | 없음 |
| `active_sync` | 아직 pending/running run 소유 | 보존 |
| `grace_period` | 최근 생성, DB commit 지연 가능 | 보존 |
| `orphan_preserved` | grace 경과, DB 참조 없음 | 최소 retention 연장 |
| `manual_disposition_required` | 장기 orphan | retention 연장 및 사람 검토 |

worker에는 delete method가 없다. inventory에 delete marker가 하나라도
발견되면 immutable storage 위반으로 전체 작업을 실패시킨다.

## 8. 모니터링과 알람

최소 지표:

- provider/scope별 마지막 checkpoint sequence·age
- lease acquire/renew/release, conflict, fencing reject
- page record count, page publish latency, replay count
- terminal completion 성공/실패와 manifest mismatch
- effective-time overlap/gap/predecessor conflict
- provider schema drift/quarantine
- raw put precondition conflict와 Object Lock 검증 실패
- reconciliation status별 객체 수와 partial cursor age
- runtime coverage complete/partial/unknown
- stale legal evidence가 gate를 차단한 횟수
- citation mismatch와 open corpus issue
- provider retry/circuit-open/rate-limit
- sync queue age, retry count, DLQ depth
- partial target count와 target별 sanitized error code
- quarantine/DLQ/replay requested/running/completed/failed count와 age
- normalized Approval Readiness DB-unreadable/identity/hash/lineage/delegation
  blocker count
- Citator queue age와 citation/relation review turnaround
- golden-corpus claim precision/recall, abstention
  precision/recall/accuracy, locator accuracy

즉시 알람:

- stale checkpoint가 정책 한도를 초과
- 동일 scope lease conflict가 반복
- fencing reject 발생
- terminal manifest 검증 실패
- Object Lock/versioning 비활성
- delete marker 발견
- source schema drift
- legal-sync role over-privilege audit 실패
- legal-review/legal-operations role over-privilege audit 실패
- runtime role의 global corpus write 권한 발견
- hardened Approval Readiness에서 normalized DB re-query 실패
- 승인되지 않은 object reference/hash를 사용한 replay 시도

## 9. Retry와 DLQ

- 408, 429, 5xx와 네트워크 read만 bounded retry한다. 다른 4xx는 자동
  재시도하지 않는다.
- 429/5xx의 `Retry-After`를 상한 내에서 존중하고 bounded jitter를
  적용한다.
- provider별 token bucket과 circuit breaker를 독립 적용한다.
- page publish는 동일 idempotency identity로 replay한다.
- stale checkpoint/fencing/effective-time/manifest 오류는 자동 무한 재시도하지
  않는다.
- provider schema drift payload는 raw object와 hash를 보존하고 quarantine한다.
- DLQ item에는 provider, scope, checkpoint, adapter/schema version,
  sanitized error code, raw object identity, retry count를 기록한다.
- secret, 원문 전체, credential-bearing URL을 DLQ metadata에 넣지 않는다.
- 수동 replay는 adapter release와 fixture contract test 통과 후 새 audit
  identity로 실행한다.

영속 운영 경계:

- serving runtime은 provider operations table에 접근하지 않는다.
  privacy-safe quarantine/DLQ/metrics와 replay workflow는 모두
  `REALYTICS_LEGAL_OPERATIONS_DATABASE_URL`을 사용하는 전용 repository가
  append한다.
- `admin` 또는 `compliance_officer`만 `legal:operate` 권한으로
  `/ops/legal-provider/dead-letters`를 조회하고 replay request를 생성한다.
- 외부 approval/request caller는 locator, object version 또는 SHA-256을
  주장할 수 없다. trusted provider/object worker가 먼저 immutable object
  receipt를 catalog에 등록하고, approval caller는 `replayObjectId`와
  idempotency key만 제출한다. catalog가 없는 dead-letter는
  non-replayable이다.
- replay execution은 `requested → running → completed|failed` 이벤트를
  append하며 예외 원문 대신 sanitized outcome code만 저장한다.
- 현재 저장소는 replay executor 주입 경계까지 제공한다. 실제 immutable
  object reader, worker dispatch, credential/IAM, metric exporter와 운영
  replay 훈련은 배포 작업으로 남아 있다.

### Provider operations isolation contract

- A dead-letter without a row in `legal_provider_replay_objects` is
  non-replayable. Only the trusted provider/object worker may call the internal
  receipt registration use case; no HTTP route exposes catalog insertion.
- The catalog binds tenant, project, dead-letter, provider, stable object
  locator, object-store version ID, SHA-256, Object Lock mode, retention
  deadline, object-store receipt hash, and worker identity in an append-only
  row. Approval callers submit only `replayObjectId`.
- Approval, request, and replay-start paths re-read that exact catalog row.
  Expired retention, cross-tenant/project/provider scope, a changed locator,
  version, digest, or receipt hash fails closed. The replay executor must return
  the exact request ID, catalog ID, version ID, and digest it verified.
- This database receipt does not by itself prove live bucket policy or bytes.
  Production completion still requires a version-aware object reader, separate
  IAM credentials, Object Lock/versioning verification, and a restore/replay
  drill.
- `legal_provider_quarantine`, `legal_provider_dead_letters`, and
  `legal_provider_metrics` are global privacy-safe operations metadata. They
  must never contain tenant payloads, query text, response bodies, secrets, or
  credential-bearing URLs.
- The serving runtime has zero table privileges on all provider operations and
  replay tables. Both operational sinks and replay workflows connect only with
  `REALYTICS_LEGAL_OPERATIONS_DATABASE_URL`.
- The legal-operations principal may `SELECT, INSERT` only. It may append
  global quarantine/DLQ/metric records and tenant-native replay approval,
  request, and event records; it may never `UPDATE`, `DELETE`, `TRUNCATE`,
  own tables, inherit elevation, or bypass RLS.
- `legal_provider_replay_approval_events` and
  `legal_provider_replay_requests` carry `tenant_id` and `project_id`.
  `legal_provider_replay_events` inherits tenant visibility from its parent
  request. All three tables use forced RLS and fail closed when
  `realytics.tenant_id` is absent or different.
- Replay requires a compliance-officer approval for a different, named
  requester. The approval expires within 24 hours, is consumed once, and binds
  the catalog ID plus the catalog's immutable locator, object-store version,
  object/locator digests, retention, and receipt hash. Lifecycle events must
  follow `requested -> running -> completed|failed` with exact sequence and
  non-decreasing timestamps.

## 10. Golden corpus release gate

평가기 실행 예:

```powershell
& '.\.venv\Scripts\python.exe' `
  services/api-server/scripts/evaluate_legal_golden_corpus.py `
  --manifest '<approved-manifest.json>' `
  --predictions '<complete-predictions.jsonl>'
```

exit code `0`만 promotion 후보이며, `2`는 schema/hash/경로 등 artifact
integrity 실패, `3`은 평가 완료 후 threshold 실패다. Manifest는 label
bytes SHA-256, corpus/version/case count, preparer와 다른 approver,
counsel-approved threshold를 고정해야 한다. 최소 측정값은 claim
precision/recall, abstention precision/recall/accuracy, exact locator
accuracy다.

저장소의 synthetic fixture는 평가기 계약 테스트일 뿐 production
golden corpus가 아니다. 실제 법령·판례·법령해석·행정규칙·자치법규·헌재
결정·인허가·신탁/담보·PF covenant의 answerable/abstention 사례와 정확한
corpus snapshot/fetch/resource/version/provision locator를 독립 법무
maker-checker가 승인해야 이 gate를 닫을 수 있다.

## 11. Backup, restore, disaster recovery

백업 단위:

- PostgreSQL private schema와 migration ledger
- S3/MinIO 모든 object version과 retention metadata
- provider adapter artifact, schema fixture, normalization/ranking policy
- secret은 데이터 백업이 아니라 secret manager 복구 정책으로 관리

복구 훈련:

1. 격리 DB에 migration을 새로 적용한다.
2. PostgreSQL backup을 복원한다.
3. object inventory와 `legal_source_fetches` exact key/version/hash/size를
   대사한다.
4. REALYTICS와 Supabase migration ledger checksum을 확인한다.
5. runtime/sync role을 다시 provision하고 over-privilege audit를 실행한다.
6. completion attestation과 terminal checkpoint를 재조회한다.
7. historical/current/future-effective fixture 검색을 실행한다.
8. evidence snapshot은 동일 source version/locator/hash를 재현해야 한다.
9. MCP를 모두 끈 상태에서 local legal retrieval과 Legal Pod를 실행한다.

immutable row/object를 복구 과정에서 update/delete하지 않는다. 잘못된
normalized version은 superseding record/event로 격리한다.

## 12. Live acceptance matrix

운영 완료 증거에는 다음 결과와 실행 시각, 환경, commit SHA, 담당자를
남긴다.

- [x] Supabase wrapper/checksum ledger에 `0009`·`0010` 포함 및
      `render_supabase_migrations.py --check` 통과
- [ ] `0008`·`0009`·`0010`을 실제 PostgreSQL/PostGIS에 적용
- [ ] migration owner로 승인된 8-target manifest의 `--dry-run`,
      apply, `--status`를 순서대로 통과
- [ ] 1/8 completion은 incomplete, 8/8 exact contract completion만
      complete, legacy NULL/query·adapter mismatch는 incomplete
- [ ] 두 incremental worker가 동일 current leaf를 이으려 할 때 단 하나의
      successor만 commit되고 checkpoint sequence가 연속임
- [ ] PostGIS=`extensions`, application table=`realytics`
- [ ] migration/runtime/legal-sync/legal-review/legal-operations principal
      분리와 별도 credential
- [ ] runtime global corpus write 거부
- [ ] legal-sync update/delete/DDL 거부
- [ ] legal-review가 허용된 corpus read와 tenant governance/Citator
      append만 가능하고 global citation/relation·runtime/sync/
      UPDATE/DELETE/DDL은 거부
- [ ] legal-operations가 운영 metadata read와 replay append만 가능하고
      raw payload/runtime/sync/UPDATE/DELETE/DDL은 거부
- [ ] missing tenant context fail-closed
- [ ] tenant A/B 교차 조회·join·count·error 격리
- [ ] session pooler에서 tenant GUC transaction scope 검증
- [ ] 두 worker의 동일 scope lease 경합에서 한 worker만 commit
- [ ] lease 만료 후 구 fencing token commit 거부
- [ ] page 중간 장애 시 checkpoint 미전진
- [ ] 동일 page replay 시 중복 version/checkpoint 미생성
- [ ] terminal completion manifest tamper 거부
- [ ] S3/MinIO versioning/Object Lock 검증
- [ ] 별도 IAM credential의 version-aware object reader가 exact
      key/version/hash/retention을 검증하고 restore/replay drill을 통과
- [ ] orphan 생성 시 retention 연장과 audit item 기록
- [ ] delete marker 발견 시 reconciliation 실패
- [ ] provider timeout/schema drift/quarantine/replay
- [ ] provider별 429 `Retry-After`, jitter, rate-limit, circuit-open과 partial
      target 상태 검증
- [ ] catalog receipt 없는 dead-letter와 caller-supplied locator/hash replay
      거부, catalog ID/version/digest 불일치 거부 및
      requested/running/completed|failed event 재현
- [ ] snapshot maker의 self approval 거부
- [ ] expired/revoked/superseded/cross-project delegation 및 concurrent
      revoke/decision 경합 거부와 유효한
      external-counsel Approval Gate·waiver·cure·opinion·analysis·Citator
      결정 재현
- [ ] legal-review credential 탈취 가정에서 actor/role 위조 거부,
      credential 회전·회수 및 DB membership/audit evidence 재현
- [ ] Citator machine-finalization 거부, exact excerpt/hash와 append-only
      successor/review event 재현
- [ ] legacy JSON을 변조해도 normalized Gate 결과가 변하지 않음
- [ ] Chief Deal Room 표시와 normalized readiness가 일치
- [ ] MCP 전체 비활성 상태의 local retrieval·Legal Pod 성공
- [ ] backup/restore 후 exact version/locator/hash 재현
- [ ] 실제 공식 provider 자격증명·terms와 승인된 byte-preserving
      list/detail/history fixture로 initial/incremental backfill 검증
- [ ] 실제 golden corpus threshold, label hash, maker-checker 법무 승인과
      prediction report 기록

현재 shell에는 migration/runtime/test/sync/review/operations DSN과 role
환경변수가 설정되지 않았으므로 위 live 항목은 실행되지 않았다. repository
회귀 테스트는 코드 경로만 검증한다. 실제 DSN, bucket, provider OC, secret
manager, worker scheduler, counsel-approved corpus가 없는 로컬 실행은 위
live 항목의 완료 증거가 아니다.
