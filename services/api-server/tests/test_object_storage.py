from __future__ import annotations

from datetime import datetime, timezone

import pytest

from db.object_storage import (
    LocalObjectStorage,
    S3ObjectStorage,
    get_object_storage,
    sha256_bytes,
)


def test_local_put_get_exists_roundtrip(tmp_path):
    store = LocalObjectStorage(tmp_path)
    meta = store.put("p1/doc.bin", b"hello", content_type="application/octet-stream")
    assert meta["backend"] == "local"
    assert meta["size"] == 5
    assert meta["sha256"] == sha256_bytes(b"hello")
    assert store.exists("p1/doc.bin")
    assert store.get("p1/doc.bin") == b"hello"
    assert store.local_path("p1/doc.bin") is not None


def test_local_delete_prefix(tmp_path):
    store = LocalObjectStorage(tmp_path)
    store.put("p1/a.bin", b"a")
    store.put("p1/b.bin", b"b")
    assert store.delete_prefix("p1") is True
    assert not store.exists("p1/a.bin")
    assert store.delete_prefix("p1") is False  # already gone


def test_local_rejects_path_traversal(tmp_path):
    store = LocalObjectStorage(tmp_path)
    with pytest.raises(ValueError):
        store.put("../escape.bin", b"x")


@pytest.mark.parametrize("key", ["", ".", "..", "/", "a/..", "a/../b", "a//b", "../sibling/file", "C:/file", "a\\..\\file"])
def test_local_storage_never_targets_root_or_ambiguous_paths(tmp_path, key):
    store = LocalObjectStorage(tmp_path / "objects")
    store.put("keep/original.bin", b"original")
    with pytest.raises(ValueError):
        store.delete_prefix(key)
    assert store.get("keep/original.bin") == b"original"


def test_local_storage_rejects_symlink_escape(tmp_path):
    base = tmp_path / "objects"
    outside = tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    (outside / "evidence.bin").write_bytes(b"private")
    try:
        (base / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires OS privileges")
    store = LocalObjectStorage(base)
    with pytest.raises(ValueError):
        store.get("linked/evidence.bin")
    with pytest.raises(ValueError):
        store.delete_prefix("linked")
    assert (outside / "evidence.bin").read_bytes() == b"private"


def test_factory_defaults_to_local(tmp_path, monkeypatch):
    monkeypatch.delenv("REALYTICS_OBJECT_STORAGE", raising=False)
    store = get_object_storage(default_base_dir=tmp_path)
    assert store.backend == "local"


def test_factory_selects_s3_when_configured(monkeypatch):
    monkeypatch.setenv("REALYTICS_OBJECT_STORAGE", "s3")
    monkeypatch.setenv("REALYTICS_S3_BUCKET", "realytics-docs")
    monkeypatch.setenv("REALYTICS_S3_PREFIX", "pf")
    store = get_object_storage()
    assert isinstance(store, S3ObjectStorage)
    assert store.backend == "s3"
    assert store._full_key("p1/doc.bin") == "pf/p1/doc.bin"


def test_factory_fails_closed_when_s3_misconfigured(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_OBJECT_STORAGE", "s3")
    monkeypatch.delenv("REALYTICS_S3_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="REALYTICS_S3_BUCKET"):
        get_object_storage(default_base_dir=tmp_path)


def test_hardened_s3_requires_encryption_and_object_lock(monkeypatch):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "production")
    monkeypatch.setenv("REALYTICS_OBJECT_STORAGE", "s3")
    monkeypatch.setenv("REALYTICS_S3_BUCKET", "realytics-docs")
    for key in (
        "REALYTICS_S3_SERVER_SIDE_ENCRYPTION",
        "REALYTICS_OBJECT_RETENTION_DAYS",
        "REALYTICS_OBJECT_LOCK_MODE",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(RuntimeError, match="Object Lock"):
        get_object_storage()


def test_s3_active_put_applies_encryption_metadata_and_retention():
    class _Client:
        def __init__(self):
            self.kwargs = None

        def put_object(self, **kwargs):
            self.kwargs = kwargs

    store = S3ObjectStorage(
        "realytics-docs",
        server_side_encryption="aws:kms",
        kms_key_id="kms-key",
        retention_days=365,
        retention_mode="COMPLIANCE",
    )
    client = _Client()
    store._client = client
    retain_until = datetime(2027, 7, 22, tzinfo=timezone.utc)

    metadata = store.put(
        "tenants/t1/projects/p1/active/doc.pdf",
        b"document",
        content_type="application/pdf",
        metadata={"malware-scan-status": "clean"},
        retention_until=retain_until,
        retention_mode="COMPLIANCE",
    )

    assert client.kwargs["ServerSideEncryption"] == "aws:kms"
    assert client.kwargs["SSEKMSKeyId"] == "kms-key"
    assert client.kwargs["ObjectLockMode"] == "COMPLIANCE"
    assert client.kwargs["ObjectLockRetainUntilDate"] == retain_until
    assert client.kwargs["Metadata"]["malware-scan-status"] == "clean"
    assert metadata["retentionMode"] == "COMPLIANCE"


def test_s3_backend_errors_clearly_without_boto3(monkeypatch):
    # boto3 is not a hard dependency; selecting S3 without it must fail loudly.
    store = S3ObjectStorage("bucket")
    import builtins

    real_import = builtins.__import__

    def _no_boto3(name, *args, **kwargs):
        if name == "boto3":
            raise ImportError("no boto3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_boto3)
    with pytest.raises(RuntimeError, match="boto3"):
        store.client()
