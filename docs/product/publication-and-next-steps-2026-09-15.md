# 코드 업로드와 다음 작업

2026-09-15. GitHub 코드 업로드와 Supabase DB 수정 반영을 완료했다.
운영용 main 병합과 Web/API/Worker 운영 활성화는 아직 하지 않았다.

## GitHub와 검증 결과

- 비공개 저장소: parkwh79-stack/real-estate-os.
- 브랜치: codex/parser-v2-postgres-runtime.
- [검토용 PR #2](https://github.com/parkwh79-stack/real-estate-os/pull/2)는 draft 상태다.
- 검증 코드 기준: f09fd0f. [전체 성공한 GitHub CI](https://github.com/parkwh79-stack/real-estate-os/actions/runs/34915950088).
  이후 최종 기록 커밋은 문서만 변경한다.
- Vercel의 자동 Preview 배포가 성공했다. 실제 API/Worker/S3 운영 연결 검증을
  완료했다는 뜻은 아니다.
- 비밀 환경 파일, 업무 DB, 업로드 원본, .runtime 로그·복구 후보와 백업은
  업로드 대상에서 제외했다.

| 검증 | 결과 |
|---|---|
| API 전체 | 2,193 passed / 40 skipped |
| 실제 PostgreSQL | 55 passed / 0 skipped; migration 재실행, RLS·권한, 승인·근거, 계산·작업 큐·보고서 검증 |
| 브라우저 | 238 passed |
| 웹 품질 | Node 계약 검사, TypeScript, ESLint, production build 통과 |
| Windows 정리 도구 | 6개 검사 통과; 경로 경계와 원본 보존 검사 포함 |

API job의 40개 skip 중 39개는 별도 PostgreSQL job에서 실행한 DB 검사다.
나머지 1개는 외부에 올리지 않은 로컬 원본 Excel 시험이다. 이 원본 시험은
9월 14일 로컬 전체 검사에서 통과했다. skip을 운영 검증 성공으로 계산하지 않는다.

## 업로드 과정에서 추가 수정한 내용

- Windows 8.3 경로명을 정규화해 안전한 임시 파일 정리가 오탐으로 막히지 않게 했다.
- 폐기 가능한 PostgreSQL 시험 DB에 필요한 역할을 준비하고, Parser 시험은
  전용 DB의 실제 realytics 스키마를 사용한다. DB 검사가 skip되면 CI가 실패한다.
- 모의 인증 브라우저 검사를 로컬 .env와 분리했다. 실제 인증 smoke 설정은 별도다.
- 공용 법률 승인 트리거는 테이블을 먼저 분기한 뒤 해당 컬럼을 참조한다.
- 동적 SQL의 조회 성공 여부는 FOUND 대신 ROW_COUNT로 확인한다.
  승인 조회의 outer join은 실제 projection 행을 잠그도록 DB와 API를 함께 수정했다.
- 시험 근거는 DB 시계, 순차 버전, 초안→별도 검토자 승인, 정식 freshness 정책,
  원문 계보와 본문 해시를 사용한다. 운영 승인·시간·테넌트·근거 조건은 유지했다.

동적 SQL 동작 근거: [PostgreSQL 결과 상태 확인](https://www.postgresql.org/docs/16/plpgsql-statements.html#PLPGSQL-STATEMENTS-DIAGNOSTICS).

## Supabase 반영 결과

대상 프로젝트는 iorpwxxzvryuxhraiuhs다.

1. 기존 20260728031948_identity_tenants_select_only.sql의 실제 적용 SQL을 대조한 뒤,
   누락된 realytics.schema_migrations 원장 1행을 복구했다.
   scripts/ops/reconcile-identity-migration-ledger.sql에 검증 조건을 보관했다.
   과거 권한 SQL은 재실행하지 않아 후속 onboarding의 조직명 조회 권한을 보존했다.
2. 새 20260915005540_legal_governance_trigger_record_dispatch.sql을 CLI로 적용했다.
   기존 적용 migration은 수정하지 않았다. 신규 migration은 트리거 함수 4개를 고친다.
3. 적용 후 로컬 25개·Supabase 이력 25개·애플리케이션 원장 25개가 모두 일치했다.
   원장 checksum 불일치 0건, 함수 본문 SHA-256 불일치 0건, 기존 트리거 연결 12개를 확인했다.
4. 함수의 제한된 search path, invoker 실행, 공개 사용자 직접 실행 금지를 유지했다.
   db push 사전 확인 결과 추가 적용 대상은 0개다.
5. 업무 데이터·751 입력·사람 승인·serving 역할의 LOGIN/비밀값은 바꾸지 않았다.

보안 advisor에는 기존 유출 비밀번호 차단 비활성화 경고 1건만 남았다.
[Supabase 설정 조건과 방법](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection)을
확인해 Auth 운영 설정 단계에서 처리한다.

## 다음 작업 순서

| 순서 | 작업 | 완료 조건 |
|---|---|---|
| 1 | 운영 연결과 최소 권한 구성 | API·identity·PF-worker의 전용 연결을 비밀 저장소에 등록하고, 제한 역할로 계산·큐·실행 원장 저장 및 tenant 격리 검증 |
| 2 | 751 원본 검토와 복구 | 적용 원본 버전, 천원→원 단위, 부가세 범위, 월별 거래 행 확정 후 Parser 후보→사람 승인→새 계산 |
| 3 | 실제 환경 통합 검증 | 두 계정의 테넌트 분리와 별도 승인자의 AAL2 승인, 업로드·재분석·보고서, S3 원본 접근, Worker 재시도, 백업 복원 통과 |
| 4 | Auth 운영 설정 | 메일·인증 제한과 유출 비밀번호 차단 설정을 점검하고 실제 계정으로 확인 |
| 5 | 운영 배포 | 위 결과와 PR을 검토한 뒤 main 병합, Web/API/Worker 배포, 실제 업로드→승인→계산→보고서 확인 |

1번이 최우선이다. 현재 realytics_runtime은 NOLOGIN이고 calculation_runs는
SELECT만 가능하다. deal_state_records, async_jobs, agent_runs의 저장 권한도
부족하다. 프로젝트 테이블 쓰기 권한이나 관리자 접속 성공만으로 운영 가능하다고
판정하지 않는다.

751의 기존 잘못된 매출/비용 입력은 자동 덮어쓰지 않았다. 원본 복구 후보와
비교 자료는 로컬에 보존했고, 원본 버전·월별 흐름의 사람 검토가 다음 입력이다.
전체 기능의 PostgreSQL 전환과 나머지 호환 경로의 운영 승격은 각 저장소 계약의
실제 제한 역할 검증을 거쳐 별도 진행한다.

관련 근거: [수정 실행 기록](remediation-execution-2026-09-14.md),
[751 복구 검토](project-751-source-recovery-review-2026-09-14.md),
[운영 적용 전 조건](runtime-activation-gates-2026-09-14.md).
