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
- **Request Body**: `{ "options": {...} }`
- **Response**: Markdown string of the generated report.

### `GET /projects/{project_id}/reports`
- **Description**: Retrieve previously generated reports.
- **Response**: List of report metadata.
