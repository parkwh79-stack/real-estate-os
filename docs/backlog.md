# Product Backlog

Updated: 2026-05-13

| Priority | Module | Task | Status | Notes |
|---|---|---|---|---|
| P0 | Core | Project CRUD | Done | JSON-backed MVP CRUD supports create, list, get, update, delete, metadata, and related report/run cleanup |
| P0 | Calculation | Feasibility calculation | Done | Pure feasibility metrics and API return revenue, cost, profit, margin, profit-on-cost, break-even, and status |
| P0 | Calculation | Monthly Cash Flow | Done | Structured monthly CF builder supports net cashflows or cash-in/cash-out schedules |
| P0 | Calculation | Max Minus | Done | Returns max minus amount, month, period, funding gap, cumulative CF, and ending cash |
| P0 | Calculation | LTV/LTC/DSCR | Done | PF metrics return values, status bands, tranche all-in cost, debt service, and summary |
| P0 | Calculation | Exit Sale Rate | Done | Exit sale rate included in PF metrics with credit status thresholds |
| P0 | Risk | Basic Risk Matrix | Done | Risk matrix returns category rows, top risks, heatmap, recommendations, and inferred default project risks |
| P0 | Agent | Development Feasibility Agent | Done | Parses business/basic architectural plan fields into developer-side feasibility, PF readiness, and sensitivity outputs |
| P0 | Report | Phase 11 PF Review Report | Done | 20-section Hana-style PF review report implemented in web UI, structured API, saved report versions, and markdown/json/html export artifacts |
| P0 | Orchestration | PF Review Run API | Done | Synchronous MVP run covers load, feasibility, PF structure, risk, Phase 11 report, quality check, approval |
| P0 | Agent | Marketability Analysis Agent | Done | Applies to saleable assets such as apartment, officetel, knowledge industry center, mixed-use, commercial pre-sale projects |
| P0 | Agent | Korean Legal/Permit Review Agent | Done | Preliminary legal, permit, land-rights, trust-security, covenant review with Korean Law MCP context hook |
| P0 | Report | Report fixture data | Done | Added anonymized Hana-style PF review fixture and regression checks |
| P0 | Storage | Persistent project/report/run storage | Done | SQLite-backed storage added for projects, reports, and orchestration runs; legacy JSON fallback/migration retained |
| P0 | Orchestration | Upload-to-run flow | Done | PDF business plan/basic architectural plan upload -> parse -> developer feasibility -> PF review run via `/upload-and-run` |
| P0 | Frontend | Orchestration run dashboard | Done | Project detail includes AI orchestration page with run start, run list, agent artifacts, quality checks, and approval state |
| P0 | Frontend | Orchestration approval action UI | Done | Existing approve API wired into run dashboard with reviewer/comment input, submit button, approved-state refresh, and disabled state after approval |
| P0 | Integration | Korean Law MCP connector | In Progress | Sidecar integration guide, query plan tool, and `realytics-law-context-v1` adapter added; direct MCP client automation remains optional |
| P0 | AI | Claude Desktop MCP user linkage | Done | MCP/API AI context records provider, host mode, model, and linked `user_id`; Claude Desktop mode returns host LLM extraction requests, Anthropic API key enables server-side Claude extraction |
| P0 | Auth | User identity provider integration | Planned | Replace env/header-only user ID with login, tenant, session, and audit tables |
| P0 | Report | Structured report export pipeline | In Progress | Markdown/JSON/print-ready HTML export history added; PDF/DOCX binary renderers remain next |
| P0 | Parser | Source provenance per parsed field | Next | Persist page/source/confidence metadata for uploaded business plan/basic architectural plan fields |
| P0 | Report | PDF print layout QA | Done | Added A4 print CSS, table/section break controls, approval block handling, and QA checklist |
| P1 | Export | Excel upload | Planned | |
| P1 | Export | Developer feasibility Excel export | Planned | Export developer-side feasibility table, sensitivity, PF readiness |
| P1 | Export | PDF export | Planned | |
| P1 | Export | DOCX export | Planned | |
| P1 | Calculation | Waterfall simulator | In Progress | Pure function exists; UI/API scenario workflow still pending |
| P1 | Calculation | Unsold LTV Matrix | In Progress | Pure function exists; scenario matrix UI/API still pending |
| P1 | Logistics | Logistics module | Planned | Special fields & formulas |
| P1 | Report | Report template editor | Planned | |
| P1 | QA | Parser regression fixtures | Planned | Anonymized business plan/basic architectural plan documents for parser QA |
| P1 | API | 20-section report generation API | Done | `/reports/generate` returns structured Phase 11 sections plus Markdown compatibility output |
| P2 | Integration| External API integration | Planned | Full mapping |
| P2 | Auth | Organization workspace | Planned | |
| P2 | Auth | RBAC | Planned | |
| P2 | AI | Private AI knowledge base | Planned | |
| P2 | GIS | Advanced GIS | Planned | |
| P2 | Review | Public sector review | Planned | |
| P3 | Core | Full ordinance automation | Planned | |
| P3 | Core | Scenario optimization | Planned | |
| P3 | Dashboard| Portfolio monitoring | Planned | |
| P3 | Deployment| Enterprise deployment | Planned | |
| P3 | Payment | Payment system | Planned | |
