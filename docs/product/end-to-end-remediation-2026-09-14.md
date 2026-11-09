# REALYTICS End-to-End 진단 및 구현 계획

Updated: 2026-09-14

> 초기 진단·수락 기준을 보존한 문서다. 최신 상태는
> [실행 기록](remediation-execution-2026-09-14.md)을 기준으로 한다.

## 목적과 상태 관리

2026-09-14 사용자에게 보고한 상세 진단 전체의 결함·영향·수정 방향·검증 기준을
보존한다. 새 기능보다 기존 자료 → 파싱 → 승인 입력 → Pod → 계산 → AI 검토 →
Gate → 보고서 연결을 우선한다. 아래 항목은 코드 수정, 자동시험, 실제 운영 검증을
각각 구분한다. 코드가 존재하거나 단위시험을 통과했다고 운영 완료로 표시하지 않는다.

운영 DB/원본/751의 승인 상태는 자동으로 변경하지 않는다. 기존 계산을 삭제하거나
임의의 숫자로 덮지 않는다. 실제 로그인, cloud migration, 역할/credential 발급,
승인 정책 변경, 유료 서비스 활성화는 별도 확인한다.

## 진단 방법과 한계

- 현재 working tree, API/웹 경로, 저장소, 파서, Pod, Gate, export를 정적으로 추적.
- 로컬 3000 웹과 8000 API는 연결 거부. 종료 원인은 미확정.
- 웹 TypeScript (`--noEmit --incremental false`) 및 ESLint 통과.
- SQLite 751은 `mode=ro`로만 조회. 배포 PostgreSQL의 현재 상태로 해석하지 않는다.
- 흡수율 메서드를 앱/DB와 분리하여 직접 실행해 결함 재현.
- 운영 데이터 오염 우려로 전체 API pytest는 진단 단계에서 실행하지 않음.
- 실제 로그인, S3, 전용 PostgreSQL 역할, 전체 업로드/승인 E2E는 미검증.
- 폴더 정리 때 삭제한 것은 생성물뿐이며 DB 안의 모순된 업무 기록은 보존했다.

## 확인된 로컬 751 기록 (7월 13일 저장본)

| 항목 | 값/상태 |
|---|---|
| 현재 feasibility_input.total_revenue / total_cost | 1,000 / 600 |
| 같은 입력 객체의 development_profit | 74,601,514,158.00154 KRW |
| 같은 입력 객체의 pf_amount | 350,000,000,000 KRW |
| 최초 CF 매출 / 총사업비 | 853,951,767,703.25 / 779,350,253,545.2485 KRW |
| 이후 5개 CF 매출 / 총사업비 | 1,000 / 600 |
| 최신 CF maxMinus | 0 |
| 최신 CF inputEvidenceIds | 빈 목록 |
| 최신 CF sourceLocator | engineKey만 존재; 원본 위치 없음 |
| 최신 CF createdAt | 2026-07-13T10:16:46.827862+00:00 |
| Feasibility AI review | blocking, 최신 2026-07-13T09:47:09.676721+00:00 |
| 재계산 이벤트 | pending_recalculation 10건 |
| source_documents | land_ledger, feasibility_workbook 각 1건, archiveStored=true |

매출-비용과 개발이익이 모순된다. 시험값 혼입 가능성은 있으나 어떤 실행이
덮어썼는지는 확인하지 않았다. 별도 사업계획서/인허가 문서는 현재 목록에서
확인되지 않는다. 문서 목록 교체 결함 때문에 과거 업로드 부재를 단정할 수 없다.
최신 시간순 계산을 승인자료로 사용하지 말고 원본과 승인 입력 버전을 먼저 검증한다.

## 결함별 상세 진단과 수정 계약

### R01 — 실행 환경/준비 상태 (P0)

진단: localhost:3000 및 :8000의 live/ready 모두 연결 거부. 로컬 웹 설정은
루프백 HTTP API를 가리킴. 웹만 켜도 API 없는 업무 흐름은 실패한다.

조치: 웹/API/Worker 독립 실행, 비밀값 없는 사전 점검, 포트 충돌 및 시작 실패
진단, liveness/readiness 구분. readiness는 S3/ClamAV 통신을 검사하지 않는다는
현재 한계를 유지해서 명시한다.

완료 기준: 로컬 live/ready와 웹 HTTP 응답, 실행/종료 로그, 기존 프로세스 보존.
운영 배포 완료는 별도다. 근거: `core/runtime_health.py`, `scripts/start-web-dev.ps1`.

### R02 — 시험 DB/파일 격리 (P0)

진단: `tests/conftest.py`가 외부 API 플래그는 제어하지만 앱 import 전에 모든
저장을 분리하지 않는다. `main.py`는 .env를 로드하고 `routers/projects.py`는
import 시 load_fixtures를 호출한다. 함수 내부 monkeypatch만으로 초기 접속을
막을 수 없다. 기본 storage backend는 SQLite다.

조치: collection 전에 임시 SQLite/JSON/object 저장소 확보, 개발 .env 로딩 차단,
운영 저장 경로 거부, 외부 연결 opt-in, subprocess 검증. 시험 DB를 사용하는
PostgreSQL 통합시험은 명시적 test DSN만 허용하고 운영 DSN을 상속하지 않는다.

완료 기준: 테스트/collection 후 운영 DB와 원본 무변경, 세션별 저장 디렉터리 분리,
CI/직접 pytest 실행 모두 안전. 운영 데이터 fixture로 대체하지 않는다.

### R03 — 751 데이터 정합성 복구 (P0, 사용자 입력 필요)

진단: 위 표처럼 동일 프로젝트에 서로 모순되는 값과 근거 없는 최신 계산 존재.
조치: 원본 hash/버전 조사 → 승인 입력 후보 제시 → 사용자 검토 → 새 계산 버전.
기존 기록은 감사 이력으로 보존. superseded/excluded 상태 도입 시 사유/검토자 기록.
완료 기준: 매출-비용=이익, 항목합계=총계, 단위/기준일/승인입력/run 일치.
최신 생성 시각만으로 유효 계산을 선정하지 않는다.

### R04 — 문서 누적 및 값-출처 원자성 (P0)

진단: `parse_uploaded_business_plan`은 새 source_documents 목록을 만들고
`ProjectRepository.apply_parsed_data`의 deep_merge가 목록을 교체한다. A 후 B를
업로드하면 A가 검색/다운로드 메타데이터에서 빠질 수 있다. 기존 사전의 생략된
locator는 남아 새 값과 옛 근거가 섞일 수 있다.

조치: archive/document/version ID 기준 누적; 같은 문서 재업로드는 중복 억제;
다른 버전/상충 근거는 유지. 숫자, locator, 검토 상태는 함께 다룬다.
완료 기준: A→B 업로드 후 둘 다 조회/다운로드; A 재업로드 중복 없음; 충돌 보존.
근거: `routers/projects.py` parse_uploaded_business_plan,
`db/repositories/project_repository.py`, `use_cases/common.py`.

### R05 — 기존 문서 검색 후 missing 판정 (P0)

진단: Legal Pod는 `options.parsedDocuments`만 읽어 인허가 근거를 평가한다.
Chief의 입력 snapshot에 sourceDocuments가 있어도 실제 executor 옵션에 기존
파싱 결과를 공급하지 않으면 'No permit ... document was provided'가 나온다.

조치: 프로젝트 문서/파싱 후보/검토 결과를 조회하고 필요한 문서만 재파싱/OCR.
없음, 미파싱, 추출실패, 후보발견, 미승인, 승인됨, 입력연결실패를 분리한다.
완료 기준: 승인된 인허가 근거는 재사용; 미승인 근거는 자동 승인하지 않음;
원본을 조회한 후에도 없을 때만 보완자료 요청.
근거: `use_cases/legal_pod.py` _verified_permit_document_evidence 호출부,
`use_cases/chief_deal_orchestration.py` land_legal executor.

### R06 — 추가자료 재분석 dependency closure (P0)

진단: reanalyze targets가 시작할 때 고정됨. 시장자료 변경 후 market_product가
하위 Pod를 needs_recalculation으로 표시해도 실행하지 않고 종료 가능.
_completed_pod_keys는 현재 blocker와 무관하게 artifact 존재만으로 완료 취급.
수동 재분석의 빈 document type 요청은 과거 artifact가 모두 있으면 no-op 가능.

조치: 현재 입력 버전/Pod 상태 기준, 하위 의존성 순서, bounded cascade,
idempotency, 재시도. 완료/근거검토/승인 상태를 분리한다.
완료 기준: 시장 변경→사업수지/CF→PF→AI Review→Gate가 동일 revision으로 갱신.
근거: `use_cases/reanalyze_on_evidence.py`, `routers/projects.py` _completed_pod_keys.

### R07 — 파싱 검증 순서 통합 (P0)

진단: 일반 Pod API는 실행 후 parsed fact 검증/보강을 수행하나 추가자료 재분석은
bare use case를 호출해서 같은 API 검증을 거치지 않는다.
조치: 근거 검색→검증→승인입력 선택→계산→저장→AI Review→cascade→Gate를 공유.
완료 기준: 모든 업로드/수동/Worker 경로가 동일 검증 계약과 감사 이벤트 사용.

### R08 — Tranche API/화면/Excel 계약 (P0)

진단: 엔진은 claimAmount/recoveredAmount를 생성하지만 화면/Excel은
principalClaim/paymentReceived/shortfall을 읽고 누락을 0으로 표시한다.
claimAmount는 이자/수수료를 포함하므로 '원금'으로 단순 명칭 변경하면 안 된다.
조치: 총청구액/원금/회수액/부족액 구분, 공통 타입/adapter, 미산출은 —,
부족액은 결정론 엔진에서 산출. 완료 기준: API=화면=Excel, 0과 누락 분리.
근거: `agents/scenario_engine_agent.py`, 프로젝트 cashflow page/export route.

### R09 — Cash Flow/하위 시나리오 입력 일치 (P0)

진단: direct CF route는 부모 run ID는 연결하지만 시나리오를 기존 프로젝트에서
다시 만들므로 CF override와 다른 입력 가능. Pod 경로의 별도 계산은 export가
요구하는 부모 연결이 없을 수 있다. 하위 실패를 빈 ID 목록으로 숨기는 경로도 있음.
조치: 하나의 승인 input snapshot/digest/scenario에서 계산 묶음을 생성/저장.
완료 기준: 부모 ID뿐 아니라 입력 버전/금액/시나리오가 일치; 불완전 묶음 명시.

### R10 — Gate 최신성 및 보고서 적격 (P0)

진단: _metric_uses_latest_calculation_run은 엔진/최신 run을 찾지 못하면 True.
cashflow-export-package는 lineage를 검사한 뒤 reportEligible=True를 반환하나
실제 승인 Gate/예비분석 report-ineligible 상태 검증과는 다름. 전체 승인 우회가
입증된 것은 아니며 export 적격 표시의 과도한 의미가 문제다.
조치: 검증불가는 unknown/not_ready, cross-engine lineage 검사. 초안 export와
승인보고서 적격을 분리. 정책 확정 전 자동 승인/waive 금지.
완료 기준: 없는/stale/unapproved 근거와 불일치 입력은 승인 보고서로 표기 안 됨.

### R11 — 후보 적용 후 검토 상태 보존 (P0)

진단: builder는 proxy/derived에 needsReview를 표시하나 apply가 숫자 위주 저장.
다음 build에서 existing feasibility_input으로 읽으면 confidence=0.8,
needsReview=False가 될 수 있다.
조치: 값/evidence/locator/review state/승인자/시각 함께 저장; apply≠approve.
완료 기준: 반복 build/apply로 검토 미완료가 자동 승인 상태로 바뀌지 않음.

### R12 — 프로젝트 문맥 일관성 (P0)

진단: sidebar는 딜룸만 URL project ID를 보존; 전역 CF는 localStorage 복원.
751에서 이동해 이전 선택 프로젝트가 표시될 수 있음. 과거 748 혼동 원인은 미확정.
조치: URL 우선, 메뉴에 ID 전파, 응답 ID 검증, 요청 취소/순서 보호.
완료 기준: 프로젝트 전환 및 빠른 연속 선택에서도 다른 프로젝트 결과 미표시.

### R13 — 실프로젝트 Excel/샘플 분리 (P0)

진단: cashflow-detail의 /api/export-cf 링크는 대구 본동 고정 샘플을 다운로드.
조치: 선택 프로젝트 export 연결; 샘플은 명확한 학습용 별도 링크.
완료 기준: 다운로드 프로젝트/run이 화면과 일치. 프로젝트별 export의 completion
감사 callback은 이미 연결돼 있음(구형 backlog의 미연결 문구 정정 필요).

### R14 — Gate 및 재조회 오류 UI (P0)

진단: 초기 loadPlan은 plan/Gate를 갱신하지만 runPod/runAll은 plan만 갱신.
재분석 후 loadPlan의 catch 오류를 호출자가 setError(null)로 지울 수 있음.
조치: 같은 응답 revision으로 plan/Gate 갱신, 재조회 실패를 보존, stale 표시.
완료 기준: 차단 해소와 새 blocker 모두 즉시 표시; 조회 실패를 성공으로 위장 안 함.

### R15 — 업무 실패와 HTTP 성공 분리 (P0)

진단: Smart Intake는 parse_failed/host_llm_required 등 HTTP 200도 done 처리.
재시도는 done을 건너뜀. 서버도 모든 재분석 예외를 blocked로 변환하거나 숨김.
조치: uploaded/parse_failed/awaiting_review/extraction_unavailable/execution_failed
상태, 원본 재업로드 없는 단계별 재시도, 비밀값 없는 오류 코드.
완료 기준: 자료부족과 파서/API/DB 오류를 구분하고 실패를 처리완료로 표시 안 함.

### R16 — Market/Product/Covenant 및 흡수율 (P1)

이미 연결됨: ProductPlanning/MarketProductDepth는 market_product,
CovenantEod는 pf_credit에서 실행; Pod AI Reviewer가 세 산출물의 근거 검토.
시행사/매각주관사/공공데이터 분류 및 추가 작업 추천도 존재. 최신 승인 자료를
받는다는 운영 보장은 R04-R11 해결 후 검증한다.

재현된 결함: commercial 100호실, 월3% → estimatedMonthsToSellout=33,
33개월 curve=99%. round(1/rate)가 완판 전에 끊음.
조치: 올림 및 곡선 일치, finite/0/음수/>1 검증, 36개월 horizon 이후 미분양 표시.
계획 벤치마크를 검증된 실분양 예측으로 부르지 않는다. 시장 비교는 제공주체,
지역, 상품, 면적/가격 단위, 기준일을 맞추고 차이를 근거로 보여준다.

### R17 — PostgreSQL 요청/Worker authority 통합 (P0, 운영 검증 별도)

진단: 조회는 normalized repository, 계산/재분석 일부는 PROJECTS_DB.
context 없이 project_repository() 호출은 PG에서 오류. normalized-only 프로젝트가
조회 가능하지만 계산 불가하거나 오래된 입력으로 계산될 수 있음.
JobStore claim/update 일부는 역할별 pool 대신 postgres.connect/ensure_schema 사용.

조치: request/job tenant-scoped repository로 통합; owner DSN 우회 금지.
전용 api/identity/pf_worker 계정, FORCE RLS, claim/retry/lease를 실제 역할로 검증.
완료 기준: normalized-only project E2E, cross-tenant 거부, crash/reclaim/idempotency.
기존 미커밋 calculation_run_repository/deal_state_repository 작업은 보존/조정한다.

### R18 — 인증/S3/배포/복원 운영 Gate (P0, 사용자/외부환경 필요)

구현은 있음: OIDC, membership/RBAC, MFA, S3, retention, audit.
로컬은 PostgreSQL 업무 DSN/S3/external worker 설정 없이 SQLite/local 기본값.
배포 상태는 미검증. health/ready의 S3/ClamAV는 configuration 검사일 뿐.

필수 실검증: 로그인→tenant 가입→역할별 접근, 다른 승인자 AAL2,
S3 원본 hash/version/다운로드 권한, malware 격리, 백업 복원/원장 연결.
관리자 접속 성공을 실제 제한된 runtime 역할의 성공으로 간주하지 않는다.
CI/CD, 장애 알림, rate limit, secret rotation, retention 정책을 운영 인수에 포함.

## 실행 순서 및 진행 원장

| 단계 | 대상 | 상태 | 완료 증거 |
|---|---|---|---|
| 1 | R02 시험 격리 | 구현·검증 | import 이전 격리/원본 무변경 시험 |
| 2 | R01 실행 환경 | 구현·로컬 확인 | 시작 도구, 웹/API 응답 |
| 3 | R03 751 복구안 | 후보 준비·검토 필요 | 원본 해시/단위/항목합계 대조. 승인·반영 없음 |
| 4 | R04-R07,R15 자료/재분석 | 구현·집중 검증 | 원본 누적, 근거 재사용, 하위 상태/표준 검증/실패 표시 |
| 5 | R08,R12-R14 UI/계약 | 구현·브라우저 검증 | 프로젝트/금액/Gate/export 연결 |
| 6 | R09-R11 계산/Gate | 구현·집중 검증 | 입력 해시, 승인용 export 거부 조건, 후보 상태 |
| 7 | R16 흡수율 | 구현·검증 | 올림과 invalid 입력 차단 |
| 8 | R17-R18 운영 연결 | 코드 보완·실운영 미검증 | 전용 역할/Worker/S3/인증/복원 실검증 필요 |
| 9 | 실제 파일럿 E2E | 미실행 | 실제 승인자·제한 계정으로 아래 시나리오 검증 필요 |

## 최종 E2E 수락 시나리오

- 사업계획서 A 후 인허가 B 추가: A/B 원본과 locator 유지.
- 연면적/사업비 상충: 자동 덮어쓰기 대신 conflict/검토 요청.
- 분양가 변경: CF/PF/AI Review/Gate가 동일 승인 입력 revision 사용.
- 파싱 실패: missing evidence가 아니라 parsing failure와 재시도 안내.
- Worker 중단: lease 회수, 중복 결과 방지, 필요한 단계만 재시도.
- 751 화면 이동/프로젝트 빠른 전환: 다른 ID 결과 노출 없음.
- API/화면/Excel: 동일 run, 동일 금액, 미산출을 0으로 위장 안 함.
- 필지 PNU/법정동 불일치: 자동 확정 금지, 원본 기준 재검토.
- cure/추가 증빙→재계산→Gate: 근거/검토 부족은 계속 승인 불가.
- 최종 산출물은 reviewer/assumption/evidence/calculation/risk lineage 표시.

## 사용자 확인이 필요한 사항

1. 751 복구의 기준 원본/승인 입력: 원본 재검증 후 후보 제시를 기본 권고.
2. 초안 export와 승인보고서의 정확한 적격 정책: 코드 수정 후에도 production-ready
   선언 전 승인 필요.
3. 실제 staging/production 대상, 제한된 DSN/Secret Manager, S3 retention 및 비용.
4. 복구 백업 삭제는 별도 승인 전 보존.

## 구현/검증 기록

- 2026-09-14: 상세 진단과 18개 remediation 항목을 본 문서로 통합. 구현 시작.
