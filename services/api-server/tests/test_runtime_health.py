from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from core.runtime_health import liveness_payload, readiness_payload
from db.postgres_runtime_pool import RuntimePoolName


class _Cursor:
    @staticmethod
    def fetchone():
        return (1,)


class _Connection:
    @contextmanager
    def transaction(self):
        yield

    @staticmethod
    def execute(_query: str):
        return _Cursor()


class _Pool:
    @contextmanager
    def connection(self, *, timeout: float):
        assert timeout > 0
        yield _Connection()


class _Registry:
    configured = True
    opened = True

    def __init__(self):
        self.specs = {
            RuntimePoolName.API: object(),
            RuntimePoolName.IDENTITY: object(),
        }

    @staticmethod
    def require_pool(name: RuntimePoolName):
        assert name in {RuntimePoolName.API, RuntimePoolName.IDENTITY}
        return _Pool(), SimpleNamespace(acquire_timeout_seconds=1.0)


def _application(registry=None):
    return SimpleNamespace(
        state=SimpleNamespace(postgres_runtime_pools=registry)
    )


def test_liveness_does_not_claim_dependency_readiness():
    assert liveness_payload() == {
        "schemaVersion": "realytics-runtime-health-v1",
        "status": "alive",
        "checks": {"process": "ok"},
    }


def test_local_readiness_allows_explicit_compatibility_lane(monkeypatch):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "development")
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY", "claims")

    status_code, payload = readiness_payload(_application())

    assert status_code == 200
    assert payload["status"] == "ready"
    assert payload["authority"]["businessData"] == "sqlite"
    assert payload["failureCodes"] == []


def test_hardened_readiness_fails_closed_without_runtime_pools(monkeypatch):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "staging")
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY", "postgres")
    monkeypatch.setenv("REALYTICS_OBJECT_STORAGE", "s3")
    monkeypatch.setenv("REALYTICS_S3_BUCKET", "documents")
    monkeypatch.setenv("REALYTICS_S3_SERVER_SIDE_ENCRYPTION", "aws:kms")
    monkeypatch.setenv("REALYTICS_S3_KMS_KEY_ID", "alias/test")
    monkeypatch.setenv("REALYTICS_OBJECT_RETENTION_DAYS", "30")
    monkeypatch.setenv("REALYTICS_OBJECT_LOCK_MODE", "COMPLIANCE")
    monkeypatch.setenv("REALYTICS_MALWARE_SCANNER", "clamav")
    monkeypatch.setenv("REALYTICS_CLAMAV_HOST", "clamav.internal")

    status_code, payload = readiness_payload(_application())

    assert status_code == 503
    assert payload["status"] == "not_ready"
    assert "postgres_runtime_pools_unavailable" in payload["failureCodes"]
    assert payload["checks"]["objectStorage"]["connectivityChecked"] is False
    assert payload["checks"]["malwareScanner"]["connectivityChecked"] is False


def test_hardened_readiness_pings_api_and_identity_pools(monkeypatch):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "staging")
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("REALYTICS_IDENTITY_MEMBERSHIP_AUTHORITY", "postgres")
    monkeypatch.setenv("REALYTICS_OBJECT_STORAGE", "s3")
    monkeypatch.setenv("REALYTICS_S3_BUCKET", "documents")
    monkeypatch.setenv("REALYTICS_S3_SERVER_SIDE_ENCRYPTION", "AES256")
    monkeypatch.setenv("REALYTICS_OBJECT_RETENTION_DAYS", "30")
    monkeypatch.setenv("REALYTICS_OBJECT_LOCK_MODE", "GOVERNANCE")
    monkeypatch.setenv("REALYTICS_MALWARE_SCANNER", "clamav")
    monkeypatch.setenv("REALYTICS_CLAMAV_HOST", "clamav.internal")

    status_code, payload = readiness_payload(_application(_Registry()))

    assert status_code == 200
    assert payload["status"] == "ready"
    assert payload["checks"]["postgresPools"] == {
        "api": "ok",
        "identity": "ok",
    }
    assert payload["failureCodes"] == []
