from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import PreconditionFailed
from google.cloud.storage import Blob, Bucket

from db.gcs_object_storage import GCSObjectStorage
from db.object_storage import get_object_storage


@pytest.fixture
def store():
    return GCSObjectStorage("pilot-evidence", "pilot-project", prefix="originals", retention_days=3)


def receipt(store, monkeypatch, *, size=5, crc="mnG7TA==", mode="Unlocked"):
    # Real SDK objects verify its property names and retention wire format.
    blob = Blob("originals/t1/p1/a", Bucket(None, "pilot-evidence"))
    until = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=3)
    properties = {
        "size": str(size), "crc32c": crc, "generation": "123", "metageneration": "1",
        "etag": "etag-123", "metadata": {"lifecycle-status": "active"},
        "retention": {"mode": mode, "retainUntilTime": until.isoformat().replace("+00:00", "Z")},
    }
    blob._set_properties(deepcopy(properties))
    upload = Mock(side_effect=lambda *a, **k: blob._set_properties(deepcopy(properties)))
    monkeypatch.setattr(blob, "upload_from_string", upload)
    monkeypatch.setattr(blob, "reload", Mock())
    monkeypatch.setattr(store, "_blob", Mock(return_value=blob))
    return blob, upload, until


def test_gcs_put_verifies_native_receipt_and_unlocked_retention(store, monkeypatch):
    blob, upload, until = receipt(store, monkeypatch)
    result = store.put("t1/p1/a", b"hello", metadata={"lifecycle-status": "active"},
                       retention_until=until, retention_mode="GOVERNANCE")
    assert result["versionId"] == "123"
    assert result["retentionMode"] == "GOVERNANCE"
    assert upload.call_args.kwargs["if_generation_match"] == 0
    assert upload.call_args.kwargs["checksum"] == "crc32c"
    assert blob._properties["retention"]["mode"] == "Unlocked"


@pytest.mark.parametrize("key", ["", ".", "..", "/a", "a/../b", "a//b", "a\\b", "C:/a", "a\x00b"])
def test_gcs_rejects_ambiguous_paths_before_credentials(store, key):
    with pytest.raises(ValueError):
        store.delete_prefix(key)
    assert store._client is None


@pytest.mark.parametrize("kwargs", [{"size": 6}, {"crc": "wrong"}, {"mode": "Locked"}])
def test_gcs_rejects_bad_write_receipt(store, monkeypatch, kwargs):
    _, _, until = receipt(store, monkeypatch, **kwargs)
    with pytest.raises(RuntimeError, match="^gcs_object_write_failed$"):
        store.put("t1/p1/a", b"hello", retention_until=until, retention_mode="GOVERNANCE")


def test_gcs_no_overwrite_on_conflicting_content(store, monkeypatch):
    _, upload, until = receipt(store, monkeypatch, size=6)
    upload.side_effect = PreconditionFailed("private provider detail")
    with pytest.raises(RuntimeError, match="^gcs_object_write_failed$"):
        store.put("t1/p1/a", b"hello", retention_until=until, retention_mode="GOVERNANCE")


def test_gcs_rounds_provider_retention_precision_up_not_down(store, monkeypatch):
    blob, upload, until = receipt(store, monkeypatch)
    requested = until.replace(microsecond=123456)
    def provider_receipt(*args, **kwargs):
        assert blob.retention.retain_until_time == until.replace(microsecond=124000)
    upload.side_effect = provider_receipt
    result = store.put("t1/p1/a", b"hello", retention_until=requested, retention_mode="GOVERNANCE")
    assert datetime.fromisoformat(result["retentionUntil"]) >= requested


def test_gcs_active_original_requires_retention(store, monkeypatch):
    receipt(store, monkeypatch)
    with pytest.raises(ValueError, match="explicit retention"):
        store.put("t1/p1/a", b"hello", metadata={"lifecycle-status": "active"})


def test_gcs_range_binds_generation_and_etag(store, monkeypatch):
    blob, _, _ = receipt(store, monkeypatch)
    monkeypatch.setattr(blob, "download_as_bytes", Mock(return_value=b"ell"))
    assert store.get_range("t1/p1/a", 1, 3, version_id="123", if_match="etag-123") == b"ell"
    assert blob.download_as_bytes.call_args.kwargs["if_generation_match"] == 123
    assert blob.download_as_bytes.call_args.kwargs["if_etag_match"] == "etag-123"
    blob.download_as_bytes.return_value = b"e"
    with pytest.raises(RuntimeError, match="gcs_object_range_failed"):
        store.get_range("t1/p1/a", 1, 3, version_id="123")


def test_gcs_head_returns_actual_security_metadata(store, monkeypatch):
    receipt(store, monkeypatch)
    head = store.head("t1/p1/a")
    assert head["objectLockMode"] == "GOVERNANCE"
    assert head["objectLockRetainUntil"]
    assert head["serverSideEncryption"] == "google-managed"
    assert head["versionId"] == "123"


def test_gcs_prefix_delete_has_directory_boundary(store):
    blob = Mock(name="blob")
    blob.name = "originals/t1/p1/a"
    blob.generation = 123
    store._client = Mock()
    store._client.list_blobs.return_value = [blob]
    assert store.delete_prefix("t1/p1")
    assert store._client.list_blobs.call_args.kwargs["prefix"] == "originals/t1/p1/"
    blob.delete.assert_called_once_with(if_generation_match=123, timeout=15, retry=None)


def test_gcs_factory_configuration(monkeypatch):
    monkeypatch.setenv("REALYTICS_OBJECT_STORAGE", "gcs")
    monkeypatch.setenv("REALYTICS_GCS_BUCKET", "pilot-evidence")
    monkeypatch.setenv("REALYTICS_GCP_PROJECT", "pilot-project")
    monkeypatch.setenv("REALYTICS_OBJECT_RETENTION_DAYS", "3")
    assert get_object_storage().backend == "gcs"
    monkeypatch.setenv("REALYTICS_OBJECT_RETENTION_DAYS", "0")
    with pytest.raises(ValueError):
        get_object_storage()
