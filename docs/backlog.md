# Product Backlog

Updated: 2026-05-20

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
| P0 | Agent | Development Feasibility downstream readiness | Done | Agent now returns feasibility orchestrator role, PF readiness ratios, required missing fields, Cash Flow / Waterfall / Unsold LTV delegation flags, and FeasibilityStudyV2 integration status |
| P0 | Schema | Feasibility Study v2 schema | Done | Added canonical `realytics-feasibility-study-v2` backend schema, frontend type mirror, fixture, and Excel workbook mapping strategy |
| P0 | QA | Feasibility workbook validation | Done | Added sample-informed Excel validation gate for required sheets, key cells, formula errors, reconciliations, and v2 import readiness |
| P0 | Agent | Land Ledger Import Agent | Done | Parses legacy Bonri-style `.xls` land ledgers into parcel, owner, payment, area, amount, import consistency validation, lot normalization, lifecycle status, land acquisition status, PF summary, QA gates, PF mapping, and enrichment-plan outputs |
| P0 | Report | Phase 11 PF Review Report | Done | 20-section Hana-style PF review report implemented in web UI, structured API, saved report versions, and markdown/json/html export artifacts |
| P0 | Orchestration | PF Review Run API | Done | Synchronous MVP run covers load, feasibility, PF structure, risk, Phase 11 report, quality check, approval |
| P0 | Orchestration | Land Agent PF handoff | Done | PF review runs now include `land_agent_review`, `landAgentReview` artifacts, and `land_to_feasibility_legal_pf` handoffs carrying site, land cost, acquisition, evidence readiness, blockers, limitations, and PF land summary |
| P0 | Orchestration | Cross-agent reconciliation gate | Done | PF review runs now include `cross_agent_reconciliation` to compare feasibility revenue, marketability revenue, project land area, Land Agent parcel area, official price readiness, registry readiness, market evidence readiness, and PF metric availability before legal/risk/report steps |
| P0 | Agent | Marketability Analysis Agent MVP | Done | Applies to saleable assets and returns basic location, competition, pricing, payment schedule, discount trigger, and exit resilience checks |
| P0 | Agent | Marketability Agent v2 product classifier | Done | Classifies apartment, officetel, knowledge industry center, mixed-use, commercial presale, serviced residence, and non-saleable asset types with confidence |
| P0 | Agent | Marketability product mix normalizer | Done | Normalizes product mix, units, exclusive/contract area, price per py, total revenue, exclusive ratio, and evidence IDs |
| P0 | Agent | Marketability comparable data workflow | Done | Manual comparable/transaction/presale/listing fixture adapter normalizes category, source type, price basis, source metadata, and converted price units; licensed/public external adapters are tracked separately |
| P0 | Agent | Marketability document evidence reconciliation | Done | Parser prompt extracts market evidence from uploaded documents; upload parsing hydrates document_market_evidence; Market Evidence Agent merges existing and document-derived evidence before reconciliation/scoring |
| P0 | Agent | Marketability evidence records | Done | Market Evidence Agent emits normalized records for product mix, sale comparables, and rent comparables; records are persisted to JSON/SQLite state and exposed through the market-evidence API |
| P0 | Frontend | Marketability evidence review UI | Done | Orchestration page now shows persisted market evidence records and supports approve, reject, and needs-revision actions through the review API |
| P0 | Parser | Uploaded document provenance | Done | Business plan upload now stores original file name, SHA-256 hash, content type, file size, upload timestamp, parser version, and source document metadata for market evidence records |
| P0 | Parser | Original uploaded document archive | Done | Business plan upload now stores the original file under runtime upload archive storage and exposes source document list/download APIs for audit review |
| P0 | Agent | Marketability pricing judgement | Done | Compares proposed sale price against adjusted comparable median and returns price band, premium, percentile, judgement, and pricing comment |
| P0 | Agent | Marketability exit sale rate | Done | Calculates base, 5/10/15/20% discount scenarios, tranche-level thresholds, and repayment feasibility flags for PF review |
| P0 | Agent | Marketability discount/absorption/unsold baseline | Done | Produces trigger table with reasons, base/downside/stress absorption coverage, product-type unsold recovery scenarios, unsold LTV, and tranche recovery |
| P0 | Agent | Marketability score v2 | Done | Scores location, demand, supply, pricing, product, absorption, and PF Exit with product-specific weight profiles and score breakdown |
| P0 | Agent | Marketability lender risk flags | Done | Generates lender-side risk flag details for pricing, evidence, supply, demand, absorption, Exit pressure, unsold LTV, and tranche recovery weakness |
| P0 | Report | Marketability report paragraph generator | Done | Generates market analysis, pricing, business feasibility, exit strategy, discount trigger, unsold risk, recommendation, and lender risk memo text from score/risk/Exit outputs |
| P0 | Agent | Marketability source metadata | Done | Preserves source name/type/evidence ID/collected date/base date/license note/confidence with complete/partial metadata status and missing-field summary |
| P0 | Parser | Market evidence field locator coverage | Done | Market evidence records now expose fieldSourceLocators and locatorCoverage for extracted product mix, sale comparable, and rent comparable fields, with page/table/row inference from evidence IDs |
| P0 | Agent | Korean Legal/Permit Review Agent | Done | Preliminary legal, permit, land-rights, trust-security, covenant review with Korean Law MCP context hook |
| P0 | Report | Report fixture data | Done | Added anonymized Hana-style PF review fixture and regression checks |
| P0 | Storage | Persistent project/report/run storage | Done | SQLite-backed storage added for projects, reports, and orchestration runs; legacy JSON fallback/migration retained |
| P0 | Orchestration | Upload-to-run flow | Done | PDF business plan/basic architectural plan upload -> parse -> developer feasibility -> PF review run via `/upload-and-run` |
| P0 | Frontend | Orchestration run dashboard | Done | Project detail includes AI orchestration page with run start, run list, agent artifacts, quality checks, and approval state |
| P0 | Frontend | Orchestration approval action UI | Done | Existing approve API wired into run dashboard with reviewer/comment input, submit button, approved-state refresh, and disabled state after approval |
| P0 | Frontend | Land and risk review detail views | Done | Project land page split into review sections; project risk page shows all 20 PF risk management controls with API-assessed grade/score where available |
| P0 | Frontend | Land Agent dashboard summary | Done | Main dashboard and project dashboard now show Land Agent signals for PNU, zoning/regulation, land acquisition, official land price, registry evidence, public land, and source/evidence readiness |
| P0 | Integration | Korean Law MCP connector | Done | Sidecar integration guide, query plan MCP tool, `realytics-law-context-v1` adapter, citation verification handling, and legal-review integration completed; direct API-server MCP client automation is optional future hardening |
| P0 | AI | Claude Desktop MCP user linkage | Done | MCP/API AI context records provider, host mode, model, and linked `user_id`; Claude Desktop mode returns host LLM extraction requests, Anthropic API key enables server-side Claude extraction |
| P0 | Architecture | Repository architecture alignment plan | Done | Added `docs/architecture.md` with current vs target structure, gap analysis, migration principles, and phased refactor plan |
| P0 | Architecture | Use case layer baseline | Done | Added `services/api-server/use_cases` and routed development feasibility, cash-flow run, and PF review orchestration through reusable application workflows |
| P0 | Architecture | Agent interaction operating model | Done | Added `docs/product/agent-interaction-operating-model.md` for typed agent handoffs, progress ledger, Marketability-to-Feasibility patches, Land-to-Legal/PF patches, reconciliation flags, and orchestration quality gates |
| P0 | Architecture | Real Estate OS philosophy | Done | Agent operating model now defines one canonical schema language, no silent overwrites, evidence-first calculations, server-side institutional memory, controlled vocabulary, and historical comparability |
| P0 | Auth | User identity provider integration | Planned | Replace env/header-only user ID with login, tenant, session, and audit tables |
| P0 | Report | Structured report export pipeline | Done | Markdown/JSON/print-ready HTML/PDF/DOCX export artifacts are supported with export history and download endpoints |
| P0 | Parser | Source provenance per parsed field | In Progress | Market evidence field-level locator coverage is implemented; next step is locator coverage for non-market parser fields such as land, regulation, cost, schedule, PF, and covenants |
| P0 | Integration | Batch PNU and official land price enrichment | Done | Added LandLedgerEnrichmentAgent and `/land-ledger/enrich` workflow to resolve PNU with confidence from legal dong/lot or connector records, attach cadastral characteristics, official land price with base year/source, and registry/encumbrance evidence without fabricating missing source data |
| P0 | Report | PDF print layout QA | Done | Added A4 print CSS, table/section break controls, approval block handling, and QA checklist |
| P0 | QA | Visual Regression & Quality Gate | Done | Implemented VisualQAAgent and fully integrated it into OrchestrationAgent's quality_check step to perform automatic structural margin verification, overlap detection, A4 compliance, and pixel-level regression comparison |
| P0 | Frontend | Sensitivity Matrices & Stress Scenarios | Done | Built premium `SensitivityMatricesPanel` with tabs for Waterfall allocation simulation, color-coded 2D Unsold LTV stress heatmap, and tranche-level final recovery ratios |
| P1 | Export | Excel upload | In Progress | Feasibility Study v2 schema and workbook mapping designed; next step is XLSX template import validator |
| P1 | Export | Developer feasibility Excel export | In Progress | Export should generate formula-preserving `realytics-fs-xlsx-v1` workbook with assumptions, product mix, costs, financing, monthly CF, sensitivity, summary, and checks |
| P1 | Export | PDF export | Done | Structured Phase 11 reports can be rendered as binary PDF artifacts and downloaded through saved export history |
| P1 | Export | DOCX export | Done | Structured Phase 11 reports can be rendered as binary DOCX artifacts and downloaded through saved export history |
| P1 | Architecture | Use case layer expansion | Done | Added use cases for land ledger import, marketability review, legal review, report generation/export, evidence review, and orchestration approval |
| P1 | Architecture | Router thinning follow-up | In Progress | Project CRUD, source-document metadata, report, evidence, and orchestration persistence now use repository wrappers; remaining work is runtime archive file operations, project lookup in all endpoints, and async job state |
| P1 | Architecture | Repository wrapper baseline | Done | Added report, market evidence, and orchestration repository wrappers and connected report/evidence/orchestration use cases through repositories |
| P1 | Architecture | Project and source document repositories | Done | Added project CRUD/source-document metadata repositories and matching use cases; project create/list/get/update/delete, parsed upload application, source-document listing, and archive metadata lookup now flow through use case -> repository |
| P1 | Architecture | Runtime upload archive repository | Done | Added `UploadArchiveRepository` and source-document use cases for binary archive save, path resolution, file existence, SHA-256 integrity verification, download metadata, project archive cleanup, and future storage-backend abstraction |
| P1 | Architecture | Agent handoff schema and progress ledger | Done | Added `AgentHandoffEnvelope` and `AgentProgress` schemas; PF review runs now return typed `handoffs` and per-agent `agentProgress` for land, development feasibility, market evidence, marketability, legal, and quality-gate workflows |
| P1 | Frontend | Agent progress and handoff review panels | Done | Orchestration page now displays per-agent progress, dependencies, artifacts, review flags, typed handoff routes, payload previews, evidence counts, blockers, assumptions, and data limitations |
| P1 | Workflow | Agent handoff review workflow | Done | Added handoff review API and UI actions for approve, reject, and needs-revision with reviewer, timestamp, comment, review history, and progress review-state updates |
| P1 | Workflow | Handoff-gated final approval | Done | Final orchestration approval now returns conflict when required handoff reviews are pending, rejected, or marked needs-revision; UI shows open review count and disables final approval |
| P1 | Architecture | Canonical schema registry | Planned | Track schema versions and common cross-agent fields for FeasibilityStudyV2, LandLedgerImportV1, MarketabilityAnalysisV2, CashFlowRunRequestV1, ScenarioSetV1, EvidenceRecordV1, and Phase11ReportV1 |
| P1 | Data | Real estate glossary and canonical field map | Planned | Persist controlled vocabulary, aliases, definitions, canonical field mappings, source examples, and review status for new real estate/PF terms |
| P1 | Data | Server-side project snapshots | Done | PF review saves immutable `realytics-project-snapshot-v1` records with canonical project inputs, agent artifacts, handoffs, reconciliation, quality, report metadata, and case-comparison seed fields; list/get snapshot APIs added |
| P1 | Data | Historical Similar Case Search | Done | Added deterministic `realytics-similar-case-search-v1` use case and API to compare saved project snapshots by case seed, metrics, Risk Register overlap, historical review status, mitigants, approval conditions, and Credit Review outcome |
| P1 | Data | Snapshot diff | Planned | Compare two snapshots from the same project/run to explain changed assumptions, risks, approvals, and report sections |
| P1 | Agent | Evidence Graph Agent | Done | Added deterministic `EvidenceGraphAgent` and `evidence_graph_generation` orchestration step linking project inputs, source documents, evidence IDs, agent artifacts, handoffs, claims, and Phase 11 report sections; exact sentence/cell locators remain future hardening |
| P1 | Agent | Evidence Graph persistence and UI | Planned | Persist graph records separately, add report sentence IDs, field-level locators, graph query APIs, and reviewer/auditor UI |
| P1 | Agent | Data Quality Agent | Done | Added deterministic `DataQualityAgent` and `data_quality_review` orchestration step to detect project name, land area, GFA, sales revenue, PF amount, unit count, sqm/py conversion, VAT basis, schedule, and tranche structure conflicts |
| P1 | Agent | Data Quality override workflow | Planned | Add approved assumption override records, UI review actions, and links from data quality issues to Evidence Graph and Risk Register |
| P1 | Agent | Risk Register Agent | Done | Added deterministic `RiskRegisterAgent` and `risk_register_generation` orchestration step to consolidate Land, Marketability, Legal, Cash Flow, PF Structure, Scenario, Data Quality, and Risk Matrix issues into one credit-review table |
| P1 | Agent | Risk Register review workflow | Done | Added risk-row review API and UI actions for approved, waived, needs_revision, and resolved with reviewer, comment, waiver reason, audit history, and final approval blocking for unresolved material risks |
| P1 | Agent | Risk Register evidence links | Done | Risk rows now become Evidence Graph `risk` nodes with links to source artifacts, evidence IDs, source documents, source locators, and related claim nodes; persisted historical risk register storage remains a future hardening item |
| P1 | Agent | Credit Review Agent | Done | Added deterministic `CreditReviewAgent` and `credit_review_decision` orchestration step to convert Risk Register, Legal, Data Quality, Marketability, and PF metrics into approve/conditional/rework/reject guidance |
| P1 | Agent | Credit Review policy profiles | Planned | Add lender-specific thresholds, waiver rules, committee statuses, and final reviewer memo workflow |
| P1 | Architecture | Calculator package wrappers | Planned | Add `services/api-server/calculators` wrappers for feasibility, cash flow, LTV/LTC, waterfall, and sensitivity before moving implementations from `core` |
| P1 | Architecture | Reports and DB package wrappers | Planned | Add `services/api-server/reports` and `services/api-server/db` compatibility wrappers before moving report and storage implementations |
| P1 | Architecture | Shared contracts packages | Planned | Add `packages/shared-types` and `packages/realytics-contracts` after FeasibilityStudyV2, CashFlowRunRequest, Land Ledger, Marketability, and Phase 11 schemas stabilize |
| P1 | Calculation | Cash Flow Engine / Agent design | Done | Added English roadmap and schema alignment review for monthly cash flow, Max Minus, PF draw, sales collection, account allocation, waterfall, Exit sale rate, unsold LTV, risk flags, API, UI, and report fragments |
| P1 | Calculation | Development Feasibility to FeasibilityStudyV2 draft mapping | Done | DevelopmentFeasibilityAgent now emits a FeasibilityStudyV2-valid draft with site, program, revenue, cost, financing, schedule, scenarios, metrics, and mapping diagnostics |
| P1 | Calculation | Asset-type calculation profiles | Done | Added deterministic asset profiles for apartment, officetel, commercial, KIC, mixed-use, logistics, lodging, and other assets; profiles drive revenue mode, collection schedule, recovery haircut, and LTV threshold |
| P1 | Calculation | FeasibilityStudyV2 to CashFlowRunRequest draft mapping | Done | Added asset-aware mapper that converts FeasibilityStudyV2 drafts into CashFlowRunRequest drafts with sources/uses, product mix profiles, PF structure, schedule, scenario set, diagnostics, and source metadata |
| P1 | Calculation | Cash Flow Engine run implementation | Done | Baseline engine consumes CashFlowRunRequest and generates sales collection, operating/repayment account allocation, tranche-aware PF draw schedule, monthly cash flow, Max Minus, risk flags, and report fragments |
| P1 | Calculation | Cash Flow Engine advanced scenario workflow | In Progress | Calculation Scenario Engine now covers persisted recovery scenarios, waterfall, unsold LTV matrix, and tranche recovery; remaining work is direct Cash Flow Review UI and best/base/downside/stress run orchestration |
| P1 | Calculation | Calculation Scenario Engine design | Done | Added roadmap and schema for waterfall, unsold LTV matrix, tranche recovery, API workflow, UI review, persistence, and report mapping |
| P1 | Calculation | Waterfall simulator | Done | Wraps pure waterfall functions into API-ready composing workflow inside `/scenarios/waterfall` and `/scenarios/recovery` endpoints |
| P1 | Calculation | Unsold LTV Matrix | Done | Generates a 2D matrix of unsold LTV and tranche recovery rows across sale and discount rate vectors via `/scenarios/unsold-ltv-matrix` and `/scenarios/recovery` endpoints |
| P1 | QA | Automated PDF visual QA | Done | Added `VisualQAAgent` for A4 structure, margin overflow, overlap, empty-page, approval-block, and baseline visual regression checks integrated into orchestration quality gate |
| P1 | Logistics | Logistics module | Planned | Special fields & formulas |
| P1 | Report | Report template editor | Planned | |
| P1 | QA | Parser regression fixtures | Planned | Anonymized business plan/basic architectural plan documents for parser QA |
| P1 | API | 20-section report generation API | Done | `/reports/generate` returns structured Phase 11 sections plus Markdown compatibility output |
| P1 | Agent | Marketability subscription and unsold stats | Planned | Collect subscription competition, contract rate, unsold inventory, and local supply statistics |
| P1 | Agent | Marketability absorption model | Planned | Predict monthly absorption using comparable cases, supply, price, and product type |
| P1 | Agent | Marketability discount trigger proposal | Planned | Suggest target sales rate and discount trigger table from score and exit resilience |
| P1 | Frontend | Marketability map view | Planned | Visualize comparable supply and presale cases by radius, product type, and price band |
| P2 | Integration| External API integration | Planned | Full mapping |
| P2 | Data | Historical review database | Planned | Promote snapshots, reviewed risk treatments, decisions, conditions, and actual outcomes into queryable first-class records for benchmarking and similar-case retrieval |
| P2 | AI | Similar-case RAG | Future | Retrieve prior reviewed projects by product type, location, zoning, pricing premium, Exit sale rate, land readiness, legal issues, and approval conditions |
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
