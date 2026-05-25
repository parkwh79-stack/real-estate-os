# Credit Review Agent

Updated: 2026-05-25

## Purpose

Credit Review Agent is the lender-side decision layer for REALYTICS x PLATFORM9.

It consumes the consolidated Risk Register and key upstream artifacts, then produces a structured credit opinion:

- `approve`: approval possible,
- `conditional_approval`: conditional approval possible,
- `rework_required`: rework and resubmission required,
- `reject_recommended`: rejection recommended.

The Agent does not replace a credit committee. It prepares a deterministic decision package with reasons, key risks, mitigants, approval conditions, required waivers, and report paragraphs.

## Current Schema

Current schema version:

```text
realytics-credit-review-v1
```

Current output:

| Field | Description |
|---|---|
| `decision` | Machine-readable credit decision |
| `decisionLabel` | Korean decision label |
| `status` | `clear`, `conditional`, `requires_rework`, or `reject_recommended` |
| `summary` | Risk, legal, data quality, marketability, and PF metric summary |
| `decisionReasons` | Reasons used to select the decision |
| `keyRisks` | Top risk rows selected from Risk Register |
| `mitigants` | Risk mitigants and covenant support |
| `approvalConditions` | Candidate approval conditions |
| `requiredFollowUps` | Items that must be resolved or reviewed |
| `requiredWaivers` | Waivers required if approval proceeds despite unresolved risks |
| `reportParagraphs` | Credit opinion text for PF review report integration |
| `humanReview` | Whether a credit officer must review the decision package |

## Orchestration Position

Credit Review Agent runs after Risk Register Agent and before Phase 11 report generation.

```text
Risk Assessment
  -> Risk Register Agent
  -> Credit Review Agent
  -> Phase 11 Report
  -> Evidence Graph
  -> Quality Gate
```

The baseline orchestration stores:

- `creditReviewDecision` artifact,
- `credit_review_to_report` handoff,
- `credit_review_decision` progress step.

## Input Artifacts

Credit Review Agent currently reads:

- `riskRegister`
- `legalPermitReview`
- `dataQualityReview`
- `reconciliationGate`
- `marketabilityAnalysis`
- `pfStructure`

## Current Decision Logic

Baseline deterministic rules:

| Condition | Decision |
|---|---|
| Legal blocking, data quality blocking, or reconciliation blocking | `rework_required` |
| Three or more critical risks | `rework_required` |
| Senior shortfall critical scenario | `reject_recommended` |
| Exit sale rate above 85% | `rework_required` |
| Marketability grade D or E | `rework_required` |
| High risk register status or high risks exist | `conditional_approval` |
| Data quality or legal caution exists | `conditional_approval` |
| No material issue | `approve` |

## Important Rule

Credit Review Agent must not hide unresolved risk. If the decision is conditional or worse, it must preserve the reason, source risk, required follow-up, and approval-condition candidate.

## Next Steps

1. Feed `creditReviewDecision.reportParagraphs` into the Phase 11 report sections.
2. Add institution-specific credit policy profiles.
3. Add reviewer approval, waiver, and condition tracking per decision.
4. Persist credit decisions for historical similar-case comparison.
5. Add Credit Review UI actions for final reviewer memo and committee status.
