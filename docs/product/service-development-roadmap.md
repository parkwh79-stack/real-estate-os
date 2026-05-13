# Service Development Roadmap

Updated: 2026-05-13

| Phase | Description | Key Deliverables | Status | Priority |
|---|---|---|---|---|
| **Phase 1: MVP** | Core project management and calculation engines. | Feasibility calculation, monthly CF, Max Minus, LTV/LTC/DSCR, exit sale rate, basic risk matrix, Project CRUD. | In Progress | P0 |
| **Phase 2: Beta** | Export functions, logistics modules, report templates. | PDF/DOCX Export, Logistics logic, Waterfall simulator. | Planned | P1 |
| **Phase 3: Enterprise** | External APIs, RBAC, workspaces. | API integrations, Org structure, Advanced GIS. | Planned | P2 |
| **Phase 4: Optimization** | Automation, portfolio management, optimizations. | Full ordinance auto-check, AI portfolio monitoring. | Planned | P3 |
| **Phase 11: PF Report Overhaul** | Real-document-based PF review report. | 20-section Hana-style report, structured report data, saved versions, export history, PDF/DOCX readiness. | Done | P0 |

## Phase 11 Scope

Phase 11 upgrades the PF review report from a generic Markdown/summary report into a full 20-section investment review document. The report structure is documented in [`phase-11-pf-review-report.md`](phase-11-pf-review-report.md).

## Target Architecture

- **Frontend**: Next.js (React)
- **Backend**: FastAPI (Python)
- **Data/Storage**: TBD (PostgreSQL for RDBMS, Redis for cache)
- **AI/Agents**: Provider-aware custom Agent ecosystem supporting OpenAI, Anthropic Claude API, and Claude Desktop MCP host metadata

## Immediate Execution Order

1. PDF/DOCX binary renderers for the structured Phase 11 report export pipeline.
2. SQLite/PostgreSQL persistence with project/report/run/user tables.
3. Parser provenance per extracted field for uploaded business plan and basic architectural plan documents.
4. Decision point for Korean Law MCP: keep Claude Desktop-mediated sidecar flow or add optional API-server MCP client automation.

## Completed P0 Engine Baseline

- Project CRUD.
- Feasibility calculation.
- Monthly cash flow and Max Minus.
- LTV, LTC, DSCR, and exit sale rate.
- Basic real estate PF risk matrix.
- Phase 11 report generation, version metadata, and Markdown/JSON/HTML export artifacts.
