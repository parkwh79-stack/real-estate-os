# REALYTICS x PLATFORM9 Current Implementation Status

Updated: 2026-05-13

## Summary

The platform has an MVP-level PF review orchestration flow that can serve both financial institution PF review and real estate developer feasibility review.

The current implementation is synchronous and uses file-backed JSON state storage for projects, reports, and orchestration runs. Agent boundaries, API endpoints, MCP tools, tests, and documentation are now structured so the system can later move to SQLite/PostgreSQL, async jobs, and stronger UI-based monitoring.

The AI connection layer records provider, runtime mode, model, host, and linked `user_id` for API uploads, MCP document parsing, and orchestration runs.

## Implemented

### Phase 11 PF Review Report

- 20-section Hana-style PF review report structure.
- Structured API response for `POST /projects/{project_id}/reports/generate`.
- Markdown compatibility output through `report_markdown`.
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

- Persist `PROJECTS_DB`, `REPORTS_DB`, and `ORCHESTRATIONS_DB` to JSON state.
- Restore state at API startup before falling back to bundled fixtures.
- Keep runtime state out of Git through `services/api-server/data/runtime/.gitignore`.

Default state path:

- `services/api-server/data/runtime/state.json`

Environment override:

- `REALYTICS_STATE_FILE`

### Calculation and Risk Engine

Files:

- `services/api-server/core/calculations.py`
- `services/api-server/core/risk_assessment.py`

Implemented P0 calculation outputs:

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

- `POST /projects/{project_id}/cashflow/calculate`
- `POST /projects/{project_id}/pf/structure`
- `POST /projects/{project_id}/risk/assess`

### Development Feasibility Agent

File: `services/api-server/agents/development_feasibility_agent.py`

Purpose:

- Parse business plan/basic architectural plan fields into developer-side feasibility.
- Support both developer usage and PF review usage.

Outputs:

- Architectural program
- Revenue assumptions
- Cost breakdown
- Developer returns
- PF readiness
- Sensitivity analysis
- Data quality warnings

API:

- `POST /projects/{project_id}/development-feasibility/analyze`

MCP:

- `analyze_development_feasibility`

### Marketability Analysis Agent

File: `services/api-server/agents/marketability_agent.py`

Purpose:

- Applies only to saleable real estate assets.
- Handles apartments, residential projects, officetels, knowledge industry centers, mixed-use projects, and commercial pre-sale assets.
- Skips non-saleable asset classes such as logistics and lodging.

API:

- `POST /projects/{project_id}/marketability/analyze`

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
- In `claude_desktop` mode it returns `host_llm_required` instead of pretending the server can call the Claude Desktop host model.

## Current Orchestration Steps

1. `project_load`: Load project data.
2. `development_feasibility_analysis`: Analyze developer-side feasibility from business/basic architectural plan fields.
3. `feasibility_calculation`: Run core feasibility calculations.
4. `pf_structure_calculation`: Calculate PF tranche and loan structure metrics.
5. `marketability_analysis`: Validate marketability for saleable real estate assets.
6. `legal_permit_review`: Run preliminary legal, permit, land-rights, trust-security, and covenant checks.
7. `risk_assessment`: Produce a basic risk matrix.
8. `phase11_report_generation`: Generate the Phase 11 PF review report artifact.
9. `quality_check`: Check required artifacts and report completeness.

## Verification

Latest verification baseline:

- API tests: `41 passed`
- Web lint: passed
- Web type check: passed
- Web build: passed
- Local dev servers: stopped as of 2026-05-13 after the user requested shutdown.

## Current Limitations

- Data storage is file-backed JSON for MVP, not yet relational DB.
- Orchestration runs are synchronous, not background jobs.
- Generated reports and orchestration runs persist through `state.json`, but do not yet have relational querying, migrations, or multi-user isolation.
- Frontend displays orchestration run detail, agent artifacts, and approval actions; deeper approval audit trails are not yet modeled beyond the run approval record.
- Korean Law MCP sidecar is represented through query planning and normalized `law_context`; direct automated MCP client calls are not yet built into the API server.
- ParserAgent requires either `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` for real server-side document extraction; otherwise OpenAI/Anthropic modes return explicit mock metadata.
- Claude Desktop's local host model cannot be invoked from inside the MCP server process. In `claude_desktop` mode the MCP server returns a `host_llm_required` payload so Claude Desktop can perform the extraction in the conversation.
- Source-level provenance per parsed field is not yet persisted.
- PDF/DOCX/Excel exports are not yet implemented from the structured report data.

## Next Work

### P0

- Add PDF/DOCX export from the structured Phase 11 report data.
- Add persistent report versioning and export history.
- Replace JSON state storage with SQLite or PostgreSQL.
- Add first-class per-user auth/session storage instead of env/header-only `user_id` linkage.
- Decide whether Korean Law MCP remains Claude Desktop-mediated only or gets optional API-server-side MCP client automation.
- Add parser provenance per extracted field so report values can be traced back to uploaded documents.

### P1

- Add Excel-style developer feasibility export.
- Add real anonymized business plan and basic architectural plan fixtures for parser and feasibility regression tests.
- Add standalone UI pages for Development Feasibility, Marketability, Legal Review, and Orchestration artifacts.
- Convert synchronous orchestration into async queue-based jobs.
- Add UI/API scenario workflow for waterfall and unsold LTV matrix.

### P2

- Add user/organization workspace and RBAC.
- Add source citation/provenance views in the frontend.
- Add private institution-specific credit policy rules.
- Add deployment pipeline for staging and production.

## Git Note

Latest committed implementation baseline before this work: `8e2758a Add orchestration approval UI`.
