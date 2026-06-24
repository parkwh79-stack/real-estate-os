# REALYTICS x PLATFORM9 - IR Deck vs Implementation Gap

Updated: 2026-06-24

Source deck: `20260504 REALYTICS AI LAB 사업계획(REAL VER V2).pdf` (30 pages, REAL VER V2).

This document maps the deck's promised capabilities to the current codebase. It exists so the product team can close the gap between the pitch, the pilot, and the actual operating system.

## Legend

- Done: implemented and covered by tests or working API.
- Partial: schema, fixture, mock, or baseline exists but production workflow is incomplete.
- Planned: not yet implemented.
- Blocked: depends on API key, licensed data, external infrastructure, or business approval.

## Deck Capability Coverage

| Deck Capability | Status | Current Codebase Mapping |
|---|---|---|
| REALYTICS land, regulation, feasibility, Cash Flow, Max Minus | Done | `DevelopmentFeasibilityAgent`, `FeasibilityStudyV2`, Cash Flow Engine |
| PLATFORM9 PF review, Tranche, LTV/LTC/DSCR, Exit sale rate, unsold LTV, Waterfall | Done | Credit Review, Scenario Engine, 27-step PF orchestration |
| Phase 11 Hana-style PF review report | Done | Structured 20-section report API, Markdown/HTML/PDF/DOCX export |
| Project IRR, Equity IRR, Equity Multiple, Payback, ICR | Done | `calculate_return_metrics`, `POST /projects/{id}/returns/calculate` |
| 4-stage Stress Test: Base/Mild/Severe/Tail | Done | `StressTestAgent`, `POST /projects/{id}/stress-test/run` |
| Credit Enhancement 6-measure mapping | Done | `CreditEnhancementAgent`, `POST /projects/{id}/credit-enhancement/evaluate` |
| Cash Sweep / Reserve | Done | `CashSweepAgent`, `POST /projects/{id}/cash-sweep/simulate` |
| Intent-driven orchestration: "what should we analyze next?" | Partial | `OrchestrationPlannerAgent`, `/orchestration/plan`, `/orchestration/run-workflow`, Kakao intake menu UI |
| Valuemap live data | Partial / Blocked | Mock adapter exists; live partner/API access is required |
| VWorld / official land price live connector | Partial / Blocked | URL/API adapter design exists; API keys and production connector policy are required |
| Developability Score | Done | `DevelopabilityScoreAgent`, in the PF run (step) + Phase 11 report + UI; `GET /projects/{id}/developability-score` |
| Asset-type simultaneous feasibility comparison | Partial | Asset profiles exist; UI and multi-asset comparison workflow remain |
| Map Intelligence / 6-layer spatial data OS | Planned | PostGIS scaffolded (geometry tables + GIST), but map UI + spatial layers remain |
| Real-time PF dashboard / EOD / Covenant monitoring | Done | `CovenantMonitoringAgent` (per-project) + portfolio dashboard `GET /monitoring/portfolio`, `/monitoring` page, threshold management, snapshot-driven trend; async job runner underpins it. Scheduled background polling + push alerts remain future |
| Term Sheet draft / IC Memo auto-generation | Done | `CreditDocumentAgent` + `TermSheetAgent`: stage-specific term sheets (브릿지/본PF), IC Memo, Korean-localized, PDF/DOCX export, UI panel |
| Public sector review workflows | Planned | HUG/HF/public-land specific review logic remains |
| Company knowledge / Private AI | Partial | Excel Workbook Learning Agent + similar-case RAG over reviewed snapshots (grounding-only, cited) exist; pgvector persistence + legal/document RAG remain |
| 3-tier monetization | Partial | Product docs exist; billing and entitlement enforcement remain |

## Implementation Strengths Beyond the Deck

The current build is stronger than the deck in governance and auditability:

- Evidence Graph
- Risk Register and review workflow
- Data Quality Agent and approved override workflow
- Server-side project snapshots
- Historical similar-case search and snapshot diff
- Approval Readiness Gate
- KakaoTalk mobile intake and safe notification preview
- User activity audit logs
- Real estate glossary and canonical field map

## Integration Gaps

1. ~~Stress Test IRR is still stylised.~~ **RESOLVED 2026-06-23.**
   StressTestAgent per-scenario Project IRR now comes from the real Cash Flow Engine (scenario-adjusted sales collection + progress-weighted costs + tranche stack), with a stylised fallback and `cashflowSource`/`cashflowDiagnostics` for auditability.

2. ~~Credit Review does not yet consume Return Metrics and Stress Test results.~~ **RESOLVED 2026-06-20.**
   `CreditReviewAgent` now reads `returnMetrics`/`stressTest`: base-case senior break → reject; mild break or negative base IRR → rework; severe/tail break or below-hurdle base IRR → conditional; IRR/ICR/stress added to reasons + summary + approval conditions. Also surfaced in the orchestration UI engine-metrics panel.

3. **Intent-driven orchestration: async baseline done, pause/resume pending.**
   In-process async job runner added 2026-06-20 (`POST /orchestrations/pf-review/async` + `GET /jobs[/{id}]`) so runs are non-blocking and pollable. True mid-run human-in-the-loop pause/resume + persisted/distributed job state remain future work.

4. **Live external data remains blocked by credentials and policy.**
   Valuemap, VWorld, official land price, cadastral data, and licensed market data need production credentials and usage constraints.

5. **Report prose for new engines needs richer Korean PF wording.**
   The structured numbers are now surfaced, but report paragraphs should become decision-oriented credit memo language.

## Architecture Maturity

| Area | Current | Target |
|---|---|---|
| Orchestration | Durable async job runner + interrupted-run recovery (2026-06-24) | Distributed/DB-backed queue with mid-run pause/resume |
| Storage | SQLite / local runtime | PostgreSQL + object storage |
| Spatial data | Minimal | PostGIS + map UI |
| Documents | Local runtime archive | S3/R2/NAS object storage with metadata DB |
| Auth | Pilot HS256/local session | OIDC RS256/JWKS, tenant isolation, secret manager |
| Review workflow | Baseline review actions | Full assignment, SLA, audit, escalation |

## Recommended Next Priorities

1. ~~Feed Return Metrics and Stress Test results into `CreditReviewAgent`.~~ ✅ Done 2026-06-20.
2. ~~Link Stress Test to the real Cash Flow Engine schedule.~~ ✅ Done 2026-06-23.
3. ~~Build the Developability Score engine.~~ ✅ Done 2026-06-20 (now a PF-run step + report + UI).
4. ~~Async job runner + real-time monitoring.~~ ✅ Durable async jobs (2026-06-24) + portfolio monitoring dashboard (2026-06-24). Next: scheduled background polling + push alerts + mid-run pause/resume.
5. **Map Intelligence / GIS** — PostGIS is scaffolded (geometry tables + GIST index); next is the spatial-layer ingest + map UI for the deck's 6-layer spatial OS.
6. **Public-sector review module** (HUG/HF/public-land specific review logic) — deterministic, no external-key dependency.
7. Connect live land and market data (Valuemap/VWorld/공시지가) once API keys/licensing are ready (🔒 blocked).
