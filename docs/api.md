# API Documentation

## Overview
This document outlines the core APIs required for the REALYTICS x PLATFORM9 application.

## Endpoints

### `GET /health`
- **Description**: Check if the API server is running.
- **Response**: `{ "status": "ok" }`

### `GET /projects`
- **Description**: Retrieve a list of all projects.
- **Response**: `[ { "id": 1, "name": "...", ... } ]`

### `POST /projects`
- **Description**: Create a new project.
- **Request Body**: `{ "project": { "name": "Project Name", "type": "mixed_use" }, "feasibility_input": {...} }`
- **Response**: The created project record with `project.id`, default `project.status`, and `_meta.created_at` / `_meta.updated_at`.

### `GET /projects/{project_id}`
- **Description**: Retrieve details of a specific project.
- **Response**: The project object.

### `PUT /projects/{project_id}`
- **Description**: Update project details.
- **Request Body**: Partial or full project object.
- **Response**: The updated project object.

### `DELETE /projects/{project_id}`
- **Description**: Delete a project and remove associated MVP report/orchestration/runtime source document archive records.
- **Response**: `{ "deleted": true, "project_id": "...", "project": {...} }`

### `POST /projects/{project_id}/upload`
- **Description**: Upload a PDF business plan and extract project data with the configured server-side AI provider.
- **Headers**: Optional `X-REALYTICS-USER-ID` and `X-REALYTICS-AI-PROVIDER` (`openai`, `anthropic`, or `claude_desktop`).
- **Request Body**: Multipart form with `file`.
- **Response**: Updated project object with parser `_meta.ai_connection`, `_meta.upload`, `_meta.parserFieldProvenance`, `parser_field_source_locators`, and `parser_locator_coverage` provenance. Upload provenance includes original file name, SHA-256 hash, content type, file size, upload timestamp, parser version, archive ID, archive path, archive hash algorithm, integrity status, and source document download URL. Parser field provenance covers non-market fields such as project, land, regulation, architectural plan, cost, feasibility, schedule, PF, developer/contractor, and covenants; missing locators are reported without fabricating evidence. In `claude_desktop` mode the response can include `host_llm_extraction` because the API server cannot access the Claude Desktop host model directly.

### `POST /projects/{project_id}/upload-and-run`
- **Description**: Upload a PDF business plan, parse/update the project, then immediately run PF review orchestration and save the generated report.
- **Headers**: Optional `X-REALYTICS-USER-ID` and `X-REALYTICS-AI-PROVIDER`.
- **Request Body**: Multipart form with `file`.
- **Response**: `{ "status": "completed", "project": {...}, "run": {...} }`. Uploaded document provenance and archive metadata are stored in `_meta.upload` and propagated into market evidence records when document-derived market evidence is extracted.
- **Note**: In `claude_desktop` mode this endpoint returns `status: host_llm_required` until Claude Desktop converts the extracted text into project JSON.

### `GET /projects/{project_id}/source-documents`
- **Description**: List uploaded source documents attached to the project.
- **Response**: Source document metadata including original file name, SHA-256 hash, archive ID, file size, content type, upload timestamp, parser version, archive path, archive hash algorithm, integrity status, and download URL.

### `GET /projects/{project_id}/source-documents/{archive_id}/download`
- **Description**: Download the archived original source document for audit review.
- **Response**: Original uploaded file as an attachment.
- **Note**: The download is only available when the archive ID is listed on the project source document metadata and the archived binary still matches its SHA-256 archive ID.

### `POST /projects/{project_id}/ai/connection/resolve`
- **Description**: Resolve the active AI provider, model, runtime mode, and linked user ID without calling an external model.
- **Request Body**: `{ "user_id": "user-123", "ai_context": { "provider": "claude_desktop" } }`
- **Response**: `{ "project_id": "...", "ai_connection": {...}, "note": "..." }`
- **Note**: Claude Desktop hosts MCP tool calls. In `claude_desktop` mode the server records `runtime_provider: mcp_host_llm` and returns extracted text plus instructions for the host LLM. Direct Claude extraction inside the API or MCP server requires `ai_provider=anthropic` and `ANTHROPIC_API_KEY`.

### `POST /projects/{project_id}/feasibility/calculate`
- **Description**: Trigger feasibility calculation.
- **Request Body**: Optional override for `feasibility_input`, such as `{ "total_revenue": 0, "total_cost": 0 }` or component values like `sales_revenue`, `other_revenue`, `land_cost`, `construction_cost`, `finance_cost`, and `other_cost`.
- **Response**: Total revenue, total cost, development profit, profit margin, profit-on-cost, cost ratio, break-even revenue, surplus to break-even, and feasibility status.

### `POST /projects/{project_id}/development-feasibility/analyze`
- **Description**: Analyze developer-side project feasibility from business plan or architectural plan data.
- **Request Body**: Optional override containing `architectural_plan`, `sales_assumptions`, `cost_assumptions`, `feasibility_input`, and `pf_structure`.
- **Response**: Architectural program, revenue, costs, developer returns, PF readiness, downstream engine readiness, asset calculation profiles, logistics analysis when `project.type=logistics`, FeasibilityStudyV2 integration status, a schema-valid `feasibilityStudyV2Draft`, asset-aware `cashFlowRunRequestDraft`, baseline `cashFlowRunResult`, sensitivity, and data quality. The agent acts as the feasibility orchestrator and delegates monthly cash flow to Cash Flow Engine while Waterfall, Unsold LTV, and tranche recovery remain Calculation Scenario Engine workflows. Logistics analysis includes NOI, cap-rate Exit value, forward-sale LTV, appraisal LTV, tenant stability, location/spec scores, formulas, and logistics risk flags.

### `POST /projects/{project_id}/land-ledger/analyze`
- **Description**: Upload and analyze a legacy Korean land ledger `.xls` file.
- **Request Body**: Multipart file upload.
- **Response**: Import metadata, upload/archive metadata, source totals, parcel list, owner/payment records, total area, total contract amount, validation status, import consistency checks, land acquisition status, PF summary, missing PNU/official-land-price indicators, registry evidence readiness, and enrichment plan. Uploaded land ledger files are archived as `documentType=land_ledger` source documents.
- **Note**: If original workbook totals and parsed totals fail reconciliation, the response status is `needs_review` even when the file parses successfully.

### `POST /projects/{project_id}/feasibility-workbook/validate`
- **Description**: Archive and validate a formula-driven feasibility workbook before accepting it as a canonical FeasibilityStudyV2 import source.
- **Request Body**: Multipart `.xlsx` or `.xlsm` file upload.
- **Response**: `{ "project_id": "...", "upload": {...}, "validation": {...}, "feasibilityStudyV2Draft": {...} }` with source-document archive metadata, workbook profile, required sheet checks, key cells, formula integrity, reconciliation checks, schema readiness, recommendations, and a conservative FeasibilityStudyV2 import draft when the workbook profile is supported. For `realytics-fs-xlsx-v1-draft` formula exports, the draft preserves core metrics plus `raw_import.productMonthlySalesRateScenarios` from the product-level monthly collection schedule. Uploaded workbooks are archived as `documentType=feasibility_workbook` source documents.
- **Note**: Validation is read-only. The returned draft is not canonical until a reviewer approves it through `/feasibility-workbook/approve`.

### `POST /projects/{project_id}/feasibility-workbook/approve`
- **Description**: Persist a reviewer-approved workbook import draft as the project's canonical `FeasibilityStudyV2`.
- **Request Body**: `{ "importId": "fs-import-1", "approvedBy": "credit_officer", "comment": "...", "feasibilityStudyV2Draft": {...}, "validation": {...} }`
- **Response**: Approval metadata, canonical FeasibilityStudyV2, derived `feasibility_input` metrics, and the updated project record. The original draft provenance remains in `raw_import.approval`.
- **Audit**: Emits `feasibility_workbook_import_approved` user activity.

### `POST /projects/{project_id}/feasibility-workbook/learn`
- **Description**: Archive and profile a feasibility or cash-flow workbook as an LLM-first learning candidate for future importer/template rules.
- **Request Body**: Multipart `.xlsx` or `.xlsm` file upload.
- **Response**: `{ "project_id": "...", "upload": {...}, "learning": {...} }` with workbook profile, template similarity scores, source-located mapping candidates, LLM judgement plan, automation decision, continuous learning plan, safe LLM prompt context, safety policy, warnings, and next actions.
- **Note**: Workbook learning is read-only. Mapping candidates cannot become production import behavior until LLM judgement, automated validation, shadow-mode import, regression fixture checks, audit metadata, and rollback requirements are satisfied.

### `POST /projects/{project_id}/risk-report-workbook/validate`
- **Description**: Archive and parse a compact `risk-report-xlsx-v1` workbook into a Risk Register/report-section import draft.
- **Request Body**: Multipart `.xlsx` file upload.
- **Response**: `{ "project_id": "...", "upload": {...}, "riskReportImportDraft": {...} }` with schema validation, named-range values, key PF metrics, imported risk rows, report paragraphs, approval-condition text, cash-flow summary rows, source-cell mapping, and source-document archive metadata.
- **Note**: Validation is read-only. The workbook must include `risk_schema_version=realytics-risk-report-xlsx-v1` and required `risk_*` named ranges.

### `POST /projects/{project_id}/risk-report-workbook/approve`
- **Description**: Persist an approved `risk-report-xlsx-v1` import into the server-side Risk Register store and project report-section data.
- **Request Body**: `{ "approvedBy": "risk_reviewer", "comment": "...", "riskReportImportDraft": {...} }`
- **Response**: Approval metadata, persisted Risk Register record, imported report sections, and the updated project record. The Risk Register is saved using the workbook `importId` as the run ID.
- **Audit**: Emits `risk_report_workbook_import_approved` user activity.

### `GET /projects/{project_id}/report-templates`
- **Description**: List editable report templates saved on a project.
- **Response**: `{ "schemaVersion": "realytics-report-template-list-v1", "projectId": "...", "templates": [...] }`

### `POST /projects/{project_id}/report-templates`
- **Description**: Create or update a project-scoped report template for Phase 11 or future report variants.
- **Request Body**: `{ "templateId": "phase11-custom", "name": "...", "sections": [...], "style": {}, "variables": {}, "approvalBlock": {}, "editedBy": "..." }`
- **Response**: Saved `realytics-report-template-v1` template, version number, history, and updated project record.
- **Audit**: Emits `report_template_upserted` user activity.

### `GET /projects/{project_id}/report-templates/{template_id}`
- **Description**: Fetch one saved report template.
- **Response**: `realytics-report-template-v1`.

### `POST /projects/{project_id}/land-ledger/enrich`
- **Description**: Enrich a parsed land ledger with batch PNU, cadastral characteristics, official land price, and registry/encumbrance evidence.
- **Request Body**: `{ "landLedger": {...}, "options": { "legalDongCode": "2729012300", "useLiveConnectors": false, "baseYear": 2026, "pnuResolutions": [], "cadastralCharacteristics": [], "officialLandPrices": [], "registryEvidence": [] }, "persist": false }`
- **Response**: Updated land ledger with refreshed parcel QA, workflow status, enrichment run summary, PF summary, evidence records, and coverage ratios.
- **Live Connector Options**:
  - `useLiveConnectors`: when true, runs the configured land data connector router before manual records are applied.
  - `legalDongCode`: required to deterministically generate PNU from normalized lot number when PNU is missing.
  - `baseYear`: target official land price base year.
  - `connectorTimeoutSeconds`: per-request timeout for configured connector calls.
- **Connector Environment Variables**:
  - `REALYTICS_CADASTRAL_URL_TEMPLATE`: URL template for cadastral/land-use lookup. Supports `{pnu}`, `{service_key}`, and `{base_year}`.
  - `REALYTICS_OFFICIAL_LAND_PRICE_URL_TEMPLATE`: URL template for official land price lookup. Supports `{pnu}`, `{service_key}`, and `{base_year}`.
  - API key lookup order: `REALYTICS_PUBLIC_DATA_API_KEY`, `DATA_GO_KR_API_KEY`, `REALYTICS_VWORLD_API_KEY`, `VWORLD_API_KEY`.
- **Data Policy**: The endpoint can generate PNU from a configured legal dong code and normalized lot numbers. Official land price and registry evidence are never fabricated. Official price must come from provided connector/manual records or configured public/licensed adapters with base year/source evidence. Registry evidence remains manual/licensed evidence attachment; live registry lookup is not implemented.

### `POST /projects/{project_id}/cashflow/calculate`
- **Description**: Trigger monthly cash flow calculation.
- **Request Body**: `{ "revenue_schedule": [...], "cost_schedule": [...] }` or `{ "cashflows": [...] }`.
- **Response**: Normalized monthly rows, net CF, cumulative CF, Max Minus amount/month/period, funding gap, ending cash, and status.

### `POST /projects/{project_id}/cash-flow/run`
- **Description**: Run the baseline Cash Flow Engine through the canonical feasibility path.
- **Request Body**: Optional `CashFlowRunRequest` directly, `{ "cashFlowRunRequest": {...} }`, `{ "cashFlowRunRequestDraft": {...} }`, or project-data overrides that can be analyzed by Development Feasibility Agent.
- **Response**: `realytics_cash_flow_engine_v1` result with summary, product-based sales collection schedule, operating/repayment reserve account allocation, tranche-aware PF draw schedule, monthly cash-flow rows, Max Minus, risk flags, report fragments, and source metadata.
- **Canonical path**: `DevelopmentFeasibilityAgent -> FeasibilityStudyV2 -> CashFlowRunRequest -> Cash Flow Engine`.

### `POST /projects/{project_id}/pf/structure`
- **Description**: Configure and calculate PF structure.
- **Request Body**: `{ "tranches": [...], "total_revenue": 0, "total_cost": 0, "net_operating_income": 0, "debt_service": 0 }`
- **Response**: Total PF amount, tranche-level all-in cost, annual interest, fees, LTV, LTC, DSCR, exit sale rate, status bands, and credit summary.

### `POST /projects/{project_id}/risk/assess`
- **Description**: Evaluate project risks.
- **Request Body**: `{ "project_data": {...}, "risk_items": [...] }`. If `risk_items` is omitted, the API infers baseline project risks from land, permit, PF, feasibility, and covenant data.
- **Response**: Basic PF risk matrix, category rows, assessed items, top risks, 5x5 heatmap, recommendations, missing categories, and overall risk rating.

### `POST /projects/{project_id}/scenarios/waterfall`
- **Description**: Run the Calculation Scenario Engine and return Waterfall repayment allocation scenarios.
- **Request Body**: Scenario input can include `productMix`, `pfStructure`, `scenarioDrivers`, `waterfallRule`, and `sourceMetadata`. When omitted, the endpoint derives baseline product mix and PF structure from the project record.
- **Response**: Scenario set ID and waterfall scenarios with sale rate, discount rate, cash available, claim amount, shortfall, and tranche-level payment/recovery rows.

### `POST /projects/{project_id}/scenarios/unsold-ltv-matrix`
- **Description**: Run the Calculation Scenario Engine and return a 2D unsold LTV matrix across sale-rate and discount-rate vectors.
- **Request Body**: Same scenario input shape as `/scenarios/waterfall`.
- **Response**: Scenario set ID and unsold LTV matrix with gross/discounted unsold value, unsold LTV, risk level, and summary metrics.

### `POST /projects/{project_id}/scenarios/recovery`
- **Description**: Run and persist a full recovery scenario set including waterfall scenarios, unsold LTV matrix, tranche recovery matrix, risk flags, and report fragments.
- **Request Body**: Same scenario input shape as `/scenarios/waterfall`.
- **Response**: `calculation_scenario_engine_v1` result and saved `project.scenario_results[scenarioSetId]`.

### `POST /projects/{project_id}/scenarios/restructuring`
- **Description**: Run the Calculation Scenario Engine, then produce a deterministic restructuring plan for rejection-driving scenario risks such as `SENIOR_SHORTFALL` and `UNSOLD_LTV_BREACH`.
- **Request Body**: Same scenario input shape as `/scenarios/waterfall`, plus optional `options` such as `{ "seniorBuffer": 1000000000, "unsoldLtvTarget": 0.90 }`.
- **Response**: `pf_restructuring_plan` with base scenario health, adjusted senior-resized scenario health, senior shortfall cure amount, DSRA/equity alternative, unsold LTV cure amount, additional collateral value, approval path, source metadata, and source locators back to waterfall rows and unsold LTV matrix cells.
- **Credit Policy**: This endpoint does not waive credit risk. It quantifies what must change before a rejected scenario can move toward conditional approval. Senior shortfall must be removed by tranche reduction, DSRA/equity, or equivalent repayment support; unsold LTV breach must be cured by PF reduction, equity increase, additional collateral, forward sale, bulk sale, refinancing evidence, or other source-backed recovery support.

### `POST /projects/{project_id}/marketability/analyze`
- **Description**: Analyze saleability for saleable real estate assets such as apartments, officetels, knowledge industry centers, mixed-use projects, and commercial pre-sale projects.
- **Request Body**: Optional project data override. `market` can include `comparables`, `manualComparables`, `transactionComparables`, `presaleComparables`, `listingComparables`, and `rentComparables`.
- **Response**: Applicability, score, grade, score breakdown, comparable summary, normalized comparables, pricing judgement, Exit sale rate discount scenarios, tranche thresholds, absorption forecast, discount triggers, unsold recovery scenarios, saleability checks, source metadata, source metadata summary, risk flags, risk flag details, report paragraphs, and recommendations.

### `GET /projects/{project_id}/market-evidence`
- **Description**: List persisted market evidence records extracted or reconciled during orchestration.
- **Query Parameters**: Optional `run_id` to filter records by orchestration run.
- **Response**: Evidence records with evidence ID, evidence type, source metadata, source metadata status/missing fields, record-level source locator, field-level source locators, locator coverage, extracted fields, related validation checks, validation status, review status, project ID, and run ID. `sourceLocator` and `fieldSourceLocators` can include page number, table, row, column, field name, text excerpt, and confidence.

### `POST /projects/{project_id}/market-evidence/review`
- **Description**: Update analyst review status for a persisted market evidence record.
- **Request Body**: `{ "record_key": "run-id:evidence-id", "review_status": "approved|rejected|needs_revision|pending_review|auto_validated", "reviewed_by": "market_analyst", "comment": "Review memo" }`
- **Response**: Updated evidence record with review status, reviewer, review timestamp, comment, and review history.

### `POST /projects/{project_id}/legal/review`
- **Description**: Run a preliminary Korean real estate PF legal, permit, land-rights, trust-security, and covenant review.
- **Request Body**: `{ "project_data": {...}, "law_context": {...} }`
- **Response**: Legal review status, check findings, Korean Law MCP connection state, references, and follow-up items.
- **Note**: `law_context` is designed for Korean Law MCP sidecar outputs and is normalized into `realytics-law-context-v1`. When no MCP context is supplied, the agent returns an explicit `connected: false` caution instead of pretending to have authoritative legal sources.
- **Integration Guide**: See `docs/integrations/korean-law-mcp.md`.

### `POST /projects/{project_id}/reports/generate`
- **Description**: Generate the PF Review Report.
- **Request Body**: `{ "template": "phase-11-pf-review", "options": {...} }`
- **Response**: Structured 20-section PF review report data plus Markdown compatibility output, report ID, and report version.

```json
{
  "project_id": "1",
  "template": "phase-11-pf-review",
  "report": {
    "template": "phase-11-pf-review",
    "title": "AI PF 심사보고서",
    "sectionOrder": [{ "index": 1, "key": "cover", "title": "표지" }],
    "sections": {},
    "qualityChecks": {
      "expectedSectionCount": 20,
      "actualSectionCount": 20,
      "isComplete": true
    }
  },
  "report_markdown": "# PF 심사보고서 (Draft)...",
  "metadata": {
    "section_count": 20,
    "expected_section_count": 20,
    "complete": true
  }
}
```

#### Phase 11 report response sections

The `phase-11-pf-review` template returns these `report.sections` keys:

- `cover`: 사업명, 심의일, 금융기관명, 심의내용 요약
- `financialTerms`: Tr.A/B/C 대출금액, 금리, 수수료, All-in Cost
- `claimPreservation`: 신탁구조, 책임준공, 연대보증, Covenant
- `discountTrigger`: 시기별 목표분양률 및 할인율
- `fundingStructure`: Cash-In/Out 상세 내역, 단위 백만원
- `useOfFunds`: 자금 사용 용도
- `landSecuring`: 필지별 소유자, 지분율, 면적, 근저당권
- `projectSchedule`: 단계별 예정/완료 일정
- `financialStructureDiagram`: 차주, 신탁사, 시공사, 대주단 관계
- `salesPaymentSchedule`: 계약금, 중도금, 잔금 납입 일정
- `salesRevenueDistribution`: 분양수입금 배분 기준
- `summaryCashFlow`: 분양률 가정 기반 요약 CF
- `borrowerAnalysis`: 시행사 개요, 재무현황, 신용등급
- `contractorAnalysis`: 시공사 수주잔고, 재무비율, 신용등급
- `marketAnalysis`: 입지여건, 경쟁단지, 분양가 적정성
- `businessFeasibility`: LTV, Exit 분양률, 수익성 지표
- `exitStrategy`: 할인분양, 미분양, 대물변제, 임대 후 매각 시나리오
- `drawdownConditions`: 인출 선행/후행조건 체크리스트
- `eodTriggers`: 당연/선택적 기한이익상실
- `fundExecutionOrder`: 자금집행 순서 및 변제 충당 순위

### `GET /projects/{project_id}/reports`
- **Description**: Retrieve previously generated reports.
- **Response**: List of report metadata, including version and export history summary.

### `GET /projects/{project_id}/reports/{report_id}`
- **Description**: Retrieve a generated report with structured payload, Markdown output, and export metadata.
- **Response**: Report metadata, `report`, `report_markdown`, and `exports`.

### `POST /projects/{project_id}/reports/{report_id}/exports`
- **Description**: Create an export artifact from a saved Phase 11 report.
- **Request Body**: `{ "format": "markdown" }`, `{ "format": "json" }`, `{ "format": "html" }`, `{ "format": "pdf" }`, or `{ "format": "docx" }`.
- **Response**: Export metadata including `export_id`, `file_name`, `content_type`, `byte_size`, and `sha256`. Export content is stored for download but omitted from metadata responses. Binary PDF/DOCX artifacts are stored internally as base64 so they remain compatible with the JSON/SQLite MVP state store.

### `GET /projects/{project_id}/reports/{report_id}/exports`
- **Description**: List export artifacts for a saved report.
- **Response**: Export metadata list.

### `GET /projects/{project_id}/reports/{report_id}/exports/{export_id}/download`
- **Description**: Download a previously created report export artifact.
- **Response**: File response with the export content and content disposition header.

### `POST /projects/{project_id}/orchestrations/pf-review`
- **Description**: Run the MVP PF review orchestration workflow synchronously.
- **Request Body**: `{ "project_data": {...}, "options": { "user_id": "user-123", "ai_context": { "provider": "claude_desktop" } } }`
- **Response**: An orchestration run with status, step outputs, typed `handoffs`, per-agent `agentProgress`, artifacts, and errors.

The workflow executes these canonical step keys:

- `project_load`: Project data load
- `land_agent_review`: Land acquisition and evidence review
- `development_feasibility_analysis`: Developer feasibility analysis
- `feasibility_calculation`: Feasibility calculation
- `pf_structure_calculation`: PF structure calculation
- `market_evidence_reconciliation`: Market evidence reconciliation
- `marketability_analysis`: Marketability analysis
- `data_quality_review`: Data quality review
- `cross_agent_reconciliation`: Cross-agent reconciliation gate
- `legal_permit_review`: Legal and permit pre-review
- `risk_assessment`: Risk assessment
- `risk_register_generation`: Risk register generation
- `credit_review_decision`: Credit review decision
- `scenario_restructuring_plan`: Scenario restructuring plan
- `phase11_report_generation`: Phase 11 report generation
- `evidence_graph_generation`: Evidence graph generation
- `quality_check`: Report completeness quality check
- `approval_readiness_gate`: Approval readiness gate

Typed handoff artifacts currently include:

- `development_feasibility_to_cash_flow`
- `land_to_feasibility_legal_pf`
- `market_evidence_to_marketability`
- `marketability_to_feasibility`
- `legal_to_credit_review`
- `risk_register_to_credit_review`
- `credit_review_to_report`

### `GET /projects/{project_id}/orchestrations`
- **Description**: List PF review orchestration runs for a project.
- **Response**: List of run metadata.

### `GET /projects/{project_id}/orchestrations/{run_id}`
- **Description**: Retrieve a specific orchestration run including steps and artifacts.
- **Response**: Full orchestration run payload.

### `GET /projects/{project_id}/snapshots`
- **Description**: List immutable project snapshots saved from PF review orchestration runs.
- **Response**: Snapshot metadata including `snapshotId`, `runId`, workflow, status, artifact keys, handoff count, reconciliation status, and quality pass flag.

### `GET /projects/{project_id}/snapshots/{snapshot_id}`
- **Description**: Retrieve one full server-side project snapshot for audit review or future similar-case comparison.
- **Response**: `realytics-project-snapshot-v1` payload with canonical project inputs, orchestration summary, agent progress, typed handoffs, key agent artifacts, reconciliation result, quality gate, report metadata, case-comparison seed fields, and optional `historicalOutcome`.

### `POST /projects/{project_id}/snapshots/{snapshot_id}/outcome`
- **Description**: Attach actual post-approval outcome data to a saved snapshot for back-testing and future similar-case learning.
- **Request**: `{ "actualSalesRate": 0.94, "actualDiscountRate": 0.03, "repaymentCompleted": true, "repaymentCompletionDate": "2026-12-31", "eodOccurred": false, "lossOccurred": false, "recoveryRate": 1.0, "seniorRecoveryRate": 1.0, "mezzanineRecoveryRate": 1.0, "juniorRecoveryRate": 1.0, "actualExitSource": "unit sales", "outcomeMemo": "...", "sourceMetadata": {...}, "sourceLocators": [...] }`
- **Response**: `realytics-historical-outcome-upsert-v1` payload with normalized `historicalOutcome` and the updated snapshot.
- **Audit Policy**: This does not rewrite the original review decision or risk treatment. It appends/updates a post-closing outcome block with updater, timestamp, source metadata, and source locators.

### `POST /projects/{project_id}/snapshots/similar`
- **Description**: Search stored project snapshots for deterministic similar PF review cases and historical risk handling.
- **Request**: Optional `snapshotId`, `limit`, `minScore`, and `includeSameProject`.
- **Response**: `realytics-similar-case-search-v1` payload with the query seed, ranked similar cases, score breakdown, matched dimensions, matched risk rows, historical review status, mitigants, approval conditions, Credit Review decision outcome, and post-approval `historicalOutcome` when available.

### `POST /projects/{project_id}/snapshots/diff`
- **Description**: Compare two saved project snapshots from the same project for audit, re-review, and committee supplement workflows.
- **Request**: `baseSnapshotId` and `compareSnapshotId`.
- **Response**: `realytics-snapshot-diff-v1` payload with seed metric changes, added/removed/changed risks, Credit Review decision changes, artifact changes, report-section changes, material changes, and re-review recommendation.

### `POST /projects/{project_id}/orchestrations/{run_id}/approve`
- **Description**: Mark a completed orchestration run as approved.
- **Request Body**: `{ "approved_by": "credit_officer", "comment": "..." }`
- **Response**: Updated orchestration run.
- **Approval Gate**: Returns `409` when any handoff that requires review is still pending, rejected, or marked needs-revision.

### `POST /projects/{project_id}/orchestrations/{run_id}/handoffs/{handoff_id}/review`
- **Description**: Review one typed agent handoff inside an orchestration run.
- **Request Body**: `{ "review_status": "approved | rejected | needs_revision", "reviewed_by": "credit_officer", "comment": "..." }`
- **Response**: Updated orchestration run with the handoff review status, review history, and related `agentProgress` review flags.

### `POST /projects/{project_id}/orchestrations/{run_id}/risk-register/{risk_id}/review`
- **Description**: Review one Risk Register row inside an orchestration run.
- **Request Body**: `{ "review_status": "approved | waived | needs_revision | resolved", "reviewed_by": "credit_officer", "comment": "...", "waiver_reason": "..." }`
- **Response**: Updated orchestration run with the risk row `reviewStatus`, reviewer, timestamp, comment, `reviewHistory`, Risk Register review summary, and refreshed Credit Review human-review metadata.
- **Approval Gate**: Final run approval is blocked while material risk rows that require human review remain outside `approved`, `waived`, or `resolved`.
- **Persistence**: The updated Risk Register is synchronized to the server-side `risk_registers` store and the action is logged as `risk_register_row_review`.

### `GET /projects/{project_id}/risk-registers`
- **Description**: List server-persisted Risk Register records for a project across orchestration runs.
- **Response**: `realytics-risk-register-list-v1` payload with run ID, status, risk count, review summary, and update time.

### `GET /projects/{project_id}/orchestrations/{run_id}/risk-register`
- **Description**: Retrieve the server-persisted Risk Register for one orchestration run.
- **Response**: Full `realytics-risk-register-v1` payload, including risks, source locators, evidence links, review statuses, and review history.

### `GET /projects/{project_id}/evidence-graphs`
- **Description**: List server-persisted Evidence Graph records for a project across orchestration runs.
- **Response**: `realytics-evidence-graph-list-v1` payload with graph ID, run ID, node count, edge count, risk node count, source locator count, and update time.

### `GET /projects/{project_id}/orchestrations/{run_id}/evidence-graph`
- **Description**: Retrieve the server-persisted Evidence Graph for one orchestration run.
- **Response**: Full `realytics-evidence-graph-v1` payload, including source nodes, claim nodes, risk nodes, report section nodes, source locators, edges, audit warnings, and summary metrics.

### `GET /projects/{project_id}/assumption-overrides`
- **Description**: List approved assumption override records used to resolve Data Quality conflicts without overwriting original source values.
- **Query params**: Optional `status` and `check_key`.
- **Response**: `realytics-assumption-override-list-v1` payload with override ID, data-quality check key, canonical field, approved value, reviewer, reason, evidence IDs, source locators, and status.

### `POST /projects/{project_id}/assumption-overrides`
- **Description**: Persist one or more human-approved canonical assumptions, such as authoritative total sales revenue, PF amount, land area, or GFA.
- **Request Body**: `{ "overrideId": "ovr-1", "checkKey": "sales_revenue_consistency", "canonicalField": "feasibility_input.total_revenue", "approvedValue": 100000000000, "unit": "KRW", "reason": "Signed workbook is authoritative.", "sourceLocators": [] }`
- **Response**: `realytics-assumption-override-upsert-v1` payload with saved override records.
- **Review Policy**: The override resolves the matching Data Quality check in downstream PF Review runs, but the original conflicting values remain visible in the check metadata for audit.

## Land Evidence APIs

### `GET /projects/{project_id}/land-evidence`
- **Description**: List persisted land evidence records for a project.
- **Query params**: `pnu`, `evidence_type`.
- **RBAC**: Requires `evidence:read` when strict RBAC mode is enabled.

### `POST /projects/{project_id}/land-evidence`
- **Description**: Persist one or more land evidence records such as PNU resolution, cadastral record, official land price, or registry record.
- **Request Body**: `{ "records": [{ "evidenceType": "official_land_price", "pnu": "...", "pricePerSqm": 0, "baseYear": 2026, "sourceUrlOrEvidenceId": "..." }] }`
- **RBAC**: Requires `project:write` when strict RBAC mode is enabled.

### `POST /projects/{project_id}/land-ledger/enrich`
- **Update**: When `persistEvidence` or `persist` is true, enrichment outputs are saved into `land_evidence` records for downstream legal/PF review.

## Glossary / Canonical Field Map APIs

### `GET /glossary/terms`
- **Description**: List persisted real estate and PF glossary terms.
- **Query params**: Optional `q`, `category`, `canonical_field`, and `review_status`.
- **Response**: `realytics-glossary-term-list-v1` payload with controlled vocabulary records, aliases, definitions, canonical field mappings, source examples, and review status.

### `POST /glossary/terms`
- **Description**: Create or update a glossary term and append review history.
- **Request Body**: `{ "termId": "dsra", "displayNameKo": "원리금상환준비계좌", "aliases": ["DSRA"], "canonicalField": "pf_structure.dsra.amount", "reviewStatus": "approved" }`
- **Validation**: `approved`, `pending_review`, and `needs_revision` automation terms require a `canonicalField`.
- **Audit**: Emits `glossary_term_upserted` user activity logs.

### `GET /glossary/terms/{term_id}`
- **Description**: Retrieve one glossary term by stable term ID.

### `GET /glossary/resolve`
- **Description**: Resolve a source term or alias to an approved glossary term.
- **Query params**: `term`, optional `include_pending`.
- **Response**: `realytics-glossary-resolution-v1` with matched term or candidate list.

### `GET /glossary/canonical-field-map`
- **Description**: Return approved canonical fields mapped to terms and aliases for parser, data-quality, report, and UI standardization.
- **Response**: `realytics-canonical-field-map-v1`.

### `GET /glossary/candidates`
- **Description**: List unmapped real estate/PF term candidates captured from `TermAnnotationAgent`.
- **Query params**: Optional `q`, `status`, `project_id`, `category`, and `limit`.
- **Response**: `realytics-glossary-term-candidate-list-v1` with surface text, source field, source locator, occurrence count, review status, and suggested metadata.

### `POST /glossary/candidates/capture`
- **Description**: Persist `unmapped_candidate` annotations into the glossary review queue.
- **Request Body**: `{ "projectId": "1", "runId": "run-1", "annotations": [{ "surface": "임대차확약", "status": "unmapped_candidate", "sourceField": "legal.conditions[0]", "sourceLocator": {...} }] }`
- **Audit**: Emits `glossary_candidate_captured` user activity logs.

### `POST /glossary/candidates/{candidate_id}/review`
- **Description**: Approve, reject, merge, or request revision for a candidate. Approval creates or updates an approved glossary term.
- **Request Body**: `{ "status": "approved", "termId": "lease_commitment", "preferredTerm": "임대차확약", "canonicalField": "real_asset.leaseCommitment", "category": "real_asset", "reason": "..." }`
- **Validation**: `approved` requires `canonicalField`.
- **Audit**: Emits `glossary_candidate_reviewed` user activity logs.

## Auth / Identity APIs

### `POST /auth/tenants`
- **Description**: Create or update a tenant/workspace identity record.
- **Request Body**: `{ "tenantId": "tenant-a", "name": "Tenant A", "status": "active" }`

### `POST /auth/users`
- **Description**: Create or update a persisted user identity record.
- **Request Body**: `{ "userId": "analyst-1", "tenantId": "tenant-a", "email": "a@example.com", "displayName": "Analyst", "role": "reviewer" }`

### `GET /auth/users`
- **Description**: List persisted users, optionally filtered by tenant.
- **Query params**: `tenant_id`, `limit`.

### `POST /auth/session`
- **Description**: Create an MVP session for a persisted user and emit a login audit event.
- **Request Body**: `{ "userId": "analyst-1", "tenantId": "tenant-a", "provider": "local", "host": "web" }`

### `GET /auth/me`
- **Description**: Resolve the current identity context from `x-realytics-session-id`, `x-realytics-user-id`, or `x-user-id`.
- **Response**: Authenticated flag, user, session, role-derived permission hints, and MVP limitations.
- **Note**: This is a persisted identity/session baseline, not yet a production identity provider.

### RBAC Mode
- **Header**: Use `x-realytics-session-id` to apply persisted user roles to sensitive endpoints.
- **Environment**: `REALYTICS_RBAC_MODE=permissive|strict`.
- **Permissive mode**: Unknown legacy users are allowed but authorization metadata records a warning.
- **Strict mode**: Unknown users or users without the required permission receive `403`.
- **Protected MVP endpoints**: audit log read/export, source document download, report export creation, and report export download.

## Audit APIs

### `POST /audit/login-event`
- **Description**: Record an observed login/session event for troubleshooting and audit review.
- **Headers**: `x-realytics-user-id` or `x-user-id` can provide the user ID when the payload does not.
- **Request Body**: `{ "userId": "analyst-1", "provider": "local", "status": "success", "tenantId": "tenant-a", "host": "web" }`
- **Response**: Stored login event record.

### `GET /audit/user-activity`
- **Description**: List server-side user activity logs. Supports filtering by user, project, and event type.
- **Query params**: `limit`, `user_id`, `project_id`, `event_type`.
- **Example**: `GET /audit/user-activity?user_id=analyst-1&limit=100`
- **Note**: This is a baseline activity log, not a complete authentication system. Real identity provider integration, tenant/session/RBAC tables, and raw evidence access logs remain planned.

### `GET /audit/user-activity/summary`
- **Description**: Return count-based troubleshooting summary by event type, user, project, status bucket, sensitive event count, and slow request count.
- **Query params**: `limit`, `user_id`, `project_id`, `event_type`.

### `GET /audit/user-activity/export`
- **Description**: Export user activity logs for audit handoff.
- **Query params**: `format=json|csv`, `limit`, `user_id`, `project_id`, `event_type`.

Explicit sensitive events currently recorded:

- `source_document_download`
- `report_export_created`
- `report_export_download`
