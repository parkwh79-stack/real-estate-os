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
- 업로드 후 검토용 PR과 GitHub CI 결과를 확인한다. main 병합 및 실제 운영
  배포 완료와 코드 업로드 완료는 별도 상태다.

## Supabase 대조

- 대상 프로젝트: `iorpwxxzvryuxhraiuhs`.
- Supabase migration 이력에는 로컬 24개 migration이 모두 있다.
- 별도 `realytics.schema_migrations` 원장에는 23개만 있다. 기존 23개 checksum은
  로컬과 일치하며 `20260728031948_identity_tenants_select_only.sql` 기록 1개가 없다.
- 해당 권한이 실제 적용됐는지 확인한 뒤 원장 불일치를 해소해야 한다.
  모든 migration을 무조건 재실행하거나 업무 데이터를 올리지 않는다.
- runtime 역할의 계산/작업 큐/실행 원장 권한은 아직 부족하다. 역할별 권한
  provisioner와 실제 LOGIN·비밀 주입·제한 역할 검증을 함께 완료해야 한다.

## 다음 작업 순서

1. GitHub PR의 Linux·Windows·브라우저 및 실제 PostgreSQL CI를 확인하고 실패를 수정한다.
2. Supabase 원장 불일치를 해소하고 API·identity·PF-worker의 별도 연결 및 권한을 검증한다.
3. 751에 적용할 원본 버전을 확인하고 단위·부가세·월별 거래 행을 검토한 후
   Parser 후보 → 사람 승인 → 새 계산 순서로 복구한다.
4. staging에서 두 계정의 테넌트 격리와 승인, 업로드·재분석·보고서,
   Worker 재시도, S3 원본 접근과 백업 복원을 검증한다.
5. 위 결과를 확인한 뒤 PR 병합과 Web/API/Worker 운영 배포를 결정한다.

관련 근거: [수정 실행 기록](remediation-execution-2026-09-14.md),
[751 복구 검토](project-751-source-recovery-review-2026-09-14.md),
[운영 적용 전 조건](runtime-activation-gates-2026-09-14.md).
