# REALYTICS 코드베이스 재진단 — 2026-09-14

> 수정 전 진단 기록이다. 최신 수정·검증 상태는
> [실행 기록](remediation-execution-2026-09-14.md)을 기준으로 한다.

## 판단

현재 상태는 **수정 중인 내부 파일럿**이다. 기존 코드를 전부 폐기할 근거는 없다.
결정론 계산, 근거 추적, 인증, 테넌트 권한, 문서 검토의 기반과 회귀 테스트는
보존할 가치가 있다. 우선해야 할 일은 기능 추가보다 **원본 → 승인 입력 → 계산
저장 → 재분석 → 화면/보고서**의 연결을 완성하고 실제 데이터 정합성을 회복하는 것이다.

특정 모델을 썼다는 사실만으로 오류 원인을 단정하지 않는다. 이번에 확인한 문제는
구형/신형 저장 경로의 공존, 호출부까지 연결되지 않은 변경, 상태 의미의 혼동,
오래된 완료 문서와 검증 공백으로 설명된다.

## 검토 범위와 근거

- 실제 Git 저장소: `realytics-platform9-monorepo`, 기준 커밋 `a05cc6d`.
  상위 `real estate os`에도 커밋 없는 별도 `.git`이 있다. 두 저장소를 혼동하지 않는다.
- 시작 시 미커밋 상태 48개 항목을 확인했다. 기존 기능 수정은 되돌리지 않았다.
- 파일 목록 989개를 분류하고 Python 587개 파일을 AST 구문 검사했다. 구문 오류는 0개다.
- API 회귀 테스트 파일 234개, 수집된 테스트 2,172개. 웹/인증/DB/계산/파싱/
  재분석/승인/내보내기 핵심 연결을 상세 검토했다.
- `AGENTS.md`, 아키텍처 계약, backlog, 기존 18개 결함 계획, 최신 작업 기록을 대조했다.
  전 파일의 모든 업무 규칙을 검증했다는 뜻은 아니다.
- 로컬 SQLite 751은 `mode=ro`와 `PRAGMA query_only=ON`으로 읽었다.
  원본·계산·승인 기록을 수정하거나 삭제하지 않았다.
- 클라우드 DB migration 실행, 배포 변경, 실제 사용자 승인, 실제 메일 발송은 수행하지 않았다.

## 검증 결과

| 검증 | 결과 | 해석 |
|---|---|---|
| Python 전체 구문 검사 | 587개 통과 | 구문 검사이며 업무 정확성 증명은 아님 |
| Python 의존성 `pip check` | 통과 | 설치 의존성 호환 검사이며 보안 검사 아님 |
| 웹 `check-types` | 통과 | Next route type generation + TypeScript |
| 웹 `lint` | 통과 | 경고 허용 0 |
| 웹 production build | 정리 전·후 통과 | 실제 배포/업무 E2E와 구분 |
| 브라우저 전체 검사 | 232 통과 / 4 실패 | 데스크톱·모바일 합계 236개 |
| 실패 브라우저 경로 재검사 | 동일 4개 모두 재실패 | worker 1개로 분리해도 반복됨 |
| API 전체 pytest | 2,144 통과 / 9 실패 / 19 skip | 총 2,172개, 651.17초; 230개 경고 |
| 정리 도구 안전성 | 5개 통과 | 별도 임시 Git 저장소에서 삭제/보존/링크/WAL 검증 |
| 기존 UI 계약 검사 | 4개 통과 | URL 프로젝트 문맥, 파싱 상태, Tranche 표시 |
| Supabase migration wrapper 동기화 | 통과 | 로컬 SQL/생성본 일치; 원격 적용 상태는 미검증 |
| npm audit | 12개 경고 | Critical 2 / High 6 / Moderate 3 / Low 1; 중복 의존성 영향 포함 |
| 로컬 live / ready / login | HTTP 200 | `development`; 업무 SQLite, 회원 PostgreSQL, 파일 local |
| Git diff whitespace 검사 | 통과 | 기존 roadmap의 trailing whitespace 정리 후 확인 |

`/health/ready`의 `connectivityChecked`는 Object Storage와 malware scanner 모두
`false`였다. HTTP 200을 S3·악성파일 검사·실운영 준비 완료로 해석하면 안 된다.

검사 로그는 `.runtime/audit-20260914-*`에 남겼다. Git에 대량 로그를 올리지 않는다.

API 실패 9건의 원인은 다음과 같다. **9건을 모두 운영 기능 장애라고 세지 않는다.**

| 실패 묶음 | 수 | 확인한 원인과 수정 방향 |
|---|---:|---|
| `test_legal_intelligence_postgres_readiness.py` | 1 | 가짜 cursor가 새 컬럼 권한 조회의 인자 4개를 테이블 권한 인자 3개로 해석함. 컬럼 권한 계약을 대역에 구현해야 함 |
| `test_postgres_migration_runner.py` | 5 | 같은 SQL 대역 불일치. 실제 권한 검사를 약화하지 말고 대역과 거부/허용 기대값을 최신화해야 함 |
| `test_legal_provider_replay.py` | 3 | `NOW=2026-07-20` + 30일의 보존기간이 현재 이미 지났는데 세 시험이 repository clock을 주입하지 않음. 의도한 object binding 검사 전에 만료 오류가 발생함 |

세 replay 시험에는 기존 테스트 clock을 주입하고, 별도의 만료 거부 시험은 유지하는
것이 맞다. 테스트를 삭제하거나 예외 메시지를 아무거나 허용하는 방식으로 바꾸지 않는다.
이번에는 원인과 수정 범위를 진단했으며 해당 9개 시험을 수정하지 않았다.

## 우선 결함

### A01 · P0 · 계산 결과의 PostgreSQL 저장 경로가 미완성

`storage.py:2823`의 `upsert_calculation_run_record`는 JSON에는 tenantId를 넣지만
PostgreSQL에 전달하는 컬럼 사전에는 `tenant_id`가 없다(`storage.py:2884` 부근).
`db/postgres.py`의 기존 typed table manifest에도 해당 컬럼이 없다.
반면 `0012_core_tenant_authority_expand.sql`은 계산/Deal State 테이블의
tenant_id를 필수로 만들고 테넌트 권한을 적용한다.

AST로 해당 함수만 분리하고 DB 호출을 수집하는 대역으로 실행했을 때도
`tenantColumnPresent=false`였다. 실제 DB에는 접속하지 않은 코드 재현이다.
`PostgresCalculationRunRepository.upsert_run_with_outbox`와
`PostgresDealStateRepository`는 미커밋 파일에 존재하지만 현재 저장 호출 경로에
연결되지 않았다. **새 메서드가 존재하는 것과 기능이 완성된 것은 다르다.**

조치: 검증된 요청/Worker tenant를 전달하고, CalculationRun·Outbox를 같은
트랜잭션으로 저장하며 Deal State materialization까지 연결한다. 전용 runtime
역할로 신규 생성, 충돌, 재시도, 다른 tenant 거부, rollback을 시험해야 한다.
`legacy` tenant 자동 기본값이나 관리자 DSN으로 실패를 우회하지 않는다.

### A02 · P0 · 조회와 계산/업로드의 프로젝트 저장소 불일치

`routers/projects.py:309`는 PostgreSQL일 때 검증된 tenant를 요구하지만,
`upload_business_plan:2051`, `calculate_feasibility:2760`,
`run_project_cash_flow:3416` 등은 `PROJECTS_DB`부터 확인한다.
구문 분석상 직접 `PROJECTS_DB`를 참조하는 라우트 81개, 인자 없는
`project_repository()`를 호출하는 라우트 13개가 있다. 숫자는 정적 전환 대상
수이지 81개 모두에서 운영 장애를 재현했다는 뜻은 아니다.

영향: 정규화 DB에서 보이는 신규 프로젝트가 업로드/계산에서는 404가 되거나,
tenant 없는 저장 호출에서 실패하거나, 이전 입력으로 계산될 수 있다.
조치: request/job 단위의 프로젝트 repository를 전 경로에 전달한다.
완료 기준은 **메모리 사전에 없는 PostgreSQL 프로젝트**의 업로드→계산→조회 E2E다.

후속 검토 정정: 기존 미들웨어에는 일부 PostgreSQL 프로젝트를 전역 사전에
보충하는 경로도 있었다. 따라서 위 정적 참조 수를 실제 404 발생 수로 해석하면
안 된다. 보충 경로 자체도 요청 간 상태 공유를 남겼다. 후속 수정에서는 이를
요청별 작업 사본으로 교체하고 메모리 사전이 빈 HTTP 연결 시험을 통과했다.

### A03 · P0 · 로컬 751의 숫자와 근거 불일치가 현재도 남음

읽기 전용 조회로 다음 저장값을 다시 확인했다.

| 필드 | 현재 로컬 저장값 |
|---|---:|
| `feasibility_input.total_revenue` | 1,000 |
| `feasibility_input.total_cost` | 600 |
| `feasibility_input.development_profit` | 74,601,514,158.00154 |
| `feasibility_input.pf_amount` | 350,000,000,000 |

동일 입력의 매출−비용과 개발이익이 일치하지 않는다. 최근 Cash Flow 기록은
2026-07-13 저장본이며 `inputEvidenceIds`가 비어 있고 sourceLocator에는
engineKey만 있다. source_documents는 토지조서·사업수지 각 1개다.
운영 PostgreSQL 상태나 덮어쓴 실행 주체는 이 조회로 알 수 없다.

조치: 원본 hash/버전 → 단위 확인 → 승인 입력 후보 검토 → 새 CalculationRun.
기존 기록은 감사 이력으로 보존하고, 확인 없이 숫자나 승인 상태를 덮어쓰지 않는다.

### A04 · P0 · 재분석이 차단 상태를 완료로 보고할 수 있음

`use_cases/reanalyze_on_evidence.py`는 `run_pod()`가 예외 없이 반환하면
반환 내용/저장 상태와 무관하게 `after="completed"`를 기록한다. 재계산 라운드도
같은 방식이다. 마지막 `cascadeComplete`는 이 잘못된 rows를 사용한다.

대역 runner가 `{"status":"blocked"}`를 반환하고 프로젝트 상태도 blocked인
격리 재현에서 `after=completed`, `cascadeComplete=true`를 얻었다.
이것만으로 최종 PF 승인 우회를 입증한 것은 아니지만 재분석 완료 신호는 부정확하다.

조치: 실행 완료·자료부족·실패·검토 대기를 실제 결과와 현재 revision으로
정규화한다. 차단 반환, failed 반환, 하위 Pod 무효화, round 제한 초과를 시험한다.

### A05 · P0 · Cash Flow와 하위 시나리오가 같은 입력을 보장하지 않음

`routers/projects.py:3416`의 직접 Cash Flow 실행은 `request_data`로 계산한 후,
하위 시나리오는 기존 `project`와 sourceMetadata/parent ID로 다시 구성한다.
요청의 변경 입력이 동일하게 전달되지 않을 수 있다. 하위 실패도 빈 실행 ID로
흡수된다(`3431` 이후).

조치: 승인 input snapshot과 digest를 한 번 확정하고 CF/Waterfall/회수율/할인
시나리오가 공유하게 한다. 부모 ID와 입력 hash를 함께 확인하고 하위 실패를
명시적으로 반환한다. 수치 변경 자체는 도메인 기준에 따라 검토한다.

### A06 · P1 · 로그인 UI의 반복되는 실패

전체 Playwright에서 다음 두 시나리오가 데스크톱·모바일 각각 실패했다.

- 비밀번호 찾기: 클릭 후 재설정 메일 버튼을 찾지 못하고 login 화면으로 돌아간다.
  폼 local mode와 인증 상태/페이지 navigation 경합을 추가 조사해야 한다.
- 계정 전환: credential 폐기→세션 종료→login 이동 검증은 지나가지만,
  전환 안내문이 없다. 안내는 구형 OAuthLoginPanel에 있고 현재 LoginPage는
  AccountAccessPanel을 렌더링하므로 UI와 시험 계약이 어긋나 있다.

단순히 실패 테스트를 삭제하거나 기대값을 약하게 바꾸지 않는다. 비밀번호 찾기는
흐름을 복구하고, 계정 전환은 필요한 안내를 새 패널에 옮겨 계약을 일치시킨다.

### A07 · P0 · 의존성 보안 기준이 오래됨

현재 lockfile 검사에서 Critical은 `next`, `maplibre-gl`이다. 프로젝트의 Next는
16.2.10, MapLibre는 5.24.0이다. Next의 Windows 서버 취약점은 공식 공지에서
16.3.3 이상 패치를 요구한다. MapLibre는 지도 popup에서 `.setHTML()`를 쓰고,
주소/지목 문자열을 HTML에 직접 삽입한다(`map-intelligence.tsx:313`).

확인 자료:
[Next.js 보안 공지](https://github.com/vercel/next.js/security/advisories/GHSA-p293-qw3h-jr36),
[MapLibre 보안 공지](https://github.com/maplibre/maplibre-gl-js/security/advisories/GHSA-jrc7-96c5-q579).

패키지 경고는 모든 배포에서 악용 가능하다는 판정이 아니다. 다만 외부 노출 전
업데이트와 실제 사용 경로 검증이 필요하다. 지도 textContent 기반 DOM 구성,
버전/lockfile 업데이트, 인증·지도·Excel 회귀를 한 묶음으로 처리한다.
`npm audit fix --force`는 ExcelJS 다운그레이드와 major 변경을 제안하므로 일괄
실행하지 않았다. 기존 backlog의 “moderate만 잔존” 문구는 현황 근거로 쓰지 않는다.

### A08 · P1 · 기존 인허가 근거가 Legal Pod로 전달되지 않는 경로

`legal_pod.py:664`는 `options.parsedDocuments`에서 인허가 근거를 가져온다.
Chief의 land_legal executor는 전달받은 options를 그대로 넘긴다
(`chief_deal_orchestration.py:1331`). 저장된 문서/검토 결과를 조회하여 options로
결합하는 계약을 완성해야 한다. 사용자에게 같은 문서를 계속 재요청할 수 있다.

조치: 문서 없음, 원본만 있음, 파싱 실패, 후보 있음, 검토 대기, 승인됨을 분리하고
검증된 기존 근거를 재사용한다. 파일이 존재한다는 이유로 승인 근거로 승격하지 않는다.

### A09 · P1 · 테스트와 완료 문서의 의미를 정리해야 함

- API 대부분은 SQLite/대역 기반이며 실 PostgreSQL/역할/Worker를 대체하지 못한다.
- CI의 PostgreSQL job은 세 파일만 실행한다. 별도 live Parser V2 등은 현재
  job의 명시 실행 목록에 없다. 시험 DSN 없이 skip된 결과를 성공으로 세면 안 된다.
- 새 `scripts/tests/remediation-contracts.test.mjs` 및 정리 안전성 시험은
  기본 CI에 연결되지 않았다.
- `routers/projects.py` 약 6,926줄, `storage.py` 약 5,960줄이다.
  핵심 경로 분리 없이 기능을 계속 얹으면 변경 영향 추적이 어렵다.
- `apps/docs`는 아직 Turborepo 시작 템플릿이다. `api/export-cf`의 고정 샘플
  생성기는 프로젝트 계산과 별도의 학습 경로로 명시하고 사용처 정리 후 제거 판단한다.
  migration 생성본, test fixture, 샘플 원본은 단순 중복으로 삭제하지 않는다.

## 이번 정리 결과

- 기존 도구가 놓친 `services/api-server/.runtime/`의 과거 pytest basetemp
  폴더 9개를 정확한 허용 목록에 추가하고 삭제했다.
- **시험 산출물 154개 / 65.98 MiB 추가 삭제**. 이전 작업 문서의 2,561개/1,010.02 MiB는
  과거 기록이며 이번 실적으로 합산하지 않는다.
- 웹 소스에서 사용 참조가 없고 루트 화면은 `/start`로 redirect하는 것을 확인한
  뒤 초기 템플릿 잔재 8개(17,907 bytes)를 제거했다: `apps/web/app/page.module.css`,
  `apps/web/public/`의 `file-text.svg`, `globe.svg`, `next.svg`, `turborepo-dark.svg`,
  `turborepo-light.svg`, `vercel.svg`, `window.svg`. `apps/docs`의 독립 사본과 실제
  브랜드 이미지는 보존했다. **이번 총 삭제 파일 수는 162개**다.
- 최근 파일·Git 추적 파일·외부 링크·SQLite WAL/SHM/journal이 있는 대상은 보존한다.
- 업무 소스/테스트 소스/fixture/원본/운영 DB/실행 환경/미커밋 기능 수정은 보존했다.
- 복구용 `baselines.deleted-full-reset`, `uploads.deleted-full-reset`,
  `realytics.sqlite3.bak-full-reset` 합계 약 5,297.67 MiB는 원본/복구 근거가
  섞일 수 있어 보존했다. 보존 기간과 복원 검증을 확인한 뒤 별도 처리한다.
- 상위 `.pnpm-store` 223.48 MiB 삭제는 자동 승인 검토에서 `blocked by policy`로
  거부되어 실행하지 않았다. 세부 사유는 도구가 제공하지 않았다.
- roadmap의 불필요한 trailing whitespace를 정리했다. Git commit/push는 하지 않았다.

## 추가 작업 순서와 완료 기준

| 순서 | 작업 | 완료 기준 |
|---|---|---|
| 1 | 현재 실패 시험 원인 확정, 작업 중 변경 인수 | 원인별 재현, 기존 변경의 작성/검증 범위 분리 |
| 2 | A01/A02 저장소 연결 완성 | normalized-only 프로젝트의 업로드→계산→Outbox→조회; 실제 제한 역할과 tenant 거부 |
| 3 | A03 원본 기반 입력 복구 | 승인된 단위/총계/항목합계가 일치하는 새 입력 버전과 실행 |
| 4 | A04/A05/A08 재분석·근거·시나리오 통합 | 같은 입력 hash, 하위 실패 노출, 기존 인허가 근거 재사용, stale Gate 거부 |
| 5 | A06 로그인 UI 및 A07 보안 업데이트 | 실패 4건 해소, 타입/린트/build, 지도/인증/Excel 회귀 |
| 6 | 운영 검증 및 문서 정정 | 별도 test DB, 로그인·MFA 다른 승인자, S3 hash/권한, Worker 재시도/복원, 최신 증거에 따른 상태 표시 |

1–5는 상호 독립성이 확인된 범위에서 조정 가능하지만 외부 공개 전 보안 패치를
마쳐야 한다. 완료 기준을 충족하기 전 “운영 가능” 또는 “전체 정상”으로 표시하지 않는다.
이번 요청에서는 근거를 갖춘 정리와 진단을 수행했으며 이 표의 모든 기능 수정을
이미 구현했다고 보고하지 않는다.
