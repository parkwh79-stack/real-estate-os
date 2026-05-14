# Marketability Analysis Agent Roadmap

Updated: 2026-05-14

## Goal

Marketability Analysis Agent는 분양형 부동산의 분양 가능성, 제안 분양가 적정성, Exit 분양률, 할인분양 필요성, 대주단 관점의 분양 리스크를 검토하는 전용 Agent입니다.

현재 Agent는 MVP로서 분양형 자산 여부를 판단하고 기본 체크를 수행합니다. 다음 단계는 실제 데이터 기반의 분양성 검증 Agent로 고도화하는 것입니다.

## Scope

적용 대상:

- 아파트
- 오피스텔
- 지식산업센터
- 주상복합
- 상업시설
- 기타 분양형 부동산

비적용 또는 별도 로직 대상:

- 물류센터
- 호텔/숙박시설
- 임대형 오피스
- 장기보유 임대형 자산

## Development Priorities

### P0 - 반드시 먼저 구현

| Item | Description | Status |
|---|---|---|
| 상품유형 판별 | 아파트, 오피스텔, 지식산업센터, 주상복합, 상업시설 등 분양 상품유형을 표준 코드로 분류 | Planned |
| 주변 실거래가·분양사례 수집 | 주변 실거래, 신규 분양, 유사 사업장 사례를 수집할 connector/fixture 구조 설계 | Planned |
| 유사사례 가격 비교 | 면적, 용도, 입지, 준공/분양시점, 상품유형을 보정하여 비교표 생성 | Planned |
| 제안 분양가 적정성 판단 | 제안 분양가가 유사사례 대비 고평가/적정/저평가인지 판정 | Planned |
| Exit 분양률 계산 | PF 상환 가능 분양률, 원리금 회수 분양률, 할인분양 반영 Exit 분양률 계산 | Planned |
| 분양성 Score 산정 | 입지, 경쟁공급, 가격, 흡수율, 납입구조, Exit 회복력 기반 종합 score/grade 산정 | Planned |
| 심사보고서 문단 자동 생성 | Phase 11 PF 심사보고서 `marketAnalysis`, `businessFeasibility`, `exitStrategy` 문단 자동 생성 | Planned |

### P1 - 다음 단계

| Item | Description | Status |
|---|---|---|
| 청약경쟁률 자동 수집 | 아파트 등 청약 대상 상품의 청약경쟁률, 계약률, 미계약 이슈 수집 | Planned |
| 미분양 통계 자동 연동 | 지역/상품별 미분양 통계를 외부 데이터 또는 수동 fixture로 연동 | Planned |
| 흡수율 예측모델 | 유사사례, 공급량, 가격 수준을 기반으로 월별 흡수율 예측 | Planned |
| 할인분양 Trigger 자동 제안 | 목표 분양률 미달 시점과 할인율 trigger table 자동 제안 | Planned |
| 지도 기반 경쟁 공급 시각화 | 경쟁단지/분양사례를 지도에 표시하고 거리/권역별로 필터링 | Planned |

### P2 - 고도화

| Item | Description | Status |
|---|---|---|
| 호갱노노·직방·부동산플래닛 제휴 데이터 연동 | 제휴 또는 라이선스 기반 데이터 connector 설계 | Future |
| 분양률 예측 ML 모델 | 상품유형, 가격, 공급량, 입지, 시장지표 기반 예측모델 | Future |
| 유사 PF 심사사례 RAG | 내부 심사사례/보고서 기반 유사사례 검색 및 문단 생성 | Future |
| 사업수지 Excel 자동 연동 | 분양가, 분양수입, 할인분양, Exit 분양률을 feasibility workbook과 연결 | Future |
| 대주단 관점 Risk Memo 자동 생성 | 대주단 회수 관점의 분양 리스크 memo 자동 생성 | Future |

## Target Workflow

```text
project/business plan input
-> classify saleable product type
-> normalize product mix and proposed sale price
-> collect nearby transaction and presale comparables
-> filter and score comparable cases
-> compare proposed price against comparable price band
-> calculate exit sale rate and repayment resilience
-> calculate marketability score and grade
-> generate review findings, risk memo, and report paragraphs
-> feed outputs into PF risk matrix and Phase 11 report
```

## Agent Step Design

1. `classify_product_type`
   - Input: `project.type`, `architectural_plan`, `sales_assumptions`, `product_mix`
   - Output: `productType`, `saleable`, `classificationConfidence`, `classificationReason`

2. `collect_market_comparables`
   - Input: `land.address`, `pnu`, `productType`, `radius`, `targetPeriod`
   - Output: 실거래/분양사례 후보 목록, source metadata, collection status

3. `normalize_comparables`
   - Input: raw comparable cases
   - Output: 면적, 가격, 시점, 상품유형, 거리, 입지 등 보정값

4. `compare_pricing`
   - Input: proposed price, normalized comparable cases
   - Output: percentile, premium/discount ratio, 적정성 판정

5. `calculate_exit_sale_rate`
   - Input: PF amount, total revenue, proposed price, discount scenarios
   - Output: base exit sale rate, discounted exit sale rate, stress exit sale rate

6. `score_marketability`
   - Input: location, comparables, pricing, sales schedule, discount trigger, exit metrics
   - Output: score, grade, weighted checks

7. `generate_report_text`
   - Input: score result, evidence, pricing comparison, exit metrics
   - Output: 심사보고서 문단, risk memo, recommendations

## Proposed Output Schema

```json
{
  "analysisType": "marketability_analysis_v2",
  "applicable": true,
  "productType": "apartment",
  "classificationConfidence": 0.92,
  "dataStatus": "needs_comparable_data",
  "score": 76.5,
  "grade": "B",
  "pricing": {
    "proposedPricePerPy": 0,
    "comparableMedianPricePerPy": 0,
    "premiumToMedian": 0,
    "judgement": "reasonable"
  },
  "exitSaleRate": {
    "base": 0,
    "discounted": 0,
    "stress": 0
  },
  "comparables": [],
  "checks": [],
  "reportParagraphs": {
    "marketAnalysis": "",
    "businessFeasibility": "",
    "exitStrategy": "",
    "riskMemo": ""
  },
  "dataSources": []
}
```

## Data Source Policy

Marketability Agent는 데이터 출처를 반드시 명시해야 합니다.

Required source fields:

- `sourceName`
- `sourceType`
- `sourceUrlOrEvidenceId`
- `collectedAt`
- `baseDate`
- `licenseOrUsageNote`
- `confidence`

Rule:

- 주변 실거래가·분양사례 데이터가 없으면 제안 분양가 적정성을 확정하지 않는다.
- 데이터 출처가 없거나 base date가 없으면 report paragraph에는 “자료 미확인” 또는 “추가 검증 필요” 문구를 포함한다.
- 제휴 데이터는 라이선스/사용권 범위를 source metadata에 남긴다.

## Development Tracking

| Phase | Deliverable | Status |
|---|---|---|
| P0-1 | Product type classifier and schema | Next |
| P0-2 | Comparable case fixture and adapter | Planned |
| P0-3 | Pricing comparison pure functions and tests | Planned |
| P0-4 | Exit sale rate integration | Planned |
| P0-5 | Marketability score v2 | Planned |
| P0-6 | Report paragraph generator | Planned |
| P1-1 | Subscription/unsold-stat connector interface | Planned |
| P1-2 | Absorption model baseline | Planned |
| P1-3 | Map-based comparable supply view | Planned |
| P2-1 | Partner-data connector strategy | Future |
| P2-2 | Similar PF review RAG | Future |
| P2-3 | Lender risk memo generator | Future |

## Current Implementation Gap

Current MVP implementation:

- Determines applicability for saleable assets.
- Produces six basic checks: location, competition, pricing, payment schedule, discount trigger, exit resilience.
- Produces score, grade, summary, and recommendations.
- Is connected to API and PF review orchestration.

Missing for practical review:

- Actual comparable collection.
- Product-type specific scoring.
- Source/evidence metadata.
- Comparable price band and premium/discount calculation.
- Automatic report paragraph generation based on evidence.
- Dedicated frontend marketability review page.
