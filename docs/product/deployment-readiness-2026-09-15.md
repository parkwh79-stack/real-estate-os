# 배포 환경 준비 상태

2026-09-15. 문서 커밋 `36afc00`을 기준으로 저장소·GitHub·로컬 설정의 존재 여부를
읽기 전용으로 확인했다. 비밀값을 출력하거나 원격 리소스를 만들지 않았다.

## 완료한 확인

- [GitHub CI 34916409466](https://github.com/parkwh79-stack/real-estate-os/actions/runs/34916409466)은
  최종 문서 커밋 `36afc008f1a50f2b1965cb3c2e83e7df71b868fc`에서 전체 성공했다.
  API, PostgreSQL, 웹 품질, Windows 정리 도구, 브라우저 검사가 모두 통과했다.
- [PR #2](https://github.com/parkwh79-stack/real-estate-os/pull/2)는 OPEN / draft /
  MERGEABLE이다. Vercel Preview 상태는 SUCCESS이며 Supabase Preview 검사는 SKIPPED다.
- `apps/web/.vercel/project.json`에 `realytics-web` 프로젝트 연결이 있다.
  원격 Vercel 환경 변수의 존재·값과 외부 API 연결은 이 확인으로 검증되지 않는다.
- GitHub 저장소 Actions secret 0개, 변수 0개, `Preview` 환경 secret 0개를 확인했다.
  Vercel이나 별도 클라우드 비밀 저장소의 내용까지 없다는 뜻은 아니다.
- API Dockerfile은 Python 3.12, 비관리자 실행, 플랫폼 `PORT`, `/health/live`를
  사용한다. `.dockerignore`는 환경 파일·원본 런타임·키·DB·테스트를 제외한다.
- 현재 Compose 정의에는 API 서비스 하나만 있다. PostgreSQL, S3/MinIO, ClamAV,
  Worker 서비스와 클라우드 IaC·작업 정의·이미지 배포 workflow는 없다.

## 현재 접근할 수 있는 설정

| 대상 | 확인한 상태 | 다음 실행 조건 |
|---|---|---|
| API 전용 PostgreSQL | API 환경 파일에 전용 API DSN 없음 | 안전한 LOGIN 역할의 전용 연결을 비밀 저장소에서 주입 |
| Identity PostgreSQL | API 환경 파일에 전용 identity DSN 존재 | 비밀 저장소 관리·회전 여부 및 실제 역할 검증; 존재만으로 연결 성공 판정 금지 |
| PF Worker PostgreSQL | 전용 DSN과 Worker tenant 설정 없음 | 전용 연결·테넌트·단일 `pf_worker` 풀·실제 권한 postflight |
| Web/BFF | 로컬 환경의 Web/API origin은 모두 HTTP loopback | 승인된 외부 HTTPS API origin과 정확한 CORS/인증 callback 연결 |
| S3/MinIO | 로컬에 bucket, endpoint, KMS, retention 설정 없음 | 접근 가능한 버킷·IAM·버전 관리·Object Lock·암호화 검증 |
| ClamAV | 로컬에 host/port 구성 없음 | 격리된 scanner endpoint와 정상·실패·감염 차단 rehearsal |
| Secrets Manager | 로컬 AWS 환경 변수·공용 AWS credentials/config 없음 | 승인된 계정/역할과 비밀 참조 경로; 업무 프로세스별 주입 범위 |
| 실제 인증 smoke | `apps/web/.auth` 없음 | 초대된 실제 사용자의 최신 로그인 상태; AAL2 및 별도 검토자는 사람 참여 필요 |

현재 셸 PATH에는 AWS, Docker, Terraform, Vercel CLI가 없고 GitHub CLI만 있다.
표준 Vercel CLI 인증 파일과 Docker 설정 파일도 확인되지 않았다.
Web 환경 파일에 배포용 OIDC 변수 이름은 있으나, 토큰의 유효성이나 클라우드
리소스 접근 권한을 확인한 것은 아니다. 이 기록은 접근 가능한 로컬 설정의
인벤토리이며 다른 계정의 인프라가 존재하지 않는다고 단정하지 않는다.

조회 시점의 로컬 `127.0.0.1:8000/health/ready`와
`127.0.0.1:3000/api/auth/supabase/readiness`는 응답하지 않았다.
로컬 개발 서버를 다시 켜는 것과 운영 연결 검증은 서로 다른 완료 조건이다.

## 코드로 확인한 배포 차단 지점

`scripts/run_pf_review_worker.py`는 이미 전용 DSN, tenant, `pf_worker` 단일 풀과
풀 수명 관리를 사용한다. 7월 30일의 `deployment-plan.md`에 남은 PF claim 경로
진단은 9월 수정 전 내용이므로 현재 PF 코드의 판정 근거로 재사용하지 않는다.

다만 별도 Agent outbox Worker는 다음 경로가 남아 있다.

1. `scripts/run_agent_platform_worker.py`는 전용 풀을 열거나 tenant를 지정하지 않는다.
2. `agent_platform/operational/repository.py`의 `claim_outbox`는
   `storage.claim_runtime_outbox_event_records`를 호출한다.
3. `db/postgres.py`의 `claim_runtime_outbox_records`는 일반 `connect()`를 열고
   `runtime_outbox_events`를 조회한다. 전용 Worker 풀 및 트랜잭션 tenant가 없다.
4. Parser outbox handler는 기본 `ParserV2PostgresRepository()`를 사용한다.
   Handler별 저장·조회에도 Worker 범위가 일관되게 전달되어야 한다.

따라서 이 독립 Agent Worker는 관리자 DSN으로 실행해 보정하면 안 된다.
전용 Worker 연결·tenant 제한·lease 소유권·재시도·종료 수명을 구현하고 실제
제한 역할로 검증한 뒤 활성화한다. PF Worker의 수정·시험 결과를 Agent Worker의
운영 검증으로 대신하지 않는다.

## 실행 가능한 다음 작업

1. 기존 DB의 역할·권한을 읽고, version-controlled manifest와 차이를 대조한다.
   권한 보정 후에는 역할 postflight와 별도 LOGIN 접속 검사를 구분해 기록한다.
2. Agent outbox Worker의 위 결함을 수정하고 폐기 가능한 PostgreSQL에서 tenant
   분리, 동시 claim, lease 만료·재시도, 실패 후 원장 상태를 검증한다.
3. 연결 자격증명 없이도 실행 가능한 Dockerfile 계약 검사, 환경 구성 검사,
   migration wrapper 검사와 전체 CI는 계속 실행할 수 있다. Docker 엔진이 없어
   로컬 이미지 build/run은 현재 확인할 수 없다.
4. 인프라가 연결되면 API preflight를 먼저 실행한다.
   `scripts/preflight_postgres_api_runtime.py`는 안전한 결과 코드만 출력한다.
   실제 identity 역할·회원 조회는 `scripts/verify_identity_runtime.py`가 담당한다.
   후자는 예외를 그대로 전달하므로 CI 증거 수집 시 raw driver traceback을 공개하지 않는다.
5. `/health/ready`의 DB checkout 성공 뒤에도 S3와 ClamAV는 별도 실검증한다.
   이 endpoint는 두 서비스에 대해 `connectivityChecked=false`를 명시한다.
   버전 관리·Object Lock·암호화·원본 해시 재조회·격리·백업 복원까지 검증하는
   통합 smoke 명령은 아직 준비되어 있지 않다.
6. 실제 Web/API와 초대 사용자 준비 후
   `npm --workspace web run test:e2e:auth-live`를 실행한다. 설정과 사람 참여 조건은
   [실제 인증 runbook](live-auth-smoke-runbook.md)을 따른다. 승인/게시 동작은
   전용 rehearsal 프로젝트·실행·보고서와 서로 다른 실제 승인자를 사용한다.

당장 필요한 외부 입력은 승인된 staging 계정/서비스 대상, 비밀 저장소의 접근
경로, 전용 API/Worker 연결, S3/KMS/IAM, scanner, 공개 HTTPS API origin이다.
대화에 비밀번호를 붙이는 방식 대신 기존 계정 연결·역할·비밀 참조를 사용한다.
751 원본의 단위·부가세·월별 현금흐름 검토와 사람 승인도 별도 필수 조건이다.

관련 기록: [업로드와 다음 작업](publication-and-next-steps-2026-09-15.md),
[운영 적용 전 조건](runtime-activation-gates-2026-09-14.md),
[배포 설계](deployment-plan.md).
