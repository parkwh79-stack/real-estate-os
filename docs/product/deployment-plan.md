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
- **Remaining wiring (deployment step)**: route `storage.py` load/save to the
  Postgres connector and adapt the read path to accept `dict`/`datetime` values
  the driver returns for `JSONB`/`TIMESTAMPTZ` columns (SQLite returns strings).
  Then run a one-time data copy from the SQLite file into Postgres.

To run the migration manually:

```bash
psql "$REALYTICS_DATABASE_URL" -f services/api-server/db/migrations/0001_initial_schema.sql
```
