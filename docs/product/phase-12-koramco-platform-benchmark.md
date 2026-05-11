# Phase 12: Koramco-style Real Estate Finance Platform Benchmark

## Purpose

Phase 12 expands REALYTICS x PLATFORM9 from a PF review/reporting MVP into a broader real-estate finance operating system. The benchmark model is inspired by public Koramco positioning: an integrated platform that links REITs, real estate funds, and real estate trust services, supported by sector-specialist organization, portfolio metrics, track record, and post-closing management.

## Benchmark observations

Public Koramco materials emphasize the following platform attributes:

1. Integrated business lines: REITs, real estate funds, and real estate trust.
2. Trust indicators: total AUM, cumulative trust volume, total equity, credit rating, employees, and recent track record.
3. Sector specialization: office, retail, residential, logistics, data center, overseas, debt, development, industrial-complex development, urban renewal, and trust products.
4. Portfolio presentation: business line allocation, asset class allocation, representative deal cards, and recent track record.
5. End-to-end service: land development, process management, capital management, trust, REITs/fund formation, and asset management.

## Gap against current REALYTICS Alpha

The current codebase is strong in PF review, marketability pre-check, legal/permit pre-check, and Phase 11 report generation. To become a real-estate OS, it needs the following additional layers.

| Priority | Gap | Required capability |
|---|---|---|
| P0 | Business line layer | Classify each project as PF, REITs, REF, trust, agency service, urban renewal, or hybrid structure. |
| P0 | Portfolio metrics | Track AUM, trust assets, equity exposure, debt exposure, sector allocation, and realized/unrealized track record. |
| P0 | Sector strategy | Maintain different KPI scorecards for office, logistics, data center, residential, retail, hotel, and mixed-use projects. |
| P0 | Trust product logic | Add management-type trust, leveraged land trust, completion-guarantee trust, non-land trust, and agency service classification. |
| P1 | Track record benchmark | Compare new deals with similar historical acquisition, development, lease-up, sale, or trust execution cases. |
| P1 | Expert routing | Route each project to relevant sector, legal, PF, trust, tax, and monitoring agents. |
| P1 | Monitoring | Track post-closing construction progress, presale rate, occupancy, NOI, LTV, covenant breaches, and EOD triggers. |

## Added backend component

A new `PlatformBenchmarkAgent` has been added.

Path:

```text
services/api-server/agents/platform_benchmark_agent.py
```

The agent returns:

- Koramco-style reference model
- recommended platform modules
- recommended agents
- capability gaps
- roadmap
- report section additions

A new router file has also been added:

```text
services/api-server/routers/platform.py
```

The router exposes:

```text
GET /platform/benchmark/reference-model
POST /platform/benchmark/analyze
```

> Note: `main.py` still needs to register this router with `app.include_router(platform.router)`. This repository update was not applied automatically because the GitHub connector declined the write action for the existing `main.py` file.

## Recommended new agents

### 1. BusinessLineClassifierAgent

Classifies a project into one or more business lines:

- PF review
- REITs acquisition/development
- REF acquisition/development/debt/blind fund
- management-type land trust
- leveraged land trust
- completion-guarantee trust
- non-land trust
- agency service
- urban renewal

### 2. PortfolioMetricsAgent

Tracks portfolio-level indicators:

- total AUM
- REITs AUM
- fund AUM
- trust assets
- equity committed
- debt exposure
- sector allocation
- realized IRR
- realized MOIC
- unrealized valuation change
- weighted average LTV
- weighted average occupancy
- weighted average lease expiry

### 3. SectorStrategyAgent

Maintains sector-specific scorecards.

Required scorecards:

- Office
- Logistics
- Data center
- Retail
- Residential
- Senior housing
- Mixed-use development
- Overseas real estate
- Debt investment
- Urban renewal / trust-type development

### 4. TrackRecordBenchmarkAgent

Matches a new project with comparable historical deals by:

- asset type
- location
- transaction size
- development stage
- capital structure
- tenant profile
- exit route
- realized return
- risk event history

### 5. ExpertRoutingAgent

Routes a review to the correct specialist queue:

- PF structuring
- trust/security
- permit/legal
- marketability
- logistics
- data center
- office income asset
- residential presale
- tax/accounting
- monitoring

### 6. MonitoringAgent

Tracks post-closing risk indicators:

- construction progress
- drawdown amount
- budget burn
- presale rate
- lease-up rate
- rent collection
- NOI
- LTV
- DSCR
- covenant breach
- EOD trigger
- reporting deadline

## Report additions

Phase 11 PF report should be extended with the following optional sections.

| New section | Purpose |
|---|---|
| Platform trust indicators | AUM, equity, credit rating, track record, sector expertise. |
| Business line fit | Whether the deal is best suited for PF, REITs, REF, trust, agency, or hybrid structure. |
| Sector strategy | Asset-class-specific investment logic and KPI scorecard. |
| Comparable track record | Similar historical deals and differences. |
| Post-closing monitoring plan | Construction, presale, lease-up, NOI, LTV, covenant, and EOD monitoring plan. |

## Implementation tasks

### P0

1. Register `platform.router` in `services/api-server/main.py`.
2. Add Pydantic schema for `BusinessLine`, `PortfolioMetric`, `SectorScorecard`, and `TrackRecord`.
3. Add `business_line`, `portfolio_metrics`, `sector_strategy`, and `track_records` fields to project fixtures.
4. Add `/platform/benchmark/analyze` integration test.
5. Add frontend page `/platform` to show AUM, trust assets, sector allocation, and recommended modules.

### P1

1. Add track record database table.
2. Add sector-specific scorecard tables.
3. Add monitoring event table.
4. Add report section renderer for platform trust indicators and monitoring plan.
5. Add MCP tool for platform benchmark analysis.

### P2

1. Connect actual public/company portfolio data sources.
2. Add visual portfolio allocation charts.
3. Add expert workflow assignment and approval queue.
4. Add quarterly portfolio review report generator.
