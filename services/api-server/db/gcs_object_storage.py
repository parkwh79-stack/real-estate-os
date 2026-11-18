"""ADC-backed Google object storage with generation-bound evidence reads."""

from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from db.object_storage import sha256_bytes


class GCSObjectStorage:
    backend = "gcs"

    def __init__(self, bucket: str, project: str, *, prefix: str = "", retention_days: int):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket):
            raise ValueError("Invalid Google storage bucket")
        if not re.fullmatch(r"(?:[a-z][a-z0-9-]{4,28}[a-z0-9]|[0-9]{6,20})", project):
            raise ValueError("Explicit Google storage project is required")
        if retention_days <= 0:
            raise ValueError("Positive Google object retention days are required")
        self.bucket = bucket
        self.project = project
        self.prefix = self._key(prefix) if prefix else ""
        self.retention_days = retention_days
        # Pilot governance only. Never create an irreversible compliance lock.
        self.retention_mode = "GOVERNANCE"
        self._client = None

    @staticmethod
    def _key(key: str) -> str:
        if not isinstance(key, str) or not key or "\\" in key or ":" in key:
            raise ValueError("Invalid object key")
        if any(part in {"", ".", ".."} for part in key.split("/")):
            raise ValueError("Invalid object key")
        if any(ord(char) < 32 or ord(char) == 127 for char in key):
            raise ValueError("Invalid object key")
        return key

    def _full_key(self, key: str) -> str:
        key = self._key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def client(self):
        if self._client is None:
            from google.cloud import storage

            self._client = storage.Client(project=self.project)
        return self._client

    def _blob(self, key: str, *, generation: str | None = None):
        name = self._full_key(key)
        if generation is not None and not re.fullmatch(r"[1-9][0-9]*", str(generation)):
            raise ValueError("Invalid object generation")
        return self.client().bucket(self.bucket).blob(
            name, generation=int(generation) if generation is not None else None
        )

    def put(self, key: str, content: bytes, *, content_type=None, metadata=None,
            retention_until: datetime | None = None, retention_mode: str | None = None) -> dict[str, Any]:
        import google_crc32c
        from google.api_core.exceptions import PreconditionFailed

        blob = self._blob(key)
        expected_metadata = {str(k): str(v) for k, v in (metadata or {}).items()}
        if retention_until is not None:
            if (retention_until.tzinfo is None or retention_until <= datetime.now(timezone.utc)
                    or retention_mode != "GOVERNANCE"):
                raise ValueError("Google pilot retention requires a future aware date and GOVERNANCE")
            # GCS normalizes retained timestamps to milliseconds. Round upward,
            # never downward, so provider precision cannot shorten preservation
            # or cause a valid write to fail its own receipt validation.
            remainder = retention_until.microsecond % 1000
            if remainder:
                retention_until += timedelta(microseconds=1000 - remainder)
            blob.retention.mode = "Unlocked"
            blob.retention.retain_until_time = retention_until
        elif retention_mode or expected_metadata.get("lifecycle-status") == "active":
            raise ValueError("Active originals require explicit retention")
        blob.metadata = expected_metadata
        expected_crc = base64.b64encode(google_crc32c.value(content).to_bytes(4, "big")).decode("ascii")
        try:
            try:
                # Never replace an existing generation or weaken its retention.
                blob.upload_from_string(
                    content, content_type=content_type or "application/octet-stream",
                    if_generation_match=0, checksum="crc32c", timeout=30, retry=None,
                )
            except PreconditionFailed:
                blob = self._blob(key)
                blob.reload(timeout=15, retry=None)
                if (blob.size != len(content) or blob.crc32c != expected_crc
                        or dict(blob.metadata or {}) != expected_metadata):
                    raise RuntimeError("Existing object differs")
                if retention_until is not None:
                    if blob.retention.mode != "Unlocked" or blob.retention.retain_until_time is None:
                        raise RuntimeError("Existing retention differs")
                    if blob.retention.retain_until_time < retention_until:
                        generation, metageneration = blob.generation, blob.metageneration
                        blob.retention.retain_until_time = retention_until
                        blob.patch(if_generation_match=generation,
                                   if_metageneration_match=metageneration, timeout=15, retry=None)
            if blob.crc32c != expected_crc or blob.size != len(content) or not blob.generation:
                raise RuntimeError("Object write receipt is incomplete")
            if retention_until is not None and (
                blob.retention.mode != "Unlocked" or blob.retention.retain_until_time is None
                or blob.retention.retain_until_time < retention_until
            ):
                raise RuntimeError("Object retention was not applied")
        except Exception:
            raise RuntimeError("gcs_object_write_failed") from None
        return {
            "backend": self.backend, "key": key, "bucket": self.bucket,
            "size": len(content), "sha256": sha256_bytes(content),
            "contentType": content_type, "metadata": dict(blob.metadata or {}),
            "versionId": str(blob.generation),
            "retentionMode": "GOVERNANCE" if blob.retention.mode == "Unlocked" else None,
            "retentionUntil": (blob.retention.retain_until_time.isoformat()
                               if blob.retention.retain_until_time else None),
        }

    def get(self, key: str) -> bytes:
        blob = self._blob(key)
        try:
            return blob.download_as_bytes(raw_download=True, checksum="crc32c", timeout=30, retry=None)
        except Exception:
            raise RuntimeError("gcs_object_read_failed") from None

    def head(self, key: str) -> dict[str, Any]:
        blob = self._blob(key)
        try:
            blob.reload(timeout=15, retry=None)
            return {
                "backend": self.backend, "key": key, "bucket": self.bucket,
                "size": blob.size, "contentType": blob.content_type,
                "metadata": {str(k).lower(): str(v) for k, v in (blob.metadata or {}).items()},
                "etag": blob.etag, "versionId": str(blob.generation) if blob.generation else None,
                "objectLockMode": {"Unlocked": "GOVERNANCE", "Locked": "COMPLIANCE"}.get(blob.retention.mode),
                "objectLockRetainUntil": blob.retention.retain_until_time,
                # GCS always encrypts stored data; distinguish its native scheme.
                "serverSideEncryption": "google-kms" if blob.kms_key_name else "google-managed",
                "sseKmsKeyId": blob.kms_key_name,
            }
        except Exception:
            raise RuntimeError("gcs_object_metadata_failed") from None

    def get_range(self, key: str, start: int, end: int, *, version_id=None, if_match=None) -> bytes:
        if start < 0 or end < start:
            raise ValueError("Invalid object byte range")
        blob = self._blob(key, generation=version_id)
        try:
            content = blob.download_as_bytes(
                start=start, end=end, raw_download=True, if_etag_match=if_match,
                if_generation_match=int(version_id) if version_id is not None else None,
                checksum="crc32c", timeout=30, retry=None,
            )
            if len(content) != end - start + 1:
                raise RuntimeError("Incomplete object range")
            return content
        except Exception:
            raise RuntimeError("gcs_object_range_failed") from None

    def exists(self, key: str) -> bool:
        blob = self._blob(key)
        try:
            return blob.exists(timeout=15, retry=None)
        except Exception:
            raise RuntimeError("gcs_object_lookup_failed") from None

    def delete(self, key: str) -> bool:
        from google.api_core.exceptions import NotFound

        blob = self._blob(key)
        try:
            blob.reload(timeout=15, retry=None)
            blob.delete(if_generation_match=blob.generation, timeout=15, retry=None)
        except NotFound:
            return False
        except Exception:
            raise RuntimeError("gcs_object_delete_failed") from None
        return True

    def delete_prefix(self, prefix: str) -> bool:
        full = self._full_key(prefix).rstrip("/") + "/"
        deleted = False
        try:
            for blob in self.client().list_blobs(self.bucket, prefix=full, versions=True,
                                                 timeout=15, retry=None):
                if not blob.name.startswith(full):
                    raise RuntimeError("Object listing crossed prefix")
                blob.delete(if_generation_match=blob.generation, timeout=15, retry=None)
                deleted = True
        except Exception:
            raise RuntimeError("gcs_object_prefix_delete_failed") from None
        return deleted

    def local_path(self, key: str) -> Path | None:
        self._full_key(key)
        return None
