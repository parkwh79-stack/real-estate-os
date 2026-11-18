from __future__ import annotations

"""Canonical identity contracts for the global official legal corpus.

Provider revisions identify an acquisition configuration.  They must not
fork the identity of the official resource being acquired.  This module keeps
that boundary explicit and deterministic while a revision-specific crosswalk
retains the exact provider alias used for each acquisition lineage.
"""

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any


IDENTITY_SCHEMA_VERSION = "realytics-official-resource-identity-v1"
CROSS_REVISION_KEY_PREFIX = "official-resource:v1:"


@dataclass(frozen=True)
class OfficialResourceIdentity:
    """Provider-lineage-stable identity for one official legal resource."""

    provider_key: str
    resource_type: str
    canonical_official_identifier: str
    jurisdiction_code: str

    def __post_init__(self) -> None:
        for name in (
            "provider_key",
            "resource_type",
            "canonical_official_identifier",
            "jurisdiction_code",
        ):
            value = _canonical_text(getattr(self, name))
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)

    @property
    def payload(self) -> dict[str, str]:
        return {
            "schemaVersion": IDENTITY_SCHEMA_VERSION,
            # provider_key is stable across provider revisions; provider_id is
            # deliberately excluded because it includes provider_revision.
            "providerKey": self.provider_key,
            "resourceType": self.resource_type,
            "canonicalOfficialIdentifier": (
                self.canonical_official_identifier
            ),
            "jurisdictionCode": self.jurisdiction_code,
        }

    @property
    def identity_hash(self) -> str:
        return _stable_hash(self.payload)

    @property
    def cross_revision_key(self) -> str:
        return CROSS_REVISION_KEY_PREFIX + self.identity_hash

    @property
    def legal_resource_id(self) -> str:
        return _deterministic_id(
            "legal-resource",
            {"crossRevisionKey": self.cross_revision_key},
        )


def legal_resource_crosswalk_id(
    *,
    provider_id: str,
    provider_resource_type: str,
    provider_official_identifier: str,
    provider_jurisdiction_code: str,
) -> str:
    """Return the immutable alias identity for one provider revision."""

    payload = {
        "providerId": _required(provider_id, "provider_id"),
        "providerResourceType": _required(
            provider_resource_type, "provider_resource_type"
        ),
        "providerOfficialIdentifier": _required(
            provider_official_identifier,
            "provider_official_identifier",
        ),
        "providerJurisdictionCode": _required(
            provider_jurisdiction_code,
            "provider_jurisdiction_code",
        ),
    }
    return _deterministic_id("legal-resource-crosswalk", payload)


def _deterministic_id(prefix: str, value: Any) -> str:
    return f"{prefix}:{_stable_hash(value)}"


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required(value: str, name: str) -> str:
    text = _canonical_text(value)
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _canonical_text(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


__all__ = [
    "CROSS_REVISION_KEY_PREFIX",
    "IDENTITY_SCHEMA_VERSION",
    "OfficialResourceIdentity",
    "legal_resource_crosswalk_id",
]
