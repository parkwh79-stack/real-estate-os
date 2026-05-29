# Pilot Project Runbook

Updated: 2026-05-28
Project: REALYTICS x PLATFORM9
Purpose: Run a real pilot project through the PF review orchestration with traceable inputs, evidence, review gates, and remediation actions.

## 1. Pilot Objective

The pilot should answer four questions.

1. Can the system ingest a real project package and create a usable project record?
2. Can the agents produce a complete PF review run with evidence-linked artifacts?
3. Can reviewers understand why approval is blocked or conditionally possible?
4. Can the run produce a reusable snapshot for future similar-case search?

The pilot is not considered successful only because a report is generated. A successful pilot must show the full path from input data to approval readiness, including unresolved blockers.

## 2. Pilot Scope

Recommended first pilot type:

| Item | Recommendation |
|---|---|
| Asset type | Mixed-use, apartment, officetel, or other saleable project |
| Primary exit | Presale or unit sale proceeds |
| Documents | Business plan, architectural summary, feasibility workbook, land ledger |
| Evidence depth | Manual evidence package is acceptable for first pilot |
| External APIs | Optional; do not block pilot if official adapters are not configured |
| Approval target | Conditional approval candidate, not automatic approval |

Avoid starting the first pilot with logistics, hotel, data center, or operating income assets unless the goal is specifically to test non-saleable asset logic.

## 3. Required Input Package

Create one pilot folder outside Git runtime storage.

```text
pilot-input/
  01_business_plan.pdf
  02_architectural_summary.pdf
  03_feasibility_workbook.xlsx
  04_land_ledger.xls
  05_market_comparables.xlsx
  06_land_evidence/
    official_land_price.pdf
    registry_record.pdf
    cadastral_record.pdf
  07_pf_termsheet.pdf
  pilot_input_payload.json
```

The minimum API payload should follow:

```text
services/api-server/data/fixtures/pilot_project_input_package_template.json
```

## 4. Mandatory Data Fields

### 4.1 Project and Site

| Field | Required | Notes |
|---|---:|---|
| project.name | Yes | Must match documents or be manually selected as authoritative |
| project.type | Yes | Use mixed_use, apartment, officetel, commercial, kic, logistics, etc. |
| project.location | Yes | Address or legal dong level |
| land.area_sqm | Yes | Used for land reconciliation |
| land.pnu | Preferred | If missing, use land-ledger enrich with legal dong code |
| landEvidence | Preferred | Needed to clear land evidence warnings |

### 4.2 Development Feasibility

| Field | Required | Notes |
|---|---:|---|
| feasibility_input.total_revenue | Yes | Must reconcile with product mix |
| feasibility_input.total_cost | Yes | Includes land, construction, indirect, finance, contingency |
| feasibility_input.finance_cost | Yes | Needed for PF and report consistency |
| product_mix | Yes | Type, units, area, unit price, total revenue |
| schedule | Preferred | Construction, sales, completion, maturity dates |

### 4.3 Marketability Evidence

| Field | Required | Notes |
|---|---:|---|
| market.manualComparables | Yes | At least 2 comparables for first pilot |
| market.proposedPricePerPy or product_mix area | Yes | Prevents pricing basis gaps |
| source metadata | Yes | sourceName, sourceType, baseDate, confidence |
| evidence locator | Preferred | page, table, row, API row, or Excel cell |

### 4.4 PF Structure

| Field | Required | Notes |
|---|---:|---|
| pf_structure | Yes | Tranche, amount, priority, interest, fee |
| covenants.discount_triggers | Preferred | 6/12/18 or 6/12/24 month triggers |
| covenants.claim_preservation | Preferred | Trust, completion, guarantee, cash sweep, reserve |
| waterfall assumptions | Preferred | Repayment priority and account allocation |

## 5. Evidence Policy

The pilot must preserve where key numbers came from.

| Evidence Type | Minimum Locator |
|---|---|
| PDF source | file name and page number |
| Excel source | workbook name, sheet name, and cell/range |
| API source | API name, request key, base date, and row identifier |
| Manual estimate | analyst name, base date, reason, confidence |
| Agent output | run ID, artifact key, field path |

If the system only has an `agent_artifact` fallback locator, the run can continue but should remain under review until precise document/API/cell evidence is attached.

## 6. Execution Steps

### Step 1. Prepare Input Package

- Collect the required documents.
- Create or update `pilot_input_payload.json`.
- Mask account numbers, resident IDs, and unnecessary personal information.
- Decide which document is authoritative if the same number appears differently across files.

Local workspace helper:

```text
cd services/api-server
python scripts/prepare_pilot_workspace.py --sample-dir "C:\Users\User\Documents\real estate os\sample" --overwrite
```

This creates a pilot workspace outside Git with:

- `pilot_input_payload.json`
- `source_document_manifest.json`
- `README.md`
- `outputs/`

By default, the workspace is created under `services/api-server/data/runtime/pilot-input`, which should stay out of Git. Source documents are referenced by path and hash but are not copied. Use `--copy-samples` only when the workspace is allowed to hold the original documents.

Sample-derived payload helper:

```text
cd services/api-server
python scripts/update_pilot_input_from_samples.py
```

This reads the local Bonri feasibility workbook, land ledger, and PF review DOCX, then updates `data/runtime/pilot-input/pilot_input_payload.json` with extracted project, land, feasibility, product mix, market benchmark, PF tranche, covenant, source document, and source locator values. It does not fabricate official land price or registry evidence; those remain enrichment/manual evidence tasks.

Parcel-level land evidence request:

```text
cd services/api-server
python scripts/build_land_evidence_request.py
```

Outputs:

- `data/runtime/pilot-input/outputs/land-evidence-request.json`
- `data/runtime/pilot-input/outputs/land-evidence-request.md`
- `data/runtime/pilot-input/outputs/land-evidence-template.json`

After official land price, cadastral, and registry fields are filled in the template, attach them back to the pilot payload:

```text
cd services/api-server
python scripts/apply_land_evidence_to_pilot.py --evidence data/runtime/pilot-input/outputs/land-evidence-template.json
```

The attachment step only applies non-empty source-backed records. Generated PNU alone does not clear land review.

Configured API connector option:

```text
cd services/api-server
python scripts/fetch_land_evidence_from_connectors.py --base-year 2026
```

Set `REALYTICS_CADASTRAL_URL_TEMPLATE`, `REALYTICS_OFFICIAL_LAND_PRICE_URL_TEMPLATE`, and the relevant API key environment variable before running. See `docs/product/land-data-api-connector.md`.

### Step 2. Create Project

API:

```text
POST /projects/
```

Expected result:

- Project is created.
- Project ID is recorded in the pilot log.
- Source files remain outside Git unless anonymized and intentionally added as fixtures.

### Step 3. Upload Business Plan or Run Direct Payload

API options:

```text
POST /projects/{project_id}/upload
POST /projects/{project_id}/upload-and-run
POST /projects/{project_id}/orchestrations/pf-review
```

For first controlled pilot, prefer direct project payload plus manual evidence. Use upload parsing as a secondary test after the deterministic run passes.

### Step 4. Enrich Land Data

API:

```text
POST /projects/{project_id}/land-ledger/enrich
```

Expected checks:

- PNU coverage is complete or clearly marked as missing.
- Official land price has base year and source evidence.
- Registry evidence is attached or clearly blocked.
- Land Agent status reaches `ready_for_pf_review` if evidence is complete.

### Step 5. Run PF Review Orchestration

API:

```text
POST /projects/{project_id}/orchestrations/pf-review
```

Local CLI smoke:

```text
cd services/api-server
python scripts/run_pilot_fixture.py --input data/fixtures/pilot_project_input_package_template.json --output data/runtime/pilot-summary.json
```

The CLI prints a compact summary with run status, completed step count, report quality, approval readiness blockers, Credit Review decision, Risk Register status, land readiness, restructuring recommendations, and Evidence Graph counts.

Expected artifacts:

- `landAgentReview`
- `developerFeasibility`
- `marketEvidenceReview`
- `marketabilityAnalysis`
- `dataQualityReview`
- `reconciliationGate`
- `legalPermitReview`
- `riskRegister`
- `creditReviewDecision`
- `restructuringPlan`
- `phase11Report`
- `evidenceGraph`
- `qualityCheck`
- `approvalReadinessGate`

### Step 6. Review Gates

Use this interpretation:

| Gate | Meaning |
|---|---|
| qualityCheck.pass | Report/artifact completeness only |
| approvalReadinessGate.pass | Credit approval readiness |
| creditReviewDecision.decision | Lender-side decision guidance |
| riskRegister.status | Consolidated risk status |
| restructuringPlan.approvalPath | Numerical cure path for rejection-driving risks |

Do not treat `qualityCheck.pass=true` as approval.

### Step 7. Human Review Actions

Review and approve, waive, revise, or resolve:

- Required agent handoffs
- Material Risk Register rows
- Data Quality conflicts
- Land evidence issues
- Market evidence assumptions
- Restructuring cure assumptions

### Step 8. Snapshot and Similar Case Search

After each run:

```text
GET /projects/{project_id}/snapshots
POST /projects/{project_id}/snapshots/similar-search
POST /projects/{project_id}/snapshots/diff
```

Expected output:

- Snapshot exists for the run.
- Similar case search returns prior risk treatments if available.
- Snapshot diff explains changed assumptions if the project is rerun.

## 7. Success Criteria

### 7.1 Technical Success

- PF review orchestration completes without server error.
- 20-section Phase 11 report is generated.
- Evidence Graph is generated.
- Risk Register is generated.
- Approval Readiness Gate is generated.
- Project Snapshot is generated.
- Scenario restructuring plan is generated.

### 7.2 Analytical Success

- Major figures reconcile or are flagged:
  - total revenue
  - total cost
  - land area
  - PF amount
  - tranche total
  - product mix revenue
- Marketability result distinguishes comparable-data gaps from pricing-basis gaps.
- Land status is either `ready_for_pf_review` or has specific missing evidence.
- Credit Review explains approval, conditional approval, rework, or reject.
- Restructuring plan quantifies cure actions for senior shortfall or unsold LTV breach.

### 7.3 Review Success

- Every unresolved blocker has an owner and next action.
- Every waiver has reviewer, reason, and timestamp.
- Any approval condition is tied to a risk row or evidence source.
- Any manual assumption has source metadata.

## 8. Pilot Decision Outcomes

| Outcome | Meaning |
|---|---|
| proceed_to_review | Run is complete and ready for human committee review |
| conditional_approval_candidate | Credit decision can proceed if listed conditions are satisfied |
| rework_required | Material assumptions, evidence, or structure must be changed and rerun |
| reject_recommended | Senior repayment or other critical risk remains uncured |
| data_package_incomplete | Input package cannot support meaningful review |

## 9. Common Failure Modes

| Failure | Likely Cause | Fix |
|---|---|---|
| qualityCheck passes but approvalReadiness fails | Report complete, credit not ready | Review approvalReadiness blockers |
| LAND_EVIDENCE_READY warning | Missing PNU, official price, registry, or acquisition evidence | Attach `landEvidence` or land ledger enrichment output |
| PROPOSED_PRICE_BASIS_MISSING | Comparable exists but target price basis is missing | Add product mix area or proposed price per py |
| UNSOLD_LTV_BREACH | PF exposure too high against discounted unsold value | Reduce PF, add equity/collateral, forward sale, bulk sale, or refinancing evidence |
| SENIOR_SHORTFALL | Senior repayment cash flow is insufficient | Reduce senior, add DSRA/equity, strengthen waterfall, rerun |
| unresolved handoff reviews | Agent handoff requires human review | Approve, reject, or mark needs revision |
| unresolved risk reviews | Material risks need review | Approve, waive, resolve, or revise |

## 10. First Pilot Recommendation

Use a controlled deterministic run before live API connectors:

1. Create project with direct payload.
2. Attach manual land evidence package.
3. Attach manual market comparables.
4. Run PF orchestration.
5. Review approvalReadiness blockers.
6. Apply a restructuring scenario if needed.
7. Rerun and compare snapshots.
8. Only then test PDF/Excel parser upload against the same project.
