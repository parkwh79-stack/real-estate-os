# Chief Pod Runtime Integrity

Updated: 2026-11-06

Status: Controlled Implementation

## Purpose

This contract turns the Chief Deal Room task graph into an enforceable runtime.
It prevents a downstream Pod from presenting stale or fabricated deal-review
results when an upstream Pod has not completed.

## 운영 AI 딜룸 진입

2026-11-04 운영 로그에서 딜룸 계획과 지도가 동일한 구형 `app_state` 조회에서
`InsufficientPrivilege`로 실패했다. 딜룸의 계획은 프로젝트 범위의 승인 가정을
기존 정규화 저장소에서 읽는다. 지도는 계획이나 승인 가정을 읽지 않는다.
이를 위해 운영 DB 역할이나 RLS 정책을 넓히지 않는다.

딜룸은 진입 시 계획과 저장된 결과를 불러온다. 6개 Chief Pod의 업무를 시작하려면
**전체 자동 실행** 또는 해당 Pod 실행을 선택한다. 계획 조회와 실행 실패를
구분하여, 실행을 시작할 수 없어도 계획과 보완할 자료는 화면에 남는다.
운영 실행은 프로젝트와 실행 이벤트를 정규화 저장소에 기록한다. 자료가 없는
프로젝트에서도 부족한 자료와 차단 상태를 반환한다. PostgreSQL 요청 검증은
계획과 자료 부족 상태를, 별도 합성 프로젝트 검증은 실행·저장·재열기를 확인했다.

2026-11-05 운영 딜룸은 프로젝트 서버 기록으로 계획을 구성한다. 개별 Pod,
전체 실행, 재분석에서 인증된 사용자와 프로젝트 범위를 사용하고 실행 이력을
같은 PostgreSQL 권위에 기록한다. 주소 보강은 프로젝트의 서버 보관 원본만 읽는다.
대시보드는 미실행을 정상 상태로 표시하고, 자료 누락과 실패는 확인 대상으로 표시한다.

승인 준비는 별도 서버 조회에서 평가 전, 조회 불가, 평가 결과를 구분한다.
법률 목록은 프로젝트 범위의 정규화 기록을 조회한다. 법률의견, 면제, 승인 등
권위 있는 기록 저장은 전용 법률 검토 계정이 필요하다.
[전용 계정 설정과 검증 절차](../engineering/legal-corpus-operations.md)

운영 DB 권한 대조에서 Agent 실행, 단계, 인계, 계산, 결과 이력 저장을 확인했다.
독립 Agent 도구 실행은 서버에서 확인한 역할로 요청을 구성하며, 도구 감사 저장소의
권한이 없는 환경은 서비스 사용 불가를 반환한다. 법률 검토 전용 계정과 외부 PF Worker
활성화는 별도 운영 변경으로 검토한다.

증빙 보완 기록은 운영 역할에 조회와 저장 권한이 없다. 서버가 보완 저장 불가 상태를
반환하면 딜룸에서 **증빙 저장·재계산**을 비활성화한다. 보완 기록 API는 이 상태에서
503을 반환하고, 일반 계획·분석은 프로젝트에 저장된 입력을 사용한다.

## 대시보드·워크스페이스 초기 응답

Cloud Run의 로그인 확인 요청은 자동 확장으로 인스턴스가 시작된 경우 약 59.8초,
같은 리비전에서 인스턴스가 준비된 경우 약 0.063초였다. 시작 로그에서는
ClamAV 준비까지 약 44초, 이후 API 준비까지 약 15초가 걸렸다.
Cloud Run 설정에서 API와 ClamAV를 병렬로 시작한다. API 준비 상태는
정규화 DB 연결과 검사기 구성 유효성을 확인한다. 배포 검증 리비전은
인스턴스 시작 후 API 10.3초, ClamAV 33.2초에 각각 준비되었다.
이는 배포 시점의 컨테이너 준비 시간이다. 업로드 격리는 실제 검사에
성공한 파일만 해제한다. 파일 검사기가 연결되지 않으면 업로드 API가 503을 반환한다.
Vercel 서버 함수의 단일 리전은 API와 Supabase가 있는 도쿄로 설정한다.

대시보드는 최근 작업과 전체 목록을 각각 도착하는 대로 표시한다.
프로젝트 헤더와 본문의 동일한 컨텍스트 요청은 인증 범위 안에서 진행 중일 때만 공유한다.
새로고침과 인증 변경 후에는 새 요청을 한다. 서버의 프로젝트 권한 검사는 요청마다 수행한다.
딜룸은 계획을 먼저 표시하고 승인 준비·실행 이력·법률 기록은 별도로 조회한다.
승인 관련 입력과 제출은 해당 권위를 확인한 경우에 활성화한다.
첫 접속과 같은 계정에서 재접속한 시간을 별도로 측정한다.

## 사용자 AI 딜룸과 운영 진단

사용자 딜룸은 현재 상태, 다음 할 일, 검토 현황 순으로 표시한다. 자료 요청은
실행을 차단하는 항목부터 관련 화면별로 모으고, 보완 대상과 예시 자료는 펼쳐서 확인한다.
각 검토 카드는 완료·대기·보완·실패·재검토를 구분한다. 완료 결과가 0건이면
전체 실행 요청이 정상 응답해도 분석이 완료되었다고 표시하지 않는다.
사전 검토 전에는 가능한 자료 확인을 먼저 안내한다. 검토가 정리되면 남은 확인·제출
항목을 안내한다. 토지 미리보기는 해당 화면의 권한과 입력 조건을 따른다.

패럴랙스는 소개 배경에만 적용하고, 유리 효과는 검토 요약에 적용한다.
수치를 읽는 본문과 조작 요소는 고정된 불투명 표면에 표시한다. 모바일과
동작 감소 설정에서 배경 스크롤 효과를 중지한다. 검토 상세는 키보드로도 닫을 수
있는 오른쪽 서랍에서 읽는다.

실행 이벤트, 내부 Pod·Agent 명칭, 작업 그래프와 원본 오류 코드는
`/project/{id}/chief-orchestration/diagnostics`에서 확인한다. 플랫폼 운영자
여부와 기존 프로젝트 읽기 권한을 확인한 뒤 진단 화면을 열고, 각 API에서도
프로젝트 범위를 검증한다.


## 정식 PF 합의의 독립 재계산 (2026-11-06)

사전 검토의 입력별 재사용 정책과 정식 합의의 독립 재계산은 별도 경로다.
`OrchestrationAgent.run_pf_review`는 `consensus_review_cycle.recompute_consensus_cycle`로
초기 평가를 한 번 수행하고, 실제 입력 패치가 적용된 매 회차에서 구현된 도메인
Agent/엔진을 다시 실행한다. 이전 토지·시장·사업성 결과를 callback에 캡처하지 않는다.
원본과 프로젝트를 직접 바꾸지 않고 각 Agent가 별도의 입력 사본을 받는다.
같은 회차에서 나온 개발·시장·법률·PF 결과를 데이터 품질·리스크·신용 Agent에 전달한다.

기존 27개 단계는 초기 입력의 결과를 한 번 게시한다. 후보 회차마다 보고서 렌더링,
PDF QA, 신용문서 발행을 반복하지 않는다. `consensusResolution.candidateReview`는
`analysisOnly: true`, `reportEligible: false`인 미승인 검토안이다. 원래 승인된
canonical 입력과 원래 입력의 계산·보고서는 유지하고, 수치 변경은 기존 가정 제안·
승인 절차로 보낸다. 합의 수치가 맞아도 근거 누락이나 데이터 품질 문제는 남는다.

실행 범위는 서버가 만든 run/evaluation 식별자로 구분한다. 기존 durable Agent
런타임에 이를 포함하여 같은 사업 입력을 다시 평가할 때도 이전 성공을 재생하지 않는다.
회차별 원본 입력 해시, 산출물 해시, Agent 실행 해시·종류, typed 인계와 6개 Pod의
지원 범위를 기록한다. 공공·법률 근거는 시작 시 한 번 확인해 동결하며 회차마다
새로 외부 조회하지 않는다. 새로운 유료 서비스나 LLM 수치 계산은 추가하지 않는다.

매 평가에는 토지 준비·법률, 개발 사업성·핵심 사업수지·현금흐름·수익률,
시장 근거·시장성·시장 심화·상품계획, PF 구조·회수 시나리오·스트레스·신용보강·
조건부 cash sweep·covenant/EOD, 개발가능성, 데이터 품질·위험·신용·구조조정·
사업 개선 검토가 포함된다. 물리적으로 같은 개발 엔진에 속한 현금흐름은 nested
실행, 원가·일정 및 공공성의 기존 결과 투영은 projection으로 구분한다.
별도 구현이 없는 VE·시공사 전문 검토와 고급 공공정책 시뮬레이션을 독립 실행했다고
표시하지 않는다. 필수 입력이 없는 엔진은 미실행 사유를 남긴다.

초기 평가 뒤 실제 변경을 최대 세 회 적용하며, 무변경·진전 없음·반복 상태에서
종료한다. 어느 회차든 실패한 계산이나 잘못된 reconciliation은 이전 성공값으로
대체하지 않는다. 정식 실행을 실패로 남기고 보고서를 완료 처리하지 않는다.

승인된 canonical 현금흐름이 작업용 Developer 현금흐름과 함께 있는 경우,
RiskRegister는 보고서와 동일한 최상위 `cashFlowRunResult`를 선택한다. 최상위 결과가
없는 경우에만 기존 중첩 계산을 사용한다. 근거 위치와 자료 제한 판단도 같은 계산을
따른다. 원래 Developer의 독립 결과는 출처 확인용으로 유지하고 승인값으로 덮지 않는다.
작업용 계산과 승인 원본 계산의 입력이 다르면 두 계산의 출처를 각각 기록하며, 같은
계산을 두 번 실행한 것으로 취급하거나 서로 같은 결과로 격상하지 않는다.
`cashFlowBasisReview`는 승인 계산의 매출·총사업비와 작업 입력 및 중첩 계산을
대조한다. 다르면 `cash_flow_input_basis_matching` 차단 검사로 남아 합의 완료와
승인을 막는다. 자료가 부족하면 확인 필요로 남긴다. 가정이나 승인 원본을 자동 정렬하지 않는다.
보고서는 사업성과 현금흐름 본문에 한국어 기준 확인 안내를 표시하고, 상세 원본 경로와
대조값은 JSON 품질 메타데이터에 둔다. 입력 기준 불일치는 보고서 품질 통과로 표시하지 않는다.
관련 후속 회귀 96개와 확장 통합 5개(4개 통과 뒤 변경된 기준 검토 1개 재검증)도 통과했다.

검증: 관련 API 회귀 182개(합의 엔진·기존 정식 실행 59개, 승인 입력·보고서·실패
처리 등 104개, 상품계획·Covenant/EOD·근거 그래프 19개)가 통과했다. 변경 후 확장한
독립 Agent 통합 5개도 통과했다(위 59개에 포함되는 같은 테스트 파일).
이 검증은 격리된 합성 자료와 기존 회귀에서 실제 도메인 메서드 및 durable runtime을
호출한다. 실제 사용자 계정의 운영 사업 전체 재심사 인수를 뜻하지 않는다.
웹 타입 검사와 lint가 통과했다. 미승인 후보의 근거·용어·위치는 정식 EvidenceGraph의
집계·supports·locator에 포함하지 않고, 원래 입력의 수치와 기존 법률 검토 출처는 유지한다.
변경 제안이 없으면 동일 baseline 산출물을 candidateReview에 중복 보관하지 않는다.

운영 반영: Cloud Build `6f155685-c554-4b6c-81d9-8a37556186ad`가 성공했고,
`sha256:02ccb3f078d1ecd8c5f1090500a67dae1dc3af50c1b885635fa344d92323d20f` 이미지를
기존 Cloud Run `realytics-api-00041-mek`에 배포했다. 먼저 트래픽 0%에서 health/ready와
OpenAPI 200, 미인증 프로젝트 계획 401을 확인한 뒤 해당 리비전으로 전환했다.
ClamAV 이미지, DB 스키마·권한과 기존 인프라 구성은 유지했다. GitHub의 현재 작업
브랜치에서 전체 CI와 Vercel 연동 배포 상태를 추가 확인한다. 자동 PR 검사 실행이
생성되지 않을 때도 동일 브랜치 소스를 검증할 수 있도록 기존 CI·CodeQL에 수동
workflow_dispatch 진입을 추가했다. 기존 검사 단계와 권한은 유지한다.

후속 정확성 보완은 Cloud Build `49111a00-67e0-4290-9707-6faaaf184469`와
`sha256:9c044b3dcd259f80dd526d7633eb02dc2063b590daf7e5105d6040b2be16c7a8` 이미지에
반영했다. 기준 혼용을 발견한 00041 리비전 대신 보완본 `realytics-api-00045-lum`을
0% 트래픽에서 health/ready·OpenAPI 200, 미인증 계획 401 및 정확한 이미지 digest로
검증한 뒤 운영에 반영했다. 기존 DB 스키마·권한과 ClamAV 이미지는 유지했다.

## 6개 Pod 사전 검토

사용자의 첫 행동은 저장 자료의 자동 확인이다.
`POST /projects/{id}/chief-orchestration/preflight`가 부분 입력으로 지원되는
실제 Agent를 실행하고, 확인한 결과를 `AgentHandoffEnvelope`로 전달한다.
입력과 선행 결과의 변경에 영향을 받는 Pod를 다시 검토한 뒤 남은 사용자 확인·제출
항목을 관련 화면과 사용 목적별로 모은다. 공통 원본 누락도 사전 검토의 시작을 막지
않는다. 공개 조회의 연결 실패·미구성, 조회 결과 없음, 입력 확인 필요를 구분한다.

`reviewLoop`는 `stable`, `needs_material`, `retry_needed`,
`limit_reached`와 실행·재사용 개수, 남은 Pod, 종료 이유를 반환한다.
사용자 확인·제출 항목은 검토가 정리된 뒤 표시한다. 요청 실패는 분석 시작 조건을
충족하지 않는다. 화면과 서랍에서는 사전 확인과 정식 분석 상태를 각각 읽을 수 있다.

재개 요청은 `previousReview`의 `receiptId`와 `resumeState`를 함께 보낸다.
서버는 인증된 사용자·테넌트·프로젝트·트랙, 실행기 버전, 원본 입력 개정값,
상태 해시와 유효시간을 대조한다. 같은 입력과 선행 결과는 재사용하며,
실패 항목은 명시적인 `retryFailed` 요청으로 재실행한다.
서버의 `canContinue`가 참인 시간 예산 종료에서만 자동으로 이어간다.
화면은 한 작업에서 최대 세 번 요청하고, 추가 이어가기는 사용자가 선택한다.
동일 입력의 누적 실행·라운드 한도에 도달하면 정지한다.

큰 재개 출력은 압축해 반환하고, 서버에는 인증 범위·상태 해시·만료 시각의 수신 확인
기록을 저장한다. 원문·공개 조회 결과와 Agent 출력은 검토 출력 DB에 보관하지 않는다.
전송 상태 512 KiB와 해제 상태 4 MiB 한도를 적용한다. 브라우저에서는 검증된
사용자·세션·테넌트·프로젝트·트랙별 메모리에 최대 네 항목을 30분 동안 유지하고,
인증 변경·만료 때 정리한다. SPA 이동 후 같은 상태를 사용할 수 있다.
브라우저 전체 새로고침 후에는 새 사전 검토를 시작한다.

현재 재개 실행기는 `realytics-chief-preflight-review-v3`다. 이전 버전의 상태는
새 검토로 전환한다. 주소로 확인한 PNU는 원래의 필지 식별 근거를 유지하며,
실제 요청 PNU와 일치하는 지적 속성 응답만 지적 근거로 전달한다. 주소 확인을
지적·권리·용도지역의 확인 완료로 격상하지 않는다. 공공 연결 미설정은 제한으로
표시하고 가능한 검토와 남은 자료 요청을 제공한다. 실제 조회 실패나 응답 PNU
불일치는 실패·재시도 상태를 유지한다.

2026-11-09 보완: `landLedgerSources`의 여러 원본 필지를 입력 개정과 각 소비 Pod에
포함한다. 명시적으로 저장한 사업 대상 주소는 임시 토지조서로 확인할 수 있지만,
문서의 임의 주소를 사업 대상지로 선택하지 않는다. 확인된 PNU마다 용도지역·공시지가·
토지 특성을 기존 연결기로 조회한다. 한 단계에 최대 두 필지를 처리하고 개별 조회의
남은 시간과 완료된 종류를 유지한다. 수집 지시는 실제 조회의 미설정·미조회·실패·
연결 완료 상태를 따른다. 새 근거는 Legal Pod가 저장 자료로 덮어 버리지 않으며,
후속 Pod의 입력·선행 산출물 해시가 바뀌면 다시 검토한다. 시장 비교 조회는 최근
세 개월을 최대 12건까지 확인한다. 공사비·실제 금리·약정 조건을 공공 평균으로 채우지 않는다.

저장된 분석 입력과 값이 일치하고 보관 원본과 셀이 연결된 재무 검토 출처는 비용·
현금흐름·PF Agent 검토에 전달한다. 이는 정식 승인으로 승격하는 동작이 아니다.
각 영역의 실제 도메인 Agent 계산과 검토가 루프에 포함되지만, 35개 역할명 각각에
독립 계산 엔진이 존재한다는 의미는 아니다. 공공성 전용 정책 시뮬레이션 등 미지원
영역은 계속 구분한다. DB에는 기존의 인증 범위·해시 수신 확인만 기록하며 공개 응답,
실거래와 사전 검토 결과는 분석·재개 상태에만 유지한다. 선택 저장 원칙은 동일하다.

이번 연결의 관련 API 95건과 배포 번들 PC·모바일 14건, 웹 계약 18건·타입·린트를
검증했다. 공공 연결 응답을 주입한 실제 도메인 Agent 검증에서 세 원본의 3필지가
법률·후속 계산에 전달됐으며 원본 무변경, PNU 불일치 차단, 실패 재시도와 같은 입력
재사용을 확인했다. 이 회귀 결과는 운영 대상지 전 필지의 실제 공공 조회 성공을
의미하지 않는다. 실제 설정·공공 서비스 응답·대상 필지에 따라 화면의 조회 상태를 따른다.

정식 분석 시작 뒤에도 현재 입력과 일치하는 사전 검토를 유지하고 최신 계획을 다시
읽는다. 원본 입력 개정값이 바뀌면 이전 수신 확인을 사용해 영향을 받는 검토를 수행한다.
사전 검토는 정식 계산·심사·승인 기록과 별도 단계로 표시한다.

2026-11-05 로컬 실제 본리동 자료 검증에서 9필지·64개 월차 입력으로 6개 Pod의
실제 Agent 실행을 확인했다. 동일 입력 재개는 실행 0개·재사용 6개였으며,
압축 전 재개 상태는 440,854바이트였다. 운영 배포와 원격 CI 결과는 해당 배포 기록에서
별도로 확인한다. 공공성 Pod의 전용 정책 시뮬레이션·공공기여 최적화와 브라우저 전체
새로고침을 넘는 사전 검토 재개는 후속 개발 범위다.
## Runtime Guarantees

| Capability | Implementation | Status |
|---|---|---|
| Dependency enforcement | `PodExecutionCoordinator` validates the track-specific DAG and computes a topological run order. A single-Pod run fails when an upstream dependency is not complete. | Implemented |
| Calculation persistence | Feasibility-Cashflow and PF-Credit Pod deterministic outputs are written to `calculation_runs` immediately and referenced from the Pod artifact. | Implemented |
| Agent handoff persistence | Each completed Pod creates validated `AgentHandoffEnvelope` rows for its immediate downstream Pods. | Implemented |
| Hash Event Bus | Pod input and output snapshots use stable SHA-256 hashes. Recalculation is emitted only for explicit domain triggers or a changed output hash. | Implemented |
| Event lifecycle | Durable events move through `pending_recalculation`, `running`, `completed`, or `failed`. Storage failure is not silently ignored. | Implemented |
| Deal-state materialization | Nested numeric metrics, risk issues, evidence references, and assumption references are materialized into `deal_state_records` with an exact artifact path. Writes are batched. | Implemented |
| No fabricated PF fallback | Missing product mix or tranche structure produces `scenarioEngineReadiness.status=not_ready`; it no longer creates a synthetic 100-unit product or synthetic Senior tranche. | Implemented |
| PostgreSQL typed persistence | Hosted requests use Tenant/project-scoped normalized repositories for artifacts, AI reviews, handoffs, events, calculations and deal state. Legacy full-state compatibility is not the hosted authority. | Implemented; isolated live DB verification uses `REALYTICS_TEST_DATABASE_URL` |

## Pod Execution Contract

```text
load Shared Deal State
  -> resolve track-specific AgentPodPlan DAG
  -> verify dependency statuses
  -> build input snapshot and inputHash
  -> mark inbound recalculation events running
  -> execute deterministic Pod use case
  -> persist CalculationRun records
  -> persist AgentHandoffEnvelope records
  -> persist Pod artifact with outputHash and references
  -> recursively materialize Deal State records in one transaction
  -> complete or fail inbound events
  -> emit downstream event only when an explicit trigger or output change exists
```

## Event State Machine

```text
pending_recalculation
  -> running
     -> completed
     -> failed
```

An event keeps the same `eventId` through every transition. It also preserves
`inputHash`, `previousOutputHash`, `outputHash`, `sourceArtifactKey`, and related
`calculationRunIds`.

## Persistence Ownership

| Record | Typed table | Source of truth |
|---|---|---|
| Pod artifact | `chief_pod_artifacts` | Pod execution result and hashes |
| Pod AI review | `pod_ai_reviews` | Evidence-aware reviewer output |
| Pod handoff | `agent_handoffs` | Agent-to-Agent communication ledger |
| Recalculation event | `recalculation_events` | Event Bus lifecycle history |
| Calculation run | `calculation_runs` | Deterministic engine input/output snapshot |
| Evidence/Assumption/Metric/Risk | `deal_state_records` | Queryable Shared Deal State ledger |

The PostgreSQL adapter uses an allowlist for table and column identifiers. The
SQLite path remains the local controlled-implementation backend, and endpoint
E2E tests use a disposable SQLite file instead of the pilot runtime database.

## Verification

- DAG ordering, dependency blocking, and cycle detection tests
- Hash-event emission and event lifecycle tests
- CalculationRun and handoff API persistence tests
- Recursive materialization and source-path tests
- PostgreSQL typed SQL allowlist tests
- Optional isolated live PostgreSQL round trip using `REALYTICS_TEST_DATABASE_URL`

2026-07-14 verification completed with 42 focused runtime tests and 122 wider
Cash Flow, Approval Gate, Risk, Evidence, storage, and API regression tests.
The evidence-triggered reanalysis route now forwards the same CalculationRun and
handoff persistence sinks as direct Pod execution. Materialized metric keys omit
the structural `metrics` container while retaining the exact `artifactPath` in
the source locator. Web TypeScript and zero-warning ESLint checks also pass.

At the 2026-07-14 verification, the live PostgreSQL round trip remained unverified
because neither `REALYTICS_TEST_DATABASE_URL` nor `REALYTICS_DATABASE_URL` is
configured. The optional test skips instead of writing to a developer or pilot
database.

## Historical follow-up list (2026-07-14)

This list records the earlier runtime review. Current hosted authority, roles,
worker activation and remaining changes are governed by the dated sections above
and [the current architecture boundary](../architecture/architecture-boundary.md).

1. Run the optional integration suite against a dedicated PostgreSQL CI service.
2. Apply migrations through a versioned deployment pipeline instead of runtime `ensure_schema` calls.
3. Move full-state `app_state` dual-write to a migration compatibility flag after typed-table parity is proven.
4. Add a worker/outbox consumer for distributed Event Bus retries and idempotency leases.
5. Add tenant keys and row-level ownership constraints to every typed table.

## 운영 배선 검증 (2026-11-05)

문서·토지·시장성·딜룸·근거 화면의 수정 범위를 배포용 웹 빌드로 실행해 PC·모바일 회귀 100개와 임시 화면 캡처 검사 2개, API 통합 83개 검사를 통과했다. 웹 타입·변경 파일 린트·빌드도 통과했다. 새 토지·시장 조회의 외부 응답은 회귀 검사용 응답으로 검증했으며 실제 사용자 세션에서의 전 과정 완료를 의미하지 않는다. 운영에서는 기존 PDF 4건과 파싱 실행 기록, 서버 상태·운영 웹 주소·보호 API의 인증 차단을 확인했다.

PF 시장 근거 목록과 검토는 기존 `realytics.market_evidence`를 테넌트 범위로 사용한다. 검토 저장은 읽은 원문과 비교해 충돌 시 409를 반환한다. 운영 테이블의 기존 열과 런타임 SELECT·UPDATE 권한을 확인했으며 새 스키마 변경은 필요하지 않다. 직접 주소·호출, 운영 배포 환경, 저장소 권한이 실제 연결 상태의 기준이다. Markdown은 동작을 재현하기 위한 계약·검증 기록으로 관리한다.

운영 카카오 접수 계획은 인증된 프로젝트 권한과 정규화 감사 기록을 사용한다. 기존 로컬 DB 전용 시스템 통계는 운영에서 503으로 조회 불가를 알린다. 표준 용어 읽기는 운영 DB로 연결하고, 후보 읽기·용어집 편집의 부족한 권한은 별도 검토 대상으로 둔다.
