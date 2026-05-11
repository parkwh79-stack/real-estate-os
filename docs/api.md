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

### `POST /projects/{project_id}/feasibility/calculate`
- **Description**: Trigger feasibility calculation.
- **Request Body**: `{ "land": {...}, "regulation": {...}, ... }`
- **Response**: Feasibility results (Total revenue, costs, dev profit, margins).

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

### `POST /projects/{project_id}/reports/generate`
- **Description**: Generate the PF Review Report.
- **Request Body**: `{ "template": "phase-11-pf-review", "options": {...} }`
- **Response**: Structured 20-section PF review report data plus optional rendered Markdown/HTML.

#### Phase 11 report response sections

The `phase-11-pf-review` template should return these top-level sections:

- `cover`: 사업명, 심의일, 금융기관명, 심의내용 요약
- `financeConditions`: Tr.A/B/C 대출금액, 금리, 수수료, All-in Cost
- `bondPreservation`: 신탁구조, 책임준공, 연대보증, Covenant
- `discountTriggers`: 시기별 목표분양률 및 할인율
- `fundingStructure`: Cash-In/Out 상세 내역, 단위 백만원
- `fundUsage`: 자금 사용 용도
- `landSecuring`: 필지별 소유자, 지분율, 면적, 근저당권
- `projectSchedule`: 단계별 예정/완료 일정
- `financialStructureDiagram`: 차주, 신탁사, 시공사, 대주단 관계
- `salesPaymentSchedule`: 계약금, 중도금, 잔금 납입 일정
- `salesRevenueDistribution`: 분양수입금 배분 기준
- `summaryCashFlow`: 분양률 가정 기반 요약 CF
- `borrowerAnalysis`: 시행사 개요, 재무현황, 신용등급
- `contractorAnalysis`: 시공사 수주잔고, 재무비율, 신용등급
- `marketAnalysis`: 입지여건, 경쟁단지, 분양가 적정성
- `feasibilityAnalysis`: LTV, Exit 분양률, 분양가 LTV
- `exitStrategy`: 할인분양, 미분양, 대물변제, 임대 후 매각 시나리오
- `drawdownConditions`: 인출 선행/후행조건 체크리스트
- `eodTriggers`: 당연/선택적 기한이익상실
- `fundExecutionOrder`: 자금집행 순서 및 변제 충당 순위

### `GET /projects/{project_id}/reports`
- **Description**: Retrieve previously generated reports.
- **Response**: List of report metadata.
