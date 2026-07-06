# REALYTICS AI LAB OS Knowledge Learning Directive

Updated: 2026-06-30

This document records the ChatGPT-derived operating directive for turning REALYTICS x PLATFORM9 into a practical real-estate development and PF credit-review OS.

## 1. Final System Shape

REALYTICS x PLATFORM9 is organized as two engines and one orchestrator.

| Layer | Role | Primary Scope |
|---|---|---|
| REALYTICS Engine | Land & Feasibility Intelligence | Address/PNU, parcels, zoning, regulation, market data, product planning, feasibility, monthly cash flow, Max Minus |
| PLATFORM9 Engine | Finance & Credit Review Intelligence | Bridge loans, main PF, ABL, refinancing, tranche structure, credit enhancement, Waterfall, Covenants, EOD, IC memo |
| Real Estate Decision OS | Orchestrator | Routes uploaded materials into the right workflow, coordinates agents, blocks unsupported conclusions, and requires human review |

The OS must not automatically approve permits, loans, investments, or disbursements. It prepares evidence, calculations, risk opinions, and approval conditions for human review.

## 2. Knowledge Domains To Standardize

The knowledge base must cover these domains as first-class data and workflow concepts.

| Domain | Required Coverage |
|---|---|
| Development process | Site sourcing, land/legal review, market research, development concept, feasibility, design/permit, contractor selection, PF preparation, construction, sale/lease, completion, settlement, operation/exit |
| Asset/product types | Apartment, urban residential, youth housing, mixed-use, officetel, serviced residence, retail, section office, knowledge industry center, logistics, data center, hotel, golf, retail |
| Feasibility structure | Sales revenue, lease income, sale proceeds, deposits, VAT refund, land cost, construction cost, indirect cost, sales cost, taxes, trust fee, overhead, contingency, finance cost, fees |
| Cash Flow | Monthly Cash In/Out, collection schedule, PF draw, equity injection, construction payment, finance cost timing, operating/repayment accounts, cumulative cash, Max Minus |
| PF structure | Borrower, sponsor, contractor, trustee, agent bank, senior/mezz/junior lenders, equity, SPC, ABSTB, private bond, credit facility, sell-down, trust/delegated account |
| Tranche | Name, seniority, amount, draw/repayment type, rate, fee, all-in cost, payment type, draw/repayment/collateral priority, LTV/LTC, Exit sale rate, unsold LTV, target IRR |
| Credit enhancement | Trust beneficiary right, responsible completion, debt assumption, guarantees, share pledge, abandonment letters, insurance pledge, construction cost retention, performance bond, forward sale, master lease |
| Risk factors | Land acquisition, title, relocation, permit, civil complaint, sponsor, contractor, trustee, cost overrun, delay, sale risk, unsold recovery, balance payment, lease termination, forward sale break, rate, liquidity, refinancing, exit, legal, tax/accounting, ESG/policy |
| Waterfall | Presale proceeds split, contract/interim/balance split, pre/post completion rules, sales-rate thresholds, reserve account, operating account, loan repayment, finance cost, project cost, construction cost, trust profit, equity recovery |
| Covenants and EOD | Target sale rate, discount trigger, progress delay, responsible completion deadline, interest delay, LTV/DSCR breach, minimum equity, permit cancellation/delay, collateral damage, contractor credit downgrade, representations breach |
| Reports | Land review, regulation review, developability report, feasibility statement, monthly CF, scenario analysis, PF structure memo, tranche proposal, term sheet, IC memo, PF review report, risk memo, covenant sheet, monitoring report |
| Standard terms | Canonical term, aliases, English name, definition, hierarchy, related metrics, usage context, source document/page, confidence, review status |

## 3. Agent Map

| Agent | Responsibility | Core Output |
|---|---|---|
| Document Ingestion Agent | Parse PDF/DOCX/XLSX/PPT/images/tables/formulas into source-mapped facts | document inventory, parsed pages, tables, formulas, source map |
| Terminology Agent | Normalize real-estate, PF, finance, legal, and feasibility terms | canonical terms, synonym map, undefined terms |
| Project Intake Agent | Create project from address/PNU/documents and classify available/missing materials | project id, asset type, parties, documents, missing list |
| Land & Parcel Agent | Analyze PNU, parcels, ownership, encumbrances, road/access, land-use plan | parcel table, ownership status, title risk |
| Regulation Agent | Analyze zoning, district plans, laws, ordinances, FAR/BCR/height/parking constraints | zoning summary, permitted use, constraints |
| Market & Valuation Agent | Compare transactions, presale, rent, Cap Rate, valuation evidence | comparable data, price/rent assumptions, marketability score |
| Product Planning Agent | Compare feasible product options from land/regulation/market constraints | product options, GFA program, unit mix, parking plan |
| Feasibility Agent | Build and validate feasibility statement and economics | revenue/cost tables, profit, IRR, NPV, sensitivity |
| Cash Flow Agent | Generate monthly CF, draw/collection/payment schedules, Max Minus | monthly CF, Max Minus, ending cash, remaining loan |
| PF Structuring Agent | Design bridge/main PF/ABL/refi/tranche structure | financing plan, tranche structure, LTV/LTC/DSCR/Exit |
| Risk Review Agent | Aggregate land, legal, market, cost, completion, and exit risks | risk matrix, mitigation plan, red flags |
| Covenant & EOD Agent | Define covenant thresholds, EOD triggers, notification/remedy workflow | covenant sheet, EOD trigger list, monitoring rules |
| Report Generation Agent | Generate lender/developer/investor-facing reports with citations | IC memo, PF report, term sheet, committee memo |
| Validation Agent | Recalculate and block unsupported assumptions or formula mismatches | validation report, calculation errors, missing assumptions |
| Monitoring Agent | Track post-approval covenant and project performance | dashboard, covenant heatmap, monitoring report |
| Alert Agent | Notify breaches, missing data, and review actions | alerts, Kakao/mobile work orders |

## 4. End-to-End Orchestration Phases

| Phase | Agents | Outputs |
|---|---|---|
| Phase 0 Document Learning | Document Ingestion, Terminology | document inventory, knowledge base, source map, standard terms |
| Phase 1 Land and Regulation | Project Intake, Land & Parcel, Regulation | parcel analysis, legal constraints, development capacity, permit risk |
| Phase 2 Market and Feasibility | Market & Valuation, Product Planning, Feasibility, Cash Flow | product plan, feasibility statement, monthly CF, Max Minus, IRR/NPV |
| Phase 3 PF Structuring | PF Structuring, Risk Review, Covenant & EOD | tranche structure, financing plan, Waterfall, covenant sheet, risk matrix |
| Phase 4 Reporting and Validation | Report Generation, Validation | PF report, IC memo, term sheet, validation report, human review checklist |
| Phase 5 Monitoring | Covenant & EOD, Monitoring, Alert | PF dashboard, covenant heatmap, EOD alert, monthly monitoring report |

## 4.1 Target Pod-Based Orchestration Architecture

The next target architecture is not a simple linear chain. It is a shared-state, parallel-pod operating model.

```text
User Input / Files / APIs / Internal DB
  -> Deal Intake & Router
  -> Shared Deal State Graph
       - Project Memory
       - Evidence Vault
       - Assumption Book
  -> Parallel Agent Pods
       - Land-Legal Pod
       - Market-Product Pod
       - Cost-Schedule Pod
       - Feasibility-Cashflow Pod
       - PF-Credit Pod
       - Public-Policy Pod
  -> Cross-Agent Reconciliation Layer
  -> AI Risk Committee
  -> Decision Gate
  -> Report Studio + Human Expert Review
  -> Learning Loop
```

### Pod Definitions

| Pod | Agents | Primary Questions |
|---|---|---|
| Land-Legal Pod | Land & Parcel, Zoning/Regulation, Rights/Encumbrance, Permit Feasibility, Site Constraint | Is the site legally, physically, and title-wise developable? |
| Market-Product Pod | Market Comparable, Demand/Absorption, Product Concept, Pricing, Competition | What product should be built, at what price, and how fast can it be absorbed? |
| Cost-Schedule Pod | Construction Cost, Indirect Cost, Schedule, VE/Spec Optimization, Contractor Risk | What will it cost, when will cash be needed, and what construction risks can change feasibility? |
| Feasibility-Cashflow Pod | Revenue Model, Development Cost Model, Proforma, Monthly Cash Flow, Max Minus/Sensitivity | Does the project work economically and what funding gap appears over time? |
| PF-Credit Pod | PF Structuring, Tranche, LTV/LTC/DSCR, Exit Sales Rate, Covenant/Waterfall, Lender Persona | Can lenders recover principal and what structure/conditions are needed? |
| Public-Policy Pod | Public Feasibility, Urban Planning, Public Contribution, Policy Simulation | Is there a public-sector feasibility, planning, or policy impact angle? |

### Shared Deal State Graph

The Shared Deal State Graph is the canonical project memory. It should persist:

| Component | Purpose |
|---|---|
| Project Memory | Current canonical project state across land, regulation, market, feasibility, cash flow, PF, risks, and reports |
| Evidence Vault | Source files, API records, Excel cells, pages, tables, formulas, source locators, hashes, and evidence IDs |
| Assumption Book | Human-approved assumptions, overrides, expirations, superseded values, and scenario assumptions |
| Agent Artifacts | Each pod's intermediate outputs, progress, blockers, handoffs, and confidence |
| Scenario Ledger | Cash flow, stress, waterfall, unsold LTV, covenant, and monitoring run history |

### Reconciliation Loop

Pod outputs must be reconciled before credit/report conclusions.

```text
Business feasibility
  -> Cash Flow
  -> Max Minus
  -> PF limit adjustment
  -> finance cost changes
  -> feasibility recalculation
  -> Exit sale rate recalculation
  -> risk grade update
```

This loop is not optional. If the Cash Flow or PF structure changes finance cost, the Feasibility-Cashflow Pod must rerun and the Risk Committee must receive the updated metrics.

### AI Risk Committee

The AI Risk Committee is a review layer, not a final approver.

| Committee Agent | Role |
|---|---|
| Devil's Advocate Agent | Actively challenges optimistic assumptions, missing evidence, and weak mitigants |
| Stress Test Agent | Runs downside, severe, tail, discount, delay, cost overrun, and rate shock scenarios |
| Legal & Compliance Agent | Checks legal blockers, CP/CS/Covenant/EOD, permit, title, trust, and regulatory issues |
| Credit Review Agent | Converts risk register, metrics, and mitigants into credit opinion |
| IC Judge Agent | Simulates investment committee review quality and asks whether the memo is decision-grade |
| Lender Persona Agent | Re-reads the deal from bank, securities, capital, savings bank, asset manager, and equity investor viewpoints |

### Report Studio and Learning Loop

Report Studio should generate:

- land review memo
- feasibility statement
- monthly Cash Flow workbook
- PF review report
- IC Memo
- Term Sheet
- monitoring sheet

Human Expert Review should persist reviewer corrections as:

- approved assumption overrides
- glossary/canonical term changes
- risk review decisions
- report section edits
- institution-specific policy preferences
- historical outcome records

Those corrections should feed the Learning Loop, but never silently rewrite deterministic calculations.

## 5. Implementation Updates From This Directive

- Track-specific workflow definitions now include Product Planning, Covenant/EOD, Validation, Monitoring, and Alert responsibilities where relevant.
- The intake flow should remain step-by-step. It should not jump to PF review until common evidence gates and selected-track workflow steps are explicitly run.
- New terms found in uploaded materials should flow to the glossary candidate queue before they become canonical fields.
- Source locators must be preserved for every material number, formula, risk, report sentence, and human override.
- The Chief Deal Orchestrator prompt is now stored as `docs/product/chief-deal-orchestrator-prompt.md`.
- The first schema contract for the target AI Deal Room is available in `schemas/orchestration.py`: `SharedDealStateGraph`, `DealStateValue`, `MissingInputRegisterItem`, `AgentPodPlan`, `AgentPodRun`, `RiskCommitteeOpinion`, `ScenarioConclusion`, `DecisionGateResult`, and `ChiefDealOrchestrationPlan`.
- `POST /projects/{project_id}/chief-orchestration/plan` now builds the first API-level AI Deal Room plan from hydrated project data. It returns the Shared Deal State Graph, Missing Input Register, six Agent Pods, task graph, scenario placeholders, and preliminary Decision Gate. It does not run approval-sensitive pod analysis yet.

## 6. Remaining Implementation Gaps

| Gap | Recommended Next Step |
|---|---|
| Product Planning Agent is represented in workflow but not fully implemented as a standalone engine | Create product option schema and deterministic product feasibility comparison |
| Covenant & EOD Agent is represented but still partly embedded in PF/credit outputs | Promote covenant thresholds, breach severity, remedy period, and monitoring rule generation into a dedicated module |
| Validation Agent is represented but needs cross-workbook/source recalculation depth | Add formula-level audit for uploaded feasibility workbooks and report generation blocks |
| Monitoring and Alert workflows need runtime persistence | Store covenant monitoring rules, monthly actuals, breach logs, and Kakao/mobile notifications |
| Standard term DB needs deeper parser integration | Call glossary normalization on every parser field and create unmapped candidates automatically |
| Pod-based parallel execution is not yet first-class | Baseline plan API exists. Next: add pod-level run API, EventBus, recalculation event persistence, and pod artifact storage |
| AI Risk Committee is partially represented by existing credit/stress/risk logic | Add Devil's Advocate, IC Judge, and Lender Persona as explicit committee steps |
| Cost-Schedule Pod is under-modeled | Promote construction cost, indirect cost, schedule, VE/spec optimization, and contractor risk into dedicated outputs |
| Public-Policy Pod is not implemented | Add public feasibility, urban planning, public contribution, and policy simulation workflow for public/private projects |
