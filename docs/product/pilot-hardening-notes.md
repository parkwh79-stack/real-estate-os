# Pilot Hardening Notes

Updated: 2026-05-27

These notes preserve the issues found during the first end-to-end Daegu mixed-use PF pilot run.

## Findings

1. `qualityCheck` was too shallow.
   - `qualityCheck.pass=true` only proved report/artifact completeness.
   - Risk Register could still be `blocking`.
   - Credit Review could still be `reject_recommended`.
   - Action: split report completeness from approval readiness.

2. Marketability risk wording was too broad.
   - When comparables exist but proposed price per py cannot be derived, the issue is not `NO_COMPARABLE_DATA`.
   - Action completed: `PROPOSED_PRICE_BASIS_MISSING` is now emitted when comparables exist but target pricing basis is missing, and proposed price per py is derived from product mix total revenue/area when possible.

3. Risk source locators were incomplete.
   - Evidence Graph creates risk nodes, but many risks only link to artifacts/claims.
   - Action completed for baseline pilot gating: Marketability, Scenario, Data Quality, Land, Legal, Cash Flow, PF Structure, and Risk Matrix risks now carry explicit `sourceLocators`. When a document page, Excel cell, API row, or evidence ID is not yet available, Risk Register attaches an `agent_artifact` fallback locator and marks it as `locatorPrecision=artifact_level`.

4. Similar Case risk matching was too broad.
   - Category/severity fallback matched unrelated risk rows.
   - Action completed: exact risk code matching is now required when a query risk has a code; category/severity fallback only runs for query risks without a code. A regression test now prevents broad same-category/high-severity matches from being counted as the same risk.

5. Historical cases need actual outcomes.
   - Similar-case search can explain historical review handling, but not yet whether the case performed well.
   - Action: add actual sales, discount, repayment, extension/default/EOD, and recovery/loss outcomes.

## Implemented First

`realytics-approval-readiness-gate-v1` was added as a separate orchestration artifact.

It checks:

- report completeness quality gate,
- legal blocking status,
- data-quality blocking status,
- cross-agent reconciliation blocking status,
- Risk Register blocking status,
- Credit Review reject/rework decisions,
- unresolved Agent handoff reviews,
- unresolved material Risk Register reviews,
- land evidence readiness warnings,
- risk source locator warnings.

This lets `qualityCheck` remain a report completeness gate while `approvalReadinessGate` answers whether a run is actually ready for credit approval.

## Implemented Second

Marketability pricing-basis hardening was added.

It now separates:

- `NO_COMPARABLE_DATA`: no usable transaction, presale, listing, or manual comparable evidence exists.
- `PROPOSED_PRICE_BASIS_MISSING`: comparable evidence exists, but the target project's proposed price per py cannot be derived.

The Agent first tries to derive proposed price per py from product mix `totalRevenue / (units * contractAreaPy)`, then from the weighted product mix price, then from explicit `market.proposedPricePerPy`. This prevents the pilot issue where a pricing-basis problem was mislabeled as a comparable-data problem.

## Implemented Third

Risk source locator propagation was strengthened.

The first pass now attaches:

- Marketability pricing risk locators for proposed price, comparable median, comparable source locator, and product mix fields.
- Scenario Engine locators for waterfall shortfall rows, tranche rows, unsold LTV matrix cells, and breach-count summary fields.
- Data Quality locators already generated from conflicting source values are preserved by the Risk Register.
- Evidence Graph summary now reports `sourceLocatorCount` and `riskRowsWithSourceLocatorCount`.
- Every Risk Register row now has at least one explicit locator. Rows with direct source evidence keep document/API/scenario locators; generated risks without source evidence receive an `agent_artifact` fallback locator so auditors can trace the responsible artifact and required next evidence.

Remaining work is to replace fallback artifact-level locators with document-page, Excel-cell, API-row, or registry/official-price evidence locators whenever source files or connectors are attached.

## Implemented Fourth

Land evidence readiness was hardened for pilots and partial integrations.

PF review orchestration now accepts a flat `landEvidence` package when a full `landLedger` import is not yet attached. The package can include:

- `officialLandPrice`,
- `officialLandPriceBaseYear`,
- `officialLandPriceEvidence`,
- `registryEvidence` or `encumbranceEvidence`,
- `sourceMetadata`,
- optional `acquisitionAreaRatio`.

If PNU, official land price evidence, registry evidence, and land acquisition ratio are complete, Land Agent marks the patch as `ready_for_pf_review`. This lets a pilot run distinguish "no evidence attached" from "evidence attached outside the ledger importer."

## Implemented Fifth

Review actions now refresh approval readiness.

When a reviewer approves/rejects/revises an Agent handoff or approves/waives/resolves a Risk Register row, the stored orchestration run refreshes `approvalReadinessGate`. The UI and API therefore show the current unresolved handoff/risk counts after each review action instead of leaving the original gate result stale.
