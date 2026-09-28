# REALYTICS Design System

Updated: 2026-09-28
Status: Approved design contract v1; token/state-axis foundation implemented, screen migration incomplete
Primary users: Korean real-estate development, PF, credit, legal, and investment professionals

Implementation note (2026-07-22): `apps/web/app/globals.css` and the Tailwind
theme expose the v1 semantic tokens. `@repo/ui` provides FinancialMetric and
independent state-axis badges used by the Web application. Unused package-level
Button/Card/Code exports were removed on 2026-09-28 together with the starter
docs app; active Web controls and their design requirements remain. The former
create-turbo alert/link behavior is removed. Existing routes that still use a
generic status pill or raw slate/emerald/amber/rose classes remain migration
work and must not be treated as design-system compliant yet.

Follow-up (2026-07-24): the Settings surface now obtains its panel, control,
border, typography, and shadow treatment from `re-*` semantic tokens. The new
tenant invitation workflow preserves explicit lifecycle text alongside color
and keeps security warnings in the primary page flow rather than a transient
modal.

Decision-workspace migration (2026-07-24): project pages now use one
`ProjectContextHeader` with six business navigation groups instead of stacked
route tabs and memory cards. Shared status contracts cover execution, review,
approval, evidence, data quality, report eligibility, and value nature without
collapsing them into one color. `FinancialMetric` preserves raw/display units,
scenario, calculation Run, source Snapshot, calculation version, approval, and
report eligibility. The Dashboard is an API-backed work inbox; the document
review route is a three-pane Parser V2 workspace; and the feasibility route is
a Cash Flow/PF decision surface with a fail-visible Approval Gate. These are
the first controlled migrations, not evidence that every legacy route now
conforms to this standard.

Document-review follow-up (2026-07-27): the first evidence-first vertical slice
now consumes one typed, versioned server read model. It separates candidate
review from active snapshot approval, exposes `validationOutcome` plus
concurrent `issueCodes[]`, provides a durable review Queue and Parser snapshot
Changes view, and supports Fact-to-source and source-to-Fact navigation. The
shared state system now also has a dedicated `ApprovalReadinessStatusBadge`;
readiness is never encoded as an approval decision. Extraction confidence is
labelled as extraction confidence and cannot stand in for review, evidence, or
approval.

Financial-lineage follow-up (2026-07-27): Feasibility metrics no longer borrow
missing values from a mutable workspace projection, and an unrelated latest PF
approval is not attached to a CalculationRun. Until exact run/Snapshot
ID/Snapshot hash binding exists, the calculation remains visibly unbound and
analysis-only. PF Structure, Risk, Credit, and Covenant values are rendered
from one exact Chief artifact; a standalone Covenant recalculation is labelled
as analysis-only rather than silently replacing that package.

## 1. Design objective

REALYTICS is a decision operating system, not a promotional dashboard. The UI
must help a reviewer answer, without guessing:

1. What is the current deal and review context?
2. Which number or claim is being shown?
3. What unit, basis date, scenario, and calculation version apply?
4. Is the value sourced, assumed, calculated, estimated, conflicting, or
   missing?
5. Where is the underlying document/page/table/row/cell/API record?
6. What blocks approval and who must act next?
7. Did a process finish, and has a qualified human approved the result?

Visual polish supports these questions; it never replaces evidence or state.

## 2. Core principles

- **Decision first:** prioritize approval blockers, reconciliations, downside,
  leverage, dates, and source traceability over decorative KPIs.
- **Evidence visible:** material figures and claims expose a source locator or a
  clear missing-evidence state.
- **Independent state axes:** execution, approval, evidence, data quality, and
  report eligibility are displayed separately.
- **High-density, not clutter:** professionals need comparison tables, but
  hierarchy, alignment, pinned identifiers, and progressive disclosure keep
  them usable.
- **Conservative semantics:** red, amber, green, and approval language have
  controlled meanings. Decorative uses are prohibited.
- **Korean financial legibility:** long Korean labels, tabular numbers, signed
  amounts, rates, dates, and mixed KRW units are first-class constraints.
- **Accessible without color:** every state has text and, where useful, an icon
  or pattern in addition to color.
- **No false certainty:** missing, estimated, fallback, stale, and disputed data
  remain visibly distinct from approved data.

## 3. Visual direction

The default product surface is light, restrained, and institution-facing.
Dark mode may be supported for monitoring views, but it must preserve the same
semantic contrast and must not become a separate visual language.

### 3.1 Target color tokens

Token values below are the v1 target. Components consume semantic tokens rather
than raw Tailwind color names.

| Token | Value | Use |
|---|---:|---|
| `--re-bg-canvas` | `#F6F8FB` | Application canvas |
| `--re-bg-surface` | `#FFFFFF` | Primary surfaces |
| `--re-bg-subtle` | `#F1F5F9` | Table headers, secondary areas |
| `--re-border-default` | `#D8E0EA` | Standard borders |
| `--re-border-strong` | `#AAB7C7` | Focused grouping and frozen columns |
| `--re-text-primary` | `#0B172A` | Primary text |
| `--re-text-secondary` | `#475569` | Secondary labels |
| `--re-text-muted` | `#718096` | Metadata |
| `--re-brand-900` | `#08213F` | Navigation and institutional anchor |
| `--re-brand-700` | `#123F73` | Strong brand action |
| `--re-action-600` | `#1D5FD1` | Primary interactive action |
| `--re-info-600` | `#2563EB` | Informational state |
| `--re-success-600` | `#15803D` | Verified positive state |
| `--re-warning-600` | `#B45309` | Attention/conditional state |
| `--re-danger-600` | `#B42318` | Blocking/error/rejection state |
| `--re-neutral-600` | `#526071` | Neutral or unavailable state |

Rules:

- Use one brand/action family per screen.
- Do not introduce a new hue for each Pod, chart, or card.
- Green never means merely “completed”; it means the displayed semantic state
  is positively verified or approved.
- Red is reserved for blocking, failed, rejected, overdue, or threshold breach.
- Gradients are prohibited on business surfaces and status indicators. A
  controlled brand illustration on the public login/marketing surface is the
  only exception.

### 3.2 Typography

Preferred stack:

```text
Pretendard Variable, Noto Sans KR, Inter, system-ui, sans-serif
```

- Page title: 24–28 px, 700 weight.
- Section title: 17–20 px, 700 weight.
- Card/table title: 14–16 px, 600–700 weight.
- Body: 13–14 px, 400–500 weight.
- Metadata/help: 11–12 px, 400–500 weight.
- Financial numbers use `font-variant-numeric: tabular-nums`.
- Avoid all-bold layouts. Weight establishes hierarchy, not decoration.
- Korean business terms remain intact; internal camelCase paths are not shown
  to users.

### 3.3 Spacing, radius, and elevation

- Base spacing unit: 4 px.
- Standard gaps: 8, 12, 16, 24, 32 px.
- Control height: compact 32 px, standard 40 px, prominent 44 px.
- Surface radius: 8 px standard, 12 px for major panels.
- Pills are reserved for compact status/category labels.
- `rounded-2xl`, `rounded-3xl`, and large shadows are not default application
  patterns.
- Borders and spacing establish hierarchy before shadows.

## 4. Financial data standard

### 4.1 Unit contract

Every material number declares or inherits an explicit unit and basis.

| Data | Display rule |
|---|---|
| KRW amount | Right aligned; thousands separators; screen-level unit label |
| 억원/백만원 | One unit per table or chart; mixed units require explicit row labels |
| Percentage | One agreed decimal precision per metric family |
| Percentage point | Use `%p`, never `%` |
| Multiple | Use `x`, e.g. `1.68x` |
| Area | State `㎡`, `평`, 전용/공급/계약 basis |
| Price | State denominator, e.g. 원/㎡, 만원/평, 원/호 |
| Date | `YYYY-MM-DD`; basis date labelled separately from observation time |
| Negative amount | Minus sign and semantic tone; do not rely on parentheses alone |
| Missing | `—` plus reason/required action where material; never `0` |

The UI must not convert authoritative units silently. Conversion views preserve
the original value, conversion rule, and rounding policy.

### 4.2 Metric context

A report-grade metric exposes, directly or through a standard detail affordance:

- metric label and value;
- unit and basis;
- scenario/base case;
- as-of date;
- source or calculation run;
- calculation/schema version;
- evidence/data-quality status;
- comparison threshold when one exists.

### 4.3 Tables

- Text is left aligned; numbers are right aligned; status and short codes are
  centered only where it improves scanning.
- Header units apply to the whole column.
- Sticky headers and the minimal set of frozen identifier columns are allowed.
- Row hover must not hide selected, blocked, or conflict states.
- Sorting and filters expose the active criterion.
- Totals identify whether they are source totals, calculated totals, or
  reconciliation differences.
- Material rows link to evidence locators.
- Dense desktop tables may transform into labeled row groups on small screens;
  horizontal overflow is acceptable when lossless comparison is more important.

### 4.4 Charts

- Charts supplement exact values; they do not replace tables for approval data.
- Every chart states unit, date range, scenario, and data freshness.
- Use direct labels or a stable legend; avoid indistinguishable blue series.
- Cash flow distinguishes inflow, operating outflow, financing flow, debt
  balance, and cumulative cash consistently.
- Sensitivity matrices show the base cell, assumptions, and invalid/ineligible
  cells explicitly.
- Truncated axes, 3D charts, decorative gauges, and unexplained smoothing are
  prohibited.

## 5. State system

### 5.1 Independent status components

| Component | Required content |
|---|---|
| `ExecutionStatusBadge` | planned/pending/running/completed/blocked/failed plus time |
| `ApprovalReadinessStatusBadge` | not started/evaluating/not ready/ready/needs review/hold; never a human decision |
| `ApprovalStatusBadge` | reviewer decision and decision time/owner where allowed |
| `EvidenceStatusBadge` | source tier and locator availability |
| `DataQualityBadge` | missing/partial/conflicting/reconciled/failed |
| `ReportEligibilityBadge` | not eligible/conditional/eligible and reason |

Do not use one generic `StatusBadge` to collapse these semantics.

### 5.2 Screen states

Every server-backed screen defines:

- `loading`: skeleton preserving the final layout;
- `empty`: what is missing and the first allowed action;
- `unconfigured`: required provider/tenant/storage configuration;
- `permission_denied`: requested scope and safe recovery action;
- `stale`: last successful update and refresh action;
- `partial`: completed subset and missing subset;
- `conflict`: fields/entities in conflict and resolution route;
- `blocked`: policy or dependency that prevents progression;
- `failed`: error category, retry eligibility, correlation ID;
- `completed`: execution result only;
- `approved`: separate human/policy decision state.

A modal alone cannot carry a durable warning, approval condition, or failure
recovery instruction.

## 6. Page anatomy

Project pages follow a stable composition:

1. `ProjectContextHeader`: tenant/project, workflow track, version/as-of date,
   user role, freshness.
2. `DecisionSummary`: approval state, top blockers, required next action.
3. Primary work surface: table, document, calculation, map, or workflow.
4. `EvidenceRail` or contextual evidence drawer: locator and provenance.
5. `Activity/AuditTimeline`: material state changes and reviewer actions.
6. Persistent next-action area where a workflow requires user action.

Global navigation remains stable across projects. Page-specific tabs reflect
the business workflow rather than internal Agent or package names.

## 7. Component contract

Priority shared components are:

| Component | Purpose |
|---|---|
| `AppShell` | Navigation, tenant context, user and environment state |
| `ProjectContextHeader` | Project identity, version, review context |
| `DecisionSummary` | Approval outcome, top blockers, next actions |
| `FinancialMetric` | Number, unit, basis, source, freshness |
| `KoreanFinancialDataTable` | Dense, aligned, traceable financial data |
| `EvidenceLocatorLink` | Opens exact page/table/row/cell/API record |
| `WorkflowStepper` | Durable workflow progress without implying approval |
| Status components | Separate execution/approval/evidence/quality/eligibility |
| `RiskRegisterTable` | Severity, probability, owner, cure, evidence, decision |
| `FileUploader` | Quarantine/upload/scan/classification/processing states |
| `AuditTimeline` | Human, service, Agent, override, delegation events |
| `EmptyState` / `ErrorState` | Actionable, domain-specific recovery |

New pages use these primitives before inventing local variants. Components
accept semantic props rather than arbitrary color props.

## 8. Buttons and actions

- One primary action per decision region.
- Labels describe the consequence: `승인 요청`, `근거 확인`, `재계산 실행`,
  `심사보고서 생성`; avoid ambiguous `확인`.
- Destructive, approval, and irreversible actions require clear scope and an
  explicit confirmation step.
- A disabled action states why it is disabled and what resolves it.
- Double submission is prevented visually and server-side through idempotency.
- Optimistic UI must not show approval, payment, upload completion, or worker
  completion before server authority confirms it.

## 9. Copy and terminology

- Use the versioned REALYTICS glossary for domain labels.
- Prefer `검토 필요`, `조건부 승인`, `근거 부족`, `재계산 필요` to generic
  technical errors.
- Clearly distinguish `공식 검색 후보`, `검증된 원문 스냅샷`, and
  `사람 검토·승인 완료`.
- `AI 분석` states the permitted role: 분류, 요약, 설명, 초안. It must not
  imply that AI approved a deal or calculated an authoritative result.
- Internal IDs are copyable in technical details but not used as primary labels.

## 10. Prohibited patterns

- Meaningless gradients on product surfaces.
- Decorative emoji or unrelated icons.
- Every datum placed inside a separate card.
- A different radius, shadow, or action color per page.
- Status communicated only by color.
- Important instructions available only in a transient toast or modal.
- Raw JSON/camelCase paths in ordinary business UI.
- Green checks for provider connectivity or unreviewed AI output.
- Hiding missing values by rendering zero.
- Replacing detailed financial tables with oversized KPI cards.
- Animation that delays reading or suggests work completed before confirmation.

## 11. Accessibility and responsive behavior

- Target WCAG 2.2 AA contrast and keyboard access.
- Visible focus state is mandatory.
- Icon-only actions require accessible names and tooltips.
- Error text is programmatically associated with the relevant field.
- Touch targets remain usable on mobile even when desktop density is compact.
- Mobile prioritizes project context, decision state, blockers, and next action;
  full audit and evidence detail may use drawers without hiding their existence.
- Print and export layouts preserve units, provenance, conditions, and page
  breaks independently from screen styling.

## 12. Implementation and migration sequence

1. Convert this contract into design tokens and a component QA surface.
2. Replace starter/boilerplate shared UI with REALYTICS primitives.
3. Implement state-axis components and financial typography first.
4. Migrate login, global shell, project header, and one reference project page.
5. Validate with PF/credit practitioners before broad page migration.
6. Migrate document review, feasibility, cash flow, risk, legal, and report
   screens by workflow.
7. Add screenshot/visual regression at representative desktop and mobile sizes.
8. Remove page-local styles only after migrated routes pass functional and
   visual checks.

## 13. Definition of done

A migrated screen is complete when:

- its Screen Policy identifies user, source context, mutations, and all states;
- it uses semantic tokens and shared components;
- material numbers follow the unit and provenance contract;
- all five state axes remain distinguishable where applicable;
- keyboard, contrast, long Korean labels, dense tables, mobile, and print are
  verified;
- no mock or estimated value appears authoritative;
- functional tests and visual QA pass.
