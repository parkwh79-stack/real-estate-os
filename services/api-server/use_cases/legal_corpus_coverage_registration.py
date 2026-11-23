"""Deployment-only registration contract for official-corpus coverage sets.

The serving API and legal-sync worker only consume registered contracts.  This
module builds and validates the immutable eight-target manifest that a
deployment-role repository may persist; it has no runtime composition path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from legal_intelligence.corpus_coverage import (
    COVERAGE_IDENTITY_SCHEMA_VERSION,
    OfficialCorpusCoverageIdentity,
)
from legal_intelligence.law_go_kr_contracts import (
    LAW_GO_KR_BOUNDARY_CONTRACTS,
)


COVERAGE_REGISTRATION_MANIFEST_VERSION = (
    "legal-corpus-coverage-registration-v1"
)
REQUIRED_COVERAGE_CONTRACT_COUNT = 8
MAX_COVERAGE_MANIFEST_BYTES = 256 * 1024

_IDENTITY_REQUIRED_KEYS = frozenset(
    {
        "providerKey",
        "providerRevision",
        "registryVersion",
        "resourceKind",
        "target",
        "querySha256",
        "sourceAdapterVersion",
        "boundaryContractVersion",
        "coverageContractVersion",
    }
)
_IDENTITY_DERIVED_KEYS = frozenset(
    {
        "schemaVersion",
        "providerId",
        "coverageRegistryId",
        "coverageContractId",
        "coverageIdentitySha256",
    }
)


class LegalCorpusCoverageRegistrationError(ValueError):
    """A deployment manifest cannot become an authoritative contract set."""


@dataclass(frozen=True)
class LegalCorpusCoverageContractSetPlan:
    """One complete, deterministic registry insertion plan."""

    identities: tuple[OfficialCorpusCoverageIdentity, ...]
    registered_by: str
    expected_contracts_sha256: str

    @property
    def first(self) -> OfficialCorpusCoverageIdentity:
        return self.identities[0]

    @property
    def coverage_registry_id(self) -> str:
        return self.first.coverage_registry_id

    @property
    def provider_id(self) -> str:
        return self.first.provider_id

    @property
    def provider_key(self) -> str:
        return self.first.provider_key

    @property
    def provider_revision(self) -> int:
        return self.first.provider_revision

    @property
    def registry_version(self) -> str:
        return self.first.registry_version

    @property
    def coverage_contract_version(self) -> str:
        return self.first.coverage_contract_version

    @property
    def source_adapter_version(self) -> str:
        return self.first.source_adapter_version

    @property
    def required_targets(self) -> Mapping[str, str]:
        return {
            identity.resource_kind: identity.target
            for identity in self.identities
        }

    @property
    def metadata(self) -> Mapping[str, object]:
        return {
            "registrationPath": "deployment-migration-role-v1",
            "manifestVersion": COVERAGE_REGISTRATION_MANIFEST_VERSION,
            "expectedContractsSha256": self.expected_contracts_sha256,
        }

    def as_dict(self) -> Mapping[str, object]:
        return {
            "manifestVersion": COVERAGE_REGISTRATION_MANIFEST_VERSION,
            "coverageRegistryId": self.coverage_registry_id,
            "providerId": self.provider_id,
            "providerKey": self.provider_key,
            "providerRevision": self.provider_revision,
            "registryVersion": self.registry_version,
            "coverageContractVersion": self.coverage_contract_version,
            "expectedContractCount": len(self.identities),
            "expectedContractsSha256": self.expected_contracts_sha256,
            "registeredBy": self.registered_by,
            "contracts": [
                identity.as_dict() for identity in self.identities
            ],
        }


@dataclass(frozen=True)
class LegalCorpusCoverageRegistrationResult:
    """Safe operator-facing outcome; never contains queries or credentials."""

    action: str
    coverage_registry_id: str
    expected_contract_count: int
    expected_contracts_sha256: str
    provider_id: str
    provider_registered: bool
    supersedes_coverage_registry_id: str | None = None

    def __post_init__(self) -> None:
        if self.action not in {"current", "would_register", "registered"}:
            raise ValueError("unsupported coverage registration action")

    def as_dict(self) -> Mapping[str, object]:
        return {
            "action": self.action,
            "coverageRegistryId": self.coverage_registry_id,
            "expectedContractCount": self.expected_contract_count,
            "expectedContractsSha256": self.expected_contracts_sha256,
            "providerId": self.provider_id,
            "providerRegistered": self.provider_registered,
            "supersedesCoverageRegistryId": (
                self.supersedes_coverage_registry_id
            ),
        }


class LegalCorpusCoverageRegistrationRepository(Protocol):
    """Deployment adapter; intentionally absent from API/worker composition."""

    def inspect(
        self,
        plan: LegalCorpusCoverageContractSetPlan,
    ) -> LegalCorpusCoverageRegistrationResult: ...

    def register(
        self,
        plan: LegalCorpusCoverageContractSetPlan,
    ) -> LegalCorpusCoverageRegistrationResult: ...


class LegalCorpusCoverageRegistrationService:
    """Thin use-case boundary for status/dry-run/apply operation modes."""

    def __init__(
        self,
        repository: LegalCorpusCoverageRegistrationRepository,
    ) -> None:
        self._repository = repository

    def inspect(
        self,
        plan: LegalCorpusCoverageContractSetPlan,
    ) -> LegalCorpusCoverageRegistrationResult:
        return self._repository.inspect(plan)

    def register(
        self,
        plan: LegalCorpusCoverageContractSetPlan,
    ) -> LegalCorpusCoverageRegistrationResult:
        return self._repository.register(plan)


def build_coverage_contract_set_plan(
    identities: Sequence[OfficialCorpusCoverageIdentity],
    *,
    registered_by: str,
) -> LegalCorpusCoverageContractSetPlan:
    """Validate exactly one complete Law.go.kr target contract set."""

    actor = str(registered_by or "").strip()
    if not actor or len(actor) > 300 or any(ord(char) < 32 for char in actor):
        raise LegalCorpusCoverageRegistrationError(
            "registeredBy must be a non-empty, control-free actor identity"
        )
    normalized = tuple(
        sorted(
            identities,
            key=lambda identity: (
                identity.resource_kind,
                identity.coverage_contract_id,
            ),
        )
    )
    if len(normalized) != REQUIRED_COVERAGE_CONTRACT_COUNT:
        raise LegalCorpusCoverageRegistrationError(
            "coverage manifest must contain exactly 8 target identities"
        )

    first = normalized[0]
    set_identity = (
        first.provider_key,
        first.provider_revision,
        first.registry_version,
        first.coverage_contract_version,
        first.source_adapter_version,
        first.coverage_registry_id,
        first.provider_id,
    )
    for identity in normalized:
        if type(identity.provider_revision) is not int:
            raise LegalCorpusCoverageRegistrationError(
                "providerRevision must be an integer"
            )
        if (
            identity.provider_key,
            identity.provider_revision,
            identity.registry_version,
            identity.coverage_contract_version,
            identity.source_adapter_version,
            identity.coverage_registry_id,
            identity.provider_id,
        ) != set_identity:
            raise LegalCorpusCoverageRegistrationError(
                "all coverage identities must bind one provider, adapter, "
                "registry, and coverage-contract version"
            )

    resource_kinds = [identity.resource_kind for identity in normalized]
    contract_ids = [
        identity.coverage_contract_id for identity in normalized
    ]
    identity_hashes = [
        identity.coverage_identity_sha256 for identity in normalized
    ]
    if (
        len(set(resource_kinds)) != len(resource_kinds)
        or len(set(contract_ids)) != len(contract_ids)
        or len(set(identity_hashes)) != len(identity_hashes)
    ):
        raise LegalCorpusCoverageRegistrationError(
            "coverage manifest contains duplicate target identities"
        )

    code_owned_targets = {
        kind.value: contract.list_target
        for kind, contract in LAW_GO_KR_BOUNDARY_CONTRACTS.items()
    }
    if len(code_owned_targets) != REQUIRED_COVERAGE_CONTRACT_COUNT:
        raise LegalCorpusCoverageRegistrationError(
            "code-owned Law.go.kr target taxonomy is not an eight-target set"
        )
    actual_targets = {
        identity.resource_kind: identity.target for identity in normalized
    }
    if actual_targets != code_owned_targets:
        raise LegalCorpusCoverageRegistrationError(
            "coverage manifest does not match the code-owned required targets"
        )
    expected_boundaries = {
        kind.value: (
            f"{contract.list_contract_version}."
            f"{contract.detail_contract_version}."
            f"{contract.history_contract_version}"
        )
        for kind, contract in LAW_GO_KR_BOUNDARY_CONTRACTS.items()
    }
    actual_boundaries = {
        identity.resource_kind: identity.boundary_contract_version
        for identity in normalized
    }
    if actual_boundaries != expected_boundaries:
        raise LegalCorpusCoverageRegistrationError(
            "coverage manifest does not match the code-owned boundary "
            "contract versions"
        )

    expected_hash = hashlib.sha256(
        "\n".join(sorted(contract_ids)).encode("utf-8")
    ).hexdigest()
    return LegalCorpusCoverageContractSetPlan(
        identities=normalized,
        registered_by=actor,
        expected_contracts_sha256=expected_hash,
    )


def load_coverage_contract_set_manifest(
    path: str | Path,
) -> LegalCorpusCoverageContractSetPlan:
    """Load a strict, query-free-on-output deployment manifest from disk."""

    manifest_path = Path(path)
    try:
        encoded = manifest_path.read_bytes()
        if len(encoded) > MAX_COVERAGE_MANIFEST_BYTES:
            raise LegalCorpusCoverageRegistrationError(
                "coverage registration manifest exceeds 256 KiB"
            )
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LegalCorpusCoverageRegistrationError(
            "coverage registration manifest is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise LegalCorpusCoverageRegistrationError(
            "coverage registration manifest must be a JSON object"
        )
    allowed_root = {"schemaVersion", "registeredBy", "identities"}
    if set(payload) != allowed_root:
        raise LegalCorpusCoverageRegistrationError(
            "coverage registration manifest has missing or unknown fields"
        )
    if payload.get("schemaVersion") != (
        COVERAGE_REGISTRATION_MANIFEST_VERSION
    ):
        raise LegalCorpusCoverageRegistrationError(
            "coverage registration manifest schemaVersion is unsupported"
        )
    rows = payload.get("identities")
    if not isinstance(rows, list):
        raise LegalCorpusCoverageRegistrationError(
            "coverage registration identities must be a JSON array"
        )
    identities = tuple(
        _identity_from_manifest_row(row, index=index)
        for index, row in enumerate(rows)
    )
    return build_coverage_contract_set_plan(
        identities,
        registered_by=str(payload.get("registeredBy") or ""),
    )


def _identity_from_manifest_row(
    row: Any,
    *,
    index: int,
) -> OfficialCorpusCoverageIdentity:
    if not isinstance(row, dict):
        raise LegalCorpusCoverageRegistrationError(
            f"coverage identity at index {index} must be a JSON object"
        )
    keys = set(row)
    if not _IDENTITY_REQUIRED_KEYS.issubset(keys) or not keys.issubset(
        _IDENTITY_REQUIRED_KEYS | _IDENTITY_DERIVED_KEYS
    ):
        raise LegalCorpusCoverageRegistrationError(
            f"coverage identity at index {index} has missing or unknown fields"
        )
    if (
        type(row["providerRevision"]) is not int
        or any(
            not isinstance(row[key], str)
            for key in _IDENTITY_REQUIRED_KEYS - {"providerRevision"}
        )
    ):
        raise LegalCorpusCoverageRegistrationError(
            f"coverage identity at index {index} has invalid field types"
        )
    try:
        identity = OfficialCorpusCoverageIdentity(
            provider_key=row["providerKey"],
            provider_revision=row["providerRevision"],
            registry_version=row["registryVersion"],
            resource_kind=row["resourceKind"],
            target=row["target"],
            query_sha256=row["querySha256"],
            source_adapter_version=row["sourceAdapterVersion"],
            boundary_contract_version=row["boundaryContractVersion"],
            coverage_contract_version=row["coverageContractVersion"],
        )
    except (TypeError, ValueError) as exc:
        raise LegalCorpusCoverageRegistrationError(
            f"coverage identity at index {index} is invalid"
        ) from exc

    derived = identity.as_dict()
    if (
        "schemaVersion" in row
        and row["schemaVersion"] != COVERAGE_IDENTITY_SCHEMA_VERSION
    ):
        raise LegalCorpusCoverageRegistrationError(
            f"coverage identity at index {index} has a stale schemaVersion"
        )
    for key in (
        "providerId",
        "coverageRegistryId",
        "coverageContractId",
        "coverageIdentitySha256",
    ):
        if key in row and row[key] != derived[key]:
            raise LegalCorpusCoverageRegistrationError(
                f"coverage identity at index {index} has a mismatched {key}"
            )
    return identity


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> Mapping[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LegalCorpusCoverageRegistrationError(
                f"coverage registration manifest repeats JSON key {key!r}"
            )
        result[key] = value
    return result


def _reject_non_finite_json_constant(value: str) -> None:
    raise LegalCorpusCoverageRegistrationError(
        f"coverage registration manifest contains non-JSON value {value!r}"
    )


__all__ = [
    "COVERAGE_REGISTRATION_MANIFEST_VERSION",
    "LegalCorpusCoverageContractSetPlan",
    "LegalCorpusCoverageRegistrationError",
    "LegalCorpusCoverageRegistrationRepository",
    "LegalCorpusCoverageRegistrationResult",
    "LegalCorpusCoverageRegistrationService",
    "MAX_COVERAGE_MANIFEST_BYTES",
    "REQUIRED_COVERAGE_CONTRACT_COUNT",
    "build_coverage_contract_set_plan",
    "load_coverage_contract_set_manifest",
]
