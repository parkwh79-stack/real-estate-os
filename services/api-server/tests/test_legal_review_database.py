from __future__ import annotations

from contextlib import contextmanager

import pytest

from db import legal_review_database


class _Connection:
    def __init__(self, role: str):
        self.role = role
        self.closed = False

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, statement):
        assert "current_user, session_user" in statement

    def fetchone(self):
        return (self.role, self.role, True, False, False, False, False, False, False)

    def close(self):
        self.closed = True


def test_legal_review_connection_has_no_runtime_or_sync_fallback(monkeypatch):
    monkeypatch.delenv(
        legal_review_database.LEGAL_REVIEW_DATABASE_URL_ENV,
        raising=False,
    )
    monkeypatch.setenv(
        "REALYTICS_DATABASE_URL",
        "postgresql://runtime/realytics",
    )
    monkeypatch.setenv(
        "REALYTICS_LEGAL_SYNC_DATABASE_URL",
        "postgresql://sync/realytics",
    )
    with pytest.raises(
        legal_review_database.LegalReviewDatabaseConfigurationError,
        match="REALYTICS_LEGAL_REVIEW_DATABASE_URL",
    ):
        legal_review_database.connect_legal_review_database()
    assert legal_review_database.legal_review_database_configured() is False


def test_legal_review_connection_uses_only_dedicated_dsn(monkeypatch):
    dedicated = "postgresql://legal-review/realytics"
    monkeypatch.setenv(
        legal_review_database.LEGAL_REVIEW_DATABASE_URL_ENV,
        dedicated,
    )
    captured = {}
    expected_connection = _Connection("realytics_legal_review")

    def _connect(dsn):
        captured["dsn"] = dsn
        return expected_connection

    monkeypatch.setattr(legal_review_database.postgres, "connect", _connect)
    assert (
        legal_review_database.connect_legal_review_database()
        is expected_connection
    )
    assert captured == {"dsn": dedicated}
    assert legal_review_database.legal_review_database_configured() is True


def test_legal_review_connection_rejects_runtime_role_on_dedicated_dsn(monkeypatch):
    monkeypatch.setenv(legal_review_database.LEGAL_REVIEW_DATABASE_URL_ENV, "configured")
    wrong_connection = _Connection("realytics_runtime")
    monkeypatch.setattr(legal_review_database.postgres, "connect", lambda _dsn: wrong_connection)

    with pytest.raises(legal_review_database.LegalReviewDatabaseConfigurationError):
        legal_review_database.connect_legal_review_database()
    assert wrong_connection.closed is True
