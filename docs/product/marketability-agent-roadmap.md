# Marketability Analysis Agent Roadmap

Updated: 2026-05-14
Project: REALYTICS AI LAB
Agent Code: P0-Agent-Marketability
Agent Name: Marketability Analysis Agent

## 1. Goal

The Marketability Analysis Agent evaluates saleable real estate development projects from a lender and developer perspective.

Its core question is:

> Can this project sell enough units, at the proposed price and within the required timeline, to repay PF debt?

The Agent is not a simple price lookup tool. It combines product type, product mix, proposed sale price, comparable transactions, presale cases, supply competition, absorption assumptions, PF repayment structure, discount-sale scenarios, and lender risk flags.

The current implementation is a v2 baseline. It produces a structured response, normalizes product mix, accepts manual comparable fixtures, reconciles uploaded document market evidence, calculates pricing judgement, estimates Exit sale rate, proposes discount triggers, generates risk flags, and creates PF review report paragraphs.

## 2. Position in REALYTICS

```text
Land Identification Agent
    -> Regulation Analysis Agent
    -> Mass Planning / Feasibility Agent
    -> Marketability Analysis Agent
    -> Business Feasibility Agent
    -> Cash Flow Agent
    -> PF Structuring Agent
    -> Credit Review / IC Memo Agent
```

| Upstream / Downstream | Connection |
|---|---|
| Land Agent | Address, PNU, location, land area, zoning context |
| Regulation Agent | Permitted use, FAR, BCR, buildable scale |
| Feasibility Agent | Saleable area, total sale revenue, project feasibility |
| Cash Flow Agent | Contract deposit, interim payments, balance, sale proceeds timing |
| PF Structuring Agent | Loan amount, tranche structure, LTV, LTC, Exit sale rate |
| Credit Review Agent | Marketability risk, discount trigger, unsold recovery risk |

## 3. Scope

### 3.1 Applicable Product Types

The Agent applies to assets where individual unit sales or strata sales are a material exit source.

| Product Type | Applicable | Notes |
|---|---:|---|
| Apartment | Yes | General presale, association sale, mixed-use apartment units |
| Urban Residential Housing | Yes | Urban residential housing, compact housing |
| Officetel | Yes | Residential and office officetel products |
| Mixed-use Residential | Yes | Apartment, officetel, and retail mixed projects |
| Commercial Pre-sale | Yes | Retail, neighborhood facilities, strata retail |
| Knowledge Industry Center | Yes | Factory/office strata-sale products and support facilities |
| Section Office | Yes | Small office strata-sale products |
| Serviced Residence | Conditional | Applies when sold as strata-sale units; operating hotel logic is separate |
| Other Strata-sale Assets | Conditional | Applies if individual sale proceeds are a repayment source |

### 3.2 Non-applicable or Separate-Agent Product Types

| Product Type | Handling | Reason |
|---|---|---|
| Logistics Center | Separate logistics leasing/exit agent | Lease, cap rate, tenant, forward-sale logic |
| Hotel | Separate hospitality operating agent | ADR, OCC, GOP, operator contract logic |
| Long-term Rental Office | Separate leasing agent | Rent, vacancy, NOI, cap rate logic |
| Data Center | Separate tenant/power/lease agent | Power, tenant, long-term contract logic |
| Rental Housing | Separate rental feasibility agent | Rent, deposit, NOI, operating expense logic |
| Senior Housing / Healthcare | Separate operating business agent | Operator capability and operating revenue logic |

## 4. Core Analytical Questions

1. Is the project a saleable real estate asset?
2. What product type best describes the saleable asset?
3. Is the product mix complete and internally consistent?
4. Is the proposed sale price reasonable versus comparable evidence?
5. Are market data sources and base dates attached?
6. Is demand evidence sufficient?
7. Is nearby supply or competing presale inventory a material risk?
8. What sale rate is required to repay PF debt?
9. What happens under 5%, 10%, 15%, and 20% discount-sale scenarios?
10. What discount trigger table should be reflected in PF covenants?
11. What is the unsold recovery risk by product type?
12. What should be inserted into the Phase 11 PF review report?

## 5. Development Priorities

### 5.1 P0 - Required Baseline

| Item | Description | Status |
|---|---|---|
| Product type classification | Classify apartment, officetel, KIC, mixed-use, retail, section office, serviced residence, and non-saleable assets | Done |
| Applicability check | Decide whether the Agent applies to the project | Done |
| Product mix normalization | Normalize type, units, exclusive area, contract area, sale price, total revenue, exclusive ratio, and evidence IDs | Done |
| Manual comparable fixture adapter | Accept manual comparable, transaction, presale, and listing arrays in the `market` payload | In Progress |
| Document-derived market evidence parser | Ask ParserAgent to extract product mix, sale price, comparables, rent comparables, rental assumptions, and source metadata from uploaded documents | In Progress |
| Market evidence reconciliation | Use Market Evidence Agent to reconcile document-derived sale price, rent, product mix revenue, feasibility revenue, and comparable evidence before Marketability scoring | In Progress |
| Evidence record output | Emit normalized evidence records for product mix, sale comparables, and rent comparables with source IDs, extracted fields, related checks, and validation status | Done |
| Evidence record persistence | Persist market evidence records to JSON/SQLite state and expose them through project-level API | Done |
| Evidence review API/UI | Allow analysts to approve, reject, or request revision for persisted market evidence records | Done |
| Uploaded document provenance | Attach original file name, SHA-256 hash, file size, content type, upload timestamp, and parser version to parsed project data and market evidence records | Done |
| Original document binary archive | Preserve the uploaded source document in runtime storage and expose source document list/download APIs | Done |
| Comparable normalization | Apply simple distance and product-type adjustment to comparable price per py | Done |
| Pricing comparison | Calculate comparable min, median, max, premium, percentile, judgement, and pricing comment | Done |
| Exit sale rate | Calculate base, 5%, 10%, 15%, and 20% discount scenarios plus tranche-level sale-rate thresholds | In Progress |
| Discount trigger proposal | Produce default trigger table for 3 months, 6 months, 12 months, and completion | In Progress |
| Absorption baseline | Estimate simple completion sale rate from pricing, demand, supply, and Exit pressure | In Progress |
| Unsold recovery baseline | Apply product-type haircut to estimate unsold recovery value and unsold LTV | In Progress |
| Marketability score v2 | Score location, demand, supply, pricing, product, absorption, and PF Exit | In Progress |
| Risk flags | Generate lender-side flags such as price premium, no comparable data, exit rate high, and supply overhang | In Progress |
| Report paragraphs | Generate market analysis, pricing, business feasibility, exit, discount trigger, unsold risk, risk memo, and recommendation paragraphs | In Progress |
| Source metadata | Preserve source name, source type, evidence ID or URL, collected date, base date, license note, and confidence | In Progress |

### 5.2 P1 - Next Step

| Item | Description | Status |
|---|---|---|
| Public transaction adapter | Connect official public transaction data where allowed | Planned |
| Presale case adapter | Collect or import comparable presale case data | Planned |
| Subscription data adapter | Collect subscription competition and contract-rate data | Planned |
| Unsold inventory statistics | Add local unsold inventory and supply statistics | Planned |
| Product-specific scoring weights | Use different scoring weights for apartment, officetel, retail, KIC, and serviced residence | Planned |
| Rent-yield based pricing | Reverse-check officetel, retail, and KIC price using rent/yield assumptions | Planned |
| Absorption model v2 | Estimate monthly absorption from comparable evidence, supply, price, and product type | Planned |
| Frontend Marketability Review page | Add UI panels for summary, pricing, demand/supply, Exit, and report text | Planned |

### 5.3 P2 - Advanced

| Item | Description | Status |
|---|---|---|
| Licensed partner data connectors | Hogangnono, Zigbang, KB, Real Estate Planet, or other licensed data sources | Future |
| Sales-rate prediction ML | Predict sale rate using product, price, supply, location, and market indicators | Future |
| Similar PF case RAG | Retrieve internal PF review cases and marketability risk paragraphs | Future |
| Feasibility Excel integration | Link sale price, total revenue, discounts, and Exit sale rate to feasibility workbooks | Future |
| Lender risk memo generator | Produce lender-specific marketability risk memo | Future |
| Monitoring and back-testing | Compare predicted sale rate against actual sale performance | Future |

## 6. Target Workflow

```text
project / business plan input
    -> classify saleable product type
    -> check Marketability Agent applicability
    -> normalize product mix and proposed sale price
    -> reconcile uploaded document market evidence
    -> define market area by radius and administrative boundary
    -> collect transaction, presale, listing, rent, subscription, and unsold data
    -> normalize comparable prices
    -> compare proposed price against comparable price band
    -> estimate absorption speed and sales schedule
    -> calculate Exit sale rate and repayment resilience
    -> analyze discount-sale scenarios
    -> calculate marketability score and grade
    -> generate risk flags and PF review paragraphs
    -> feed outputs into Phase 11 PF review report and risk matrix
```

## 7. Agent Step Design

### 7.1 classify_product_type

Input fields:

- `project.type`
- `sales_schedule`
- `feasibility_input.total_revenue`
- optional product mix and sales assumptions

Output fields:

```json
{
  "productType": "mixed_use_residential",
  "saleable": true,
  "classificationConfidence": 0.9,
  "classificationReason": "project.type=mixed_use maps to Mixed-use Residential."
}
```

Product type codes:

| Code | Description |
|---|---|
| apartment | Apartment |
| urban_residential | Urban residential housing |
| officetel_residential | Residential officetel |
| officetel_office | Office officetel |
| mixed_use_residential | Mixed-use residential |
| commercial_retail | Commercial retail |
| knowledge_industry_center | Knowledge industry center |
| section_office | Section office |
| serviced_residence | Serviced residence |
| other_saleable | Other saleable asset |
| non_saleable | Non-saleable asset |

### 7.2 determine_applicability

Logic:

```text
if product generates sale proceeds through individual unit sales:
    applicable = true
elif product is primarily a long-term lease or operating-income asset:
    applicable = false
else:
    applicable = conditional or false until evidence is provided
```

### 7.3 normalize_product_mix

Accepted input locations:

- `product_mix`
- `productMix`
- `sales_assumptions.product_mix`
- `salesAssumptions.productMix`
- fallback from `feasibility_input.total_revenue` and saleable area

Output fields:

| Field | Description |
|---|---|
| type | Unit or product type name |
| productType | Standard product type code |
| exclusiveAreaPy | Exclusive area in py |
| contractAreaPy | Contract area in py |
| units | Unit count |
| pricePerPy | Sale price per contract py |
| totalRevenue | Unit-level total sale revenue |
| exclusiveRatio | Exclusive area divided by contract area |
| evidenceIds | Source evidence IDs |

### 7.4 define_market_area

Default market areas:

| Product Type | Primary Radius | Secondary Radius | Tertiary Area |
|---|---:|---:|---|
| Apartment | 1km | 3km | Same district/city |
| Officetel | 500m | 1km | Office catchment |
| Commercial Retail | 300m | 500m | Retail catchment |
| Knowledge Industry Center | 3km | 5km | Industrial catchment |
| Serviced Residence | 500m | 1km | Tourism/office catchment |
| Section Office | 500m | 1km | Office catchment |

### 7.5 collect_market_comparables

The current baseline accepts comparable arrays from the project payload.

Accepted keys:

- `market.comparables`
- `market.transactionComparables`
- `market.transaction_comparables`
- `market.presaleComparables`
- `market.presale_comparables`
- `market.listingComparables`
- `market.listing_comparables`

Required comparable fields:

| Field | Description |
|---|---|
| caseId | Comparable case ID |
| name | Comparable project or building name |
| productType | Comparable product type |
| distanceM | Distance from subject site |
| pricePerPy | Price per py |
| sourceName | Source name |
| sourceType | Source type |
| sourceUrlOrEvidenceId | URL or internal evidence ID |
| baseDate | Transaction, sale, or data base date |
| confidence | Source confidence |

### 7.6 normalize_comparables

The current baseline applies simple adjustments:

- distance adjustment
- product-type adjustment
- similarity score

Future versions should add:

- area adjustment
- building age or presale timing adjustment
- brand adjustment
- transit/access adjustment
- floor and visibility adjustment for retail
- rent-yield adjustment for investment products
- market timing adjustment

### 7.7 compare_pricing

Pricing judgement rules:

| Premium to Comparable Median | Judgement |
|---:|---|
| Below -10% | underpriced |
| -10% to +5% | reasonable |
| +5% to +10% | slightly_high |
| +10% to +20% | high |
| Above +20% | excessive |

Output fields:

- `proposedPricePerPy`
- `comparableMinPricePerPy`
- `comparableMedianPricePerPy`
- `comparableMaxPricePerPy`
- `premiumToMedian`
- `percentile`
- `judgement`
- `pricingComment`
- `evidenceIds`

### 7.8 analyze_demand

Current baseline:

- accepts explicit `market.demandLevel`
- defaults to `unknown` if no demand evidence exists

Future demand evidence:

- population and household trends
- employment and office demand
- subscription competition
- unsold inventory trend
- rent/yield support
- investor and owner-occupier demand split

### 7.9 analyze_supply_competition

Current baseline:

- uses number of comparable cases
- uses `market.upcomingSupplyUnits`

Supply risk levels:

| Level | Meaning |
|---|---|
| low | Limited comparable supply |
| moderate | Comparable supply exists but may be absorbable |
| high | Heavy competing supply or large upcoming pipeline |
| severe | Multiple failed presale cases or material unsold risk |

### 7.10 forecast_absorption

Current baseline uses:

- pricing judgement
- supply competition level
- demand level
- Exit sale rate pressure

Output:

- 3-month expected sale rate
- 6-month expected sale rate
- 12-month expected sale rate
- completion sale rate
- base/downside/stress scenarios

### 7.11 calculate_exit_sale_rate

Definition:

```text
Base Exit Sale Rate =
Required Repayment Amount / Total Sale Revenue
```

The Agent also calculates:

- 5% discount scenario
- 10% discount scenario
- 15% discount scenario
- 20% stress discount scenario
- senior, mezzanine, and junior cumulative tranche sale-rate thresholds

Future versions should connect to:

- sales collection schedule
- repayment reserve ratio
- cash waterfall
- unpaid construction cost
- financial cost
- tranche amortization and repayment priority

### 7.12 analyze_unsold_recovery

Current baseline:

- applies a product-type haircut to total sale revenue
- calculates unsold recovery value
- calculates unsold LTV
- returns a senior/mezzanine recovery judgement

Future product-specific methods:

| Product Type | Method |
|---|---|
| Apartment | Discount sale, auction value, completed-unit mortgage capacity |
| Officetel | Rent yield, collateral loan capacity, discount sale |
| Commercial Retail | Rent capitalization, floor-level recovery, vacancy stress |
| Knowledge Industry Center | Rent, tenant demand, collateral recognition |
| Serviced Residence | Operating revenue, regulation risk, discount sale |
| Section Office | Rent, small office demand, vacancy stress |

### 7.13 propose_discount_trigger

Default trigger table:

| Time | Target Sale Rate | Discount Rate | Action |
|---|---:|---:|---|
| 3 months | 25% | 0% | Strengthen marketing and buyer incentives |
| 6 months | 40% | 5% | Review indirect discount up to 5% |
| 12 months | 60% | 10% | Review direct discount up to 10% |
| Completion | Max of 80%, forecast, or 10% discount Exit rate | 15% | Review bulk sale, refinancing, or rental conversion |

### 7.14 score_marketability

Current weighted baseline:

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
| 85-100 | A | Strong marketability |
| 70-84 | B | Acceptable marketability |
| 55-69 | C | Moderate, needs support |
| 40-54 | D | High marketability risk |
| Below 40 | E | Project structure should be revisited |

### 7.15 generate_report_text

Generated paragraph fields:

| Field | Description |
|---|---|
| marketAnalysis | Market and competition summary |
| pricingAnalysis | Proposed price judgement |
| businessFeasibility | Exit sale rate and repayment analysis |
| exitStrategy | PF repayment and covenant logic |
| discountTrigger | Discount-sale trigger proposal |
| unsoldRisk | Unsold recovery and lender risk |
| riskMemo | Lender-side marketability risk memo |
| recommendation | Decision-oriented recommendation |

## 8. Output Schema

```json
{
  "analysisType": "marketability_analysis_v2",
  "agentName": "Marketability Analysis Agent",
  "applicable": true,
  "applicabilityReason": "",
  "projectType": "mixed_use",
  "productType": "mixed_use_residential",
  "assetClass": "Mixed-use Residential",
  "classificationConfidence": 0.9,
  "classificationReason": "",
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
  "exitSaleRate": {},
  "discountTrigger": [],
  "unsoldRecovery": {},
  "score": 0,
  "grade": "C",
  "summary": "",
  "checks": [],
  "riskFlags": [],
  "recommendations": [],
  "reportParagraphs": {},
  "dataSources": [],
  "comparables": [],
  "normalizedComparables": []
}
```

## 9. Data Source Policy

Every market data point used in a conclusion should include source metadata.

Required source fields:

| Field | Description |
|---|---|
| sourceName | Data source name |
| sourceType | public_api, partner_api, internal_db, manual_upload, web, estimate |
| sourceUrlOrEvidenceId | URL or internal evidence ID |
| collectedAt | Collection timestamp |
| baseDate | Data base date |
| licenseOrUsageNote | License, usage right, or partnership note |
| confidence | Source confidence score |

Data source hierarchy:

| Priority | Source Type | Examples |
|---:|---|---|
| 1 | Official public data | MOLIT transaction data, CheongyakHome, unsold statistics, Statistics Korea |
| 2 | Licensed partner data | Valuemap, Real Estate Planet, Hogangnono, Zigbang, KB, if licensed |
| 3 | Internal database | Internal PF review cases, presale case DB, feasibility DB |
| 4 | Manual upload | Sale brochure, marketing agency proposal, appraisal report |
| 5 | Analyst estimate | Analyst assumption, market interview, conservative stress assumption |

Rules:

1. Do not confirm sale price adequacy without comparable evidence.
2. Do not present definitive conclusions when source metadata or base date is missing.
3. Record license and usage notes for partner data.
4. Use Hogangnono, Zigbang, KB, Real Estate Planet, or similar private-platform data only through official API, partnership, license, or permitted data use.
5. Do not implement non-permitted scraping as a core Agent capability.
6. Cross-check conclusions with at least two evidence categories when available.
7. Do not rely on listing price alone for final pricing judgement.
8. Mark absorption estimates as estimates when actual sale-rate data is unavailable.
9. When using internal PF cases, disclose the case year and market-cycle difference.

## 10. Data Adapter Strategy

```text
Marketability Analysis Agent
    -> Market Data Router
        -> MOLIT Transaction API Adapter
        -> CheongyakHome Adapter
        -> Unsold Housing Statistics Adapter
        -> VWorld Location Adapter
        -> Valuemap Adapter
        -> Real Estate Planet Adapter
        -> Hogangnono / Zigbang Licensed Data Adapter
        -> KB Real Estate Adapter
        -> Internal Comparable DB Adapter
        -> Internal PF Review RAG Adapter
        -> Manual Upload Parser
```

Failover rule:

```text
If partner data is unavailable:
    use official public data
    + internal comparable DB
    + manual uploads
    + analyst assumptions with explicit limitation notes
```

The Agent should not fail just because one source is unavailable. It must clearly state which evidence is missing.

## 11. Product-specific Scoring Logic

### 11.1 Apartment

| Category | Weight |
|---|---:|
| Location and transit | 20 |
| School and living infrastructure | 10 |
| Owner-occupier demand | 15 |
| Subscription and presale evidence | 15 |
| Sale price adequacy | 20 |
| Upcoming supply | 10 |
| PF Exit stability | 10 |

### 11.2 Officetel

| Category | Weight |
|---|---:|
| Transit and office catchment | 20 |
| One-person and two-person household demand | 15 |
| Rent yield | 20 |
| Competing supply | 15 |
| Sale price adequacy | 15 |
| Parking and exclusive ratio | 10 |
| PF Exit stability | 5 |

### 11.3 Commercial Retail

| Category | Weight |
|---|---:|
| Foot traffic and retail district | 20 |
| Visibility and accessibility | 15 |
| Captive household demand | 10 |
| Rent affordability | 20 |
| Floor-level pricing adequacy | 15 |
| Vacancy risk | 10 |
| PF Exit stability | 10 |

### 11.4 Knowledge Industry Center

| Category | Weight |
|---|---:|
| Industrial location | 20 |
| Corporate demand | 15 |
| Eligible tenant industry fit | 15 |
| Price and rent yield | 20 |
| Supply pipeline | 15 |
| Tax and financing benefits | 10 |
| PF Exit stability | 5 |

### 11.5 Serviced Residence

| Category | Weight |
|---|---:|
| Tourism and business demand | 20 |
| Operating revenue potential | 15 |
| Regulatory risk | 20 |
| Sale price adequacy | 15 |
| Comparable product supply | 10 |
| Operator and management structure | 10 |
| PF Exit stability | 10 |

## 12. Development Tracking

| Phase | Deliverable | Status |
|---|---|---|
| P0-1 | Product type classifier and schema | Done |
| P0-2 | Applicability checker | Done |
| P0-3 | Product mix normalizer | Done |
| P0-4 | Comparable case fixture and adapter | In Progress |
| P0-5 | Pricing comparison pure functions and tests | Done |
| P0-6 | Exit sale rate integration | In Progress |
| P0-7 | Discount scenario analyzer | In Progress |
| P0-8 | Marketability score v2 | In Progress |
| P0-9 | Evidence metadata schema | In Progress |
| P0-10 | Report paragraph generator | In Progress |
| P1-1 | Subscription / unsold-stat connector interface | Planned |
| P1-2 | Absorption model baseline | In Progress |
| P1-3 | Map-based comparable supply view | Planned |
| P1-4 | Product-specific scoring engine | Planned |
| P1-5 | Unsold recovery model | Planned |
| P2-1 | Partner-data connector strategy | Future |
| P2-2 | Similar PF review RAG | Future |
| P2-3 | Lender risk memo generator | Future |
| P2-4 | Monitoring and back-testing module | Future |

## 13. Current Implementation

Implemented baseline:

- Saleable asset applicability check
- Product type classification with confidence
- Non-saleable asset exclusion
- Product mix normalization
- Manual comparable fixture ingestion
- Uploaded document market evidence extraction prompt
- Market Evidence Agent reconciliation before marketability scoring
- Normalized evidence records for product mix, sale comparable, and rent comparable evidence
- Persistent market evidence index in JSON/SQLite state
- Project-level market evidence API
- Analyst review workflow for persisted market evidence records
- Uploaded document provenance metadata on parsed project data and market evidence records
- Original uploaded source document archive with project-level list/download API
- Agent-to-agent messages from Market Evidence Agent to Orchestration, Marketability, and Feasibility agents
- Comparable price normalization
- Comparable price band and premium calculation
- Pricing judgement
- Exit sale rate and discount scenarios
- Baseline absorption forecast
- Discount trigger proposal
- Unsold recovery estimate
- Weighted marketability score
- Risk flags
- Data source metadata output
- Missing data and limitation output
- PF review report paragraph generation
- API and orchestration integration

Still missing for practical production review:

| Missing Item | Why It Matters |
|---|---|
| Official public transaction adapter | Needed for real comparable evidence |
| Licensed partner data adapters | Needed for broader market data if rights are secured |
| Product-specific adjustment model | Avoids applying apartment logic to retail or KIC assets |
| Page-level evidence locator | Required to map each extracted value to document page, table, and row references |
| Subscription and unsold data | Required for apartment and residential presale review |
| Rent-yield model | Required for officetel, retail, KIC, and section office pricing |
| Absorption back-testing | Required to improve forecast reliability |
| Dedicated frontend review page | Required for analyst review, correction, and approval |

## 14. Acceptance Criteria

### 14.1 Functional Criteria

- [x] Agent can classify product type with confidence score.
- [x] Agent can determine whether project is saleable.
- [x] Agent can normalize product mix and proposed price.
- [x] Agent can ingest at least manual comparable fixtures.
- [x] Agent can calculate comparable price band.
- [x] Agent can judge proposed price adequacy.
- [x] Agent can calculate base Exit sale rate.
- [x] Agent can calculate 5%, 10%, 15%, and 20% discount scenarios.
- [x] Agent can produce marketability score and grade.
- [x] Agent can generate PF review paragraphs.
- [x] Agent can list missing data and data limitations.

### 14.2 Evidence Criteria

- [x] Comparable cases can carry source metadata.
- [x] Pricing conclusions can include evidence IDs.
- [x] If no evidence exists, the Agent marks the result as preliminary.
- [x] Data base date can be visible in output.
- [x] Partner data usage notes can be stored.
- [ ] Evidence metadata is persisted in storage.
- [ ] Source-level audit trail is available in UI.

### 14.3 Report Criteria

- [x] Output can be inserted into Phase 11 PF review report.
- [x] Report paragraphs include market analysis, pricing, Exit, and risk memo.
- [x] Report paragraphs state missing data when data is incomplete.
- [x] Final recommendation is decision-oriented.

## 15. Test Scenarios

| Scenario | Expected |
|---|---|
| Apartment project with slightly high proposed price and high supply | Pricing `slightly_high`, supply risk moderate/high, score B or C |
| Officetel project with weak rent yield and many competitors | Pricing high if yield below target, risk flag for yield once rent model exists |
| Commercial project with high vacancy and aggressive first-floor price | Supply/vacancy risk high, pricing excessive, unsold recovery weak |
| Knowledge industry center with unclear tenant demand | Demand confidence low, supply risk high, data status partial |
| Serviced residence marketed as residential substitute | Regulatory risk high, marketability score C/D, risk memo flags usage restriction |
| Manual comparable fixture with high proposed price | Pricing `high`, `PRICE_PREMIUM_HIGH` risk flag |

## 16. Frontend Requirements

The Marketability Review page should include:

- Summary panel: product type, applicability, score, grade, risk flags, recommendation
- Pricing panel: proposed price, comparable median, premium, comparable table, price band chart
- Demand/supply panel: market area, competitors, upcoming supply, unsold statistics, subscription data
- Exit panel: total sale revenue, PF amount, base Exit sale rate, discounted Exit sale rate, tranche recovery
- Report panel: market analysis, pricing paragraph, Exit strategy paragraph, risk memo, copy-to-IC-memo action

## 17. Risk Flags

| Risk Flag | Condition |
|---|---|
| PRICE_PREMIUM_HIGH | Proposed price is at least 10% above comparable median |
| PRICE_PREMIUM_EXCESSIVE | Proposed price is at least 20% above comparable median |
| SUPPLY_OVERHANG | Nearby or upcoming supply is high |
| UNSOLD_INCREASE | Unsold inventory trend is increasing |
| LOW_SUBSCRIPTION | Subscription competition is weak |
| LOW_RENT_YIELD | Rent yield is below required investor yield |
| WEAK_PARKING | Parking is weak versus market expectations |
| LOW_EXCLUSIVE_RATIO | Exclusive ratio is below product threshold |
| REGULATORY_RISK | Product type has material usage or regulatory risk |
| EXIT_RATE_HIGH | Base Exit sale rate is at least 70% |
| DISCOUNT_REQUIRED | Discount scenario requires high sale rate |
| MEZZ_RECOVERY_WEAK | Mezzanine or junior recovery is weak |
| NO_COMPARABLE_DATA | Comparable evidence is missing |
| DATA_BASE_DATE_OLD | Data base date is stale |
| DEMAND_EVIDENCE_MISSING | Demand evidence is missing or unclear |

## 18. Recommendation Logic

Pricing recommendations:

- Maintain proposed sale price
- Adjust price by 3% to 5%
- Adjust price by 5% to 10%
- Rebalance unit-level pricing
- Rebalance floor, orientation, or retail-location pricing

Marketing recommendations:

- Run presale demand survey
- Confirm marketing agency opinion
- Review fixed deposit promotion
- Review interest-free interim payment condition
- Review early-buyer incentive

PF condition recommendations:

- Add target sale-rate trigger
- Add discount-sale trigger
- Increase sale proceeds repayment reserve ratio
- Add completed-unit collateral or trust condition for unsold units
- Strengthen contractor or trustee completion support
- Reduce junior tranche or increase equity

## 19. Example Final Opinion

```text
The project shows acceptable baseline marketability based on its location and saleable asset structure. However, the proposed sale price must be verified against comparable transaction and presale evidence before the pricing conclusion can be confirmed.

Base Exit sale rate is estimated at approximately 55% to 60%, while a 10% discount-sale scenario increases the required sale rate. Therefore, target sale-rate covenants should be set at launch plus 6 months, 12 months, and completion.

The project may be acceptable if comparable evidence supports the proposed pricing and if discount-sale triggers, sale proceeds waterfall controls, and unsold recovery protections are reflected in PF conditions.
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

- Frontend Marketability Review page
- Test scenarios
- Back-testing structure

## 21. Final Product Definition

The Marketability Analysis Agent is a REALYTICS Agent for saleable development projects such as apartments, officetels, serviced residences, knowledge industry centers, section offices, and commercial strata-sale assets.

Given project summary, address/PNU, product mix, proposed sale price, feasibility revenue, PF structure, and market evidence, it estimates marketability, pricing adequacy, absorption, Exit sale rate, discount trigger needs, unsold recovery risk, and lender-side risk flags. It then generates structured outputs and report paragraphs for the Phase 11 PF review report.
