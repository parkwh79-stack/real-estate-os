from __future__ import annotations

import re
from pathlib import Path


MIGRATION = (
    Path(__file__).parents[1]
    / "db"
    / "migrations"
    / "0010_legal_corpus_coverage_contracts.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_coverage_migration_registers_exact_required_target_set():
    sql = _sql()
    target_rows = re.findall(
        r"\(\s*'law-go-kr',\s*'law-go-kr-full-corpus-v1',\s*"
        r"'([^']+)',\s*'([^']+)'\s*\)",
        sql,
    )

    assert set(target_rows) == {
        ("statute", "law"),
        ("provision", "law"),
        ("precedent", "prec"),
        ("interpretation", "expc"),
        ("administrative_rule", "admrul"),
        ("local_ordinance", "ordin"),
        ("constitutional_decision", "detc"),
        ("legal_term", "lstrmAI"),
    }
    assert len(target_rows) == 8


def test_coverage_identity_regexes_respect_postgres_repeat_limits():
    sql = _sql()

    assert "{2,299}" not in sql
    assert sql.count("BETWEEN 3 AND 300") == 5


def test_coverage_migration_is_append_only_and_concurrency_safe():
    sql = _sql()

    assert "uq_legal_coverage_registry_root" in sql
    assert "uq_legal_coverage_registry_successor" in sql
    assert "uq_legal_completion_coverage_root" in sql
    assert "uq_legal_completion_coverage_successor" in sql
    assert sql.count("pg_advisory_xact_lock") >= 2
    assert "NEW.registered_at := pg_catalog.statement_timestamp()" in sql
    assert "must supersede the current leaf" in sql
    assert "baseline_row.last_checkpoint_sequence + 1" in sql
    assert "NEW.first_checkpoint_sequence" in sql
    assert "coverage-bound completion must be verified" in sql
    assert "BEFORE UPDATE OR DELETE" in sql


def test_coverage_migration_binds_query_adapter_and_boundary_identity():
    sql = _sql()

    for field in (
        "querySha256",
        "sourceAdapterVersion",
        "boundaryContractVersion",
        "coverageIdentitySha256",
        "coverageContractId",
        "coverageRegistryId",
    ):
        assert field in sql
    assert "sync run typed coverage identity is inconsistent" in sql
    assert "completion includes another coverage contract run" in sql
    assert "coverage_contract_id IS NULL" in sql
