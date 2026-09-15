from __future__ import annotations

import pytest

from db import postgres
from db.postgres_runtime_privileges import (
    required_column_privileges, required_sequence_privileges, required_table_privileges,
)


class _LegalReadinessCursor:
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
                    "realytics_runtime",
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                )
            ]
        elif "FROM pg_auth_members AS membership" in normalized:
            self.rows = []
        elif "FROM pg_class AS c" in normalized:
            requested_tables = tuple(params[0])
            self.rows = [
                (
                    table,
                    True,
                    table not in self.connection.not_forced,
                    table in self.connection.owned,
                )
                for table in requested_tables
                if table not in self.connection.missing
            ]
        elif normalized == "SELECT current_schema()":
            self.rows = [("realytics",)]
        elif normalized.endswith("'USAGE')"):
            self.rows = [(True,)]
        elif normalized.endswith("'CREATE')"):
            self.rows = [(False,)]
        elif "has_column_privilege" in normalized:
            column, privilege, _schema, table = params
            self.rows = [(column in required_column_privileges().get(table, {}).get(privilege, ()),)]
        elif "WHEN relation_oid IS NULL THEN FALSE" in normalized:
            privilege, _schema, table = params
            manifest = required_sequence_privileges() if "has_sequence_privilege" in normalized else required_table_privileges()
            self.rows = [
                (privilege in manifest.get(table, ()),)
            ]
        else:  # pragma: no cover - catches readiness SQL contract drift
            raise AssertionError(f"unexpected readiness SQL: {normalized}")

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _LegalReadinessConnection:
    def __init__(
        self,
        ledger,
        *,
        missing=(),
        not_forced=(),
        owned=(),
    ):
        self.ledger = dict(ledger)
        self.missing = set(missing)
        self.not_forced = set(not_forced)
        self.owned = set(owned)
        self.closed = False

    def cursor(self):
        return _LegalReadinessCursor(self)

    def close(self):
        self.closed = True


def _legal_migration(tmp_path):
    migration = tmp_path / postgres.LEGAL_INTELLIGENCE_MIGRATION
    migration.write_text("SELECT 1;", encoding="utf-8")
    return migration


def _governance_migration(tmp_path):
    migration = tmp_path / postgres.LEGAL_GOVERNANCE_MIGRATION
    migration.write_text("SELECT 2;", encoding="utf-8")
    return migration


def test_runtime_readiness_includes_all_legal_tenant_tables(
    monkeypatch,
    tmp_path,
) -> None:
    migration = _legal_migration(tmp_path)
    monkeypatch.setattr(postgres, "MIGRATIONS_DIR", tmp_path)
    connection = _LegalReadinessConnection(
        {migration.name: postgres._migration_checksum(migration)}
    )
    monkeypatch.setattr(postgres, "connect", lambda: connection)

    postgres.assert_parser_v2_runtime_ready()

    assert connection.closed is True


def test_runtime_readiness_includes_governance_tenant_tables(
    monkeypatch,
    tmp_path,
) -> None:
    legal_migration = _legal_migration(tmp_path)
    governance_migration = _governance_migration(tmp_path)
    monkeypatch.setattr(postgres, "MIGRATIONS_DIR", tmp_path)
    connection = _LegalReadinessConnection(
        {
            legal_migration.name: postgres._migration_checksum(
                legal_migration
            ),
            governance_migration.name: postgres._migration_checksum(
                governance_migration
            ),
        },
        missing=("legal_waivers",),
    )
    monkeypatch.setattr(postgres, "connect", lambda: connection)

    with pytest.raises(
        postgres.ParserV2RuntimeReadinessError,
        match="legal_waivers",
    ):
        postgres.assert_parser_v2_runtime_ready()


def test_runtime_readiness_requires_forced_rls_on_provider_replay_tables(
    monkeypatch,
    tmp_path,
) -> None:
    assert set(postgres.LEGAL_PROVIDER_REPLAY_RLS_TABLES).issubset(
        postgres.LEGAL_GOVERNANCE_RLS_TABLES
    )
    legal_migration = _legal_migration(tmp_path)
    governance_migration = _governance_migration(tmp_path)
    monkeypatch.setattr(postgres, "MIGRATIONS_DIR", tmp_path)
    connection = _LegalReadinessConnection(
        {
            legal_migration.name: postgres._migration_checksum(
                legal_migration
            ),
            governance_migration.name: postgres._migration_checksum(
                governance_migration
            ),
        },
        not_forced=("legal_provider_replay_events",),
    )
    monkeypatch.setattr(postgres, "connect", lambda: connection)

    with pytest.raises(
        postgres.ParserV2RuntimeReadinessError,
        match="legal_provider_replay_events",
    ):
        postgres.assert_parser_v2_runtime_ready()


@pytest.mark.parametrize(
    ("connection_kwargs", "expected"),
    [
        (
            {"missing": ("legal_evidence_snapshots",)},
            "legal_evidence_snapshots",
        ),
        (
            {"not_forced": ("legal_query_runs",)},
            "legal_query_runs",
        ),
        (
            {"owned": ("legal_review_decisions",)},
            "legal_review_decisions",
        ),
    ],
)
def test_runtime_readiness_rejects_unsafe_legal_tenant_table(
    monkeypatch,
    tmp_path,
    connection_kwargs,
    expected,
) -> None:
    migration = _legal_migration(tmp_path)
    monkeypatch.setattr(postgres, "MIGRATIONS_DIR", tmp_path)
    connection = _LegalReadinessConnection(
        {migration.name: postgres._migration_checksum(migration)},
        **connection_kwargs,
    )
    monkeypatch.setattr(postgres, "connect", lambda: connection)

    with pytest.raises(
        postgres.ParserV2RuntimeReadinessError,
        match=expected,
    ):
        postgres.assert_parser_v2_runtime_ready()

    assert connection.closed is True
