# Phase 11: AI PF 심사보고서 완전 개편

## 목적

Phase 11의 목표는 실제 금융기관 PF 심사보고서 구조를 기준으로, PLATFORM9의 자동 보고서 생성 기능을 단순 요약 리포트가 아닌 실무형 투자심사 보고서 수준으로 확장하는 것입니다.

기준 문서 구조는 하나금융투자 PF 심사보고서 형식을 참고하며, 보고서 화면은 금융기관 심사역이 바로 검토할 수 있도록 표지, 금융조건, 채권보전, 자금조달, 분양성, 사업성, 리스크, EOD, 자금집행 순서를 하나의 문서 흐름으로 구성합니다.

## 구현 상태

- 상태: In Progress
- 프론트엔드 화면: `apps/web/app/report/page.tsx`
- 보고서 섹션 컴포넌트:
  - `apps/web/app/report/_components/sections-part1.tsx`
  - `apps/web/app/report/_components/sections-part2.tsx`
- 보고서 샘플 데이터: `apps/web/lib/report-data.ts`
- 보고서 공통 프로젝트/금융 데이터: `apps/web/lib/platform-data.ts`
- 구조화 보고서 API: `POST /projects/{project_id}/reports/generate`
- 백엔드 보고서 생성기: `services/api-server/agents/report_agent.py`
- 보고서 섹션 정의: `services/api-server/schemas/report.py`

## 실제 문서 기반 20개 섹션

| No. | 섹션 | 목적 | 현재 반영 |
|---:|---|---|---|
| 1 | 표지 및 종합 심사 의견 | 사업명, 심의일, 금융기관명, 심의내용 요약, 조건부 승인 의견 | 반영 |
| 2 | 주요 금융조건 요약표 | Tr.A/B/C 선순위·중순위·후순위별 대출금액, 금리, 수수료, All-in Cost | 반영 |
| 3 | 주요 채권보전방안 | 신탁구조, 책임준공, 연대보증, Covenant | 반영 |
| 4 | 할인분양 Trigger | 시기별 목표분양률 및 할인율 조건표 | 반영 |
| 5 | 자금조달 구조 | Cash-In/Out 상세 내역, 단위 백만원 | 반영 |
| 6 | 자금 사용 용도 | 토지비, 공사비, 금융비용, 기타사업비 등 항목별 금액 | 반영 |
| 7 | 사업부지 확보 | 필지별 소유자, 지분율, 면적, 근저당권 현황 | 반영 |
| 8 | 사업추진 일정 | 단계별 예정/완료 일정 | 반영 |
| 9 | 금융구조도 | 차주, 신탁사, 시공사, 대주단 관계 설명 | 반영 |
| 10 | 분양대금 납입 일정 | 계약금, 중도금, 잔금 비율과 납입 시기 | 반영 |
| 11 | 분양수입금 배분 | 분양률별 상환적립 및 사업비 배분 기준 | 반영 |
| 12 | 요약 현금흐름표 | 분양률 가정 기반 CF 테이블 | 반영 |
| 13 | 차주 분석 | 시행사 회사개요, 재무현황, 신용등급 | 반영 |
| 14 | 시공사 분석 | 신세계건설 수주잔고, 재무비율, 신용등급 | 반영 |
| 15 | 분양성 분석 | 입지여건, 경쟁단지, 분양가 적정성 | 반영 |
| 16 | 사업성 분석 | LTV, Exit 분양률, 분양가 LTV | 반영 |
| 17 | Exit 전략 및 리스크 | 할인분양, 미분양, 대물변제, 임대 후 매각 시나리오 | 반영 |
| 18 | 인출 선행/후행조건 | 인출 전후 체크리스트 | 반영 |
| 19 | 채무불이행 사유(EOD) | 당연/선택적 기한이익상실 | 반영 |
| 20 | 자금집행 순서 및 변제 충당 순위 | 제세공과금, 신탁보수, 이자, 사업비, 원금, 시행이익 순위 | 반영 |

## 추가 보강 필요 항목

현재 화면 구조는 20개 섹션을 반영했지만, 실제 금융기관 제출 수준까지 올리려면 아래 항목을 추가로 진행해야 합니다.

| Priority | 항목 | 설명 |
|---|---|---|
| P0 | 보고서 데이터 스키마 고정 | 20개 섹션 key와 API payload 구조 반영 완료, 프론트 공통 타입 분리 필요 |
| P0 | 실제 문서 샘플 기반 fixture | 하나금융투자식 실제 보고서 값을 익명화하여 테스트 fixture로 저장 |
| P0 | PDF 인쇄 레이아웃 QA | A4 기준 페이지 나눔, 표 잘림, 목차/결재란 출력 검증 |
| P1 | DOCX Export | 금융기관 내부 공유용 Word 문서 생성 |
| P1 | PPT/IC Memo Export | 투자심의위원회 발표용 요약 슬라이드 생성 |
| P1 | 보고서 생성 API | `POST /projects/{project_id}/reports/generate`가 20개 섹션 JSON과 Markdown 호환 출력을 반환 |
| P1 | 보고서 템플릿 버전관리 | 금융기관별 템플릿, 문구, Covenant 기준 분리 |
| P2 | AI 근거 추적 | 각 의견에 계산 근거, 원문 근거, 법규/약정 근거 연결 |
| P2 | 내부 심사 기준 학습 | 금융기관별 Private AI 기준표 반영 |

## 권장 데이터 구조

```ts
interface PfReviewReport {
  cover: CoverSection;
  financialTerms: FinancialTermsSection;
  claimPreservation: ClaimPreservationSection;
  discountTrigger: DiscountTriggerSection;
  fundingStructure: FundingStructureSection;
  useOfFunds: UseOfFundsSection;
  landSecuring: LandSecuringSection;
  projectSchedule: ProjectScheduleSection;
  financialStructureDiagram: FinancialStructureSection;
  salesPaymentSchedule: SalesPaymentSection;
  salesRevenueDistribution: SalesDistributionSection;
  summaryCashFlow: SummaryCashFlowSection;
  borrowerAnalysis: BorrowerSection;
  contractorAnalysis: ContractorSection;
  marketAnalysis: MarketSection;
  businessFeasibility: FeasibilitySection;
  exitStrategy: ExitStrategySection;
  drawdownConditions: DrawdownConditionSection;
  eodTriggers: EodSection;
  fundExecutionOrder: FundExecutionSection;
}
```

## Acceptance Criteria

- 보고서 화면에 20개 섹션이 목차와 본문에 모두 노출된다.
- 각 섹션은 실제 PF 심사보고서에서 요구하는 핵심 표 또는 체크리스트를 포함한다.
- `POST /projects/{project_id}/reports/generate`가 20개 섹션 구조화 JSON과 `report_markdown`을 함께 반환한다.
- `npm run check-types --workspace=web`, `npm run lint --workspace=web`, `npm run build --workspace=web`가 통과한다.
- `/report` 페이지가 브라우저에서 정상 렌더링된다.
- `/report` 인쇄 시 A4 기준으로 표와 섹션 제목이 과도하게 잘리지 않는다.
- 실제 문서 기반 fixture와 현재 UI 출력값을 비교할 수 있다.

## 다음 단계

1. `report-data.ts`를 임시 샘플 데이터에서 명시적 타입 기반 데이터로 분리합니다.
2. 프론트엔드 `/report` 화면이 백엔드 구조화 JSON을 직접 렌더링하도록 연결합니다.
3. PDF/DOCX Export를 보고서 화면과 동일한 데이터 소스에서 생성하도록 연결합니다.
4. 실제 샘플 문서 기준으로 표 단위 regression test를 추가합니다.
