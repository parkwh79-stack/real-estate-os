# REALYTICS x PLATFORM9 Current Implementation Status

Updated: 2026-05-14

## Summary

The platform has an MVP-level PF review orchestration flow that can serve both financial institution PF review and real estate developer feasibility review.

The current implementation is synchronous and uses SQLite-backed state storage for projects, reports, and orchestration runs. Agent boundaries, API endpoints, MCP tools, tests, and documentation are now structured so the system can later move to PostgreSQL, async jobs, and stronger UI-based monitoring.

The AI connection layer records provider, runtime mode, model, host, and linked `user_id` for API uploads, MCP document parsing, and orchestration runs.

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

### Phase 11 PF Review Report

- 20-section Hana-style PF review report structure.
- Structured API response for `POST /projects/{project_id}/reports/generate`.
- Markdown compatibility output through `report_markdown`.
- Saved report version numbers and export history metadata.
- Export artifact creation for `markdown`, `json`, and print-ready `html`.
- Download endpoint for previously created report export artifacts.
- Report section registry in `services/api-server/schemas/report.py`.
- Frontend report payload types in `apps/web/lib/report-types.ts`.
- Anonymized Hana-style regression fixture in `services/api-server/data/fixtures/hana_pf_review_anonymized.json`.
- A4 print QA controls and checklist in `docs/product/pdf-print-layout-qa.md`.

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
  - `artifacts`
  - `errors`
  - `approval`

Frontend:

- Project detail includes an `AI orchestration` step.
- Route: `apps/web/app/project/[id]/orchestration/page.tsx`
- The screen can start a PF review run, refresh run history, select a run, and inspect developer feasibility, marketability, legal/permit, report, quality-check artifacts, and approval state.
- The screen can approve completed runs through the existing approve API with reviewer/comment input and immediate state refresh.

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

Important:

- The reviewed sample contains 9 parcels, 22 owner/payment records, 4,691.9 sqm, and KRW 41.374 billion total contract amount.
- The sample does not contain PNU or official land price, so the correct status is `needs_enrichment`.
- The existing frontend PNU lookup is single-PNU and land-use focused; backend batch PNU and official land price enrichment is still required.
- Land ledger imports must not become `ready_for_pf_review` until PNU, official price, registry evidence, security redaction, and PF output mapping pass.

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
- 2026-05-18 update: ParserAgent `realytics-parser-v2.3` now asks for a full `document_market_evidence` payload, upload parsing hydrates document evidence from extracted market/product-mix fields, and MarketabilityAgent normalizes manual, transaction, presale, and listing comparable arrays with category/source/price-basis metadata.
- 2026-05-18 update: MarketabilityAgent now produces structured Exit sale rate discount scenarios, tranche sale-rate thresholds, absorption base/downside/stress coverage, trigger reasons, product-type unsold recovery scenarios, unsold LTV, and tranche recovery.
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
2. `development_feasibility_analysis`: Analyze developer-side feasibility from business/basic architectural plan fields.
3. `feasibility_calculation`: Run core feasibility calculations.
4. `pf_structure_calculation`: Calculate PF tranche and loan structure metrics.
5. `market_evidence_reconciliation`: Reconcile uploaded document-derived market evidence before Marketability scoring.
6. `marketability_analysis`: Validate marketability for saleable real estate assets.
7. `legal_permit_review`: Run preliminary legal, permit, land-rights, trust-security, and covenant checks.
8. `risk_assessment`: Produce a basic risk matrix.
9. `phase11_report_generation`: Generate the Phase 11 PF review report artifact.
10. `quality_check`: Check required artifacts and report completeness.

## Verification

Latest verification baseline:

- Land Ledger targeted test: `1 passed`
- Real sample land ledger validation: executed against `01.본리동토지조서.xls`; status `needs_review`, PF summary `needs_enrichment`
- API tests: `60 passed`
- Web lint: passed
- Web type check: passed
- Web build: passed
- Local dev servers: stopped as of 2026-05-13 after the user requested shutdown.

## Current Limitations

- Data storage is SQLite-backed for MVP. PostgreSQL migration, indexing strategy, and multi-tenant isolation are not yet implemented.
- Orchestration runs are synchronous, not background jobs.
- Generated reports and orchestration runs persist through SQLite tables, but do not yet have rich relational querying, migrations, or multi-user isolation.
- Frontend displays orchestration run detail, agent artifacts, and approval actions; deeper approval audit trails are not yet modeled beyond the run approval record.
- Korean Law MCP sidecar integration is complete for P0. Direct API-server MCP client automation is optional future hardening, not a blocker for the current connector.
- ParserAgent requires either `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` for real server-side document extraction; otherwise OpenAI/Anthropic modes return explicit mock metadata.
- Claude Desktop's local host model cannot be invoked from inside the MCP server process. In `claude_desktop` mode the MCP server returns a `host_llm_required` payload so Claude Desktop can perform the extraction in the conversation.
- Source-level provenance per parsed field is not yet persisted.
- Markdown, JSON, and print-ready HTML report exports are implemented. PDF/DOCX binary renderers are not yet implemented from the structured report data.

## Next Work

### P0

- Add PDF/DOCX binary renderers from the structured Phase 11 report export pipeline.
- Add first-class per-user auth/session storage instead of env/header-only `user_id` linkage.
- Add parser provenance per extracted field so report values can be traced back to uploaded documents.

### P1

- Add Excel-style developer feasibility export.
- Generate/import the `realytics-fs-xlsx-v1` feasibility workbook with formulas preserved.
- Add legacy adapter for the Bonri-style Korean workbook cell map so existing 사업수지 files can import into `FeasibilityStudyV2`.
- Add persistent land ledger import records, approval workflow, evidence tables, and backend batch PNU/official price/registry enrichment for imported land ledger parcels.
- Add real anonymized business plan and basic architectural plan fixtures for parser and feasibility regression tests.
- Add standalone UI pages for Development Feasibility, Marketability, Legal Review, and Orchestration artifacts.
- Convert synchronous orchestration into async queue-based jobs.
- Add UI/API scenario workflow for waterfall and unsold LTV matrix.
- Optionally add API-server-side MCP client automation for Korean Law MCP if Claude Desktop-mediated sidecar operation is not sufficient.

### P2

- Add user/organization workspace and RBAC.
- Add source citation/provenance views in the frontend.
- Add private institution-specific credit policy rules.
- Add deployment pipeline for staging and production.

## Git Note

Latest committed implementation baseline before this work: `d9e0c0f Add SQLite runtime storage`.
