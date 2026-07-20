from __future__ import annotations

import json

import pytest

from scripts import register_legal_corpus_coverage as command
from use_cases.legal_corpus_coverage_registration import (
    LegalCorpusCoverageRegistrationResult,
)


def result(action: str) -> LegalCorpusCoverageRegistrationResult:
    return LegalCorpusCoverageRegistrationResult(
        action=action,
        coverage_registry_id=(
            "legal-corpus-coverage-registry:" + ("a" * 64)
        ),
        expected_contract_count=8,
        expected_contracts_sha256="b" * 64,
        provider_id="legal-provider:" + ("c" * 64),
        provider_registered=False,
    )


def test_command_never_falls_back_to_runtime_or_sync_dsn(monkeypatch):
    monkeypatch.delenv(
        command.MIGRATION_DATABASE_URL_ENV,
        raising=False,
    )
    monkeypatch.setenv(
        "REALYTICS_DATABASE_URL",
        "postgresql://runtime-secret",
    )
    monkeypatch.setenv(
        "REALYTICS_LEGAL_SYNC_DATABASE_URL",
        "postgresql://sync-secret",
    )

    with pytest.raises(
        RuntimeError,
        match="deployment-role DSN",
    ):
        command.main(["not-read.json", "--dry-run"])


@pytest.mark.parametrize(
    "flag,action,expected_exit",
    [
        ("--dry-run", "would_register", 0),
        ("--status", "would_register", command.STATUS_DRIFT_EXIT_CODE),
        ("--status", "current", 0),
    ],
)
def test_read_only_modes_report_safe_deterministic_status(
    monkeypatch,
    capsys,
    flag,
    action,
    expected_exit,
):
    marker = object()

    class Repository:
        def __init__(self, **kwargs):
            assert set(kwargs) == {"connection_factory", "schema_name"}

        def inspect(self, value):
            assert value is marker
            return result(action)

        def register(self, _value):
            raise AssertionError("read-only mode must not register")

    monkeypatch.setenv(
        command.MIGRATION_DATABASE_URL_ENV,
        "postgresql://migration-secret",
    )
    monkeypatch.setattr(
        command,
        "load_coverage_contract_set_manifest",
        lambda _path: marker,
    )
    monkeypatch.setattr(
        command,
        "PostgresLegalCorpusCoverageRegistrationRepository",
        Repository,
    )

    exit_code = command.main(["manifest.json", flag])

    assert exit_code == expected_exit
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == action
    assert "migration-secret" not in json.dumps(payload)


def test_apply_mode_is_explicit_and_calls_register(monkeypatch, capsys):
    marker = object()
    calls = []

    class Repository:
        def __init__(self, **_kwargs):
            pass

        def inspect(self, _value):
            raise AssertionError("apply mode must not inspect only")

        def register(self, value):
            calls.append(value)
            return result("registered")

    monkeypatch.setenv(
        command.MIGRATION_DATABASE_URL_ENV,
        "postgresql://migration",
    )
    monkeypatch.setattr(
        command,
        "load_coverage_contract_set_manifest",
        lambda _path: marker,
    )
    monkeypatch.setattr(
        command,
        "PostgresLegalCorpusCoverageRegistrationRepository",
        Repository,
    )

    assert command.main(["manifest.json"]) == 0
    assert calls == [marker]
    assert json.loads(capsys.readouterr().out)["action"] == "registered"
