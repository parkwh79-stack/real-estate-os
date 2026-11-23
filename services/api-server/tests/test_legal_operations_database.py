from __future__ import annotations

import pytest

from db import legal_operations_database


def test_legal_operations_connection_has_no_runtime_fallback(monkeypatch):
    monkeypatch.delenv(
        legal_operations_database.LEGAL_OPERATIONS_DATABASE_URL_ENV,
        raising=False,
    )
    monkeypatch.setenv(
        "REALYTICS_DATABASE_URL",
        "postgresql://runtime/realytics",
    )
    with pytest.raises(
        legal_operations_database.LegalOperationsDatabaseConfigurationError,
        match="REALYTICS_LEGAL_OPERATIONS_DATABASE_URL",
    ):
        legal_operations_database.connect_legal_operations_database()
    assert (
        legal_operations_database.legal_operations_database_configured()
        is False
    )


def test_legal_operations_connection_uses_only_dedicated_dsn(monkeypatch):
    dedicated = "postgresql://legal-operations/realytics"
    monkeypatch.setenv(
        legal_operations_database.LEGAL_OPERATIONS_DATABASE_URL_ENV,
        dedicated,
    )
    captured = {}
    expected_connection = object()

    def _connect(dsn):
        captured["dsn"] = dsn
        return expected_connection

    monkeypatch.setattr(
        legal_operations_database.postgres,
        "connect",
        _connect,
    )
    assert (
        legal_operations_database.connect_legal_operations_database()
        is expected_connection
    )
    assert captured == {"dsn": dedicated}


def test_legal_operations_configuration_respects_explicit_environment(
    monkeypatch,
):
    monkeypatch.setenv(
        legal_operations_database.LEGAL_OPERATIONS_DATABASE_URL_ENV,
        "postgresql://ambient/realytics",
    )
    assert (
        legal_operations_database.legal_operations_database_configured(
            environ={},
        )
        is False
    )
    assert (
        legal_operations_database.legal_operations_database_configured(
            environ={
                legal_operations_database
                .LEGAL_OPERATIONS_DATABASE_URL_ENV: (
                    "postgresql://legal-operations/realytics"
                )
            },
        )
        is True
    )
