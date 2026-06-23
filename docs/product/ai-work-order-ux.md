# AI Work Order UX

Updated: 2026-06-20

## Goal

REALYTICS should not expose every engine as a raw menu first. The user should begin with a practical work order:

```text
Upload business materials
-> AI reads available evidence
-> AI explains what can be done now
-> AI lists missing materials
-> User chooses the workflow/output/validation conditions
-> Agents run only the needed work
-> Approval readiness and evidence links are reviewed
```

This design is inspired by real estate finance prompt-builder workflows, but REALYTICS extends the pattern with server-side evidence, Risk Register, Approval Readiness Gate, Excel formula auditability, and Evidence Graph persistence.

## Core UX Principles

1. Start from user intent, not system modules.
2. Show practical Korean finance terms instead of internal machine keys.
3. Always show missing evidence before producing a firm conclusion.
4. Let the user select outputs before running a workflow.
5. Attach validation conditions to every AI-generated workbook or report.
6. Preserve every prompt/work order as an auditable artifact in later phases.
7. Avoid making users choose between too many pages before they know what is missing.

## Primary Screen

The project dashboard should show:

- AI automatic diagnosis
- Items that need updates
- Next actions
- AI Work Order panel
- Fast upload
- Workflow menu
- Links to detailed modules

## AI Work Order Panel

The AI Work Order panel contains:

- Available workflows
- Recommended workflow
- Missing materials
- Output options
- Validation options
- Excel/AI prompt draft
- Copy button for Claude in Excel, ChatGPT, or internal Agent use

## Workflow Options

| Workflow | Korean Label | Purpose |
|---|---|---|
| land_review | 토지권원 검토 | PNU, official land price, registry, acquisition readiness |
| feasibility | 사업수지 분석 | revenue, cost, profit, IRR, equity metrics |
| marketability | 분양성·시장성 분석 | pricing, comparables, absorption, discount trigger |
| returns_stress | 수익지표·Stress Test | project/equity return metrics and stress scenarios |
| pf_structure_review | PF 구조·신용보강 검토 | LTV, LTC, Exit sale rate, credit enhancement |
| full_pf_review | 전체 PF 심사 | complete orchestration and Phase 11 report |

## Output Options

- PF 심사보고서
- 사업수지 Excel
- Cash Flow Workbook
- 리스크 등록부
- 승인 가능성 대시보드
- 근거 연결도
- 분양성 검토 메모
- 토지 증빙 패키지

## Validation Conditions

- 원본 파일 보존
- Excel 수식 경로 보존
- Sources & Uses 균형 검증
- 문서 간 숫자 대사
- 근거 위치 필수
- 무근거 값 생성 금지

## Implementation Status

Done:

- Backend `OrchestrationPlannerAgent` now returns output options, validation options, and prompt draft.
- Web `IntentPlanMenu` now renders an AI Work Order panel.
- Project dashboard was simplified around upload, diagnosis, work order, and next actions.
- Financial term mapping file was cleaned so internal keys render as Korean finance terms.
- Generated work orders are persisted as `prompt_artifact` records.
- Copied web prompt drafts are saved as `copied_prompt` artifacts.
- Prompt artifact list/get/upsert APIs are available under `/projects/{project_id}/prompt-artifacts`.
- A real estate template gallery is available at `/templates` with required materials, workflows, outputs, and validation checks for apartment PF, officetel PF, logistics PF, land-ledger review, REIT review, risk report, refinancing, and retail marketability.
- Kakao mobile intake now reuses the same AI Work Order model so users can upload documents, load recommended workflows, review outputs/validation checks, and run selected workflows from a concise mobile action center.

Next:

- Attach prompt artifacts to Evidence Graph.
- Add editable work-order assumptions before execution.
- Add a "create project from template" action that seeds expected materials and default workflow options.
- Clean the remaining legacy Kakao backend message text while preserving the safe-message policy.
