# Excel Workbook Learning Agent

Updated: 2026-06-04

## 1. Purpose

The Excel Workbook Learning Agent helps REALYTICS absorb new feasibility-study and cash-flow workbook formats without turning every upload into an unsafe parser change.

Its job is not to let an LLM rewrite import code directly. Its job is to profile an uploaded workbook, compare it with known workbook templates, propose mapping candidates, preserve source locators, let an LLM judge the mapping, and then advance the workbook through automated validation, shadow-mode import, regression tests, and monitored production import.

The target direction is LLM-first continuous improvement. Human review becomes an exception path for ambiguous, low-confidence, or high-impact failures rather than the default operating mode.

## 2. Why This Matters

Korean real estate development workbooks are not standardized. Two files may both be called "business feasibility" while using different sheet names, unit bases, merged cells, formula structures, and PF cash-flow logic.

If the system imports these files blindly, the most dangerous failures are:

- total sales revenue imported from a visual subtotal instead of the real formula total;
- KRW thousand, KRW million, and KRW 100m unit basis confused;
- PF amount imported from a draw schedule instead of approved facility amount;
- formula cells lost during export;
- new workbook pattern silently changing the canonical FeasibilityStudyV2 schema;
- report and credit decision using values with no Excel cell locator.

The learning workflow prevents this by making every new workbook pattern pass through profiling, candidate generation, LLM judgement, automated validation, shadow-mode import, and regression testing.

## 3. Target Workflow

```text
Workbook upload
    -> WorkbookProfiler
    -> Template similarity scoring
    -> Mapping candidate generation
    -> LLM judgement
    -> Automated validation
    -> Shadow-mode import
    -> Regression fixture pass
    -> Template registry update
    -> FeasibilityStudyV2 importer promotion
```

## 4. Agent Boundary

The agent may:

- inspect workbook structure, sheets, formulas, named ranges, merged cells, and preview cells;
- compare the workbook against known REALYTICS and legacy sample profiles;
- propose candidate mappings to FeasibilityStudyV2 paths;
- create LLM prompt context for mapping judgement and additional mapping suggestions;
- let the LLM reject ambiguous mappings or advance high-confidence mappings into shadow mode;
- attach Excel source locators such as `사업수지!D11`, named ranges, and workbook hash metadata;
- suggest validation checks and test fixtures.

The agent must not:

- mark a mapping approved by itself;
- mutate parser code directly;
- overwrite project values;
- fabricate missing source data;
- treat an LLM judgement as production behavior without validation, regression, audit, and rollback;
- expose sensitive owner/account data in logs or report output.

## 5. Output Contract

The current implementation is `realytics-excel-workbook-learning-v1`.

Core fields:

| Field | Meaning |
|---|---|
| `workbookProfile` | Sheet-level dimensions, formula counts, merged ranges, freeze panes, preview cells, and named ranges |
| `templateCandidates` | Similarity scores against known workbook profiles |
| `mappingCandidates` | Candidate schema mappings with value, formula, source locator, confidence, and LLM/automation gate status |
| `validationPlan` | Required checks before import promotion |
| `llmJudgementPlan` | Required LLM decision structure for mapping acceptance, rejection, or shadow-mode promotion |
| `automationDecision` | Deterministic pre-check for whether the workbook can enter autonomous shadow mode |
| `continuousLearningPlan` | Feedback signals and promotion path for improving workbook templates over time |
| `llmPromptContext` | Safe context for LLM-assisted mapping suggestions |
| `safetyPolicy` | Explicit allowed and forbidden LLM actions |
| `warnings` | Reasons the workbook cannot be auto-imported |
| `nextActions` | LLM judgement, validation, shadow-mode, and regression steps before registration |

## 6. Status Lifecycle

```text
profiled
candidate_generated
llm_judgement
shadow_mode_import
regression_ready
registered
monitored_production
```

`candidate_generated` means mappings were proposed. It does not mean the workbook can be imported as canonical project data. The LLM must judge the mapping and the automated validation/regression path must pass before the mapping can be registered.

## 7. Template Registry Direction

Known templates should eventually be persisted as server-side records:

```json
{
  "templateId": "legacy-bonri-mixed-use-pf-feasibility",
  "version": "1.0.0",
  "acceptedBy": "llm-judge-or-automation-policy-id",
  "acceptedAt": "2026-06-04T00:00:00Z",
  "requiredSheets": [],
  "requiredNamedRanges": [],
  "mappingRules": [],
  "validationRules": [],
  "regressionFixtureIds": []
}
```

The MVP keeps deterministic known templates in code. Production should move them to an LLM-governed template registry backed by PostgreSQL, object storage fixtures, validation history, and rollback metadata.

## 8. Required Validation Checks

Every LLM-accepted workbook mapping must check:

- original file SHA-256 is preserved;
- formula workbook and value workbook can both be opened;
- required sheets or named ranges exist;
- source locator exists for every mapped value;
- total revenue, total cost, developer profit, PF amount, and Max Minus reconcile;
- unit basis is explicit;
- sensitive account or owner data is masked before logs, exports, or LLM context;
- a regression fixture covers the new mapping.

## 9. LLM-First Learning Policy

LLM usage is the primary judgement layer, but it is not allowed to bypass deterministic validation and regression checks.

Good LLM tasks:

- "Find likely total revenue cells from sheet previews."
- "Suggest validation formulas for this workbook pattern."
- "Propose FeasibilityStudyV2 mapping candidates with confidence."
- "Explain why a mapping is ambiguous."
- "Reject this workbook mapping because the unit basis is unclear."
- "Advance this workbook to shadow mode because template, locator, and reconciliation checks are strong."

Forbidden LLM tasks:

- "Deploy this parser change."
- "Guess missing PF amount."
- "Ignore unit basis."
- "Convert a stress scenario into a base case."
- "Skip regression tests."
- "Overwrite project data without shadow-mode comparison."

## 10. Current Implementation

Implemented:

- `services/api-server/agents/excel_workbook_learning_agent.py`
- deterministic workbook profiling;
- similarity scoring against the legacy Bonri sample profile and REALYTICS formula export profile;
- mapping candidate generation with Excel source locators;
- LLM judgement plan and prompt context generation without making a live LLM call;
- autonomous promotion pre-checks for source locator coverage, mapping confidence, required mapping coverage, and template similarity;
- tests for legacy-like workbooks and unknown workbooks.

Next:

- persisted workbook template registry;
- LLM provider hook with redacted workbook context and structured judgement output;
- shadow-mode import comparison UI/API;
- approved-template regression fixture storage;
- direct connection to FeasibilityStudyV2 monitored import workflow.

## 11. Acceptance Criteria

- A newly uploaded workbook can be profiled without mutating project data.
- The result shows sheet/formula/named-range structure.
- The system can identify known workbook templates by similarity.
- Mapping candidates include schema path, value type, value/formula, confidence, and source locator.
- Unknown workbook formats remain in LLM judgement / needs-more-evidence state.
- No candidate is treated as production-ready without LLM judgement, automated validation, regression pass, and audit metadata.
- A regression test exists before the mapping can become automatic import behavior.
