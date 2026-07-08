# Pod AI Reviewer Roadmap

Updated: 2026-07-07
Project: REALYTICS x PLATFORM9  
Status: In Progress
Owner: REALYTICS AI LAB / PLATFORM9  

---

## 1. Purpose

Chief Deal Orchestrator currently runs six Agent Pods and stores deterministic pod artifacts. The next step is to attach an AI reviewer layer to each Pod so the system can explain, challenge, request missing evidence, and prepare downstream handoffs without allowing the LLM to invent numbers or override deterministic calculations.

This document defines the implementation path for **Pod AI Reviewer**.

---

## 2. Current State

### 2026-07-07 Implementation Update

Implemented:

- Chief Deal Room UI now reads persisted Pod AI Reviews and displays each Pod card's review status, confidence, AI Brain, findings, missing evidence, next actions, and handoffs.
- Market-Product Pod now creates and stores a linked `pod_ai_reviews` record after deterministic market/product artifact execution.
- Market-Product Pod artifact now exposes `documentParsedMarketFacts` from uploaded business plans / IM documents and `documentMarketReconciliation` from `MarketEvidenceAgent`.
- Market-Product Pod AI Review now stores `documentFacts`, flags document-market reconciliation issues, and hands them back to Chief Deal Orchestrator before feasibility / PF handoff.
- Chief Deal Room UI now shows parsed document market facts inside the Pod AI Review card, including source document, product mix count, sale comparable count, rent comparable count, proposed price per py, reconciliation status, issue count, and source locator count.
- Market-Product deterministic guardrails now distinguish:
  - no comparable data,
  - thin comparable data,
  - proposed price-per-py basis missing,
  - rent / NOI / Cap Rate evidence missing for real-asset finance / REIT / fund tracks,
  - weak marketability judgement basis.
- Pod run responses expose `podAiReviewIds` for Market-Product as well as Land-Legal.

Still pending:

- PF-Credit Pod AI Review.
- Cost-Schedule, Feasibility-Cashflow, and Public-Policy Pod AI Review.
- AI Risk Committee integration using Pod AI Review findings.
- ReportStudio paragraph generation with Pod AI Review evidence links.

### 2026-07-06 Implementation Update

Implemented:

- Backend `PodAIReview`, `PodAIReviewFinding`, and `PodAIReviewHandoff` schema added in `services/api-server/schemas/orchestration.py`.
- JSON/SQLite persistence added as first-class `pod_ai_reviews` records.
- PostgreSQL bootstrap already includes `pod_ai_reviews` in the JSONB store list.
- Generic `PodAIReviewerAgent` added.
- Land-Legal Pod now creates a deterministic Pod artifact and a linked Pod AI Review record.
- Chief Pod artifact keeps a lightweight `podAiReviewId` / `podAiReview` reference.
- Chief Pod run response now exposes `podAiReviewIds`.
- API endpoints added:
  - `GET /projects/{project_id}/chief-orchestration/pod-ai-reviews`
  - `GET /projects/{project_id}/chief-orchestration/pod-ai-reviews/{review_id}`
- `AIConnection.extract_json` now attaches `ai_brain_used_event_id` to result metadata when the audit row is persisted.

### 2026-07-06 Live verification + PF-Credit + Approval Gate wiring

Verified live that Land-Legal and Market-Product Pod AI Reviews run and produce
evidence-aware findings (market_product → `proposed_price_basis_missing`,
`rent_cap_rate_evidence_missing`; land_legal → 7 findings incl. blocking
`data_quality`). Verification surfaced two gaps, both now closed:

- **PF-Credit Pod AI Reviewer** (`_pf_credit_findings`): routes the deterministic
  scenario-engine flags into reviewer findings — `SENIOR_SHORTFALL` /
  `STRESS_SENIOR_SHORTFALL` / `WATERFALL_SHORTFALL` → blocking `risk_note` +
  `recalculation_needed`; `UNSOLD_LTV_BREACH` → `risk_note`; `TRIGGER_REQUIRED` /
  mezzanine shortfall → `risk_note`; and flags missing PF structure /
  covenant·EOD·CP terms / un-run scenarios as `missing_evidence`. Wired into the
  Chief pod review set (`land_legal`, `market_product`, `pf_credit`); agent id
  `PFCreditPodAIReviewer`. It never re-decides PASS/FAIL — the deterministic
  credit decision is carried as `context` only.
- **Approval Readiness Gate ← Pod AI Review**: `build_deal_state_approval_readiness`
  adds a `POD_AI_REVIEW_BLOCKERS_CLEARED` gate that blocks readiness when the
  latest per-pod review has an unresolved **blocking** finding — but only in
  deterministic-grounded categories (`missing_evidence`, `source_locator_gap`,
  `data_quality`, `risk_note`, `recalculation_needed`). An LLM opinion alone
  (e.g. `guardrail`) never gates PASS/FAIL, per the invariant.

### 2026-07-06 All six pods reviewed

Added deterministic reviewers for the remaining three pods, so all six Chief
pods now produce a Pod AI Review (LLM-evidence-aware when a key is available,
deterministic fallback otherwise):

- **Cost-Schedule** (`_cost_schedule_findings`): routes `engineReadiness`
  (blockingFields / canRunCashFlow) and empty costBreakdown/schedule as
  missing-evidence / recalculation-needed; honestly flags that contractor-risk
  and VE outputs are not yet produced by any agent. Reads structured fields, not
  risk codes (this pod emits none). Also documents two orchestrator key-mismatch
  bugs (costBreakdown/dataQualityWarnings) to fix in engineering.
- **Feasibility-Cashflow** (`_feasibility_cashflow_findings`): routes
  `MAX_MINUS_EXCEEDS_PF_LIMIT` (blocking + recalculation_needed),
  `PF_LIMIT_FULLY_DRAWN`, negative/ below-hurdle project IRR, `pfReadiness`
  leverage review, `dataQuality.missingFields`, partial dataStatus, and
  income-asset logistics flags. Never recomputes maxMinus/IRR.
- **Public-Policy** (`_public_policy_findings`): routes developability-score
  `grade=no_data`, `dataLimitations`, missing regulation dimension, weak `gaps`,
  and the still-stubbed policy-simulation output.

All wired into the Chief pod review set (all 6 pods) with per-pod agent ids.
Verified: 8 new unit tests + live run of all three (CostSchedule/Feasibility
Cashflow/PublicPolicy reviewer ids, findings produced).

### 2026-07-06 AI Risk Committee consumes Pod AI Review findings

The AI Risk Committee (`_risk_committee_opinions`) now folds each reviewer's
accountable pods' Pod AI Review findings into its opinion:

- `_load_pod_ai_review_findings(project_id)` reads the latest unresolved review
  per pod and keeps only deterministic-grounded blocking/high findings
  (categories missing_evidence / source_locator_gap / data_quality / risk_note /
  recalculation_needed).
- `_REVIEWER_POD_MAP` routes each of the 7 reviewers to its pods (credit_reviewer
  → pf_credit/feasibility_cashflow, legal_compliance → land_legal/public_policy,
  market_stress_tester → market_product, construction_risk → cost_schedule/
  feasibility_cashflow, devils_advocate + ic_judge → all pods).
- A **blocking** deterministic finding escalates the reviewer's stance to at
  least `supports_hold` (never downgrades an existing `supports_fail`); a
  **high** finding is surfaced as a concern only. An LLM opinion alone never
  moves a stance (AGENTS.md §1). Findings + recommended actions appear in the
  reviewer's keyConcerns / requiredActions tagged with the source pod.

Verified: 3 unit tests + live (committee reviewers surface their pods'
findings; escalation on blocking). Chief regression 17 green.

### 2026-07-06 Report paragraphs carry Pod AI Review evidence

`build_paragraph_evidence_links` now attaches, per report section, the Pod AI
Review findings from the pods that back the section's category
(`_SECTION_CATEGORY_PODS`: land→land_legal, market→market_product,
feasibility/cashflow→feasibility_cashflow, pf→pf_credit, risk→pf_credit +
land_legal + public_policy). Only deterministic-grounded findings
(missing_evidence / source_locator_gap / data_quality / risk_note /
recalculation_needed) are linked; each section carries `podAiReviewFindings`
(pod/severity/message/recommendedAction) and a `podAiReviewBlocking` flag, and
the top-level result reports `podAiReviewFindingCount` /
`sectionsWithPodReviewCount`. The report page renders these as a per-section
"Pod AI Review 지적" panel (rose when blocking). Resolved/waived reviews are not
linked. Verified: 3 unit tests + live (generatedFrom
`deal_state_records+pod_ai_reviews`, pf_credit findings on all pf/risk
sections); report regression 11 green, web tsc/eslint 0.

Still pending:

- PG v2 native per-table persistence (the understand-workflow spec agent hit the
  session limit; also not live-verifiable without psycopg + a running Postgres).

Current backend inventory:

| Type | Count | Notes |
|---|---:|---|
| Python files in `services/api-server/agents` | 46 | Includes helpers, routers, policies, engines, and agents |
| Agent-like classes | 31 | Classes ending with `Agent`, `Engine`, or `Coordinator` |
| Chief Orchestrator Pods | 6 | Land-Legal, Market-Product, Cost-Schedule, Feasibility-Cashflow, PF-Credit, Public-Policy |
| Pod role agents | 30 | Role-level agents shown in the Chief task graph |
| Files with current LLM touch points | 8 | `ParserAgent`, `IntakeTriageAgent`, `ConsensusEngine`, retrieval/router/connection/audit support |

Current Pod execution:

```text
Pod button
  -> deterministic domain workflow / use case
  -> durable chief_pod_artifact
  -> project standard artifact keys
  -> event bus / recalculation state
```

Current missing layer:

```text
Pod artifact
  -> AI reviewer reads artifact + evidence + assumptions
  -> reviewer produces critique, missing evidence request, handoff, report notes
  -> aiBrainUsed audit event records provider/model/key/token usage
```

---

## 3. Core Rule

Pod AI Reviewer is not a calculator.

Allowed:

- Explain what the Pod found.
- Identify missing evidence.
- Identify contradictions between Pod artifacts.
- Draft human-readable risk notes.
- Draft next-step work orders.
- Suggest which Pod should rerun.
- Draft report paragraph candidates with evidence IDs.

Not allowed:

- Create PNU, official land price, registry status, or market comparables without source.
- Change feasibility, cash flow, LTV, LTC, DSCR, Exit sale rate, waterfall, or shortfall results.
- Waive blocking risks.
- Convert `needs_review` into `ready` without evidence or human approval.

---

## 4. Target Pod AI Reviewer Contract

Each Pod should produce a deterministic artifact and an optional AI review artifact.

```json
{
  "schemaVersion": "realytics-pod-ai-review-v1",
  "projectId": "project-id",
  "podKey": "land_legal",
  "agentId": "LandLegalPodAIReviewer",
  "sourceArtifactKeys": [],
  "inputEvidenceIds": [],
  "inputAssumptionIds": [],
  "reviewStatus": "completed",
  "confidence": 0.82,
  "findings": [
    {
      "findingId": "finding-id",
      "category": "missing_evidence",
      "severity": "high",
      "message": "PNU and official land price evidence are incomplete for some parcels.",
      "sourceLocators": [],
      "recommendedAction": "Run land ledger enrichment or upload official evidence."
    }
  ],
  "handoffs": [
    {
      "fromAgent": "LandLegalPodAIReviewer",
      "toAgent": "PFCreditPod",
      "handoffType": "blocker",
      "payload": {},
      "evidenceIds": [],
      "confidence": 0.8
    }
  ],
  "reportFragments": {
    "riskMemo": "",
    "missingDataRequest": "",
    "approvalConditionDraft": ""
  },
  "aiBrainUsedEventId": "audit-event-id"
}
```

---

## 5. Pod-Specific AI Reviewer Responsibilities

### 5.1 Land-Legal Pod AI Reviewer

Input:

- `landAgentReview`
- `landLedgerEnrichment`
- `legalPermitReview`
- `developabilityScore`
- land evidence records

AI tasks:

- Explain land readiness.
- Identify PNU, official land price, cadastral, registry, and encumbrance evidence gaps.
- Draft legal/PF missing material request.
- Draft CP suggestions.

Guardrail:

- No PNU or registry conclusion without API/evidence.

### 5.2 Market-Product Pod AI Reviewer

Input:

- `marketEvidenceReview`
- `marketabilityAnalysis`
- market comparables
- product mix and pricing assumptions

AI tasks:

- Explain market evidence quality.
- Distinguish no comparable data from missing proposed price basis.
- Identify unsupported pricing, rent, absorption, and competition assumptions.
- Draft marketability report notes.

Guardrail:

- No definitive price adequacy without source metadata and base date.

### 5.3 Cost-Schedule Pod AI Reviewer

Input:

- cost breakdown
- schedule
- contractor risk data
- data quality warnings

AI tasks:

- Explain cost/schedule readiness.
- Identify missing construction cost, indirect cost, VE, contractor, and responsible completion evidence.
- Suggest which feasibility/cashflow assumptions must be rerun.

Guardrail:

- No construction cost estimate as final unless evidence or approved assumption exists.

### 5.4 Feasibility-Cashflow Pod AI Reviewer

Input:

- `developerFeasibility`
- `feasibility`
- `returnMetrics`
- `cashFlowRunResult`

AI tasks:

- Explain developer feasibility.
- Identify calculation assumptions lacking evidence.
- Identify whether market/product or cost/schedule changes require recalculation.
- Draft feasibility report notes.

Guardrail:

- Do not calculate or modify cash flow, Max Minus, IRR, NPV, waterfall, LTV/LTC.

### 5.5 PF-Credit Pod AI Reviewer

Input:

- `pfStructure`
- `scenariosEvaluation`
- `riskRegister`
- `creditReviewDecision`

AI tasks:

- Explain lender-side credit issues.
- Draft cure options for senior shortfall, unsold LTV breach, missing collateral evidence, and covenant gaps.
- Draft CP/CS/Covenant/EOD text candidates.
- Prepare AIRiskCommittee prompts.

Guardrail:

- No waiver of senior shortfall or LTV breach by LLM opinion.

### 5.6 Public-Policy Pod AI Reviewer

Input:

- developability score
- regulation evidence
- public contribution/policy documents

AI tasks:

- Explain public policy relevance.
- Identify planning, public contribution, disclosure, and public support issues.
- Draft public sector review checklist.

Guardrail:

- No public-law conclusion without source law/regulation evidence.

---

## 6. Recommended Implementation Order

### Step 1 - Shared Pod AI Review schema

Add backend schema and storage contract:

- `PodAIReview`
- `PodAIReviewFinding`
- `PodAIReviewHandoff`
- `PodAIReviewReportFragments`

Storage target:

- JSON/SQLite MVP: `pod_ai_reviews`
- PostgreSQL target: `pod_ai_reviews` with JSONB payload and indexed `project_id`, `pod_key`, `agent_id`, `review_status`.

DB rule:

- Pod AI Review must be persisted as a first-class server record, not only embedded inside `chief_pod_artifacts`.
- `chief_pod_artifacts` may keep a lightweight reference such as `podAiReviewId`, but the full review payload belongs in `pod_ai_reviews`.
- Each review row must carry `projectId`, `podKey`, `sourceArtifactKey`, `agentId`, `reviewStatus`, `aiBrainUsedEventId`, `createdAt`, `updatedAt`, and full JSON payload.
- The row becomes part of Shared Deal State Graph, Evidence Graph, Risk Register, ReportStudio, and Historical Similar Case Search.

Target table contract:

```sql
CREATE TABLE IF NOT EXISTS pod_ai_reviews (
  project_id TEXT NOT NULL,
  review_id TEXT NOT NULL,
  pod_key TEXT,
  source_artifact_key TEXT,
  agent_id TEXT,
  review_status TEXT,
  ai_brain_used_event_id TEXT,
  created_at TEXT,
  updated_at TEXT,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(project_id, review_id)
);

CREATE INDEX IF NOT EXISTS idx_pod_ai_reviews_project_pod
  ON pod_ai_reviews(project_id, pod_key, updated_at);

CREATE INDEX IF NOT EXISTS idx_pod_ai_reviews_project_status
  ON pod_ai_reviews(project_id, review_status, updated_at);
```

PostgreSQL target uses the same logical fields with a JSONB payload:

```sql
CREATE TABLE IF NOT EXISTS pod_ai_reviews (
  id TEXT PRIMARY KEY,
  project_id TEXT,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pod_ai_reviews_project
  ON pod_ai_reviews(project_id);
```

### Step 2 - PodAIReviewerAgent

Add one generic agent:

```text
services/api-server/agents/pod_ai_reviewer_agent.py
```

It should:

- Read pod key and source artifact.
- Build a concise evidence-aware prompt.
- Use `AIConnection.extract_json`.
- Persist `aiBrainUsed`.
- Fall back to deterministic review if no LLM key is available.

### Step 3 - Wire into Chief Pod execution

After `_execute_pod_artifact` creates the deterministic artifact:

```text
deterministic artifact
  -> PodAIReviewerAgent.review(...)
  -> pod_ai_review artifact
  -> chief_pod_artifact reference
  -> Shared Deal State Graph update
```

### Step 4 - UI exposure

Chief Deal Room should show:

- deterministic Pod artifact status
- AI review status
- AI reviewer confidence
- missing evidence count
- handoffs
- aiBrainUsed link

### Step 5 - AIRiskCommittee integration

AI Risk Committee should read:

- deterministic artifacts
- Pod AI Review findings
- Risk Register
- Evidence Graph
- Assumption Book

The committee should not rerun calculations. It should challenge assumptions, classify risks, and draft approval/rework/fail logic.

---

## 7. First Pod to Implement

Start with **Land-Legal Pod AI Reviewer**.

Reason:

- Current pilot blocker is land evidence: PNU, official land price, cadastral, registry/encumbrance.
- Land-Legal already produces real deterministic artifacts.
- AI can add immediate value by explaining missing evidence and producing work orders without fabricating facts.
- PF-Credit can use the result as a gate condition.

Second:

- Market-Product Pod AI Reviewer.

Reason:

- Current pilot also has market/pricing evidence gaps.
- It should distinguish `NO_COMPARABLE_DATA` from `PROPOSED_PRICE_BASIS_MISSING`.

Third:

- PF-Credit Pod AI Reviewer.

Reason:

- It should draft cure paths for senior shortfall and unsold LTV breach after deterministic calculations are available.

---

## 8. Acceptance Criteria

P0 completion:

- [ ] `PodAIReview` schema exists.
- [ ] Generic `PodAIReviewerAgent` exists.
- [ ] `Land-Legal Pod` writes deterministic artifact and AI review artifact.
- [ ] AI review stores `ai_brain_used` audit event.
- [ ] AI review does not fabricate PNU, official land price, registry, or encumbrance conclusion.
- [ ] Chief Deal Room shows AI review status.
- [ ] Missing evidence requests are visible to user.

P1 completion:

- [ ] Market-Product AI review implemented.
- [ ] Feasibility-Cashflow AI review implemented.
- [ ] PF-Credit AI review implemented.
- [ ] AIRiskCommittee reads Pod AI Review findings.
- [ ] ReportStudio can use AI review report fragments with evidence links.

P2 completion:

- [ ] Cost-Schedule AI review implemented.
- [ ] Public-Policy AI review implemented.
- [ ] Historical similar case search uses Pod AI Review findings.
- [ ] Human feedback updates reviewer prompt policy.
