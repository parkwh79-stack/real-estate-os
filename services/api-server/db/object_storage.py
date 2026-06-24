from __future__ import annotations

"""Object storage abstraction for source documents and exports.

A single interface over where binary objects live, so the rest of the app does
not care whether it is local disk (default, dev/MVP) or S3/R2/MinIO (production).
The local backend is the current behaviour and is fully tested; the S3 backend
lazy-imports boto3 and is selected by env, so deployment is a config change with
no code change. PF documents are sensitive, so the default stays on-prem.
"""

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Protocol


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class ObjectStorage(Protocol):
    """Minimal object-store contract used by the archive/export repositories."""

    backend: str

    def put(self, key: str, content: bytes, *, content_type: Optional[str] = None) -> Dict[str, Any]: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete_prefix(self, prefix: str) -> bool: ...
    def local_path(self, key: str) -> Optional[Path]: ...


class LocalObjectStorage:
    """Filesystem-backed object storage (default)."""

    backend = "local"

    def __init__(self, base_dir: Path | str):
        self.base_dir = Path(base_dir).resolve()

    def _resolve(self, key: str) -> Path:
        path = (self.base_dir / key).resolve()
        if self.base_dir != path and self.base_dir not in path.parents:
            raise ValueError("Invalid object key (path traversal)")
        return path

    def put(self, key: str, content: bytes, *, content_type: Optional[str] = None) -> Dict[str, Any]:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        return {
            "backend": self.backend,
            "key": key,
            "size": len(content),
            "sha256": sha256_bytes(content),
            "contentType": content_type,
        }

    def get(self, key: str) -> bytes:
        return self._resolve(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()

    def delete_prefix(self, prefix: str) -> bool:
        target = self._resolve(prefix)
        if not target.exists():
            return False
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return True

    def local_path(self, key: str) -> Optional[Path]:
        path = self._resolve(key)
        return path if path.exists() else None


class S3ObjectStorage:
    """S3/R2/MinIO-backed object storage (production).

    boto3 is imported lazily so the dependency is only required when this backend
    is actually selected. Endpoint URL lets the same code target AWS S3,
    Cloudflare R2, or self-hosted MinIO.
    """

    backend = "s3"

    def __init__(self, bucket: str, prefix: str = "", *, endpoint_url: Optional[str] = None, region: Optional[str] = None):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._endpoint_url = endpoint_url
        self._region = region
        self._client = None

    def _full_key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def client(self):
        if self._client is None:
            try:
                import boto3  # noqa: WPS433  (lazy: only when S3 backend is used)
            except ImportError as exc:  # pragma: no cover - depends on deploy env
                raise RuntimeError(
                    "S3 object storage selected but boto3 is not installed. "
                    "Add boto3 to requirements and `pip install -r requirements.txt`."
                ) from exc
            self._client = boto3.client("s3", endpoint_url=self._endpoint_url, region_name=self._region)
        return self._client

    def put(self, key: str, content: bytes, *, content_type: Optional[str] = None) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"Bucket": self.bucket, "Key": self._full_key(key), "Body": content}
        if content_type:
            kwargs["ContentType"] = content_type
        self.client().put_object(**kwargs)
        return {
            "backend": self.backend,
            "key": key,
            "bucket": self.bucket,
            "size": len(content),
            "sha256": sha256_bytes(content),
            "contentType": content_type,
        }

    def get(self, key: str) -> bytes:
        obj = self.client().get_object(Bucket=self.bucket, Key=self._full_key(key))
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client().head_object(Bucket=self.bucket, Key=self._full_key(key))
            return True
        except Exception:
            return False

    def delete_prefix(self, prefix: str) -> bool:
        client = self.client()
        full = self._full_key(prefix)
        listed = client.list_objects_v2(Bucket=self.bucket, Prefix=full)
        contents = listed.get("Contents") or []
        if not contents:
            return False
        client.delete_objects(
            Bucket=self.bucket,
            Delete={"Objects": [{"Key": item["Key"]} for item in contents]},
        )
        return True

    def local_path(self, key: str) -> Optional[Path]:
        return None  # remote objects have no local path; callers stream via get()


def get_object_storage(default_base_dir: Path | str | None = None) -> ObjectStorage:
    """Select the object storage backend from environment.

    REALYTICS_OBJECT_STORAGE = local (default) | s3. For s3, set
    REALYTICS_S3_BUCKET (+ optional REALYTICS_S3_PREFIX / _ENDPOINT_URL / _REGION).
    Falls back to local on any misconfiguration so the app keeps working.
    """
    backend = (os.environ.get("REALYTICS_OBJECT_STORAGE") or "local").strip().lower()
    if backend == "s3":
        bucket = os.environ.get("REALYTICS_S3_BUCKET")
        if bucket:
            return S3ObjectStorage(
                bucket,
                os.environ.get("REALYTICS_S3_PREFIX", ""),
                endpoint_url=os.environ.get("REALYTICS_S3_ENDPOINT_URL"),
                region=os.environ.get("REALYTICS_S3_REGION"),
            )
    base = default_base_dir or (Path(__file__).resolve().parents[1] / "data" / "runtime" / "uploads")
    return LocalObjectStorage(base)
