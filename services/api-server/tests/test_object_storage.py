from __future__ import annotations

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


def test_factory_falls_back_to_local_when_s3_misconfigured(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_OBJECT_STORAGE", "s3")
    monkeypatch.delenv("REALYTICS_S3_BUCKET", raising=False)
    store = get_object_storage(default_base_dir=tmp_path)
    assert store.backend == "local"


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
