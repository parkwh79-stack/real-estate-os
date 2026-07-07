# Deployment Plan

| Environment | Purpose | Infrastructure | Deployment Method | Status | Priority |
|---|---|---|---|---|---|
| Local | Development | Docker Compose | Manual | In Progress | P0 |
| Staging | QA & Demo | TBD (e.g., AWS EC2/ECS) | CI/CD Pipeline | Planned | P1 |
| Production | Live Service | TBD (e.g., Kubernetes) | CI/CD Pipeline | Planned | P2 |

## Key Components
- **API Server**: Containerized FastAPI application.
- **Web Client**: Containerized Next.js application.
- **Database**: Managed DB service for Production.
- **Cache**: Managed Redis service for Production.

## Database migration (SQLite runtime → PostgreSQL/PostGIS)

The dev/pilot runtime uses a single SQLite file (`REALYTICS_SQLITE_FILE`).
Enterprise deployment moves to PostgreSQL + PostGIS.

- **Authoritative schema**: `services/api-server/db/migrations/0001_initial_schema.sql`
  — a faithful, typed mirror of `storage._ensure_sqlite_schema` (all 24 persisted
  stores + `async_jobs` + PostGIS `land_parcels`), `payload_json`/`metadata_json`
  as `JSONB` (GIN-indexed on the high-traffic stores), timestamps as `TIMESTAMPTZ`,
  composite PKs and every index preserved. Idempotent (`IF NOT EXISTS`).
- **Provisioning**: `db/postgres.py :: schema_ddl()` loads this file so the app's
  `ensure_schema()` and a manually-run migration provision identical tables (one
  source of truth). Configure via `REALYTICS_DATABASE_URL` or `REALYTICS_PG_*`.
- **Dual-backend wiring (done, v1)**: `REALYTICS_STORAGE_BACKEND=postgres`
  activates the Postgres backend; the default stays SQLite (safe, rollback-easy).
  `storage.py` routes the whole-state document read/write through
  `db/postgres.py` (`load_state_document`/`save_state_document`) into the
  `app_state` JSONB row — the same document model as the JSON-file backend, but
  durable/network-shared on Postgres. The document-store branches (`json` and
  `postgres`) share one code path via `_use_document_store()`; the sqlite
  per-table incremental fast path is untouched. Verified offline with a fake
  connection (psycopg is not installed here); live verification needs
  `pip install "psycopg[binary]"` + a running Postgres.
- **Next optimization (v2)**: native per-first-class-table persistence using
  the 0001 schema (JSONB per store + typed key columns) instead of one document
  row — so enterprise ad-hoc SQL/GIN queries hit indexed columns. Then a
  one-time data copy from the SQLite file into Postgres.

To run the migration manually:

```bash
psql "$REALYTICS_DATABASE_URL" -f services/api-server/db/migrations/0001_initial_schema.sql
```
