from __future__ import annotations

import pytest

from db import postgres
from db.postgres_runtime_privileges import required_table_privileges


class _MigrationCursor:
    def __init__(self, connection):
        self.connection = connection
        self._rows = []

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        self.connection.statements.append((normalized, params))
        if normalized.startswith("SELECT c.oid FROM pg_class AS c"):
            self._rows = [
                ("schema_migrations" if self.connection.ledger_exists else None,)
            ] if self.connection.ledger_exists else []
        elif normalized.startswith("SELECT version, checksum"):
            self._rows = list(self.connection.ledger.items())
        elif normalized.startswith("CREATE TABLE IF NOT EXISTS schema_migrations"):
            self.connection.ledger_exists = True
        elif normalized.startswith("INSERT INTO schema_migrations"):
            self.connection.ledger[str(params[0])] = str(params[1])
        elif not (
            normalized.startswith("SELECT pg_advisory_xact_lock")
            or normalized.startswith("CREATE TABLE IF NOT EXISTS schema_migrations")
        ):
            self.connection.applied_ddl.append(normalized)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _MigrationConnection:
    def __init__(self):
        self.ledger = {}
        self.ledger_exists = False
        self.statements = []
        self.applied_ddl = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _MigrationCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_sql_splitter_preserves_semicolons_inside_postgres_quoting() -> None:
    statements = postgres._split_sql_statements(
        """
        -- a comment; is not a statement
        COMMENT ON TABLE parser_project_profiles IS
            'first clause; second clause';
        CREATE FUNCTION sample() RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM 1;
        END;
        $$;
        SELECT "identifier;still_identifier";
        """
    )

    assert len(statements) == 3
    assert "'first clause; second clause'" in statements[0]
    assert "PERFORM 1;" in statements[1]
    assert '"identifier;still_identifier"' in statements[2]


def test_parser_v2_migration_comment_is_one_statement() -> None:
    migration = (
        postgres.MIGRATIONS_DIR / "0006_parser_v2_platform.sql"
    ).read_text(encoding="utf-8")
    statements = postgres._split_sql_statements(migration)
    profile_comments = [
        statement
        for statement in statements
        if statement.startswith("COMMENT ON TABLE parser_project_profiles")
    ]

    assert len(profile_comments) == 1
    assert "before creating parser_runs; parser_runs never reference" in profile_comments[0]


class _ReadinessCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        if normalized.startswith("SELECT version, checksum"):
            self.rows = list(self.connection.ledger.items())
        elif normalized.startswith("SELECT rolname, rolsuper"):
            self.rows = [
                (
                    "realytics_app",
                    self.connection.superuser,
                    False,
                    False,
                    False,
                    False,
                    self.connection.replication,
                )
            ]
        elif "FROM pg_auth_members AS membership" in normalized:
            self.rows = [(role,) for role in self.connection.member_of]
        elif "FROM pg_class AS c" in normalized:
            self.rows = [
                (table, True, True, False)
                for table in postgres.PARSER_V2_RLS_TABLES
            ]
        elif normalized == "SELECT current_schema()":
            self.rows = [("public",)]
        elif normalized.endswith("'USAGE')"):
            self.rows = [(True,)]
        elif normalized.endswith("'CREATE')"):
            self.rows = [(self.connection.schema_create,)]
        elif "WHEN relation_oid IS NULL THEN FALSE" in normalized:
            privilege, _schema, table = params
            expected = privilege in required_table_privileges().get(table, ())
            if self.connection.missing_privilege == (table, privilege):
                expected = False
            if self.connection.forbidden_privilege == (table, privilege):
                expected = True
            self.rows = [(expected,)]
        else:
            raise AssertionError(f"unexpected readiness SQL: {normalized}")

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _ReadinessConnection:
    def __init__(
        self,
        ledger,
        *,
        superuser=False,
        missing_privilege=None,
        forbidden_privilege=None,
        schema_create=False,
        replication=False,
        member_of=(),
    ):
        self.ledger = dict(ledger)
        self.superuser = superuser
        self.missing_privilege = missing_privilege
        self.forbidden_privilege = forbidden_privilege
        self.schema_create = schema_create
        self.replication = replication
        self.member_of = tuple(member_of)
        self.closed = False

    def cursor(self):
        return _ReadinessCursor(self)

    def close(self):
        self.closed = True


def test_ensure_schema_records_checksum_and_skips_applied_migration(
    monkeypatch, tmp_path
):
    migration = tmp_path / "0001_example.sql"
    migration.write_text(
        "CREATE TABLE IF NOT EXISTS example (id TEXT PRIMARY KEY);",
        encoding="utf-8",
    )
    monkeypatch.setattr(postgres, "MIGRATIONS_DIR", tmp_path)
    connection = _MigrationConnection()

    postgres.ensure_schema(connection)
    assert connection.applied_ddl == [
        "CREATE TABLE IF NOT EXISTS example (id TEXT PRIMARY KEY);"
    ]
    assert connection.ledger["0001_example.sql"] == postgres._migration_checksum(
        migration
    )

    connection.applied_ddl.clear()
    postgres.ensure_schema(connection)

    assert connection.applied_ddl == []
    assert connection.commits == 2
    lock_calls = [
        params
        for sql, params in connection.statements
        if sql.startswith("SELECT pg_advisory_xact_lock")
    ]
    assert lock_calls == [(postgres.MIGRATION_LOCK_KEY,)]


def test_ensure_schema_rejects_edited_applied_migration(monkeypatch, tmp_path):
    migration = tmp_path / "0001_example.sql"
    migration.write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setattr(postgres, "MIGRATIONS_DIR", tmp_path)
    connection = _MigrationConnection()
    postgres.ensure_schema(connection)

    migration.write_text("SELECT 2;", encoding="utf-8")

    with pytest.raises(postgres.MigrationChecksumMismatch, match="checksum"):
        postgres.ensure_schema(connection)

    assert connection.rollbacks == 1


def test_migration_checksum_is_independent_of_host_line_endings(tmp_path):
    migration = tmp_path / "0001_example.sql"
    migration.write_bytes(b"SELECT 1;\nSELECT 2;\n")
    lf_checksum = postgres._migration_checksum(migration)

    migration.write_bytes(b"SELECT 1;\r\nSELECT 2;\r\n")

    assert postgres._migration_checksum(migration) == lf_checksum


def test_migration_ledger_lookup_is_pinned_to_current_schema(
    monkeypatch, tmp_path
):
    migration = tmp_path / "0001_example.sql"
    migration.write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setattr(postgres, "MIGRATIONS_DIR", tmp_path)
    connection = _MigrationConnection()

    postgres.ensure_schema(connection)

    lookup_sql = [
        sql
        for sql, _params in connection.statements
        if sql.startswith("SELECT c.oid FROM pg_class AS c")
    ]
    assert lookup_sql
    assert all("n.nspname = current_schema()" in sql for sql in lookup_sql)


def test_parser_v2_runtime_readiness_is_read_only_and_checks_rls_role(
    monkeypatch, tmp_path
):
    migration = tmp_path / "0007_parser_v2_rls.sql"
    migration.write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setattr(postgres, "MIGRATIONS_DIR", tmp_path)
    connection = _ReadinessConnection(
        {migration.name: postgres._migration_checksum(migration)}
    )
    monkeypatch.setattr(postgres, "connect", lambda: connection)

    postgres.assert_parser_v2_runtime_ready()

    assert connection.closed is True


def test_parser_v2_runtime_readiness_rejects_privileged_role(monkeypatch, tmp_path):
    migration = tmp_path / "0007_parser_v2_rls.sql"
    migration.write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setattr(postgres, "MIGRATIONS_DIR", tmp_path)
    connection = _ReadinessConnection(
        {migration.name: postgres._migration_checksum(migration)},
        superuser=True,
    )
    monkeypatch.setattr(postgres, "connect", lambda: connection)

    with pytest.raises(
        postgres.ParserV2RuntimeReadinessError,
        match="superuser",
    ):
        postgres.assert_parser_v2_runtime_ready()

    assert connection.closed is True


@pytest.mark.parametrize(
    ("connection_kwargs", "expected"),
    [
        ({"replication": True}, "REPLICATION"),
        ({"member_of": ("privileged_parent",)}, "privileged_parent"),
    ],
)
def test_parser_v2_runtime_readiness_rejects_replication_and_membership(
    monkeypatch,
    tmp_path,
    connection_kwargs,
    expected,
):
    migration = tmp_path / "0007_parser_v2_rls.sql"
    migration.write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setattr(postgres, "MIGRATIONS_DIR", tmp_path)
    connection = _ReadinessConnection(
        {migration.name: postgres._migration_checksum(migration)},
        **connection_kwargs,
    )
    monkeypatch.setattr(postgres, "connect", lambda: connection)

    with pytest.raises(postgres.ParserV2RuntimeReadinessError, match=expected):
        postgres.assert_parser_v2_runtime_ready()

    assert connection.closed is True


@pytest.mark.parametrize(
    ("connection_kwargs", "expected"),
    [
        (
            {"missing_privilege": ("schema_migrations", "SELECT")},
            "missing=schema_migrations.SELECT",
        ),
        (
            {"forbidden_privilege": ("parser_runs", "UPDATE")},
            "forbidden=parser_runs.UPDATE",
        ),
        (
            {"schema_create": True},
            "schema:public.CREATE",
        ),
    ],
)
def test_parser_v2_runtime_readiness_enforces_privilege_manifest(
    monkeypatch,
    tmp_path,
    connection_kwargs,
    expected,
):
    migration = tmp_path / "0007_parser_v2_rls.sql"
    migration.write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setattr(postgres, "MIGRATIONS_DIR", tmp_path)
    connection = _ReadinessConnection(
        {migration.name: postgres._migration_checksum(migration)},
        **connection_kwargs,
    )
    monkeypatch.setattr(postgres, "connect", lambda: connection)

    with pytest.raises(postgres.ParserV2RuntimeReadinessError, match=expected):
        postgres.assert_parser_v2_runtime_ready()

    assert connection.closed is True
