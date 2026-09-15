"""Bootstrap a disposable CI database without application/production credentials."""
from pathlib import Path
import os
import sys

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def main():
    from db import postgres
    from psycopg import sql

    dsn = os.environ.get("REALYTICS_TEST_DATABASE_URL")
    if not dsn:
        raise RuntimeError("An explicit disposable REALYTICS_TEST_DATABASE_URL is required")
    with postgres.connect(dsn) as connection:
        # Supabase creates these roles before migrations; plain PostGIS does not.
        # They are inert test principals, without passwords or login capability.
        for role in ("anon", "authenticated", "service_role"):
            if connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone() is None:
                connection.execute(sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
                ).format(sql.Identifier(role)))
        role_source = API_ROOT.parents[1] / "supabase" / "roles.sql"
        connection.execute(role_source.read_text(encoding="utf-8"))
    print("Disposable PostgreSQL role bootstrap complete.")


if __name__ == "__main__":
    main()
