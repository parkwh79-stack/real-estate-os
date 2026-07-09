# Agent Interaction Operating Model

Updated: 2026-05-20  
Project: REALYTICS x PLATFORM9  
Document status: Target architecture and implementation guide

## 1. Purpose

REALYTICS should not operate as a set of isolated agents. It should operate as an agent system where each agent produces structured evidence, passes decision-grade inputs to the next agent, receives feedback when assumptions conflict, and reports progress to the Orchestration Agent.

This document defines REALYTICS as a **Real Estate Operating System**. The goal is not to build a collection of report-writing assistants. The goal is to build a system that reads documents, normalizes data, validates assumptions, calculates feasibility, structures PF risk, learns from prior reviews, and produces evidence-backed decisions.

The core philosophy is:

```text
Documents are inputs.
Schemas are the common language.
Agents are specialized reviewers.
Handoffs are controlled data contracts.
The server is the system of record.
The database becomes institutional memory.
Reports are evidence-backed outputs.
```

The target operating model is:

```text
Land Agent
  -> land defects, PNU, official land price, registry evidence, acquisition status
  -> Legal Review Agent
  -> Development Feasibility Agent
  -> PF Review / Report Agent

Market Evidence Agent
  -> extracted market evidence and comparable evidence QA
  -> Marketability Agent
  -> Development Feasibility Agent
  -> Cash Flow / PF Scenario Engine
  -> PF Review / Report Agent

Orchestration Agent
  -> task graph
  -> progress tracking
  -> artifact handoff
  -> assumption conflict detection
  -> human review gates
```

## 1.1.1 Current Chief Pod Execution Contract

As of 2026-07-01, the Chief Deal Orchestrator is no longer only a planning board. Each selected Pod writes a durable `chief_pod_artifact` record and also maps its result into standard downstream artifact keys.

For the pilot PF track, the intended step-by-step execution is:

```text
Land-Legal Pod
  -> landAgentReview
  -> legalPermitReview
  -> developabilityScore
  -> landLedgerEnrichment or landLedgerEnrichmentError

Market-Product Pod
  -> marketEvidenceReview
  -> marketabilityAnalysis
  -> liveMarketComparables when configured

Feasibility-Cashflow Pod
  -> developerFeasibility
  -> feasibility
  -> returnMetrics
  -> cashFlowRunResult

PF-Credit Pod
  -> pfStructure
  -> scenariosEvaluation when product mix and PF structure are ready
  -> riskRegister
  -> creditReviewDecision
```

Land evidence gaps should not prevent the Land-Legal Pod from running, because that Pod is responsible for identifying and requesting missing PNU, official land price, cadastral, registry, and encumbrance evidence. The same gap may still block the PF-Credit Pod and approval readiness gate.

The Orchestrator must therefore distinguish:

| State | Meaning |
|---|---|
| `planned` | Enough input exists to run the Pod and produce a diagnostic artifact. |
| `completed` | The Pod wrote an artifact, even if that artifact says `needs_review`. |
| `blocked` | A downstream decision cannot run because a prerequisite evidence class is missing. |
| `needs_recalculation` | A prior Pod changed an assumption and the Event Bus marked this Pod for rerun. |

The system must answer three questions:

1. What did each agent complete?
2. What did each agent pass to another agent?
3. Which assumptions are still unverified and should not be treated as final for legal review, feasibility review, or PF approval?

It must also answer three long-term operating questions:

1. Which schema field did this decision come from?
2. Which evidence and source document support this field?
3. How does this case compare with prior reviewed projects?

## 1.1 Real Estate OS Principles

| Principle | Meaning |
|---|---|
| One canonical schema | Every agent should map inputs into shared schemas before downstream use. |
| No silent overwrite | Agents may propose patches, but must not silently overwrite another agent's assumptions. |
| Evidence-first calculation | Important numbers must carry source metadata, confidence, and evidence IDs. |
| Server as source of truth | Parsed facts, assumptions, handoffs, reviews, reports, and approvals must be persisted server-side. |
| Historical comparability | Current reviews should become future comparable cases for marketability, feasibility, PF structuring, and credit review. |
| Controlled vocabulary | New business terms must be registered, normalized, and mapped to canonical fields before becoming automation logic. |
| Human review gates | AI can recommend and reconcile, but final approval-sensitive assumptions require review state and audit trail. |

## 1.2 Canonical Schema Strategy

The system should treat `FeasibilityStudyV2` as the first canonical operating schema, but it should not be the only one. REALYTICS needs a family of compatible schemas:

| Schema | Purpose |
|---|---|
| `FeasibilityStudyV2` | Developer feasibility, site, program, product mix, revenue, cost, financing, schedule, scenarios, provenance, formulas, and metrics. |
| `LandLedgerImportV1` | Land ledger import, parcel records, owner/payment records, PNU confidence, official land price, registry evidence, acquisition status, and PF land summary. |
| `MarketabilityAnalysisV2` | Product type, market evidence, comparables, pricing judgement, absorption, Exit sale rate, discount trigger, unsold recovery, score, and risk flags. |
| `CashFlowRunRequestV1` | Sources/uses, product sales schedule, collection rules, cost schedules, PF draw rules, account allocation, waterfall rules, and scenarios. |
| `ScenarioSetV1` | Waterfall, unsold LTV matrix, tranche recovery, discount stress, and downside/stress cases. |
| `AgentHandoffEnvelopeV1` | Typed communication between agents with payload, evidence, confidence, blockers, review status, and progress impact. |
| `EvidenceRecordV1` | Source document, field locator, original text, extracted value, normalized value, confidence, hash, and review status. |
| `CreditReviewDecisionV1` | Final credit opinion, conditions, mitigants, risks, required waivers, and approval trail. |
| `Phase11ReportV1` | 20-section PF review report payload and export metadata. |

Rule:

Every agent may have its own internal model, but every cross-agent output must be translated into one of the canonical schemas or into an `AgentHandoffEnvelopeV1` patch against a canonical schema.

## 1.3 Controlled Vocabulary and New-Term Handling

Real estate/PF documents often introduce new or inconsistent terms. The system must handle new terms without breaking downstream calculations.

Examples:

```text
Exit 분양률
분양가 LTV
상환적립계좌
관리형토지신탁
우선수익권
책임준공
채무인수
할인분양 Trigger
Max Minus
미분양 LTV
선매입계약
책임임차인
국공유지
제한물권
```

The target mechanism is a `RealEstateGlossary` and `CanonicalFieldMap`:

```json
{
  "term": "상환적립계좌",
  "aliases": ["대출금상환계좌", "repayment reserve account", "상환계좌"],
  "canonicalField": "cashFlow.accounts.repaymentReserveAccount",
  "domain": "cash_flow",
  "definition": "Account where sale proceeds or other cash are reserved for loan repayment before distribution.",
  "usedByAgents": ["CashFlowAgent", "PFStructuringAgent", "CreditReviewAgent", "ReportAgent"],
  "sourceExamples": [
    {
      "sourceType": "pf_review_report",
      "text": "분양수입금 중 잔금은 대출원금 상환에 우선 사용"
    }
  ],
  "reviewStatus": "approved"
}
```

New-term workflow:

```text
Parser Agent detects unknown term
  -> Data Quality / Glossary module creates candidate term
  -> Orchestration Agent flags glossary review
  -> Human reviewer maps term to canonical field or marks as free text
  -> Future parser and agents reuse the approved term mapping
```

Rule:

If a new term affects numbers, legal status, cash flow, PF conditions, or report conclusions, it must be mapped to a canonical field before it is used for automated decision logic.

## 1.4 Server Data and Institutional Memory

The server should persist more than final reports. It should become the institutional memory of the platform.

Required persisted entities:

| Entity | Why It Matters |
|---|---|
| Source documents | Original evidence for parser, legal, audit, and report review. |
| Parsed facts | Reusable structured facts extracted from business plans, Excel, PDFs, and APIs. |
| Canonical project snapshot | The normalized current state of project data. |
| Agent handoffs | Traceable cross-agent communication and assumption changes. |
| Evidence records | Source-backed values, field locators, confidence, and review status. |
| Scenario runs | Cash flow, Max Minus, waterfall, unsold LTV, and discount stress outputs. |
| Risk register | Consolidated risk items across agents. |
| Credit decision | Approval/conditional approval/rejection, conditions, mitigants, and reviewer trail. |
| Report exports | Markdown, JSON, HTML, PDF, DOCX artifacts and hashes. |
| Historical review cases | Prior reviewed projects for benchmarking and RAG/comparable analysis. |

### Parsed Fact Store

As of the current implementation, uploaded business plans and IM documents do
not flow directly into downstream Pods as loose JSON only. Parser outputs are
also persisted as `deal_state_records` with `recordType="parsed_fact"`.

Each `parsed_fact` row carries:

| Field | Purpose |
|---|---|
| `rawText` | Evidence-facing raw parser surface. |
| `normalizedSurface` | Unicode-safe matching surface for glossary/canonical-field mapping. |
| `textQualityFlags` | UTF-8, mojibake, replacement-character, zero-width diagnostics. |
| `termId` | Approved or candidate glossary term ID. |
| `canonicalField` / `factKey` | The shared field each Pod should reason about. |
| `value` / `normalizedValue` | Extracted value and normalized comparable value. |
| `sourceLocator` | Document page, Excel cell, API row, evidence ID, or document-only locator. |
| `locatorStatus` | `located`, `document_only`, `missing`, or related status. |
| `targetPods` | Pods expected to validate or consume the fact. |
| `validationStatus` | `parsed`, `needs_review`, or `needs_source_locator`. |

Operating rule:

```text
Parser Agent
  -> persists parsed_fact candidates
  -> Chief Deal Orchestrator loads parsed facts into Shared Deal State
  -> Cross-Agent Reconciliation checks conflicts and weak locators
  -> Each Pod validates only the parsed facts relevant to its domain
  -> Human reviewer approves/overrides only when needed
```

This prevents early parsing from becoming a silent final assumption. It also
lets Market-Product, Land-Legal, Feasibility-Cashflow, and PF-Credit Pods share
the same candidate values, source locators, and term normalization boundary.

### Pod Parsed-Fact Validation

Each Pod must validate the parser candidates it consumes before treating them
as decision-grade assumptions. The validator is deterministic and evidence
aware; LLM reviewers may explain findings, but they do not replace the source
locator and value-consistency checks.

| Pod | Parsed-fact validation responsibility |
|---|---|
| `land_legal` | Verify land area, PNU, official land price, registry and encumbrance evidence candidates. |
| `market_product` | Verify proposed sale price basis, transaction/presale/listing/manual comparables, rent assumptions, and Cap Rate evidence. |
| `feasibility_cashflow` | Verify total sales revenue, total project cost, construction cost, land cost, funding/PF amount, and schedule values. |
| `pf_credit` | Verify PF amount, LTV/LTC/DSCR, Exit sale rate, Waterfall, tranche recovery, and discount-stress candidates. |

Implementation contract:

```text
Chief Pod run
  -> writes chief_pod_artifact
  -> PodParsedFactValidator loads parsed_fact rows whose targetPods include the Pod
  -> Auto Evidence Supplement tries public/internal/historical sources before blocking
  -> validator updates podValidationStatusByPod on each parsed_fact
  -> validator writes parsed_fact_validation record
  -> validator materializes blocking gaps as risk_issue records
  -> Approval Readiness Gate and Risk Register read the same records
```

### Auto Evidence Supplement Before Blocking

REALYTICS should not stop at "missing input" when the missing value can be
legitimately recovered from public APIs, internal deal memory, or historical
review cases. The Orchestrator must first attempt supplementing evidence and
only then ask the user for additional materials.

```text
Parsed Fact validation gap
  -> Auto Evidence Supplement
     -> official public API evidence when available
     -> project-memory evidence when already stored
     -> market comparable benchmark when price basis is missing
     -> historical benchmark candidate when project data is absent
     -> manual evidence request when licensed/manual evidence is required
  -> Persist source-backed records
  -> Re-run Pod parsed-fact validation
  -> Keep approval-grade use behind review/approval when the value is a proxy
```

Supplemented values are classified by origin:

| Origin | Record type | Approval use |
|---|---|---|
| Official public API | `evidence` + `parsed_fact` | Can support analysis; final approval still checks locator/review gates. |
| Project memory / uploaded state | `evidence` + `parsed_fact` | Can support analysis when source locator exists. |
| Market comparable benchmark | `assumption` + `parsed_fact` | Scenario and review use only until approved. |
| Historical case benchmark | `assumption` + `parsed_fact` | Proxy only; requires human approval. |
| Registry / encumbrance missing | `evidence` request | Cannot be invented; remains a required evidence request. |

Important invariant:

```text
Auto supplement may create a candidate value.
It must never pretend that a proxy is the user's submitted assumption.
Every supplemented value must carry sourceAuthority, factOrigin, sourceLocator,
confidence, and review status.
```

Approval gate rule:

```text
Auto-supplemented official evidence can support analysis when the source locator
is complete.

Auto-supplemented benchmark/proxy values cannot support final approval until
their assumption candidate and companion risk_issue are reviewed.

Failed auto-supplement attempts, evidence requests, or user-material-required
gaps remain approval blockers until they are resolved, waived with rationale,
or replaced by source-backed evidence.
```

The Approval Readiness Gate therefore evaluates:

| Gate | Meaning |
|---|---|
| `DEAL_STATE_ASSUMPTION_CANDIDATES_REVIEWED` | Benchmark/proxy assumption candidates must be approved, waived, resolved, or replaced. |
| `DEAL_STATE_AUTO_SUPPLEMENT_REVIEW_ITEMS_CLEARED` | Auto-supplement risks such as `PROPOSED_PRICE_BASIS_SUPPLEMENTED_NEEDS_REVIEW` must be reviewed. |
| `DEAL_STATE_AUTO_SUPPLEMENT_GAPS_RESOLVED` | Failed or manual-evidence-required supplement attempts must be closed before approval. |

Reviewer UI rule:

```text
Risk Register shows auto-supplement risk_issue rows.
Evidence / Assumption Review shows evidence, assumptions, parsed facts,
validation records, and auto_supplement_attempt rows from the same Deal State
ledger.
Review actions write back to deal_state_records with audit history.
```

Standard finding codes include:

| Code | Meaning |
|---|---|
| `PARSED_FACT_SOURCE_LOCATOR_WEAK` | A critical parsed value lacks page/cell/API-row/evidence locator. |
| `PARSED_FACT_VALUE_CONFLICT` | Two or more parsed values for the same canonical field differ beyond tolerance. |
| `PARSED_FACT_TERM_MAPPING_NEEDED` | A parsed term is not sufficiently mapped to the controlled vocabulary. |
| `PARSED_FACT_REQUIRED_MISSING` | The Pod lacks a required parsed candidate/evidence class. |
| `PROPOSED_PRICE_BASIS_MISSING` | Market comparables may exist, but the project’s proposed price basis is missing. |

Target historical comparison:

```text
Current project
  -> compare with prior projects by:
     - location
     - product type
     - zoning
     - land area
     - GFA
     - sale price per py
     - land cost ratio
     - construction cost per py
     - total PF amount
     - LTV / LTC
     - Exit sale rate
     - marketability score
     - land readiness score
     - legal readiness
     - final approval conditions
     - actual outcome when available
```

This enables:

- Similar PF review case retrieval
- Marketability benchmark comparison
- Feasibility assumption sanity checks
- Lender policy comparison
- Approval condition recommendation
- Backtesting of prior AI assumptions
- Institutional learning over time

## 2. Current Implementation Check

### 2.1 Already Implemented

| Area | Current Status |
|---|---|
| Market Evidence Agent | Extracts and reconciles uploaded document-derived product mix, sale comparables, rent comparables, source metadata, evidence records, and agent messages. |
| Marketability Agent | Classifies saleable asset type, normalizes product mix and comparables, evaluates pricing, absorption, Exit sale rate, discount trigger, unsold recovery, score, risk flags, and report paragraphs. |
| Development Feasibility Agent | Produces developer-side feasibility, PF readiness, FeasibilityStudyV2 draft, asset calculation profiles, and CashFlowRunRequest draft. |
| Cash Flow Engine | Consumes CashFlowRunRequest draft and returns sales collection, account allocation, PF draw, monthly cash flow, Max Minus, risk flags, and report fragments. |
| Land Ledger Agent | Parses land ledger workbook, validates import consistency, normalizes lots, detects PNU and official land price gaps, produces land acquisition status and PF summary. |
| Legal Review Agent | Reviews legal, permit, land-rights, trust/security, covenant, and Korean Law MCP context when supplied. |
| Orchestration Agent | Runs a synchronous PF review flow and stores steps, artifacts, errors, approval, and quality check. |

### 2.2 Partially Implemented

| Requirement | Current Gap |
|---|---|
| Marketability to Feasibility handoff | Baseline implemented. Orchestration now stores a typed `marketability_to_feasibility` patch with revenue, absorption, discount scenario, risk flag, and report paragraph payloads. |
| Agent-to-agent interaction | Baseline implemented. Orchestration now stores typed `AgentHandoffEnvelope` records for key agent handoffs, while a full message bus remains future work. |
| Orchestration progress tracking | Baseline implemented. Orchestration now stores `agentProgress` records with progress percentage, completed/pending tasks, dependencies, artifacts, handoff IDs, and review flags. |
| Land Agent in PF orchestration | Baseline implemented. PF review orchestration now runs `land_agent_review`, stores `landAgentReview`, and emits a typed `land_to_feasibility_legal_pf` patch for feasibility, legal, PF structuring, and credit review consumers. |
| Official land price enrichment | Baseline implemented. LandLedgerEnrichmentAgent can batch resolve PNU from legal dong/lot, attach cadastral characteristics, official price, and registry evidence from connector/manual records. Live public/licensed adapters remain future connector work. |
| Assumption conflict handling | Baseline implemented. PF review orchestration now creates a `cross_agent_reconciliation` artifact that compares feasibility revenue, marketability revenue, land area, official price readiness, registry readiness, market evidence readiness, and PF metric availability. |

### 2.3 Not Yet Implemented

| Requirement | Needed Implementation |
|---|---|
| Typed agent handoff artifact | Baseline handoff review records and UI actions are implemented. Next step is cross-run audit history and stricter quality gates. |
| Agent progress ledger | Baseline `agentProgress` is visible in the orchestration UI and updates from handoff review actions. Final approval is blocked until unresolved handoff reviews are cleared. |
| Market-to-feasibility patch | Complete downstream reconciliation so Development Feasibility can accept, reject, or rerun from Marketability patch assumptions. |
| Land-to-feasibility/legal/PF patch | Deterministic converter from land output to site inputs, land cost validation, official land price, acquisition status, legal evidence requirements, and PF report land section. |
| Cross-agent reconciliation | Comparison between market-derived sales revenue, feasibility workbook revenue, business plan revenue, and cash flow scenario revenue. |
| Human review gates | Required approval gates before treating market price, land acquisition, legal evidence, or feasibility outputs as final. |

## 3. Core Agent Roles

## 3.1 Market Evidence Agent

Purpose:

Validate the reliability of source evidence before Marketability Agent uses it.

Inputs:

- Uploaded business plan
- Uploaded market report
- Uploaded sales agency proposal
- Manual comparable fixture
- Transaction comparable array
- Presale comparable array
- Listing comparable array
- Rent comparable array
- Source locators and evidence metadata

Outputs:

- Normalized evidence records
- Field source locators
- Evidence completeness
- Evidence confidence
- Market evidence issues
- Agent messages

Required downstream handoff:

```json
{
  "handoffType": "market_evidence_to_marketability",
  "fromAgent": "MarketEvidenceAgent",
  "toAgent": "MarketabilityAgent",
  "payload": {
    "productMixEvidence": [],
    "saleComparables": [],
    "rentComparables": [],
    "sourceMetadata": [],
    "missingRequiredEvidence": []
  },
  "confidence": 0.82,
  "blockingIssues": [],
  "requiresHumanReview": false
}
```

Rule:

Marketability Agent must not make a final pricing conclusion when evidence is missing, stale, unlicensed, or below confidence threshold.

## 3.2 Marketability Agent

Purpose:

Assess whether the proposed product, price, sales velocity, discount trigger, and unsold recovery support the project and PF repayment.

Inputs:

- Product type
- Product mix
- Proposed sale price
- Comparable sale price
- Comparable rent
- Subscription / unsold statistics when available
- Feasibility revenue
- PF structure
- Sales collection schedule

Outputs:

- Product type classification
- Saleable asset applicability
- Normalized product mix
- Comparable price band
- Pricing judgement
- Marketability score and grade
- Absorption forecast
- Discount trigger
- Exit sale rate
- Unsold recovery
- Risk flags
- Report paragraphs

Required downstream handoff to Development Feasibility Agent:

```json
{
  "handoffType": "marketability_to_feasibility",
  "fromAgent": "MarketabilityAgent",
  "toAgent": "DevelopmentFeasibilityAgent",
  "payload": {
    "revenueAssumptionPatch": {
      "weightedAveragePricePerPy": 0,
      "totalSalesRevenue": 0,
      "pricingJudgement": "reasonable",
      "premiumToComparableMedian": 0,
      "evidenceIds": []
    },
    "absorptionScenarioPatch": {
      "baseCompletionSalesRate": 0,
      "downsideCompletionSalesRate": 0,
      "stressCompletionSalesRate": 0
    },
    "discountScenarioPatch": [
      {
        "discountRate": 0.1,
        "adjustedRevenue": 0,
        "exitSaleRate": 0
      }
    ],
    "riskFlags": []
  },
  "confidence": 0.78,
  "blockingIssues": [],
  "requiresHumanReview": true
}
```

Rule:

Marketability Agent should send a feasibility patch, not directly overwrite feasibility assumptions. Development Feasibility Agent must reconcile the patch against business plan, Excel workbook, and manually approved assumptions.

## 3.3 Development Feasibility Agent

Purpose:

Create developer-side feasibility, normalize it to FeasibilityStudyV2, and decide whether downstream cash flow and PF scenario analysis can run.

Inputs:

- Business plan
- Basic architectural plan
- Feasibility workbook
- Marketability handoff
- Land handoff
- User assumptions

Outputs:

- FeasibilityStudyV2 draft
- Revenue assumptions
- Cost assumptions
- Developer returns
- PF readiness
- Data quality warnings
- CashFlowRunRequest draft
- Sensitivity inputs

Required reconciliation:

```text
business plan revenue
vs workbook revenue
vs marketability-derived revenue
vs product mix revenue
vs cash-flow sales revenue
```

Output rule:

If marketability-derived revenue differs materially from feasibility revenue, the agent must flag `REVENUE_ASSUMPTION_CONFLICT` and ask Orchestration Agent to hold final PF review until reviewed.

## 3.4 Land Agent

Purpose:

Determine whether the land can support legal review, feasibility review, and PF review.

Inputs:

- Land ledger Excel
- Parcel list
- Owner/payment records
- Address
- PNU
- Official land price data
- Cadastral characteristics
- Registry evidence
- Land use plan
- Public land acquisition status

Outputs:

- Parcel normalization
- PNU confidence score
- Official land price with base year/source
- Land acquisition ratio
- Public land detection
- Ownership/share validation
- Payment schedule validation
- Registry and encumbrance evidence status
- Land defects and legal follow-ups
- PF land summary

Required downstream handoff:

```json
{
  "handoffType": "land_to_feasibility_legal_pf",
  "fromAgent": "LandAgent",
  "toAgent": ["DevelopmentFeasibilityAgent", "LegalReviewAgent", "PFReviewAgent"],
  "payload": {
    "sitePatch": {
      "pnu": "",
      "legalAddress": "",
      "landAreaSqm": 0,
      "officialLandPrice": {
        "baseYear": 2026,
        "pricePerSqm": 0,
        "sourceEvidenceId": ""
      }
    },
    "landCostValidation": {
      "ledgerTotalPurchaseAmount": 0,
      "feasibilityLandCost": 0,
      "variance": 0,
      "status": "matched_or_needs_review"
    },
    "landAcquisitionStatus": {
      "acquisitionAreaRatio": 0,
      "acquisitionAmountRatio": 0,
      "missingParcels": [],
      "publicLandDetected": false
    },
    "legalEvidenceStatus": {
      "registryEvidenceCoverageRatio": 0,
      "encumbranceIssues": [],
      "releaseRequired": []
    }
  },
  "confidence": 0.86,
  "blockingIssues": [
    "official_land_price_missing",
    "registry_evidence_missing"
  ],
  "requiresHumanReview": true
}
```

Critical rule:

Land ledger import is only the beginning of land acquisition review. It must not be treated as legal review complete or PF review final until PNU, cadastral characteristics, official land price, registry evidence, ownership/share validation, and encumbrance review are complete.

## 3.5 Legal Review Agent

Purpose:

Review legal/permit risk after receiving land evidence and Korean Law MCP context.

Inputs:

- Land Agent handoff
- Korean Law MCP context
- Permit schedule
- Trust/security structure
- Covenant/EOD draft

Outputs:

- Legal status
- Permit status
- Blocking legal issues
- Required follow-ups
- Evidence references
- Covenant recommendations

Rule:

Legal Review Agent must distinguish between `not reviewed`, `preliminary`, `ready for legal review`, and `legal review completed`.

## 3.6 Orchestration Agent

Purpose:

Coordinate agents, track progress, detect conflicts, and prevent premature final decisions.

Responsibilities:

- Build task graph
- Start agent runs
- Store artifacts
- Store handoff envelopes
- Track progress
- Detect blocked dependencies
- Compare assumptions across agents
- Trigger human review gates
- Generate final report only after minimum quality gates

## 4. Agent Handoff Envelope

All agent-to-agent communication should use one envelope shape:

```json
{
  "handoffId": "uuid",
  "projectId": "project-id",
  "runId": "run-id",
  "fromAgent": "MarketabilityAgent",
  "toAgent": "DevelopmentFeasibilityAgent",
  "handoffType": "marketability_to_feasibility",
  "status": "ready",
  "createdAt": "2026-05-20T00:00:00+09:00",
  "payloadVersion": "realytics-agent-handoff-v1",
  "payload": {},
  "evidenceIds": [],
  "sourceMetadata": [],
  "confidence": 0.8,
  "progressImpact": {
    "unblocks": ["feasibility_revenue_reconciliation"],
    "blocks": []
  },
  "blockingIssues": [],
  "warnings": [],
  "requiresHumanReview": false,
  "review": {
    "status": "pending",
    "reviewedBy": null,
    "reviewedAt": null,
    "comment": null
  }
}
```

Required statuses:

| Status | Meaning |
|---|---|
| `draft` | Agent has partial output but should not be consumed downstream. |
| `ready` | Output can be consumed by target agent. |
| `needs_review` | Output can be inspected but should not be treated as final. |
| `blocked` | Output cannot be used until blocking issues are resolved. |
| `accepted` | Human or downstream agent accepted the handoff. |
| `rejected` | Handoff was rejected and must be revised. |

## 5. Agent Progress Ledger

Orchestration Agent should store progress per agent:

```json
{
  "agentProgress": {
    "LandAgent": {
      "status": "blocked",
      "progressPercent": 55,
      "completedTasks": [
        "ledger_import",
        "parcel_normalization",
        "import_consistency_validation"
      ],
      "pendingTasks": [
        "pnu_resolution",
        "official_land_price_fetch",
        "registry_evidence_verification"
      ],
      "blockingIssues": [
        "official_land_price_missing",
        "registry_evidence_missing"
      ],
      "latestArtifactKey": "landLedger",
      "lastUpdatedAt": "2026-05-20T00:00:00+09:00"
    }
  }
}
```

Recommended progress model:

| Agent | Key Milestones | Weight |
|---|---|---:|
| Land Agent | Import, parcel validation, PNU, official price, registry evidence, PF land summary | 20 |
| Market Evidence Agent | Source extraction, evidence normalization, locator coverage, evidence QA | 15 |
| Marketability Agent | Product classification, comparable pricing, absorption, Exit sale rate, score, report text | 15 |
| Development Feasibility Agent | FeasibilityStudyV2, revenue/cost reconciliation, PF readiness, cash flow draft | 20 |
| Cash Flow / Scenario Engine | Monthly CF, Max Minus, waterfall, unsold LTV, discount stress | 15 |
| Legal Review Agent | Law context, permit, land rights, trust/security, covenant/EOD | 10 |
| Report Agent | Phase 11 report, quality check, exports | 5 |

## 6. Cross-Agent Reconciliation Rules

### 6.1 Market Price Reconciliation

Marketability Agent must compare:

```text
proposed sale price
vs comparable median
vs feasibility workbook sale price
vs business plan sale price
```

Flags:

| Flag | Condition |
|---|---|
| `PRICE_PREMIUM_HIGH` | Proposed price is more than 10% above comparable median. |
| `PRICE_ASSUMPTION_CONFLICT` | Market-derived revenue differs from feasibility revenue by more than 5%. |
| `NO_COMPARABLE_EVIDENCE` | No usable comparable evidence exists. |
| `SOURCE_METADATA_MISSING` | Pricing conclusion lacks source metadata. |

### 6.2 Revenue Reconciliation

Development Feasibility Agent must compare:

```text
business plan revenue
workbook revenue
marketability revenue patch
cash-flow sales revenue
```

If difference exceeds threshold:

```json
{
  "code": "REVENUE_RECONCILIATION_FAILED",
  "severity": "high",
  "message": "Marketability-derived sales revenue differs from feasibility revenue beyond tolerance.",
  "requiresHumanReview": true
}
```

### 6.3 Land Cost Reconciliation

Land Agent and Feasibility Agent must compare:

```text
land ledger total purchase amount
vs feasibility land cost
vs sources and uses land cost
vs PF report land cost
```

Flags:

| Flag | Condition |
|---|---|
| `LAND_COST_MISMATCH` | Ledger land cost and feasibility land cost differ beyond tolerance. |
| `PNU_CONFIDENCE_LOW` | PNU match confidence is below threshold. |
| `OFFICIAL_LAND_PRICE_MISSING` | Official land price is missing or lacks base year/source. |
| `REGISTRY_EVIDENCE_MISSING` | Registry evidence is missing. |
| `PUBLIC_LAND_REVIEW_REQUIRED` | Public land or special parcel is detected. |

## 7. Target Orchestration Flow

```text
1. Project Load
2. Source Document Parse
3. Land Agent
   - ledger import
   - PNU/official price/registry evidence status
   - land defects and PF land summary
4. Market Evidence Agent
   - product mix evidence
   - sale/rent comparable evidence
   - source metadata and locator coverage
5. Marketability Agent
   - pricing adequacy
   - absorption
   - Exit sale rate
   - marketability score
6. Development Feasibility Agent
   - FeasibilityStudyV2
   - revenue/cost reconciliation
   - market and land patches applied as reviewed assumptions
7. Cash Flow / Scenario Engine
   - monthly cash flow
   - Max Minus
   - waterfall
   - unsold LTV
8. Legal Review Agent
   - legal/permit/land-rights/trust/covenant review
9. Risk Agent
10. Phase 11 Report Agent
11. Quality Gate
12. Human Approval
```

## 8. Minimum Quality Gates

The Orchestration Agent should not mark a PF review run as final unless:

| Gate | Required Condition |
|---|---|
| Land gate | PNU, official land price base year/source, registry evidence, ownership/share validation, and acquisition status are complete or explicitly waived. |
| Market gate | Comparable evidence and source metadata are present, or missing evidence is explicitly disclosed. |
| Feasibility gate | Revenue, cost, land cost, and product mix reconcile within tolerance or have approved overrides. |
| Cash flow gate | Monthly CF, Max Minus, Exit sale rate, waterfall, and unsold LTV are generated for required scenarios. |
| Legal gate | Legal review status is not `blocking_issue`. |
| Report gate | Phase 11 report has all 20 sections and includes limitations. |

## 9. Data Platform and Historical Review Database

The Real Estate OS must store actual operating data on the server so it can later become a queryable project and credit review database.

## 9.1 Target Database Domains

| Domain | Example Tables / Collections |
|---|---|
| Project master | projects, project_versions, project_snapshots |
| Source documents | source_documents, document_pages, document_hashes |
| Parsed facts | extracted_facts, field_provenance, parser_runs |
| Land intelligence | parcels, parcel_owners, land_payments, pnu_candidates, official_land_prices, registry_evidence |
| Market intelligence | market_comparables, rent_comparables, presale_cases, subscription_stats, unsold_stats |
| Feasibility | feasibility_studies, product_mix, revenue_items, cost_items, financing_items, workbook_versions |
| Cash flow | cash_flow_runs, monthly_cash_flows, account_movements, pf_draws, repayment_schedules |
| Scenario | scenario_sets, waterfall_results, unsold_ltv_matrix, tranche_recovery_results |
| Legal/PF | legal_reviews, permit_reviews, covenants, eod_events, collateral_packages |
| Risk/Credit | risk_register, credit_reviews, approval_conditions, human_reviews |
| Reports | report_versions, report_exports, report_section_sources |
| Glossary | glossary_terms, canonical_field_map, term_aliases, term_review_history |

## 9.2 Project Snapshot Rule

Every major orchestration run should preserve a project snapshot:

```json
{
  "snapshotId": "uuid",
  "projectId": "project-id",
  "runId": "run-id",
  "createdAt": "2026-05-20T00:00:00+09:00",
  "schemaVersions": {
    "feasibilityStudy": "realytics-feasibility-study-v2",
    "marketability": "marketability-analysis-v2",
    "landLedger": "land-ledger-import-v1",
    "handoff": "realytics-agent-handoff-v1"
  },
  "canonicalPayload": {},
  "assumptionSetId": "uuid",
  "evidenceRecordIds": [],
  "reviewStatus": "draft_or_approved"
}
```

Snapshots allow the platform to answer:

- What did the AI know at the time of review?
- Which assumptions were approved?
- Which evidence supported the conclusion?
- How did the project compare with later or earlier cases?

## 9.3 Prior Review Comparison

The historical review database should support this query:

```text
Find prior projects with:
- same or similar product type
- same administrative region or market area
- similar land area / GFA
- similar PF size
- similar pricing premium
- similar Exit sale rate
- similar legal or land acquisition issues
```

Expected output:

```json
{
  "similarCaseCount": 8,
  "similarCases": [
    {
      "projectId": "case-001",
      "projectName": "Anonymized Mixed-Use PF",
      "similarityScore": 0.86,
      "matchedFactors": ["mixed_use", "commercial_zoning", "exit_sale_rate_60_70", "price_premium_high"],
      "approvalDecision": "conditional_approval",
      "keyConditions": [
        "discount trigger",
        "cash sweep",
        "responsible completion"
      ],
      "actualOutcome": {
        "finalSalesRate": 0.82,
        "seniorRecovered": true,
        "mezzanineRecovered": true
      }
    }
  ],
  "benchmarkOpinion": "Current project has higher pricing risk than median similar cases but comparable land readiness."
}
```

## 9.4 Feedback and Learning Loop

The system should preserve actual outcomes after review:

| Outcome | Source |
|---|---|
| Actual sales rate | Sales reports, trust account reports, developer updates |
| Actual discount rate | Sales agency report, approval request, trust account records |
| Actual construction delay | Construction progress report |
| Actual cost overrun | Construction cost ledger, change orders |
| Actual PF repayment | Loan management records |
| Covenant breach | Lender monitoring records |
| Final recovery | Tranche repayment history |

These outcomes should be used for:

- Marketability backtesting
- Cash flow forecast error analysis
- PF structuring calibration
- Risk flag threshold adjustment
- Credit Review Agent policy refinement

## 10. Expanded Agent Architecture

The target Real Estate OS should use four layers.

### Layer 1. Intake and Evidence Layer

| Agent | Role |
|---|---|
| Parser Agent | Extract raw facts with source location, unit, normalized value, and confidence. |
| Feasibility Workbook Validator | Validate Excel workbook structure, formulas, checks, and schema import readiness. |
| Evidence Graph Agent | Link every claim, number, table, and report sentence to source evidence and derived calculations. |
| Data Quality Agent | Detect inconsistencies across documents, Excel, API data, and agent assumptions. |
| Glossary / Canonical Term Agent | Detect new terms and map aliases to canonical fields. |

### Layer 2. Domain Analysis Layer

| Agent | Role |
|---|---|
| Land Agent | Validate land ledger, parcel data, ownership, acquisition, defects, and land readiness. |
| PNU Resolver Agent | Resolve PNU candidates with confidence score and candidate count. |
| Official Land Price Agent | Fetch official land price with base year, base date, source, and evidence ID. |
| Registry Evidence Agent | Verify registry ownership, encumbrances, trust registration, mortgage, seizure, easement, and release conditions. |
| Permit / Regulation Agent | Review zoning, FAR/BCR, ordinance, permit path, and buildability. |
| Product Mix Agent | Normalize apartment, officetel, KIC, commercial, lodging, logistics, and mixed-use product mix. |
| Market Evidence Agent | Normalize market evidence, comparables, rent, presale, subscription, unsold, and source metadata. |
| Cost Estimation Agent | Validate land, construction, indirect cost, financial cost, contingency, and cost per py/sqm. |

### Layer 3. Financial Intelligence Layer

| Agent / Engine | Role |
|---|---|
| Development Feasibility Agent | Build FeasibilityStudyV2, reconcile assumptions, and assess developer-side feasibility. |
| Cash Flow Mapper | Convert FeasibilityStudyV2 into CashFlowRunRequest. |
| Cash Flow Engine | Calculate monthly cash flow, Max Minus, PF draw, sales collection, account allocation, and report fragments. |
| Calculation Scenario Engine | Run waterfall, unsold LTV, tranche recovery, discount stress, downside, and recovery scenarios. |
| Restructuring Agent | Convert rejection-driving scenario risks into quantified senior reduction, DSRA/equity, PF reduction, collateral, forward-sale, bulk-sale, or refinancing cure actions. |
| PF Structuring Agent | Recommend Senior/Mezz/Junior/Equity structure, LTV/LTC, all-in cost, Exit sale rate, cash sweep, and reserve rules. |
| Covenant Generator Agent | Draft sales, progress, LTV, DSCR, completion, drawdown, cash sweep, and EOD covenants. |

### Layer 4. Credit Decision and Output Layer

| Agent | Role |
|---|---|
| Legal Review Agent | Review legal, permit, land-rights, trust/security, covenant, and Korean Law MCP context. |
| Risk Register Agent | Consolidate risks across all agents into one risk register. |
| Credit Review Agent | Produce lender-side decision logic, conditions, mitigants, and approval recommendation. |
| Human Review / Approval Agent | Manage human approval, rejection, override, comments, and audit trail. |
| Orchestration Agent | Coordinate task graph, handoffs, progress, conflicts, review gates, and final readiness. |
| Report Agent | Assemble 20-section PF review report from agent evidence. |
| Report Export Agent | Export Markdown, JSON, HTML, PDF, and DOCX artifacts. |

## 11. Implementation Backlog

### P0

| Task | Description |
|---|---|
| Agent handoff schema | Done for baseline PF review orchestration. `AgentHandoffEnvelope` is implemented and run payloads store typed handoffs. |
| Progress ledger | Done for baseline PF review orchestration. `agentProgress` is implemented with percent, completed/pending tasks, dependencies, artifacts, and handoff IDs. |
| Progress and handoff UI | Done for read-only review. Orchestration UI displays progress bars, handoff routes, payload previews, evidence counts, blockers, assumptions, and data limitations. |
| Handoff review workflow | Done for baseline. API and UI support approve, reject, and needs-revision with reviewer, comment, timestamp, review history, and progress review-state updates. |
| Handoff-gated final approval | Done for baseline. Final approval is blocked while required handoff reviews are pending, rejected, or marked needs-revision. |
| Marketability to feasibility patch | Done for baseline handoff generation. Next step is automatic reconciliation and optional rerun in Development Feasibility. |
| Land to feasibility/legal/PF patch | Done for baseline orchestration. `land_to_feasibility_legal_pf` now carries site, land cost, evidence readiness, PF land summary, acquisition status, blockers, limitations, confidence, and review state. |
| Reconciliation flags | Add revenue, land cost, official price, registry evidence, and comparable price conflict flags. |
| Quality gate update | Done for handoff review gates and baseline reconciliation gates for revenue, land, evidence, and PF metric conflicts. Scenario conflict reconciliation remains future hardening. |
| Canonical schema registry | Define schema versions and common cross-agent field names. |
| Glossary and term mapping baseline | Add controlled vocabulary for core PF and real estate development terms. |
| Server-side case snapshot | Persist canonical project snapshots per orchestration run. |

### P1

| Task | Description |
|---|---|
| Batch PNU resolver | Baseline implemented through LandLedgerEnrichmentAgent with legal-dong-code/lot generation and manual connector records. |
| Official land price connector | Baseline record attachment implemented with base year, base date, source, and evidence ID. Live public API adapter remains future connector work. |
| Registry evidence model | Store registry/cadastral/land-use/contract evidence with hashes and review status. |
| Human review UI | Add UI for accepting/rejecting handoff patches and assumption overrides. |
| Async orchestration | Move agent runs to background jobs with live progress. |
| Evidence Graph Agent | Baseline implemented. PF review runs now create `evidenceGraph` artifacts linking project inputs, source documents, evidence IDs, agent artifacts, handoffs, claims, and Phase 11 report sections. |
| Historical Similar Case Search | Baseline implemented. Stored project snapshots can now be compared by case seed metrics, Risk Register overlap, prior review status, mitigants, approval conditions, and Credit Review outcomes. |
| Snapshot Diff | Baseline implemented. Two same-project snapshots can now be compared for changed assumptions, risks, decisions, artifacts, report sections, material changes, and re-review recommendations. |
| Data Quality Agent | Baseline implemented. PF review runs now create `dataQualityReview` artifacts for project name, land area, GFA, sales revenue, PF amount, unit count, sqm/py conversion, VAT basis, schedule, and tranche structure conflicts. |
| Data Quality Agent | Detect cross-document and cross-agent inconsistencies before final calculations. |
| Risk Register Agent | Baseline implemented. PF review runs now create `riskRegister` artifacts and a `risk_register_to_credit_review` handoff consolidating Land, Marketability, Legal, Cash Flow, PF Structure, Scenario, Data Quality, and Risk Matrix risks. |
| Risk Register review workflow | Baseline implemented. Risk rows can be approved, waived, marked needs-revision, or resolved with reviewer, timestamp, comment, waiver reason, audit history, and final approval blocking for unresolved material risks. |
| Credit Review Agent | Baseline implemented. PF review runs now create `creditReviewDecision` artifacts and a `credit_review_to_report` handoff with decision guidance, reasons, conditions, waivers, and report paragraphs. |
| Restructuring Agent | Baseline implemented. PF review runs now create `restructuringPlan` artifacts and a `restructuring_to_credit_review` handoff with quantified cure actions for senior shortfall and unsold LTV breach. |

### P2

| Task | Description |
|---|---|
| Agent message bus | Add persistent typed messages between agents. |
| Backtesting | Compare marketability forecasts and feasibility assumptions against actual sales outcomes. |
| Institution policy layer | Apply lender-specific thresholds to gates and risk flags. |
| Historical case database | Query prior reviewed projects and compare assumptions, risks, conditions, and actual outcomes. |
| Similar-case RAG | Retrieve comparable prior PF reviews and support Credit Review Agent and Report Agent. |

## 12. Final Rule

Marketability, Land, Legal, Feasibility, Cash Flow, and PF Review agents must not silently overwrite one another. They should pass typed handoff patches with evidence, confidence, limitations, and review status. The Orchestration Agent is responsible for deciding whether the whole run is complete, blocked, or ready for human approval.

REALYTICS should grow into a Real Estate OS by enforcing the same schema language, controlled terminology, persistent server-side evidence, and historical comparison database across every agent.
