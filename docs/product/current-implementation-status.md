# REALYTICS x PLATFORM9 Current Implementation Status

Updated: 2026-06-12

## Summary

The platform has an MVP-level PF review orchestration flow that can serve both financial institution PF review and real estate developer feasibility review.
2026-06-11 pilot auth UI update: the web app now has a local pilot login/session screen at `/settings`. The screen creates tenant, user, and session records through `/auth/tenants`, `/auth/users`, and `/auth/session`, stores the returned session in browser local storage, resolves `/auth/me`, and displays role-derived permissions in Korean finance workflow language. A shared web auth client now sends `X-REALYTICS-SESSION-ID` and `X-REALYTICS-USER-ID` from the stored session on pilot-critical screens, including document upload, real project input package, PF orchestration, evidence review, handoff/risk review, assumption overrides, restructuring cures, historical outcome, snapshot comparison, and Evidence Graph auditor views. This improves auditability for pilot users, but it is still not production SSO; external IdP integration, tenant isolation enforcement, hardened session validation, secret manager integration, and broader RBAC coverage remain planned.
2026-06-12 latest pilot execution update: the current pilot fixture was rerun as `pilot-latest` and completed all 18 PF review orchestration steps with zero runtime errors. The report quality gate passed, but `approvalReadinessGate` remains `not_ready_for_approval` with six blockers: legal blocking status, Data Quality blocking status, Risk Register blocking status, Credit Review `rework_required`, five unresolved handoff reviews, and six unresolved material risk reviews. Land evidence is ready in the current fixture package, senior shortfall is clear, and the remaining financial cure is Unsold LTV with a required PF reduction/equity increase of KRW 48.815bn or additional collateral of KRW 54.239bn. The blocker package script now renders safe owner labels and includes pending handoff/material-risk tables, and `docs/product/pilot-latest-execution-status.md` records the durable pilot status.
2026-06-12 Data Quality pilot blocker resolution update: `services/api-server/scripts/apply_pilot_data_quality_override.py` now applies approved Data Quality assumptions to a pilot payload and immediately validates the rerun. The first use case applied `UNIT_COUNT_CONSISTENCY` using `product_mix.units.sum=260` as the authoritative value, preserving the conflicting architectural-plan value as source evidence. The rerun `pilot-latest-dq-cleared` changed Data Quality from `blocking` to `needs_review`, reduced `blockingCount` from 1 to 0, applied override `ovr-pilot-unit-count-consistency-260`, removed `DATA_QUALITY_NOT_BLOCKING` from Approval Readiness, and reduced pending material risk reviews from 6 to 5.
2026-06-12 Unsold LTV cure rerun update: `services/api-server/scripts/apply_pilot_financial_cure.py` now applies source-backed restructuring cures to a pilot payload and validates the rerun. The first pilot use case applied additional collateral support of KRW 54.239bn through `scenarioDrivers.additionalCollateralValue`, preserving the cure as `cure-pilot-unsold-ltv-additional-collateral-54239316239` with evidence/source locator metadata. Scenario Engine now calculates collateral-adjusted Unsold LTV matrix rows and treats fully sold cells as zero LTV. The rerun `pilot-latest-dq-collateral-cure` reduced max Unsold LTV from 2.9739 to 0.9, cleared `UNSOLD_LTV_BREACH`, removed the Unsold LTV approval warning, reduced pending handoff reviews from 5 to 4, and reduced pending material risk reviews from 5 to 3. Approval readiness remains blocked by legal/permit/covenant package risks and unresolved reviewer actions, not by the cured Data Quality or Unsold LTV items.
2026-06-09 Data Quality override lifecycle update: approved assumption overrides now support `active`, `revoked`, `expired`, and `superseded` lifecycle states. A newly saved active override for the same `checkKey + canonicalField` automatically supersedes the prior active record, expired records are exposed as `effectiveStatus=expired` and excluded from active PF review hydration, and revoke/expire actions emit `assumption_override_status_changed` audit events. The orchestration page now lists stored approved values with status badges and reviewer actions to revoke or expire them before rerunning PF Review. This prevents stale or mistaken approved assumptions from silently continuing to clear Data Quality blockers.
2026-06-09 restructuring cure workflow update: senior shortfall and Unsold LTV cure decisions are now persisted as first-class `restructuring_cures` server records. The API exposes `GET/POST /projects/{project_id}/restructuring-cures`, stores JSON/SQLite records with status, cure type, amount, target risk codes, reviewer, evidence IDs, source locator metadata, and emits a `restructuring_cure_selected` user activity event. PF review orchestration hydrates active cure records into `adoptedRestructuringCures`, `RestructuringAgent` reports active/source-backed cure counts, and the `restructuring_to_credit_review` handoff carries the adopted cure summary. The orchestration UI now has a Financial Cure Workflow panel where reviewers can save source-backed senior reduction, Equity/DSRA injection, additional collateral, bulk sale/forward sale, refinancing, or repayment-waterfall strengthening cures. Saving a cure still does not automatically clear approval blockers; the cure must be evidence-backed and reflected in the next rerun/calculation.

2026-06-08 UI simplification and audit update: the project home is now a diagnosis-first pilot workspace rather than a module-heavy dashboard. The primary flow is `사업계획서 업로드 -> AI 진단 -> 보완자료 확인 -> 재실행/승인 가능성 -> 보고서·감사근거`, with Korean PF finance terminology for input package, AI integrated review, approval readiness, land evidence, business feasibility, monthly cash flow, PF structure, risk register, Evidence Graph, and credit decision. A new `/project/{id}/evidence` Evidence Graph auditor screen lists persisted graph records, filters risk/report/claim/source nodes, shows connected graph edges, and exposes source locator metadata so reviewers can trace a risk row, report section, or calculated number back to a document page, Excel cell, API row, evidence ID, or agent artifact path.

2026-06-08 Data Quality override UI update: the orchestration page now exposes a reviewer-facing Data Quality 기준값 승인 panel. It displays Data Quality check counts, blocking/warning/missing rows, source-by-source conflicting values, Korean field labels, canonical field mapping, reviewer name, selected or manually entered approved value, approval reason, and source locator payload. Saving a row calls `POST /projects/{project_id}/assumption-overrides`, records the approved assumption override with audit metadata, reloads the selected run, and makes the override available to the next PF Review rerun without overwriting original source values. This is the pilot workflow for resolving total sales revenue, PF amount, land area, GFA, unit count, VAT basis, and schedule conflicts.

2026-06-08 Data Quality override rerun validation: the local pilot project saved four active assumption overrides for `gross_floor_area_consistency`, `pf_amount_consistency`, `unit_count_consistency`, and `vat_basis_consistency`, then reran PF Review as `fae8b0ad-c3f5-4d25-9aa1-c1c56a06d5f6`. The rerun changed `dataQualityReview.status` from `blocking` to `clear`, with `blocking=0`, `warning=0`, `missing=0`, `overridden=4`, and `appliedOverrides=4`. `approvalReadinessGate.summary.dataQualityStatus` also became `clear`. Approval readiness still remains blocked by non-Data-Quality items such as land evidence/acquisition readiness, unresolved risk reviews, and unresolved handoff reviews.

2026-06-08 Land Evidence remediation update: `land_acquisition_status` is now a first-class server-side evidence record type. Persisted Land Evidence packages hydrate the latest acquisition/closing record into `acquisitionAreaRatio`, so Land Agent can clear `LAND_ACQUISITION_READY` when source-backed closing evidence reaches 100%. The orchestration page now includes a Land Evidence readiness remediation panel showing PNU, official land price, registry evidence, acquisition readiness, unresolved land gate items, stored evidence counts, and a reviewer form for saving acquisition/closing evidence such as ownership transfer, trust registration, balance-paid confirmation, or public-land purchase approval. Regression tests confirm that persisted PNU, official price, registry, and `land_acquisition_status` records clear the acquisition gate in PF Review orchestration.

2026-06-08 runtime hygiene update: `.gitignore` now excludes `.runtime/`, Python bytecode, `__pycache__/`, pytest cache, and local virtualenv folders. The API server requirements now explicitly pin `starlette>=0.36.3,<0.37.0` for `fastapi==0.110.0`, preventing the local `starlette 1.0.0` incompatibility that blocked Uvicorn startup in the shared workstation environment.

2026-06-06 Excel import approval update: workbook import now has a reviewer approval gate. `/projects/{project_id}/feasibility-workbook/validate` remains read-only and returns a FeasibilityStudyV2 draft, while `/projects/{project_id}/feasibility-workbook/approve` persists reviewer-approved values as the project canonical `FeasibilityStudyV2`, updates derived `feasibility_input`, preserves source/validation/approval provenance in `raw_import.approval`, and emits a user activity audit event. The compact risk workbook path is also now server-importable: `RiskReportWorkbookImporter` parses `risk-report-xlsx-v1` named ranges, key metrics, 20 risk rows, report paragraphs, approval-condition text, cash-flow summary rows, and source-cell mapping. `/risk-report-workbook/validate` returns a draft, and `/risk-report-workbook/approve` persists the imported Risk Register under the workbook import ID and attaches imported report-section text to the project.

2026-06-02 formula workbook update: the web Cash Flow Excel export now creates a formula-auditable workbook instead of a value-only schedule. It was rechecked against the local sample workbook `19.12.11 본리동 주상복합 사업수지 (PF검토용).xlsx`, whose core tabs are `입력`, `분양`, `사업수지`, `2.현금흐름`, and `공정율`. The export now includes `00_사용안내`, `01_입력가정`, `02A_분양수입`, `02B_수납스케줄`, `02_사업수지`, `03A_공정률`, `03_월별현금흐름`, `03B_PF인출상환`, `03C_Tranche상환`, `03D_Tranche월별상환`, `04A_할인민감도`, `04B_할인Trigger`, `04_검증`, `05_수식지도`, and `06_샘플대사` sheets. Core feasibility metrics, product-mix revenue, contract/interim/balance collection schedules, S-curve progress, monthly cash flow, cumulative cash flow, Max Minus, PF draw need, PF interest, repayment reserve, principal repayment, ending PF balance, Tr.A/B/C total claim, tranche payment/shortfall/recovery ratio, monthly tranche principal repayment, discount-sale adjusted revenue, trigger-adjusted monthly revenue, tranche-level Exit sale rates, LTC, 분양가 LTV, and Base Exit 분양률 are stored as Excel formulas with cached results. The checks sheet reconciles monthly totals, product-mix totals, PF draw limit, ending PF balance, tranche principal total, monthly tranche balance, Waterfall shortfall, and trigger-adjusted revenue back to the assumptions. The current workbook is sample-informed but still simplified; the next step is full `realytics-fs-xlsx-v1` compatibility with named ranges, product-level sales-rate scenarios, monthly discount trigger execution into the main CF scenario selector, protected formula ranges, and canonical import round-tripping.

2026-06-02 sample-like Excel mirror update: the Cash Flow export now places Bonri-style mirror sheets before the canonical REALYTICS calculation sheets: `입력`, `분양`, `사업수지`, and `2.현금흐름`. These tabs mirror the local sample workbook's user-facing flow while linking into the formula workbook with explicit formulas such as `사업수지!D8 = '분양'!J41`, subtotal formulas for construction/land/design/sales costs, and monthly cash-flow formulas for sales inflow, PF draw, equity input, total cash-in, total cash-out, current-period cash flow, and cumulative cash flow. The web app's Cash Flow detail screen now exposes a Korean calculation-evidence view showing sheet mapping, source cells, formula logic, and calculation trace before the user downloads Excel.

2026-06-04 sample Excel similarity review and full sample-package parity update: `docs/product/sample-excel-similarity-checklist.md` now tracks the sample workbook similarity path. The export route now has a local sample-template mode: when the Bonri sample workbook is available through `REALYTICS_SAMPLE_WORKBOOK_PATH` or the local `sample` folder, it copies the full source sample workbook sheet package before appending REALYTICS canonical calculation, validation, formula-map, sample-trace, and import-contract sheets. The route supports controlled deployment variables `REALYTICS_EXCEL_TEMPLATE_PATH`, `REALYTICS_EXCEL_TEMPLATE_DIR`, `REALYTICS_EXCEL_TEMPLATE_ID`, and `REALYTICS_EXCEL_TEMPLATE_VERSION`, and template path/source/hash resolution now lives behind `apps/web/lib/excel-template-repository.ts` instead of being embedded directly in the route. This preserves original input, sales, expense, business-plan, summary, overview, cash-flow, sale-price cap, progress, infrastructure-charge, and review sheets in local sample mode. The generated workbook now includes `08_Template계약`, recording template mode, ID, version, source type/path/file, SHA-256, copied sheet count/names, and resolved timestamp. The exported `2.현금흐름` still matches the sample at `A1:CH158`, 7,085 non-empty cells, 6,527 formula cells, 145 merged ranges, `F5` freeze pane, and key first-month cash-flow values such as `F56=5,000`, `F120=5,267.49`, `F122=-267.49`, and `F124=-267.49`. Validator status remains `passed`, and `services/api-server/scripts/verify_cashflow_parity.py` now supports `--all-sample-sheets` for full copied sample-package regression. Latest local QA generated `.runtime/formula-cashflow-export-template-repository.xlsx` and `.runtime/template-repository-parity-check.json`; all 12 source sample sheets passed dimension, formula-count, non-empty-cell, merged-range, freeze-pane, and key-cell parity checks. The workbook validator now scopes formula integrity to the REALYTICS canonical sheets for formula-export workbooks, so copied sample-sheet `#REF!` artifacts are preserved for parity while canonical import readiness still returns `status=passed`, `directV2ImportReady=true`, and no formula errors. Remaining Excel parity work is now focused on moving the actual template binary to secured object storage or a server-managed template location and parameterizing copied sample cells from REALYTICS canonical assumptions.

2026-06-05 risk report compact Excel update: `/api/export-risk-excel` now creates a reduced 6-sheet workbook for Risk Report insertion and server import. It includes `리스크보고서첨부`, `핵심지표`, `20대리스크`, `현금흐름요약`, `근거셀매핑`, and `Import계약` sheets, with report-ready Korean PF review paragraphs, key PF/CF metrics, 20 risk management rows, source-cell locators, template hash metadata, and `risk_*` named ranges. This is intentionally separate from the full 29-sheet formula-auditable Cash Flow model: the compact file is for report appendices, Risk Register ingestion, and fast reviewer loading. The Cash Flow detail page now exposes both downloads: the compact risk report Excel and the full formula verification workbook.

2026-06-04 Excel workbook learning update: `docs/product/excel-workbook-learning-agent.md` now defines the LLM-first continuous learning workflow for new feasibility-study and cash-flow workbook formats. The implementation adds `ExcelWorkbookLearningAgent` as `realytics-excel-workbook-learning-v1`, which profiles uploaded workbooks, scores similarity against known Bonri-style and REALYTICS formula-export templates, proposes source-located FeasibilityStudyV2 mapping candidates, creates an LLM judgement plan, returns an automation decision, and defines a continuous-learning path through automated validation, shadow-mode import, regression fixtures, template registry update, and monitored production import. `POST /projects/{project_id}/feasibility-workbook/learn` archives the uploaded workbook and returns this learning package. This is intentionally an LLM-governed improvement layer rather than direct parser mutation.

2026-06-02 scenario selector update: `01_입력가정!C22` now provides an Excel dropdown for `Base` or `Trigger`. The main monthly cash flow sheet uses this selector to pull either the original collection schedule from `02B_수납스케줄` or the discount-trigger adjusted revenue from `04B_할인Trigger`, while `04B_할인Trigger` now references the base collection schedule directly to avoid circular formulas.

2026-06-02 import contract update: the Cash Flow Excel export now includes `07_Import계약` and workbook-level named ranges for `realytics_fs_schema_version`, `realytics_source_workbook`, `fs_total_revenue`, `fs_total_project_cost`, `fs_pf_loan`, `fs_revenue_scenario`, `cf_monthly_revenue`, `cf_cumulative_cash_flow`, `cf_max_minus`, `pf_ending_balance`, `pf_tranche_shortfall_total`, and `trigger_adjusted_revenue_total`. The schema version is stored as the literal `realytics-fs-xlsx-v1-draft`, and the import contract sheet reads it through the named range to avoid circular formulas. Generated workbooks also protect all sheets while leaving input assumption cells unlocked, so users can edit assumptions without accidentally overwriting formula cells. The generated XLSX was downloaded from `/api/export-cf` and inspected with OpenPyXL to confirm 20 sheets, named-range destinations, Base/Trigger validation, scenario formula wiring, unlocked input cells, locked formula cells, and active worksheet protection.

2026-06-02 workbook validator update: `feasibility_workbook_validator.py` now auto-detects REALYTICS formula export workbooks through the `realytics_fs_schema_version` named range or `07_Import계약` sheet. It validates all required export sheets, required named ranges, named-range destinations/values, formula integrity, and direct import readiness separately from the legacy Bonri workbook profile. The downloaded `/api/export-cf` workbook was passed through this validator and returned `status=passed`, `profile=realytics-formula-cash-flow-export`, `directV2ImportReady=true`, and no missing required named ranges. Full FeasibilityStudyV2 object creation from these named ranges remains the next parser step.

2026-06-02 workbook importer update: `feasibility_workbook_importer.py` now creates a FeasibilityStudyV2 draft from either the Bonri-style legacy sample workbook or the REALYTICS formula export workbook. The Bonri adapter maps `입력`, `분양`, `사업수지`, and `2.현금흐름` into site area, zoning/FAR, program area, product mix by apartment/officetel/commercial, revenue items, cost line items, PF/equity funding, schedule, core metrics, unit scale, mapped cells, and limitations. The actual local sample workbook `19.12.11 본리동 주상복합 사업수지 (PF검토용).xlsx` was imported successfully, producing project name `본리동 주상복합 신축공사`, land area `4,691.9 sqm`, gross floor area `54,919.35 sqm`, product mix of apartment 230 units, officetel 76 units, commercial 2 units, total revenue about KRW 165.597bn, total cost about KRW 151.842bn, and PF principal about KRW 50.0bn from the workbook's cash-flow sheet. The `/feasibility-workbook/validate` endpoint now returns this `feasibilityStudyV2Draft` for reviewer comparison before persisted canonical import.

The current implementation is synchronous and uses SQLite-backed state storage for projects, reports, and orchestration runs. Agent boundaries, API endpoints, MCP tools, tests, and documentation are now structured so the system can later move to PostgreSQL, async jobs, and stronger UI-based monitoring.

The AI connection layer records provider, runtime mode, model, host, and linked `user_id` for API uploads, MCP document parsing, and orchestration runs.

User activity logging is now implemented as a baseline troubleshooting/audit layer. The API server records request-level `user_activity_logs` in JSON/SQLite runtime state, extracts user IDs from request headers or query params, links project/run IDs from request paths, and exposes `POST /audit/login-event`, `GET /audit/user-activity`, `GET /audit/user-activity/summary`, and `GET /audit/user-activity/export`. Source document downloads, report export creation, and report export downloads also emit explicit sensitive activity events. The web app exposes `/audit` and `/project/{id}/audit` panels for event mix, sensitive actions, slow requests, and recent activity rows. Persisted tenant, user, and session records are now available through `/auth/tenants`, `/auth/users`, `/auth/session`, and `/auth/me`; role-derived RBAC checks now protect audit log reads, source document downloads, report export creation, and report export downloads, with `REALYTICS_RBAC_MODE=strict` available for blocking behavior. The `/settings` page can now create a pilot user session and major client-side workflows propagate that session to the API, so reviewer actions are no longer limited to anonymous fixed `web` headers during local pilots. This is not full authentication yet, so real identity provider integration, broader RBAC coverage, tenant isolation enforcement, and secret management remain planned.

Land Evidence storage is now a separate server-side record set rather than only an embedded land ledger artifact. The API server persists `pnu_resolution`, `cadastral_record`, `official_land_price`, and `registry_record` records through JSON/SQLite `land_evidence`, exposes `GET/POST /projects/{project_id}/land-evidence`, and can persist evidence records directly from `/land-ledger/enrich` when `persistEvidence` is true. PF Review orchestration now hydrates saved `land_evidence` into the project payload before a run, so Land Agent readiness, Legal Review land-right checks, Risk Register source locators, and Evidence Graph links can reflect stored PNU, official-price, and registry evidence. Approval Readiness Gate now evaluates land readiness through separate blocker checks for PNU, official land price, registry evidence, and land acquisition readiness.

The application layer now includes baseline `use_cases` modules so API endpoints, MCP tools, future background jobs, and future UI-triggered actions can reuse the same workflow functions instead of duplicating agent orchestration in routers.

The persistence boundary now includes baseline repository wrappers for project CRUD, source document metadata, upload archive files, reports, market evidence, and orchestration runs. These wrappers sit above the current SQLite/JSON-backed runtime stores and are used by the project routes, upload/source-document routes, report, evidence, orchestration, and approval use cases.

Agent-to-agent interaction now has a baseline implementation. PF review orchestration runs return typed `AgentHandoffEnvelope` artifacts and an `agentProgress` ledger, including Land to Feasibility/Legal/PF, Development Feasibility to Cash Flow, Market Evidence to Marketability, Marketability to Feasibility, and Legal to Credit Review handoffs. A first cross-agent reconciliation gate now checks revenue, land area, evidence readiness, and PF metric availability before legal/risk/report steps. Richer reviewer assignment remains a next architecture step. The target model is documented in `docs/product/agent-interaction-operating-model.md`.

The long-term product direction is now documented as a Real Estate OS: agents should share canonical schemas, use controlled terminology, preserve evidence and source metadata on the server, create project snapshots per review run, and eventually compare current projects against a historical PF/development review database.

Server-side project snapshots are now implemented as `realytics-project-snapshot-v1`. Each saved PF review run creates an immutable snapshot with canonical project inputs, agent artifacts, handoffs, reconciliation output, quality gate, report metadata, and case-comparison seed fields. Snapshot list/get APIs are available for audit and future similar-case retrieval.

Historical Similar Case Search is now implemented as `realytics-similar-case-search-v1`. It compares stored project snapshots by case seed metrics, location tokens, PF size, LTV/LTC, Exit sale rate, market/land/reconciliation status, and Risk Register overlap, then returns how similar historical risks were reviewed, mitigated, waived, resolved, or converted into approval conditions.

Snapshot Diff is now implemented as `realytics-snapshot-diff-v1`. It compares two saved snapshots from the same project and returns changed case seed fields, added/removed/changed Risk Register rows, Credit Review decision changes, artifact changes, report-section changes, material-change flags, and re-review recommendations.

Historical outcome fields are now implemented for project snapshots. Reviewers can attach post-approval outcomes through `POST /projects/{project_id}/snapshots/{snapshot_id}/outcome`, including actual sales rate, realized discount rate, repayment completion, extension/default/EOD status, loss amount, total and tranche-level recovery rates, actual exit source, source metadata, and source locators. Similar-case search now returns `historicalOutcome` and outcome summary fields when prior cases have actual performance data, so future review can compare not only how a risk was approved but whether the case actually repaid or produced loss.

Approval Readiness Gate is now implemented as `realytics-approval-readiness-gate-v1`. It separates report completeness from credit approval readiness by checking report quality, legal/data/reconciliation blockers, Risk Register blocking status, Credit Review reject/rework decisions, unresolved handoffs, unresolved material risk reviews, land evidence warnings, and missing risk source locators. Review actions for handoffs and Risk Register rows now refresh this gate so unresolved review counts do not become stale.

Evidence Graph Agent is now implemented as `realytics-evidence-graph-v1`. PF review orchestration creates an `evidenceGraph` artifact after Phase 11 report generation, linking project input, source documents, evidence IDs, agent artifacts, handoffs, extracted claims, Risk Register rows, source locators, and report sections. This is the baseline audit trail for explaining where report numbers, judgements, and material risk rows came from.

Evidence Graph persistence is now implemented. PF review runs persist their `evidenceGraph` artifact into JSON/SQLite `evidence_graphs` by project/run, expose list/get APIs for project-level graph history, and preserve graph summary counts for node, edge, risk-node, and source-locator coverage.

2026-05-26 locator update: Marketability, Scenario, and Data Quality risk rows now carry explicit `sourceLocators` into the Risk Register and Evidence Graph. Marketability pricing risks include proposed-price and comparable/product-mix field paths plus source locators where available. Scenario risks include waterfall row, tranche shortfall, unsold LTV matrix cell, and scenario summary locators. Evidence Graph now summarizes `sourceLocatorCount` and `riskRowsWithSourceLocatorCount`.

2026-05-27 locator update: every Risk Register row now carries at least one explicit source locator. Rows with direct field/scenario/document evidence keep precise locators; generated Land, Legal, Cash Flow, PF Structure, and Risk Matrix rows receive an `agent_artifact` fallback locator with `locatorPrecision=artifact_level` until document page, Excel cell, API row, registry evidence, or official-price evidence is attached.

2026-05-27 land evidence update: PF review orchestration accepts a flat `landEvidence` package when a full land ledger import is not yet available. If the package supplies PNU, official land price evidence with base year, registry/encumbrance evidence, source metadata, and complete acquisition coverage, Land Agent marks the land patch `ready_for_pf_review` and clears the land-evidence approval warning.

2026-05-27 scenario restructuring update: `RestructuringAgent` is implemented as `realytics-restructuring-agent-v1` with `POST /projects/{project_id}/scenarios/restructuring`. It quantifies the minimum senior tranche reduction or DSRA/equity injection needed to remove `SENIOR_SHORTFALL`, calculates the PF reduction/equity increase or additional collateral value required to cure `UNSOLD_LTV_BREACH`, reruns a senior-resized scenario, and returns source locators to the relevant waterfall row and unsold LTV matrix cell. The Scenario Engine waterfall now treats interest plus fee as the tranche claim rate so row claims and simulated payments are aligned. PF review orchestration now also creates a `restructuringPlan` artifact and `restructuring_to_credit_review` handoff so rejection-cure recommendations are visible inside the same review run.

2026-05-27 pilot readiness update: `docs/product/pilot-project-runbook.md` and `services/api-server/data/fixtures/pilot_project_input_package_template.json` are added for the first real pilot. The template defines project/site, land evidence, feasibility, product mix, market comparables, PF tranches, discount triggers, scenario drivers, source documents, and required review artifacts. A fixture smoke test now confirms the template completes all 18 PF review orchestration steps and produces `qualityCheck`, `approvalReadinessGate`, `creditReviewDecision`, `restructuringPlan`, `phase11Report`, and `evidenceGraph` artifacts. `services/api-server/scripts/run_pilot_fixture.py` can run any pilot package locally and emit a compact approval, risk, land, restructuring, and evidence summary JSON. `services/api-server/scripts/prepare_pilot_workspace.py` creates an external pilot workspace, copies the payload template, inventories source documents with SHA-256 hashes, and keeps original files outside Git unless explicitly copied.

2026-05-28 sample-derived pilot update: `services/api-server/scripts/update_pilot_input_from_samples.py` now extracts the local Bonri feasibility workbook, land ledger, and PF review DOCX into `data/runtime/pilot-input/pilot_input_payload.json`. Extracted values include 4,691.9 sqm land area, 9 parcels, KRW 41.374bn land purchase amount, KRW 165.597bn VAT-excluded revenue, KRW 151.842bn total cost, and KRW 90.000bn PF across Tr.A/B/C. The resulting pilot run completes all 18 orchestration steps with no runtime errors and passes report completeness, but `approvalReadinessGate` remains blocked because official land price evidence, registry/encumbrance evidence, unresolved handoff/risk reviews, and Unsold LTV cure evidence are still missing.

2026-06-01 real pilot input package UI update: the web app now includes `/project/{id}/input-package`, a project-level pilot package screen for uploading a business plan/basic plan PDF, land ledger workbook, feasibility workbook, land evidence JSON, and approved data-quality overrides before running PF review orchestration. Business plan and land ledger uploads are archived as source documents, land ledger upload can immediately run enrichment with persisted land evidence records, and `POST /projects/{project_id}/feasibility-workbook/validate` archives and validates formula-driven feasibility workbooks before full canonical import is implemented.

2026-06-01 approval readiness dashboard update: the project orchestration page now displays an Approval Readiness Dashboard that summarizes `approvalReadinessGate` into four pilot-critical lanes: Land Evidence, Risk Review, Credit Decision, and Handoff Review. Each lane is classified as `ready`, `needs_review`, or `blocking`, and the panel surfaces blocking gate checks, warnings, and required actions so users can distinguish a complete report from an approvable credit package.

2026-06-01 historical outcome UI update: the project orchestration page now includes a Historical Outcome panel for saved project snapshots. Reviewers can select a snapshot and persist actual sales rate, realized discount rate, repayment completion, extension/EOD/loss status, loss amount, total and tranche-level recovery rates, actual exit source, source metadata, and source locators. These fields feed future similar-case search and back-testing so REALYTICS can compare not only prior approval logic but also actual repayment and loss outcomes.

2026-06-01 similar-case UI update: the project orchestration page now includes a Historical Similar Cases panel. Reviewers can select the query snapshot, set search limit/minimum score, optionally include same-project snapshots for pilot testing, and inspect returned cases with similarity score, matched dimensions, exact risk-code matches, prior risk treatment, approval conditions, Credit Review decision, and actual repayment/EOD/loss/recovery outcome fields.

2026-06-01 snapshot diff UI update: the project orchestration page now includes a Snapshot Diff panel. Reviewers can compare two saved project snapshots and see changed case seed assumptions, added/changed/removed Risk Register rows, Credit Review decision changes, artifact/report-section changes, material-change counts, and re-review recommendations. This supports pilot audit trails when users rerun PF Review after adding land evidence, data-quality overrides, restructuring cures, or updated market assumptions.

2026-06-01 project workspace UI update: the project home page is now a pilot workflow hub rather than a simple module list. It exposes quick actions for 자료 입력, 통합심사, and 감사이력, keeps project KPIs visible, and presents the Real Estate OS workflow from source package upload through land/feasibility/cash-flow/PF/risk/report review and back-testing.

2026-06-01 AI diagnosis UI update: the project home page now starts with an AI automatic diagnosis panel. It reads the latest PF review run, Approval Readiness Gate, Credit Review decision, Risk Register, and handoff review state, then explains in Korean finance workflow terms which items are blocking approval, what needs to be updated, and which screen the user should open next. A frontend terminology map was added so platform concepts such as input package, orchestration, snapshot, similar cases, evidence graph, and credit decision can be rendered as Korean PF/finance terms.

2026-05-28 land evidence workflow update: `services/api-server/scripts/build_land_evidence_request.py` now also emits `land-evidence-template.json`, and `services/api-server/scripts/apply_land_evidence_to_pilot.py` attaches filled official land price, cadastral, and registry evidence back into the runtime pilot payload. The attachment workflow filters blank or non-source-backed records, reruns Land Agent enrichment, and preserves `needs_review` / `needs_enrichment` status when evidence is missing instead of manufacturing approval readiness.

2026-06-05 pilot approval blocker package update: `services/api-server/scripts/build_pilot_blocker_resolution_package.py` now converts the latest full pilot run plus parcel-level land evidence request into JSON and Markdown resolution packages. The latest generated package reports 6 approval blockers, 27 missing land evidence fields across 9 parcels, 18 unresolved material Risk Register reviews, 7 unresolved handoff reviews, senior shortfall already clear, and an Unsold LTV cure requirement of KRW 66.386bn PF reduction/equity increase or KRW 73.763bn additional collateral. Outputs are written to `services/api-server/data/runtime/pilot-input/outputs/pilot-blocker-resolution-package.json` and `.md`. This package is intentionally non-fabricating: official land price, cadastral, registry, collateral, forward-sale, bulk-sale, refinancing, or waiver evidence must be source-backed before rerunning approval readiness.

2026-05-28 connector runner update: `services/api-server/scripts/fetch_land_evidence_from_connectors.py` can now run the configured land data connector router against the runtime pilot payload and emit both raw connector diagnostics and an evidence JSON compatible with the manual attachment workflow. `docs/product/land-data-api-connector.md` documents the URL-template environment variables for VWorld/public-data/internal proxy connectors. In the current local environment no API URL templates are configured, so the dry run correctly returned `skipped_missing_config` diagnostics with zero fabricated cadastral or official price records.

Data Quality Agent is now implemented as `realytics-data-quality-v1`. PF review orchestration creates a `dataQualityReview` artifact before cross-agent reconciliation to detect project name, land area, GFA, sales revenue, PF amount, unit count, sqm/py conversion, VAT basis, schedule, and tranche structure conflicts. Blocking data quality issues now block the Approval Readiness Gate rather than the report-completeness quality check.

Data Quality override workflow is now implemented. Reviewers can persist `approved_assumption_override` records in JSON/SQLite `assumption_overrides`; PF review runs attach active overrides before Data Quality review, mark matching conflicts as `overridden`, preserve original conflict metadata, and emit `assumption_override_approved` audit events when overrides are saved.

Risk Register Agent is now implemented as `realytics-risk-register-v1`. PF review orchestration creates a `riskRegister` artifact after the Basic Risk Matrix and before report generation, consolidating Land, Marketability, Legal, Cash Flow, PF Structure, Scenario, Data Quality, and Risk Matrix issues into one credit-review table with top risks, approval-condition candidates, and a `risk_register_to_credit_review` handoff.

Risk Register review workflow is now implemented. Material risk rows can be marked `approved`, `waived`, `needs_revision`, or `resolved` with reviewer, timestamp, comment, waiver reason, and row-level audit history. Final orchestration approval is blocked while risk rows requiring human review remain unresolved.

Risk Register server records are now implemented. PF review runs persist their `riskRegister` artifact into JSON/SQLite `risk_registers` by project/run, expose list/get APIs for project-level review history, synchronize row-review outcomes back to the persisted register, and emit `risk_register_row_review` audit events.

Risk Register rows now carry `evidenceGraphLinks`. Each row is represented as a graph `risk` node and is linked back to its source artifact, evidence IDs, source documents, source locators, and supporting claim nodes when available.

Credit Review Agent is now implemented as `realytics-credit-review-v1`. PF review orchestration creates a `creditReviewDecision` artifact after Risk Register and before report generation, converting Risk Register, Legal, Data Quality, Reconciliation, Marketability, and PF metrics into `approve`, `conditional_approval`, `rework_required`, or `reject_recommended` guidance with reasons, mitigants, waivers, approval conditions, and report paragraphs.

## Implemented

### Project CRUD

API:

- `GET /projects/`
- `POST /projects/`
- `GET /projects/{project_id}`
- `PUT /projects/{project_id}`
- `DELETE /projects/{project_id}`

Capabilities:

- JSON-backed MVP create, list, get, update, and delete.
- Project records are normalized into a `project` envelope with `project.id`, default `project.status`, and `_meta.created_at` / `_meta.updated_at`.
- Delete removes associated MVP report and orchestration runtime records.

### Application Use Cases

Files:

- `services/api-server/use_cases/common.py`
- `services/api-server/use_cases/project_management.py`
- `services/api-server/use_cases/source_documents.py`
- `services/api-server/use_cases/development_feasibility.py`
- `services/api-server/use_cases/cash_flow.py`
- `services/api-server/use_cases/pf_review.py`
- `services/api-server/use_cases/land_ledger_import.py`
- `services/api-server/use_cases/marketability_review.py`
- `services/api-server/use_cases/legal_review.py`
- `services/api-server/use_cases/report_generation.py`
- `services/api-server/use_cases/evidence_review.py`
- `services/api-server/use_cases/orchestration_approval.py`

Implemented baseline:

- Development feasibility use case merges project state with request overrides and calls Development Feasibility Agent.
- Project management use case handles create, list, get, update, delete, and parsed upload application through `ProjectRepository`.
- Source document use case handles uploaded document metadata save/list/find through `SourceDocumentRepository` and binary archive save/download metadata through `UploadArchiveRepository`.
- Cash Flow use case accepts a direct `CashFlowRunRequest` or derives one through the canonical Development Feasibility path.
- PF review use case runs the orchestration workflow without coupling it to FastAPI.
- Land ledger, marketability, legal review, report generation/export, market evidence review, and orchestration approval use cases are now available.
- `routers/projects.py` now delegates development feasibility, cash-flow run, upload-and-run PF review, PF review orchestration, land ledger analysis, marketability analysis, legal review, report generation/export, evidence review, and orchestration approval to use-case functions.

Purpose:

- Keep routers thin.
- Allow API, MCP, batch jobs, and future UI actions to share the same application workflows.
- Prepare the codebase for async job execution and repository-based persistence.

### Repository Layer

Files:

- `services/api-server/db/repositories/project_repository.py`
- `services/api-server/db/repositories/source_document_repository.py`
- `services/api-server/db/repositories/upload_archive_repository.py`
- `services/api-server/db/repositories/report_repository.py`
- `services/api-server/db/repositories/market_evidence_repository.py`
- `services/api-server/db/repositories/orchestration_repository.py`

Implemented baseline:

- Project repository creates, lists, fetches, updates, deletes, normalizes, and applies parsed project records.
- Source document repository stores uploaded document metadata, lists project source documents, and locates archive metadata by archive ID.
- Upload archive repository stores source document binaries, resolves archive paths, verifies SHA-256 integrity, returns download metadata, and cleans up project archive folders.
- Report repository creates report records, lists report metadata, fetches saved reports, saves exports, and returns export metadata.
- Market evidence repository lists, fetches, stores, and sorts market evidence records.
- Orchestration repository saves PF review runs, persists related market evidence, creates report records for Phase 11 artifacts, lists run metadata, and fetches runs.
- Project CRUD routes, upload/source-document routes, report generation/export, evidence review, PF review save, and orchestration approval use cases now call repositories instead of manipulating router-local store logic directly.

Remaining:

- Async job state repository.

### Phase 11 PF Review Report

- 20-section Hana-style PF review report structure.
- Structured API response for `POST /projects/{project_id}/reports/generate`.
- Markdown compatibility output through `report_markdown`.
- Saved report version numbers and export history metadata.
- Export artifact creation for `markdown`, `json`, print-ready `html`, binary `pdf`, and binary `docx`.
- Download endpoint for previously created report export artifacts.
- Binary report exports are stored as base64 inside the MVP JSON/SQLite state record and decoded back to bytes by the download endpoint.
- Report section registry in `services/api-server/schemas/report.py`.
- Frontend report payload types in `apps/web/lib/report-types.ts`.
- Anonymized Hana-style regression fixture in `services/api-server/data/fixtures/hana_pf_review_anonymized.json`.
- A4 print QA controls and checklist in `docs/product/pdf-print-layout-qa.md`.
- Automated visual QA and layout verification via `VisualQAAgent` using PyMuPDF (A4 size validation, overlap/collision checks, margin boundary checks, and pixel-by-pixel visual regression comparison), fully integrated as an automated Quality Gate during the `quality_check` orchestration step.


### PF Review Orchestration

- Synchronous MVP orchestration run API:
  - `POST /projects/{project_id}/orchestrations/pf-review`
  - `GET /projects/{project_id}/orchestrations`
  - `GET /projects/{project_id}/orchestrations/{run_id}`
  - `POST /projects/{project_id}/orchestrations/{run_id}/approve`
- Run artifact structure:
  - `run_id`
  - `project_id`
  - `workflow`
  - `status`
  - `steps`
  - `agentProgress`
  - `handoffs`
  - `artifacts`
  - `errors`
  - `approval`

Frontend:

- Project detail includes an `AI orchestration` step.
- Route: `apps/web/app/project/[id]/orchestration/page.tsx`
- The screen can start a PF review run, refresh run history, select a run, and inspect developer feasibility, marketability, legal/permit, report, quality-check artifacts, and approval state.
- The screen can approve completed runs through the existing approve API with reviewer/comment input and immediate state refresh.
- Orchestration run payloads now include typed handoff envelopes and per-agent progress records for downstream UI review.
- The orchestration screen now displays `Agent Progress` and `Agent Handoffs` panels with progress bars, dependencies, latest artifacts, review flags, handoff routes, payload previews, confidence, evidence counts, blockers, assumptions, and data limitations.
- The orchestration screen can now review individual handoffs as approved, rejected, or needs revision. Reviews persist reviewer, timestamp, comment, and review history, and update related agent progress review flags.
- Final run approval is now blocked when any handoff that requires review is still pending, rejected, or marked needs-revision. The orchestration screen shows open review count and disables final approval until those handoffs are cleared.
- The orchestration screen now renders a premium `SensitivityMatricesPanel` showcasing interactive tabs for Waterfall repayment allocation simulation (by dropdown scenario selection), a color-coded 2D Unsold LTV stress heatmap (mapping LTV values to HSL risk bands: safe, caution, high, critical), and tranche-level final recovery ratios under variable sale/discount triggers.


Current limitation:

- Orchestration runs are still sequential and synchronous.
- Per-agent progress percentage, typed handoff envelopes, handoff review actions, and approval blocking for unresolved handoffs are now implemented. Remaining work is to add richer reviewer assignment, role-based permissions, and cross-run audit reporting.
- Land Ledger Agent output is available through upload/analyze, but it is not yet a required first-class step in the PF review orchestration graph.
- Land Agent output now produces a formal `land_to_feasibility_legal_pf` patch handoff for site, land cost, evidence readiness, PF land summary, acquisition status, blockers, limitations, confidence, and review state.
- Marketability output now produces a formal Feasibility assumption patch handoff, but Development Feasibility does not yet reconcile and rerun from that patch automatically.
- Orchestration now creates `reconciliationGate` artifacts to compare feasibility revenue, marketability revenue, project land area, Land Agent parcel area, official price readiness, registry readiness, market evidence readiness, and PF metric availability.
- Canonical schema registry, glossary/canonical field map, risk register agent, approved override workflow, and historical review database remain next implementation work.

### Persistent MVP State Storage

File: `services/api-server/storage.py`

Purpose:

- Persist `PROJECTS_DB`, `REPORTS_DB`, and `ORCHESTRATIONS_DB` to SQLite by default.
- Restore state at API startup before falling back to bundled fixtures.
- Keep separate SQLite tables for projects, reports, orchestrations, and metadata.
- Preserve legacy JSON state support for tests, explicit override, and first-run migration.
- Keep runtime state out of Git through `services/api-server/data/runtime/.gitignore`.

Default SQLite state path:

- `services/api-server/data/runtime/realytics.sqlite3`

Environment overrides:

- `REALYTICS_STORAGE_BACKEND=sqlite|json`
- `REALYTICS_SQLITE_FILE`
- `REALYTICS_STATE_FILE`

### Calculation and Risk Engine

Files:

- `services/api-server/core/calculations.py`
- `services/api-server/core/risk_assessment.py`

Implemented P0 calculation outputs:

- Feasibility metrics from totals or revenue/cost components.
- Development profit, profit margin, profit-on-cost, cost ratio, break-even revenue, surplus to break-even, and feasibility status.
- Monthly cash-flow builder from either net cashflows or cash-in/cash-out schedules.
- Max Minus details including amount, month, period, funding gap, cumulative CF, and ending cash.
- PF metrics bundle for LTV, LTC, DSCR, and exit sale rate.
- Tranche-level all-in cost, annual interest, fee amount, and priority ordering.
- Status bands for credit review thresholds.

Implemented P0 risk outputs:

- Basic real estate PF risk matrix.
- Category rows across land, permit, developer, contractor, trust, market, sales, construction cost, financing, exit, legal, and public sector risk.
- Top risks, 5x5 heatmap cells, recommendations, and missing-category indicators.
- Default inferred risk items when no explicit `risk_items` are provided.

APIs:

- `POST /projects/{project_id}/feasibility/calculate`
- `POST /projects/{project_id}/cashflow/calculate`
- `POST /projects/{project_id}/pf/structure`
- `POST /projects/{project_id}/risk/assess`

### Development Feasibility Agent

File: `services/api-server/agents/development_feasibility_agent.py`
Schema: `services/api-server/schemas/feasibility_study.py`

Purpose:

- Parse business plan/basic architectural plan fields into developer-side feasibility.
- Support both developer usage and PF review usage.
- Provide a migration path to `realytics-feasibility-study-v2`, the canonical schema for Excel import/export and later automatic calculation.

Outputs:

- Architectural program
- Revenue assumptions
- Cost breakdown
- Developer returns
- PF readiness
- Downstream engine readiness for Cash Flow, Waterfall, Unsold LTV, and Scenario Engine delegation
- FeasibilityStudyV2 integration status and required mapper path
- Sensitivity analysis
- Data quality warnings

Feasibility Study v2 schema:

- Backend Pydantic schema added for site, program, product mix, revenue items, cost items, financing, schedule, scenarios, workbook mapping, provenance, formulas, and metrics.
- Frontend TypeScript mirror added in `apps/web/lib/feasibility-study-types.ts`.
- Fixture added in `services/api-server/data/fixtures/feasibility_study_v2_schema_example.json`.
- Workbook template contract added as `realytics-fs-xlsx-v1`, with named ranges, repeated Excel tables, formula preservation, import provenance, and checks-sheet requirements.
- Sample-informed workbook validator added in `services/api-server/agents/feasibility_workbook_validator.py` to scan required sheets, key cells, formula errors, reconciliations, and direct-v2 import readiness before accepting Excel outputs.

API:

- `POST /projects/{project_id}/development-feasibility/analyze`

MCP:

- `analyze_development_feasibility`

2026-05-18 update:

- Development Feasibility Agent now explicitly identifies itself as the `development_feasibility_orchestrator`.
- `pfReadiness` includes `isPfReviewReady`, `requiredMissingFields`, `equityRatio`, cost ratios, `developerProfitMargin`, `estimatedExitSalesRate`, and delegation flags for Cash Flow, Waterfall, and Unsold LTV analysis.
- The agent does not directly perform Cash Flow / Waterfall / Unsold LTV calculations; it returns `downstreamEngineReadiness` and `feasibilityStudyV2Integration` so specialized engines can be called later.
- The agent now emits `feasibilityStudyV2Draft`, a schema-valid `realytics-feasibility-study-v2` draft containing site, program, revenue items, cost items, financing, schedule, scenarios, metrics, and raw import metadata.
- The draft now includes deterministic `assetCalculationProfiles` in `raw_import` so the future `FeasibilityStudyV2 -> CashFlowRunRequest` mapper can apply asset-specific revenue mode, collection schedule, recovery haircut, and LTV threshold.
- `agents/cash_flow_mapper.py` now converts `FeasibilityStudyV2` drafts into asset-aware `cashFlowRunRequestDraft` payloads with sources/uses, product mix profiles, PF structure, schedule, scenario set, diagnostics, and source metadata.
- `agents/cash_flow_engine.py` now consumes `cashFlowRunRequestDraft` and returns a baseline `cashFlowRunResult` with product-based sales collection, operating/repayment reserve account allocation, tranche-aware PF draw schedule, monthly cash flow, Max Minus, risk flags, and report fragments.
- API endpoint `POST /projects/{project_id}/cash-flow/run` runs the same engine from a direct `CashFlowRunRequest`, nested request payload, or Development Feasibility Agent-derived draft.

### Land Ledger Import Agent

File: `services/api-server/agents/land_ledger_agent.py`

Purpose:

- Parse legacy Korean land acquisition ledgers such as the Bonri `.xls` 토지조서.
- Extract parcel rows, owner/payment records, land category, official area, purchase amounts, contract dates, payment deadlines, and remarks.
- Mask sensitive account numbers after import.
- Detect missing PNU and official land price data.
- Build Land Ledger QA v1 checks for source reconciliation, parcel consistency, ownership share, payment schedule, PNU confidence, official land price base year, registry evidence, special parcel exceptions, security redaction, and PF output mapping.
- Return parcel workflow status, standardized issues, PF review mapping, and an enrichment plan for PNU resolution, cadastral characteristics, official land price, and registry/encumbrance verification.

API:

- `POST /projects/{project_id}/land-ledger/analyze`
- `POST /projects/{project_id}/land-ledger/enrich`

Important:

- The reviewed sample contains 9 parcels, 22 owner/payment records, 4,691.9 sqm, and KRW 41.374 billion total contract amount.
- The sample does not contain PNU or official land price, so the correct status is `needs_enrichment`.
- Backend batch PNU and evidence enrichment now exists through `LandLedgerEnrichmentAgent`; live public/official adapters still require API credentials and connector configuration.
- Land ledger imports must not become `ready_for_pf_review` until PNU, official price, registry evidence, security redaction, and PF output mapping pass.

2026-05-20 update:

- Added `services/api-server/agents/land_ledger_enrichment_agent.py`.
- `LandLedgerAgent.enrich_analysis` now combines parsed land ledger output with batch enrichment results and rebuilds parcel QA, workflow status, land acquisition status, and PF summary.
- `/land-ledger/enrich` can resolve PNU from a confirmed legal dong code plus normalized lot number, or from supplied PNU resolution records.
- The enrichment workflow attaches cadastral characteristics, official land price with base year/source, and registry/encumbrance evidence from connector/manual records.
- Official land price and registry evidence are not fabricated when source records are missing; missing records remain enrichment/legal/PF blockers.
- 2026-05-27 update: `services/api-server/integrations/land_data_connectors.py` adds a configurable live connector router. `useLiveConnectors=true` can call cadastral and official land price URL templates when `REALYTICS_CADASTRAL_URL_TEMPLATE`, `REALYTICS_OFFICIAL_LAND_PRICE_URL_TEMPLATE`, and API-key environment variables are configured. If configuration is missing, the run returns explicit diagnostics and continues with no fabricated official data. Live registry lookup is intentionally not implemented; registry completion still requires manual or licensed evidence.

2026-05-14 update:

- The agent now preserves import metadata including original file name/hash, parser version, row/column counts, ledger version, and validation status.
- The agent now normalizes Korean lot numbers into raw lot, mountain flag, main lot number, sub lot number, and normalized lot number.
- The agent now validates original-vs-parsed consistency before treating the import as usable.
- The agent now returns land acquisition status and a PF review summary in addition to parcel QA and enrichment planning.
- The reviewed sample returns import-level `needs_review` because parcel-level purchase amount and owner-level individual amount totals do not fully reconcile, even though area and total contract amount reconcile.
- The PF summary remains `needs_enrichment` because PNU, official price, and registry evidence coverage are all 0%.
- Land ledger imports must not become `ready_for_legal_review` or `ready_for_pf_review` until import totals reconcile, PNU confidence passes, official price has base year/source, registry evidence is attached, security redaction passes, and PF output mapping is complete.
- Web project land page now presents land review as separate sections: basic land data, source/import metadata, acquisition status, zoning/building limits, import consistency, evidence readiness, parcel review, and PF review summary.

Supported land ledger lifecycle statuses:

- `uploaded`
- `parsed`
- `parse_failed`
- `parsed_with_warnings`
- `needs_review`
- `needs_enrichment`
- `enrichment_in_progress`
- `enrichment_failed`
- `enriched`
- `needs_legal_evidence`
- `ready_for_legal_review`
- `legal_review_in_progress`
- `legal_review_completed`
- `ready_for_pf_review`

### Web Risk Management View

- Project risk page now calls the risk assessment API and displays the standard 20 PF risk management controls individually.
- Each control shows category, responsible owner, required evidence, API-assessed grade/score when available, and mitigation text when supplied.
- The page also shows overall risk grade, category-level matrix rows, top risks, and recommendations.

### Land Agent Dashboard Summary

- Main dashboard now includes Land Agent overview cards for managed projects, PNU coverage, land review readiness, and unresolved land/evidence issues.
- Main dashboard now includes a Land Agent control section for parcel/PNU identification, zoning and buildability, land acquisition status, official land price, registry evidence, and legal/PF readiness.
- Project dashboard now includes per-project land readiness cards with PNU, regulation, acquisition ratio, and registry evidence status.
- Dashboard pages use live API project data when the API server is available and fall back to local sample data when the API is offline, so land analysis remains visible in the web app.

### Marketability Analysis Agent

File: `services/api-server/agents/marketability_agent.py`

Purpose:

- Applies only to saleable real estate assets.
- Handles apartments, residential projects, officetels, knowledge industry centers, mixed-use projects, and commercial pre-sale assets.
- Skips non-saleable asset classes such as logistics and lodging.
- Current implementation is v2 baseline: it checks applicability, classifies product type, normalizes product mix, ingests manual comparable fixtures, reconciles uploaded document market evidence, compares proposed price against adjusted comparable median, estimates absorption, proposes discount triggers, estimates unsold recovery, and returns weighted score/grade.
- Marketability Agent v2 roadmap is now documented in `docs/product/marketability-agent-roadmap.md`.
- Next P0 work is full parser-field locator coverage, product-specific scoring weights, external public/partner market-data adapters, and a dedicated frontend Marketability Review page.
- 2026-05-14 update: the agent response is now v2-compatible and includes `analysisType`, product type classification, classification confidence, applicability reason, data status, missing required data, source metadata, normalized product mix, normalized comparables, price band, pricing judgement, Exit sale rate scenarios, absorption forecast, discount triggers, unsold recovery, risk flags, and report paragraph skeletons.
- 2026-05-14 update: `MarketEvidenceAgent` now reconciles uploaded document-derived market evidence before Marketability scoring and sends messages to the Orchestration, Marketability, and Feasibility agents.
- 2026-05-15 update: `MarketEvidenceAgent` now emits normalized `evidenceRecords` for product mix, sale comparables, and rent comparables, including extracted fields, source/evidence IDs, related validation checks, and validation status.
- 2026-05-15 update: market evidence records are now persisted in JSON/SQLite runtime state and can be queried through `GET /projects/{project_id}/market-evidence`.
- 2026-05-15 update: persisted market evidence records can now be reviewed through `POST /projects/{project_id}/market-evidence/review`, and the orchestration page displays evidence records with approve, reject, and needs-revision actions.
- 2026-05-15 update: uploaded business plan parsing now records original file name, SHA-256 hash, content type, file size, upload timestamp, parser version, and source document metadata that flows into market evidence records.
- 2026-05-15 update: uploaded source documents are now archived in runtime storage and can be listed/downloaded through project source document APIs for audit review.
- 2026-05-18 update: ParserAgent `realytics-parser-v2.2` introduced extracted PDF page markers, and market evidence records now persist `sourceLocator` metadata with page, table, row, column, field name, text excerpt, and confidence where available.
- 2026-05-18 update: ParserAgent `realytics-parser-v2.4` now asks for a full `document_market_evidence` payload and top-level source locator map, upload parsing hydrates document evidence from extracted market/product-mix fields, and MarketabilityAgent normalizes manual, transaction, presale, and listing comparable arrays with category/source/price-basis metadata.
- 2026-05-18 update: MarketabilityAgent now produces structured Exit sale rate discount scenarios, tranche sale-rate thresholds, absorption base/downside/stress coverage, trigger reasons, product-type unsold recovery scenarios, unsold LTV, and tranche recovery.
- 2026-05-18 update: MarketabilityAgent now returns product-specific `marketability_score_v2` breakdowns, lender risk flag details, and report paragraphs generated from pricing, demand, supply, Exit, absorption, trigger, and unsold recovery outputs.
- 2026-05-18 update: Marketability source metadata now preserves source name/type/evidence ID/collected date/base date/license note/confidence plus complete/partial status and missing-field summaries across normalized comparables and market evidence records.
- 2026-05-18 update: MarketEvidenceAgent now adds field-level `fieldSourceLocators`, record `locatorCoverage`, and summary `fieldLocatorCoverage` for product mix, sale comparable, and rent comparable evidence, including page/table/row inference from evidence IDs.
- 2026-05-26 update: Marketability pricing-basis hardening now derives proposed price per py from product mix total revenue/area where possible and emits `PROPOSED_PRICE_BASIS_MISSING` instead of `NO_COMPARABLE_DATA` when comparable evidence exists but the target project's pricing basis is missing.
- Serviced residence is treated as applicable when it is a strata-sale/presale asset; operating hotel/lodging remains non-saleable and should be handled by a separate operating asset Agent.

API:

- `POST /projects/{project_id}/marketability/analyze`
- `GET /projects/{project_id}/source-documents`
- `GET /projects/{project_id}/source-documents/{archive_id}/download`
- `GET /projects/{project_id}/market-evidence`
- `POST /projects/{project_id}/market-evidence/review`

MCP:

- `analyze_marketability`

### Legal/Permit Review Agent

File: `services/api-server/agents/legal_review_agent.py`

Purpose:

- Preliminary Korean real estate PF legal and permit review.
- Checks zoning/FAR/BCR, permit schedule, land rights, trust/security, covenants, EOD, and execution order.
- Includes a Korean Law MCP sidecar context hook through normalized `law_context`.
- Adds `realytics-law-context-v1` normalization for Korean Law MCP outputs.
- Provides a query-plan generator so Claude Desktop can call Korean Law MCP before REALYTICS legal review.

Important:

- Korean Law MCP is intentionally not vendored into this repository.
- The recommended deployment is a sidecar MCP server plus REALYTICS adapter.
- The P0 connector scope is complete for sidecar mode: query planning, normalized `realytics-law-context-v1`, citation-verification handling, and legal-review integration are implemented.
- The agent does not pretend to have authoritative legal sources when `law_context` is missing.
- It returns `connected: false` and caution findings when no MCP legal context is supplied.

API:

- `POST /projects/{project_id}/legal/review`

MCP:

- `review_korean_real_estate_law`
- `get_korean_law_mcp_query_plan`

Docs:

- `docs/integrations/korean-law-mcp.md`

### Parser Agent and AI Connection

Files:

- `services/api-server/agents/parser_agent.py`
- `services/api-server/agents/ai_connection.py`

Supported extraction hints:

- `architectural_plan`
- `sales_assumptions`
- `cost_assumptions`

Supported AI connection modes:

- `openai`: direct OpenAI API extraction when `OPENAI_API_KEY` is configured.
- `anthropic`: direct Claude API extraction when `ANTHROPIC_API_KEY` is configured.
- `claude_desktop`: Claude Desktop hosts MCP tool calls. The server returns `host_llm_extraction` with PDF text and extraction instructions for Claude Desktop to complete.

API/MCP user linkage:

- API upload accepts `X-REALYTICS-USER-ID` and `X-REALYTICS-AI-PROVIDER`.
- API connection check: `POST /projects/{project_id}/ai/connection/resolve`.
- MCP tools accept `user_id`, `ai_provider`, and `ai_context`.
- MCP environment can set `MCP_USER_ID` and `REALYTICS_AI_PROVIDER=claude_desktop`.

### Upload-to-Run Flow

- API: `POST /projects/{project_id}/upload-and-run`
- Frontend: project detail file upload panel supports both document analysis and analyze-then-run PF review.
- The endpoint parses the uploaded business plan/basic architectural plan, updates the project, runs PF review orchestration, persists the run, and saves the Phase 11 report artifact.
- The original uploaded PDF is retained under runtime upload archive storage, and the project stores archive ID/download URL metadata for source traceability.
- In `claude_desktop` mode it returns `host_llm_required` instead of pretending the server can call the Claude Desktop host model.

## Current Orchestration Steps

1. `project_load`: Load project data.
2. `land_agent_review`: Review land acquisition and evidence readiness.
3. `development_feasibility_analysis`: Analyze developer-side feasibility from business/basic architectural plan fields.
4. `feasibility_calculation`: Run core feasibility calculations.
5. `pf_structure_calculation`: Calculate PF tranche and loan structure metrics.
6. `market_evidence_reconciliation`: Reconcile uploaded document-derived market evidence before Marketability scoring.
7. `marketability_analysis`: Validate marketability for saleable real estate assets.
8. `data_quality_review`: Detect numeric conflicts across documents, inputs, and Agent outputs.
9. `cross_agent_reconciliation`: Reconcile cross-Agent assumptions before legal/risk/report steps.
10. `legal_permit_review`: Run preliminary legal, permit, land-rights, trust-security, and covenant checks.
11. `risk_assessment`: Produce a basic risk matrix.
12. `risk_register_generation`: Consolidate Agent risks into one credit-review table.
13. `credit_review_decision`: Produce approve/conditional/rework/reject credit guidance.
14. `scenario_restructuring_plan`: Quantify senior shortfall and unsold LTV cure actions before approval.
15. `phase11_report_generation`: Generate the Phase 11 PF review report artifact.
16. `evidence_graph_generation`: Link evidence, claims, handoffs, and report sections.
17. `quality_check`: Check required artifacts, report completeness, and PDF QA.
18. `approval_readiness_gate`: Check whether the run is ready for credit approval.

## Verification

Latest verification baseline:

- Cash Flow Engine direct smoke: passed against `daegu_bondong_mixed_use_pf.json`; generated 36 monthly rows and 10 PF draw rows with `funding_gap_covered` status.
- Python compile check: passed for Cash Flow Engine, mapper, Development Feasibility Agent, project router, and targeted test file.
- Expanded use-case smoke: passed for marketability, legal review, report generation/export, evidence review, and orchestration approval use cases.
- Repository use-case smoke: passed for report create/export, market evidence add/list/review, orchestration save, and orchestration approval using in-memory repository stores.
- Project/source-document repository compile and smoke: passed for project create/update/apply/delete, project-scoped cleanup, source metadata save/list/find, and project router import compilation.
- Web Land Agent dashboard verification: `npm run lint --workspace web` passed, `npm run check-types --workspace web` passed, and browser DOM checks confirmed the main dashboard and project dashboard render the new land analysis sections.
- Report export renderer verification: Python compile passed for report export agent/router/tests; direct export smoke passed for markdown, JSON, HTML, PDF, and DOCX with JSON-serializable storage records and binary decode checks.
- PDF/DOCX Visual Regression Verification: passed automated structural and visual regression tests under `tests/test_visual_regression.py` using PyMuPDF (A4 dimension checking, paragraph overlap checks, vertical/horizontal margin boundary checks, and pixel-by-pixel regression baselines).


- Land Ledger targeted test: `1 passed`
- Real sample land ledger validation: executed against `01.본리동토지조서.xls`; status `needs_review`, PF summary `needs_enrichment`
- API tests: `60 passed`
- Web lint: passed
- Web type check: passed
- Web build: passed
- Local dev servers: stopped as of 2026-05-13 after the user requested shutdown.
- Current bundled Python runtime does not include `pytest` or `fastapi`, so the latest endpoint pytest run and FastAPI TestClient smoke could not be executed in this session.

## Current Limitations

- Data storage is SQLite-backed for MVP. PostgreSQL migration, indexing strategy, and multi-tenant isolation are not yet implemented.
- Orchestration runs are synchronous, not background jobs.
- Generated reports and orchestration runs persist through SQLite tables, but do not yet have rich relational querying, migrations, or multi-user isolation.
- Frontend displays orchestration run detail, agent artifacts, and approval actions; deeper approval audit trails are not yet modeled beyond the run approval record.
- Korean Law MCP sidecar integration is complete for P0. Direct API-server MCP client automation is optional future hardening, not a blocker for the current connector.
- ParserAgent requires either `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` for real server-side document extraction; otherwise OpenAI/Anthropic modes return explicit mock metadata.
- Claude Desktop's local host model cannot be invoked from inside the MCP server process. In `claude_desktop` mode the MCP server returns a `host_llm_required` payload so Claude Desktop can perform the extraction in the conversation.
- Source-level provenance per parsed field is not yet persisted.
- Markdown, JSON, print-ready HTML, PDF, and DOCX report exports are implemented from the same structured Phase 11 report record.
- Calculation Scenario Engine: fully implemented and documented. Features schemas (`schemas/scenario.py`), coordination wrapper (`ScenarioEngineAgent` inside `agents/scenario_engine_agent.py`), API endpoints (`/scenarios/waterfall`, `/scenarios/unsold-ltv-matrix`, `/scenarios/recovery`, `/scenarios/restructuring`), SQLite persistence, risk flag compilation, and Korean Hana-style PF review report synthesis.
- Restructuring Agent: implemented and exposed through `/scenarios/restructuring` to convert rejection-driving scenario flags into quantified cure actions before rerunning PF review. It does not override Credit Review; it provides the numerical path for senior exposure reduction, DSRA/equity support, PF exposure reduction, collateral enhancement, forward sale, bulk sale, or refinancing evidence.
- Cash Flow Engine / Cash Flow Agent design is documented in `docs/product/cash-flow-engine-agent.md`; it aligns FeasibilityStudyV2 with monthly cash flow, Max Minus, PF draw, sales collection, account allocation, waterfall, Exit sale rate, unsold LTV, risk flags, API, UI, and report fragments.
- Baseline Cash Flow Engine run is implemented through the required upstream path `DevelopmentFeasibilityAgent -> FeasibilityStudyV2 -> CashFlowRunRequest -> Cash Flow Engine`, so Excel import/export, workbook formulas, provenance, and PF review outputs share one canonical feasibility source.
- Advanced scenario persistence, waterfall API workflow, unsold LTV matrix workflow, tranche recoveries, and Korean scenario report fragments are complete. Next step: frontend Scenario Review UI and Cash Flow Review UI.

## Next Work

### P0

- Harden PDF/DOCX visual QA with representative long legal clauses and real institution templates.
- Add first-class per-user auth/session storage instead of env/header-only `user_id` linkage.
- Extend parser provenance from market evidence fields to non-market extracted fields so land, regulation, cost, schedule, PF, and covenant values can be traced back to uploaded documents.

### P1

- Run the first real pilot using `docs/product/pilot-project-runbook.md` and replace template evidence with the user's actual anonymized project package.
- Add Excel-style developer feasibility export.
- Generate/import the `realytics-fs-xlsx-v1` feasibility workbook with formulas preserved.
- Add legacy adapter for the Bonri-style Korean workbook cell map so existing 사업수지 files can import into `FeasibilityStudyV2`.
- Add persistent land ledger import records, approval workflow, evidence tables, and backend batch PNU/official price/registry enrichment for imported land ledger parcels.
- Add real anonymized business plan and basic architectural plan fixtures for parser and feasibility regression tests.
- Add standalone UI pages for Development Feasibility, Marketability, Legal Review, and Orchestration artifacts.
- Convert synchronous orchestration into async queue-based jobs.
- Add frontend Scenario Review UI for waterfall, unsold LTV matrix, and tranche recovery outputs.
- Add Cash Flow Review UI for saved latest runs and best/base/downside/stress/recovery scenario comparison.
- Extend scenario engine with richer product-type recovery haircuts and lender-specific threshold presets.
- Optionally add API-server-side MCP client automation for Korean Law MCP if Claude Desktop-mediated sidecar operation is not sufficient.

### P2

- Add user/organization workspace and RBAC.
- Add source citation/provenance views in the frontend.
- Add private institution-specific credit policy rules.
- Add deployment pipeline for staging and production.

## Git Note

Latest committed implementation baseline before this work: `d9e0c0f Add SQLite runtime storage`.
