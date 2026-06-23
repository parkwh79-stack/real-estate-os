# Recursive Optimization Loop (usage-driven, human-in-the-loop)

Updated: 2026-06-20

Goal: as users use REALYTICS, capture their behaviour and feed it back into
continuous improvement — without compromising auditability, correctness, or
the compliance posture required for a Korean PF credit-review system.

## Design: three layers + a human gate

```
[1] Structured event capture     user_activity_logs (JSON/SQLite, metadata only)
        |                         eventType, normalized path, statusCode, durationMs,
        |                         safe metadata (workflow, recommendedWorkflow, blockerCodes)
        v  aggregate (deterministic)
[2] Distilled insights -> md      use_cases/usage_insights.py
        |                         GET /ops/usage-insights, /ops/optimization-log/preview
        |                         scripts/generate_optimization_log.py -> docs/ops/optimization-log.md
        v  maintainer reviews
[3] Human-reviewed change         docs/backlog.md -> normal PR + tests
```

Why not write raw usage straight to Markdown, or auto-modify code:

- **Markdown is for curated insight, not high-volume events.** Raw events go to
  the structured `user_activity_logs`; Markdown holds a periodic, distilled,
  human-readable report (`docs/ops/optimization-log.md`).
- **No autonomous self-modifying code.** A system that issues credit decisions
  must not silently rewrite itself. The loop produces *proposals*; humans review
  and the change goes through tests. Deterministic calculators (feasibility,
  cash flow, PF metrics, scenario, stress, credit enhancement) stay rule-based —
  the loop optimises UX, prompts, model routing, defaults, and prioritisation,
  never the math or the credit logic.

## Privacy / compliance rules

- Capture **metadata only**: event type, normalized request path (IDs stripped),
  status code, duration, counts, workflow keys, blocker codes.
- **Never** log PF amounts, owner names, account numbers, PNU, registry details,
  or document contents — same policy as the KakaoTalk message layer.
- The optimization report contains no sensitive data and is safe to keep in git.
  If any future signal could carry sensitive hints, anonymise/aggregate first.

## What is safe to optimise from usage

- Planner recommendation accuracy (which workflow users actually choose).
- Recurring approval blockers → prioritise guides/automation.
- UX friction / drop-off points.
- Slow endpoints → async/queue or query optimisation.
- Parser mock-fallback rate → prompt/model tuning.
- LLM provider/cost routing (see `docs/product/llm-provider-routing.md`).

## Implementation status (2026-06-20)

- ✅ Layer 1: `user_activity_logs` already captures request metadata + sensitive-action events.
- ✅ Layer 2: `use_cases/usage_insights.py` (`build_usage_insights`, `render_optimization_log_markdown`),
  `routers/ops.py` (`GET /ops/usage-insights`, `GET /ops/optimization-log/preview`),
  `scripts/generate_optimization_log.py` → `docs/ops/optimization-log.md`.
- ✅ Layer 3: reports are promoted by a maintainer into `docs/backlog.md`.

## Open follow-ups

- Schedule the generator (see the `schedule` skill / cron) for a recurring report.
- Frontend "optimization insights" panel for maintainers.
- Optional LLM enrichment of the report (Haiku) — proposal only, human-gated.
- Per-step LLM tiered routing informed by parser-failure signals.
