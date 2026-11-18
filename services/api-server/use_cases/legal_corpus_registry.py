"""Versioned, fail-closed registry for official legal-corpus sources.

An entry in the Law.go.kr search target map is not enough to publish a
verified corpus version.  Production enablement additionally requires a
fixture-backed detail/history contract and a versioned semantic normalizer.
This registry makes that distinction executable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Callable, Iterable, Mapping

from legal_intelligence.law_go_kr_contracts import (
    LAW_GO_KR_BOUNDARY_CONTRACTS,
)
from legal_intelligence.corpus_coverage import (
    OfficialCorpusCoverageIdentity,
)
from legal_intelligence.models import LegalResourceKind
from legal_intelligence.providers import LAW_GO_KR_TARGET_DESCRIPTORS
from use_cases.law_go_kr_corpus_source import (
    LawGoKrHistoryFetcher,
    LawGoKrOfficialCorpusPageSource,
    LawGoKrRecordNormalizer,
)
from use_cases.legal_corpus_coordinator import OfficialCorpusPageSource


_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{2,299}$")


class LegalCorpusBackfillMode(str, Enum):
    INITIAL = "initial"
    INCREMENTAL = "incremental"


class OfficialCorpusSourceAvailability(str, Enum):
    ENABLED = "enabled"
    BLOCKED = "blocked"
    DISABLED = "disabled"


class OfficialCorpusSourceUnavailable(RuntimeError):
    """A target is not eligible to publish verified corpus versions."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class OfficialCorpusSourceRequest:
    resource_kind: LegalResourceKind
    query: str
    requested_scope: str
    requested_by: str
    credential: str
    mode: LegalCorpusBackfillMode
    requested_as_of_date: date | None = None
    lease_owner_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "query",
            "requested_scope",
            "requested_by",
            "credential",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")

    @property
    def query_sha256(self) -> str:
        return hashlib.sha256(self.query.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OfficialCorpusTargetCapability:
    provider_key: str
    provider_revision: int
    resource_kind: LegalResourceKind
    registry_version: str
    source_adapter_version: str
    boundary_contract_version: str
    availability: OfficialCorpusSourceAvailability
    supported_modes: tuple[LegalCorpusBackfillMode, ...]
    reason_code: str | None = None
    reason: str | None = None
    detail_verified: bool = False
    history_verified: bool = False
    semantic_normalizer_verified: bool = False
    coverage_contract_version: str = "official-corpus-coverage-v1"
    certified_query_sha256: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "provider_key",
            "registry_version",
            "source_adapter_version",
            "boundary_contract_version",
        ):
            if not _VERSION.fullmatch(str(getattr(self, name) or "")):
                raise ValueError(f"{name} must be a versioned safe identifier")
        if self.provider_revision < 1:
            raise ValueError("provider_revision must be positive")
        object.__setattr__(
            self,
            "supported_modes",
            tuple(dict.fromkeys(self.supported_modes)),
        )
        if self.availability == OfficialCorpusSourceAvailability.ENABLED:
            if not self.supported_modes:
                raise ValueError("enabled source requires a supported mode")
            if not (
                self.detail_verified
                and self.history_verified
                and self.semantic_normalizer_verified
            ):
                raise ValueError(
                    "enabled source requires verified detail, history, "
                    "and semantic-normalizer adapters"
                )
            if self.reason_code or self.reason:
                raise ValueError("enabled source cannot carry a blocked reason")
            if not re.fullmatch(
                r"[0-9a-f]{64}",
                str(self.certified_query_sha256 or ""),
            ):
                raise ValueError(
                    "enabled source requires a code-owned certified query hash"
                )
        elif not self.reason_code or not self.reason:
            raise ValueError("blocked/disabled source requires a reason")

    def coverage_identity(self) -> OfficialCorpusCoverageIdentity:
        contract = LAW_GO_KR_BOUNDARY_CONTRACTS[self.resource_kind]
        return OfficialCorpusCoverageIdentity(
            provider_key=self.provider_key,
            provider_revision=self.provider_revision,
            registry_version=self.registry_version,
            resource_kind=self.resource_kind.value,
            target=contract.list_target,
            query_sha256=str(self.certified_query_sha256 or ""),
            source_adapter_version=self.source_adapter_version,
            boundary_contract_version=self.boundary_contract_version,
            coverage_contract_version=self.coverage_contract_version,
        )

    def as_public_dict(self) -> dict[str, object]:
        return {
            "providerKey": self.provider_key,
            "providerRevision": self.provider_revision,
            "resourceKind": self.resource_kind.value,
            "registryVersion": self.registry_version,
            "sourceAdapterVersion": self.source_adapter_version,
            "boundaryContractVersion": self.boundary_contract_version,
            "availability": self.availability.value,
            "supportedModes": [item.value for item in self.supported_modes],
            "reasonCode": self.reason_code,
            "reason": self.reason,
            "detailVerified": self.detail_verified,
            "historyVerified": self.history_verified,
            "semanticNormalizerVerified": (
                self.semantic_normalizer_verified
            ),
            "coverageContractVersion": self.coverage_contract_version,
            "certifiedQuerySha256": self.certified_query_sha256,
        }


OfficialCorpusSourceFactory = Callable[
    [OfficialCorpusSourceRequest],
    OfficialCorpusPageSource,
]


@dataclass(frozen=True)
class OfficialCorpusSourceRegistration:
    capability: OfficialCorpusTargetCapability
    factory: OfficialCorpusSourceFactory | None = None

    def __post_init__(self) -> None:
        enabled = (
            self.capability.availability
            == OfficialCorpusSourceAvailability.ENABLED
        )
        if enabled != (self.factory is not None):
            raise ValueError(
                "only an enabled source registration may have a factory"
            )


class VersionedOfficialCorpusSourceRegistry:
    """Immutable-by-key registry; duplicate target replacement is forbidden."""

    def __init__(
        self,
        registrations: Iterable[OfficialCorpusSourceRegistration] = (),
    ) -> None:
        self._registrations: dict[
            tuple[str, LegalResourceKind],
            OfficialCorpusSourceRegistration,
        ] = {}
        for registration in registrations:
            self.register(registration)

    def register(
        self,
        registration: OfficialCorpusSourceRegistration,
    ) -> None:
        key = (
            registration.capability.provider_key,
            registration.capability.resource_kind,
        )
        if key in self._registrations:
            raise ValueError(
                "official corpus source target is already registered"
            )
        self._registrations[key] = registration

    def registration(
        self,
        provider_key: str,
        resource_kind: LegalResourceKind,
    ) -> OfficialCorpusSourceRegistration:
        key = (provider_key.strip(), resource_kind)
        registration = self._registrations.get(key)
        if registration is None:
            raise OfficialCorpusSourceUnavailable(
                "OFFICIAL_CORPUS_TARGET_UNREGISTERED",
                "No versioned official corpus source is registered for "
                f"{provider_key}:{resource_kind.value}.",
            )
        return registration

    def create_source(
        self,
        provider_key: str,
        request: OfficialCorpusSourceRequest,
    ) -> OfficialCorpusPageSource:
        registration = self.registration(
            provider_key,
            request.resource_kind,
        )
        capability = registration.capability
        if (
            capability.availability
            != OfficialCorpusSourceAvailability.ENABLED
        ):
            raise OfficialCorpusSourceUnavailable(
                str(capability.reason_code),
                str(capability.reason),
            )
        if request.mode not in capability.supported_modes:
            raise OfficialCorpusSourceUnavailable(
                "OFFICIAL_CORPUS_BACKFILL_MODE_UNSUPPORTED",
                f"{request.mode.value} is not certified for "
                f"{request.resource_kind.value}.",
            )
        if request.query_sha256 != capability.certified_query_sha256:
            raise OfficialCorpusSourceUnavailable(
                "OFFICIAL_CORPUS_QUERY_NOT_CERTIFIED",
                "The configured query does not match the code-owned full "
                f"coverage contract for {request.resource_kind.value}.",
            )
        assert registration.factory is not None
        return registration.factory(request)

    def capabilities(self) -> tuple[OfficialCorpusTargetCapability, ...]:
        return tuple(
            registration.capability
            for _, registration in sorted(
                self._registrations.items(),
                key=lambda item: (
                    item[0][0],
                    item[0][1].value,
                ),
            )
        )


def build_default_official_corpus_source_registry(
    *,
    registry_version: str = "official-corpus-registry-v1",
) -> VersionedOfficialCorpusSourceRegistry:
    """Return truthful production defaults.

    Law.go.kr list/detail boundary fixtures exist, but no production semantic
    normalizer and no official target-specific history fetcher are registered.
    Every target therefore remains blocked.  Search results can still be used
    as candidates through the search provider, but cannot enter the verified
    corpus through this registry.
    """

    registrations: list[OfficialCorpusSourceRegistration] = []
    for resource_kind in sorted(
        LAW_GO_KR_TARGET_DESCRIPTORS,
        key=lambda item: item.value,
    ):
        contract = LAW_GO_KR_BOUNDARY_CONTRACTS[resource_kind]
        registrations.append(
            OfficialCorpusSourceRegistration(
                capability=OfficialCorpusTargetCapability(
                    provider_key="law-go-kr",
                    provider_revision=1,
                    resource_kind=resource_kind,
                    registry_version=registry_version,
                    source_adapter_version=(
                        "realytics-law-go-kr-corpus-v1"
                    ),
                    boundary_contract_version=(
                        f"{contract.list_contract_version}."
                        f"{contract.detail_contract_version}."
                        f"{contract.history_contract_version}"
                    ),
                    availability=(
                        OfficialCorpusSourceAvailability.BLOCKED
                    ),
                    supported_modes=(),
                    reason_code=(
                        "VERIFIED_SEMANTIC_AND_HISTORY_ADAPTER_NOT_REGISTERED"
                    ),
                    reason=(
                        "The official list/detail boundary alone is not "
                        "evidence of historical applicability. A versioned "
                        "semantic normalizer and fixture-backed official "
                        "history adapter must be registered in code."
                    ),
                    detail_verified=True,
                    history_verified=False,
                    semantic_normalizer_verified=False,
                    coverage_contract_version=(
                        "law-go-kr-full-corpus-v1"
                    ),
                )
            )
        )
    return VersionedOfficialCorpusSourceRegistry(registrations)


def build_law_go_kr_source_registration(
    *,
    resource_kind: LegalResourceKind,
    record_normalizer: LawGoKrRecordNormalizer,
    history_fetcher: LawGoKrHistoryFetcher,
    source_adapter_version: str,
    registry_version: str,
    certified_query_sha256: str,
    coverage_contract_version: str = "law-go-kr-full-corpus-v1",
    provider_revision: int = 1,
    supported_modes: tuple[LegalCorpusBackfillMode, ...] = (
        LegalCorpusBackfillMode.INITIAL,
    ),
    source_options: Mapping[str, object] | None = None,
) -> OfficialCorpusSourceRegistration:
    """Build an enabled registration from reviewed, code-owned adapters.

    Environment variables cannot name/import arbitrary normalizer functions.
    Enabling a target is therefore a deploy-time code decision backed by the
    registered boundary contract and its official response fixtures.
    """

    contract = LAW_GO_KR_BOUNDARY_CONTRACTS.get(resource_kind)
    if contract is None:
        raise ValueError("resource kind has no Law.go.kr boundary contract")
    if LegalCorpusBackfillMode.INCREMENTAL in supported_modes:
        raise ValueError(
            "the generic Law.go.kr list-page source is not a certified "
            "incremental change feed"
        )
    options = dict(source_options or {})
    forbidden = {
        "oc",
        "resource_kind",
        "query",
        "requested_scope",
        "requested_by",
        "record_normalizer",
        "requested_as_of_date",
        "history_fetcher",
        "history_schema_version",
        "sync_kind",
        "lease_owner_id",
            "adapter_version",
            "coverage_identity",
            "coverage_contract_version",
    }
    overlap = forbidden.intersection(options)
    if overlap:
        raise ValueError(
            "source_options cannot override registry-owned fields: "
            + ", ".join(sorted(overlap))
        )

    def factory(
        request: OfficialCorpusSourceRequest,
    ) -> LawGoKrOfficialCorpusPageSource:
        return LawGoKrOfficialCorpusPageSource(
            oc=request.credential,
            resource_kind=request.resource_kind,
            query=request.query,
            requested_scope=request.requested_scope,
            requested_by=request.requested_by,
            record_normalizer=record_normalizer,
            requested_as_of_date=request.requested_as_of_date,
            history_fetcher=history_fetcher,
            history_schema_version=contract.history_contract_version,
            sync_kind=request.mode.value,
            lease_owner_id=request.lease_owner_id,
            adapter_version=source_adapter_version,
            coverage_identity=OfficialCorpusCoverageIdentity(
                provider_key="law-go-kr",
                provider_revision=provider_revision,
                registry_version=registry_version,
                resource_kind=resource_kind.value,
                target=contract.list_target,
                query_sha256=certified_query_sha256,
                source_adapter_version=source_adapter_version,
                boundary_contract_version=(
                    f"{contract.list_contract_version}."
                    f"{contract.detail_contract_version}."
                    f"{contract.history_contract_version}"
                ),
                coverage_contract_version=coverage_contract_version,
            ),
            provider_revision=provider_revision,
            **options,
        )

    return OfficialCorpusSourceRegistration(
        capability=OfficialCorpusTargetCapability(
            provider_key="law-go-kr",
            provider_revision=provider_revision,
            resource_kind=resource_kind,
            registry_version=registry_version,
            source_adapter_version=source_adapter_version,
            boundary_contract_version=(
                f"{contract.list_contract_version}."
                f"{contract.detail_contract_version}."
                f"{contract.history_contract_version}"
            ),
            availability=OfficialCorpusSourceAvailability.ENABLED,
            supported_modes=supported_modes,
            detail_verified=True,
            history_verified=True,
            semantic_normalizer_verified=True,
            coverage_contract_version=coverage_contract_version,
            certified_query_sha256=certified_query_sha256,
        ),
        factory=factory,
    )


__all__ = [
    "LegalCorpusBackfillMode",
    "OfficialCorpusSourceAvailability",
    "OfficialCorpusSourceRegistration",
    "OfficialCorpusSourceRequest",
    "OfficialCorpusSourceUnavailable",
    "OfficialCorpusTargetCapability",
    "VersionedOfficialCorpusSourceRegistry",
    "build_default_official_corpus_source_registry",
    "build_law_go_kr_source_registration",
]
