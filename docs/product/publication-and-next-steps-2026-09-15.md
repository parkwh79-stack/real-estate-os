# 코드 업로드와 다음 작업

2026-09-15. 사용자 지시에 따라 현재까지의 수정 코드를 GitHub에 보관하고
Supabase 적용 이력과 남은 운영 조건을 대조한다.

## GitHub

- 대상: 비공개 `parkwh79-stack/real-estate-os`.
- 현재 작업 브랜치: `codex/parser-v2-postgres-runtime`.
- 검증된 변경 파일 110개를 검토했다. 비밀 환경 파일, 업무 DB, 업로드 원본,
  `.runtime` 로그/후보 파일과 백업은 업로드 대상에 포함하지 않는다.
- 기존 API 2,193개 통과 / PostgreSQL 18개 skip, 브라우저 238개 통과,
  타입·린트·빌드 통과, npm audit 0건을 기준으로 커밋한다.
- `db92adc` 커밋 111개 파일을 업로드하고 [검토용 PR #2](https://github.com/parkwh79-stack/real-estate-os/pull/2)를 만들었다.
  GitHub의 첫 실행에서 API와 웹 품질 검사가 통과했다. Windows의 8.3 경로명과
  PostgreSQL 역할 bootstrap 누락을 추가 발견해 수정하고 다시 검증한다.
  정리 도구의 경로 경계 검사를 유지한 로컬 회귀시험 6개가 통과했다.
  일반 PostGIS에는 Supabase의 기본 역할도 없어 시험 전용 bootstrap에서 생성한다.
  이 도구는 명시적인 시험 DSN만 허용하며 애플리케이션 DSN으로 대체하지 않는다.
  브라우저 계약 검사는 `.env`의 실제 Supabase 설정에 의존하던 부분을 제거하고
  인증 라우트 대역용 가짜 설정을 사용한다. 실제 인증 smoke 설정에는 적용하지 않는다.
  실제 PostgreSQL 검증에서 한 DB의 여러 스키마가 과거 policy 이름 검사와 충돌했다.
  Parser live 시험은 전용 임시 DB 안의 실제 `realytics` 스키마를 사용하도록 바꿨다.
  원본 migration이나 운영 권한 검사를 약화하지 않고 격리 단위를 수정했다.
  이 환경에서 27개 PostgreSQL 검사가 통과했다. 추가 7개는 7월로 고정된 시험 승인
  시각이 DB의 5분 인과성 제한에 걸렸다. 승인 제한은 유지하고 시험 데이터를 DB
  시계와 근거 저장 이후의 결정 시각에 맞춰 수정했다.
  같은 시험 자료의 구형 승인 생성 방식도 현행의 초안 v1 → 별도 검토자 승인 v2와
  실제 claim 문자열 해시 계약에 맞췄다. 기존 승인 제약은 유지했다.
  실제 PostgreSQL은 공용 delegation/snapshot 트리거의 없는 컬럼 참조 결함도
  발견했다. 테이블별 분기 뒤 컬럼을 해석하도록 신규 migration을 추가했다.
  과거 migration은 보존하며 승인·위임·근거·cure 검증 조건을 유지한다.
  실제 테이블 구조를 사용하는 회귀 검사와 기존 유효/무효 근거 검사를 함께 실행한다.
  이 단계에서 47개 중 45개가 통과했고, 남은 두 건은 동적 SQL의 `FOUND`를
  잘못 사용해 정상 projection을 없다고 판단하는 결함이었다. 신규 migration에
  projection/decision의 `ROW_COUNT` 확인과 nullable join의 잠금 대상 수정을 추가했다.
  두 종류의 검토 테이블에서 정상 predecessor는 통과하고 누락·다른 테넌트·다른
  프로젝트 predecessor는 거부하는 PostgreSQL 회귀 검사를 추가한다.
  근거: [PostgreSQL 동적 SQL 결과 확인](https://www.postgresql.org/docs/16/plpgsql-statements.html#PLPGSQL-STATEMENTS-DIAGNOSTICS).
  추가 검증은 55개 중 54개를 통과했다. 마지막 승인 위임 시험의 이틀 전 생성
  시각도 현행 DB 시계 제한에 맞게 고쳤다. 운영 DB의 시간·승인 제한은 유지한다.
  API의 같은 projection 조회도 `FOR SHARE OF projection`으로 잠금 대상을 명시한다.
  nullable outer join의 근거 쪽을 잠그려다 승인 조회가 실패하지 않도록 맞췄다.
  승인 저장 후 실제 readiness 검사에는 단조 증가 snapshot 버전, 정식 freshness
  정책, 명시적 원문 계보와 본문 SHA-256이 필요했다. 기존 시험 자료를 이 계약에
  맞추고 실패 시 readiness 사유 전체를 출력하도록 보강했다.
  main 병합 및 실제 운영
  배포 완료와 코드 업로드 완료는 별도 상태다.

## Supabase 대조

- 대상 프로젝트: `iorpwxxzvryuxhraiuhs`.
- Supabase migration 이력에는 로컬 24개 migration이 모두 있다.
- 별도 `realytics.schema_migrations` 원장에는 처음에 23개만 있었다. 기존 23개
  checksum은 로컬과 일치했고 `20260728031948_identity_tenants_select_only.sql` 기록만 없었다.
- Supabase 이력의 실제 SQL과 로컬 원본은 외곽 공백을 제외하면 바이트 단위로 같다.
  검증한 SQL 해시와 migration 잠금을 조건으로 누락된 원장 1행을 복구했다.
  실행 스크립트는 `scripts/ops/reconcile-identity-migration-ledger.sql`이다.
- 과거 권한 SQL은 재실행하지 않았다. 후속 onboarding migration이 허용한 조직명
  조회를 보존하고, 메타데이터 조회와 테넌트 수정 권한이 여전히 없음을 확인했다.
  업무 데이터·원본·승인·비밀값과 LOGIN 설정은 변경하지 않았다.
- runtime 역할의 계산/작업 큐/실행 원장 권한은 아직 부족하다. 역할별 권한
  provisioner와 실제 LOGIN·비밀 주입·제한 역할 검증을 함께 완료해야 한다.
- Supabase 보안 advisor에는 유출 비밀번호 차단 비활성화 경고 1건이 있다.
  [설정 조건과 방법](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection)을
  확인해 운영 Auth 설정 단계에서 처리한다.

## 다음 작업 순서

1. GitHub PR의 Linux·Windows·브라우저 및 실제 PostgreSQL CI를 확인하고 실패를 수정한다.
2. API·identity·PF-worker의 별도 연결 및 권한을 검증한다. Supabase 원장 누락은 복구했다.
3. 751에 적용할 원본 버전을 확인하고 단위·부가세·월별 거래 행을 검토한 후
   Parser 후보 → 사람 승인 → 새 계산 순서로 복구한다.
4. staging에서 두 계정의 테넌트 격리와 승인, 업로드·재분석·보고서,
   Worker 재시도, S3 원본 접근과 백업 복원을 검증한다.
5. 위 결과를 확인한 뒤 PR 병합과 Web/API/Worker 운영 배포를 결정한다.

관련 근거: [수정 실행 기록](remediation-execution-2026-09-14.md),
[751 복구 검토](project-751-source-recovery-review-2026-09-14.md),
[운영 적용 전 조건](runtime-activation-gates-2026-09-14.md).
