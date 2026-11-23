"""Immutable S3/MinIO storage adapter for official legal source payloads.

This adapter is intentionally separate from the ordinary project-document
storage abstraction.  An authoritative legal source payload requires bucket
versioning, Object Lock, content-addressed keys, immutable version receipts,
and inventory/retention operations for orphan reconciliation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from use_cases.legal_corpus_reconciliation import (
    RawObjectIdentity,
    RawObjectInventoryConflict,
    RawObjectInventoryItem,
    RawObjectInventoryPage,
    RetentionHoldReceipt,
)
from use_cases.legal_corpus_sync import RawObjectReceipt


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_METADATA_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SENSITIVE_METADATA_FRAGMENTS = frozenset(
    {
        "authorization",
        "password",
        "secret",
        "token",
        "api-key",
        "apikey",
        "service-key",
        "servicekey",
    }
)
_LEGAL_RAW_PREFIX = "legal-corpus/raw/"


class LegalRawObjectStorageError(RuntimeError):
    """Base immutable legal-object storage error."""


class LegalRawObjectStorageConfigurationError(
    LegalRawObjectStorageError
):
    """The configured bucket cannot prove required immutability controls."""


class LegalRawObjectImmutableConflict(LegalRawObjectStorageError):
    """A content-addressed key is already bound to different bytes."""


class S3ImmutableLegalRawObjectStore:
    """Versioned, Object-Locked S3/MinIO adapter.

    ``client`` is injectable so contract tests do not need boto3 or network
    access.  Production construction lazy-loads boto3.
    """

    def __init__(
        self,
        *,
        bucket: str,
        storage_prefix: str = "",
        endpoint_url: str | None = None,
        region: str | None = None,
        lock_mode: str = "COMPLIANCE",
        minimum_retention_days: int = 365,
        client: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.bucket = _required(bucket, "bucket")
        self.storage_prefix = _validated_storage_prefix(storage_prefix)
        self.endpoint_url = endpoint_url
        self.region = region
        self.lock_mode = str(lock_mode or "").strip().upper()
        if self.lock_mode not in {"COMPLIANCE", "GOVERNANCE"}:
            raise ValueError(
                "lock_mode must be COMPLIANCE or GOVERNANCE"
            )
        if minimum_retention_days < 1:
            raise ValueError("minimum_retention_days must be positive")
        self.minimum_retention = timedelta(
            days=minimum_retention_days
        )
        self._client = client
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._configuration_verified = False

    def client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - deployment only
                raise LegalRawObjectStorageConfigurationError(
                    "boto3 is required for immutable legal S3 storage"
                ) from exc
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                region_name=self.region,
            )
        return self._client

    def verify_configuration(self) -> None:
        versioning = self.client().get_bucket_versioning(
            Bucket=self.bucket
        )
        if str(versioning.get("Status") or "") != "Enabled":
            raise LegalRawObjectStorageConfigurationError(
                "legal raw-object bucket versioning must be Enabled"
            )
        try:
            lock_configuration = (
                self.client().get_object_lock_configuration(
                    Bucket=self.bucket
                )
            )
        except Exception as exc:
            raise LegalRawObjectStorageConfigurationError(
                "legal raw-object bucket must have Object Lock enabled"
            ) from exc
        if (
            lock_configuration.get("ObjectLockConfiguration", {}).get(
                "ObjectLockEnabled"
            )
            != "Enabled"
        ):
            raise LegalRawObjectStorageConfigurationError(
                "legal raw-object bucket must have Object Lock enabled"
            )
        self._configuration_verified = True

    def put_if_absent(
        self,
        *,
        object_key: str,
        content: bytes,
        content_type: str,
        expected_sha256: str,
        metadata: Mapping[str, str],
    ) -> RawObjectReceipt:
        self._ensure_configuration()
        _validate_object_key(object_key)
        _validate_sha256(expected_sha256)
        if object_key.rsplit("/", 1)[-1] != expected_sha256:
            raise ValueError(
                "legal raw object key must end with expected_sha256"
            )
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != expected_sha256:
            raise LegalRawObjectImmutableConflict(
                "raw content does not match expected_sha256"
            )
        safe_metadata = _validated_metadata(metadata)
        safe_metadata["sha256"] = expected_sha256
        full_key = self._full_key(object_key)
        existing = self._head_if_present(full_key)
        if existing is not None:
            return self._receipt_from_head(
                object_key,
                existing,
                expected_sha256=expected_sha256,
                expected_size=len(content),
            )

        retain_until = _aware(self._clock(), "clock") + self.minimum_retention
        try:
            response = self.client().put_object(
                Bucket=self.bucket,
                Key=full_key,
                Body=content,
                ContentType=_required(content_type, "content_type"),
                Metadata=safe_metadata,
                ChecksumSHA256=base64.b64encode(
                    bytes.fromhex(expected_sha256)
                ).decode("ascii"),
                ObjectLockMode=self.lock_mode,
                ObjectLockRetainUntilDate=retain_until,
                IfNoneMatch="*",
            )
        except Exception as exc:
            if not _is_precondition_failure(exc):
                raise
            existing = self._head_if_present(full_key)
            if existing is None:
                raise LegalRawObjectStorageError(
                    "conditional put lost a race but no object is readable"
                ) from exc
            return self._receipt_from_head(
                object_key,
                existing,
                expected_sha256=expected_sha256,
                expected_size=len(content),
            )

        version_id = str(response.get("VersionId") or "").strip()
        if not version_id:
            raise LegalRawObjectStorageConfigurationError(
                "object put did not return a version id"
            )
        head = self.client().head_object(
            Bucket=self.bucket,
            Key=full_key,
            VersionId=version_id,
        )
        return self._receipt_from_head(
            object_key,
            head,
            expected_sha256=expected_sha256,
            expected_size=len(content),
            expected_version_id=version_id,
        )

    def list_inventory(
        self,
        *,
        prefix: str,
        cursor: str | None,
        limit: int,
    ) -> RawObjectInventoryPage:
        self._ensure_configuration()
        _validate_object_prefix(prefix)
        if not 1 <= limit <= 5_000:
            raise ValueError("limit must be between 1 and 5000")
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Prefix": self._full_key(prefix),
            "MaxKeys": limit,
        }
        if cursor is not None:
            decoded = _decode_cursor(cursor, expected_prefix=prefix)
            kwargs["KeyMarker"] = decoded["keyMarker"]
            if decoded["versionIdMarker"]:
                kwargs["VersionIdMarker"] = decoded["versionIdMarker"]

        response = self.client().list_object_versions(**kwargs)
        delete_markers = response.get("DeleteMarkers") or []
        if delete_markers:
            raise RawObjectInventoryConflict(
                "delete markers exist inside immutable legal raw storage"
            )

        items: list[RawObjectInventoryItem] = []
        for version in response.get("Versions") or []:
            full_key = _required(version.get("Key"), "inventory key")
            object_key = self._relative_key(full_key)
            _validate_object_key(object_key)
            version_id = _required(
                version.get("VersionId"), "inventory version id"
            )
            if version_id.lower() == "null":
                raise LegalRawObjectStorageConfigurationError(
                    "inventory contains an unversioned legal raw object"
                )
            head = self.client().head_object(
                Bucket=self.bucket,
                Key=full_key,
                VersionId=version_id,
            )
            metadata = {
                str(key): str(value)
                for key, value in (head.get("Metadata") or {}).items()
            }
            sha256 = str(metadata.get("sha256") or "").lower()
            _validate_sha256(sha256)
            if object_key.rsplit("/", 1)[-1] != sha256:
                raise RawObjectInventoryConflict(
                    "inventory key is not bound to its SHA-256 metadata"
                )
            created_at = version.get("LastModified")
            if not isinstance(created_at, datetime):
                raise RawObjectInventoryConflict(
                    "inventory item has no timezone-aware LastModified"
                )
            retention_until = head.get("ObjectLockRetainUntilDate")
            if retention_until is not None and not isinstance(
                retention_until, datetime
            ):
                raise RawObjectInventoryConflict(
                    "object retention timestamp is invalid"
                )
            items.append(
                RawObjectInventoryItem(
                    identity=RawObjectIdentity(
                        object_key=object_key,
                        object_version=version_id,
                    ),
                    sha256=sha256,
                    size_bytes=int(
                        head.get("ContentLength", version.get("Size", -1))
                    ),
                    created_at=_aware(created_at, "LastModified"),
                    retention_until=(
                        _aware(retention_until, "retention timestamp")
                        if retention_until is not None
                        else None
                    ),
                    metadata=metadata,
                )
            )

        truncated = bool(response.get("IsTruncated"))
        next_cursor = None
        if truncated:
            next_key = _required(
                response.get("NextKeyMarker"), "NextKeyMarker"
            )
            next_cursor = _encode_cursor(
                prefix=prefix,
                key_marker=next_key,
                version_id_marker=str(
                    response.get("NextVersionIdMarker") or ""
                ),
            )
        return RawObjectInventoryPage(
            items=tuple(items),
            next_cursor=next_cursor,
            inventory_complete=not truncated,
        )

    def ensure_retention(
        self,
        *,
        identity: RawObjectIdentity,
        retain_until: datetime,
        reason: str,
        metadata: Mapping[str, str],
    ) -> RetentionHoldReceipt:
        self._ensure_configuration()
        _required(reason, "reason")
        _validated_metadata(metadata)
        requested_until = _aware(retain_until, "retain_until")
        full_key = self._full_key(identity.object_key)
        current = self.client().get_object_retention(
            Bucket=self.bucket,
            Key=full_key,
            VersionId=identity.object_version,
        ).get("Retention") or {}
        current_until = current.get("RetainUntilDate")
        current_mode = str(current.get("Mode") or "").upper()
        if (
            isinstance(current_until, datetime)
            and _aware(current_until, "current retention")
            >= requested_until
            and current_mode in {"COMPLIANCE", "GOVERNANCE"}
        ):
            return RetentionHoldReceipt(
                identity=identity,
                retained_until=current_until,
                immutable=True,
            )

        self.client().put_object_retention(
            Bucket=self.bucket,
            Key=full_key,
            VersionId=identity.object_version,
            Retention={
                "Mode": self.lock_mode,
                "RetainUntilDate": requested_until,
            },
            BypassGovernanceRetention=False,
        )
        verified = self.client().get_object_retention(
            Bucket=self.bucket,
            Key=full_key,
            VersionId=identity.object_version,
        ).get("Retention") or {}
        verified_until = verified.get("RetainUntilDate")
        verified_mode = str(verified.get("Mode") or "").upper()
        if (
            not isinstance(verified_until, datetime)
            or _aware(verified_until, "verified retention")
            < requested_until
            or verified_mode not in {"COMPLIANCE", "GOVERNANCE"}
        ):
            raise LegalRawObjectStorageConfigurationError(
                "object store did not persist the requested retention"
            )
        return RetentionHoldReceipt(
            identity=identity,
            retained_until=verified_until,
            immutable=True,
        )

    def _ensure_configuration(self) -> None:
        if not self._configuration_verified:
            self.verify_configuration()

    def _full_key(self, object_key: str) -> str:
        return (
            f"{self.storage_prefix}/{object_key}"
            if self.storage_prefix
            else object_key
        )

    def _relative_key(self, full_key: str) -> str:
        if not self.storage_prefix:
            return full_key
        expected = f"{self.storage_prefix}/"
        if not full_key.startswith(expected):
            raise RawObjectInventoryConflict(
                "inventory key is outside configured storage prefix"
            )
        return full_key[len(expected) :]

    def _head_if_present(self, full_key: str) -> Mapping[str, Any] | None:
        try:
            return self.client().head_object(
                Bucket=self.bucket,
                Key=full_key,
            )
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise

    def _receipt_from_head(
        self,
        object_key: str,
        head: Mapping[str, Any],
        *,
        expected_sha256: str,
        expected_size: int,
        expected_version_id: str | None = None,
    ) -> RawObjectReceipt:
        version_id = str(head.get("VersionId") or "").strip()
        metadata_sha = str(
            (head.get("Metadata") or {}).get("sha256") or ""
        ).lower()
        size = int(head.get("ContentLength", -1))
        if not version_id or version_id.lower() == "null":
            raise LegalRawObjectStorageConfigurationError(
                "immutable object head has no version id"
            )
        if expected_version_id and version_id != expected_version_id:
            raise LegalRawObjectImmutableConflict(
                "object head returned a different version id"
            )
        if metadata_sha != expected_sha256 or size != expected_size:
            raise LegalRawObjectImmutableConflict(
                "content-addressed object key is bound to different content"
            )
        retention_until = head.get("ObjectLockRetainUntilDate")
        lock_mode = str(head.get("ObjectLockMode") or "").upper()
        if (
            not isinstance(retention_until, datetime)
            or _aware(retention_until, "object retention")
            <= _aware(self._clock(), "clock")
            or lock_mode not in {"COMPLIANCE", "GOVERNANCE"}
        ):
            raise LegalRawObjectStorageConfigurationError(
                "raw object version is not protected by a current Object Lock"
            )
        return RawObjectReceipt(
            object_key=object_key,
            object_version=version_id,
            sha256=metadata_sha,
            size_bytes=size,
        )


def get_immutable_legal_raw_object_store(
    *,
    client: Any | None = None,
) -> S3ImmutableLegalRawObjectStore:
    """Build the production adapter without a permissive local fallback."""

    backend = (
        os.environ.get("REALYTICS_LEGAL_RAW_OBJECT_STORAGE") or ""
    ).strip().lower()
    if backend != "s3":
        raise LegalRawObjectStorageConfigurationError(
            "REALYTICS_LEGAL_RAW_OBJECT_STORAGE must be s3"
        )
    bucket = (
        os.environ.get("REALYTICS_LEGAL_RAW_BUCKET")
        or os.environ.get("REALYTICS_S3_BUCKET")
        or ""
    )
    if not bucket.strip():
        raise LegalRawObjectStorageConfigurationError(
            "REALYTICS_LEGAL_RAW_BUCKET is required"
        )
    retention_text = (
        os.environ.get("REALYTICS_LEGAL_RAW_RETENTION_DAYS") or "365"
    )
    try:
        retention_days = int(retention_text)
    except ValueError as exc:
        raise LegalRawObjectStorageConfigurationError(
            "REALYTICS_LEGAL_RAW_RETENTION_DAYS must be an integer"
        ) from exc
    return S3ImmutableLegalRawObjectStore(
        bucket=bucket,
        storage_prefix=os.environ.get(
            "REALYTICS_LEGAL_RAW_PREFIX", "realytics"
        ),
        endpoint_url=os.environ.get("REALYTICS_S3_ENDPOINT_URL"),
        region=os.environ.get("REALYTICS_S3_REGION"),
        lock_mode=os.environ.get(
            "REALYTICS_LEGAL_RAW_LOCK_MODE", "COMPLIANCE"
        ),
        minimum_retention_days=retention_days,
        client=client,
    )


def _validated_storage_prefix(value: str) -> str:
    normalized = str(value or "").strip().strip("/")
    if (
        ".." in normalized.split("/")
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError("storage_prefix contains an unsafe path component")
    return normalized


def _validate_object_key(value: str) -> None:
    _required(value, "object_key")
    if (
        not value.startswith(_LEGAL_RAW_PREFIX)
        or ".." in value.split("/")
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(
            "object_key must stay within legal-corpus/raw/"
        )


def _validate_object_prefix(value: str) -> None:
    _required(value, "prefix")
    if (
        not value.startswith(_LEGAL_RAW_PREFIX)
        or ".." in value.split("/")
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("prefix must stay within legal-corpus/raw/")


def _validated_metadata(
    value: Mapping[str, Any],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip().lower()
        metadata_value = str(raw_value or "").strip()
        if not _METADATA_KEY_PATTERN.fullmatch(key):
            raise ValueError("object metadata key is not safe")
        if any(fragment in key for fragment in _SENSITIVE_METADATA_FRAGMENTS):
            raise ValueError("secret-bearing object metadata is forbidden")
        if not metadata_value or len(metadata_value) > 1024:
            raise ValueError("object metadata value is invalid")
        if any(
            ord(character) < 32 or ord(character) > 126
            for character in metadata_value
        ):
            raise ValueError(
                "object metadata must contain printable ASCII only"
            )
        result[key] = metadata_value
    return result


def _validate_sha256(value: str) -> None:
    if not _SHA256_PATTERN.fullmatch(str(value or "")):
        raise ValueError("expected_sha256 must be a lowercase SHA-256 digest")


def _encode_cursor(
    *,
    prefix: str,
    key_marker: str,
    version_id_marker: str,
) -> str:
    payload = json.dumps(
        {
            "prefix": prefix,
            "keyMarker": key_marker,
            "versionIdMarker": version_id_marker,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(
    value: str,
    *,
    expected_prefix: str,
) -> Mapping[str, str]:
    normalized = _required(value, "cursor")
    if len(normalized) > 8192:
        raise ValueError("cursor is too long")
    try:
        padding = "=" * (-len(normalized) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(
                (normalized + padding).encode("ascii")
            ).decode("utf-8")
        )
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("prefix") != expected_prefix
    ):
        raise ValueError("cursor does not belong to the requested prefix")
    key_marker = _required(payload.get("keyMarker"), "cursor key marker")
    version_marker = str(payload.get("versionIdMarker") or "")
    return {
        "keyMarker": key_marker,
        "versionIdMarker": version_marker,
    }


def _is_not_found(exc: Exception) -> bool:
    return _error_code(exc) in {
        "404",
        "NoSuchKey",
        "NotFound",
        "NoSuchVersion",
    }


def _is_precondition_failure(exc: Exception) -> bool:
    return _error_code(exc) in {
        "409",
        "412",
        "ConditionalRequestConflict",
        "PreconditionFailed",
    }


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error")
        if isinstance(error, Mapping):
            return str(error.get("Code") or "")
        metadata = response.get("ResponseMetadata")
        if isinstance(metadata, Mapping):
            return str(metadata.get("HTTPStatusCode") or "")
    return ""


def _required(value: Any, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


__all__ = [
    "LegalRawObjectImmutableConflict",
    "LegalRawObjectStorageConfigurationError",
    "LegalRawObjectStorageError",
    "S3ImmutableLegalRawObjectStore",
    "get_immutable_legal_raw_object_store",
]
