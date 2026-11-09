from __future__ import annotations

"""Verified official-corpus synchronization.

The application service preserves two hard boundaries:

1. provider bytes are durably written with put-if-absent semantics to an
   immutable object store;
2. only after that receipt is hash/size verified may the migration-owned
   PostgreSQL repository atomically publish a verified normalized version.

The ordinary API runtime role must not call this service.  Deploy it in the
legal corpus sync worker with a dedicated object-store identity and the
database role described by ``PostgresLegalCorpusSyncRepository``.
"""

import hashlib
import hmac
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from db.repositories.legal_corpus_repository import (
    CorpusRecordPublication,
    CorpusPublication,
    LegalCorpusEffectiveTimeConflict,
    LegalCorpusImmutableConflict,
    LegalCorpusRepositoryError,
    LegalCorpusSyncRepository,
    LegalProvisionRecord,
    LegalResourceCrosswalkRecord,
    LegalResourceRecord,
    LegalResourceVersionRecord,
    LegalSourceProviderRecord,
    LegalSyncRunRecord,
    PublishedCorpusPage,
    PublishedCorpusVersion,
    SourceFetchRecord,
)
from legal_intelligence.corpus_identity import (
    IDENTITY_SCHEMA_VERSION,
    OfficialResourceIdentity,
    legal_resource_crosswalk_id,
)
from legal_intelligence.corpus_coverage import (
    OfficialCorpusCoverageIdentity,
)
from legal_intelligence.effective_time import (
    EFFECTIVE_CONTINUITY_POLICIES,
)


RESOURCE_TYPES = frozenset(
    {
        "statute",
        "decree",
        "ministerial_rule",
        "administrative_rule",
        "local_ordinance",
        "precedent",
        "constitutional_decision",
        "legal_interpretation",
        "committee_decision",
        "official_term",
        "official_form",
        "official_appendix",
    }
)
PROVISION_TYPES = frozenset(
    {
        "preamble",
        "chapter",
        "section",
        "article",
        "paragraph",
        "item",
        "subitem",
        "addendum",
        "appendix",
        "form",
        "decision_section",
        "holding",
        "reasoning",
    }
)
TRANSPORTS = frozenset(
    {
        "direct_api",
        "bulk_download",
        "self_hosted_mcp",
        "remote_mcp",
        "manual_verified_import",
    }
)
SYNC_KINDS = frozenset(
    {"initial", "incremental", "backfill", "gap_fill", "verification"}
)
AUTHORITY_TIERS = frozenset(
    {
        "official_primary",
        "official_secondary",
        "verified_professional",
        "tenant_supplied",
    }
)
GLOBAL_CORPUS_AUTHORITY_TIERS = frozenset(
    {"official_primary", "official_secondary"}
)
PROVIDER_KINDS = frozenset(
    {
        "official_api",
        "official_bulk",
        "court_api",
        "regulator_api",
        "verified_manual_import",
    }
)
PUBLISHABLE_VERIFICATION_STATUSES = frozenset(
    {"source_verified", "schema_verified"}
)
VERIFICATION_BASES_BY_TRANSPORT = {
    "direct_api": frozenset({"official_api_response"}),
    "bulk_download": frozenset({"official_bulk_artifact"}),
    "self_hosted_mcp": frozenset({"self_hosted_official_bridge"}),
    "manual_verified_import": frozenset({"verified_manual_review"}),
    "remote_mcp": frozenset(),
}
_SENSITIVE_KEYS = frozenset(
    {
        "oc",
        "key",
        "token",
        "secret",
        "password",
        "authorization",
        "apikey",
        "accesskey",
        "accesstoken",
        "refreshtoken",
        "servicekey",
        "clientsecret",
    }
)
_SAFE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")


class LegalCorpusSyncError(RuntimeError):
    """Base error raised before or during a corpus synchronization."""


class RawObjectIntegrityError(LegalCorpusSyncError):
    """Immutable storage receipt does not attest the supplied bytes."""


@dataclass(frozen=True)
class RawObjectReceipt:
    object_key: str
    object_version: str
    sha256: str
    size_bytes: int


class ImmutableRawObjectStore(Protocol):
    """Content-addressed storage with versioning/object-lock semantics."""

    def put_if_absent(
        self,
        *,
        object_key: str,
        content: bytes,
        content_type: str,
        expected_sha256: str,
        metadata: Mapping[str, str],
    ) -> RawObjectReceipt: ...


@dataclass(frozen=True)
class OfficialSourceVerification:
    """Attestation produced by the provider-specific fetch/normalizer adapter."""

    response_sha256: str
    source_authority: str
    official_identifier: str
    parser_schema_version: str
    verification_basis: str
    verifier_id: str
    verifier_version: str
    schema_validation_status: str = "passed"
    authority_validation_status: str = "matched"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "response_sha256",
            _sha256(self.response_sha256, "response_sha256"),
        )
        for name in (
            "source_authority",
            "official_identifier",
            "parser_schema_version",
            "verification_basis",
            "verifier_id",
            "verifier_version",
            "schema_validation_status",
            "authority_validation_status",
        ):
            object.__setattr__(
                self, name, _canonical_text(getattr(self, name), name)
            )
        if self.schema_validation_status != "passed":
            raise ValueError("schema_validation_status must be passed")
        if self.authority_validation_status != "matched":
            raise ValueError(
                "authority_validation_status must be matched"
            )


@dataclass(frozen=True)
class OfficialProvisionInput:
    provision_type: str
    provision_identifier: str
    display_path: str
    body_text: str
    source_locator: str
    parent_provision_identifier: str | None = None
    ordinal_path: tuple[int, ...] = ()
    heading_text: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "provision_type",
            "provision_identifier",
            "display_path",
            "body_text",
            "source_locator",
        ):
            object.__setattr__(
                self, name, _canonical_text(getattr(self, name), name)
            )
        if self.parent_provision_identifier is not None:
            object.__setattr__(
                self,
                "parent_provision_identifier",
                _canonical_text(
                    self.parent_provision_identifier,
                    "parent_provision_identifier",
                ),
            )
        if self.heading_text is not None:
            object.__setattr__(
                self,
                "heading_text",
                _canonical_text(
                    self.heading_text, "heading_text", allow_empty=True
                ),
            )
        _choice(self.provision_type, PROVISION_TYPES, "provision_type")
        _required(self.provision_identifier, "provision_identifier")
        _required(self.display_path, "display_path")
        _required(self.source_locator, "source_locator")
        if not str(self.body_text or "").strip() and not str(
            self.heading_text or ""
        ).strip():
            raise ValueError(
                "a provision requires body_text or heading_text"
            )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in self.ordinal_path
        ):
            raise ValueError("ordinal_path must contain non-negative integers")


@dataclass(frozen=True)
class OfficialLegalRecordInput:
    resource_type: str
    official_identifier: str
    official_version_identifier: str
    canonical_title: str
    issuing_authority: str
    provisions: tuple[OfficialProvisionInput, ...]
    jurisdiction_code: str = "KR"
    canonical_official_identifier: str | None = None
    alternative_titles: tuple[str, ...] = ()
    publication_date: date | None = None
    promulgation_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    valid_time_start: date | None = None
    valid_time_end: date | None = None
    parser_schema_version: str = "realytics-legal-corpus-v1"
    verification_status: str = "source_verified"
    predecessor_official_version_identifier: str | None = None
    effective_continuity_policy: str = "strict_contiguous"
    effective_gap_reason: str | None = None
    observed_at: datetime | None = None
    resource_metadata: Mapping[str, Any] = field(default_factory=dict)
    version_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "resource_type",
            "official_identifier",
            "official_version_identifier",
            "canonical_title",
            "issuing_authority",
            "jurisdiction_code",
            "parser_schema_version",
            "verification_status",
            "effective_continuity_policy",
        ):
            object.__setattr__(
                self, name, _canonical_text(getattr(self, name), name)
            )
        canonical_identifier = (
            self.official_identifier
            if self.canonical_official_identifier is None
            else _canonical_text(
                self.canonical_official_identifier,
                "canonical_official_identifier",
            )
        )
        object.__setattr__(
            self,
            "canonical_official_identifier",
            canonical_identifier,
        )
        if self.predecessor_official_version_identifier is not None:
            object.__setattr__(
                self,
                "predecessor_official_version_identifier",
                _canonical_text(
                    self.predecessor_official_version_identifier,
                    "predecessor_official_version_identifier",
                ),
            )
        if self.effective_gap_reason is not None:
            object.__setattr__(
                self,
                "effective_gap_reason",
                _canonical_text(
                    self.effective_gap_reason,
                    "effective_gap_reason",
                ),
            )
        normalized_titles: list[str] = []
        seen_titles: set[str] = set()
        for title in self.alternative_titles:
            normalized = _canonical_text(
                title, "alternative_titles", allow_empty=True
            )
            if normalized and normalized not in seen_titles:
                seen_titles.add(normalized)
                normalized_titles.append(normalized)
        object.__setattr__(
            self, "alternative_titles", tuple(normalized_titles)
        )
        object.__setattr__(self, "provisions", tuple(self.provisions))
        _choice(self.resource_type, RESOURCE_TYPES, "resource_type")
        _required(self.official_identifier, "official_identifier")
        _required(
            self.official_version_identifier,
            "official_version_identifier",
        )
        _required(self.canonical_title, "canonical_title")
        _required(self.issuing_authority, "issuing_authority")
        _required(self.jurisdiction_code, "jurisdiction_code")
        _required(self.parser_schema_version, "parser_schema_version")
        _choice(
            self.verification_status,
            PUBLISHABLE_VERIFICATION_STATUSES,
            "verification_status",
        )
        _choice(
            self.effective_continuity_policy,
            EFFECTIVE_CONTINUITY_POLICIES,
            "effective_continuity_policy",
        )
        if (
            self.effective_continuity_policy
            == "allow_documented_gap"
            and self.effective_gap_reason is None
        ):
            raise ValueError(
                "allow_documented_gap requires effective_gap_reason"
            )
        if (
            self.effective_continuity_policy
            != "allow_documented_gap"
            and self.effective_gap_reason is not None
        ):
            raise ValueError(
                "effective_gap_reason requires allow_documented_gap"
            )
        if not self.provisions:
            raise ValueError("a verified legal record requires provisions")
        _ordered_provisions(self.provisions)
        _date_range(
            self.effective_from, self.effective_to, "effective date"
        )
        _date_range(
            self.valid_time_start, self.valid_time_end, "valid-time"
        )
        if self.observed_at is not None:
            _aware(self.observed_at, "observed_at")


@dataclass(frozen=True)
class LegalCorpusSyncRequest:
    provider_key: str
    provider_revision: int
    source_authority: str
    authority_tier: str
    provider_kind: str
    adapter_version: str
    transport: str
    endpoint_key: str
    source_locator: str
    raw_content: bytes
    raw_content_type: str
    record: OfficialLegalRecordInput
    source_verification: OfficialSourceVerification
    requested_scope: str
    requested_by: str
    coverage_identity: OfficialCorpusCoverageIdentity | None = None
    request_identity: Mapping[str, Any] = field(default_factory=dict)
    canonical_base_url: str | None = None
    terms_version: str | None = None
    sync_kind: str = "incremental"
    checkpoint_before: str | None = None
    checkpoint_after: str | None = None
    idempotency_key: str | None = None
    response_status: int | None = 200
    provider_etag: str | None = None
    provider_last_modified: str | None = None
    # Adapters must preserve this value when retrying one acquisition. A new
    # poll uses a new timestamp and therefore receives a new default run/fetch.
    source_fetched_at: datetime | None = None
    lease_owner_id: str | None = None
    lease_ttl_seconds: int = 300
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)
    fetch_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "provider_key",
            "source_authority",
            "authority_tier",
            "provider_kind",
            "adapter_version",
            "transport",
            "endpoint_key",
            "source_locator",
            "raw_content_type",
            "requested_scope",
            "requested_by",
            "sync_kind",
        ):
            object.__setattr__(
                self, name, _canonical_text(getattr(self, name), name)
            )
        if self.canonical_base_url is not None:
            object.__setattr__(
                self,
                "canonical_base_url",
                _canonical_text(
                    self.canonical_base_url, "canonical_base_url"
                ),
            )
        if self.terms_version is not None:
            object.__setattr__(
                self,
                "terms_version",
                _canonical_text(self.terms_version, "terms_version"),
            )
        if self.idempotency_key is not None:
            object.__setattr__(
                self,
                "idempotency_key",
                _canonical_text(
                    self.idempotency_key, "idempotency_key"
                ),
            )
        if self.lease_owner_id is not None:
            object.__setattr__(
                self,
                "lease_owner_id",
                _canonical_text(self.lease_owner_id, "lease_owner_id"),
            )
        _safe_key(self.provider_key, "provider_key")
        if (
            not isinstance(self.provider_revision, int)
            or isinstance(self.provider_revision, bool)
            or self.provider_revision < 1
        ):
            raise ValueError("provider_revision must be a positive integer")
        _required(self.source_authority, "source_authority")
        _choice(self.authority_tier, AUTHORITY_TIERS, "authority_tier")
        if self.authority_tier not in GLOBAL_CORPUS_AUTHORITY_TIERS:
            raise ValueError(
                "global legal corpus accepts only official_primary or "
                "official_secondary authority; tenant/professional material "
                "must use the tenant RLS overlay"
            )
        if _normalized_key(self.source_authority) in {
            "tenantdocument",
            "tenantsupplied",
            "callersupplied",
            "unknown",
        }:
            raise ValueError(
                "tenant/caller material cannot be published to the global "
                "legal corpus"
            )
        _choice(self.provider_kind, PROVIDER_KINDS, "provider_kind")
        _required(self.adapter_version, "adapter_version")
        _choice(self.transport, TRANSPORTS, "transport")
        _safe_key(self.endpoint_key, "endpoint_key")
        _required(self.source_locator, "source_locator")
        if not isinstance(self.raw_content, bytes):
            raise TypeError("raw_content must be immutable bytes")
        if not self.raw_content:
            raise ValueError("raw_content must not be empty")
        if not isinstance(
            self.source_verification, OfficialSourceVerification
        ):
            raise TypeError(
                "source_verification must be OfficialSourceVerification"
            )
        _required(self.raw_content_type, "raw_content_type")
        _choice(self.sync_kind, SYNC_KINDS, "sync_kind")
        _required(self.requested_scope, "requested_scope")
        _required(self.requested_by, "requested_by")
        if self.idempotency_key is not None:
            _required(self.idempotency_key, "idempotency_key")
        if self.response_status is not None and not (
            100 <= int(self.response_status) <= 599
        ):
            raise ValueError("response_status must be a valid HTTP status")
        if self.source_fetched_at is not None:
            _aware(self.source_fetched_at, "source_fetched_at")
        if (
            not isinstance(self.lease_ttl_seconds, int)
            or isinstance(self.lease_ttl_seconds, bool)
            or not 30 <= self.lease_ttl_seconds <= 3600
        ):
            raise ValueError(
                "lease_ttl_seconds must be an integer between 30 and 3600"
            )


class LegalCorpusSyncService:
    """Coordinates immutable raw storage and atomic normalized publication."""

    def __init__(
        self,
        repository: LegalCorpusSyncRepository,
        raw_object_store: ImmutableRawObjectStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._raw_object_store = raw_object_store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def sync(self, request: LegalCorpusSyncRequest) -> PublishedCorpusVersion:
        """Publish one record through the same fenced page transaction."""

        return self.sync_page((request,)).records[0]

    def sync_page(
        self, requests: Sequence[LegalCorpusSyncRequest]
    ) -> PublishedCorpusPage:
        """Publish one provider page as one fetch/checkpoint transaction.

        Every request represents one normalized record extracted from the same
        immutable provider response.  The page either publishes every record
        and advances the checkpoint once, or publishes none of them.
        """

        page_requests = tuple(requests)
        if not page_requests:
            raise ValueError("a legal corpus page requires at least one record")
        _validate_page_request_envelope(page_requests)
        request = page_requests[0]

        requested_at = _aware(self._clock(), "clock")
        fetched_at = request.source_fetched_at or requested_at
        _aware(fetched_at, "source_fetched_at")
        if fetched_at > requested_at:
            raise ValueError(
                "source_fetched_at cannot be later than requested_at"
            )

        sanitized_locator = sanitize_source_locator(request.source_locator)
        request_fingerprint = _stable_hash(
            {
                "endpointKey": request.endpoint_key,
                "requestIdentity": redact_secrets(request.request_identity),
                "sourceLocator": sanitized_locator,
            }
        )
        raw_sha256 = hashlib.sha256(request.raw_content).hexdigest()
        provider_id = _deterministic_id(
            "legal-provider",
            {
                "providerKey": request.provider_key,
                "providerRevision": request.provider_revision,
            },
        )

        prepared: list[dict[str, Any]] = []
        record_identities: set[tuple[str, str]] = set()
        resource_ids: set[str] = set()
        official_identifiers: set[str] = set()
        for item_request in page_requests:
            observed_at = item_request.record.observed_at or fetched_at
            _aware(observed_at, "observed_at")
            if observed_at > fetched_at:
                raise ValueError("observed_at cannot be later than fetched_at")
            normalized_sha256 = _stable_hash(
                canonical_normalized_record(item_request.record)
            )
            source_verification = _verify_source_contract(
                item_request, raw_sha256=raw_sha256
            )
            resource_identity = OfficialResourceIdentity(
                provider_key=item_request.provider_key,
                resource_type=item_request.record.resource_type,
                canonical_official_identifier=(
                    item_request.record.canonical_official_identifier
                    or item_request.record.official_identifier
                ),
                jurisdiction_code=(
                    item_request.record.jurisdiction_code
                ),
            )
            resource_identity_hash = resource_identity.identity_hash
            resource_id = resource_identity.legal_resource_id
            crosswalk_id = legal_resource_crosswalk_id(
                provider_id=provider_id,
                provider_resource_type=(
                    item_request.record.resource_type
                ),
                provider_official_identifier=(
                    item_request.record.official_identifier
                ),
                provider_jurisdiction_code=(
                    item_request.record.jurisdiction_code
                ),
            )
            lineage_contract_sha256 = _stable_hash(
                {
                    "declaredPredecessorOfficialVersionIdentifier": (
                        item_request.record.predecessor_official_version_identifier
                    ),
                    "effectiveContinuityPolicy": (
                        item_request.record.effective_continuity_policy
                    ),
                    "effectiveGapReason": (
                        item_request.record.effective_gap_reason
                    ),
                }
            )
            version_id = _deterministic_id(
                "legal-resource-version",
                {
                    "legalResourceId": resource_id,
                    "normalizedSha256": normalized_sha256,
                },
            )
            identity = (resource_id, version_id)
            if identity in record_identities:
                raise ValueError(
                    "a legal corpus page cannot contain duplicate records"
                )
            if resource_id in resource_ids:
                raise ValueError(
                    "a legal corpus page may contain only one version per "
                    "canonical resource"
                )
            if item_request.record.official_identifier in official_identifiers:
                raise ValueError(
                    "page source attestations require unique official identifiers"
                )
            record_identities.add(identity)
            resource_ids.add(resource_id)
            official_identifiers.add(
                item_request.record.official_identifier
            )
            prepared.append(
                {
                    "request": item_request,
                    "observedAt": observed_at,
                    "normalizedSha256": normalized_sha256,
                    "sourceVerification": source_verification,
                    "resourceIdentityHash": resource_identity_hash,
                    "crossRevisionKey": (
                        resource_identity.cross_revision_key
                    ),
                    "resourceCrosswalkId": crosswalk_id,
                    "lineageContractSha256": lineage_contract_sha256,
                    "resourceId": resource_id,
                    "versionId": version_id,
                }
            )

        page_hash = _stable_hash(
            {
                "records": sorted(
                    (
                        {
                            "legalResourceId": item["resourceId"],
                            "legalResourceVersionId": item["versionId"],
                            "normalizedSha256": item["normalizedSha256"],
                            "sourceVerificationSha256": item[
                                "sourceVerification"
                            ]["contractSha256"],
                            "lineageContractSha256": item[
                                "lineageContractSha256"
                            ],
                        }
                        for item in prepared
                    ),
                    key=lambda item: (
                        item["legalResourceId"],
                        item["legalResourceVersionId"],
                    ),
                )
            }
        )
        requested_scope = sanitize_requested_scope(
            request.requested_scope
        )
        if request.coverage_identity is None:
            requested_scope_hash = _stable_hash(
                {
                    "providerId": provider_id,
                    "requestedScope": requested_scope,
                }
            )
            coverage_identity_payload = None
        else:
            request.coverage_identity.assert_provider(
                provider_key=request.provider_key,
                provider_revision=request.provider_revision,
                provider_id=provider_id,
            )
            requested_scope_hash = request.coverage_identity.scope_hash(
                requested_scope=requested_scope
            )
            coverage_identity_payload = dict(
                request.coverage_identity.as_dict()
            )
        checkpoint_before = sanitize_checkpoint(request.checkpoint_before)
        checkpoint_after = sanitize_checkpoint(request.checkpoint_after)
        default_idempotency_key = "legal-corpus:" + _stable_hash(
            {
                "providerId": provider_id,
                "syncKind": request.sync_kind,
                "requestedScope": requested_scope,
                "checkpointBefore": checkpoint_before,
                "checkpointAfter": checkpoint_after,
                "requestFingerprint": request_fingerprint,
                "rawSha256": raw_sha256,
                "pageHash": page_hash,
                "rawContentType": request.raw_content_type,
                "responseStatus": request.response_status,
                "providerEtag": _redact_optional(request.provider_etag),
                "providerLastModified": _redact_optional(
                    request.provider_last_modified
                ),
                # A new acquisition remains a distinct auditable fetch even if
                # the official bytes and normalized page are unchanged.
                "sourceFetchedAt": _datetime_text(fetched_at),
            }
        )
        idempotency_key = (
            _redact_text(request.idempotency_key)
            if request.idempotency_key
            else default_idempotency_key
        )
        sync_run_id = _deterministic_id(
            "legal-sync-run",
            {
                "providerId": provider_id,
                "idempotencyKey": idempotency_key,
            },
        )
        source_fetch_id = _deterministic_id(
            "legal-source-fetch",
            {"kind": "source-fetch", "syncRunId": sync_run_id},
        )
        input_hash = _stable_hash(
            {
                "providerId": provider_id,
                "idempotencyKey": idempotency_key,
                "requestFingerprint": request_fingerprint,
                "rawSha256": raw_sha256,
                "pageHash": page_hash,
                "transport": request.transport,
                "syncKind": request.sync_kind,
                "rawContentType": request.raw_content_type,
                "requestedScope": requested_scope,
                "checkpointBefore": checkpoint_before,
                "checkpointAfter": checkpoint_after,
                "responseStatus": request.response_status,
                "providerEtag": _redact_optional(request.provider_etag),
                "providerLastModified": _redact_optional(
                    request.provider_last_modified
                ),
                "sourceFetchedAt": _datetime_text(fetched_at),
            }
        )

        provider = LegalSourceProviderRecord(
            provider_id=provider_id,
            provider_key=request.provider_key,
            provider_revision=request.provider_revision,
            source_authority=request.source_authority,
            authority_tier=request.authority_tier,
            provider_kind=request.provider_kind,
            canonical_base_url=(
                sanitize_source_locator(request.canonical_base_url)
                if request.canonical_base_url
                else None
            ),
            terms_version=request.terms_version,
            adapter_version=request.adapter_version,
            configured_by="legal-corpus-sync-worker",
            configured_at=requested_at,
            metadata=redact_secrets(request.provider_metadata),
        )
        run = LegalSyncRunRecord(
            sync_run_id=sync_run_id,
            provider=provider,
            sync_kind=request.sync_kind,
            requested_scope=requested_scope,
            requested_scope_hash=requested_scope_hash,
            checkpoint_before=checkpoint_before,
            page_hash=page_hash,
            page_record_count=len(prepared),
            idempotency_key=idempotency_key,
            input_hash=input_hash,
            requested_by=request.requested_by,
            lease_owner_id=request.lease_owner_id or request.requested_by,
            lease_ttl_seconds=request.lease_ttl_seconds,
            requested_at=requested_at,
            metadata={
                "pageHash": page_hash,
                "pageRecordCount": len(prepared),
                "rawSha256": raw_sha256,
                "coverageIdentity": coverage_identity_payload,
                "normalizedSha256ByVersion": {
                    item["versionId"]: item["normalizedSha256"]
                    for item in prepared
                },
            },
            coverage_contract_id=(
                request.coverage_identity.coverage_contract_id
                if request.coverage_identity is not None
                else None
            ),
        )

        expected_records = tuple(
            (item["resourceId"], item["versionId"]) for item in prepared
        )
        started = self._repository.begin_sync(run)
        if started.status == "completed":
            return self._repository.get_completed_page(
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                page_hash=page_hash,
                expected_records=expected_records,
            )
        if started.lease is None:
            raise LegalCorpusRepositoryError(
                "running sync did not return a fenced lease"
            )

        try:
            object_key = (
                f"legal-corpus/raw/{provider_id}/{raw_sha256[:2]}/"
                f"{raw_sha256}"
            )
            receipt = self._raw_object_store.put_if_absent(
                object_key=object_key,
                content=request.raw_content,
                content_type=request.raw_content_type,
                expected_sha256=raw_sha256,
                metadata={
                    "provider-id": provider_id,
                    "sync-run-id": sync_run_id,
                    "sha256": raw_sha256,
                },
            )
            _validate_receipt(
                receipt,
                expected_object_key=object_key,
                expected_sha256=raw_sha256,
                expected_size=len(request.raw_content),
            )

            verified_at = _not_before(
                _aware(self._clock(), "clock"),
                fetched_at,
                "verified_at",
            )
            record_publications: list[CorpusRecordPublication] = []
            for item in prepared:
                item_request = item["request"]
                source_verification = item["sourceVerification"]
                resource = LegalResourceRecord(
                    legal_resource_id=item["resourceId"],
                    provider_id=provider_id,
                    cross_revision_key=item["crossRevisionKey"],
                    resource_type=item_request.record.resource_type,
                    official_identifier=(
                        item_request.record.canonical_official_identifier
                        or item_request.record.official_identifier
                    ),
                    jurisdiction_code=item_request.record.jurisdiction_code,
                    issuing_authority=item_request.record.issuing_authority,
                    identity_hash=item["resourceIdentityHash"],
                    first_observed_at=item["observedAt"],
                    metadata={
                        **redact_secrets(
                            item_request.record.resource_metadata
                        ),
                        "identitySchemaVersion": (
                            IDENTITY_SCHEMA_VERSION
                        ),
                        "identityProviderKey": (
                            item_request.provider_key
                        ),
                    },
                )
                crosswalk = LegalResourceCrosswalkRecord(
                    legal_resource_crosswalk_id=(
                        item["resourceCrosswalkId"]
                    ),
                    legal_resource_id=item["resourceId"],
                    provider_id=provider_id,
                    first_observed_source_fetch_id=source_fetch_id,
                    provider_resource_type=(
                        item_request.record.resource_type
                    ),
                    provider_official_identifier=(
                        item_request.record.official_identifier
                    ),
                    provider_jurisdiction_code=(
                        item_request.record.jurisdiction_code
                    ),
                    cross_revision_key=item["crossRevisionKey"],
                    observed_at=item["observedAt"],
                    metadata={
                        "adapterVersion": item_request.adapter_version,
                        "providerRevision": (
                            item_request.provider_revision
                        ),
                    },
                )
                version = LegalResourceVersionRecord(
                    legal_resource_version_id=item["versionId"],
                    legal_resource_id=item["resourceId"],
                    provider_id=provider_id,
                    source_fetch_id=source_fetch_id,
                    official_version_identifier=(
                        item_request.record.official_version_identifier
                    ),
                    canonical_title=item_request.record.canonical_title,
                    alternative_titles_text=" | ".join(
                        title.strip()
                        for title in item_request.record.alternative_titles
                        if title.strip()
                    ),
                    publication_date=item_request.record.publication_date,
                    promulgation_date=item_request.record.promulgation_date,
                    effective_from=item_request.record.effective_from,
                    effective_to=item_request.record.effective_to,
                    valid_time_start=(
                        item_request.record.valid_time_start
                        or item_request.record.effective_from
                    ),
                    valid_time_end=(
                        item_request.record.valid_time_end
                        or item_request.record.effective_to
                    ),
                    observed_at=item["observedAt"],
                    fetched_at=fetched_at,
                    verified_at=verified_at,
                    verification_status=(
                        item_request.record.verification_status
                    ),
                    normalized_sha256=item["normalizedSha256"],
                    parser_schema_version=(
                        item_request.record.parser_schema_version
                    ),
                    declared_predecessor_official_version_identifier=(
                        item_request.record.predecessor_official_version_identifier
                    ),
                    effective_continuity_policy=(
                        item_request.record.effective_continuity_policy
                    ),
                    effective_gap_reason=(
                        item_request.record.effective_gap_reason
                    ),
                    metadata={
                        "resourceMetadata": redact_secrets(
                            item_request.record.resource_metadata
                        ),
                        **redact_secrets(
                            item_request.record.version_metadata
                        ),
                        "sourceVerification": source_verification,
                    },
                )
                record_publications.append(
                    CorpusRecordPublication(
                        resource=resource,
                        crosswalk=crosswalk,
                        version=version,
                        provisions=_provision_records(
                            item_request.record.provisions,
                            resource_id=item["resourceId"],
                            version_id=item["versionId"],
                        ),
                    )
                )

            source_verifications = [
                item["sourceVerification"] for item in prepared
            ]
            fetch_metadata: dict[str, Any] = {
                **redact_secrets(request.fetch_metadata),
                "coverageIdentity": coverage_identity_payload,
                "legalResourceIds": [
                    item["resourceId"] for item in prepared
                ],
                "legalResourceVersionIds": [
                    item["versionId"] for item in prepared
                ],
                "normalizedSha256ByVersion": {
                    item["versionId"]: item["normalizedSha256"]
                    for item in prepared
                },
                "pageHash": page_hash,
                "pageRecordCount": len(prepared),
                "sourceVerifications": source_verifications,
            }
            if len(prepared) == 1:
                fetch_metadata.update(
                    {
                        "legalResourceId": prepared[0]["resourceId"],
                        "legalResourceVersionId": prepared[0]["versionId"],
                        "normalizedSha256": prepared[0][
                            "normalizedSha256"
                        ],
                        "sourceVerification": source_verifications[0],
                    }
                )
            fetch = SourceFetchRecord(
                source_fetch_id=source_fetch_id,
                provider_id=provider_id,
                sync_run_id=sync_run_id,
                transport=request.transport,
                endpoint_key=request.endpoint_key,
                sanitized_source_locator=sanitized_locator,
                request_fingerprint=request_fingerprint,
                response_status=request.response_status,
                raw_object_key=receipt.object_key,
                raw_object_version=receipt.object_version,
                raw_sha256=raw_sha256,
                raw_size_bytes=receipt.size_bytes,
                response_content_type=request.raw_content_type,
                provider_etag=_redact_optional(request.provider_etag),
                provider_last_modified=_redact_optional(
                    request.provider_last_modified
                ),
                fetched_at=fetched_at,
                metadata=fetch_metadata,
            )
            lease = self._repository.renew_lease(
                started.lease,
                occurred_at=verified_at,
                lease_ttl_seconds=request.lease_ttl_seconds,
            )
            completed_at = _not_before(
                _aware(self._clock(), "clock"),
                verified_at,
                "completed_at",
            )
            return self._repository.publish_page(
                CorpusPublication(
                    sync_run_id=sync_run_id,
                    provider_id=provider_id,
                    lease=lease,
                    fetch=fetch,
                    records=tuple(record_publications),
                    page_hash=page_hash,
                    checkpoint_before=checkpoint_before,
                    checkpoint_after=checkpoint_after,
                    completed_at=completed_at,
                )
            )
        except Exception as exc:
            try:
                self._repository.mark_failed(
                    sync_run_id=sync_run_id,
                    provider_id=provider_id,
                    error_code=_failure_code(exc),
                    occurred_at=_aware(self._clock(), "clock"),
                )
            except Exception as ledger_exc:  # preserve the causal failure
                add_note = getattr(exc, "add_note", None)
                if callable(add_note):
                    add_note(
                        "legal sync failure event could not be appended: "
                        f"{ledger_exc.__class__.__name__}"
                    )
            raise


def _validate_page_request_envelope(
    requests: Sequence[LegalCorpusSyncRequest],
) -> None:
    baseline = requests[0]
    baseline_hash = _stable_hash(_page_request_envelope(baseline))
    for request in requests[1:]:
        if request.raw_content != baseline.raw_content:
            raise ValueError(
                "all records in a page must share the same raw response bytes"
            )
        if not hmac.compare_digest(
            _stable_hash(_page_request_envelope(request)), baseline_hash
        ):
            raise ValueError(
                "all records in a page must share one provider/fetch/scope "
                "and lease envelope"
            )


def _page_request_envelope(
    request: LegalCorpusSyncRequest,
) -> Mapping[str, Any]:
    return {
        "providerKey": request.provider_key,
        "providerRevision": request.provider_revision,
        "sourceAuthority": request.source_authority,
        "authorityTier": request.authority_tier,
        "providerKind": request.provider_kind,
        "adapterVersion": request.adapter_version,
        "transport": request.transport,
        "endpointKey": request.endpoint_key,
        "sourceLocator": request.source_locator,
        "rawSha256": hashlib.sha256(request.raw_content).hexdigest(),
        "rawContentType": request.raw_content_type,
        "requestedScope": request.requested_scope,
        "requestedBy": request.requested_by,
        "coverageIdentity": (
            dict(request.coverage_identity.as_dict())
            if request.coverage_identity is not None
            else None
        ),
        "requestIdentity": request.request_identity,
        "canonicalBaseUrl": request.canonical_base_url,
        "termsVersion": request.terms_version,
        "syncKind": request.sync_kind,
        "checkpointBefore": request.checkpoint_before,
        "checkpointAfter": request.checkpoint_after,
        "idempotencyKey": request.idempotency_key,
        "responseStatus": request.response_status,
        "providerEtag": request.provider_etag,
        "providerLastModified": request.provider_last_modified,
        "sourceFetchedAt": _datetime_text(request.source_fetched_at),
        "leaseOwnerId": request.lease_owner_id,
        "leaseTtlSeconds": request.lease_ttl_seconds,
        "providerMetadata": request.provider_metadata,
        "fetchMetadata": request.fetch_metadata,
    }


def _verify_source_contract(
    request: LegalCorpusSyncRequest,
    *,
    raw_sha256: str,
) -> Mapping[str, Any]:
    """Validate authority, transport, schema, identity, and response bytes."""

    if (
        request.response_status is None
        or not 200 <= int(request.response_status) <= 299
    ):
        raise LegalCorpusSyncError(
            "verified corpus publication requires a 2xx source response"
        )
    if request.transport == "remote_mcp":
        raise LegalCorpusSyncError(
            "remote MCP transport cannot publish the verified corpus"
        )
    attestation = request.source_verification
    allowed_bases = VERIFICATION_BASES_BY_TRANSPORT[request.transport]
    if attestation.verification_basis not in allowed_bases:
        raise LegalCorpusSyncError(
            "verification_basis is not valid for the selected transport"
        )
    comparisons = {
        "response_sha256": (
            attestation.response_sha256,
            raw_sha256,
        ),
        "source_authority": (
            attestation.source_authority,
            request.source_authority,
        ),
        "official_identifier": (
            attestation.official_identifier,
            request.record.official_identifier,
        ),
        "parser_schema_version": (
            attestation.parser_schema_version,
            request.record.parser_schema_version,
        ),
    }
    mismatches = [
        name
        for name, (actual, expected) in comparisons.items()
        if actual != expected
    ]
    if mismatches:
        raise LegalCorpusSyncError(
            "source verification attestation mismatch: "
            + ", ".join(mismatches)
        )
    payload: dict[str, Any] = {
        "schemaVersion": "realytics-legal-source-verification-v1",
        "responseSha256": attestation.response_sha256,
        "sourceAuthority": attestation.source_authority,
        "officialIdentifier": attestation.official_identifier,
        "parserSchemaVersion": attestation.parser_schema_version,
        "schemaValidationStatus": (
            attestation.schema_validation_status
        ),
        "authorityValidationStatus": (
            attestation.authority_validation_status
        ),
        "verificationBasis": attestation.verification_basis,
        "verifierId": attestation.verifier_id,
        "verifierVersion": attestation.verifier_version,
        "transport": request.transport,
    }
    payload["contractSha256"] = _stable_hash(payload)
    return payload


def canonical_normalized_record(
    record: OfficialLegalRecordInput,
) -> Mapping[str, Any]:
    """Return the deterministic semantic payload used for version identity."""

    return {
        "resourceType": record.resource_type,
        # Provider-specific spellings belong in the revision crosswalk.  The
        # semantic version hash uses only the canonical official identity.
        "officialIdentifier": (
            record.canonical_official_identifier
            or record.official_identifier
        ).strip(),
        "officialVersionIdentifier": (
            record.official_version_identifier.strip()
        ),
        "jurisdictionCode": record.jurisdiction_code.strip(),
        "issuingAuthority": record.issuing_authority.strip(),
        "canonicalTitle": record.canonical_title.strip(),
        "alternativeTitles": sorted(
            {title.strip() for title in record.alternative_titles if title.strip()}
        ),
        "publicationDate": _date_text(record.publication_date),
        "promulgationDate": _date_text(record.promulgation_date),
        "effectiveFrom": _date_text(record.effective_from),
        "effectiveTo": _date_text(record.effective_to),
        "validTimeStart": _date_text(
            record.valid_time_start or record.effective_from
        ),
        "validTimeEnd": _date_text(
            record.valid_time_end or record.effective_to
        ),
        "parserSchemaVersion": record.parser_schema_version.strip(),
        "verificationStatus": record.verification_status,
        "resourceMetadata": redact_secrets(record.resource_metadata),
        "versionMetadata": redact_secrets(record.version_metadata),
        "provisions": [
            _canonical_provision(provision)
            for provision in _ordered_provisions(record.provisions)
        ],
    }


def sanitize_source_locator(value: str | None) -> str:
    """Remove credentials and sensitive query parameters from a locator."""

    text = _required(value, "source_locator")
    parsed = urlsplit(text)
    if parsed.scheme and parsed.netloc:
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        netloc = f"{hostname}{port}"
        safe_query = [
            (key, item)
            for key, item in parse_qsl(
                parsed.query, keep_blank_values=True
            )
            if not _is_sensitive_key(key)
        ]
        fragment = (
            ""
            if _contains_secret_assignment(parsed.fragment)
            else parsed.fragment
        )
        sanitized = urlunsplit(
            (
                parsed.scheme.lower(),
                netloc,
                parsed.path,
                urlencode(safe_query, doseq=True),
                fragment,
            )
        )
    else:
        sanitized = _redact_text(text)
    if not sanitized.strip():
        raise ValueError("source locator is empty after redaction")
    return sanitized


def sanitize_checkpoint(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "://" in text:
        return sanitize_source_locator(text)
    return _redact_text(text)[:2048]


def redact_secrets(value: Any) -> Any:
    """Recursively produce JSON-safe metadata with secret-bearing keys masked."""

    if isinstance(value, Mapping):
        return {
            str(key): (
                "***"
                if _is_sensitive_key(str(key))
                else redact_secrets(item)
            )
            for key, item in sorted(
                value.items(), key=lambda pair: str(pair[0])
            )
        }
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return (
            sanitize_source_locator(value)
            if "://" in value
            else _redact_text(value)
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _provision_records(
    provisions: tuple[OfficialProvisionInput, ...],
    *,
    resource_id: str,
    version_id: str,
) -> tuple[LegalProvisionRecord, ...]:
    ordered = _ordered_provisions(provisions)
    identifiers = {
        provision.provision_identifier: _deterministic_id(
            "legal-provision",
            {
                "legalResourceVersionId": version_id,
                "provisionIdentifier": provision.provision_identifier,
            },
        )
        for provision in ordered
    }
    return tuple(
        LegalProvisionRecord(
            legal_provision_id=identifiers[
                provision.provision_identifier
            ],
            legal_resource_version_id=version_id,
            legal_resource_id=resource_id,
            parent_provision_id=(
                identifiers[provision.parent_provision_identifier]
                if provision.parent_provision_identifier
                else None
            ),
            provision_type=provision.provision_type,
            provision_identifier=provision.provision_identifier,
            display_path=provision.display_path,
            ordinal_path=tuple(provision.ordinal_path),
            heading_text=(
                provision.heading_text.strip()
                if provision.heading_text
                else None
            ),
            body_text=str(provision.body_text or "").strip(),
            provision_sha256=_stable_hash(
                _canonical_provision(provision)
            ),
            source_locator=sanitize_source_locator(
                provision.source_locator
            ),
            metadata=redact_secrets(provision.metadata),
        )
        for provision in ordered
    )


def _canonical_provision(
    provision: OfficialProvisionInput,
) -> Mapping[str, Any]:
    # source_locator is transport evidence rather than legal text identity.
    # Endpoint/key rotation therefore does not manufacture a new legal version.
    return {
        "provisionType": provision.provision_type,
        "provisionIdentifier": provision.provision_identifier.strip(),
        "parentProvisionIdentifier": (
            provision.parent_provision_identifier.strip()
            if provision.parent_provision_identifier
            else None
        ),
        "displayPath": provision.display_path.strip(),
        "ordinalPath": list(provision.ordinal_path),
        "headingText": (
            provision.heading_text.strip()
            if provision.heading_text
            else None
        ),
        "bodyText": str(provision.body_text or "").strip(),
        "metadata": redact_secrets(provision.metadata),
    }


def _ordered_provisions(
    provisions: tuple[OfficialProvisionInput, ...],
) -> tuple[OfficialProvisionInput, ...]:
    by_id: dict[str, OfficialProvisionInput] = {}
    for provision in provisions:
        identifier = provision.provision_identifier
        if identifier in by_id:
            raise ValueError(
                f"duplicate provision identifier: {identifier}"
            )
        by_id[identifier] = provision
    for provision in provisions:
        parent = provision.parent_provision_identifier
        if parent and parent not in by_id:
            raise ValueError(
                f"missing parent provision: {parent}"
            )

    ordered: list[OfficialProvisionInput] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visited:
            return
        if identifier in visiting:
            raise ValueError("provision hierarchy contains a cycle")
        visiting.add(identifier)
        provision = by_id[identifier]
        if provision.parent_provision_identifier:
            visit(provision.parent_provision_identifier)
        visiting.remove(identifier)
        visited.add(identifier)
        ordered.append(provision)

    for identifier in sorted(
        by_id,
        key=lambda item: (
            by_id[item].ordinal_path,
            item,
        ),
    ):
        visit(identifier)
    return tuple(ordered)


def _validate_receipt(
    receipt: RawObjectReceipt,
    *,
    expected_object_key: str,
    expected_sha256: str,
    expected_size: int,
) -> None:
    if (
        receipt.object_key != expected_object_key
        or not str(receipt.object_version or "").strip()
        or str(receipt.sha256 or "").lower() != expected_sha256
        or receipt.size_bytes != expected_size
    ):
        raise RawObjectIntegrityError(
            "immutable object receipt failed key/version/hash/size attestation"
        )


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, RawObjectIntegrityError):
        return "RAW_OBJECT_INTEGRITY_FAILED"
    if isinstance(exc, LegalCorpusEffectiveTimeConflict):
        conflict_code = re.sub(
            r"[^A-Z0-9_]",
            "_",
            str(exc.code).upper(),
        ).strip("_")
        return f"EFFECTIVE_TIME_{conflict_code}"[:80]
    if isinstance(exc, LegalCorpusImmutableConflict):
        return "IMMUTABLE_IDENTITY_CONFLICT"
    if exc.__class__.__name__.lower().find("object") >= 0:
        return "RAW_OBJECT_STORE_FAILED"
    return "LEGAL_CORPUS_PUBLISH_FAILED"


def _deterministic_id(prefix: str, value: Any) -> str:
    return f"{prefix}:{_stable_hash(value)}"


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _required(value: Any, name: str) -> str:
    text = _canonical_text(value, name, allow_empty=True)
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _choice(value: str, choices: frozenset[str], name: str) -> None:
    if value not in choices:
        raise ValueError(
            f"{name} must be one of: {', '.join(sorted(choices))}"
        )


def _safe_key(value: str, name: str) -> None:
    text = _required(value, name)
    if not _SAFE_KEY_PATTERN.fullmatch(text):
        raise ValueError(f"{name} contains unsafe characters")


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _not_before(
    value: datetime, lower_bound: datetime, name: str
) -> datetime:
    if value < lower_bound:
        raise ValueError(f"{name} cannot precede its causal event")
    return value


def _date_range(
    start: date | None, end: date | None, name: str
) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError(f"{name} end cannot precede start")


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _canonical_text(
    value: Any,
    name: str,
    *,
    allow_empty: bool = False,
) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    if not text and not allow_empty:
        raise ValueError(f"{name} is required")
    return text


def _sha256(value: Any, name: str) -> str:
    digest = _canonical_text(value, name).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return digest


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _is_sensitive_key(value: str) -> bool:
    normalized = _normalized_key(value)
    return normalized in _SENSITIVE_KEYS or any(
        normalized.endswith(suffix)
        for suffix in (
            "password",
            "secret",
            "token",
            "apikey",
            "accesskey",
            "servicekey",
        )
    )


def _contains_secret_assignment(value: str) -> bool:
    return bool(
        re.search(
            r"(?i)(?:^|[?&#;\s])"
            r"(?:oc|key|token|secret|password|authorization|api[_-]?key|"
            r"access[_-]?token|service[_-]?key)\s*=",
            value,
        )
    )


def _redact_text(value: str) -> str:
    return re.sub(
        r"(?i)((?:^|[?&#;\s])"
        r"(?:oc|key|token|secret|password|authorization|api[_-]?key|"
        r"access[_-]?token|service[_-]?key)\s*=)[^?&#;\s]*",
        r"\1***",
        str(value),
    )


def sanitize_requested_scope(value: str) -> str:
    """Canonicalize and redact a scope before hashing or persistence."""

    return _redact_text(_canonical_text(value, "requested_scope"))


def _redact_optional(value: str | None) -> str | None:
    return _redact_text(value) if value is not None else None


__all__ = [
    "ImmutableRawObjectStore",
    "LegalCorpusSyncError",
    "LegalCorpusSyncRequest",
    "LegalCorpusSyncService",
    "OfficialLegalRecordInput",
    "OfficialProvisionInput",
    "OfficialSourceVerification",
    "RawObjectIntegrityError",
    "RawObjectReceipt",
    "canonical_normalized_record",
    "redact_secrets",
    "sanitize_checkpoint",
    "sanitize_requested_scope",
    "sanitize_source_locator",
]
