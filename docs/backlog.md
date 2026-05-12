# Product Backlog

| Priority | Module | Task | Status | Notes |
|---|---|---|---|---|
| P0 | Core | Project CRUD | In Progress | In-memory or simple DB for MVP |
| P0 | Calculation | Feasibility calculation | In Progress | Requires pure functions and tests |
| P0 | Calculation | Monthly Cash Flow | In Progress | |
| P0 | Calculation | Max Minus | In Progress | |
| P0 | Calculation | LTV/LTC/DSCR | In Progress | |
| P0 | Calculation | Exit Sale Rate | In Progress | |
| P0 | Risk | Basic Risk Matrix | In Progress | |
| P0 | Agent | Development Feasibility Agent | Done | Parses business/basic architectural plan fields into developer-side feasibility, PF readiness, and sensitivity outputs |
| P0 | Report | Phase 11 PF Review Report | In Progress | 20-section Hana-style PF review report implemented in web UI and structured API; export hardening remains |
| P0 | Orchestration | PF Review Run API | Done | Synchronous MVP run covers load, feasibility, PF structure, risk, Phase 11 report, quality check, approval |
| P0 | Agent | Marketability Analysis Agent | Done | Applies to saleable assets such as apartment, officetel, knowledge industry center, mixed-use, commercial pre-sale projects |
| P0 | Agent | Korean Legal/Permit Review Agent | Done | Preliminary legal, permit, land-rights, trust-security, covenant review with Korean Law MCP context hook |
| P0 | Report | Report fixture data | Planned | Add anonymized real-document fixture for regression checks |
| P0 | Storage | Persistent project/report/run storage | In Progress | JSON state storage added; next step is SQLite/PostgreSQL |
| P0 | Orchestration | Upload-to-run flow | Planned | PDF business plan/basic architectural plan upload -> parse -> developer feasibility -> PF review run |
| P0 | Frontend | Orchestration run dashboard | Done | Project detail includes AI orchestration page with run start, run list, agent artifacts, quality checks |
| P0 | Integration | Korean Law MCP connector | Planned | Connect real legal source retrieval into `law_context` for LegalReviewAgent |
| P0 | AI | Claude Desktop MCP user linkage | Done | MCP/API AI context records provider, host mode, model, and linked `user_id`; Claude Desktop mode returns host LLM extraction requests, Anthropic API key enables server-side Claude extraction |
| P0 | Auth | User identity provider integration | Planned | Replace env/header-only user ID with login, tenant, session, and audit tables |
| P1 | Report | PDF print layout QA | Planned | A4 pagination, table overflow, section page-break checks |
| P1 | Export | Excel upload | Planned | |
| P1 | Export | Developer feasibility Excel export | Planned | Export developer-side feasibility table, sensitivity, PF readiness |
| P1 | Export | PDF export | Planned | |
| P1 | Export | DOCX export | Planned | |
| P1 | Calculation | Waterfall simulator | Planned | |
| P1 | Calculation | Unsold LTV Matrix | Planned | |
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
