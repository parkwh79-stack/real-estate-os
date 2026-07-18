"""Typed legal-source, search, and citation contracts."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceAuthority(str, Enum):
    """Who published the underlying legal material.

    A local database and MCP are intentionally absent: both are transports.
    """

    LAW_GO_KR = "law_go_kr"
    SUPREME_COURT = "supreme_court"
    CONSTITUTIONAL_COURT = "constitutional_court"
    CENTRAL_GOVERNMENT = "central_government"
    LOCAL_GOVERNMENT = "local_government"
    PUBLIC_AGENCY = "public_agency"
    NATIONAL_TAX_SERVICE = "national_tax_service"
    TENANT_DOCUMENT = "tenant_document"
    HUMAN_COUNSEL = "human_counsel"
    CALLER_SUPPLIED = "caller_supplied"
    UNKNOWN = "unknown"


class SourceTransport(str, Enum):
    LOCAL_VERIFIED_SNAPSHOT = "local_verified_snapshot"
    LAW_GO_KR_DIRECT_API = "law_go_kr_direct_api"
    SELF_HOSTED_MCP = "self_hosted_mcp"
    REMOTE_MCP = "remote_mcp"
    TENANT_OBJECT_STORAGE = "tenant_object_storage"
    CALLER_CONTEXT = "caller_context"


class TrustLevel(str, Enum):
    UNTRUSTED_CANDIDATE = "untrusted_candidate"
    SOURCE_ATTESTED = "source_attested"
    VERIFIED_SNAPSHOT = "verified_snapshot"
    HUMAN_REVIEWED = "human_reviewed"


class LegalResourceKind(str, Enum):
    STATUTE = "statute"
    PROVISION = "provision"
    PRECEDENT = "precedent"
    INTERPRETATION = "interpretation"
    ADMINISTRATIVE_RULE = "administrative_rule"
    LOCAL_ORDINANCE = "local_ordinance"
    CONSTITUTIONAL_DECISION = "constitutional_decision"
    COMMITTEE_DECISION = "committee_decision"
    LEGAL_TERM = "legal_term"


class SearchStage(str, Enum):
    EXACT_IDENTIFIER = "exact_identifier"
    EXACT_NAME = "exact_name"
    APPROVED_ALIAS = "approved_alias"
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    RELATION_EXPANSION = "relation_expansion"
    CALLER_CANDIDATE = "caller_candidate"


SEARCH_STAGE_PRIORITY: dict[SearchStage, int] = {
    SearchStage.EXACT_IDENTIFIER: 0,
    SearchStage.EXACT_NAME: 1,
    SearchStage.APPROVED_ALIAS: 2,
    SearchStage.LEXICAL: 3,
    SearchStage.SEMANTIC: 4,
    SearchStage.RELATION_EXPANSION: 5,
    SearchStage.CALLER_CANDIDATE: 6,
}


class CoverageStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class FreshnessStatus(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class ProviderStatus(str, Enum):
    SUCCESS = "success"
    NO_MATCH = "no_match"
    SKIPPED = "skipped"
    FAILED = "failed"
    CIRCUIT_OPEN = "circuit_open"


class DataClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    PROJECT_CONFIDENTIAL = "project_confidential"
    LEGALLY_PRIVILEGED = "legally_privileged"


def canonical_content_hash(content: Any) -> str:
    """Return a stable SHA-256 over JSON-compatible source content."""

    encoded = json.dumps(
        content,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=_json_default,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return str(value)


class CitationAttestation(BaseModel):
    """Tamper-evident binding between a claim candidate and official material."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_authority: SourceAuthority
    source_transport: SourceTransport
    trust_level: TrustLevel
    resource_kind: LegalResourceKind
    official_resource_id: str = Field(min_length=1)
    official_version_id: str | None = None
    locator: str = Field(min_length=1)
    as_of: date
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    fetched_at: datetime
    verified_at: datetime | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    article: str | None = None

    @model_validator(mode="after")
    def _validate_temporal_and_trust_contract(self) -> "CitationAttestation":
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            raise ValueError("effective_from must not be after effective_to")
        if self.trust_level in {TrustLevel.VERIFIED_SNAPSHOT, TrustLevel.HUMAN_REVIEWED} and not self.verified_at:
            raise ValueError("verified snapshots and human-reviewed citations require verified_at")
        if self.source_authority == SourceAuthority.CALLER_SUPPLIED and self.trust_level != TrustLevel.UNTRUSTED_CANDIDATE:
            raise ValueError("caller-supplied material cannot be authoritative")
        return self

    def verifies(self, content: Any) -> bool:
        return canonical_content_hash(content) == self.content_hash

    @property
    def is_authoritative_evidence(self) -> bool:
        """Evidence authority, not a final legal opinion."""

        return (
            self.source_authority
            not in {SourceAuthority.CALLER_SUPPLIED, SourceAuthority.UNKNOWN}
            and self.trust_level
            in {
                TrustLevel.VERIFIED_SNAPSHOT,
                TrustLevel.HUMAN_REVIEWED,
            }
            and bool(self.official_resource_id)
            and bool(self.locator)
            and bool(self.content_hash)
        )


def build_attestation(
    *,
    content: Any,
    source_authority: SourceAuthority,
    source_transport: SourceTransport,
    trust_level: TrustLevel,
    resource_kind: LegalResourceKind,
    official_resource_id: str,
    locator: str,
    as_of: date,
    fetched_at: datetime | None = None,
    verified_at: datetime | None = None,
    official_version_id: str | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    article: str | None = None,
) -> CitationAttestation:
    now = fetched_at or datetime.now(timezone.utc)
    return CitationAttestation(
        source_authority=source_authority,
        source_transport=source_transport,
        trust_level=trust_level,
        resource_kind=resource_kind,
        official_resource_id=official_resource_id,
        official_version_id=official_version_id,
        locator=locator,
        as_of=as_of,
        content_hash=canonical_content_hash(content),
        fetched_at=now,
        verified_at=verified_at,
        effective_from=effective_from,
        effective_to=effective_to,
        article=article,
    )


class LegalSearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    resource_kinds: tuple[LegalResourceKind, ...] = (LegalResourceKind.STATUTE,)
    identifiers: tuple[str, ...] = ()
    approved_aliases: tuple[str, ...] = ()
    as_of: date = Field(default_factory=date.today)
    limit: int = Field(default=10, ge=1, le=100)
    data_classification: DataClassification = (
        DataClassification.PROJECT_CONFIDENTIAL
    )

    @model_validator(mode="after")
    def _normalize_contract(self) -> "LegalSearchQuery":
        if not self.text.strip():
            raise ValueError("text must contain a non-whitespace character")
        if not self.resource_kinds:
            raise ValueError("at least one resource kind is required")
        return self


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    excerpt: str | None = None
    stage: SearchStage
    lexical_score: float = Field(default=0.0, ge=0.0, le=1.0)
    content: Mapping[str, Any]
    attestation: CitationAttestation

    @property
    def is_authoritative_evidence(self) -> bool:
        return self.attestation.is_authoritative_evidence and self.attestation.verifies(self.content)


class ProviderDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    retryable: bool = False
    sanitized_url: str | None = None


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str
    status: ProviderStatus
    transport: SourceTransport
    hits: tuple[SearchHit, ...] = ()
    coverage: CoverageStatus = CoverageStatus.UNKNOWN
    freshness: FreshnessStatus = FreshnessStatus.UNKNOWN
    diagnostics: tuple[ProviderDiagnostic, ...] = ()
    attempts: int = Field(default=1, ge=0)

    @property
    def can_satisfy_from_verified_local_snapshot(self) -> bool:
        return (
            self.transport == SourceTransport.LOCAL_VERIFIED_SNAPSHOT
            and self.coverage == CoverageStatus.COMPLETE
            and self.freshness == FreshnessStatus.FRESH
            and all(hit.is_authoritative_evidence for hit in self.hits)
        )


class LegalSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: LegalSearchQuery
    authoritative_hits: tuple[SearchHit, ...]
    candidate_hits: tuple[SearchHit, ...]
    provider_trace: tuple[ProviderResult, ...]
    satisfied_by_local_snapshot: bool = False

    @model_validator(mode="after")
    def _enforce_authority_partition(self) -> "LegalSearchResponse":
        if any(not hit.is_authoritative_evidence for hit in self.authoritative_hits):
            raise ValueError("authoritative_hits may contain only verified, hash-bound evidence")
        return self


_SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?i)(?P<key>OC|api[_-]?key|access[_-]?token|refresh[_-]?token|token|"
    r"authorization|client[_-]?secret|password|passwd|credential|secret|key)"
    r"(?P<sep>\s*=\s*)(?P<value>[^&\s\"']+)"
)


def redact_secret_text(value: str, *, secrets: tuple[str, ...] = ()) -> str:
    """Redact credentials from URLs and exception text before persistence."""

    redacted = _SENSITIVE_QUERY_PATTERN.sub(
        lambda match: f"{match.group('key')}{match.group('sep')}[REDACTED]",
        str(value),
    )
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def sort_hits(hits: list[SearchHit], *, limit: int) -> tuple[SearchHit, ...]:
    ordered = sorted(
        hits,
        key=lambda hit: (
            SEARCH_STAGE_PRIORITY[hit.stage],
            -hit.lexical_score,
            hit.attestation.official_resource_id,
        ),
    )
    return tuple(ordered[:limit])
