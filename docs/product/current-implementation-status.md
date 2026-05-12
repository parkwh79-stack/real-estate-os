# REALYTICS x PLATFORM9 Current Implementation Status

Updated: 2026-05-12

## Summary

The platform now has an MVP-level PF review orchestration flow that can serve both financial institution PF review and real estate developer feasibility review.

The current implementation is synchronous and now has file-backed JSON state storage for projects, reports, and orchestration runs. The agent boundaries, API endpoints, MCP tools, tests, and documentation have been structured so the system can later move to SQLite/PostgreSQL, async jobs, and UI-based run monitoring.

The AI connection layer now records the selected provider, runtime mode, model, host, and linked `user_id` for API uploads, MCP document parsing, and orchestration runs.

## Implemented

### Phase 11 PF Review Report

- 20-section Hana-style PF review report structure.
- Structured API response for `POST /projects/{project_id}/reports/generate`.
- Markdown compatibility output through `report_markdown`.
- Report metadata storage in in-memory `REPORTS_DB`.
- Report section registry in `services/api-server/schemas/report.py`.

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

- Project detail now includes an `AI 오케스트레이션` step.
- Route: `apps/web/app/project/[id]/orchestration/page.tsx`
- The screen can start a PF review run, refresh run history, select a run, and inspect developer feasibility, marketability, legal/permit, report, and quality-check artifacts.

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
- Includes a Korean Law MCP context hook through `law_context`.

Important:

- A real Korean Law MCP tool is not currently available in the execution environment.
- The agent does not pretend to have authoritative legal sources when `law_context` is missing.
- It returns `connected: false` and caution findings when no MCP legal context is supplied.

API:

- `POST /projects/{project_id}/legal/review`

MCP:

- `review_korean_real_estate_law`

### Parser Agent Improvements

File: `services/api-server/agents/parser_agent.py`

Supporting file: `services/api-server/agents/ai_connection.py`

Added extraction hints for:

- `architectural_plan`
- `sales_assumptions`
- `cost_assumptions`

Mock fallback now includes `_meta.parsing_source = "mock"` and `_meta.ai_connection` so downstream reports can identify non-real server-side parsed data and the user/provider context.

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
- Frontend: project detail file upload panel now supports both "document analysis" and "analyze then run PF review".
- The endpoint parses the uploaded business plan/basic architectural plan, updates the project, runs PF review orchestration, persists the run, and saves the Phase 11 report artifact.
- In `claude_desktop` mode it returns `host_llm_required` instead of pretending the server can call the Claude Desktop host model.

## Current Orchestration Steps

1. `project_load`: 프로젝트 데이터 로드
2. `development_feasibility_analysis`: 디벨로퍼 사업수지분석
3. `feasibility_calculation`: 사업성 계산
4. `pf_structure_calculation`: PF 구조 계산
5. `marketability_analysis`: 분양성 검증
6. `legal_permit_review`: 법률/인허가 예비 검토
7. `risk_assessment`: 리스크 평가
8. `phase11_report_generation`: Phase 11 보고서 생성
9. `quality_check`: 품질 점검

## Verification

Latest verification:

- API tests: `26 passed`
- Web lint: passed
- Web type check: passed
- Web build: passed

## Current Limitations

- Data storage is file-backed JSON for MVP, not yet relational DB.
- Orchestration runs are synchronous, not background jobs.
- Generated reports and orchestration runs persist through `state.json`, but do not yet have relational querying, migrations, or multi-user isolation.
- Frontend displays orchestration run detail and agent artifacts; approval action UI is not yet implemented.
- Korean Law MCP is only represented as a pluggable `law_context` hook.
- ParserAgent requires either `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` for real server-side document extraction; otherwise OpenAI/Anthropic modes return explicit mock metadata.
- Claude Desktop's local host model cannot be invoked from inside the MCP server process. In `claude_desktop` mode the MCP server returns a `host_llm_required` payload so Claude Desktop can perform the extraction in the conversation.

## Next Work

### P0

- Replace JSON state storage with SQLite or PostgreSQL.
- Add approval action UI to the frontend orchestration run dashboard.
- Add persistent report versioning.
- Add Korean Law MCP connector integration once the tool is available.
- Add first-class per-user auth/session storage instead of env/header-only `user_id` linkage.

### P1

- Add PDF/DOCX export from the structured Phase 11 report data.
- Add Excel-style developer feasibility table export.
- Add real anonymized business plan fixtures for parser and feasibility regression tests.
- Add UI pages for Development Feasibility, Marketability, Legal Review, and Orchestration artifacts.

### P2

- Convert synchronous orchestration into async queue-based jobs.
- Add user/organization workspace and RBAC.
- Add source citation/provenance per parsed field.
- Add private institution-specific credit policy rules.

## Git Note

The latest source changes are present in the workspace. Git staging/commit may still require elevated permission in this local environment. Python `__pycache__` artifacts generated by tests should be excluded before the next commit.
