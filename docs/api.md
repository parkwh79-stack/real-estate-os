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
- **Request Body**: `{ "name": "Project Name", "type": "Mixed Use", ... }`
- **Response**: The created project object.

### `GET /projects/{project_id}`
- **Description**: Retrieve details of a specific project.
- **Response**: The project object.

### `PUT /projects/{project_id}`
- **Description**: Update project details.
- **Request Body**: Partial or full project object.
- **Response**: The updated project object.

### `POST /projects/{project_id}/upload`
- **Description**: Upload a PDF business plan and extract project data with the configured server-side AI provider.
- **Headers**: Optional `X-REALYTICS-USER-ID` and `X-REALYTICS-AI-PROVIDER` (`openai`, `anthropic`, or `claude_desktop`).
- **Request Body**: Multipart form with `file`.
- **Response**: Updated project object with parser `_meta.ai_connection`. In `claude_desktop` mode the response can include `host_llm_extraction` because the API server cannot access the Claude Desktop host model directly.

### `POST /projects/{project_id}/ai/connection/resolve`
- **Description**: Resolve the active AI provider, model, runtime mode, and linked user ID without calling an external model.
- **Request Body**: `{ "user_id": "user-123", "ai_context": { "provider": "claude_desktop" } }`
- **Response**: `{ "project_id": "...", "ai_connection": {...}, "note": "..." }`
- **Note**: Claude Desktop hosts MCP tool calls. In `claude_desktop` mode the server records `runtime_provider: mcp_host_llm` and returns extracted text plus instructions for the host LLM. Direct Claude extraction inside the API or MCP server requires `ai_provider=anthropic` and `ANTHROPIC_API_KEY`.

### `POST /projects/{project_id}/feasibility/calculate`
- **Description**: Trigger feasibility calculation.
- **Request Body**: `{ "land": {...}, "regulation": {...}, ... }`
- **Response**: Feasibility results (Total revenue, costs, dev profit, margins).

### `POST /projects/{project_id}/development-feasibility/analyze`
- **Description**: Analyze developer-side project feasibility from business plan or architectural plan data.
- **Request Body**: Optional override containing `architectural_plan`, `sales_assumptions`, `cost_assumptions`, `feasibility_input`, and `pf_structure`.
- **Response**: Architectural program, revenue, costs, developer returns, PF readiness, sensitivity, and data quality.

### `POST /projects/{project_id}/cashflow/calculate`
- **Description**: Trigger monthly cash flow calculation.
- **Request Body**: `{ "revenue_schedule": [...], "cost_schedule": [...] }`
- **Response**: Monthly CF arrays and Max Minus value.

### `POST /projects/{project_id}/pf/structure`
- **Description**: Configure and calculate PF structure.
- **Request Body**: `{ "tranches": [...] }`
- **Response**: LTV, LTC, DSCR, All-in-cost per tranche.

### `POST /projects/{project_id}/risk/assess`
- **Description**: Evaluate project risks.
- **Request Body**: `{ "project_data": {...} }`
- **Response**: Risk scores, mitigation strategies, and overall risk rating.

### `POST /projects/{project_id}/marketability/analyze`
- **Description**: Analyze saleability for saleable real estate assets such as apartments, officetels, knowledge industry centers, mixed-use projects, and commercial pre-sale projects.
- **Request Body**: Optional project data override.
- **Response**: Applicability, score, grade, saleability checks, and recommendations.

### `POST /projects/{project_id}/legal/review`
- **Description**: Run a preliminary Korean real estate PF legal, permit, land-rights, trust-security, and covenant review.
- **Request Body**: `{ "project_data": {...}, "law_context": {...} }`
- **Response**: Legal review status, check findings, Korean Law MCP connection state, references, and follow-up items.
- **Note**: `law_context` is designed for Korean Law MCP outputs. When no MCP context is supplied, the agent returns an explicit `connected: false` caution instead of pretending to have authoritative legal sources.

### `POST /projects/{project_id}/reports/generate`
- **Description**: Generate the PF Review Report.
- **Request Body**: `{ "template": "phase-11-pf-review", "options": {...} }`
- **Response**: Structured 20-section PF review report data plus Markdown compatibility output.

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
- **Response**: List of report metadata.

### `POST /projects/{project_id}/orchestrations/pf-review`
- **Description**: Run the MVP PF review orchestration workflow synchronously.
- **Request Body**: `{ "project_data": {...}, "options": { "user_id": "user-123", "ai_context": { "provider": "claude_desktop" } } }`
- **Response**: An orchestration run with status, step outputs, artifacts, and errors.

The workflow executes these steps:

- `project_load`: 프로젝트 데이터 로드
- `development_feasibility_analysis`: 디벨로퍼 사업수지분석
- `feasibility_calculation`: 사업성 계산
- `pf_structure_calculation`: PF 구조 계산
- `marketability_analysis`: 분양성 검증
- `legal_permit_review`: 법률/인허가 예비 검토
- `risk_assessment`: 리스크 평가
- `phase11_report_generation`: Phase 11 보고서 생성
- `quality_check`: 품질 점검

### `GET /projects/{project_id}/orchestrations`
- **Description**: List PF review orchestration runs for a project.
- **Response**: List of run metadata.

### `GET /projects/{project_id}/orchestrations/{run_id}`
- **Description**: Retrieve a specific orchestration run including steps and artifacts.
- **Response**: Full orchestration run payload.

### `POST /projects/{project_id}/orchestrations/{run_id}/approve`
- **Description**: Mark a completed orchestration run as approved.
- **Request Body**: `{ "approved_by": "credit_officer", "comment": "..." }`
- **Response**: Updated orchestration run.
