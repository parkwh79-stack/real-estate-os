# Cash Flow Engine / Cash Flow Agent Roadmap

Updated: 2026-05-18  
Project: REALYTICS AI LAB  
Module Code: P1-Calculation-CashFlow-Scenario-Engine  
Agent Name: Cash Flow Engine / Cash Flow Agent  

## 1. Purpose

The Cash Flow Engine is the central numerical engine connecting REALYTICS feasibility intelligence with PLATFORM9 PF review intelligence.

It is not just a monthly cash-flow calculator. It should connect:

```text
Business feasibility
    -> Monthly cash flow
    -> Max Minus
    -> PF draw schedule
    -> Sales collection schedule
    -> Operating / repayment account allocation
    -> Repayment waterfall
    -> Exit sale rate
    -> Unsold LTV matrix
    -> Tranche recovery
    -> Risk flags
    -> PF review report fragments
```

The engine should remain deterministic, auditable, and source-metadata aware.

## 2. Position in REALYTICS and PLATFORM9

```text
REALYTICS
  Land & Feasibility Intelligence
    -> Land / PNU / regulation analysis
    -> product mix and feasibility study
    -> cost and revenue schedule
    -> Cash Flow Engine

PLATFORM9
  Finance & Credit Review Intelligence
    -> PF tranche structuring
    -> waterfall and cash sweep
    -> unsold LTV and tranche recovery
    -> covenant / EOD trigger checks
    -> credit review report
```

The Cash Flow Engine sits between the two systems. It converts feasibility assumptions into lender-facing recovery and credit outputs.

## 3. Schema Compatibility Review

The proposed Cash Flow schema is broadly compatible with the current codebase, but it should be layered rather than merged into one object.

### 3.1 Existing Schema Fit

| Existing Object | Current Location | Fit |
|---|---|---|
| `FeasibilityStudyV2` | `services/api-server/schemas/feasibility_study.py` | Source schema for site, program, product mix, revenue, cost, financing, schedule, scenarios, workbook mapping, and provenance |
| `build_monthly_cash_flow` | `services/api-server/core/calculations.py` | Simple monthly cash-flow builder; should become a lower-level function |
| `calculate_cashflow_max_minus_details` | `services/api-server/core/calculations.py` | Max Minus basis; should feed cash-flow result summary |
| `simulate_waterfall` | `services/api-server/core/calculations.py` | Current simple waterfall function; should be wrapped by scenario schema |
| `calculate_unsold_ltv` | `services/api-server/core/calculations.py` | Single-point LTV function; should be wrapped into matrix generation |
| `calculate_tranche_recovery` | `services/api-server/core/calculations.py` | Recovery helper; should feed tranche recovery matrix |
| `MarketabilityAgent.unsoldRecovery` | `services/api-server/agents/marketability_agent.py` | Current market-facing approximation; should later consume Cash Flow Scenario results |
| `Calculation Scenario Engine` | `docs/product/calculation-scenario-engine.md` | Shared scenario layer for waterfall, unsold LTV, and tranche recovery |

### 3.2 Recommended Schema Split

Do not force all data into one monolithic Cash Flow object.

Recommended layers:

| Layer | Role |
|---|---|
| `FeasibilityStudyV2` | Canonical feasibility and workbook import/export source |
| `CashFlowRunRequest` | Execution payload for one run |
| `CashFlowScenarioSet` | Best/base/downside/stress/recovery scenario assumptions |
| `MonthlyCashFlowResult` | Monthly row outputs and Max Minus |
| `ScenarioRecoveryResult` | Waterfall, Exit sale rate, unsold LTV, tranche recovery |
| `ReportFragments` | PF review report-ready text |
| `SourceMetadata` | Manual/API/document source traceability |

This split keeps the codebase easier to test and prevents UI/API payloads from becoming brittle.

## 4. Current Implementation

Current implementation exists at the pure-function level.

| Capability | Status |
|---|---|
| Feasibility metrics | Implemented |
| Basic monthly cash flow | Implemented |
| Max Minus | Implemented |
| Basic PF metrics and Exit sale rate | Implemented |
| Simple waterfall | Implemented as pure function |
| Single-point unsold LTV | Implemented as pure function |
| Tranche recovery helper | Implemented as pure function |
| Full Cash Flow Agent schema | Not implemented |
| PF draw schedule | Not implemented |
| Sales collection schedule by product | Not implemented |
| Operating / repayment account allocation | Not implemented |
| Cash Flow API workflow | Not implemented |
| Cash Flow Review UI | Not implemented |
| Report fragment generator | Not implemented |

## 5. Development Goal

The Cash Flow Agent should produce the following output:

```json
{
  "analysisType": "realytics_cash_flow_engine_v1",
  "projectId": "project-id",
  "status": "completed",
  "dataStatus": "complete",
  "summary": {
    "totalRevenue": 0,
    "totalProjectCost": 0,
    "developmentProfit": 0,
    "profitMargin": 0,
    "pfAmount": 0,
    "maxMinus": 0,
    "requiredPfLimit": 0,
    "baseExitSalesRate": 0,
    "seniorRecoveryRatio": 0,
    "mezzanineRecoveryRatio": 0,
    "juniorRecoveryRatio": 0
  },
  "monthlyCashFlow": [],
  "pfDrawSchedule": [],
  "salesCollectionSchedule": [],
  "waterfallResult": {},
  "exitSalesRate": [],
  "unsoldLtvMatrix": {},
  "riskFlags": [],
  "approvalConditions": [],
  "reportFragments": {},
  "sourceMetadata": {}
}
```

## 6. Canonical Input Schema

### 6.1 Project Input

```json
{
  "project": {
    "projectId": "project-id",
    "projectName": "Project Name",
    "location": "Address or PNU",
    "pnu": "optional-pnu",
    "landAreaSqm": 0,
    "grossFloorAreaSqm": 0,
    "useDistrict": "Zoning / district",
    "buildingUse": "mixed_use_residential",
    "developer": "Developer",
    "contractor": "Contractor",
    "trustee": "Trust company",
    "lenderGroup": ["Lender A"],
    "constructionStartDate": "2026-06",
    "completionDate": "2029-06",
    "loanMaturityDate": "2030-07"
  }
}
```

### 6.2 Sources and Uses

```json
{
  "sourcesAndUses": {
    "revenue": {
      "totalSalesRevenue": 0,
      "residentialSales": 0,
      "officetelSales": 0,
      "retailSales": 0,
      "logisticsSalePrice": 0,
      "leaseDeposit": 0,
      "refinancingProceeds": 0
    },
    "cost": {
      "landCost": 0,
      "landAcquisitionTax": 0,
      "directConstructionCost": 0,
      "indirectConstructionCost": 0,
      "designFee": 0,
      "supervisionFee": 0,
      "salesMarketingCost": 0,
      "trustFee": 0,
      "developerOverhead": 0,
      "taxAndDues": 0,
      "financialCost": 0,
      "contingency": 0,
      "pfRepayment": 0,
      "equityReturn": 0,
      "developerProfit": 0,
      "otherCost": 0
    },
    "funding": {
      "equity": 0,
      "bridgeLoan": 0,
      "pfLoan": 0,
      "salesRevenue": 0,
      "forwardSaleProceeds": 0,
      "refinancingLoan": 0
    }
  }
}
```

### 6.3 Product Mix

Use a product mix shape that can map from both `FeasibilityStudyV2.product_mix` and Marketability Agent product mix.

```json
{
  "productMix": [
    {
      "productId": "apt-001",
      "type": "apartment",
      "units": 235,
      "exclusiveAreaPy": 0,
      "contractAreaPy": 0,
      "avgUnitPrice": 0,
      "pricePerPy": 0,
      "totalSalesRevenue": 0,
      "vatIncluded": false,
      "recoveryHaircut": 0.1
    }
  ]
}
```

Recommended product type codes:

| Code | Use |
|---|---|
| `apartment` | Apartment presale |
| `officetel_residential` | Residential officetel |
| `officetel_office` | Office officetel |
| `serviced_residence` | Presale serviced residence |
| `commercial_retail` | Retail / neighborhood facility |
| `knowledge_industry_center` | Knowledge industry center |
| `section_office` | Section office |
| `logistics` | Logistics center |
| `other_saleable` | Other strata-sale asset |

### 6.4 Sales Collection Schedule

```json
{
  "salesCollectionSchedule": {
    "apartment": [
      {"label": "contract", "month": 0, "collectionRate": 0.1},
      {"label": "interim_1", "month": 4, "collectionRate": 0.1},
      {"label": "interim_2", "month": 8, "collectionRate": 0.1},
      {"label": "interim_3", "month": 12, "collectionRate": 0.1},
      {"label": "interim_4", "month": 16, "collectionRate": 0.1},
      {"label": "interim_5", "month": 20, "collectionRate": 0.1},
      {"label": "interim_6", "month": 24, "collectionRate": 0.1},
      {"label": "balance", "month": 36, "collectionRate": 0.3}
    ]
  }
}
```

Default schedules:

| Product | Default Schedule |
|---|---|
| Apartment / officetel | Contract 10%, six interim payments of 10%, balance 30% |
| Serviced residence | Contract 10%, five interim payments of 10%, balance 40% |
| Retail | Contract 10-20%, four or five interim payments, balance 40% |
| Logistics | Use lease, forward sale, or refinancing schedule instead of presale collection |

### 6.5 Sales Rate Scenario

```json
{
  "salesRateScenario": {
    "scenarioName": "base_case",
    "monthlySalesRates": [
      {"month": 0, "apartment": 0.1, "officetel_residential": 0.1, "commercial_retail": 0.05},
      {"month": 6, "apartment": 0.4, "officetel_residential": 0.35, "commercial_retail": 0.2},
      {"month": 12, "apartment": 0.6, "officetel_residential": 0.55, "commercial_retail": 0.3},
      {"month": 24, "apartment": 0.75, "officetel_residential": 0.7, "commercial_retail": 0.5}
    ]
  }
}
```

Default scenario set:

| Scenario | Meaning |
|---|---|
| `best_case` | Strong initial sales, no discount |
| `base_case` | Credit review base case |
| `downside_case` | Delayed sales |
| `stress_case` | Target sales miss and discount sale |
| `recovery_case` | Completed-unit collateral and unsold recovery |

### 6.6 PF Tranche Structure

```json
{
  "pfStructure": [
    {
      "tranche": "Tr.A",
      "name": "senior",
      "amount": 63000000000,
      "drawType": "bullet_plus_limit",
      "initialDrawAmount": 19500000000,
      "limitAmount": 43500000000,
      "interestRate": 0.045,
      "feeRate": 0.015,
      "repaymentPriority": 1,
      "drawPriority": 2,
      "interestPaymentType": "quarterly_in_advance",
      "maturityMonths": 49
    }
  ]
}
```

Important rule:

- Draw priority and repayment priority may be different.
- The engine must not assume senior tranche is drawn first.
- The engine must not assume all PF amount is available immediately.

### 6.7 Collection Allocation Rule

```json
{
  "collectionAllocationRule": [
    {
      "condition": "before_completion_and_sales_rate_lte_40",
      "contractAndInterimToRepayment": 0.25,
      "contractAndInterimToOperating": 0.75,
      "balanceToRepayment": 1.0,
      "balanceToOperating": 0.0
    },
    {
      "condition": "before_completion_and_sales_rate_gt_40",
      "contractAndInterimToRepayment": 0.9,
      "contractAndInterimToOperating": 0.1,
      "balanceToRepayment": 1.0,
      "balanceToOperating": 0.0
    },
    {
      "condition": "after_completion",
      "allSalesProceedsToRepayment": 1.0
    }
  ]
}
```

### 6.8 Operating Account Waterfall

```json
{
  "operatingAccountWaterfall": [
    {"rank": 1, "item": "taxes_and_public_charges"},
    {"rank": 2, "item": "trust_fee_and_trust_expenses"},
    {"rank": 3, "item": "loan_interest_fees_and_financial_cost"},
    {"rank": 4, "item": "essential_project_cost"},
    {"rank": 5, "item": "construction_cost_90_percent"},
    {"rank": 6, "item": "due_principal_repayment"},
    {"rank": 7, "item": "remaining_construction_cost_10_percent"},
    {"rank": 8, "item": "other_lender_approved_cost"},
    {"rank": 9, "item": "trust_profit_distribution"}
  ]
}
```

### 6.9 Repayment Waterfall Rule

```json
{
  "repaymentWaterfallRule": {
    "priorityOrder": [
      "tax",
      "trust_expense",
      "senior_interest",
      "senior_fee",
      "senior_principal",
      "mezzanine_interest",
      "mezzanine_fee",
      "mezzanine_principal",
      "junior_interest",
      "junior_fee",
      "junior_principal",
      "construction_cost_retention",
      "equity_return",
      "developer_profit"
    ],
    "includeAccruedInterest": true,
    "includeFees": true,
    "includeDefaultInterest": true
  }
}
```

### 6.10 Completion Support

```json
{
  "completionSupport": {
    "contractor": "Contractor",
    "responsibleCompletionDeadlineMonths": 46,
    "debtAssumptionOnFailure": true,
    "trustCompanyCompletionSupport": true,
    "performanceBondRate": 0.15,
    "constructionCostRetentionRate": 0.1
  }
}
```

### 6.11 Logistics Project Mode

```json
{
  "logisticsProject": {
    "leaseType": "master_lease_or_responsible_lease",
    "tenant": "Tenant",
    "leaseAreaPy": 0,
    "rentPerPy": 0,
    "annualRent": 0,
    "leaseTermYears": 0,
    "forwardSaleBuyer": "Forward sale buyer",
    "forwardSalePrice": 0,
    "capRate": 0,
    "closingDate": "YYYY-MM",
    "refinancingLoan": 0
  }
}
```

## 7. Monthly Cash Flow Output

```json
{
  "monthlyCashFlow": [
    {
      "monthIndex": 0,
      "period": "2026-06",
      "cashIn": {
        "equity": 0,
        "bridgeLoan": 0,
        "pfDraw": {"Tr.A": 0, "Tr.B": 0, "Tr.C": 0},
        "salesCollection": {"contract": 0, "interim": 0, "balance": 0},
        "leaseIncome": 0,
        "saleProceeds": 0,
        "refinancingProceeds": 0
      },
      "cashOut": {
        "landCost": 0,
        "constructionCost": 0,
        "indirectCost": 0,
        "salesMarketingCost": 0,
        "taxAndDues": 0,
        "financialCost": 0,
        "loanRepayment": {"Tr.A": 0, "Tr.B": 0, "Tr.C": 0},
        "equityDistribution": 0
      },
      "accounts": {
        "operatingAccountInflow": 0,
        "repaymentReserveAccountInflow": 0,
        "operatingAccountOutflow": 0,
        "repaymentReserveAccountOutflow": 0
      },
      "netCashFlow": 0,
      "beginningCash": 0,
      "endingCash": 0,
      "cumulativeCashFlow": 0,
      "maxMinus": 0
    }
  ]
}
```

## 8. Core Calculations

### 8.1 Max Minus

```text
Max Minus = minimum cumulative cash flow across project period
Recommended PF limit = abs(Max Minus) + finance cost buffer + contingency + DSRA
```

### 8.2 Exit Sale Rate

```text
Tranche Exit Sale Rate =
Required repayment amount through the tranche / total sales revenue
```

Required repayment amount should include:

- principal
- accrued interest
- fees
- senior priority costs
- repayment reserve adjustment

### 8.3 Unsold LTV

```text
Unsold LTV =
Outstanding PF amount / discounted unsold recovery value
```

Risk bands:

| Unsold LTV | Risk Level |
|---:|---|
| below 75% | low |
| 75% to 85% | moderate |
| 85% to 90% | high |
| above 90% | breach |

### 8.4 Discount Sale Trigger

Default trigger table:

| Month | Target Sales Rate | Discount Action |
|---:|---:|---|
| 6 | 40% | lender consultation / up to 5% discount |
| 12 | 60% | discount sale up to 10% |
| 24 | 75% | mandatory discount and cash sweep |
| completion | 80% | completed-unit recovery or bulk sale review |

## 9. Risk Flags

The engine should generate at least the following risk flags.

| Code | Condition |
|---|---|
| `MAX_MINUS_EXCEEDS_PF_LIMIT` | Max Minus plus buffers exceeds approved PF limit |
| `WATERFALL_SHORTFALL` | Available repayment cash is below total claims |
| `SENIOR_SHORTFALL` | Senior tranche recovery is below 100% |
| `MEZZANINE_SHORTFALL` | Mezzanine tranche recovery is below 100% |
| `JUNIOR_SHORTFALL` | Junior tranche recovery is below 100% |
| `UNSOLD_LTV_HIGH` | Unsold LTV is above 85% |
| `UNSOLD_LTV_BREACH` | Unsold LTV is above 90% |
| `DISCOUNT_STRESS_FAIL` | 10% or higher discount scenario fails required repayment |
| `TRIGGER_REQUIRED` | Sale-rate, discount, or cash sweep trigger is needed |
| `CONSTRUCTION_COST_SHORTFALL` | Construction cost remains unpaid at balance or maturity date |
| `EQUITY_NOT_RECOVERED` | Developer equity is not recovered in base case |
| `COMPLETION_DELAY_RISK` | Completion support or deadline mismatch increases repayment risk |
| `DRAWDOWN_CONDITION_NOT_MET` | Required drawdown conditions are incomplete |
| `FORWARD_SALE_CLOSING_RISK` | Logistics forward sale conditions are not certain |
| `LEASE_CREDIT_RISK` | Logistics tenant or master lease credit support is weak |

## 10. Report Fragments

The Agent should generate report-ready Korean PF review text, but the schema keys should remain English.

Required fragments:

| Key | Purpose |
|---|---|
| `cashFlowSummary` | Monthly cash flow and Max Minus summary |
| `pfStructureSummary` | PF tranche, draw, interest, and fee summary |
| `exitSalesRateSummary` | Tranche-level exit sale rate summary |
| `unsoldLtvSummary` | Unsold LTV and recovery risk summary |
| `waterfallSummary` | Repayment waterfall and shortfall summary |
| `riskOpinion` | Lender-side risk opinion |
| `approvalCondition` | Approval conditions and covenant triggers |
| `covenantOrEodTriggerDraft` | Draft covenant / EOD trigger text |

## 11. API Design

Required endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /projects/{project_id}/cash-flow/run` | Run one cash-flow analysis |
| `POST /projects/{project_id}/cash-flow/scenarios` | Run best/base/downside/stress/recovery cases |
| `POST /projects/{project_id}/cash-flow/waterfall` | Run repayment waterfall |
| `POST /projects/{project_id}/cash-flow/unsold-ltv` | Generate unsold LTV matrix |
| `GET /projects/{project_id}/cash-flow/latest` | Get latest saved cash-flow result |
| `POST /projects/{project_id}/cash-flow/report-fragments` | Generate report-ready text fragments |

## 12. UI Design

Cash Flow Review UI should include:

| Panel | Description |
|---|---|
| Project Summary | Project, land, program, schedule, developer, contractor, trustee |
| Sources and Uses | Cash-in and cash-out by category |
| Monthly Cash Flow | Monthly cash-in/out, net, beginning cash, ending cash, cumulative cash, Max Minus |
| PF Draw Schedule | Tranche draw, undrawn limit, interest, fees |
| Sales Collection | Contract, interim, balance collection by product and sales rate |
| Account Allocation | Operating account and repayment reserve account split |
| Waterfall | Tranche repayment, shortfall, recovery ratio |
| Unsold LTV Matrix | Heatmap by sale rate and discount rate |
| Risk Flags | Key lender-side risk flags |
| Report Fragment Preview | Copy-ready report text |

## 13. Testing Requirements

Required tests:

1. Development project with no PF loan
2. Single senior tranche PF
3. Senior / mezzanine / junior tranche structure
4. Delayed sales rate increases Max Minus
5. Balance collection repays PF principal
6. Post-completion sales proceeds sweep 100% to repayment account
7. 10%, 15%, and 20% discount sale scenarios
8. Unsold LTV high and breach flags
9. Mezzanine shortfall in downside case
10. Construction cost 10% retention and unpaid construction cost
11. Logistics forward sale repayment
12. Completion delay increases finance cost
13. Drawdown condition not met blocks draw

## 14. Development Plan

### Sprint 1: Schema Alignment

- Add Pydantic schemas or typed dictionaries for:
  - `CashFlowRunRequest`
  - `CashFlowScenarioSet`
  - `MonthlyCashFlowRow`
  - `CashFlowRunResult`
  - `WaterfallResult`
  - `UnsoldLtvMatrix`
  - `CashFlowReportFragments`
- Add schema mapping from `FeasibilityStudyV2`.
- Add fixture payloads.

### Sprint 2: Pure Calculation Wrappers

- Build product sales revenue helpers.
- Build sales collection schedule generator.
- Build monthly cash-flow row builder.
- Build Max Minus detail wrapper.
- Build PF draw schedule helper.
- Build operating / repayment account allocation helper.

### Sprint 3: Scenario Engine Integration

- Use Calculation Scenario Engine for waterfall, unsold LTV matrix, and tranche recovery.
- Add best/base/downside/stress/recovery scenario runner.
- Generate risk flags.

### Sprint 4: API and Persistence

- Add `/cash-flow/*` endpoints.
- Save latest cash-flow result.
- Add result history.
- Add source metadata.

### Sprint 5: Report and UI

- Add report fragments.
- Feed Phase 11 PF review sections.
- Add Cash Flow Review UI.

## 15. Recommendation

Recommendation: proceed with enhancement.

Rationale:

- The existing pure functions already cover the calculation seed.
- The current missing work is mostly schema, scenario composition, API workflow, persistence, UI, and report text.
- Cash Flow Engine is the natural bridge between REALYTICS feasibility and PLATFORM9 PF review.
- This module is lender-critical because it tests principal preservation, not just development profit.

Implementation should begin with schema alignment and pure deterministic wrappers before UI or advanced agent behavior.
