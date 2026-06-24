# CLAUDE.md

**Read [AGENTS.md](AGENTS.md) first** — it is the invariant framework contract for
this repo (deterministic/evidence-first calculations, LLM-as-coordinator only, no
fabrication, human-gated learning, Korean lender-facing output) and the required
change convention.

On every change:
- Add a dated row to `docs/backlog.md` (the change log) and bump its `Updated:` date.
- Update the relevant `docs/product/*.md` when behavior/architecture changes.
- Keep tests green (`pytest`; web `tsc`/`lint`); never break the invariants in AGENTS.md §1.
