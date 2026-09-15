# 운영 연결 준비와 후속 실행

2026-09-15. 이전 [업로드 기록](publication-and-next-steps-2026-09-15.md)에
이어 전용 DB 권한, Worker 실행 경로, 751 원본과 신규 인프라 구성을 점검했다.
이 문서는 운영 준비 완료나 사람의 사업 입력 승인을 대신하지 않는다.

## 이번 변경

- API 시작 검사를 기존 전체 테이블·컬럼·시퀀스·함수 권한 계약으로 확대했다.
  부족한 계산·queue·Agent 원장 권한, 컬럼 전용 과다 권한과 객체 소유권을 검출한다.
- PF Worker는 전용 역할명과 직접 LOGIN 연결을 요구한다. API/identity 역할 재사용,
  소유자·BYPASSRLS·상속과 허용 범위 밖 권한을 거부한다. 승인 readiness 조회도
  현재 Worker tenant 트랜잭션을 사용하고 정규화 assumption override를 조회한다.
  상세 계약은 [전용 로그인 검사](runtime-login-preflight-hardening-2026-09-15.md)에 있다.
- 별도 Agent outbox Worker는 tenant claim·handler·lease 경로가 미완성이므로
  production/prod/staging에서 시작을 차단한다. 로컬 실험과 CLI 도움말은 유지한다.
- 새 migration `20260915060531_runtime_role_privilege_alignment.sql`은 기존
  `realytics_runtime`에 현재 API manifest를 정렬하고 `realytics_identity`에
  `extensions`의 USAGE만 보완한다. CLI로 새 버전을 생성했고 원본과 Supabase
  wrapper를 함께 관리한다. 기존 migration 수정, LOGIN 활성화, 비밀번호 등록,
  PF 역할 생성이나 업무 데이터 변경은 포함하지 않는다.

## 실제 연결에서 발견한 사항

전용 identity DSN으로 TLS 연결 후 읽기 전용 트랜잭션 검사를 실행했다.
`current_user`와 `session_user`는 모두 `realytics_identity`였으며,
누락 항목은 `extensions` 스키마 USAGE 한 가지였다. 비밀값과 driver 원문 오류는
출력하지 않았고 안전한 결과만 무시된 로컬 runtime 폴더에 보관했다.

기존 `realytics_runtime`은 NOLOGIN이며 프로젝트 CRUD 외 계산·queue·Agent
원장·회원 관리 등에 필요한 권한 127개가 부족했다. 새 migration은 안전한 속성·상속·소유권을
먼저 확인하고 정해진 권한만 부여한 다음 998개 유효 권한을 같은 트랜잭션에서
대조한다. 운영 비밀 저장소가 생기기 전까지 API LOGIN은 활성화하지 않는다.

## 원본과 인프라

[751 월별 매핑 준비](project-751-recovery-preparation-2026-09-15.md)에서 원본의
54개월과 616개 합계·잔액 연결을 검증했다. 최대 차이는 0.00012원이다.
원본·후보·업무 DB는 그대로 보존했으며 보증금·부가세·금융 약정·시점 확인 전에는
후보의 미확정 금액이나 승인 상태를 변경하지 않는다.

사용자는 기존 API/Worker 운영 환경과 비밀 저장소가 없어 신규 구성안을 요청했다.
[신규 인프라 제안](infrastructure-proposal-2026-09-15.md)은 Vercel·Supabase를
유지하면서 AWS Fargate·S3·KMS·Secrets Manager를 연결한다. 저사용량 시험 환경의
신규 AWS 예산은 월 USD 250–320이며 계정·리전·보존 정책과 실제 견적 확정이
필요하다. 현재 Supabase는 도쿄에 있다. 유료 자원 생성이나 배포는 실행하지 않았다.

## 검증 기록

변경 범위의 집중 회귀 검사 200개와 Web 타입·lint가 통과했고, 독립 검토에서도
326개 SQL 명령 및 998개 사후 검사의 최신 manifest 일치를 확인했다.
로컬 전체 API 결과는 2,253 passed / 40 skipped / 0 failed다. DB 자격증명 없는
검사는 원격의 폐기 가능한 PostgreSQL CI로 보완한다. 최종 집중 검사는 전체 검사
수집 후 추가된 수정도 포함한다. Supabase dry-run은 이번 새 migration 한 개만
대상이며 seed와 역할 생성은 없다.
실제 PostgreSQL CI는 **56 passed / 0 skipped**이며 새 migration의 전체 적용과
직접 Worker LOGIN, tenant 분리, 승인·identity 쓰기 거부, 미등록 테이블·컬럼 및
PUBLIC 권한 누수 검출을 포함한다. 로컬 테스트 산출물과 비밀값은 Git에 포함하지 않는다.

## Supabase 적용 결과

검증한 코드 커밋은 `e1b06e3e899a91ac4bbdb9335177cb51237f16ba`이며
[CI 실행 34936530114](https://github.com/parkwh79-stack/real-estate-os/actions/runs/34936530114)의
PostgreSQL 단계 통과 후 연결된 프로젝트에 새 migration 한 개를 적용했다.
같은 커밋의 CI 다섯 항목은 모두 성공했다: API **2,253 passed / 41 skipped**,
PostgreSQL **56 passed / 0 skipped**, browser **238 passed**, Web 타입·lint·build와
Windows cleanup **6개** 통과. 일반 API job의 40개 DB 검사는 별도 PostgreSQL
job에서 실행하며, private 원본 워크북 한 개는 로컬에만 있어 CI에서 건너뛴다.
Vercel Preview도 성공했다. Main 병합과 운영 배포는 아직 진행하지 않았다.

- 적용 후 **998개 검사 불일치 0건**. 적용 전 부족했던 권한 127개를 정렬했다.
- 로컬 원본, Supabase 배포 원장, 애플리케이션 원장 **각 26개**의 버전과 원본
  SHA-256이 모두 일치한다. 재실행 dry-run은 up-to-date이며 남은 migration은 없다.
- 실제 `realytics_identity` TLS LOGIN의 preflight는 **문제 0건**이다.
  `extensions` USAGE는 true, CREATE는 false다.
- 같은 연결에서 명명된 API 역할 전체를 읽기 전용 검사한 결과는 의도한
  `role_nologin` 한 개뿐이다. API 실제 LOGIN 연결 성공으로 표현하지 않는다.
  Runtime의 NOLOGIN, identity의 LOGIN과 두 역할의 안전한 속성은 그대로다.
- PF 역할·비밀번호·유료 자원은 생성하지 않았다. 업무 레코드와 기존 원본은
  수정하지 않았다. 보안 advisor는 기존 Auth 유출 비밀번호 차단 미설정 경고
  한 개만 남았으며 새 경고는 없다.

증거는 무시된 `.runtime/activation-20260915-remote-postflight.json`과
`.runtime/activation-20260915-identity-postflight.json`에 비밀값 없이 보관한다.
현재 배포 상태와 사람 검토 조건은 아래 후속 항목을 따른다.

## 남은 실행 순서

1. PF Worker의 공유 `orchestrations`·`assumption_proposals` payload 안에서
   자동 초안과 사람 결정을 DB가 구분하도록 쓰기 경계를 구현한다. 역할 검사만
   통과해도 이 조건이 끝나기 전에는 Worker를 운영에 올리지 않는다.
2. 신규 계정·리전·예산·보존 기간을 확정하고 검토 가능한 IaC를 준비한 뒤 기반을
   구성한다. 전용 LOGIN 비밀을 저장소로 주입하고 합성 tenant로 인수 검사한다.
3. 실제 S3·ClamAV·복원, 두 사용자의 로그인·AAL2 승인, Web/API 연결을 확인한다.
4. 751 담당 검토자가 원본 버전과 금액 의미를 확정하면 승인 입력과 새 계산을
   연결해 복구한다. 별도 Agent Worker의 완전한 tenant/lease 구현은 별도 인수한다.
