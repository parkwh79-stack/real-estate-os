# REALYTICS OS Next Work Roadmap

Updated: 2026-07-10  
Purpose: 파일럿 승인 가능성과 부동산 OS 운영화를 위한 통합 개발 로드맵

## 1. Product North Star

사용자가 사업계획서, 토지조서, 사업수지 Excel, 등기·공시지가 증빙을 업로드하면 다음 흐름이 하나의 원장과 하나의 오케스트레이션 안에서 동작해야 한다.

```text
자료 업로드
→ 문서 분류·파싱
→ Shared Deal State Graph 저장
→ 토지·시장·사업수지·Cash Flow·PF 병렬 분석
→ Cross-Agent Reconciliation
→ CalculationRun 저장
→ Risk Register / Evidence Graph materialize
→ cure 또는 보완자료 요청
→ 재계산
→ Approval Readiness Gate 재평가
→ 심사보고서·IC Memo·승인조건 생성
```

핵심 원칙:

- LLM은 계산자가 아니라 근거 기반 검토자다.
- 금융 수치는 결정론적 계산엔진에서만 확정한다.
- 모든 숫자·문장·리스크는 source locator와 원장 레코드를 가져야 한다.
- 실제 자료가 없으면 자동 보강을 시도하되, 추정·벤치마크·미확보 상태를 구분한다.
- Approval Readiness는 보고서 완성도가 아니라 증빙·계산·리스크·검토 상태를 기준으로 판단한다.

## 2. Current Baseline

현재 구현된 기반:

- Shared Deal State Graph 및 `deal_state_records`
- Evidence, Assumption, Metric, Risk Issue 저장 구조
- CalculationRun v2 필드 확장
- Waterfall, Unsold LTV, Tranche Recovery, Discount Stress 계산 기록
- 사업수지·Cash Flow·PF 지표 계산 기록
- Bonri-style 사업수지 Excel import 및 샘플 셀 provenance
- 6개 Chief Pod와 Pod AI Review
- Approval Readiness Gate v2
- Risk Register·Evidence Review·Chief Deal Room 화면
- Unsold LTV cure 저장·증빙 연결·재계산 API
- 자산유형별 사업성 비교 API·UI

현재 주요 운영 한계:

- 일부 Cash Flow 화면이 저장된 CalculationRun보다 기존 표현·mock에 의존할 가능성
- 일부 화면의 한글 인코딩 및 금융용어 표준화 미완료
- SQLite 중심 MVP 저장 구조
- 실제 PostgreSQL·Object Storage·Background Worker 미전환
- 실제 프로젝트 112필지와 외부 증빙을 사용한 최종 승인 E2E 미완료

## 3. P0 — Pilot Approval Critical Path

### P0-01 Cash Flow 화면 실데이터 연결

Status: Next

Scope:

- 고정 숫자 제거
- 최신 `calculationRunId` 조회
- `cashflow`와 `cash_flow_engine` 최신 run 비교
- Max Minus 표시
- PF 인출·상환 표시
- 월별 Cash Flow 표시
- Waterfall·Tranche Recovery·Unsold LTV 연결
- 모든 숫자에 CalculationRun·source locator 표시

API:

- `GET /projects/{id}/calculation-runs?engine_key=cash_flow_engine`
- `GET /projects/{id}/deal-state-records?record_type=metric`
- `GET /projects/{id}/deal-state-records?record_type=risk_issue`

Acceptance:

- 저장된 run이 없으면 숫자를 표시하지 않고 실행 안내를 표시한다.
- 화면의 Max Minus와 API CalculationRun metric이 일치한다.
- 화면의 PF 인출액과 PF 구조·Cash Flow artifact가 일치한다.
- 각 metric에 `calculationRunId`, `engineVersion`, `sourceLocator`가 표시된다.

### P0-02 Approval Readiness Gate 최종 검증

Status: In Progress

필수 Gate:

- 빈 Deal State 차단
- blocking risk issue 해소
- 필수 evidence 존재
- 최신 CalculationRun metric 사용
- 승인된 assumption 또는 active override 존재
- source locator completeness 충족
- Pod AI Review의 deterministic blocker 해소
- unresolved handoff 및 review 해소

Acceptance:

- 빈 원장은 `not_ready`이며 blocking으로 반환한다.
- `UNSOLD_LTV_BREACH`, `SENIOR_SHORTFALL`가 남아 있으면 승인 통과하지 않는다.
- LLM 의견만으로 승인 차단하지 않는다.
- Gate 결과와 required actions가 DB 원장·UI·보고서에 동일하게 표시된다.

### P0-03 파일럿 실제 데이터 E2E

Status: Next

실행 순서:

1. 사업계획서 업로드
2. 토지조서 112필지 파싱
3. 필지별 PNU resolve 및 법정동 검증
4. 공시지가 보강
5. 시장 실거래·분양사례 보강
6. 사업수지 Excel import
7. Feasibility CalculationRun 생성
8. Cash Flow CalculationRun 생성
9. PF·Waterfall·Unsold LTV 계산
10. blocking risk 생성
11. cure 선택 및 증빙 연결
12. 재계산
13. Gate 재평가
14. 최종 승인조건·심사보고서 생성

필수 결과:

- run ID 목록
- 필지별 PNU 상태
- 공시지가 증빙 목록
- 시장 근거 목록
- CalculationRun 목록
- 남은 blocker
- 승인 가능성 결과
- 최종 승인조건

### P0-04 실제 토지 증빙 보강

Status: In Progress

필지별 저장 항목:

- raw lot number
- normalized lot number
- legal dong code
- main lot number
- sub lot number
- PNU
- PNU confidence
- land category
- area sqm
- official land price base year/date
- official land price per sqm
- zoning/land characteristics
- registry issue date
- registered owner
- ownership match
- mortgage/seizure/provisional registration/trust/easement
- release condition
- acquisition status and acquisition ratio
- source URL/evidence ID
- document page/API row/Excel cell locator

원칙:

- 필지별 법정동코드가 프로젝트 기본 코드와 다르면 자동 PNU를 확정하지 않는다.
- 등기부가 없으면 등기 상태를 추정하지 않고 evidence request로 남긴다.
- 공시지가와 지적특성은 기준일·출처 없는 값을 승인자료로 사용하지 않는다.

### P0-05 원장 기반 화면 통합

대상 화면:

- Land
- Marketability
- Feasibility
- Cash Flow
- PF Structure
- Risk
- Evidence
- Report

공통 read model:

- `GET /projects/{id}/workspace/context?page=...`
- `GET /projects/{id}/deal-state-records`
- `GET /projects/{id}/calculation-runs`

각 화면은 다음을 동일하게 표시해야 한다.

- 프로젝트명
- 업로드 문서
- 최신 run
- evidence count
- missing input
- blocking risk
- approval readiness

### P0-06 한글 금융용어 표준화

Status: In Progress

표준 용어 예시:

| 화면/원문 변형 | 표준 표시 |
|---|---|
| Official Land Price | 개별공시지가 |
| Sales Rate | 누적 분양률 |
| Unsold LTV | 미분양 LTV |
| Repayment Reserve | 상환계좌 적립 |
| Senior | 선순위 |
| Mezzanine | 중순위 |
| Junior | 후순위 |
| Max Minus | 최대 부족자금 |
| Cash Sweep | 분양수입금 상환적립 |
| Exit Sale Rate | 대출금 회수 필요 분양률 |
| Cure | 리스크 보완조치 |

적용 범위:

- Chief Deal Room
- Land
- Marketability
- Asset Comparison
- Map
- Report Studio
- API error message
- PDF/DOCX/Excel export

## 4. P1 — CalculationRun Registry and Auditability

### P1-01 CalculationRun v2 완성

Status: In Progress

모든 계산 run은 다음을 저장한다.

```json
{
  "calculationRunId": "calc:waterfall:...",
  "engineKey": "waterfall",
  "engineVersion": "calculation_scenario_engine_v1",
  "inputEvidenceIds": [],
  "assumptionIds": [],
  "outputMetrics": [],
  "riskIssueIds": [],
  "sourceLocator": {},
  "scenario": {},
  "artifactRef": {},
  "createdAt": "",
  "updatedAt": ""
}
```

엔진 목록:

- `feasibility`
- `cashflow`
- `cash_flow_engine`
- `max_minus`
- `waterfall`
- `unsold_ltv`
- `tranche_recovery`
- `discount_stress`
- `exit_sale_rate`
- `ltv_ltc_dscr`
- `pf_metrics`

추가 작업:

- 모든 API에서 동일한 v2 schema 사용
- engineVersion을 실제 코드 버전과 연결
- 최신 run 선택 규칙 통일
- 실패 run도 `status=failed`로 저장
- 입력 hash와 실행 actor 저장
- CalculationRun 간 parent/child 관계 저장

### P1-02 Evidence·Assumption·Metric·RiskIssue 1급 저장

승격 대상:

- 보고서 문장
- Excel 셀
- API row
- 문서 페이지
- Agent artifact
- 계산 metric
- risk issue
- 승인·Waive·Override 상태

필수 연결:

```text
Report Paragraph
  → Evidence
  → CalculationRun / Metric
  → Assumption
  → RiskIssue
  → Reviewer / Audit Event
```

### P1-03 Evidence Graph 감사 화면

감사자가 확인할 수 있어야 하는 정보:

- 원본 파일명·hash
- PDF 페이지
- Excel sheet/cell/range
- API provider·row locator
- CalculationRun ID
- engine version
- assumption ID
- Agent artifact key
- 생성 시각
- 검토자
- 변경 이력

### P1-04 Market-Product Pod

Status: 고도화 v1 Done (2026-07-10) — `agents/market_product_depth_agent.py` +
`POST /projects/{id}/market-product-depth/analyze` + `components/market/market-depth-panel.tsx`.
market_product Pod 아티팩트에 `marketProductDepth` 주입, Pod AI Reviewer가 소비.

- 비교사례 자동 수집 ✅ (라이브 국토부 실거래가; 밸류맵 제휴 대기)
- 제안 분양가 기준 검증 ✅ (공공 실거래 중위값 앵커)
- **자료 제공주체 명명·비교 (시행사/매각주관사)** ✅ (dataProvider 우선·문서유형/파일명 추론)
- 흡수율 모델 ✅ (월별 곡선; 예측 없으면 자산유형 벤치마크)
- 상품유형별 Score ✅ (A~E)
- **AI 추가필요작업 판단** ✅ (completeness critic, AIConnection+결정론 폴백)
- 임대료·Cap Rate 검증 🟡 (income asset 축은 product planning/feasibility에서, 심화는 후속)
- 청약경쟁률 ❌ / 미분양 통계 ❌ / 경쟁공급 지도 ❌ (외부데이터 의존 — 소스 확보 후)
- 민간 데이터 license metadata 🟡

### P1-05 Cost-Schedule Pod

- 직접공사비
- 간접공사비
- 설계·감리비
- 금융비용
- 공정률
- 공사기간
- 책임준공
- 공사비 증액
- 시공사 신용
- VE 시나리오
- 비용·일정 변경 시 downstream 재계산 이벤트

### P1-06 Product Planning Agent

Status: Done (v1, 2026-07-10) — `agents/product_planning_agent.py` + `use_cases/product_planning.py`
+ `POST /projects/{id}/product-planning/compare` + `components/product/product-planning-panel.tsx`
(자산비교 페이지). 토지·규제에서 자산유형별 상품기획을 결정론 산출. 전용률·평형·주차원단위는
`PLANNING_BENCHMARKS`(basis=planning_benchmark, override 가능), 가격 근거 없으면 분양수입/NOI
미산정. LLM은 계산자가 아니라 market_product Pod 리뷰어로 근거검토만 얹는 구조(후속).

대상 자산:

- 아파트
- 오피스텔
- 상가
- 지식산업센터
- 물류센터
- 생활형숙박시설
- 리츠·임대형 자산

출력:

- 대지·연면적
- 세대수·호실수
- 전용률
- 주차대수
- 분양가
- 임대료
- Cap Rate
- 상품별 사업수지
- 상품별 Cash Flow
- 추천 상품 Mix

### P1-07 Covenant / EOD Agent

Status: Done (v1, 2026-07-10) — `agents/covenant_eod_agent.py` + `use_cases/covenant_eod.py`
+ `POST /projects/{id}/covenant-eod/evaluate`. 결정론 feasibility/PF 산출값에서 커버넌트
임계값과 EOD 조건을 도출하며 각 항목에 source locator를 부여한다. `covenant_monitoring`
(사후 모니터링)과 구분되는 **정의(definition) 엔진**이다.

- 목표 분양률 (Exit 분양률 기준) ✅
- LTV 상한 (자산 프로파일) ✅ / LTC 상한·최소 DSCR (대주 정책 기본값, 라벨 명시) ✅
- 책임준공 ✅
- 할인분양 (EOD 트리거) ✅
- Cash Sweep 개시 분양률 ✅
- 상환계좌 배분 (Tranche 우선순위→시공사) ✅
- 공사비 초과 충당 ✅
- Equity 선투입 (requiredEquity) ✅
- EOD 조건 5~6종 ✅
- 조건별 source locator ✅ + **담당 reviewer 매핑** ✅ (2026-07-10): 각 covenant/EOD에
  `responsibleReviewer`(credit_reviewer/market_stress_tester/construction_risk 등).
- **TermSheet 소비 연결** ✅ (2026-07-10): `credit_documents.generate_credit_documents`가
  `termSheetCovenants`를 `project_data.covenants`에 병합(리뷰어 override 우선) → term sheet가
  실제 값 렌더. `covenantEod` 산출물도 노출.
- **PF 화면 패널** ✅ (2026-07-10): `components/pf/covenant-eod-panel.tsx` → `/project/[id]/pf-structure`.
- **Covenant Sheet export** ✅ (2026-07-10): `covenantSheet`(한국어 마크다운) — 커버넌트 표+EOD+담당;
  PF 패널 다운로드 버튼(.md). Report Studio 통합 export(PDF/DOCX)는 P1-09에서.

무조작: 수치 근거 없으면 `needs_evidence` + dataQualityNote, 정책 기본값은
`sourceType=lender_policy_default`로 명시. P1-07 완결.

### P1-08 PF-Credit 고도화

- 대주단별 credit policy
- 선순위·중순위·후순위 구조
- Tranche별 회수율
- Senior shortfall
- Mezzanine shortfall
- Junior recovery
- 추가 담보
- Bulk Sale
- 선매입
- Refinancing
- DSRA
- cure 전후 비교표

### P1-09 Report Studio

대상 산출물:

- 토지검토서
- 사업성 검토서
- 사업수지표
- Cash Flow
- PF 심사보고서
- IC Memo
- Term Sheet
- Covenant Sheet
- Monitoring Sheet

각 문단은 다음을 포함한다.

- Evidence IDs
- Metric IDs
- Assumption IDs
- Risk Issue IDs
- source locator completeness
- 생성 Agent
- 검토 상태

## 5. P1 — Data and Infrastructure

### P1-10 PostgreSQL 전환

- SQLite MVP 종료
- migration 체계
- project/tenant isolation
- CalculationRun 인덱스
- evidence locator 검색 인덱스
- risk status 인덱스
- audit log 보존정책
- backup/restore

### P1-11 Object Storage 운영화

대상:

- 사업계획서
- 토지조서
- 사업수지 Excel
- 등기부
- 공시지가 PDF
- 리포트 PDF/DOCX

필수 metadata:

- hash
- uploader
- uploadedAt
- documentType
- parserVersion
- download permission
- retention policy

### P1-12 Background Worker

- 대용량 PDF 파싱
- Excel 검증·import
- 112필지 PNU 처리
- VWorld 보강
- 실거래가 수집
- 시나리오 일괄 계산
- PDF/DOCX 생성
- 재시도·실패·중복방지

### P1-13 Event Bus 운영화

- 이벤트 영속화
- retry policy
- idempotency key
- 실패 상태
- downstream Pod 재계산
- event audit trail
- 수동 재처리 버튼

## 6. P1 — Authentication and Security

### P1-14 실제 로그인

- OIDC
- JWKS/RS256
- Kakao 로그인
- 세션 검증
- 로그아웃 시 임시 LLM key 폐기
- tenant isolation

### P1-15 RBAC

역할:

- 심사역
- 관리자
- 검토자
- 승인자
- 감사자
- 외부 사용자

권한:

- 조회
- 업로드
- 원본 다운로드
- 승인
- Waive
- Override
- cure 선택
- 리포트 export
- admin 설정

### P1-16 Secret Manager

저장 대상:

- Gemini API Key
- OpenAI API Key
- Anthropic API Key
- VWorld API Key
- 국토부 API Key
- Kakao Key

금지:

- DB 평문 저장
- 로그 기록
- report export 포함
- frontend localStorage 영구 저장

### P1-17 사용자 감사 로그

- 로그인
- 파일 업로드
- 원본 다운로드
- AI Brain 사용
- token usage
- cure 선택
- 승인
- Waive
- Override
- 리포트 export
- Gate 결과 변경

## 7. P2 — Learning and Product Expansion

### P2-01 Historical Similar Case Search

- 과거 PF 심사
- 승인 여부
- 실제 분양률
- 실제 할인율
- 상환완료
- EOD 발생
- 손실 여부
- 회수율
- 실제 공사비
- 실제 준공기간
- 유사 리스크 처리 결과

### P2-02 Learning Loop

- 심사역 수정
- 승인조건 변경
- 용어 승인
- 리스크 Waive
- 실제 사후성과
- 예측값과 실제값 비교
- 모델 drift 감시

### P2-03 LLM Brain 고도화

- Parser Brain
- Land-Legal Reviewer
- Market Reviewer
- Feasibility Reviewer
- Cash Flow Reviewer
- PF-Credit Reviewer
- Risk Committee Reviewer
- Report Reviewer

LLM의 판단은 반드시 다음을 포함한다.

- evidence IDs
- source locators
- confidence
- review mode
- model/provider
- token usage
- deterministic result와의 구분

### P2-04 LLM Provider Routing

우선순위:

1. 사용자 API Key
2. 조직 API Key
3. REALYTICS 기본 Gemini 무료 모드
4. deterministic fallback

저장·감사:

- 모델명
- provider
- Agent ID
- selection source
- token usage
- latency
- fallback reason
- 비용 추정

### P2-05 모바일·카카오 업무 흐름

- 카카오 자료 접수
- 업무 트랙 선택
- 보완자료 요청
- Gate 상태 알림
- 승인·Waive 요청
- 모바일 심사
- 메시지 발송 audit log

### P2-06 리츠·펀드·실물금융 트랙

- 리츠 편입심사
- 임대수익
- NOI
- Cap Rate
- 배당가능이익
- 펀드 설정
- 투자자금·대출·배당
- 실물자산 매입
- 임대차·공실·리파이낸싱

### P2-07 운영·배포

- staging
- production
- CI/CD
- DB migration pipeline
- 모니터링
- 장애 알림
- backup/restore
- 보안 점검
- 개인정보 보호
- API rate limit
- 운영 runbook

## 8. Recommended Coding Order

### Sprint 1 — Approval Critical Path

- Cash Flow 화면 mock 제거
- 최신 CalculationRun 연결
- Gate v2 UI 정리
- 실제 cure E2E
- 112필지 파일럿 재실행

### Sprint 2 — Evidence and Calculation Audit

- CalculationRun v2 전 엔진 적용
- Evidence/Assumption/Metric/RiskIssue 원장 승격
- Evidence Graph 감사 화면
- Report paragraph evidence link 검증

### Sprint 3 — Domain Pod Depth

- Market-Product Pod
- Cost-Schedule Pod
- Product Planning Agent
- Covenant/EOD Agent
- PF-Credit cure 비교

### Sprint 4 — Infrastructure

- PostgreSQL
- Object Storage
- Background Worker
- Event Bus
- migration·backup·monitoring

### Sprint 5 — Security and Users

- OIDC/JWKS
- Kakao login
- tenant isolation
- RBAC
- Secret Manager
- 사용자 감사 로그

### Sprint 6 — Learning and Expansion

- Historical Similar Case outcome
- Learning Loop
- LLM Brain routing
- 모바일·카카오 업무
- 리츠·펀드·실물금융 트랙

## 9. Definition of Done for Pilot Approval

- [ ] 실제 사업계획서와 토지조서가 서버에 저장됨
- [ ] 112필지 모두 원장에 저장됨
- [ ] PNU 검증 결과가 필지별로 존재함
- [ ] 공시지가 기준연도·가격·출처가 존재함
- [ ] 등기 evidence 상태가 필지별로 존재함
- [ ] 시장 비교사례 또는 자동보강 근거가 존재함
- [ ] 사업수지 CalculationRun 존재
- [ ] Cash Flow CalculationRun 존재
- [ ] Waterfall·Unsold LTV·Tranche Recovery run 존재
- [ ] 모든 핵심 metric에 source locator 존재
- [ ] blocking risk가 승인·Waive·resolved·cure 중 하나로 처리됨
- [ ] cure 증빙이 연결됨
- [ ] 재계산 후 최신 run이 Gate에 반영됨
- [ ] Approval Readiness Gate가 `ready` 또는 정직한 `not_ready`로 표시됨
- [ ] 최종 심사보고서와 승인조건이 생성됨
- [ ] 사용자의 모든 승인·수정·다운로드 행위가 audit log에 남음

## 10. Known Non-Goals

- LLM이 Excel 수식을 대체하지 않는다.
- LLM이 PNU·공시지가·등기 상태를 임의로 생성하지 않는다.
- 자료가 없다는 이유만으로 모든 작업을 차단하지 않는다. 자동 보강 후에도 부족한 항목만 명시적으로 차단한다.
- Approval Readiness와 보고서 생성 완료를 동일하게 취급하지 않는다.
- SQLite MVP를 PostgreSQL 운영환경으로 표시하지 않는다.
