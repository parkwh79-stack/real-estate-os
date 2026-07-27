# REALYTICS workflow state model

Updated: 2026-07-27
Status: Controlled contract; source profiles are being migrated incrementally

## Authority rule

Technical runtimes keep their native status enums. The product UI does not
merge Parser V2, Agent Run, Outbox, Coding Pod, review, readiness, and approval
states into one universal enum.

The UI exposes independent decision axes and a versioned adapter from each
technical source profile to the relevant axis. Every projection preserves:

- the source profile;
- the raw source status;
- the canonical presentation status;
- a stable mapping code explaining any non-exact projection.

Unknown or absent source states fail closed as `unknown`. They are never
inferred as completed, approved, or report eligible.

## Execution adapter profiles

### `parser_v2`

The Parser V2 canonical values are exact mappings:

```text
pending | running | completed | completed_with_warnings | failed | cancelled
```

The shared decision axis additionally supports explicit `blocked` and
`skipped` presentations for server contracts that already emit those values.

### `recent_work_v2`

The operational inbox preserves the API's raw status and applies only these
reviewed aliases:

| Raw status | Canonical execution presentation | Mapping code |
|---|---|---|
| `queued` | `pending` | `QUEUED_AS_PENDING` |
| `retrying` | `running` | `RETRYING_AS_RUNNING` |
| `completed_with_blockers` | `completed_with_warnings` | `COMPLETED_WITH_BLOCKERS_AS_WARNING` |
| `needs_recalculation` | `completed_with_warnings` | `RECALCULATION_REQUIRED` |

Canonical values use `EXACT`. A missing or blank value uses
`NO_EXECUTION_RECORD`; an unsupported value uses `UNKNOWN_SOURCE_STATUS`.

## Independent decision axes

- execution answers whether a process ran;
- review answers what a reviewer concluded about an item;
- approval readiness answers whether policy permits an approval request;
- approval answers whether an authorized human made the decision;
- evidence, data quality, and report eligibility remain separate.

An adapter may project technical execution states only. It must not manufacture
a review, readiness, approval, evidence, quality, or eligibility state.

## Planned profiles

Agent Run, Outbox, and Coding Pod adapters require their own reviewed mapping
tables and parity tests. Until then, screens must show the native raw status or
`unknown` rather than reuse the `recent_work_v2` aliases.
