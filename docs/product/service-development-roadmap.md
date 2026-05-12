# Service Development Roadmap

| Phase | Description | Key Deliverables | Status | Priority |
|---|---|---|---|---|
| **Phase 1: MVP** | Core project management and calculation engines. | Feasibility calculation, Basic risk review, Project CRUD. | In Progress | P0 |
| **Phase 2: Beta** | Export functions, logistics modules, report templates. | PDF/DOCX Export, Logistics logic, Waterfall simulator. | Planned | P1 |
| **Phase 3: Enterprise** | External APIs, RBAC, workspaces. | API integrations, Org structure, Advanced GIS. | Planned | P2 |
| **Phase 4: Optimization** | Automation, portfolio management, optimizations. | Full ordinance auto-check, AI portfolio monitoring. | Planned | P3 |
| **Phase 11: PF Report Overhaul** | Real-document-based PF review report. | 20-section Hana-style report, structured report data, PDF/DOCX readiness. | In Progress | P0 |

## Phase 11 Scope

Phase 11 upgrades the PF review report from a generic Markdown/summary report into a full 20-section investment review document. The report structure is documented in [`phase-11-pf-review-report.md`](phase-11-pf-review-report.md).

## Target Architecture

- **Frontend**: Next.js (React)
- **Backend**: FastAPI (Python)
- **Data/Storage**: TBD (PostgreSQL for RDBMS, Redis for cache)
- **AI/Agents**: Provider-aware custom Agent ecosystem supporting OpenAI, Anthropic Claude API, and Claude Desktop MCP host metadata
