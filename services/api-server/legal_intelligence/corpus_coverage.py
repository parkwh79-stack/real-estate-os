"""Code-owned identity for one authoritative official-corpus coverage lane."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any, Mapping


COVERAGE_IDENTITY_SCHEMA_VERSION = "legal-corpus-coverage-v2"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_KIND = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_SAFE_TARGET = re.compile(r"^[A-Za-z0-9]{1,80}$")
_SAFE_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{2,299}$")


def _coverage_digest(parts: tuple[str, ...]) -> str:
    encoded = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _framed(value: str) -> str:
    return f"{len(value.encode('utf-8'))}:{value}"


@dataclass(frozen=True)
class OfficialCorpusCoverageIdentity:
    """Immutable target/query/adapter contract carried by every certified page."""

    provider_key: str
    provider_revision: int
    registry_version: str
    resource_kind: str
    target: str
    query_sha256: str
    source_adapter_version: str
    boundary_contract_version: str
    coverage_contract_version: str

    def __post_init__(self) -> None:
        provider_key = str(self.provider_key or "").strip()
        resource_kind = str(self.resource_kind or "").strip()
        registry_version = str(self.registry_version or "").strip()
        target = str(self.target or "").strip()
        query_sha256 = str(self.query_sha256 or "").strip()
        source_adapter_version = str(
            self.source_adapter_version or ""
        ).strip()
        boundary_contract_version = str(
            self.boundary_contract_version or ""
        ).strip()
        coverage_contract_version = str(
            self.coverage_contract_version or ""
        ).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,79}", provider_key):
            raise ValueError("provider_key is not a safe provider identity")
        if self.provider_revision < 1:
            raise ValueError("provider_revision must be positive")
        if not _SAFE_KIND.fullmatch(resource_kind):
            raise ValueError("resource_kind is not a safe canonical kind")
        if not _SAFE_TARGET.fullmatch(target):
            raise ValueError("target is not a safe official target")
        if not _SHA256.fullmatch(query_sha256):
            raise ValueError("query_sha256 must be a lowercase SHA-256")
        for name, value in (
            ("registry_version", registry_version),
            ("source_adapter_version", source_adapter_version),
            ("boundary_contract_version", boundary_contract_version),
            ("coverage_contract_version", coverage_contract_version),
        ):
            if not _SAFE_VERSION.fullmatch(value):
                raise ValueError(f"{name} must be a versioned safe identifier")
        object.__setattr__(self, "provider_key", provider_key)
        object.__setattr__(self, "registry_version", registry_version)
        object.__setattr__(self, "resource_kind", resource_kind)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "query_sha256", query_sha256)
        object.__setattr__(
            self,
            "source_adapter_version",
            source_adapter_version,
        )
        object.__setattr__(
            self,
            "boundary_contract_version",
            boundary_contract_version,
        )
        object.__setattr__(
            self,
            "coverage_contract_version",
            coverage_contract_version,
        )

    @property
    def coverage_identity_sha256(self) -> str:
        return _coverage_digest(
            (
                COVERAGE_IDENTITY_SCHEMA_VERSION,
                self.provider_id,
                self.registry_version,
                self.resource_kind,
                self.target,
                self.query_sha256,
                self.source_adapter_version,
                self.boundary_contract_version,
                self.coverage_contract_version,
            )
        )

    @property
    def coverage_registry_id(self) -> str:
        digest = _coverage_digest(
            (
                "legal-corpus-coverage-registry-v1",
                self.provider_key,
                str(self.provider_revision),
                self.registry_version,
                self.coverage_contract_version,
            )
        )
        return f"legal-corpus-coverage-registry:{digest}"

    @property
    def coverage_contract_id(self) -> str:
        return (
            "legal-corpus-coverage-contract:"
            + self.coverage_identity_sha256
        )

    @property
    def provider_id(self) -> str:
        payload = (
            '{"providerKey":"'
            + self.provider_key
            + '","providerRevision":'
            + str(self.provider_revision)
            + "}"
        )
        return (
            "legal-provider:"
            + hashlib.sha256(payload.encode("utf-8")).hexdigest()
        )

    def assert_provider(
        self,
        *,
        provider_key: str,
        provider_revision: int,
        provider_id: str | None = None,
    ) -> None:
        if (
            self.provider_key != provider_key
            or self.provider_revision != provider_revision
            or (
                provider_id is not None
                and not hmac.compare_digest(self.provider_id, provider_id)
            )
        ):
            raise ValueError(
                "coverage identity does not match the source provider"
            )

    def scope_hash(self, *, requested_scope: str) -> str:
        scope = str(requested_scope or "").strip()
        if not scope:
            raise ValueError("requested_scope is required")
        return _coverage_digest(
            (
                "legal-corpus-scope-v1",
                _framed(self.provider_id),
                _framed(self.coverage_identity_sha256),
                _framed(scope),
            )
        )

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "schemaVersion": COVERAGE_IDENTITY_SCHEMA_VERSION,
            "providerKey": self.provider_key,
            "providerRevision": self.provider_revision,
            "providerId": self.provider_id,
            "registryVersion": self.registry_version,
            "coverageRegistryId": self.coverage_registry_id,
            "coverageContractId": self.coverage_contract_id,
            "resourceKind": self.resource_kind,
            "target": self.target,
            "querySha256": self.query_sha256,
            "sourceAdapterVersion": self.source_adapter_version,
            "boundaryContractVersion": self.boundary_contract_version,
            "coverageContractVersion": self.coverage_contract_version,
            "coverageIdentitySha256": self.coverage_identity_sha256,
        }


__all__ = [
    "COVERAGE_IDENTITY_SCHEMA_VERSION",
    "OfficialCorpusCoverageIdentity",
]
