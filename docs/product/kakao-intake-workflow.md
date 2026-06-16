# Kakao Intake Workflow

Updated: 2026-06-16

This document defines how REALYTICS should support a KakaoTalk-driven file intake and AI analysis workflow.

## Product Decision

KakaoTalk should not be the place where REALYTICS stores, parses, or exposes sensitive PF documents.

KakaoTalk should be used as:

1. Intake trigger
2. Mobile entry point
3. Short status notification
4. Summary answer channel

REALYTICS web/API remains the secure workspace for:

- Original file upload
- Source document archive
- Excel / PDF parsing
- Agent orchestration
- Evidence graph
- Risk register
- PF review report
- Detailed review results

## Recommended Flow

```text
KakaoTalk message
    -> "사업자료 업로드" action link
    -> REALYTICS Kakao Intake page
    -> User uploads business plan / land ledger / feasibility workbook
    -> REALYTICS source document archive
    -> Parser Agent / Land Agent / Development Feasibility Agent / Marketability Agent
    -> PF Review Orchestration
    -> Mobile Action Center summary
    -> KakaoTalk summary notification
    -> Detailed review remains in REALYTICS
```

## Current Implementation

Current code provides:

- `apps/web/app/project/[id]/kakao-intake/page.tsx`
- `document_upload_requested`, `document_analysis_started`, and `document_analysis_completed` Kakao event types
- `document_upload_requested` routing to `/project/{project_id}/kakao-intake?source=kakao&event=document_upload_requested`
- Existing secure upload endpoints reused by the Kakao Intake page
- Existing orchestration endpoint reused for AI analysis
- Existing Kakao message preview policy and audit log baseline

## Kakao Intake Page

The page is designed for mobile users entering from KakaoTalk.

It supports:

1. Business plan / basic plan PDF upload
2. Land ledger Excel upload and enrichment-ready parsing
3. Feasibility workbook Excel validation
4. AI PF review orchestration run
5. Link to mobile summary
6. Link to detailed orchestration review

Route:

```text
/project/{project_id}/kakao-intake?source=kakao&event=document_upload_requested
```

## Kakao Answer Policy

KakaoTalk may answer with a short summary such as:

```text
[REALYTICS 분석 완료]

현재 승인 가능성은 "보완 후 재심 필요"입니다.

주요 보완:
1. 토지권원·증빙 보완 필요
2. 리스크 검토상태 확정 필요
3. 금융구조 보강안 검토 필요

상세 내용은 REALYTICS에서 확인하세요.
```

KakaoTalk must not include:

- PF amount
- Loan/tranche amount
- Owner name
- Bank account
- PNU
- Registry / encumbrance detail
- Resident registration number
- Phone number
- Email address
- Legal opinion detail
- Risk memo detail
- Original document text
- Excel row/cell raw financial data

## Agent Responsibilities

| Step | Responsible module |
|---|---|
| File upload | Source Document / Upload Archive repository |
| Business plan parsing | Parser Agent / Upload-and-run workflow |
| Land ledger parsing | Land Ledger Import Agent |
| Land evidence enrichment | Land Ledger Enrichment Agent |
| Workbook validation | Feasibility Workbook Validator |
| Developer-side feasibility | Development Feasibility Agent |
| Market validation | Market Evidence Agent / Marketability Agent |
| PF orchestration | PF Review Orchestrator |
| Mobile summary | Mobile Action Center |
| Kakao summary | Kakao Message Adapter |
| Audit trail | User Activity Log |

## Data Security Rules

1. Never store uploaded project files inside KakaoTalk.
2. Never send original file contents through KakaoTalk messages.
3. Store uploaded files only in REALYTICS source document archive.
4. Record upload, preview, analysis, and review events in audit logs.
5. Use KakaoTalk only for safe summaries and links.
6. Require REALYTICS session before showing detailed results.
7. Keep evidence graph and source locators inside REALYTICS.

## Acceptance Criteria

- Kakao message preview can route `document_upload_requested` to the Kakao Intake page.
- Kakao Intake page can upload business plan PDF.
- Kakao Intake page can upload land ledger Excel and run land ledger analysis.
- Kakao Intake page can upload feasibility workbook and run workbook validation.
- Kakao Intake page can launch PF review orchestration.
- Kakao answer remains a summary and never exposes sensitive PF fields.
- Detailed report, evidence graph, and risk register remain inside REALYTICS.

## Next Engineering Steps

1. Add a server-side Kakao notification repository for live send attempts and delivery status.
2. Add safe AI summary generation for Kakao responses using approval readiness and risk register fields.
3. Add mobile upload progress and background job status polling.
4. Add Kakao Channel / BizMessage / AlimTalk provider abstraction.
5. Add real KakaoTalk in-app browser QA after deployment.
