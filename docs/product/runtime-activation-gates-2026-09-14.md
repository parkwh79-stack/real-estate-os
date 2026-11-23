# 정규화 런타임 적용 전 검증

2026-09-14. 수정 코드에 대한 운영 적용 기록이 아니라 인수 조건이다.
현재 사용자 지시는 로컬 정리와 결함 수정을 허용한다. 원격 역할·비밀값·승인
입력·배포는 이 작업에서 변경하지 않았다.

## 실제 확인한 상태

Supabase `iorpwxxzvryuxhraiuhs`에 읽기 전용 조회를 수행했다.

| 항목 | 확인 결과 |
|---|---|
| 인증 사용자 / 검증된 MFA | 2명 / 1개 |
| realytics_identity | LOGIN, NOSUPERUSER, NOBYPASSRLS |
| realytics_runtime | NOLOGIN, NOSUPERUSER, NOBYPASSRLS |
| projects | runtime에 SELECT / INSERT / UPDATE 있음 |
| calculation_runs | runtime에 SELECT만 있음 |
| deal_state_records / async_jobs / agent_runs / chief_pod_artifacts | runtime에 SELECT / INSERT / UPDATE 없음 |
| project/calculation RLS | realytics.tenant_id 트랜잭션 설정과 tenant_id 비교 |

따라서 관리자 연결 성공이나 로컬 SQLite 검사 성공을 실제 API/Worker 성공으로
볼 수 없다. 현재 클라우드 권한으로는 수정된 계산 저장 경로를 운영할 수 없다.

## 코드에 준비한 변경

- API 요청은 검증된 테넌트에서 프로젝트 작업 사본을 만들고 읽은 버전으로 CAS한다.
  Parser V2의 프로젝트 조회도 같은 범위를 사용한다.
- 계산·Outbox·Deal State는 한 트랜잭션에서 저장한다. 원자 저장이 실패하면
  JSON 사본으로 우회하지 않는다.
- 정규화 Parser V2, Agent 실행/Outbox와 PF Worker는 요청/Worker 전용
  트랜잭션을 사용한다. PF Worker는 `REALYTICS_PF_WORKER_TENANT_ID`를
  명시해야 하며, 지정 테넌트의 큐만 `SKIP LOCKED`로 가져온다.
- Worker 실행 파일은 전용 DSN과 pf_worker 풀만 허용하고, 풀 시작·종료를
  프로세스 수명에 연결한다. 종료 예외에서도 풀을 닫는 시험을 통과했다.
- Worker의 최종 저장은 lease owner 및 attempt 번호가 맞아야 한다.
  소유권을 잃으면 `lease_lost`를 반환하며 다른 Worker의 작업을 완료시키지 않는다.
- `db/postgres_runtime_privileges.py`에 실제 계산/큐/실행 저장에 필요한
  DML과 금지 권한을 명시했다. 원격 GRANT/REVOKE는 실행하지 않았다.
- CI PostgreSQL job은 명시적인 시험 DSN과 realytics 스키마를 사용하고,
  Parser V2 제한 역할 시험을 포함한다. PostgreSQL 검사가 skip되면 CI를 실패시킨다.

## 적용 및 실검증 순서

1. 운영 책임자가 staging 대상과 테넌트를 정하고 기존 비밀 저장소를 통해
   API·identity·PF-worker의 별도 LOGIN 계정을 연결한다. 대화에 비밀번호를 붙이지 않는다.
   주요 설정은 `REALYTICS_API_DATABASE_URL`, `REALYTICS_IDENTITY_DATABASE_URL`,
   `REALYTICS_PF_WORKER_DATABASE_URL`, `REALYTICS_PF_WORKER_TENANT_ID`,
   `REALYTICS_PARSER_V2_PERSISTENCE=postgres_normalized`다.
2. 배포 역할로 migration/권한 provisioner를 실행하고, 동일 설정으로 readiness와
   권한 postflight를 검증한다. serving 역할은 owner/superuser/BYPASSRLS가 아니어야 한다.
3. 폐기 가능한 DB에서 CI의 live PostgreSQL 검사를 실제로 실행한다.
   로컬에서는 시험 DSN과 PostgreSQL 서버가 없어 해당 18개 검사를 건너뛰었다.
4. 같은 테넌트의 신규 프로젝트 업로드→승인 입력→계산→재조회, 다른 테넌트의
   읽기/쓰기 거부, 동시 편집 충돌, Worker 종료→lease 만료→재수행을 검증한다.
5. 실제 다른 승인자의 AAL2 승인, S3 원본 해시/버전/접근 권한, 악성 파일 격리,
   백업 복원 후 원장·원본 연결을 확인한다. 로컬 health HTTP 200은 이 검증을 대체하지 않는다.

로컬 브라우저 시험은 mock 응답을 포함하므로 실제 회원 초대 메일, 외부 OAuth,
S3, 유료 LLM, 클라우드 Worker 실행을 검증했다고 보고하지 않는다.
이번에 원격 migration, 신규 사용자 생성, 이메일 발송, 실제 승인, 배포를 수행하지 않았다.

## 남은 범위

751은 별도 원본 대조 문서의 사람 검토가 먼저다. 월별 거래 행 매핑과
부가세/담보·회수 기준이 없는 상태에서 새 승인 계산을 만들지 않는다.
기존 대형 `routers/projects.py`/`storage.py`의 모든 기능이 정규화된 것은 아니다.
이번 범위는 진단된 업로드·계산·재분석·파서·실행 원장 연결이며, 나머지 호환
경로의 운영 승격은 해당 저장소 계약과 실제 제한 역할 검증 후 판단한다.
