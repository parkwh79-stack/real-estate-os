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
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Optional, Protocol


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class ObjectStorage(Protocol):
    """Minimal object-store contract used by the archive/export repositories."""

    backend: str

    def put(
        self,
        key: str,
        content: bytes,
        *,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        retention_until: Optional[datetime] = None,
        retention_mode: Optional[str] = None,
    ) -> Dict[str, Any]: ...
    def get(self, key: str) -> bytes: ...
    def head(self, key: str) -> Dict[str, Any]: ...
    def get_range(
        self,
        key: str,
        start: int,
        end: int,
        *,
        version_id: Optional[str] = None,
        if_match: Optional[str] = None,
    ) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete_prefix(self, prefix: str) -> bool: ...
    def delete(self, key: str) -> bool: ...
    def local_path(self, key: str) -> Optional[Path]: ...


class LocalObjectStorage:
    """Filesystem-backed object storage (default)."""

    backend = "local"

    def __init__(self, base_dir: Path | str):
        self.base_dir = Path(base_dir).resolve()

    def _resolve(self, key: str) -> Path:
        # Object operations must never address the storage root, a drive/UNC
        # path, or parent components, even if normalization lands inside root.
        if not isinstance(key, str) or not key or "\\" in key:
            raise ValueError("Invalid object key")
        if PureWindowsPath(key).drive or (os.name == "nt" and ":" in key):
            raise ValueError("Invalid object key")
        if any(part in {"", ".", ".."} for part in key.split("/")):
            raise ValueError("Invalid object key")
        # Normalize symlinks before checking the directory boundary. Include
        # the separator so e.g. objects-private cannot pass for objects.
        root = os.path.normcase(str(self.base_dir))
        normalized = os.path.normcase(os.path.realpath(os.path.join(root, key)))
        if normalized == root or not normalized.startswith(os.path.join(root, "")):
            raise ValueError("Invalid object key (path traversal)")
        return Path(normalized)

    def put(
        self,
        key: str,
        content: bytes,
        *,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        retention_until: Optional[datetime] = None,
        retention_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
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
            "metadata": dict(metadata or {}),
            "retentionUntil": retention_until.isoformat() if retention_until else None,
            "retentionMode": retention_mode,
        }

    def get(self, key: str) -> bytes:
        return self._resolve(key).read_bytes()

    @staticmethod
    def _etag(path: Path) -> str:
        stat = path.stat()
        return f'"local-{stat.st_size:x}-{stat.st_mtime_ns:x}"'

    def head(self, key: str) -> Dict[str, Any]:
        path = self._resolve(key)
        stat = path.stat()
        return {
            "backend": self.backend,
            "key": key,
            "size": stat.st_size,
            "contentType": None,
            "metadata": {},
            "etag": self._etag(path),
            "versionId": None,
        }

    def get_range(
        self,
        key: str,
        start: int,
        end: int,
        *,
        version_id: Optional[str] = None,
        if_match: Optional[str] = None,
    ) -> bytes:
        del version_id
        path = self._resolve(key)
        if start < 0 or end < start:
            raise ValueError("Invalid object byte range")
        if if_match and self._etag(path) != if_match:
            raise RuntimeError("Object changed after metadata resolution")
        size = path.stat().st_size
        if start >= size or end >= size:
            raise ValueError("Object byte range exceeds content length")
        with path.open("rb") as source:
            source.seek(start)
            content = source.read(end - start + 1)
        if len(content) != end - start + 1:
            raise RuntimeError("Object byte range response is incomplete")
        return content

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

    def delete(self, key: str) -> bool:
        target = self._resolve(key)
        if not target.exists() or not target.is_file():
            return False
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

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        *,
        endpoint_url: Optional[str] = None,
        region: Optional[str] = None,
        server_side_encryption: Optional[str] = None,
        kms_key_id: Optional[str] = None,
        retention_days: Optional[int] = None,
        retention_mode: Optional[str] = None,
    ):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._endpoint_url = endpoint_url
        self._region = region
        self._client = None
        self.server_side_encryption = server_side_encryption
        self.kms_key_id = kms_key_id
        self.retention_days = retention_days
        self.retention_mode = retention_mode

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

    def put(
        self,
        key: str,
        content: bytes,
        *,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        retention_until: Optional[datetime] = None,
        retention_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"Bucket": self.bucket, "Key": self._full_key(key), "Body": content}
        if content_type:
            kwargs["ContentType"] = content_type
        if metadata:
            kwargs["Metadata"] = {str(k): str(v) for k, v in metadata.items()}
        if self.server_side_encryption:
            kwargs["ServerSideEncryption"] = self.server_side_encryption
        if self.kms_key_id:
            kwargs["SSEKMSKeyId"] = self.kms_key_id
        effective_retention_until = retention_until
        effective_retention_mode = retention_mode or (
            self.retention_mode if effective_retention_until else None
        )
        if effective_retention_until and effective_retention_mode:
            kwargs["ObjectLockMode"] = effective_retention_mode
            kwargs["ObjectLockRetainUntilDate"] = effective_retention_until
        self.client().put_object(**kwargs)
        return {
            "backend": self.backend,
            "key": key,
            "bucket": self.bucket,
            "size": len(content),
            "sha256": sha256_bytes(content),
            "contentType": content_type,
            "metadata": dict(metadata or {}),
            "retentionUntil": (
                effective_retention_until.isoformat()
                if effective_retention_until
                else None
            ),
            "retentionMode": effective_retention_mode,
        }

    def get(self, key: str) -> bytes:
        obj = self.client().get_object(Bucket=self.bucket, Key=self._full_key(key))
        return obj["Body"].read()

    def head(self, key: str) -> Dict[str, Any]:
        response = self.client().head_object(
            Bucket=self.bucket,
            Key=self._full_key(key),
        )
        return {
            "backend": self.backend,
            "key": key,
            "bucket": self.bucket,
            "size": int(response.get("ContentLength") or 0),
            "contentType": response.get("ContentType"),
            "metadata": {
                str(name).lower(): str(value)
                for name, value in dict(response.get("Metadata") or {}).items()
            },
            "etag": response.get("ETag"),
            "versionId": response.get("VersionId"),
            "objectLockMode": response.get("ObjectLockMode"),
            "objectLockRetainUntil": response.get("ObjectLockRetainUntilDate"),
            "serverSideEncryption": response.get("ServerSideEncryption"),
            "sseKmsKeyId": response.get("SSEKMSKeyId"),
        }

    def get_range(
        self,
        key: str,
        start: int,
        end: int,
        *,
        version_id: Optional[str] = None,
        if_match: Optional[str] = None,
    ) -> bytes:
        if start < 0 or end < start:
            raise ValueError("Invalid object byte range")
        kwargs: Dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": self._full_key(key),
            "Range": f"bytes={start}-{end}",
        }
        if version_id:
            kwargs["VersionId"] = version_id
        if if_match:
            kwargs["IfMatch"] = if_match
        response = self.client().get_object(**kwargs)
        content_range = str(response.get("ContentRange") or "")
        if not content_range.startswith(f"bytes {start}-{end}/"):
            raise RuntimeError("Object byte range response is not bound to the requested interval")
        content = response["Body"].read()
        if len(content) != end - start + 1:
            raise RuntimeError("Object byte range response is incomplete")
        return content

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

    def delete(self, key: str) -> bool:
        if not self.exists(key):
            return False
        self.client().delete_object(
            Bucket=self.bucket,
            Key=self._full_key(key),
        )
        return True

    def local_path(self, key: str) -> Optional[Path]:
        return None  # remote objects have no local path; callers stream via get()


def get_object_storage(default_base_dir: Path | str | None = None) -> ObjectStorage:
    """Select the object storage backend from environment.

    REALYTICS_OBJECT_STORAGE = local (default) | s3. For s3, set
    REALYTICS_S3_BUCKET (+ optional REALYTICS_S3_PREFIX / _ENDPOINT_URL / _REGION).
    Local storage is a development default. Selecting S3 without a bucket is a
    configuration error; silently falling back would split the document source
    of truth across worker hosts.
    """
    backend = (os.environ.get("REALYTICS_OBJECT_STORAGE") or "local").strip().lower()
    if backend == "s3":
        bucket = os.environ.get("REALYTICS_S3_BUCKET")
        if not bucket:
            raise RuntimeError("REALYTICS_S3_BUCKET is required when REALYTICS_OBJECT_STORAGE=s3")
        hardened = (os.environ.get("REALYTICS_ENVIRONMENT") or "development").strip().casefold() in {
            "production", "prod", "staging"
        }
        encryption = (os.environ.get("REALYTICS_S3_SERVER_SIDE_ENCRYPTION") or "").strip() or None
        kms_key_id = (os.environ.get("REALYTICS_S3_KMS_KEY_ID") or "").strip() or None
        retention_days_raw = (os.environ.get("REALYTICS_OBJECT_RETENTION_DAYS") or "").strip()
        retention_mode = (os.environ.get("REALYTICS_OBJECT_LOCK_MODE") or "").strip().upper() or None
        if encryption not in {None, "AES256", "aws:kms"}:
            raise RuntimeError("REALYTICS_S3_SERVER_SIDE_ENCRYPTION must be AES256 or aws:kms")
        if encryption == "aws:kms" and not kms_key_id:
            raise RuntimeError("REALYTICS_S3_KMS_KEY_ID is required for aws:kms")
        if retention_mode not in {None, "GOVERNANCE", "COMPLIANCE"}:
            raise RuntimeError("REALYTICS_OBJECT_LOCK_MODE must be GOVERNANCE or COMPLIANCE")
        try:
            retention_days = int(retention_days_raw) if retention_days_raw else None
        except ValueError as exc:
            raise RuntimeError("REALYTICS_OBJECT_RETENTION_DAYS must be a positive integer") from exc
        if retention_days is not None and retention_days <= 0:
            raise RuntimeError("REALYTICS_OBJECT_RETENTION_DAYS must be a positive integer")
        if bool(retention_days) != bool(retention_mode):
            raise RuntimeError("Object retention days and lock mode must be configured together")
        if hardened and (not encryption or not retention_days or not retention_mode):
            raise RuntimeError(
                "Hardened S3 requires server-side encryption and Object Lock retention configuration"
            )
        return S3ObjectStorage(
            bucket,
            os.environ.get("REALYTICS_S3_PREFIX", ""),
            endpoint_url=os.environ.get("REALYTICS_S3_ENDPOINT_URL"),
            region=os.environ.get("REALYTICS_S3_REGION"),
            server_side_encryption=encryption,
            kms_key_id=kms_key_id,
            retention_days=retention_days,
            retention_mode=retention_mode,
        )
    if backend != "local":
        raise RuntimeError(f"Unsupported REALYTICS_OBJECT_STORAGE backend: {backend}")
    from core.runtime_paths import runtime_data_dir

    base = default_base_dir or (runtime_data_dir() / "uploads")
    return LocalObjectStorage(base)
