"""Privacy-safe contracts for legal-provider dead-letter replay.

Raw provider queries and payloads never cross the public API boundary.  A
trusted object worker first registers an immutable object-store receipt.  Human
approvers can then reference only the server-issued catalog id; they cannot
assert an object locator, version, digest, or retention period themselves.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")
_SAFE_OBJECT_SCHEMES = frozenset({"minio", "object", "quarantine", "s3"})


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class ProviderReplayModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class ProviderReplayStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    REQUESTED = "requested"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ProviderReplayObjectLockMode(str, Enum):
    GOVERNANCE = "governance"
    COMPLIANCE = "compliance"


class LegalProviderReplayObjectReceipt(ProviderReplayModel):
    """Trusted worker input for one immutable replay-object catalog row.

    This contract is intentionally not mounted on an HTTP router.  It is used
    only behind the dedicated legal-operations database boundary after the
    object store has returned a version, digest, lock mode, and retention
    receipt.
    """

    tenant_id: str = Field(min_length=1, max_length=255)
    project_id: str = Field(min_length=1, max_length=255)
    dead_letter_id: str = Field(min_length=1, max_length=255)
    provider_id: str = Field(min_length=1, max_length=120)
    object_locator: str = Field(min_length=8, max_length=1_024)
    object_version_id: str = Field(min_length=1, max_length=512)
    object_sha256: str
    object_lock_mode: ProviderReplayObjectLockMode
    retention_until: datetime
    object_store_receipt_sha256: str
    registered_by: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator(
        "tenant_id",
        "project_id",
        "dead_letter_id",
        "provider_id",
        "registered_by",
    )
    @classmethod
    def _validate_reference(cls, value: str) -> str:
        if not _SAFE_REFERENCE.fullmatch(value):
            raise ValueError(
                "replay object receipt references must be privacy-safe "
                "opaque identifiers"
            )
        if value.casefold() in {"anonymous", "system", "unknown"}:
            raise ValueError("registeredBy must identify a trusted worker")
        return value

    @field_validator("object_version_id")
    @classmethod
    def _validate_object_version_id(cls, value: str) -> str:
        if any(ord(char) < 32 or char.isspace() for char in value):
            raise ValueError(
                "objectVersionId must be a printable opaque identifier "
                "without whitespace"
            )
        return value

    @field_validator("object_locator")
    @classmethod
    def _validate_object_locator(cls, value: str) -> str:
        return _validated_object_locator(value, field_name="objectLocator")

    @field_validator("object_sha256", "object_store_receipt_sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        return _validated_sha256(value)

    @field_validator("idempotency_key")
    @classmethod
    def _validate_idempotency_key(cls, value: str) -> str:
        return _validated_idempotency_key(value)

    @field_validator("retention_until")
    @classmethod
    def _validate_retention_is_aware(cls, value: datetime) -> datetime:
        return _validated_aware_datetime(value, field_name="retentionUntil")


class LegalProviderReplayApprovalInput(ProviderReplayModel):
    """Create a short-lived approval for a registered immutable object."""

    project_id: str = Field(min_length=1, max_length=255)
    replay_object_id: str = Field(min_length=1, max_length=256)
    replay_requester_id: str = Field(min_length=1, max_length=255)
    expires_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=128)
    supersedes_approval_event_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )

    @field_validator(
        "project_id",
        "replay_object_id",
        "replay_requester_id",
        "supersedes_approval_event_id",
    )
    @classmethod
    def _validate_reference(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        if not _SAFE_REFERENCE.fullmatch(value):
            raise ValueError(
                "project, requester, and approval references must be "
                "privacy-safe opaque identifiers"
            )
        if value.casefold() in {"anonymous", "system", "unknown"}:
            raise ValueError(
                "replayRequesterId must identify a named human operator"
            )
        return value

    @field_validator("idempotency_key")
    @classmethod
    def _validate_idempotency_key(cls, value: str) -> str:
        return _validated_idempotency_key(value)

    @field_validator("expires_at")
    @classmethod
    def _validate_expiry_is_aware(cls, value: datetime) -> datetime:
        return _validated_aware_datetime(value, field_name="expiresAt")


class LegalProviderReplayRequestInput(ProviderReplayModel):
    """Consume one server-issued immutable replay approval event."""

    approval_event_id: str = Field(min_length=1, max_length=256)

    @field_validator("approval_event_id")
    @classmethod
    def _validate_approval_event_id(cls, value: str) -> str:
        if not _SAFE_REFERENCE.fullmatch(value):
            raise ValueError(
                "approvalEventId must be a privacy-safe opaque identifier"
            )
        return value


def _validated_object_locator(value: str, *, field_name: str) -> str:
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field_name} contains a control character")
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in _SAFE_OBJECT_SCHEMES:
        raise ValueError(f"{field_name} must use an approved stable object scheme")
    if (
        not parsed.netloc
        or not parsed.path
        or parsed.path == "/"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"{field_name} must identify one object and must not contain "
            "credentials, query parameters, or a fragment"
        )
    return value


def _validated_sha256(value: str) -> str:
    normalized = value.casefold()
    if not _SHA256.fullmatch(normalized):
        raise ValueError("digest must be sha256: followed by 64 hex characters")
    return normalized


def _validated_idempotency_key(value: str) -> str:
    if not _IDEMPOTENCY_KEY.fullmatch(value):
        raise ValueError(
            "idempotencyKey may contain only letters, digits, colon, dot, "
            "underscore, and hyphen"
        )
    return value


def _validated_aware_datetime(
    value: datetime,
    *,
    field_name: str,
) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value


__all__ = [
    "LegalProviderReplayApprovalInput",
    "LegalProviderReplayObjectReceipt",
    "LegalProviderReplayRequestInput",
    "ProviderReplayModel",
    "ProviderReplayObjectLockMode",
    "ProviderReplayStatus",
]
