# 재진단 후 수정 실행 기록

2026-09-14 사용자 지시: “현재까지 파악된 작업 모두 진행하자.”
대상은 초기 감사 A01–A09와 관련 R01–R18이다. 기존 미커밋 변경을 보존했다.
로컬 구현·시험과 실제 운영 적용을 구분한다.

| 범위 | 처리 | 검증 및 남은 조건 |
|---|---|---|
| 기존 실패 API 시험 9건 | 수정 | 권한 대역의 컬럼/시퀀스 계약과 고정 시계 보완, 관련 53개 통과 |
| A01 계산 저장 | 구현 | 계산·Outbox·Deal State 동일 tenant/transaction, 단계별 rollback, Worker 연결 |
| A02 프로젝트 저장 경계 | 구현 | 요청별 작업 사본, 읽은 버전으로 CAS. 135개 함수 내부 참조와 Parser V2 조회 연결. 메모리 사전에 없는 프로젝트의 HTTP 업로드→계산→재조회 통과. DB 접속부는 대역 |
| A03 751 복구 | 검토 후보 준비 | 원본 해시·단위·총계·항목 합계 대조. 잘못된 열 합산 및 근거 없는 Max Minus/자기자본/LTV 제거. 원본 버전·부가세·월별 거래 매핑과 사람 승인 필요. DB 변경 없음 |
| A04 재분석 | 구현 | blocked/failed/검토 대기/새 실행 없음/하위 무효화/round 제한 반영. 표준 parsed fact 검증 연결 |
| A05 입력 계보 | 구현 | CF 요청 기반 하위 시나리오, 부모 ID/financialInputHash 공유, 스트레스 일정 유지, 하위 실패 표시. 승인용 export는 해시 없는 과거 실행과 혼합 입력 거부 |
| A06 로그인 | 구현 | hydration 전 입력 방지 및 계정 전환 안내. 전체 브라우저 검사 통과 |
| A07 의존성·지도 | 구현 | Next 16.3.5 / MapLibre 6.9.0 / PostCSS 8.5.28 / UUID 11.1.1. npm audit 0건. popup textContent 및 Excel roundtrip 검증 |
| A08 인허가 재사용 | 구현 | 저장된 원본/문서 버전/검토/승인 입력/해시/정확한 위치 검증. 후보·검토 대기·파싱 실패·조회 불가 구분. 다른 tenant와 구버전 승인 거부 |
| A09 CI·환경·문서 | 구현, 운영 조건 별도 | 계약/정리 검사 CI 연결, PG 검사 skip 거부. 필수 PyJWT/cryptography/boto3 설치. 원격 runtime 권한 부족 확인. 실제 계정/S3/복원 검증 미실행 |

## 검증 원장

| 검사 | 결과 |
|---|---|
| API 전체 중간 검증 | 2,178 통과 / 19 skip, 625.98초 |
| API 전체, 마지막 수정 포함 | **2,193 통과 / 0 실패 / 18 skip**, 438.40초. .runtime/remediation-20260914-api-verified.log |
| 인증 서명·Worker·권한 집중 검사 | 41 통과. ES256 실제 서명 생성/검증 포함 |
| 요청 범위·파서·재분석·Worker 집중 검사 | 21 통과. 마지막 Worker 시작/종료 및 업로드 재분석 실패 표시 묶음 20개도 통과. Worker 저장소 연결/진입점 검사 11개 통과 |
| 브라우저 전체 | 238 통과, 데스크톱/모바일. 격리 포트의 인증 기준 주소 설정 보완 |
| 웹 타입 / ESLint / production build | 통과 |
| Node 계약 / 정리 안전성 | 각각 5개 통과 |
| Supabase migration 생성본 동기화 | `render_supabase_migrations.py --check` 통과. 원격 적용 검증은 아님 |
| npm audit / pip check | 0건 / No broken requirements found |
| 실제 PostgreSQL 통합 | 시험 DSN 부재로 18개 skip. 성공으로 세지 않음 |
| 751 대조 | SHA-256·크기 일치. 매출/비용 항목합계 오차 0, 매출−비용−이익 오차 약 −0.000076원 |

중간 전체 검사의 추가 1개 skip은 필수 PyJWT/cryptography 미설치였다.
requirements 설치 후 해당 보안 시험을 필수 import로 바꿨다. 설치 직전 실행 중인
검사는 중단하고 새 환경으로 재실행했다. 중단된 로그는 통과 증거로 사용하지 않는다.
마지막 업로드 재분석 실패 표시 변경은 API/Node/타입/린트 검사로 확인했다.
Worker 실행 파일의 전용 풀 시작/종료도 집중 검사와 최종 전체 검사에 포함했다.
의존성 보완 후 첫 전체 검사는 2,186 통과 / 1 실패 / 18 skip이었다. 실패는
Worker 테스트가 이동 전 import 위치를 참조한 것이었다. 로컬 경로와 PostgreSQL
경로를 분리해 시험하고, 보고서·스냅샷까지 동일 tenant와 Worker 풀로 연결되는지
확인했다. 이 수정과 마지막 추가 시험을 포함해 전체 2,193개가 통과했다.
경고 231개는 남아 있으며 주로 httpx/Starlette의 deprecated API 안내다.

검증 원본은 로컬 `.runtime`에 보관한다:
[최종 API 로그](../../.runtime/remediation-20260914-api-verified.log),
[브라우저 전체 로그](../../.runtime/remediation-20260914-e2e-final2.log),
[npm 보안 검사](../../.runtime/remediation-20260914-audit-final.json),
[751 복구 후보](../../.runtime/recovery-751-20260914/candidate.json).

## R01–R18 연결

R01–R02는 실행 도구와 import 전 시험 격리, R04–R07은 원본 누적·값/출처 동시
교체·인허가 검색·하위 재분석·표준 검증, R08–R11은 Tranche·입력 해시·승인용
export·후보 검토 상태, R12–R14는 URL 프로젝트 문맥·프로젝트 Excel·Gate 오류
보존, R15는 파싱/검증/후속 재분석 미완료 표시, R16은 흡수율 올림과 범위 검증이다.
R17은 tenant 지정 Worker claim, lease owner/attempt 검사, Agent/Outbox 원자 갱신,
JSON 중복 저장 제거를 반영했다. 실제 제한 역할 검증은 남아 있다.

R03은 [751 검토서](project-751-source-recovery-review-2026-09-14.md),
R18 및 실제 운영 인수는 [운영 적용 전 조건](runtime-activation-gates-2026-09-14.md)을 따른다.

## 정리와 보존

앞선 감사에서 생성물 162개를 정리했다. 업무 원본·DB·백업·회귀 테스트·사용자
기존 수정은 보존했다. Git commit/push, 원격 migration, 메일 발송, 실제 승인,
배포는 하지 않았다. 기존 감사 문서는 수정 전 기록으로 명시하고 최신 실행 기록을 연결한다.

자동 승인 검토가 상위 .pnpm-store와 진단용 임시 스크립트 6개 삭제를
“blocked by policy”로 차단했다. 우회하지 않았으며 해당 파일은 남아 있다.
751 승인 입력 적용과 클라우드 운영 인수는 아직 완료되지 않았다.
