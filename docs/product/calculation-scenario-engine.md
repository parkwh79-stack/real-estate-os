# Calculation Scenario Engine Roadmap

Updated: 2026-05-18  
Project: REALYTICS AI LAB  
Module Code: P1-Calculation-Scenario  
Module Name: Calculation Scenario Engine  

## 1. Purpose

The Calculation Scenario Engine is the shared numerical scenario layer for REALYTICS.

Its purpose is to turn existing pure calculation functions into a practical workflow for PF review, developer feasibility review, marketability review, and report generation.

The first target scope is:

1. Waterfall simulator
2. Unsold LTV matrix
3. Scenario result schema
4. API workflow
5. UI review workflow
6. Phase 11 PF review report integration

This module should not replace the existing calculation functions. It should wrap them with scenario definitions, validation, provenance, storage, and report-ready outputs.

## 2. Current Implementation

Current pure functions exist in `services/api-server/core/calculations.py`.

| Function | Current Capability | Current Gap |
|---|---|---|
| `simulate_waterfall(cash_available, tranches)` | Allocates available cash by tranche priority and returns payment/shortfall | No scenario set, no API endpoint, no persistence, no UI, no report mapping |
| `calculate_unsold_ltv(pf_amount, total_units, unsold_units, avg_unit_price, discount_rate)` | Calculates one unsold LTV point | No matrix across sale rates, unsold units, discounts, or product types |
| `calculate_tranche_recovery(tranche_amount, total_recovery_pool, prior_claims)` | Calculates tranche recovery from a recovery pool | Not yet exposed as a scenario workflow |

Marketability Agent already has related baseline outputs:

| Marketability Output | Relationship |
|---|---|
| `exitSaleRate` | Provides repayment sale-rate thresholds |
| `discountTrigger` | Provides timing and discount trigger assumptions |
| `unsoldRecovery` | Applies product-type haircut and tranche recovery |
| `riskFlagDetails` | Flags unsold LTV, absorption, and tranche recovery weakness |

The Calculation Scenario Engine should become the common engine behind these outputs over time.

## 3. Why This Should Be Enhanced

This module should be enhanced because waterfall and unsold LTV are lender-critical calculations.

In PF review, the key question is not only whether total profit is positive. The key questions are:

1. Which tranche is repaid first?
2. Under what sales rate does each tranche become whole?
3. What happens if discount sale is required?
4. What is the remaining unsold collateral value?
5. Does the recovery pool cover senior, mezzanine, and junior exposure?
6. Which scenario should be reflected as a covenant, trigger, or approval condition?

The current pure functions are useful, but they are not yet orchestration-grade.

## 4. Target Position in REALYTICS

```text
Feasibility Study / Product Mix / PF Structure
    -> Calculation Scenario Engine
        -> Waterfall Scenario
        -> Unsold LTV Matrix
        -> Tranche Recovery Matrix
        -> Discount Sale Sensitivity
    -> Marketability Agent
    -> Risk Matrix
    -> Phase 11 PF Review Report
    -> Frontend Scenario Review UI
```

## 5. Scope

### 5.1 In Scope

| Area | Description |
|---|---|
| Waterfall scenarios | Allocate sale proceeds, recovery proceeds, or available cash through tranche priority |
| Unsold LTV matrix | Calculate LTV across unsold units/rates, discount rates, and product-type haircuts |
| Tranche recovery matrix | Estimate senior/mezzanine/junior recovery under each scenario |
| Discount sensitivity | Run 0%, 5%, 10%, 15%, and 20% discount cases |
| Report-ready output | Generate summary rows, risk flags, and report text fragments |
| API workflow | Add project-level endpoints for scenario runs |
| UI workflow | Add scenario matrix review panels |
| Persistence | Save scenario assumptions, result snapshots, and run metadata |

### 5.2 Out of Scope for First Version

| Area | Reason |
|---|---|
| Monte Carlo simulation | Keep first implementation deterministic and auditable |
| ML-based price recovery | Requires historical training data |
| External valuation data adapter | Should be handled by Marketability/Data Adapter modules |
| Legal covenant drafting automation | Legal Review Agent should generate legal phrasing later |

## 6. Canonical Schema

### 6.1 Scenario Request

```json
{
  "scenarioSetId": "pf-recovery-base-v1",
  "scenarioSetName": "PF Recovery Scenario Set",
  "baseDate": "2026-05-18",
  "currency": "KRW",
  "unit": "KRW",
  "project": {
    "projectId": "project-id",
    "projectName": "Project Name",
    "productType": "mixed_use_residential"
  },
  "productMix": [
    {
      "type": "A",
      "units": 100,
      "avgUnitPrice": 720000000,
      "totalRevenue": 72000000000
    }
  ],
  "pfStructure": [
    {
      "tranche": "Tr.A",
      "amount": 63000000000,
      "priority": 1,
      "interestRate": 0.065,
      "feeRate": 0.01
    }
  ],
  "scenarioDrivers": {
    "saleRates": [0.4, 0.5, 0.6, 0.7, 0.8, 1.0],
    "discountRates": [0, 0.05, 0.1, 0.15, 0.2],
    "unsoldRates": [0, 0.1, 0.2, 0.3, 0.4],
    "recoveryHaircuts": [0.1, 0.2, 0.3],
    "additionalPriorityCosts": 0
  },
  "waterfallRule": {
    "priorityOrder": ["tax", "essential_cost", "senior_interest", "senior_principal", "mezzanine", "junior", "equity"],
    "tranchePriorityField": "priority",
    "includeInterest": true,
    "includeFees": true
  },
  "sourceMetadata": {
    "sourceName": "manual scenario input",
    "sourceType": "manual_upload",
    "sourceUrlOrEvidenceId": "scenario-input-001",
    "baseDate": "2026-05-18",
    "collectedAt": "2026-05-18T00:00:00+09:00",
    "licenseOrUsageNote": "Internal scenario input",
    "confidence": 0.8
  }
}
```

### 6.2 Waterfall Result

```json
{
  "scenarioId": "sale-rate-60-discount-10",
  "cashAvailable": 90000000000,
  "totalClaimAmount": 95000000000,
  "remainingCash": 0,
  "totalShortfall": 5000000000,
  "rows": [
    {
      "rank": 1,
      "tranche": "Tr.A",
      "priority": 1,
      "principalClaim": 63000000000,
      "interestClaim": 4095000000,
      "feeClaim": 630000000,
      "totalClaim": 67725000000,
      "paymentReceived": 67725000000,
      "shortfall": 0,
      "recoveryRatio": 1.0,
      "status": "repaid"
    }
  ]
}
```

### 6.3 Unsold LTV Matrix Result

```json
{
  "matrixType": "unsold_ltv_matrix_v1",
  "pfAmount": 90000000000,
  "totalUnits": 500,
  "avgUnitPrice": 720000000,
  "rows": [
    {
      "saleRate": 0.7,
      "unsoldRate": 0.3,
      "unsoldUnits": 150,
      "discountRate": 0.1,
      "grossUnsoldValue": 108000000000,
      "discountedUnsoldValue": 97200000000,
      "unsoldLtv": 0.9259,
      "riskLevel": "high"
    }
  ],
  "summary": {
    "bestCaseLtv": 0.65,
    "baseCaseLtv": 0.82,
    "stressCaseLtv": 1.1,
    "maxLtv": 1.25,
    "breachCount": 8
  }
}
```

### 6.4 Scenario Set Result

```json
{
  "analysisType": "calculation_scenario_engine_v1",
  "scenarioSetId": "pf-recovery-base-v1",
  "status": "completed",
  "dataStatus": "complete",
  "waterfallScenarios": [],
  "unsoldLtvMatrix": {},
  "trancheRecoveryMatrix": [],
  "riskFlags": [
    {
      "code": "UNSOLD_LTV_BREACH",
      "severity": "high",
      "message": "Unsold LTV exceeds 90% in stress discount scenarios."
    }
  ],
  "reportFragments": {
    "waterfallSummary": "",
    "unsoldLtvSummary": "",
    "trancheRecoverySummary": "",
    "approvalCondition": ""
  },
  "sourceMetadata": {},
  "createdAt": "2026-05-18T00:00:00+09:00"
}
```

## 7. API Design

### 7.1 Waterfall Scenario

```text
POST /projects/{project_id}/scenarios/waterfall
```

Purpose:

- Run one or more waterfall cases.
- Accept explicit cash amounts or derive cash available from sale rate x total revenue.
- Return tranche payment, shortfall, and recovery ratio.

### 7.2 Unsold LTV Matrix

```text
POST /projects/{project_id}/scenarios/unsold-ltv-matrix
```

Purpose:

- Generate unsold LTV matrix across sale rates, unsold rates, and discount rates.
- Support product-type haircut defaults.
- Return risk bands and breach counts.

### 7.3 Combined Recovery Scenario

```text
POST /projects/{project_id}/scenarios/recovery
```

Purpose:

- Run waterfall, unsold LTV, discount sale, and tranche recovery together.
- Save result as a scenario run.
- Feed output to Marketability, Risk Matrix, and Phase 11 report.

## 8. UI Design

The Scenario Review page should contain:

| Panel | Description |
|---|---|
| Scenario inputs | Sale rates, discount rates, unsold rates, recovery haircut, PF tranches |
| Waterfall table | Tranche payment, shortfall, recovery ratio |
| Unsold LTV matrix | Heatmap by sale rate and discount rate |
| Tranche recovery matrix | Recovery by senior/mezzanine/junior |
| Risk flags | LTV breach, mezzanine shortfall, junior wipeout, trigger required |
| Report text | Copy-ready fragments for PF review report |

This can start as a project detail tab and later become a standalone Scenario page.

## 9. Report Mapping

Scenario Engine output should feed the following Phase 11 report sections:

| Report Section | Scenario Output |
|---|---|
| Main financial terms | Tranche claim and repayment assumptions |
| Sale proceeds allocation | Waterfall result |
| Summary cash flow | Scenario cash available and shortfall |
| Business feasibility | Sale-rate/discount sensitivity |
| Exit strategy and risk | Exit sale rate, unsold LTV, tranche recovery |
| Drawdown / conditions | Trigger and approval condition recommendation |
| EOD and covenant | Breach triggers for sale rate, LTV, recovery shortfall |
| Fund execution and repayment order | Waterfall rule |

## 10. Risk Flags

| Code | Condition |
|---|---|
| `WATERFALL_SHORTFALL` | Total available cash is below total claim amount |
| `SENIOR_SHORTFALL` | Senior tranche is not fully repaid |
| `MEZZANINE_SHORTFALL` | Mezzanine tranche is not fully repaid |
| `JUNIOR_SHORTFALL` | Junior tranche is not fully repaid |
| `UNSOLD_LTV_HIGH` | Unsold LTV is above 85% |
| `UNSOLD_LTV_BREACH` | Unsold LTV is above 90% |
| `DISCOUNT_STRESS_FAIL` | 10% or higher discount scenario fails required repayment |
| `TRIGGER_REQUIRED` | Scenario requires sale-rate, discount, or cash sweep trigger |

## 10.1 Restructuring Plan

The Scenario Engine now has a restructuring wrapper for rejection-driving financial risks.

API:

```text
POST /projects/{project_id}/scenarios/restructuring
```

Purpose:

- Quantify the senior tranche reduction or DSRA/equity injection required to remove `SENIOR_SHORTFALL`.
- Quantify the PF reduction/equity increase or additional collateral value required to cure `UNSOLD_LTV_BREACH`.
- Rerun a senior-resized scenario and show whether senior repayment is restored.
- Preserve source locators to the waterfall row and unsold LTV matrix cell used for the cure calculation.

Policy:

- This output is not a waiver.
- Credit Review should move from reject to conditional approval only after the cure actions are applied, source-backed, and rerun through Scenario Engine / PF Review orchestration.
- Senior shortfall should not be solved only through narrative mitigants; the cash waterfall must support full senior repayment or a dedicated repayment support source must be attached.

## 11. Acceptance Criteria

### 11.1 Functional Criteria

- [x] API can run a waterfall scenario from project PF structure.
- [x] API can run waterfall from explicit scenario payload.
- [x] API can generate unsold LTV matrix.
- [x] API can generate tranche recovery matrix.
- [x] API can generate a quantified restructuring plan for senior shortfall and unsold LTV breach.
- [x] Result includes risk flags and report fragments.
- [x] Result can be saved as a scenario run.
- [ ] Marketability Agent can reference scenario result when available.
- [ ] Phase 11 report can include scenario summary rows.

### 11.2 Calculation Criteria

- [x] Existing pure function tests remain valid.
- [x] Scenario wrapper tests cover senior-only, senior/mezzanine/junior, and insufficient-cash cases.
- [x] Restructuring tests cover senior shortfall cure and unsold LTV cure amount.
- [x] Unsold matrix tests cover 0%, 10%, 20% discount cases.
- [x] Zero or invalid unit counts do not produce division errors.
- [x] Output rounds ratios consistently.

### 11.3 Review Criteria

- [ ] Every scenario run stores source metadata.
- [ ] Manual assumptions are clearly marked as manual.
- [ ] Stress scenarios are not presented as base case.
- [ ] Report fragments mention material data limitations.

## 12. Development Plan

### Sprint 1: Schema and Pure Scenario Wrappers

- Add `ScenarioSetRequest` and result schemas.
- Add wrapper functions:
  - `run_waterfall_scenarios`
  - `build_unsold_ltv_matrix`
  - `build_tranche_recovery_matrix`
- Add unit tests.

### Sprint 2: API Workflow

- Add project endpoints:
  - `/scenarios/waterfall`
  - `/scenarios/unsold-ltv-matrix`
  - `/scenarios/recovery`
  - `/scenarios/restructuring`
- Merge project defaults with request assumptions.
- Add result persistence.

### Sprint 3: Report and Agent Integration

- Feed scenario result into Marketability Agent when present.
- Feed scenario result into Risk Matrix.
- Add Phase 11 report section mapping.

### Sprint 4: Frontend Review UI

- Add Scenario Review tab/page.
- Add waterfall table.
- Add unsold LTV heatmap.
- Add report fragment preview.

## 13. Recommendation

Recommendation: proceed with enhancement.

Reason:

- The pure functions already exist.
- The next work is mostly schema, API workflow, scenario composition, and UI/report integration.
- The output is directly useful for PF lenders and developers.
- It connects naturally with Marketability Agent, Risk Matrix, and Phase 11 report.

The first implementation should stay deterministic, transparent, and auditable. Avoid ML, opaque optimization, or external valuation dependencies until the core scenario workflow is stable.
