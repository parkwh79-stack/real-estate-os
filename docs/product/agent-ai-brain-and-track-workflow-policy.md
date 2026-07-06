# Agent AI Brain and Track Workflow Policy v2

Updated: 2026-07-02  
Project: REALYTICS x PLATFORM9  
Status: In Progress  
Owner: REALYTICS AI LAB / PLATFORM9  
Purpose: AI Deal Room 기반 부동산 개발, PF, 부동산금융, 실물금융, 리츠, 펀드 심사 운영정책

---

## 1. Core Principle

REALYTICS OS는 Agent를 단순 순서대로 실행하는 시스템이 아니다. 각 프로젝트는 하나의 **AI Deal Room**으로 생성되며, 모든 Agent는 동일한 3개 저장소를 읽고 쓴다.

1. **Shared Deal State Graph**  
   프로젝트의 사실관계, 참여자, 토지, 인허가, 상품, 사업수지, 금융구조, 리스크, 의사결정 상태를 하나의 graph로 관리한다.

2. **Evidence Vault**  
   모든 숫자, 문장, 판단, 보고서 문단의 출처를 저장한다. 문서, 엑셀 셀, 등기부, 인허가 공문, API 결과, 시장자료, 감정평가서, 사업성보고서, 내부 심사보고서를 evidence로 관리한다.

3. **Assumption Book**  
   아직 확정되지 않았지만 계산이나 판단에 필요한 가정을 저장한다. 예: 분양가, 임대료, 공사비, 금리, 분양속도, 할인율, Exit Cap Rate, 공실률, 공사기간, 책임준공기한.

---

## 2. System Invariants

아래 원칙은 모든 Agent, 모든 Track, 모든 API endpoint에 적용한다.

### 2.1 No Evidence, No Final Value

Evidence 또는 승인된 Assumption 없이 확정 숫자를 생성할 수 없다.

금지 예시:

- PNU 임의 생성
- 공시지가 임의 추정
- 등기부 권리관계 임의 판단
- 출처 없는 분양가 비교사례 생성
- 근거 없는 임대료, Cap Rate, 공사비 추정
- 증빙 없는 LTV, LTC, DSCR, ICR, Exit 분양률 확정
- 계산엔진 없이 사업수지, Cash Flow, Waterfall, Senior Shortfall 직접 산출

### 2.2 LLM은 판단 보조자이며 계산자가 아니다

LLM이 수행할 수 있는 일:

- 문서 분류
- 비정형 문서 필드 추출
- 숫자/문장별 source locator 생성 보조
- 누락자료 식별
- 가정 충돌 설명
- 리스크 논리 작성
- 보완자료 요청문 작성
- 심사보고서 문단 초안 작성
- Devil's Advocate 의견 작성
- 승인조건, Covenant, EOD Trigger 초안 작성

LLM이 수행하면 안 되는 일:

- 사업수지 직접 계산
- 월별 Cash Flow 직접 계산
- Waterfall 직접 계산
- LTV/LTC/DSCR/ICR 직접 산출
- Exit 분양률 직접 산출
- 미분양 LTV Matrix 직접 산출
- Senior Shortfall 면제
- 계산결과 임의 수정
- 출처 없는 수치로 심사의견 작성

### 2.3 Deterministic Engine First

모든 계산값은 deterministic engine에서 생성한다. LLM은 계산값을 설명하거나 오류 가능성을 지적할 수 있지만 계산값을 대체할 수 없다.

---

## 3. AI Brain Resolution Policy

사용자가 로그인하면 AI Brain은 아래 순서로 결정된다.

### 3.1 Resolution Order

1. API 요청의 명시적 `provider / model / apiKey` override
2. 사용자별 저장 AI Profile
3. Tenant 기본 AI Policy
4. System Free Fallback: Gemini 2.5 Flash
5. System key도 없으면 deterministic fallback

### 3.2 User AI Profile Schema

```ts
type UserAIProfile = {
  userId: string;
  tenantId: string;
  defaultProvider: "openai" | "anthropic" | "google" | "azure" | "gemini" | "local" | "none";
  defaultModel: string;
  encryptedApiKeyRef?: string;
  allowSystemFallback: boolean;
  allowFreeGeminiFallback: boolean;
  maxTokensPerRun?: number;
  monthlyTokenBudget?: number;
  piiMode: "allow" | "redact" | "block";
  dataRetentionMode: "standard" | "zero_retention" | "tenant_policy";
  createdAt: string;
  updatedAt: string;
};
```

### 3.3 API Key Security

- API Key는 서버에 암호화 저장한다.
- 응답에는 마스킹된 값만 반환한다.
- 운영 배포에서는 local encrypted key store를 Secret Manager로 교체한다.
- Agent Run Audit에는 원문 API Key를 절대 저장하지 않는다.
- 저장 가능한 값은 `provider`, `model`, `keyFingerprint`, `maskedKey`, `brainSource`까지만 허용한다.

### 3.4 aiBrainUsed Audit Event

모든 LLM-capable Agent 실행 시 아래 감사 이벤트를 저장한다.

```ts
type AIBrainUsedAudit = {
  runId: string;
  dealId: string;
  agentId: string;
  tenantId: string;
  userId: string;
  provider: string;
  model: string;
  brainSource: "request_override" | "user_profile" | "tenant_policy" | "system_fallback" | "deterministic_fallback";
  keyMode: "byok" | "system_key" | "no_key";
  promptHash: string;
  inputEvidenceIds: string[];
  outputFindingIds: string[];
  tokenUsage?: {
    inputTokens: number;
    outputTokens: number;
    totalTokens: number;
  };
  startedAt: string;
  completedAt: string;
  status: "success" | "failed" | "fallback_used";
};
```

---

## 4. Core Deal Room Data Model

### 4.1 Deal

```ts
type Deal = {
  dealId: string;
  tenantId: string;
  title: string;
  track: DealTrack;
  status: "intake" | "analysis" | "review" | "ic_ready" | "approved" | "rejected" | "monitoring";
  assetType?: AssetType;
  location?: LocationRef;
  borrower?: PartyRef;
  sponsor?: PartyRef;
  contractor?: PartyRef;
  trustee?: PartyRef;
  lenderGroup?: PartyRef[];
  createdBy: string;
  createdAt: string;
  updatedAt: string;
};
```

### 4.2 Deal Track

```ts
type DealTrack =
  | "real_estate_development"
  | "real_estate_pf"
  | "real_estate_finance"
  | "real_asset_finance"
  | "reit"
  | "real_estate_fund";
```

### 4.3 Evidence

```ts
type Evidence = {
  evidenceId: string;
  dealId: string;
  sourceType:
    | "uploaded_document"
    | "excel_model"
    | "api_result"
    | "manual_input"
    | "registry"
    | "market_report"
    | "appraisal"
    | "legal_opinion"
    | "internal_memo";
  sourceName: string;
  fileId?: string;
  page?: number;
  sheetName?: string;
  cellRange?: string;
  locatorText?: string;
  extractedValue?: string | number | boolean;
  unit?: string;
  baseDate?: string;
  confidence: number;
  createdByAgent?: string;
  verifiedByHuman?: boolean;
  createdAt: string;
};
```

### 4.4 Assumption

```ts
type Assumption = {
  assumptionId: string;
  dealId: string;
  category:
    | "sales_price"
    | "rent"
    | "construction_cost"
    | "land_cost"
    | "interest_rate"
    | "sales_velocity"
    | "vacancy"
    | "cap_rate"
    | "exit_value"
    | "schedule"
    | "ltv"
    | "ltc"
    | "other";
  name: string;
  value: number | string | boolean;
  unit?: string;
  baseDate?: string;
  sourceEvidenceIds: string[];
  ownerAgent: string;
  status: "draft" | "needs_review" | "approved" | "rejected" | "superseded";
  sensitivityRange?: {
    downside: number;
    base: number;
    upside: number;
  };
  createdAt: string;
  updatedAt: string;
};
```

### 4.5 Calculation Run

```ts
type CalculationRun = {
  calculationRunId: string;
  dealId: string;
  engine:
    | "feasibility_engine"
    | "cashflow_engine"
    | "pf_waterfall_engine"
    | "ltv_ltc_engine"
    | "dscr_icr_engine"
    | "exit_sales_rate_engine"
    | "reit_distribution_engine"
    | "fund_return_engine";
  inputEvidenceIds: string[];
  inputAssumptionIds: string[];
  outputMetricIds: string[];
  scenario: "base" | "downside" | "upside" | "stress" | "custom";
  version: string;
  status: "success" | "failed";
  errorMessage?: string;
  createdAt: string;
};
```

### 4.6 Metric

```ts
type Metric = {
  metricId: string;
  dealId: string;
  name:
    | "total_project_cost"
    | "gross_development_value"
    | "development_profit"
    | "profit_margin"
    | "monthly_cash_balance"
    | "max_minus"
    | "ltv"
    | "ltc"
    | "dscr"
    | "icr"
    | "exit_sales_rate"
    | "unsold_ltv"
    | "senior_shortfall"
    | "equity_irr"
    | "dividend_yield"
    | "cap_rate";
  value: number;
  unit: string;
  calculationRunId: string;
  evidenceIds: string[];
  assumptionIds: string[];
  status: "computed" | "stale" | "invalid";
  createdAt: string;
};
```

### 4.7 Risk Issue

```ts
type RiskIssue = {
  riskId: string;
  dealId: string;
  category:
    | "land"
    | "title"
    | "permit"
    | "market"
    | "sales"
    | "construction"
    | "cost_overrun"
    | "credit"
    | "legal"
    | "cashflow"
    | "waterfall"
    | "covenant"
    | "exit"
    | "compliance";
  severity: "low" | "medium" | "high" | "critical";
  title: string;
  description: string;
  evidenceIds: string[];
  assumptionIds: string[];
  metricIds: string[];
  proposedMitigation?: string;
  decisionImpact: "none" | "condition_required" | "approval_blocker";
  ownerAgent: string;
  status: "open" | "mitigated" | "waived_by_human" | "closed";
  createdAt: string;
};
```

---

## 5. Evidence Policy

### 5.1 Evidence Grade

| Grade | Source | Allowed Use |
|---|---|---|
| A | 등기부, 건축허가, 인허가 공문, 감정평가서, 법률의견서, 공식 API 원천데이터 | 확정값 |
| B | 사업성보고서, 대주단 심사보고서, 회계법인 검토자료, 제출자료 | 조건부 확정값 |
| C | 시장 리포트, 브로커 자료, 유사사례 | 비교·보조근거 |
| D | 사용자 수기입력, 미확정 엑셀, LLM 추출값 | 가정값 |
| E | 출처 없는 텍스트 설명만 있는 수치 | 확정 사용 금지 |

### 5.2 Source Locator Required Fields

숫자 evidence는 반드시 아래 중 하나 이상의 locator를 가져야 한다.

- 문서명 + 페이지
- 엑셀 파일명 + 시트명 + 셀 주소
- API endpoint + request id + response timestamp
- 수기입력자 + 입력일시 + 승인자
- 내부 DB table + row id + snapshot date

### 5.3 Evidence Conflict Rule

같은 항목에 복수 evidence가 충돌하면 우선순위는 다음과 같다.

1. 공적 원천자료
2. 법률의견서 / 감정평가서
3. 대주단 약정서 / Term Sheet
4. 최종 사업성보고서
5. 최신 엑셀 모델
6. 사용자 수기입력
7. LLM 추출값

충돌이 발견되면 Agent는 값을 임의 선택하지 않고 `conflict_issue`를 생성한다.

---

## 6. Agent Runtime Contract

모든 Agent endpoint는 아래 공통 계약을 따른다.

### 6.1 Common Agent Request

```ts
type AgentRunRequest = {
  dealId: string;
  agentId: string;
  track: DealTrack;
  requestedBy: string;
  aiOverride?: {
    provider?: string;
    model?: string;
    apiKey?: string;
  };
  inputEvidenceIds?: string[];
  inputAssumptionIds?: string[];
  inputMetricIds?: string[];
  runMode: "draft" | "review" | "final" | "monitoring";
};
```

### 6.2 Common Agent Response

```ts
type AgentRunResponse = {
  runId: string;
  dealId: string;
  agentId: string;
  aiBrainUsed: {
    provider: string;
    model: string;
    brainSource: string;
    keyMode: string;
  };
  createdEvidenceIds: string[];
  createdAssumptionIds: string[];
  createdMetricIds: string[];
  createdRiskIds: string[];
  createdReportBlockIds: string[];
  warnings: string[];
  blockers: string[];
  nextRecommendedAgents: string[];
  status: "success" | "needs_human_review" | "blocked" | "failed";
};
```

### 6.3 Agent Must Not

모든 Agent는 다음을 금지한다.

- evidence 없는 숫자 확정
- 계산엔진 우회
- 승인 가능성 단정
- 법률 확정 판단
- 담보가치 임의 평가
- 공시지가, PNU, 등기부 임의 생성
- LTV breach / Senior shortfall 임의 waiver
- Human Review 없이 최종 승인 문구 생성

---

## 7. Agent Role Map v2

| Agent / Pod | LLM Usage | Deterministic Engine | Reads | Writes | Guardrail |
|---|---:|---|---|---|---|
| ParserAgent | Required | None | Documents, Excel, PDFs | Evidence draft | locator 없는 숫자 확정 금지 |
| IntakeTriageAgent | Optional | Rule router | Deal intake | Track recommendation | 신뢰도는 라우팅 신뢰도, 승인 가능성 아님 |
| LandLegalPod | Assistive | Land API validator | PNU, 토지, 등기, 법규 | Land risk, missing docs | PNU·등기·공시지가 API/evidence 기반 |
| PermitRegulationPod | Assistive | Zoning rule engine | 법령, 조례, 지구단위 | Permit constraints | 인허가 가능성 단정 금지 |
| MarketProductPod | Recommended | Comparable filter engine | 거래사례, 분양사례, 임대료 | Market assumption | base date 없는 가격판단 금지 |
| FeasibilityCashflowPod | Extract/explain only | Feasibility & CF engine | 사업수지, Assumption | Metric explanation | 사업수지 직접 계산 금지 |
| ConstructionCostPod | Assistive | Cost benchmark engine | 도급계약, 견적, 공사비 DB | Cost risk | 평당공사비 출처 필수 |
| PFCreditPod | Review/draft | LTV/LTC/DSCR/Exit engine | Metrics, waterfall | Credit risk, covenant draft | 수치 리스크 임의 waiver 금지 |
| WaterfallPod | Explain only | Waterfall engine | CF, loan terms | Waterfall metrics | 상환순위 직접 변경 금지 |
| AIRiskCommittee | Recommended | Risk scoring engine | All graph | Challenge opinions | Case 수치는 graph 기준 |
| ReportStudio | Recommended | None | Evidence, metrics, risks | Report blocks | 문단마다 evidence/assumption 연결 |
| MonitoringAgent | Optional | Covenant monitor | Monthly data | Alert, EOD warning | 경보/중대위반 자동분류도 human review 필요 |

---

## 8. Deterministic Engine Registry

### 8.1 Feasibility Engine

Inputs:

- 토지비
- 공사비
- 간접비
- 금융비
- 판매관리비
- 예비비
- 분양수입
- 임대수입
- 매각가
- 세금
- PM Fee
- 신탁수수료

Outputs:

- 총사업비
- 총수입
- 개발이익
- 개발이익률
- 자기자본
- Equity 필요액
- 사업성 Score

### 8.2 Cash Flow Engine

Inputs:

- 월별 공정률
- 월별 공사비 지급률
- 분양대금 납입스케줄
- 대출 인출스케줄
- 이자 지급방법
- 수수료
- 운영계좌 지급순서

Outputs:

- 월별 현금증감
- 기초현금
- 기말현금
- Max Minus
- 부족자금 발생월
- 대출잔액
- 미지급공사비

### 8.3 PF Waterfall Engine

Inputs:

- 계약금 / 중도금 / 잔금 납입비율
- 분양률 시나리오
- 상환적립계좌 배분비율
- 운영계좌 배분비율
- Senior / Mezz / Junior 대출조건
- 상환순위
- Cash Sweep 조건
- 할인분양 Trigger

Outputs:

- Tranche별 상환 가능액
- Tranche별 부족액
- Senior shortfall
- Mezz shortfall
- Junior shortfall
- DSCR / ICR
- EOD Trigger 근접도

### 8.4 LTV / LTC Engine

Inputs:

- 대출금액
- 총사업비
- 감정평가액
- 선매각금액
- 총분양가
- 미분양 담보가치
- 할인율

Outputs:

- 분양가 LTV
- 감정가 LTV
- 선매각가 LTV
- LTC
- 미분양 LTV
- 할인매각 회수율

### 8.5 Exit Sales Rate Engine

Inputs:

- Tranche별 원리금
- 금융비
- 필수사업비
- 분양대금 납입비율
- 상환계좌 배분비율
- 할인분양률
- 수분양자 잔금납입률

Outputs:

- Senior Exit 분양률
- Mezz Exit 분양률
- Junior Exit 분양률
- Equity 회수 분양률
- 사업이익 분기 분양률

---

## 9. Shared Deal State Graph

### 9.1 Core Nodes

```ts
type DealGraphNode =
  | "Deal"
  | "Parcel"
  | "PNU"
  | "Owner"
  | "Encumbrance"
  | "Permit"
  | "Zoning"
  | "Product"
  | "Comparable"
  | "Assumption"
  | "Evidence"
  | "Metric"
  | "LoanTranche"
  | "Waterfall"
  | "Covenant"
  | "RiskIssue"
  | "DecisionGate"
  | "ReportBlock"
  | "MonitoringEvent";
```

### 9.2 Core Edges

```ts
type DealGraphEdge =
  | "SUPPORTED_BY"
  | "DERIVED_FROM"
  | "CONFLICTS_WITH"
  | "CALCULATED_BY"
  | "DEPENDS_ON"
  | "MITIGATED_BY"
  | "BLOCKS"
  | "REQUIRES_REVIEW"
  | "USED_IN_REPORT"
  | "SUPERSEDES";
```

### 9.3 Example

```json
{
  "node": "Metric:exit_sales_rate_mezz",
  "value": "66.96%",
  "calculatedBy": "exit_sales_rate_engine:v1.2",
  "dependsOn": ["Assumption:sales_schedule_base", "LoanTranche:TrB", "Waterfall:base"],
  "supportedBy": ["Evidence:pf_termsheet_page_3", "Evidence:cashflow_excel_sheet_cf"],
  "usedInReport": ["ReportBlock:pf_credit_summary"]
}
```

---

## 10. Track Workflow v2

각 Track은 Agent를 직렬 실행하지 않는다. Track은 여러 Agent가 같은 Deal State를 읽고 쓰는 **parallel + gated workflow**로 운영한다.

### 10.1 Real Estate Development Track

Decision Question: 이 토지와 계획으로 개발사업을 착수할 수 있는가?

| Gate | Question | Pass Criteria | Stop Condition |
|---|---|---|---|
| G1 Land Identity | 토지와 PNU가 확정되었는가 | PNU/API/등기 evidence 확보 | PNU 불명확 |
| G2 Title & Control | 권원 확보 가능한가 | 매매계약, 소유자, 제한물권 확인 | 필수 필지 미확보 |
| G3 Regulation | 계획 규모가 법규상 가능한가 | 용도지역, 용적률, 건폐율, 주차, 높이 검토 | 법정 규모 초과 |
| G4 Product | 적정 상품인가 | 시장성, 상품구성, 가격 가정 evidence | 수요 근거 없음 |
| G5 Feasibility | 개발이익이 충분한가 | 사업수지 engine 산출 | 손익분기 미달 |
| G6 Cash Flow | 자금 부족 통제 가능한가 | Max Minus, 월별 CF 산출 | 조달 불가 shortfall |
| G7 Human Review | 전문가 검토 완료 | 승인/보류/중단 | 필수 evidence 누락 |

Required Agents:

- ParserAgent
- LandLegalPod
- PermitRegulationPod
- MarketProductPod
- FeasibilityCashflowPod
- ConstructionCostPod
- AIRiskCommittee
- ReportStudio

### 10.2 Real Estate PF Track

Decision Question: 이 PF는 어떤 조건을 붙이면 승인 가능한가, 아니면 부결해야 하는가?

| Gate | Question | Pass Criteria | Stop Condition |
|---|---|---|---|
| G1 Deal Completeness | 심사자료가 충분한가 | 사업개요, 수지, CF, 금융조건, 담보자료 확보 | 핵심자료 누락 |
| G2 Land & Permit CP | 인출선행조건 충족 가능한가 | 토지, 인허가, 신탁, 법률의견 evidence | 토지/인허가 불확정 |
| G3 Sponsor & Contractor | 시행사·시공사 리스크 통제 가능한가 | 신용등급, 재무, 책임준공, 채무인수 | 책임준공 미흡 |
| G4 Market & Exit | 분양/임대/매각 Exit 가능한가 | Exit 분양률, 미분양 LTV, 비교사례 | Exit 불가능 |
| G5 Waterfall | 상환순위와 계좌통제가 충분한가 | Waterfall engine 결과 | Senior shortfall |
| G6 Covenants | 약정조건이 충분한가 | DSCR/LTV/EOD/할인분양 trigger | Covenant 부족 |
| G7 IC Decision | 심사의견 작성 가능한가 | Risk register, 조건부 승인안 | Critical risk open |

Required Agents:

- ParserAgent
- IntakeTriageAgent
- LandLegalPod
- MarketProductPod
- FeasibilityCashflowPod
- PFCreditPod
- WaterfallPod
- AIRiskCommittee
- ReportStudio

### 10.3 Real Estate Finance Track

Decision Question: 담보와 차주 현금흐름으로 대출 원리금을 회수할 수 있는가?

| Gate | Question | Pass Criteria |
|---|---|---|
| G1 Collateral | 담보 패키지가 충분한가 | 감정평가, 담보권, 선순위 권리 확인 |
| G2 Borrower Credit | 차주 신용이 충분한가 | 재무제표, 현금흐름, 부채비율 |
| G3 Repayment Source | 상환재원이 명확한가 | 영업현금흐름, 매각, 리파이낸싱 |
| G4 Loan Structure | 대출구조가 리스크에 맞는가 | 만기, 금리, 상환, Covenant |
| G5 Monitoring | 사후관리 가능한가 | 보고자료, 계좌통제, Trigger |

### 10.4 Real Asset Finance Track

Decision Question: 운영수익과 담보가치가 인수금융 또는 실물금융을 지탱하는가?

| Gate | Question | Pass Criteria |
|---|---|---|
| G1 Asset Due Diligence | 자산 실사가 충분한가 | 권리, 물리, 환경, 보험 확인 |
| G2 NOI Quality | NOI가 안정적인가 | 임대차, 공실률, 비용, 임차인 신용 |
| G3 Valuation | Cap Rate와 가치가 적정한가 | 감정평가, 거래사례, 시장 Cap Rate |
| G4 Leverage | LTV/DSCR가 적정한가 | 담보가치와 현금흐름 기준 |
| G5 Exit | 매각 또는 리파이낸싱 가능한가 | 매수자 pool, 시장성, 금리 민감도 |

### 10.5 REIT Track

Decision Question: 편입자산과 운용구조가 리츠 투자자 보호와 배당 안정성을 충족하는가?

| Gate | Question | Pass Criteria |
|---|---|---|
| G1 Asset Eligibility | 리츠 편입 가능한 자산인가 | 법적 적격성, 권리관계 |
| G2 Lease & NOI | 배당재원이 안정적인가 | 임대차, NOI, 비용구조 |
| G3 Distribution | 배당 가능성이 충분한가 | 배당수익률, DSCR, 유보금 |
| G4 Regulation | 리츠 규제 리스크는 없는가 | 공시, 자산운용, 이해상충 |
| G5 Monitoring | 배당·공시 모니터링 가능한가 | 월별 NOI, 임대차 변동, LTV |

### 10.6 Real Estate Fund Track

Decision Question: 펀드 투자구조와 회수계획이 투자자 보호와 목표수익률을 충족하는가?

| Gate | Question | Pass Criteria |
|---|---|---|
| G1 Fund Terms | 펀드 조건이 명확한가 | 만기, 보수, 우선수익, 의사결정 |
| G2 Asset Strategy | 투자대상과 전략이 적정한가 | 개발형, 코어, 밸류애드, 대출형 |
| G3 Return Model | 목표수익률 산정이 타당한가 | IRR, Equity Multiple, 배당 |
| G4 Investor Protection | 투자자 보호장치가 충분한가 | Key man, LPAC, 제한사항 |
| G5 Exit Plan | 회수계획이 실현 가능한가 | 매각, 리파이낸싱, 배당, IPO |

---

## 11. AI Risk Committee

AIRiskCommittee는 하나의 Agent가 아니라 여러 Persona가 동일 Deal State를 읽고 독립 의견을 생성하는 구조다.

### 11.1 Personas

1. **Devil's Advocate**  
   승인 논리를 반대하는 관점에서 숨은 리스크를 찾는다.

2. **Credit Reviewer**  
   대주 원금보존 관점에서 LTV, LTC, DSCR, Exit, Shortfall을 검토한다.

3. **Legal / Compliance Reviewer**  
   인허가, 권리관계, 신탁, 약정, 규제 이슈를 평가한다.

4. **Market Stress Reviewer**  
   분양률, 임대료, Cap Rate, 매각가, 금리 민감도를 검토한다.

5. **Construction Risk Reviewer**  
   공사비, 공정률, 책임준공, 시공사 신용, 기성지급 구조를 검토한다.

6. **Lender Persona**  
   선순위, 중순위, 후순위, Equity 투자자의 관점별 우려사항을 작성한다.

7. **IC Judge**  
   승인, 조건부 승인, 보류, 부결 중 하나의 초안을 제시한다.

### 11.2 Output Schema

```ts
type AIRiskCommitteeOpinion = {
  dealId: string;
  persona: string;
  conclusion: "support" | "conditional_support" | "hold" | "reject";
  keyConcerns: string[];
  requiredConditions: string[];
  evidenceIds: string[];
  metricIds: string[];
  assumptionIds: string[];
  cannotConcludeBecause?: string[];
};
```

---

## 12. Report Studio Policy

ReportStudio는 문장을 생성하지만, 보고서 문단은 반드시 Evidence 또는 Assumption에 연결되어야 한다.

### 12.1 Report Block Schema

```ts
type ReportBlock = {
  reportBlockId: string;
  dealId: string;
  reportType:
    | "land_review"
    | "feasibility_report"
    | "pf_credit_memo"
    | "ic_memo"
    | "term_sheet"
    | "monitoring_report";
  section:
    | "executive_summary"
    | "deal_overview"
    | "land_title"
    | "permit"
    | "market"
    | "feasibility"
    | "cashflow"
    | "pf_structure"
    | "waterfall"
    | "risk"
    | "approval_conditions";
  text: string;
  evidenceIds: string[];
  assumptionIds: string[];
  metricIds: string[];
  riskIds: string[];
  generatedBy: string;
  humanEdited: boolean;
  status: "draft" | "reviewed" | "approved";
};
```

### 12.2 Report Guardrail

ReportStudio는 아래 표현을 금지한다.

- "문제 없음"
- "리스크 없음"
- "확실히 승인 가능"
- "항상 가능"
- "법적으로 문제 없음"
- "분양 가능성이 높음" 같은 단독 확정 표현

대신 아래 형식을 사용한다.

- "확인된 정보와 Evidence 기준으로는 ..."
- "판단, 계약 자료가 미제출되어 Human Review가 필요함"
- "Base Case에서는 상환 가능하나 Downside Case에서는 특정 Shortfall 발생"
- "승인의 최소 조건으로 특정 Covenant 설정 필요"

---

## 13. Human Review Policy

AI는 최종 승인권자가 아니다. 아래 경우 반드시 Human Review가 필요하다.

1. Critical Risk 존재
2. Evidence Grade D 이하 자료가 핵심 계산에 사용된 경우
3. LTV / LTC / DSCR / Exit 분양률이 tenant threshold를 위반한 경우
4. 등기·인허가·법률의견서 미제출
5. 시공사 책임준공 또는 채무인수 조건 미확정
6. Waterfall에서 Senior shortfall 발생
7. 주요 Assumption 값 충돌 발생
8. ReportStudio가 `cannot conclude`를 반환한 경우

---

## 14. Tenant-Level AI Policy

Tenant는 아래 정책을 설정할 수 있어야 한다.

```ts
type TenantAIPolicy = {
  tenantId: string;
  allowedProviders: string[];
  defaultProvider: string;
  defaultModel: string;
  allowBYOK: boolean;
  allowSystemFallback: boolean;
  allowFreeGeminiFallback: boolean;
  requireZeroRetention: boolean;
  allowPIIToLLM: boolean;
  maxTokenBudgetPerDeal: number;
  maxTokenBudgetPerMonth: number;
  requiredHumanReviewForFinalReport: boolean;
  restrictedTracks?: DealTrack[];
  approvalThresholds: {
    maxLTV?: number;
    maxLTC?: number;
    minDSCR?: number;
    maxUnsoldLTV?: number;
    minExitSalesRateBuffer?: number;
  };
};
```

---

## 15. Fallback Policy

### 15.1 BYOK Failure

사용자 API Key가 실패하면 아래 순서로 처리한다.

1. 동일 provider retry
2. 사용자 profile의 backup model 사용
3. tenant policy가 허용한 system key 사용
4. Gemini 2.5 Flash free fallback
5. deterministic fallback

### 15.2 Deterministic Fallback Mode

LLM을 전혀 사용할 수 없는 경우에도 아래 기능은 작동해야 한다.

- Rule-based track routing
- Evidence 목록 표시
- 필수자료 체크리스트 생성
- 사업수지 계산
- Cash Flow 계산
- LTV/LTC/DSCR/ICR 계산
- Waterfall 계산
- Covenant threshold breach 표시
- Human Review 요청

아래 기능은 제한된다.

- 비정형 문서 필드 추출
- 자연어 심사의견 생성
- Devil's Advocate 의견
- 보고서 문단 초안
- 보완자료 요청문 자동 작성

---

## 16. Implementation Priority

### Phase 1 - Policy to Runtime

- Agent endpoint별 AI Brain resolution 적용
- aiBrainUsed audit event 저장
- Evidence / Assumption / Metric / RiskIssue schema 구현
- ParserAgent locator validation
- deterministic calculation registry 생성

### Phase 2 - Deal Room UI

- Chief Deal Room에 Agent AI Role 표시
- Track별 module menu와 Agent 상태 연결
- Evidence Vault UI
- Assumption Book UI
- Calculation Run History UI
- Risk Register UI
- AI Brain Used Audit UI

### Phase 3 - Risk & Report

- AIRiskCommittee persona 실행
- ReportStudio evidence-linked paragraph 생성
- IC Memo / PF 심사보고서 자동 초안
- Term Sheet 초안
- Covenant / EOD Trigger 초안

### Phase 4 - Production Security

- Secret Manager 전환
- tenant-level AI policy control
- key rotation
- prompt / output audit
- PII redaction
- role-based access control
- SOC2-ready audit trail

---

## 17. Definition of Done

이 정책이 구현되었다고 보려면 아래 조건을 만족해야 한다.

1. 모든 LLM-capable Agent가 user AI profile 또는 fallback policy를 사용한다.
2. 모든 Agent Run에 aiBrainUsed audit event가 저장된다.
3. 모든 숫자 결과가 Evidence 또는 Assumption에 연결된다.
4. 모든 계산값이 CalculationRun과 deterministic engine version을 가진다.
5. ReportStudio 문단이 Evidence / Assumption / Metric / RiskIssue에 연결된다.
6. Human Review 없이 최종 승인 문구가 생성되지 않는다.
7. Track별 Decision Gate가 UI에서 확인된다.
8. Critical Risk가 있으면 IC Memo가 자동으로 조건부 승인 또는 보류 상태로 표시된다.
9. BYOK 실패 시 fallback 경로가 audit에 남는다.
10. Secret Manager 전환 전까지 운영 배포 금지 상태가 명시된다.

---

## 18. Current Implementation Mapping

Implemented:

- User AI profile storage/API
- User BYOK -> system Gemini fallback resolution
- Central `AIConnection` aiBrainUsed audit event hook
- `ai_brain_used` user activity logs with provider, model, brainSource, keyMode, promptHash, tokenUsage, status, redacted aiContext, and run timestamps
- OpenAI-compatible and Anthropic token usage normalization
- `/audit` and `/project/{id}/audit` AI Brain Used Audit panel
- ParserAgent / IntakeTriageAgent / FeasibilityWorkbookLLMAgent project and agent context propagation
- Track workflow definitions in frontend and backend
- Chief task graph includes `aiAgentRoles`
- Sidebar exposes track-specific module menus and AI settings
- Policy document v2 created

In Progress:

- Pod AI Reviewer layer. See `docs/product/pod-ai-reviewer-roadmap.md`.
- Evidence / Assumption / Metric / RiskIssue first-class persistence
- CalculationRun registry
- Tenant-level AI Policy
- Chief Deal Room AI Brain Used Audit embedding

Planned:

- Production Secret Manager
- PII redaction before LLM call
- Tenant token budget controls
- Zero-retention provider policy
- SOC2-ready prompt/output audit trail
