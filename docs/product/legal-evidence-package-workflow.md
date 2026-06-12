# Legal Evidence Package Workflow

Updated: 2026-06-12

This workflow prevents pilot approval blockers from being cleared by assumption alone. It converts the remaining legal, permit, and covenant blockers into a source-backed evidence package before any `resolved`, `approved`, or `waived` review action can be applied.

## Purpose

The legal evidence package is used after Data Quality and financial cure reruns, when the remaining blockers are reviewer-controlled legal or permit issues.

Current pilot scope:

| Risk code | Required evidence |
|---|---|
| `PERMIT_SCHEDULE_READINESS` | Permit schedule, permit application receipt, approval expected date evidence, delay buffer analysis |
| `LEGAL_LAND_RIGHTS` | Registry record, encumbrance release condition, ownership transfer evidence, trust registration evidence |
| `COVENANT_AND_LEGAL_PACKAGE_COMPLETENESS` | Loan agreement draft, trust agreement, responsible completion letter, covenant/EOD checklist, legal opinion |

## Runtime Flow

```text
pilot full run
  -> review action template
  -> legal evidence package template
  -> filled legal evidence package
  -> review actions generated from evidence
  -> apply review actions
  -> blocker package rerun
```

## Commands

Generate review actions from the latest full run:

```powershell
..\..\.runtime\api-venv\Scripts\python.exe scripts\apply_pilot_review_actions.py `
  --full-run ..\..\.runtime\pilot-latest\pilot-dq-collateral-cure-full-run.json `
  --template-output ..\..\.runtime\pilot-latest\pilot-review-actions-template.json
```

Generate the legal evidence package template:

```powershell
..\..\.runtime\api-venv\Scripts\python.exe scripts\build_pilot_legal_evidence_package.py `
  --review-template ..\..\.runtime\pilot-latest\pilot-review-actions-template.json `
  --output ..\..\.runtime\pilot-latest\pilot-legal-evidence-package-template.json
```

After filling the package with source-backed evidence, generate review actions:

```powershell
..\..\.runtime\api-venv\Scripts\python.exe scripts\build_pilot_legal_evidence_package.py `
  --review-template ..\..\.runtime\pilot-latest\pilot-review-actions-template.json `
  --legal-evidence-package ..\..\.runtime\pilot-latest\pilot-legal-evidence-package-filled.json `
  --review-actions-output ..\..\.runtime\pilot-latest\pilot-review-actions-from-legal-evidence.json
```

Apply the generated review actions:

```powershell
..\..\.runtime\api-venv\Scripts\python.exe scripts\apply_pilot_review_actions.py `
  --full-run ..\..\.runtime\pilot-latest\pilot-dq-collateral-cure-full-run.json `
  --review-actions ..\..\.runtime\pilot-latest\pilot-review-actions-from-legal-evidence.json `
  --output ..\..\.runtime\pilot-latest\pilot-reviewed-full-run.json `
  --blocker-package-output ..\..\.runtime\pilot-latest\blocker-resolution-package-reviewed.json `
  --blocker-package-md-output ..\..\.runtime\pilot-latest\blocker-resolution-package-reviewed.md
```

## Evidence Rules

- Empty evidence rows do not clear risks.
- `resolved` is generated only when every required evidence type for that risk has source metadata.
- Required source metadata: `evidenceId`, `sourceName`, `sourceUrlOrEvidenceId`, and either `issuedAt` or `baseDate`.
- `waived` is never generated automatically from this package.
- Handoffs outside the legal evidence scope, such as marketability-to-feasibility handoff, remain `needs_revision`.
- Runtime package files under `.runtime/` are not committed to GitHub.

## Non-Fabrication Rule

Do not mark legal, permit, land-right, covenant, or EOD blockers as resolved without actual uploaded evidence, document locator, Excel cell, API row, or counsel-approved waiver reason.
