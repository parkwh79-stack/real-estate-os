# Operational Gate validation — 2026-07-27

Status: Controlled; hosted database publication path verified, external
identity/object-store prerequisites still blocked

## Hosted Supabase result

Project `iorpwxxzvryuxhraiuhs` was healthy on PostgreSQL 17 in
`ap-northeast-1`. The following canonical deployment-ledger entries are live
after a rollback-only preflight and a ledger-only reconciliation of the
earlier MCP application timestamps:

- `20260727002612 normalized_report_publication`
- `20260727002700 report_publication_lineage_lookup_fix`

The corresponding REALYTICS checksum-ledger entries are:

- `20260727002612_normalized_report_publication.sql` —
  `8c5018d428ca2d364ef5bd02b19bf61c82e4428446c1fcae49e207ff50af4536`
- `20260727002700_report_publication_lineage_lookup_fix.sql` —
  `82c7db9b64c59cf1ed971532d3d898eb50d60e235dd40588d47fd29330b5bb2e`

The first live insert found a real PL/pgSQL defect before any publication was
accepted: dynamic `EXECUTE ... INTO` does not update `FOUND`. The original
applied migration was left immutable. A separate migration now uses explicit
`GET DIAGNOSTICS matched_rows = ROW_COUNT` after each normalized lineage
lookup.

After the fix:

- two concurrent publication attempts produced exactly one authority row;
- the surviving row retained one actor and one content/idempotency seal;
- a requester attempting to publish their own run was rejected by the
  database maker-checker trigger;
- mutation of the normalized report after publication was rejected;
- `report_publications` has RLS enabled and forced, with one exact
  `realytics.tenant_id` policy and matching `WITH CHECK`;
- `realytics_runtime` has schema `USAGE`, publication `SELECT/INSERT`, no
  schema `CREATE`, and no publication `UPDATE/DELETE`;
- `anon` and `authenticated` have no publication-table access;
- the post-apply Supabase security advisor returned no findings.

The management SQL principal is `BYPASSRLS` and is deliberately unable to
assume `realytics_runtime`. Therefore the hosted structural RLS audit is valid,
but the final behavioral A/B/unset-tenant exercise must still run through a
dedicated runtime DSN. The opt-in disposable-schema test is
`test_live_concurrent_report_publication_is_single_and_idempotent` in
`services/api-server/tests/test_parser_v2_postgres_live.py`.

## Ephemeral smoke fixture awaiting approved cleanup

The append-only trigger correctly prevents even an administrative delete. The
following non-business fixture remains until an operator explicitly authorizes
a single-transaction, exact-key cleanup that disables only
`trg_report_publications_immutable` while holding the table lock, deletes these
rows, immediately re-enables the trigger, and verifies zero residue:

- tenant: `codex-report-smoke-1785116671875`
- project: `codex-report-project-1785116671875`
- orchestration: `codex-report-run-1785116671875`
- Snapshot: `codex-report-snapshot-1785116671875`
- report: `codex-report-1785116671875`

No trigger or role-setting relaxation was applied.

## Authentication Gate

The first approved pilot boundary is now live. A real Google PKCE round trip
issued a Supabase session, FastAPI verified its bearer token, and normalized
PostgreSQL resolved the exact issuer/subject to the active `realytics-pilot`
tenant with the `admin` role. Email was not used as an authorization key. The
bootstrap is audit-recorded and subsequent admissions remain invitation-first.

The dedicated `realytics_identity` role was also exercised through the
Supavisor session pooler. It can read the normalized principal, membership, and
tenant status required by `/auth/me`, while membership mutation remains
denied. The rehearsal credential is short-lived and is not a production
secret-store deployment; it must be rotated into the approved secret manager
before this Gate can be called durable.

Google is therefore provider/session/membership-ready. Kakao remains
fail-closed because the hosted provider requests `account_email` and the Kakao
application has not yet proven that consent scope. TOTP enrollment is available
interactively, and interrupted unverified factors can be removed and restarted,
but the current smoke still has to receive and verify the user-entered code.
Final maker-checker proof additionally needs a second distinct invited
principal in the same tenant, with both users holding fresh signed `aal2`
sessions.

## Source evidence viewer Gate

The API serves tenant/project-scoped PDF and OpenXML bytes with `GET/HEAD`, one
HTTP range, ETag/If-Range, version-bound S3 reads, local bounded seeks, and
SHA-256 verification. PDF overlays require a valid stored
`pdf_points_top_left` locator. Workbook previews require an exact sheet/cell
range, are bounded to 400 cells, and reuse ZIP expansion/path/encryption
guards. Live S3/MinIO byte-range, Object Lock, IAM, scanner, and restore smoke
remain blocked by absent endpoint/bucket credentials and ClamAV runtime.

## Regression evidence

- API viewer/archive/job group: 20 passed.
- API focused identity/bootstrap/auth group: 49 passed.
- API normalized PostgreSQL/report group: 62 passed, 16 live tests skipped
  without a direct test DSN; the migration/fix subset was also rerun with 27
  passed.
- Playwright focused Auth BFF/error/MFA/Gate group: 21 passed in Chromium.
- Web TypeScript check and lint passed.
- Supabase migration wrapper check and `git diff --check` passed.

The skipped live tests are not counted as an operating pass. They require the
dedicated direct DSNs or external services named above.
