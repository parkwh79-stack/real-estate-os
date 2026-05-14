# Marketability Analysis Agent Roadmap

Updated: 2026-05-14  
Project: REALYTICS AI LAB  
Agent Code: P0-Agent-Marketability  
Agent Name: Marketability Analysis Agent  
Korean Name: 분양성·매각성·흡수율 검토 에이전트

## 1. Goal

Marketability Analysis Agent는 분양형 부동산 개발사업의 분양 가능성, 제안 분양가 적정성, 흡수속도, Exit 분양률, 할인분양 필요성, 미분양 환가가치, 대주단 관점의 분양 리스크를 검토하는 REALYTICS 전용 Agent입니다.

핵심 질문:

> 이 사업은 현재 제안된 상품 구성과 분양가로 목표 기간 내 충분히 분양되어 PF 대출 원리금을 상환할 수 있는가?

본 Agent는 단순 시세 조회 Agent가 아닙니다. 국토부 실거래가, 분양사례, 청약경쟁률, 미분양 통계, 인근 매물, 임대료, 상품별 전용률, 납입 스케줄, PF 상환구조를 결합해 시장성, 분양성, 금융회수 가능성을 함께 판단합니다.

현재 구현은 MVP입니다. 상품유형 판별, 적용 여부 판단, 기본 점수, Exit 분양률 기초값, 데이터 한계, report paragraph skeleton까지 API 응답에 포함하도록 확장했습니다. 다음 단계는 실제 데이터 기반의 분양성 검증 Agent로 고도화하는 것입니다.

## 2. Agent Position in REALYTICS

```text
Land Identification Agent
    ↓
Regulation Analysis Agent
    ↓
Mass Planning / Feasibility Agent
    ↓
Marketability Analysis Agent
    ↓
Business Feasibility Agent
    ↓
Cash Flow Agent
    ↓
PF Structuring Agent
    ↓
Credit Review / IC Memo Agent
```

| Upstream / Downstream | 연결 내용 |
|---|---|
| Land Agent | 주소, PNU, 입지, 토지면적, 용도지역 |
| Regulation Agent | 허용용도, 용적률, 건축가능 규모 |
| Feasibility Agent | 분양면적, 총분양수입, 사업수지 |
| Cash Flow Agent | 계약금, 중도금, 잔금, 분양수입 유입 시점 |
| PF Structuring Agent | 대출금, Tranche, LTV, LTC, Exit 분양률 |
| Credit Review Agent | 분양위험, 할인분양 Trigger, 미분양 환가위험 |

## 3. Scope

### 3.1 적용 대상

Marketability Analysis Agent는 분양수입 또는 개별 매각수입이 주요 Exit 재원인 자산에 적용합니다.

| Product Type | 적용 여부 | 설명 |
|---|---:|---|
| Apartment | 적용 | 일반분양, 조합분양, 주상복합 내 공동주택 |
| Officetel | 적용 | 주거용·업무용 오피스텔 모두 포함 |
| Mixed-use Residential | 적용 | 아파트 + 오피스텔 + 근린생활시설 |
| Commercial Pre-sale | 적용 | 근린생활시설, 상가, 단지 내 상가 |
| Knowledge Industry Center | 적용 | 지식산업센터, 지원시설 포함 |
| Section Office | 적용 | 섹션오피스, 소형 업무시설 |
| Serviced Residence / 생활형숙박시설 | 조건부 적용 | 분양형 생숙은 적용, 운영형 호텔은 별도 Agent |
| Urban Residential Housing | 적용 | 도시형생활주택, 청년주택 등 |
| Other Strata-sale Assets | 조건부 적용 | 구분분양 또는 개별 매각 가능한 자산 |

### 3.2 비적용 또는 별도 로직 대상

| Product Type | 처리 방식 | 사유 |
|---|---|---|
| Logistics Center | 별도 Logistics Leasing / Exit Agent | 임대차, Cap Rate, 선매입, BTS 구조 중심 |
| Hotel | 별도 Hospitality Operation Agent | 객실 ADR, OCC, GOP, 운영계약 중심 |
| Long-term Rental Office | 별도 Leasing Agent | 임대료, 공실률, NOI, Cap Rate 중심 |
| Data Center | 별도 Tenant / Power / Lease Agent | 전력, 임차인, 장기계약 중심 |
| Rental Housing | 별도 Rental Feasibility Agent | 임대료, 보증금, NOI, 운영비 중심 |
| Senior Housing / Healthcare | 별도 Operating Business Agent | 운영수익, 사업자 역량 중심 |

생활형숙박시설은 구분분양형이면 본 Agent를 적용하고, 호텔처럼 운영수익을 기반으로 하는 경우에는 별도 운영형 Agent를 적용합니다.

## 4. Core Analytical Questions

1. 해당 상품은 분양형 자산인가?
2. 제안 분양가는 인근 유사사례 대비 적정한가?
3. 수요 기반은 충분한가?
4. 경쟁 공급 부담은 어느 정도인가?
5. 상품구성, 전용률, 면적, 주차대수, 브랜드, 입지가 경쟁력이 있는가?
6. 정상 분양 시 PF 대출금 상환이 가능한가?
7. 할인분양 시에도 선순위·중순위·후순위 상환이 가능한가?
8. 목표 분양률 미달 시 어떤 할인분양 Trigger가 필요한가?
9. 미분양 발생 시 담보가치 또는 환가가치는 충분한가?
10. 대주단 관점에서 분양위험은 상·중·하 중 어디에 해당하는가?

## 5. Development Priorities

### 5.1 P0 - 반드시 먼저 구현

P0는 실무에서 분양성 검토서 초안을 생성할 수 있는 최소 기능입니다.

| Item | Description | Status |
|---|---|---|
| 상품유형 판별 | 아파트, 오피스텔, 지식산업센터, 주상복합, 상업시설, 생활형숙박시설 등 분양 상품유형을 표준 코드로 분류 | In Progress |
| 분양형 자산 여부 판단 | 매각형·분양형·임대형·운영형 여부를 판단하고 본 Agent 적용 가능성을 결정 | In Progress |
| Product Mix 정규화 | 타입, 전용면적, 계약면적, 호실수, 분양가, 분양면적, 전용률을 표준화 | Planned |
| 주변 실거래가·분양사례 수집 | 주변 실거래, 신규 분양, 유사 사업장 사례를 수집할 connector/fixture 구조 설계 | Planned |
| 유사사례 가격 비교 | 면적, 용도, 입지, 준공/분양시점, 상품유형을 보정하여 비교표 생성 | Planned |
| 제안 분양가 적정성 판단 | 제안 분양가가 유사사례 대비 고평가/적정/저평가인지 판정 | Planned |
| Exit 분양률 계산 | PF 상환 가능 분양률, 원리금 회수 분양률, 할인분양 반영 Exit 분양률 계산 | In Progress |
| 할인분양 민감도 분석 | 5%, 10%, 15%, 20% 할인 시 PF 회수 가능성 산정 | In Progress |
| 분양성 Score 산정 | 입지, 수요, 경쟁공급, 가격, 상품성, 흡수율, Exit 회복력 기반 종합 score/grade 산정 | In Progress |
| 심사보고서 문단 자동 생성 | Phase 11 PF 심사보고서 marketAnalysis, businessFeasibility, exitStrategy, riskMemo 문단 자동 생성 | In Progress |
| Evidence Metadata 저장 | 모든 가격·사례·통계 데이터에 source metadata 부여 | In Progress |

### 5.2 P1 - 다음 단계

| Item | Description | Status |
|---|---|---|
| 청약경쟁률 자동 수집 | 아파트 등 청약 대상 상품의 청약경쟁률, 계약률, 미계약 이슈 수집 | Planned |
| 미분양 통계 자동 연동 | 지역/상품별 미분양 통계를 외부 데이터 또는 수동 fixture로 연동 | Planned |
| 흡수율 예측모델 | 유사사례, 공급량, 가격 수준을 기반으로 월별 흡수율 예측 | Planned |
| 할인분양 Trigger 자동 제안 | 목표 분양률 미달 시점과 할인율 trigger table 자동 제안 | Planned |
| 지도 기반 경쟁 공급 시각화 | 경쟁단지/분양사례를 지도에 표시하고 거리/권역별로 필터링 | Planned |
| 상품유형별 Scoring Model | 아파트, 오피스텔, 상가, 지식산업센터별 독립 가중치 적용 | Planned |
| 수익환원 기반 가격 검증 | 오피스텔, 상가, 지식산업센터의 임대료 기반 적정 분양가 역산 | Planned |
| 잔금납부위험 분석 | 중도금 대출, 투자수요, 실거주수요, 준공 후 담보대출 가능성 반영 | Planned |

### 5.3 P2 - 고도화

| Item | Description | Status |
|---|---|---|
| 호갱노노·직방·부동산플래닛 제휴 데이터 연동 | 제휴 또는 라이선스 기반 데이터 connector 설계 | Future |
| 분양률 예측 ML 모델 | 상품유형, 가격, 공급량, 입지, 시장지표 기반 예측모델 | Future |
| 유사 PF 심사사례 RAG | 내부 심사사례/보고서 기반 유사사례 검색 및 문단 생성 | Future |
| 사업수지 Excel 자동 연동 | 분양가, 분양수입, 할인분양, Exit 분양률을 feasibility workbook과 연결 | Future |
| 대주단 관점 Risk Memo 자동 생성 | 대주단 회수 관점의 분양 리스크 memo 자동 생성 | Future |
| 분양대행사 의견 비교 | 분양대행사 제안가와 Agent 산출 적정가 비교 | Future |
| 사후 분양률 Monitoring | 실제 분양률과 최초 예측값 비교, Agent 모델 업데이트 | Future |
| Private AI 심사기준 학습 | 회사 내부 PF 심사보고서의 문체·판단기준·리스크 표현 학습 | Future |

## 6. Target Workflow

```text
project / business plan input
    ↓
classify saleable product type
    ↓
check applicability of Marketability Analysis Agent
    ↓
normalize product mix and proposed sale price
    ↓
define market area by radius and administrative boundary
    ↓
collect nearby transaction, presale, listing, rent, subscription, and unsold data
    ↓
filter and score comparable cases
    ↓
normalize comparable prices by area, age, distance, product type, and timing
    ↓
compare proposed price against comparable price band
    ↓
estimate absorption speed and sales schedule
    ↓
calculate exit sale rate and repayment resilience
    ↓
analyze discount sale scenarios
    ↓
calculate marketability score and grade
    ↓
generate review findings, risk memo, and report paragraphs
    ↓
feed outputs into PF risk matrix and Phase 11 report
```

## 7. Agent Step Design

### 7.1 classify_product_type

상품유형을 표준 코드로 분류합니다.

Product Type Codes:

| Code | Description |
|---|---|
| apartment | 아파트 |
| urban_residential | 도시형생활주택 |
| officetel_residential | 주거용 오피스텔 |
| officetel_office | 업무용 오피스텔 |
| mixed_use_residential | 주상복합 |
| commercial_retail | 상업시설 |
| knowledge_industry_center | 지식산업센터 |
| section_office | 섹션오피스 |
| serviced_residence | 생활형숙박시설 |
| other_saleable | 기타 분양형 자산 |
| non_saleable | 비분양형 자산 |

### 7.2 determine_applicability

```text
if product generates sale proceeds through individual unit sales:
    applicable = true
elif product is primarily long-term lease / operating income asset:
    applicable = false
else:
    applicable = conditional
```

### 7.3 normalize_product_mix

표준화 항목:

- 타입
- 전용면적
- 계약면적
- 호실수
- 분양가
- 총분양수입
- 전용률
- VAT 포함 여부
- 타입별/층별/향별 가격 편차

### 7.4 define_market_area

| Product Type | 1차 권역 | 2차 권역 | 3차 권역 |
|---|---:|---:|---:|
| Apartment | 1km | 3km | 동일 구/시 |
| Officetel | 500m | 1km | 업무권역 |
| Commercial | 300m | 500m | 상권권역 |
| Knowledge Industry Center | 3km | 5km | 산업권역 |
| Serviced Residence | 500m | 1km | 관광/업무권역 |
| Section Office | 500m | 1km | 업무권역 |

### 7.5 collect_market_comparables

Required Data Categories:

| Category | Description | Priority |
|---|---|---|
| Actual Transaction | 실거래가 | P0 |
| Presale Case | 신규 분양가 | P0 |
| Listing | 매물가 | P0 |
| Rent | 임대료 | P1 |
| Subscription | 청약경쟁률 | P1 |
| Unsold | 미분양 통계 | P1 |
| Auction | 경매 낙찰가율 | P1 |
| Internal PF Case | 내부 심사사례 | P2 |

### 7.6 normalize_comparables

Normalization Factors:

- Distance
- Product Type
- Area
- Age
- Brand
- Transit
- Scale
- Parking
- Exclusive Ratio
- Floor / Visibility
- Regulation
- Market Timing

### 7.7 compare_pricing

Pricing Judgement Rules:

| Premium to Median | Judgement |
|---:|---|
| below -10% | underpriced |
| -10% to +5% | reasonable |
| +5% to +10% | slightly_high |
| +10% to +20% | high |
| above +20% | excessive |

### 7.8 analyze_demand

상품별 수요요인:

- Apartment: 인구·세대수, 학군, 교통, 청약경쟁률, 전세가율, 미분양, 소득수준
- Officetel: 1~2인 가구, 업무시설 밀도, 임대료, 공실률, 주차대수, 대체주거 공급
- Commercial: 유동인구, 배후세대, 가시성, 동선, 임대료, 공실률, 권리금
- Knowledge Industry Center: 산업단지, 기업수요, 입주 가능 업종, 세제혜택, 대출 가능성, 공급 파이프라인
- Serviced Residence: 관광수요, 업무수요, 규제 리스크, 운영사, 객실면적, 임대수익률

### 7.9 analyze_supply_competition

Supply Risk Levels:

| Level | Description |
|---|---|
| low | 경쟁 공급 제한적 |
| moderate | 유사 공급 존재하나 흡수 가능 |
| high | 공급 과다 또는 미분양 증가 |
| severe | 동일 권역 내 분양 실패 사례 다수 |

### 7.10 forecast_absorption

```text
Absorption Score =
price competitiveness
+ location competitiveness
+ demand depth
+ supply pressure
+ product fit
+ financing condition for buyers
+ brand / contractor credibility
+ market cycle
```

### 7.11 calculate_exit_sale_rate

Exit 분양률은 PF 대출금 및 필수 상환액을 회수하기 위해 필요한 최소 분양률입니다.

```text
Base Exit Sale Rate =
Required Repayment Amount / Total Sales Revenue
```

반영 요소:

- 계약금·중도금·잔금 납입 스케줄
- 준공 전·준공 후 상환계좌 적립비율
- 분양수입금 Waterfall
- 미지급 공사비
- 금융비용
- Tranche별 상환순위
- 할인분양 시 총분양수입 감소
- 분양률 시점별 달성 여부

### 7.12 analyze_unsold_recovery

| Product Type | Unsold Recovery Method |
|---|---|
| Apartment | 할인매각가, 경매낙찰률, 준공 후 담보대출 |
| Officetel | 임대수익률, 담보대출 가능액, 할인매각가 |
| Commercial | 임대료 수익환원, 층별 환가율, 공실률 |
| Knowledge Industry Center | 임대료, 입주업종 수요, 담보인정비율 |
| Serviced Residence | 운영수익, 숙박수요, 규제 리스크, 할인매각 |
| Section Office | 임대료, 소형 업무수요, 공실률 |

### 7.13 propose_discount_trigger

Default Trigger Template:

| Time from Sales Launch | Target Sales Rate | Discount Action |
|---:|---:|---|
| 3 months | 25% | 마케팅 강화, 조건부 혜택 |
| 6 months | 40% | 5% 이내 간접할인 검토 |
| 12 months | 60% | 10% 이내 할인분양 검토 |
| 18 months | 70% | 15% 이내 할인분양 및 임대전환 검토 |
| Completion | 80% | 준공 후 담보대출 또는 Bulk Sale 검토 |

### 7.14 score_marketability

Base Scoring Framework:

| Category | Weight |
|---|---:|
| Location Competitiveness | 20 |
| Demand Base | 15 |
| Supply / Competition Risk | 15 |
| Pricing Adequacy | 20 |
| Product Competitiveness | 10 |
| Absorption Speed | 10 |
| PF Exit Stability | 10 |
| Total | 100 |

Grade:

| Score | Grade | Meaning |
|---:|---|---|
| 85-100 | A | 분양성 우수 |
| 70-84 | B | 분양성 양호 |
| 55-69 | C | 보통, 가격·조건 보완 필요 |
| 40-54 | D | 분양위험 높음 |
| below 40 | E | 사업구조 재검토 필요 |

### 7.15 generate_report_text

Output Paragraph Types:

| Paragraph | Description |
|---|---|
| marketAnalysis | 시장환경 및 수요·공급 분석 |
| pricingAnalysis | 분양가 적정성 분석 |
| businessFeasibility | 분양수입 기반 사업성 검토 |
| exitStrategy | PF 상환 및 Exit 분양률 검토 |
| discountTrigger | 할인분양 Trigger 제안 |
| unsoldRisk | 미분양 환가위험 분석 |
| riskMemo | 대주단 관점 주요 리스크 |
| recommendation | 승인조건 또는 보완사항 |

## 8. Proposed Output Schema

```json
{
  "analysisType": "marketability_analysis_v2",
  "agentName": "Marketability Analysis Agent",
  "applicable": true,
  "applicabilityReason": "",
  "productType": "apartment",
  "classificationConfidence": 0.92,
  "dataStatus": "partial",
  "dataLimitations": [],
  "missingRequiredData": [],
  "projectSummary": {},
  "productMix": [],
  "marketArea": {},
  "pricing": {},
  "demandAnalysis": {},
  "supplyAnalysis": {},
  "absorptionForecast": {},
  "exitSaleRate": {
    "base": 0,
    "discount5": 0,
    "discount10": 0,
    "discount15": 0,
    "stress": 0,
    "trancheExitSaleRate": {
      "senior": 0,
      "mezzanine": 0,
      "junior": 0
    }
  },
  "discountTrigger": [],
  "unsoldRecovery": {},
  "score": 0,
  "grade": "",
  "checks": [],
  "riskFlags": [],
  "recommendations": [],
  "reportParagraphs": {
    "marketAnalysis": "",
    "pricingAnalysis": "",
    "businessFeasibility": "",
    "exitStrategy": "",
    "discountTrigger": "",
    "unsoldRisk": "",
    "riskMemo": "",
    "recommendation": ""
  },
  "dataSources": []
}
```

## 9. Data Source Policy

Marketability Agent는 데이터 출처를 반드시 명시해야 합니다.

Required Source Fields:

| Field | Description |
|---|---|
| sourceName | 데이터 출처명 |
| sourceType | public_api, partner_api, internal_db, manual_upload, web, estimate |
| sourceUrlOrEvidenceId | URL 또는 내부 evidence id |
| collectedAt | 수집일시 |
| baseDate | 데이터 기준일 |
| licenseOrUsageNote | 사용권, 라이선스, 제휴 여부 |
| confidence | 신뢰도 |

Data Source Hierarchy:

| Priority | Source Type | Examples |
|---:|---|---|
| 1 | Official Public Data | 국토부 실거래가, 청약홈, 미분양 통계, 통계청 |
| 2 | Licensed Partner Data | 밸류맵, 부동산플래닛, 호갱노노, 직방, KB |
| 3 | Internal Database | 과거 PF 심사보고서, 분양사례 DB, 사업수지 DB |
| 4 | Manual Upload | 분양공고문, 분양대행사 제안서, 감정평가서 |
| 5 | Analyst Estimate | 실무자 추정치, 시장 인터뷰, 보수적 가정 |

Data Usage Rules:

1. 주변 실거래가·분양사례 데이터가 없으면 제안 분양가 적정성을 확정하지 않는다.
2. 데이터 출처가 없거나 base date가 없으면 report paragraph에는 “자료 미확인” 또는 “추가 검증 필요” 문구를 포함한다.
3. 제휴 데이터는 라이선스/사용권 범위를 source metadata에 남긴다.
4. 호갱노노·직방·KB·부동산플래닛 등 민간 플랫폼 데이터는 공식 API, 제휴, 라이선스 또는 사용권이 확보된 경우에만 connector로 사용한다.
5. 비공식 크롤링 또는 약관 위반 가능성이 있는 방식은 기본 Agent 기능으로 포함하지 않는다.
6. 동일한 결론은 최소 2개 이상의 데이터 카테고리로 교차검증한다.
7. 단일 매물 호가만으로 분양가 적정성을 판단하지 않는다.
8. 분양률 자료가 없는 경우 흡수율은 “추정”으로 표시한다.
9. 내부 PF 사례를 사용할 경우 해당 사례의 사업연도와 시장상황 차이를 명시한다.

## 10. Data Adapter Strategy

```text
Marketability Analysis Agent
    ↓
Market Data Router
    ├─ MOLIT Transaction API Adapter
    ├─ CheongyakHome Adapter
    ├─ Unsold Housing Statistics Adapter
    ├─ VWorld Location Adapter
    ├─ Valuemap Adapter
    ├─ Real Estate Planet Adapter
    ├─ Hogangnono / Zigbang Licensed Data Adapter
    ├─ KB Real Estate Adapter
    ├─ Internal Comparable DB Adapter
    ├─ Internal PF Review RAG Adapter
    └─ Manual Upload Parser
```

Adapter Failover Rule:

```text
If Hogangnono / Zigbang data is unavailable:
    use MOLIT transaction data
    + CheongyakHome
    + KB / Real Estate Planet if licensed
    + internal comparable DB
    + manual upload
```

Agent는 특정 데이터 소스가 실패해도 중단되지 않아야 합니다. 단, 결과물에는 어떤 데이터가 누락되었는지 명확히 표시해야 합니다.

## 11. Product-specific Scoring Logic

### 11.1 Apartment

| Category | Weight |
|---|---:|
| 입지·교통 | 20 |
| 학군·생활인프라 | 10 |
| 실수요 기반 | 15 |
| 청약경쟁률·분양사례 | 15 |
| 분양가 적정성 | 20 |
| 입주 예정 공급 | 10 |
| PF Exit 안정성 | 10 |

### 11.2 Officetel

| Category | Weight |
|---|---:|
| 역세권·업무지 접근성 | 20 |
| 1~2인 가구 및 임대수요 | 15 |
| 임대수익률 | 20 |
| 경쟁공급 | 15 |
| 분양가 적정성 | 15 |
| 주차·전용률·상품성 | 10 |
| PF Exit 안정성 | 5 |

### 11.3 Commercial Retail

| Category | Weight |
|---|---:|
| 유동인구·상권 | 20 |
| 가시성·접근성 | 15 |
| 배후세대 | 10 |
| 임대료 부담능력 | 20 |
| 층별 가격 적정성 | 15 |
| 공실위험 | 10 |
| PF Exit 안정성 | 10 |

### 11.4 Knowledge Industry Center

| Category | Weight |
|---|---:|
| 산업입지 | 20 |
| 기업수요 | 15 |
| 입주업종 적합성 | 15 |
| 분양가·임대료 수익률 | 20 |
| 공급 파이프라인 | 15 |
| 세제·대출 메리트 | 10 |
| PF Exit 안정성 | 5 |

### 11.5 Serviced Residence / 생활형숙박시설

| Category | Weight |
|---|---:|
| 입지·관광/업무수요 | 20 |
| 운영수익 가능성 | 15 |
| 규제 리스크 | 20 |
| 분양가 적정성 | 15 |
| 유사상품 공급 | 10 |
| 운영사·위탁구조 | 10 |
| PF Exit 안정성 | 10 |

## 12. Development Tracking

| Phase | Deliverable | Status |
|---|---|---|
| P0-1 | Product type classifier and schema | In Progress |
| P0-2 | Applicability checker | In Progress |
| P0-3 | Product mix normalizer | Planned |
| P0-4 | Comparable case fixture and adapter | Planned |
| P0-5 | Pricing comparison pure functions and tests | Planned |
| P0-6 | Exit sale rate integration | In Progress |
| P0-7 | Discount scenario analyzer | In Progress |
| P0-8 | Marketability score v2 | In Progress |
| P0-9 | Evidence metadata schema | In Progress |
| P0-10 | Report paragraph generator | In Progress |
| P1-1 | Subscription / unsold-stat connector interface | Planned |
| P1-2 | Absorption model baseline | Planned |
| P1-3 | Map-based comparable supply view | Planned |
| P1-4 | Product-specific scoring engine | Planned |
| P1-5 | Unsold recovery model | Planned |
| P2-1 | Partner-data connector strategy | Future |
| P2-2 | Similar PF review RAG | Future |
| P2-3 | Lender risk memo generator | Future |
| P2-4 | Monitoring and back-testing module | Future |

## 13. Current Implementation Gap

### 13.1 Current MVP Implementation

- Determines applicability for saleable assets.
- Produces six basic checks:
  - location
  - competition
  - pricing
  - payment schedule
  - discount trigger
  - exit resilience
- Produces score, grade, summary, recommendations.
- Produces product type classification, confidence, data status, missing data list, source metadata list, Exit sale rate skeleton, and report paragraph skeleton.
- Is connected to API and PF review orchestration.

### 13.2 Missing for Practical Review

| Missing Item | Why It Matters |
|---|---|
| Actual comparable collection | 실제 분양가 적정성 판단 불가 |
| Product-type specific scoring | 아파트와 상가를 같은 기준으로 평가하는 오류 방지 |
| Source/evidence metadata persistence | 심사보고서 신뢰성 확보 |
| Comparable price band | 제안 분양가의 고평가/저평가 판단 |
| Premium/discount calculation | 분양가 민감도 분석 |
| Absorption forecast | PF 만기 전 회수 가능성 판단 |
| Discount scenario analysis | 할인분양 Trigger 설정 |
| Unsold recovery model | 미분양 담보가치 및 환가위험 판단 |
| Automatic report paragraph generation | Phase 11 심사보고서 자동화 |
| Dedicated frontend marketability review page | 실무자 검토 및 수정 편의성 |
| Back-testing framework | 예측 분양률과 실제 분양률 비교 |

## 14. Acceptance Criteria

### 14.1 Functional Criteria

- [ ] Agent can classify product type with confidence score.
- [ ] Agent can determine whether project is saleable.
- [ ] Agent can normalize product mix and proposed price.
- [ ] Agent can ingest at least manual comparable fixtures.
- [ ] Agent can calculate comparable price band.
- [ ] Agent can judge proposed price adequacy.
- [ ] Agent can calculate base Exit sale rate.
- [ ] Agent can calculate 5%, 10%, 15% discount scenarios.
- [ ] Agent can produce marketability score and grade.
- [ ] Agent can generate PF review paragraphs.
- [ ] Agent can list missing data and data limitations.

### 14.2 Evidence Criteria

- [ ] Every comparable case has source metadata.
- [ ] Every pricing conclusion refers to evidenceIds.
- [ ] If no evidence exists, Agent does not produce definitive conclusion.
- [ ] Data base date is visible in output.
- [ ] Partner data usage note is stored.

### 14.3 Report Criteria

- [ ] Output can be inserted into Phase 11 PF review report.
- [ ] Report paragraphs include market analysis, pricing, exit, and risk memo.
- [ ] Report paragraph explicitly states missing data when data is incomplete.
- [ ] Final recommendation is decision-oriented.

## 15. Test Scenarios

| Scenario | Expected |
|---|---|
| Apartment project with slightly high proposed price and high supply | Pricing `slightly_high`, supply risk moderate/high, score B or C |
| Officetel project with weak rent yield and many competitors | Pricing high if yield below target, risk flag `LOW_RENT_YIELD` |
| Commercial project with high vacancy and aggressive first-floor price | Supply/vacancy risk high, pricing excessive, unsold recovery weak |
| Knowledge industry center with unclear tenant demand | Demand confidence low, supply risk high, dataStatus `needs_tenant_demand_data` |
| Serviced residence marketed as residential substitute | Regulatory risk high, marketability score C/D, risk memo flags usage restriction |

## 16. Frontend Requirements

Marketability Review Page should include:

- Summary Panel: Product Type, Applicable, Score, Grade, Key Risk Flags, Final Recommendation
- Pricing Panel: Proposed Price, Comparable Median, Premium/Discount, Comparable Table, Price Band Chart
- Demand / Supply Panel: Market Area Map, Nearby Competitors, Upcoming Supply, Unsold Statistics, Subscription Data
- Exit Panel: Total Sales Revenue, PF Amount, Base Exit Sale Rate, Discounted Exit Sale Rate, Tranche recovery
- Report Panel: Market Analysis Paragraph, Pricing Paragraph, Exit Strategy Paragraph, Risk Memo, Copy to IC Memo Button

## 17. Risk Flags

| Risk Flag | Condition |
|---|---|
| PRICE_PREMIUM_HIGH | 제안 분양가가 비교사례 중앙값 대비 10% 이상 높음 |
| PRICE_PREMIUM_EXCESSIVE | 제안 분양가가 비교사례 중앙값 대비 20% 이상 높음 |
| SUPPLY_OVERHANG | 인근 공급 예정 물량 과다 |
| UNSOLD_INCREASE | 지역 미분양 증가 |
| LOW_SUBSCRIPTION | 청약경쟁률 저조 |
| LOW_RENT_YIELD | 임대수익률이 요구수익률 미만 |
| WEAK_PARKING | 주차대수 경쟁력 부족 |
| LOW_EXCLUSIVE_RATIO | 전용률 열위 |
| REGULATORY_RISK | 생활형숙박시설, 지식산업센터 등 규제 리스크 |
| EXIT_RATE_HIGH | Exit 분양률이 70% 이상 |
| DISCOUNT_REQUIRED | 목표 분양률 달성을 위해 할인분양 필요 |
| MEZZ_RECOVERY_WEAK | 중순위 이하 회수위험 존재 |
| NO_COMPARABLE_DATA | 비교사례 부족 |
| DATA_BASE_DATE_OLD | 데이터 기준일이 오래됨 |

## 18. Recommendation Logic

Pricing Recommendation:

- 제안 분양가 유지 가능
- 3~5% 가격 조정 권고
- 5~10% 가격 조정 필요
- 타입별 가격 재조정 필요
- 층별·향별·상가 위치별 가격 차등화 필요

Marketing Recommendation:

- 사전청약 또는 수요조사 필요
- 분양대행사 의견 추가 확인 필요
- 계약금 정액제 검토
- 중도금 무이자 검토
- 초기 계약자 혜택 검토

PF Condition Recommendation:

- 목표 분양률 Trigger 약정 반영
- 할인분양 Trigger 반영
- 분양수입금 상환계좌 적립비율 강화
- 준공 후 미분양 담보신탁 조건 반영
- 시공사 또는 신탁사 책임준공 보완
- 후순위 Tranche 축소 또는 Equity 증액

## 19. Example Final Opinion

```text
본건 사업은 역세권 접근성과 배후수요를 감안할 때 기본적인 분양수요는 존재하는 것으로 판단된다. 다만 제안 분양가는 인근 유사사례 중앙값 대비 다소 높은 수준으로, 초기 분양률은 가격조건 및 중도금 금융조건에 민감할 것으로 예상된다.

Base Case 기준 준공 시점 누적 분양률은 약 75~80% 수준으로 추정되며, PF 원리금 회수를 위한 Exit 분양률은 약 60% 내외로 산정된다. 10% 할인분양 시에도 선순위 회수 가능성은 유지되나, 중순위 이하 Tranche는 회수 안정성이 저하될 수 있다.

따라서 본건은 분양성 등급 B로 판단하되, 분양개시 후 6개월 내 40%, 12개월 내 60%, 준공 전 75%의 목표분양률 Trigger를 설정하고, 목표 미달 시 단계적 할인분양 및 분양조건 조정을 약정에 반영할 필요가 있다.
```

## 20. Near-term Implementation Plan

Sprint 1:

- Product type classifier
- Applicability checker
- Product mix schema
- Manual comparable fixture format

Sprint 2:

- Comparable normalization
- Pricing comparison
- Price band output
- Pricing judgement rules

Sprint 3:

- Exit sale rate calculator
- Discount scenario analyzer
- Tranche recovery logic

Sprint 4:

- Marketability score v2
- Risk flags
- Recommendation logic

Sprint 5:

- Report paragraph generator
- Evidence metadata
- Phase 11 PF review integration

Sprint 6:

- Frontend marketability review page
- Test scenarios
- Back-testing structure

## 21. Final Product Definition

Marketability Analysis Agent는 아파트, 오피스텔, 생활형숙박시설, 지식산업센터, 상가 등 분양형 개발사업의 분양성·가격 적정성·흡수속도·PF Exit 가능성을 분석하는 Agent입니다. 주소/PNU와 사업개요를 입력하면 실거래가, 분양사례, 청약경쟁률, 미분양 통계, 내부 PF 사례 및 제휴 가능한 민간 부동산 데이터를 활용하여 적정 분양가, 목표 분양률, Exit 분양률, 할인분양 Trigger, 미분양 환가가치를 산정하고, 최종적으로 분양성 Score와 PF 심사보고서용 리스크 문단을 생성합니다.
