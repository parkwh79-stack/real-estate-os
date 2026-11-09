from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from db.legal_raw_object_storage import (
    LegalRawObjectImmutableConflict,
    LegalRawObjectStorageConfigurationError,
    S3ImmutableLegalRawObjectStore,
    get_immutable_legal_raw_object_store,
)
from use_cases.legal_corpus_reconciliation import RawObjectIdentity


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
CONTENT = b"official legal source payload"
SHA256 = hashlib.sha256(CONTENT).hexdigest()
OBJECT_KEY = f"legal-corpus/raw/provider/{SHA256[:2]}/{SHA256}"


class FakeClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": str(code)}}


class FakeS3Client:
    def __init__(self):
        self.versioning = {"Status": "Enabled"}
        self.lock_configuration = {
            "ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}
        }
        self.heads = {}
        self.put_calls = []
        self.list_response = {
            "Versions": [],
            "DeleteMarkers": [],
            "IsTruncated": False,
        }
        self.retentions = {}
        self.retention_calls = []

    def get_bucket_versioning(self, **_kwargs):
        return self.versioning

    def get_object_lock_configuration(self, **_kwargs):
        return self.lock_configuration

    def head_object(self, *, Bucket, Key, VersionId=None):
        del Bucket
        lookup = (Key, VersionId)
        if lookup in self.heads:
            return dict(self.heads[lookup])
        if VersionId is None:
            current = [
                value
                for (key, _version), value in self.heads.items()
                if key == Key
            ]
            if current:
                return dict(current[-1])
        raise FakeClientError("404")

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        version_id = "immutable-version-1"
        retain_until = kwargs["ObjectLockRetainUntilDate"]
        head = {
            "VersionId": version_id,
            "ContentLength": len(kwargs["Body"]),
            "Metadata": dict(kwargs["Metadata"]),
            "ObjectLockMode": kwargs["ObjectLockMode"],
            "ObjectLockRetainUntilDate": retain_until,
        }
        self.heads[(kwargs["Key"], version_id)] = head
        return {"VersionId": version_id}

    def list_object_versions(self, **_kwargs):
        return dict(self.list_response)

    def get_object_retention(self, *, Bucket, Key, VersionId):
        del Bucket
        return {
            "Retention": dict(
                self.retentions.get((Key, VersionId), {})
            )
        }

    def put_object_retention(self, **kwargs):
        self.retention_calls.append(kwargs)
        self.retentions[
            (kwargs["Key"], kwargs["VersionId"])
        ] = dict(kwargs["Retention"])
        return {}


def store(client):
    return S3ImmutableLegalRawObjectStore(
        bucket="legal-evidence",
        storage_prefix="realytics",
        minimum_retention_days=365,
        client=client,
        clock=lambda: NOW,
    )


def locked_head(*, sha=SHA256, size=len(CONTENT), version="v-existing"):
    return {
        "VersionId": version,
        "ContentLength": size,
        "Metadata": {
            "sha256": sha,
            "provider-id": "provider-1",
            "sync-run-id": "sync-run-1",
        },
        "ObjectLockMode": "COMPLIANCE",
        "ObjectLockRetainUntilDate": NOW + timedelta(days=365),
    }


def test_put_if_absent_creates_versioned_object_lock_receipt():
    client = FakeS3Client()
    adapter = store(client)

    receipt = adapter.put_if_absent(
        object_key=OBJECT_KEY,
        content=CONTENT,
        content_type="application/json",
        expected_sha256=SHA256,
        metadata={
            "provider-id": "provider-1",
            "sync-run-id": "sync-run-1",
        },
    )

    assert receipt.object_key == OBJECT_KEY
    assert receipt.object_version == "immutable-version-1"
    assert receipt.sha256 == SHA256
    assert receipt.size_bytes == len(CONTENT)
    assert client.put_calls[0]["IfNoneMatch"] == "*"
    assert client.put_calls[0]["ObjectLockMode"] == "COMPLIANCE"
    assert (
        client.put_calls[0]["ObjectLockRetainUntilDate"]
        == NOW + timedelta(days=365)
    )
    assert client.put_calls[0]["Metadata"]["sha256"] == SHA256


def test_put_if_absent_reuses_matching_locked_current_version():
    client = FakeS3Client()
    full_key = f"realytics/{OBJECT_KEY}"
    client.heads[(full_key, "v-existing")] = locked_head()

    receipt = store(client).put_if_absent(
        object_key=OBJECT_KEY,
        content=CONTENT,
        content_type="application/json",
        expected_sha256=SHA256,
        metadata={"provider-id": "provider-1"},
    )

    assert receipt.object_version == "v-existing"
    assert client.put_calls == []


def test_put_if_absent_rejects_existing_content_mismatch():
    client = FakeS3Client()
    full_key = f"realytics/{OBJECT_KEY}"
    client.heads[(full_key, "v-existing")] = locked_head(
        sha="f" * 64
    )

    with pytest.raises(
        LegalRawObjectImmutableConflict,
        match="different content",
    ):
        store(client).put_if_absent(
            object_key=OBJECT_KEY,
            content=CONTENT,
            content_type="application/json",
            expected_sha256=SHA256,
            metadata={"provider-id": "provider-1"},
        )


@pytest.mark.parametrize(
    ("versioning", "lock_enabled", "message"),
    [
        (False, True, "versioning"),
        (True, False, "Object Lock"),
    ],
)
def test_configuration_fails_closed_without_versioning_and_object_lock(
    versioning,
    lock_enabled,
    message,
):
    client = FakeS3Client()
    if not versioning:
        client.versioning = {"Status": "Suspended"}
    if not lock_enabled:
        client.lock_configuration = {
            "ObjectLockConfiguration": {"ObjectLockEnabled": "Disabled"}
        }

    with pytest.raises(
        LegalRawObjectStorageConfigurationError,
        match=message,
    ):
        store(client).verify_configuration()


def test_inventory_returns_version_receipts_and_opaque_cursor():
    client = FakeS3Client()
    full_key = f"realytics/{OBJECT_KEY}"
    client.heads[(full_key, "v1")] = locked_head(version="v1")
    client.list_response = {
        "Versions": [
            {
                "Key": full_key,
                "VersionId": "v1",
                "Size": len(CONTENT),
                "LastModified": NOW - timedelta(days=3),
            }
        ],
        "DeleteMarkers": [],
        "IsTruncated": True,
        "NextKeyMarker": full_key,
        "NextVersionIdMarker": "v1",
    }
    adapter = store(client)

    first_page = adapter.list_inventory(
        prefix="legal-corpus/raw/",
        cursor=None,
        limit=100,
    )

    assert first_page.inventory_complete is False
    assert first_page.next_cursor
    assert first_page.items[0].identity == RawObjectIdentity(
        OBJECT_KEY, "v1"
    )
    assert first_page.items[0].sha256 == SHA256

    client.list_response = {
        "Versions": [],
        "DeleteMarkers": [],
        "IsTruncated": False,
    }
    second_page = adapter.list_inventory(
        prefix="legal-corpus/raw/",
        cursor=first_page.next_cursor,
        limit=100,
    )
    assert second_page.inventory_complete is True


def test_inventory_rejects_delete_markers_in_immutable_prefix():
    client = FakeS3Client()
    client.list_response["DeleteMarkers"] = [
        {"Key": f"realytics/{OBJECT_KEY}", "VersionId": "deleted"}
    ]

    with pytest.raises(RuntimeError, match="delete markers"):
        store(client).list_inventory(
            prefix="legal-corpus/raw/",
            cursor=None,
            limit=100,
        )


def test_ensure_retention_extends_and_verifies_hold():
    client = FakeS3Client()
    identity = RawObjectIdentity(OBJECT_KEY, "v1")
    full_key = f"realytics/{OBJECT_KEY}"
    client.retentions[(full_key, "v1")] = {
        "Mode": "COMPLIANCE",
        "RetainUntilDate": NOW + timedelta(days=10),
    }

    receipt = store(client).ensure_retention(
        identity=identity,
        retain_until=NOW + timedelta(days=400),
        reason="orphan reconciliation",
        metadata={"reconciliation-run-id": "run-1"},
    )

    assert receipt.identity == identity
    assert receipt.immutable is True
    assert receipt.retained_until == NOW + timedelta(days=400)
    assert client.retention_calls[0]["BypassGovernanceRetention"] is False


def test_factory_has_no_local_fallback(monkeypatch):
    monkeypatch.delenv(
        "REALYTICS_LEGAL_RAW_OBJECT_STORAGE", raising=False
    )
    monkeypatch.delenv("REALYTICS_LEGAL_RAW_BUCKET", raising=False)

    with pytest.raises(
        LegalRawObjectStorageConfigurationError,
        match="must be s3",
    ):
        get_immutable_legal_raw_object_store(client=FakeS3Client())


def test_secret_bearing_metadata_is_rejected():
    client = FakeS3Client()

    with pytest.raises(ValueError, match="secret-bearing"):
        store(client).put_if_absent(
            object_key=OBJECT_KEY,
            content=CONTENT,
            content_type="application/json",
            expected_sha256=SHA256,
            metadata={"service-key": "must-not-be-stored"},
        )


def test_put_requires_content_addressed_object_key():
    client = FakeS3Client()

    with pytest.raises(ValueError, match="end with expected_sha256"):
        store(client).put_if_absent(
            object_key="legal-corpus/raw/provider/not-the-digest",
            content=CONTENT,
            content_type="application/json",
            expected_sha256=SHA256,
            metadata={"provider-id": "provider-1"},
        )
