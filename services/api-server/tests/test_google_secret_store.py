from datetime import datetime, timedelta, timezone
import json

import google_crc32c
from google.api_core.exceptions import NotFound, PermissionDenied
from google.cloud import secretmanager
import pytest

from core import google_secret_store as store
from core import secret_vault as vault


NOW = datetime(2026, 11, 3, 6, tzinfo=timezone.utc)
BOUNDARY = {"user_id": "user-one", "tenant_id": "tenant-one", "session_id": "session-one"}


class FakeGoogleSecrets:
    """No network; real SDK request types validate the provider contract."""

    def __init__(self):
        self.records = {}
        self.created = []
        self.deleted = []
        self.calls = []
        self.corrupt_checksum = False
        self.fail_add = False
        self.fail_read = False
        self.fail_delete = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def create_secret(self, *, request, retry, timeout):
        assert retry is None and timeout == 10
        req = secretmanager.CreateSecretRequest(request)
        self.created.append(req)
        name = f"{req.parent}/secrets/{req.secret_id}"
        self.records[name] = None
        return secretmanager.Secret(name=name)

    def add_secret_version(self, *, request, retry, timeout):
        assert retry is None and timeout == 10
        req = secretmanager.AddSecretVersionRequest(request)
        if self.fail_add:
            raise PermissionDenied("sensitive-provider-error")
        assert req.payload.data_crc32c == google_crc32c.value(req.payload.data)
        self.records[req.parent] = bytes(req.payload.data)
        # Google can canonicalize a project ID to its numeric project number.
        return secretmanager.SecretVersion(
            name=req.parent.replace("projects/test-project", "projects/123456789012") + "/versions/1"
        )

    def access_secret_version(self, *, request, retry, timeout):
        assert retry is None and timeout == 10
        req = secretmanager.AccessSecretVersionRequest(request)
        self.calls.append(req.name)
        if self.fail_read:
            raise PermissionDenied("sensitive-provider-error")
        name = req.name.rsplit("/versions/", 1)[0]
        if name not in self.records:
            raise NotFound("not found")
        data = self.records[name]
        checksum = google_crc32c.value(data) + int(self.corrupt_checksum)
        return secretmanager.AccessSecretVersionResponse(
            name=req.name, payload={"data": data, "data_crc32c": checksum}
        )

    def delete_secret(self, *, request, retry, timeout):
        assert retry is None and timeout == 10
        req = secretmanager.DeleteSecretRequest(request)
        if self.fail_delete:
            raise PermissionDenied("sensitive-provider-error")
        if req.name not in self.records:
            raise NotFound("not found")
        self.deleted.append(req.name)
        del self.records[req.name]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("REALYTICS_SECRET_BACKEND", "google_secret_manager")
    monkeypatch.setenv("REALYTICS_GCP_PROJECT", "test-project")
    monkeypatch.setenv("REALYTICS_GCP_SECRET_LOCATION", "asia-northeast1")
    monkeypatch.setenv("REALYTICS_AI_CREDENTIAL_TTL_SECONDS", "60")
    monkeypatch.setattr(vault, "_utc_now", lambda: NOW)
    fake = FakeGoogleSecrets()
    monkeypatch.setattr(store, "_client", lambda: fake)
    return fake


def ingest():
    return vault.ingest_user_credential(
        **BOUNDARY, provider="gemini", secret="unit-test-secret-value"
    )


def test_google_envelope_roundtrip_uses_pinned_version_and_replica(client):
    credential = ingest()
    ref = credential["credentialRef"]
    assert ref.startswith("gcp-sm://projects/test-project/secrets/realytics-user-ai-")
    assert ref.endswith("/versions/1")
    assert "unit-test-secret-value" not in json.dumps(credential)
    assert all(value not in ref for value in BOUNDARY.values())
    request = client.created[0]
    assert request.secret.replication.user_managed.replicas[0].location == "asia-northeast1"
    assert request.secret.ttl.total_seconds() == 120
    record = json.loads(next(iter(client.records.values())))
    assert record["expiresAt"] == (NOW + timedelta(seconds=60)).isoformat()
    assert vault.resolve_user_credential(ref, **BOUNDARY) == "unit-test-secret-value"
    assert vault.credential_access_status(ref, **BOUNDARY) == "active"
    assert ref not in vault._SESSION_SECRETS


@pytest.mark.parametrize("field", ["user_id", "tenant_id", "session_id"])
@pytest.mark.parametrize("value", ["other", None, ""])
def test_google_cross_boundary_read_status_and_delete_denied(client, field, value):
    ref = ingest()["credentialRef"]
    boundary = {**BOUNDARY, field: value}
    assert vault.resolve_user_credential(ref, **boundary) is None
    assert vault.credential_access_status(ref, **boundary) == "forbidden"
    assert vault.revoke_credential(ref, **boundary) is False
    assert client.deleted == []


def test_google_application_expiry_does_not_wait_for_cloud_cleanup(client, monkeypatch):
    ref = ingest()["credentialRef"]
    monkeypatch.setattr(vault, "_utc_now", lambda: NOW + timedelta(seconds=60))
    assert vault.resolve_user_credential(ref, **BOUNDARY) is None
    assert vault.credential_access_status(ref, **BOUNDARY) == "expired"
    # Reads do not mutate another tenant's storage. Logout still deletes.
    assert client.deleted == []
    assert vault.revoke_credential(ref, **BOUNDARY) is True
    assert vault.credential_access_status(ref, **BOUNDARY) == "missing"


def test_google_old_logout_cannot_delete_a_new_session_secret(client):
    old = ingest()["credentialRef"]
    new = ingest()["credentialRef"]
    assert old != new
    assert vault.revoke_credential(old, **BOUNDARY) is True
    assert vault.revoke_credential(old, **BOUNDARY) is False
    assert vault.resolve_user_credential(new, **BOUNDARY) == "unit-test-secret-value"
    # Existing server-owned profile replacement cleanup uses no boundary args.
    assert vault.revoke_credential(new) is True


@pytest.mark.parametrize("change", [
    lambda ref: ref.replace("test-project", "another-project"),
    lambda ref: ref.replace("/versions/1", "/versions/latest"),
    lambda ref: ref.replace("realytics-user-ai-", "database-password-"),
    lambda ref: ref + "/../2",
    lambda ref: ref + "?version=2",
    lambda ref: ref.replace("/versions/1", "/versions/0"),
])
def test_google_reference_scope_rejected_before_any_cloud_call(client, change):
    ref = change(ingest()["credentialRef"])
    assert store.read_record(ref) is None
    assert store.delete_record(ref) is False
    assert client.calls == []
    assert client.deleted == []


@pytest.mark.parametrize("payload", [b"not-json", b"[]", b'{}', b'"raw-key"', b"\xff"])
def test_google_unattested_payload_never_resolves(client, payload):
    ref = ingest()["credentialRef"]
    name = next(iter(client.records))
    client.records[name] = payload
    assert vault.resolve_user_credential(ref, **BOUNDARY) is None


def test_google_checksum_failure_is_fail_closed_and_secret_safe(client):
    ref = ingest()["credentialRef"]
    client.corrupt_checksum = True
    with pytest.raises(RuntimeError, match="credential read unavailable"):
        vault.resolve_user_credential(ref, **BOUNDARY)


def test_google_add_failure_cleans_up_its_unique_secret(client):
    client.fail_add = True
    with pytest.raises(RuntimeError, match="credential creation unavailable") as error:
        ingest()
    assert "sensitive-provider-error" not in str(error.value)
    assert error.value.__suppress_context__
    assert client.records == {}
    assert len(client.deleted) == 1


def test_google_add_and_cleanup_failure_has_expiring_orphan_not_success(client):
    client.fail_add = client.fail_delete = True
    with pytest.raises(RuntimeError, match="credential creation unavailable"):
        ingest()
    assert client.created[0].secret.ttl.total_seconds() == 120


@pytest.mark.parametrize("operation", ["read", "delete"])
def test_google_permission_failure_is_not_reported_as_missing(client, operation):
    ref = ingest()["credentialRef"]
    client.fail_read = operation == "read"
    client.fail_delete = operation == "delete"
    function = store.read_record if operation == "read" else store.delete_record
    with pytest.raises(RuntimeError) as error:
        function(ref)
    assert "sensitive-provider-error" not in str(error.value)


@pytest.mark.parametrize("key,value", [
    ("REALYTICS_GCP_PROJECT", ""),
    ("REALYTICS_GCP_PROJECT", "../other"),
    ("REALYTICS_GCP_SECRET_LOCATION", ""),
    ("REALYTICS_GCP_SECRET_LOCATION", "global"),
])
def test_google_config_requires_project_and_explicit_replica(client, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match=key):
        ingest()
    assert client.created == []


def test_google_oversize_rejected_before_secret_creation(client):
    with pytest.raises(ValueError, match="storage limit"):
        vault.ingest_user_credential(**BOUNDARY, provider="gemini", secret="x" * 65536)
    assert client.created == []
