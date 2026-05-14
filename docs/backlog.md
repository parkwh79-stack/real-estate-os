# Product Backlog

Updated: 2026-05-14

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
| P0 | Schema | Feasibility Study v2 schema | Done | Added canonical `realytics-feasibility-study-v2` backend schema, frontend type mirror, fixture, and Excel workbook mapping strategy |
| P0 | QA | Feasibility workbook validation | Done | Added sample-informed Excel validation gate for required sheets, key cells, formula errors, reconciliations, and v2 import readiness |
| P0 | Agent | Land Ledger Import Agent | Done | Parses legacy Bonri-style `.xls` land ledgers into parcel, owner, payment, area, amount, import consistency validation, lot normalization, lifecycle status, land acquisition status, PF summary, QA gates, PF mapping, and enrichment-plan outputs |
| P0 | Report | Phase 11 PF Review Report | Done | 20-section Hana-style PF review report implemented in web UI, structured API, saved report versions, and markdown/json/html export artifacts |
| P0 | Orchestration | PF Review Run API | Done | Synchronous MVP run covers load, feasibility, PF structure, risk, Phase 11 report, quality check, approval |
| P0 | Agent | Marketability Analysis Agent MVP | Done | Applies to saleable assets and returns basic location, competition, pricing, payment schedule, discount trigger, and exit resilience checks |
| P0 | Agent | Marketability Agent v2 product classifier | Next | Classify apartment, officetel, knowledge industry center, mixed-use, commercial presale, and non-saleable asset types with confidence |
| P0 | Agent | Marketability comparable data workflow | Planned | Collect nearby transaction/presale comparables through fixtures/connectors and preserve source metadata |
| P0 | Agent | Marketability pricing judgement | Planned | Compare proposed sale price against similar cases and judge high/reasonable/low pricing |
| P0 | Agent | Marketability exit sale rate | Planned | Calculate repayment exit sale rate and discounted/stress exit sale rate for PF review |
| P0 | Agent | Marketability score v2 | Planned | Weighted score based on product type, location, comparable pricing, sales schedule, triggers, and exit resilience |
| P0 | Report | Marketability report paragraph generator | Planned | Generate Phase 11 market analysis, business feasibility, exit strategy, and lender risk memo text |
| P0 | Agent | Korean Legal/Permit Review Agent | Done | Preliminary legal, permit, land-rights, trust-security, covenant review with Korean Law MCP context hook |
| P0 | Report | Report fixture data | Done | Added anonymized Hana-style PF review fixture and regression checks |
| P0 | Storage | Persistent project/report/run storage | Done | SQLite-backed storage added for projects, reports, and orchestration runs; legacy JSON fallback/migration retained |
| P0 | Orchestration | Upload-to-run flow | Done | PDF business plan/basic architectural plan upload -> parse -> developer feasibility -> PF review run via `/upload-and-run` |
| P0 | Frontend | Orchestration run dashboard | Done | Project detail includes AI orchestration page with run start, run list, agent artifacts, quality checks, and approval state |
| P0 | Frontend | Orchestration approval action UI | Done | Existing approve API wired into run dashboard with reviewer/comment input, submit button, approved-state refresh, and disabled state after approval |
| P0 | Frontend | Land and risk review detail views | Done | Project land page split into review sections; project risk page shows all 20 PF risk management controls with API-assessed grade/score where available |
| P0 | Integration | Korean Law MCP connector | Done | Sidecar integration guide, query plan MCP tool, `realytics-law-context-v1` adapter, citation verification handling, and legal-review integration completed; direct API-server MCP client automation is optional future hardening |
| P0 | AI | Claude Desktop MCP user linkage | Done | MCP/API AI context records provider, host mode, model, and linked `user_id`; Claude Desktop mode returns host LLM extraction requests, Anthropic API key enables server-side Claude extraction |
| P0 | Auth | User identity provider integration | Planned | Replace env/header-only user ID with login, tenant, session, and audit tables |
| P0 | Report | Structured report export pipeline | In Progress | Markdown/JSON/print-ready HTML export history added; PDF/DOCX binary renderers remain next |
| P0 | Parser | Source provenance per parsed field | Next | Persist page/source/confidence metadata for uploaded business plan/basic architectural plan fields |
| P0 | Integration | Batch PNU and official land price enrichment | Planned | Next after validation layer: resolve PNU with confidence score, fetch cadastral characteristics, official land price with base year/source, and registry/encumbrance evidence for imported parcels |
| P0 | Report | PDF print layout QA | Done | Added A4 print CSS, table/section break controls, approval block handling, and QA checklist |
| P1 | Export | Excel upload | In Progress | Feasibility Study v2 schema and workbook mapping designed; next step is XLSX template import validator |
| P1 | Export | Developer feasibility Excel export | In Progress | Export should generate formula-preserving `realytics-fs-xlsx-v1` workbook with assumptions, product mix, costs, financing, monthly CF, sensitivity, summary, and checks |
| P1 | Export | PDF export | Planned | |
| P1 | Export | DOCX export | Planned | |
| P1 | Calculation | Waterfall simulator | In Progress | Pure function exists; UI/API scenario workflow still pending |
| P1 | Calculation | Unsold LTV Matrix | In Progress | Pure function exists; scenario matrix UI/API still pending |
| P1 | Logistics | Logistics module | Planned | Special fields & formulas |
| P1 | Report | Report template editor | Planned | |
| P1 | QA | Parser regression fixtures | Planned | Anonymized business plan/basic architectural plan documents for parser QA |
| P1 | API | 20-section report generation API | Done | `/reports/generate` returns structured Phase 11 sections plus Markdown compatibility output |
| P1 | Agent | Marketability subscription and unsold stats | Planned | Collect subscription competition, contract rate, unsold inventory, and local supply statistics |
| P1 | Agent | Marketability absorption model | Planned | Predict monthly absorption using comparable cases, supply, price, and product type |
| P1 | Agent | Marketability discount trigger proposal | Planned | Suggest target sales rate and discount trigger table from score and exit resilience |
| P1 | Frontend | Marketability map view | Planned | Visualize comparable supply and presale cases by radius, product type, and price band |
| P2 | Integration| External API integration | Planned | Full mapping |
| P2 | Integration | Marketability partner data | Future | Hogangnono, Zigbang, Real Estate Planet, or other licensed partner-data connectors |
| P2 | AI | Marketability ML/RAG | Future | Sales-rate prediction ML, similar PF review case RAG, feasibility Excel linkage, and lender risk memo generation |
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
