# Pilot Run - Daegu Mixed-use PF Review

Updated: 2026-05-27

## Purpose

This pilot checks whether the current REALYTICS x PLATFORM9 orchestration can run an example PF review end to end and whether the newer audit/comparison layers expose useful gaps.

The pilot is not an external-data validation. It uses an anonymized in-memory Daegu mixed-use PF example with manual comparable evidence and no live public API calls.

## Scenario

### Base Case

- Project type: mixed-use residential
- Location: Daegu Dalseo Bondong
- Total revenue: KRW 150.0bn
- Total cost: KRW 120.0bn
- PF amount: KRW 70.0bn
- Tranches: Senior KRW 50.0bn, Mezzanine KRW 15.0bn, Junior KRW 5.0bn
- Manual comparables: 2 cases
- Discount triggers: 6 months 40%, 12 months 60%, 18 months 80%

### Stress Case

- Total revenue reduced to KRW 138.0bn
- Total cost increased to KRW 124.0bn
- Finance cost increased to KRW 12.0bn
- PF amount increased to KRW 80.0bn
- Added market-risk item for widened price premium

### Historical Similar Case

- Same project type and location
- Total revenue KRW 148.0bn
- PF amount KRW 72.0bn
- Used as a stored project snapshot for similar-case comparison

## Pilot Results

| Item | Base | Stress |
|---|---:|---:|
| Orchestration status | completed | completed |
| Quality gate pass | true | true |
| Phase 11 sections | 20 | 20 |
| Risk Register status | blocking | blocking |
| Risk count | 18 | 21 |
| Credit Review decision | reject_recommended | reject_recommended |
| Evidence Graph nodes | 95 | 102 |
| Evidence Graph risk nodes | 18 | 21 |
| Unlinked report sections | 0 | 0 |
| Risk rows without explicit evidence | 18 | 20 |

## 2026-05-26 Current Runtime Check

After the hardening pass, the pilot was checked again in the local environment.

### Agent-level pilot run

The in-memory Daegu Bondong example can run through `OrchestrationAgent` without the FastAPI server.

| Check | Result |
|---|---:|
| Orchestration status | completed |
| Workflow steps | 17 |
| Completed steps | 17 |
| Phase 11 sections | 20 |
| Report completeness quality pass | true |
| Approval readiness pass | false |
| Approval readiness status | not_ready_for_approval |
| Risk Register status | blocking |
| Risk count | 16 |
| Credit Review decision | reject_recommended |
| Evidence Graph source locator count | 11 |
| Risk rows with source locators | 7 |
| Risk rows without explicit evidence | 9 |

Interpretation:

- The pilot orchestration engine itself is runnable.
- The report/export artifact path can produce a complete 20-section report artifact.
- The new `approvalReadinessGate` correctly blocks approval even when report completeness passes.
- Evidence locator coverage improved materially, but still needs Land/Legal/PF structure locator hardening.

## 2026-05-27 Hardening Recheck

The pilot was rerun after source-locator and land-evidence hardening.

### Baseline Daegu run

| Check | Result |
|---|---:|
| Orchestration status | completed |
| Workflow steps | 17 |
| Completed steps | 17 |
| Approval readiness pass | false |
| Approval readiness status | not_ready_for_approval |
| Approval blocking issue count | 4 |
| Approval warning count | 1 |
| Risk Register status | blocking |
| Risk count | 16 |
| Credit Review decision | reject_recommended |
| Evidence Graph source locator count | 20 |
| Risk rows with source locators | 16 |
| Risk rows without explicit evidence | 0 |
| Land status | basic_site_data_only |
| Land total area captured | 5,000 sqm |

Interpretation:

- The source-locator warning is resolved for baseline pilot gating.
- All Risk Register rows now have a traceable source locator.
- Some locators are still `agent_artifact` fallback locators and should be replaced by document page, Excel cell, API row, registry evidence, or official-price evidence when available.
- Approval remains blocked because the transaction still has substantive credit issues, unresolved handoff/risk reviews, and missing land evidence.

### Daegu run with flat `landEvidence` package

A second run added a pilot `landEvidence` package with official land price evidence, registry evidence, source metadata, PNU, and 100% acquisition coverage.

| Check | Result |
|---|---:|
| Land status | ready_for_pf_review |
| PNU ready | true |
| Official price ready | true |
| Registry evidence ready | true |
| Acquisition ready | true |
| Source data ready | true |
| Reconciliation status | clear |
| Approval warning count | 0 |
| Risk rows without explicit evidence | 0 |

Interpretation:

- Land Agent can now clear the land-evidence warning when a project supplies a ledger import or a flat evidence package.
- Approval still remains blocked because the stress/waterfall and credit-review result are intentionally adverse in this pilot.

### Daegu scenario restructuring recheck

The scenario calculations were checked again after aligning waterfall fee treatment. Previously, the pilot produced a `SENIOR_SHORTFALL` because the waterfall row claim included tranche fee while the simulated payment claim used only principal plus interest. That mismatch created a false senior shortfall equal to the missing fee component.

After the fix, the Scenario Engine treats interest plus fee as the waterfall claim rate. The Daegu pilot no longer produces `SENIOR_SHORTFALL`; Credit Review moved from `reject_recommended` to `conditional_approval` in the local orchestration check.

Remaining approval blockers are now substantive review items rather than the previous senior waterfall mismatch:

| Remaining item | Status |
|---|---|
| Unsold LTV breach | Still action required |
| Official land price / registry evidence | Requires source-backed evidence when not supplied |
| Market comparable and pricing-basis evidence | Requires stronger source coverage |
| Handoff and Risk Register reviews | Requires reviewer approval, waiver, revision, or resolution |

The new `RestructuringAgent` and `/scenarios/restructuring` endpoint quantify cure actions before rerunning Credit Review.

The same logic is now part of the PF review orchestration. New runs produce a `scenario_restructuring_plan` step, a `restructuringPlan` artifact, and a `restructuring_to_credit_review` handoff so the cure path is visible in the run history and approval gate.

## First Real Pilot Preparation

The reusable pilot runbook is now available:

```text
docs/product/pilot-project-runbook.md
```

The matching input package template is available:

```text
services/api-server/data/fixtures/pilot_project_input_package_template.json
```

The template is designed for a controlled first pilot with direct payload input, manual land evidence, manual market comparables, PF tranche structure, discount triggers, and scenario drivers. It should be copied into a project-specific pilot folder and updated with the actual anonymized documents, source locators, and evidence metadata before running the real pilot.

Observed cure output for the Daegu-style pilot:

| Cure item | Result |
|---|---:|
| Senior shortfall | 0 |
| Senior shortfall status | clear |
| Worst unsold LTV cell | 80% sale rate / 20% discount |
| Discounted unsold value | KRW 22.8bn |
| Total PF amount | KRW 70.0bn |
| Target supported PF amount at 90% LTV | KRW 20.52bn |
| Required PF reduction or equity increase | KRW 49.48bn |
| Required additional collateral value | KRW 54.98bn |

Interpretation:

- The prior automatic rejection was solved by fixing the scenario calculation mismatch.
- The transaction should not be approved automatically because unsold collateral coverage is still weak in stress.
- To move toward approval, the project needs one or more source-backed actions: reduce total PF exposure, increase equity, add collateral, secure a forward-sale or bulk-sale commitment, obtain refinancing evidence, strengthen cash sweep/discount triggers, and complete reviewer sign-off.

### Web app check

The Next.js web app passed:

- `npm run check-types --workspace web`
- `npm run lint --workspace web`
- `npm run build --workspace web`

### API server check

The current bundled Python runtime does not include `fastapi` or `uvicorn`, so the FastAPI server cannot be launched in this runtime until backend dependencies are installed from `services/api-server/requirements.txt`.

The web app currently calls the API server at:

```text
http://127.0.0.1:8000
```

Therefore, the full browser pilot requires:

1. install backend Python dependencies,
2. run `uvicorn main:app --reload --host 127.0.0.1 --port 8000` from `services/api-server`,
3. run `npm run dev --workspace web`,
4. open `http://localhost:3000`.

## Snapshot Diff Result

`realytics-snapshot-diff-v1` correctly detected material changes:

- Exit sale rate changed from 53.3% to 66.7%.
- LTV changed from 46.7% to 58.0%.
- LTC changed from 58.3% to 64.5%.
- PF amount changed from KRW 70.0bn to KRW 80.0bn.
- Total cost changed from KRW 120.0bn to KRW 124.0bn.
- 3 new risks were added.
- Re-review was required.

Recommended actions returned:

- Re-run affected calculations and confirm source assumptions.
- Review new Risk Register row and assign approval condition or waiver.

## Similar Case Search Result

`realytics-similar-case-search-v1` found the historical Daegu mixed-use snapshot as the top case.

- Similarity score: 0.8942 before stricter risk matching hardening
- Strong matched dimensions: project type, location, land area, revenue, cost, land cost, PF amount, LTC, marketability grade, land readiness, reconciliation status, and risk overlap

The search returned historical risk handling, mitigants, approval conditions, Credit Review outcome, and Evidence Graph links.

## Gaps Found

### 1. Quality Gate Is Too Shallow

The quality gate passed even though the Risk Register status was `blocking` and Credit Review decision was `reject_recommended`.

This means the current quality gate mostly checks artifact/report completeness. It should be renamed or split:

- `reportCompletenessGate`
- `approvalReadinessGate`

Approval readiness should fail when unresolved blocking risks, rejected credit decision, or missing legal/land evidence remain.

### 2. Manual Comparable Evidence Did Not Fully Prevent Marketability Data Gaps

The pilot included manual comparables, but Marketability still generated `NO_COMPARABLE_DATA` in the risk set.

Root cause observed in pilot:

- Comparable records were present, but product mix lacked area/price-per-py fields needed to derive proposed price.
- Marketability pricing logic requires both comparable prices and a proposed price basis.

Needed hardening:

- derive proposed price per py from totalRevenue / total contract area when possible,
- mark the issue as `PROPOSED_PRICE_BASIS_MISSING` rather than `NO_COMPARABLE_DATA` when comparables exist but proposed pricing basis is incomplete. Completed on 2026-05-26,
- surface product-mix area fields as required parser targets.

### 3. Risk Rows Need More Explicit Evidence IDs and Locators

Evidence Graph successfully created risk nodes, claim links, and report-section links. However, most risk rows lacked explicit evidence IDs or source locators.

Needed hardening:

- generated risk rows should inherit evidence IDs from source artifacts,
- scenario risk flags should carry scenario IDs and matrix row locators,
- land risks should link to PNU, official price, registry, and parcel evidence records,
- legal risks should link to Korean Law MCP query/result IDs and permit/security-document evidence,
- data quality risks should expose exact conflicting field paths and document/cell locators.

### 4. Similar Case Risk Matching Was Too Broad

The first pilot showed category/severity fallback could match unrelated Data Quality rows.

Immediate fix applied:

- risk matching now uses exact risk code when a query risk has a code,
- category/severity fallback is only used when the query risk has no risk code.

### 5. Historical Similar Case Search Needs Outcomes

The similar-case result can explain prior review status, mitigants, and approval conditions, but it cannot yet answer whether the historical case actually performed well.

Needed future fields:

- achieved sales rate,
- realized discount,
- loan repayment completion,
- extension/default/EOD history,
- recovery/loss result,
- actual land closing completion.

## Recommended Next Work

1. Add `approvalReadinessGate` separate from report completeness. `Done: realytics-approval-readiness-gate-v1 implemented.`
2. Harden Marketability pricing basis detection and product mix area derivation.
3. Propagate evidence IDs/source locators into all Risk Register rows. `Done for baseline artifact-level coverage; document/API/cell precision remains future hardening.`
4. Add scenario matrix locators to Scenario Engine risk flags. `Done.`
5. Add historical outcome fields to project snapshots.
6. Add UI panels for Snapshot Diff and Similar Case Search.

## Sample-Derived Runtime Pilot Update

Updated: 2026-05-28

The runtime pilot input package was updated from the local Bonri sample files by:

```text
services/api-server/scripts/update_pilot_input_from_samples.py
```

Source files used:

- `19.12.11 본리동 주상복합 사업수지 (PF검토용).xlsx`
- `01.본리동토지조서.xls`
- `부동산금융평가위원회_대구 달서구 본동 주상복합 중순위 PF_하나금융투자 (최종) - 복사본.docx`

Extracted sample values now reflected in `data/runtime/pilot-input/pilot_input_payload.json`:

- Land area: 4,691.9 sqm
- Land ledger parcels: 9
- Land ledger total purchase amount: KRW 41.374bn
- Feasibility total revenue, VAT excluded: KRW 165.597bn
- Feasibility total cost: KRW 151.842bn
- Land related cost: KRW 46.032bn
- Construction cost: KRW 72.036bn
- Finance cost: KRW 16.298bn
- PF amount: KRW 90.000bn
- PF tranches: Tr.A KRW 63.000bn, Tr.B KRW 12.000bn, Tr.C KRW 15.000bn
- Product mix: apartment 230 units, officetel 76 units, retail area-based revenue

Latest local pilot command:

```text
python scripts/run_pilot_fixture.py --input data/runtime/pilot-input/pilot_input_payload.json --project-id pilot-bonri-sample --output data/runtime/pilot-input/outputs/pilot-summary.json --full-run-output data/runtime/pilot-input/outputs/pilot-full-run.json
```

Latest result:

- Orchestration status: `completed`
- Completed steps: 18 / 18
- Error count: 0
- Report quality gate: `pass=true`
- Approval readiness gate: `pass=false`, `status=not_ready_for_approval`
- Credit decision: `rework_required`
- Risk Register: `blocking`
- Evidence Graph risk rows without explicit evidence: 0

Important interpretation:

The system is operational for a controlled pilot run, but the sample-derived case is not approval-ready. This is expected and desirable because the actual uploaded sample set does not contain complete official land price evidence or registry/encumbrance evidence.

Current blockers:

- Land Agent status is `needs_review`.
- PNU generation is working from legal dong code and parsed lot numbers.
- Official land price evidence is missing.
- Registry and encumbrance evidence is missing.
- Risk Register still contains blocking rows.
- Seven Agent handoffs remain unresolved for human review.
- Eighteen material risk rows require approve / waive / revise / resolve workflow.
- Unsold LTV breach remains and requires PF reduction, equity increase, additional collateral, forward-sale, bulk-sale, or refinancing evidence.

Current restructuring result:

- Senior shortfall: clear.
- Worst unsold LTV cell: sale rate 80%, discount 20%, LTV 343.02%.
- Required PF reduction or equity increase: KRW 66.386bn.
- Alternative additional collateral value: KRW 73.763bn.

Next pilot hardening actions:

1. Attach or fetch official land price records with base year/source evidence for every parsed parcel.
2. Attach registry and encumbrance evidence per parcel.
3. Add a human review workflow for unresolved handoffs and Risk Register rows.
4. Add a targeted restructuring scenario that reduces PF exposure, adds equity, or attaches collateral/refinancing evidence, then rerun.
5. Add backend dependency installation/runtime instructions so FastAPI can be launched consistently in the local pilot environment.

## Land Evidence Request Output

The parcel-level evidence request helper is now available:

```text
python scripts/build_land_evidence_request.py
```

Latest output files:

- `data/runtime/pilot-input/outputs/land-evidence-request.json`
- `data/runtime/pilot-input/outputs/land-evidence-request.md`
- `data/runtime/pilot-input/outputs/land-evidence-template.json`

Latest request summary:

- Parcel count: 9
- PNU ready count: 9
- Official land price missing: 9
- Registry evidence missing: 9
- Cadastral characteristics missing: 9

This makes the next human/API enrichment task explicit: each parsed parcel now has a PNU and source row locator, but each parcel still needs official land price, cadastral characteristics, and registry/encumbrance evidence before legal review or PF approval readiness can be cleared.

The evidence attachment helper is also available:

```text
python scripts/apply_land_evidence_to_pilot.py --evidence data/runtime/pilot-input/outputs/land-evidence-template.json
```

Safety behavior:

- Empty template fields are ignored.
- Official land price records require PNU, price per sqm, base year, source name, and evidence ID or URL.
- Registry records require PNU, evidence/source ID, source name, registered owner, and owner match status.
- Cadastral records require PNU, source metadata, and at least one substantive detail such as zoning, standard land category code, or base date.
- If no verified evidence is filled, Land Agent remains `needs_review` and PF Summary remains `needs_enrichment`.

Latest dry run with the empty template:

- Official land price records applied: 0
- Cadastral records applied: 0
- Registry records applied: 0
- Land ledger status: `needs_review`
- PF summary status: `needs_enrichment`

This confirms the workflow is wired while preventing accidental approval based on blank or fabricated evidence.

## Configured API Connector Dry Run

The connector fetch helper is now available:

```text
python scripts/fetch_land_evidence_from_connectors.py --base-year 2026
```

Latest local run without configured API URL templates:

- PNU resolution records fetched: 0, because PNU values were already present in the payload.
- Cadastral records fetched: 0.
- Official land price records fetched: 0.
- Registry records fetched: 0.
- Diagnostics: 18 `skipped_missing_config` entries.

Output files:

- `data/runtime/pilot-input/outputs/land-evidence-connector-result.json`
- `data/runtime/pilot-input/outputs/land-evidence-from-connectors.json`

Interpretation:

The connector workflow is operational, but no live public API is configured in the local environment. This is not treated as success evidence. The Land Agent remains blocked until source-backed cadastral, official land price, and registry evidence is supplied.
