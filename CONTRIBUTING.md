# Contributing

Read [AGENTS.md](AGENTS.md) before changing code. Preserve deterministic
calculations, evidence lineage, human approval and tenant boundaries.

1. Use a branch and a pull request; explain the user-visible outcome.
2. Use synthetic test data. Never attach `.env`, runtime databases, uploaded
   originals, access tokens or unapproved business data.
3. Add regression tests and update the relevant product contract and
   `docs/backlog.md`. Do not relabel missing evidence as approved.
4. Run the relevant API tests, Web typecheck/lint and browser tests. Financial,
   permission and database changes require the corresponding CI integration
   tests, not just a successful build.
5. Do not edit applied migrations. Add a reviewed migration and verify both
   the canonical SQL and generated Supabase wrapper.

Report security issues through [SECURITY.md](SECURITY.md), not public issues.
The software is distributed under the [MIT License](LICENSE). Third-party
materials retain their own rights and conditions.
