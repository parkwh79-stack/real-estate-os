# Legal Intelligence Corpus 운영 가이드

Updated: 2026-07-18

Status: 코드·계약 회귀 완료, 실제 PostgreSQL/S3/provider 운영 검증 대기

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
   role은 허용된 global table에 `SELECT, INSERT`만 가진다.
8. 원문 reconciliation은 삭제하지 않는다. 고아 객체는 보존하고 사람이
   별도 승인한 disposition 절차로만 처리한다.
9. stale·unverified·conflicted 결과는 표시할 수 있으나 approval gate를
   해제하지 못한다.

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

## 3. 비밀과 환경변수

비밀은 AWS Secrets Manager 또는 승인된 동등 저장소에서 process
environment로 주입한다. `.env`, 명령행 인자, URL, 로그, evidence metadata,
브라우저 번들에 실제 값을 남기지 않는다.

```dotenv
# DDL 전용
REALYTICS_MIGRATION_DATABASE_URL=<deployment-role-dsn>

# serving API 전용
REALYTICS_DATABASE_URL=<realytics-runtime-dsn>

# 공식 corpus worker/reconciliation 전용
REALYTICS_LEGAL_SYNC_DATABASE_URL=<realytics-legal-sync-dsn>

REALYTICS_DATABASE_SCHEMA=realytics
REALYTICS_POSTGIS_SCHEMA=extensions
REALYTICS_LEGAL_SYNC_SEARCH_PATH=realytics,extensions,public,pg_temp
REALYTICS_LEGAL_SYNC_MAX_PAGES=10000

REALYTICS_LEGAL_RAW_OBJECT_STORAGE=s3
REALYTICS_LEGAL_RAW_BUCKET=<private-object-lock-bucket>
REALYTICS_LEGAL_RAW_PREFIX=realytics
REALYTICS_LEGAL_RAW_LOCK_MODE=COMPLIANCE
REALYTICS_LEGAL_RAW_RETENTION_DAYS=365

LAW_OC=<secret>
```

세 DSN은 서로 대체하지 않는다. 특히
`REALYTICS_LEGAL_SYNC_DATABASE_URL`이 없으면 corpus repository는 일반
runtime DSN으로 폴백하지 않고 즉시 실패한다.

## 4. PostgreSQL/Supabase 적용

### 4.1 사전조건

- `realytics`: 비공개 application schema
- `extensions`: PostGIS와 `pg_trgm`
- `public`: migration/sync가 명시적으로 참조하는 공유 객체만 허용
- migration, runtime, legal-sync principal 분리
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

dry-run에서 private schema, extensions 위치, `0008_legal_intelligence_core.sql`,
role bootstrap, destructive statement 유무를 검토한 뒤에만 적용한다.

```powershell
npm run supabase:db:push
```

### 4.3 역할 provisioning

```powershell
$env:REALYTICS_RUNTIME_DATABASE_ROLE = 'realytics_runtime'
$env:REALYTICS_LEGAL_SYNC_DATABASE_ROLE = 'realytics_legal_sync'

& '.\.venv\Scripts\python.exe' `
  services/api-server/scripts/provision_postgres_runtime.py
& '.\.venv\Scripts\python.exe' `
  services/api-server/scripts/provision_legal_sync_role.py
```

두 스크립트는 비밀번호가 없는 `NOLOGIN` 역할과 권한만 만든다. 운영자가
승인된 IAM/secret-manager 절차로 `LOGIN`과 credential을 활성화한다.

legal-sync role의 필수 속성:

- `NOSUPERUSER`
- `NOCREATEDB`
- `NOCREATEROLE`
- `NOINHERIT`
- `NOREPLICATION`
- `NOBYPASSRLS`
- `realytics`, `extensions` schema `USAGE`
- 관리 대상 table의 `SELECT, INSERT`
- 모든 table의 `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES`, `TRIGGER` 금지
- schema `CREATE` 금지

serving runtime은 completion attestation과 checkpoint를 `SELECT`할 수
있지만 global corpus에 `INSERT`할 수 없다.

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

즉시 알람:

- stale checkpoint가 정책 한도를 초과
- 동일 scope lease conflict가 반복
- fencing reject 발생
- terminal manifest 검증 실패
- Object Lock/versioning 비활성
- delete marker 발견
- source schema drift
- legal-sync role over-privilege audit 실패
- runtime role의 global corpus write 권한 발견

## 9. Retry와 DLQ

- 네트워크 read만 bounded retry한다.
- page publish는 동일 idempotency identity로 replay한다.
- stale checkpoint/fencing/effective-time/manifest 오류는 자동 무한 재시도하지
  않는다.
- provider schema drift payload는 raw object와 hash를 보존하고 quarantine한다.
- DLQ item에는 provider, scope, checkpoint, adapter/schema version,
  sanitized error code, raw object identity, retry count를 기록한다.
- secret, 원문 전체, credential-bearing URL을 DLQ metadata에 넣지 않는다.
- 수동 replay는 adapter release와 fixture contract test 통과 후 새 audit
  identity로 실행한다.

## 10. Backup, restore, disaster recovery

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

## 11. Live acceptance matrix

운영 완료 증거에는 다음 결과와 실행 시각, 환경, commit SHA, 담당자를
남긴다.

- [ ] `0008`을 실제 PostgreSQL/PostGIS에 적용
- [ ] PostGIS=`extensions`, application table=`realytics`
- [ ] migration/runtime/legal-sync principal 분리
- [ ] runtime global corpus write 거부
- [ ] legal-sync update/delete/DDL 거부
- [ ] missing tenant context fail-closed
- [ ] tenant A/B 교차 조회·join·count·error 격리
- [ ] session pooler에서 tenant GUC transaction scope 검증
- [ ] 두 worker의 동일 scope lease 경합에서 한 worker만 commit
- [ ] lease 만료 후 구 fencing token commit 거부
- [ ] page 중간 장애 시 checkpoint 미전진
- [ ] 동일 page replay 시 중복 version/checkpoint 미생성
- [ ] terminal completion manifest tamper 거부
- [ ] S3/MinIO versioning/Object Lock 검증
- [ ] orphan 생성 시 retention 연장과 audit item 기록
- [ ] delete marker 발견 시 reconciliation 실패
- [ ] provider timeout/schema drift/quarantine/replay
- [ ] MCP 전체 비활성 상태의 local retrieval·Legal Pod 성공
- [ ] backup/restore 후 exact version/locator/hash 재현
- [ ] golden corpus threshold와 법무 승인 기록

현재 repository 회귀 테스트는 이 계약의 코드 경로를 검증한다. 실제 DSN,
bucket, provider OC, secret manager, worker scheduler가 없는 로컬 실행은 위
live 항목의 완료 증거가 아니다.
