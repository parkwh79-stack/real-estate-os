"""External legal provider adapters with explicit source and transport metadata."""

from __future__ import annotations

import ipaddress
import hashlib
import json
import math
import random
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    CoverageStatus,
    DataClassification,
    FreshnessStatus,
    LegalResourceKind,
    LegalSearchQuery,
    ProviderDiagnostic,
    ProviderResult,
    ProviderStatus,
    SearchHit,
    SearchStage,
    SourceAuthority,
    SourceTransport,
    TrustLevel,
    build_attestation,
    canonical_content_hash,
    redact_secret_text,
    sort_hits,
)
from .resilience import (
    CircuitBreaker,
    CircuitOpenError,
    NoOpProviderDeadLetterSink,
    NoOpProviderQuarantineSink,
    NoOpProviderTelemetrySink,
    ProviderDeadLetterRecord,
    ProviderDeadLetterSink,
    ProviderMetric,
    ProviderQuarantineRecord,
    ProviderQuarantineSink,
    ProviderRateLimiter,
    ProviderTelemetrySink,
    RetryDecision,
    RetryExhaustedError,
    RetryPolicy,
    TokenBucketProviderRateLimiter,
    classify_provider_error,
    resilient_call,
)


DEFAULT_PROVIDER_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_CONFIGURED_PROVIDER_RESPONSE_BYTES = 32 * 1024 * 1024
DEFAULT_PROVIDER_JSON_MAX_DEPTH = 64
DEFAULT_PROVIDER_JSON_MAX_COLLECTION_ITEMS = 20_000
DEFAULT_PROVIDER_JSON_MAX_NODES = 100_000
_PROVIDER_READ_CHUNK_BYTES = 64 * 1024
_JSON_MEDIA_TYPES = frozenset({"application/json", "text/json"})
_XML_MEDIA_TYPES = frozenset(
    {
        "application/xml",
        "text/xml",
    }
)


class ProviderResponseBoundaryError(ValueError):
    """A privacy-safe, non-retryable response-boundary rejection."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        payload_digest: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.payload_digest = payload_digest or canonical_content_hash(
            {"reasonCode": reason_code}
        )


class ProviderProtocol(Protocol):
    provider_id: str
    transport: SourceTransport

    def search(self, query: LegalSearchQuery) -> ProviderResult: ...


# Law.go.kr search API targets are deliberately resource-specific.  Adding a
# new kind requires a contract test against the official API guide.
LAW_GO_KR_TARGETS: Mapping[LegalResourceKind, str] = {
    LegalResourceKind.STATUTE: "law",
    LegalResourceKind.PROVISION: "law",
    LegalResourceKind.PRECEDENT: "prec",
    LegalResourceKind.INTERPRETATION: "expc",
    LegalResourceKind.ADMINISTRATIVE_RULE: "admrul",
    LegalResourceKind.LOCAL_ORDINANCE: "ordin",
    LegalResourceKind.CONSTITUTIONAL_DECISION: "detc",
    # Law.go.kr exposes committee-specific targets (for example ftc, acr,
    # ppc, oclt, fsc); there is no authoritative generic committee target.
    LegalResourceKind.LEGAL_TERM: "lstrmAI",
}

LAW_GO_KR_RESULT_KEYS: Mapping[str, tuple[str, ...]] = {
    "law": ("law",),
    "prec": ("prec",),
    "expc": ("expc",),
    "admrul": ("admrul",),
    "ordin": ("ordin",),
    "detc": ("detc",),
    "lstrmAI": ("법령용어", "lstrmAI", "lstrm", "term"),
}

class LawGoKrTargetDescriptor(BaseModel):
    """Versioned external contract for one Law.go.kr resource family.

    The platform at law.go.kr is a transport/aggregator. ``source_authority``
    identifies the institution that issued the underlying legal material.
    History fields stay empty until a fixture-backed official endpoint
    contract is registered.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_kind: LegalResourceKind
    list_endpoint: str = "lawSearch.do"
    list_target: str = Field(pattern=r"^[A-Za-z0-9]+$")
    detail_endpoint: str = "lawService.do"
    detail_target: str = Field(pattern=r"^[A-Za-z0-9]+$")
    result_keys: tuple[str, ...]
    source_authority: SourceAuthority
    list_schema_version: str = "law-go-kr-list-v1"
    detail_schema_version: str = "law-go-kr-detail-v1"
    history_endpoint: str | None = None
    history_target: str | None = None
    history_schema_version: str | None = None


LAW_GO_KR_TARGET_DESCRIPTORS: Mapping[
    LegalResourceKind, LawGoKrTargetDescriptor
] = {
    LegalResourceKind.STATUTE: LawGoKrTargetDescriptor(
        resource_kind=LegalResourceKind.STATUTE,
        list_target="law",
        detail_target="law",
        result_keys=LAW_GO_KR_RESULT_KEYS["law"],
        source_authority=SourceAuthority.CENTRAL_GOVERNMENT,
    ),
    LegalResourceKind.PROVISION: LawGoKrTargetDescriptor(
        resource_kind=LegalResourceKind.PROVISION,
        list_target="law",
        detail_target="law",
        result_keys=LAW_GO_KR_RESULT_KEYS["law"],
        source_authority=SourceAuthority.CENTRAL_GOVERNMENT,
    ),
    LegalResourceKind.PRECEDENT: LawGoKrTargetDescriptor(
        resource_kind=LegalResourceKind.PRECEDENT,
        list_target="prec",
        detail_target="prec",
        result_keys=LAW_GO_KR_RESULT_KEYS["prec"],
        source_authority=SourceAuthority.COURT,
    ),
    LegalResourceKind.INTERPRETATION: LawGoKrTargetDescriptor(
        resource_kind=LegalResourceKind.INTERPRETATION,
        list_target="expc",
        detail_target="expc",
        result_keys=LAW_GO_KR_RESULT_KEYS["expc"],
        source_authority=SourceAuthority.CENTRAL_GOVERNMENT,
    ),
    LegalResourceKind.ADMINISTRATIVE_RULE: LawGoKrTargetDescriptor(
        resource_kind=LegalResourceKind.ADMINISTRATIVE_RULE,
        list_target="admrul",
        detail_target="admrul",
        result_keys=LAW_GO_KR_RESULT_KEYS["admrul"],
        source_authority=SourceAuthority.CENTRAL_GOVERNMENT,
    ),
    LegalResourceKind.LOCAL_ORDINANCE: LawGoKrTargetDescriptor(
        resource_kind=LegalResourceKind.LOCAL_ORDINANCE,
        list_target="ordin",
        detail_target="ordin",
        result_keys=LAW_GO_KR_RESULT_KEYS["ordin"],
        source_authority=SourceAuthority.LOCAL_GOVERNMENT,
    ),
    LegalResourceKind.CONSTITUTIONAL_DECISION: LawGoKrTargetDescriptor(
        resource_kind=LegalResourceKind.CONSTITUTIONAL_DECISION,
        list_target="detc",
        detail_target="detc",
        result_keys=LAW_GO_KR_RESULT_KEYS["detc"],
        source_authority=SourceAuthority.CONSTITUTIONAL_COURT,
    ),
    LegalResourceKind.LEGAL_TERM: LawGoKrTargetDescriptor(
        resource_kind=LegalResourceKind.LEGAL_TERM,
        list_target="lstrmAI",
        detail_target="lstrm",
        result_keys=LAW_GO_KR_RESULT_KEYS["lstrmAI"],
        source_authority=SourceAuthority.LAW_GO_KR,
    ),
}


DEFAULT_LAW_GO_KR_ALLOWED_HOSTS = frozenset(
    {
        "law.go.kr",
        "www.law.go.kr",
        "open.law.go.kr",
    }
)

_PRIVATE_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_PRIVATE_IPV6_NETWORK = ipaddress.ip_network("fc00::/7")

_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "auth",
        "authorization",
        "bearer",
        "clientsecret",
        "credential",
        "credentials",
        "key",
        "oc",
        "password",
        "passwd",
        "refreshtoken",
        "secret",
        "token",
    }
)
_SENSITIVE_QUERY_SUFFIXES = (
    "accesstoken",
    "apikey",
    "authorization",
    "clientsecret",
    "credential",
    "password",
    "refreshtoken",
    "secret",
    "token",
)

_RESOURCE_ID_KEYS = (
    "법령ID",
    "판례일련번호",
    "법령해석례일련번호",
    "행정규칙ID",
    "자치법규ID",
    "헌재결정례일련번호",
    "결정문일련번호",
    "법령용어ID",
    "id",
)
_TITLE_KEYS = (
    "법령명한글",
    "사건명",
    "안건명",
    "행정규칙명",
    "자치법규명",
    "법령용어명",
    "title",
)
_LOCATOR_KEYS = (
    "법령상세링크",
    "판례상세링크",
    "법령해석례상세링크",
    "행정규칙상세링크",
    "자치법규상세링크",
    "헌재결정례상세링크",
    "상세링크",
    "url",
)


class LawGoKrDirectProvider:
    """Official Law.go.kr adapter.

    The ``http_get_json`` injection keeps unit tests offline and makes the
    provider contract usable by either urllib or a managed HTTP client.
    """

    provider_id = "law_go_kr_direct"
    transport = SourceTransport.LAW_GO_KR_DIRECT_API

    def __init__(
        self,
        *,
        oc: str,
        base_url: str = "https://www.law.go.kr/DRF/lawSearch.do",
        allowed_hosts: tuple[str, ...] | frozenset[str] = (
            "law.go.kr",
            "www.law.go.kr",
            "open.law.go.kr",
        ),
        timeout_seconds: float = 10.0,
        max_response_bytes: int = DEFAULT_PROVIDER_MAX_RESPONSE_BYTES,
        json_max_depth: int = DEFAULT_PROVIDER_JSON_MAX_DEPTH,
        json_max_collection_items: int = (
            DEFAULT_PROVIDER_JSON_MAX_COLLECTION_ITEMS
        ),
        json_max_nodes: int = DEFAULT_PROVIDER_JSON_MAX_NODES,
        http_get_json: Callable[[str, float], Mapping[str, Any]] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_classifier: Callable[[Exception], RetryDecision] | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        rate_limiter: ProviderRateLimiter | None = None,
        quarantine_sink: ProviderQuarantineSink | None = None,
        dead_letter_sink: ProviderDeadLetterSink | None = None,
        telemetry: ProviderTelemetrySink | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not oc.strip():
            raise ValueError("Law.go.kr OC is required")
        normalized_hosts = frozenset(
            str(host).strip().casefold()
            for host in allowed_hosts
            if str(host).strip()
        )
        if not normalized_hosts:
            raise ValueError("Law.go.kr provider requires a non-empty host allowlist")
        _validate_provider_url(
            base_url,
            allowed_hosts=normalized_hosts,
            require_https=True,
            label="Law.go.kr base URL",
        )
        self._oc = oc.strip()
        self._base_url = base_url.rstrip("?")
        self._allowed_hosts = normalized_hosts
        self._timeout_seconds = timeout_seconds
        _validate_response_boundary_configuration(
            max_response_bytes=max_response_bytes,
            json_max_depth=json_max_depth,
            json_max_collection_items=json_max_collection_items,
            json_max_nodes=json_max_nodes,
        )
        raw_http_get_json = http_get_json or (
            lambda url, timeout: _urllib_get_json(
                url,
                timeout,
                allowed_hosts=self._allowed_hosts,
                max_response_bytes=max_response_bytes,
                json_max_depth=json_max_depth,
                json_max_collection_items=json_max_collection_items,
                json_max_nodes=json_max_nodes,
            )
        )

        def bounded_http_get_json(
            url: str,
            timeout: float,
        ) -> Mapping[str, Any]:
            payload = raw_http_get_json(url, timeout)
            return _validate_json_mapping_payload(
                payload,
                max_response_bytes=max_response_bytes,
                json_max_depth=json_max_depth,
                json_max_collection_items=json_max_collection_items,
                json_max_nodes=json_max_nodes,
            )

        self._http_get_json = bounded_http_get_json
        self._retry = retry_policy or RetryPolicy()
        self._retry_classifier = _boundary_aware_retry_classifier(
            retry_classifier or classify_provider_error
        )
        self._circuit = circuit_breaker or CircuitBreaker()
        self._rate_limiter = rate_limiter or TokenBucketProviderRateLimiter(
            capacity=30,
            refill_tokens_per_second=5,
        )
        self._quarantine = quarantine_sink or NoOpProviderQuarantineSink()
        self._dead_letters = dead_letter_sink or NoOpProviderDeadLetterSink()
        self._telemetry = telemetry or NoOpProviderTelemetrySink()
        self._sleep = sleep
        self._jitter = jitter
        self._monotonic = monotonic
        self._now = now

    def search(self, query: LegalSearchQuery) -> ProviderResult:
        started_at = self._monotonic()
        was_rate_limited = False

        def finish(result: ProviderResult) -> ProviderResult:
            self._telemetry.record(
                ProviderMetric(
                    provider_id=self.provider_id,
                    operation="search",
                    outcome=result.status.value,
                    latency_ms=max(0.0, (self._monotonic() - started_at) * 1000),
                    attempts=result.attempts,
                    rate_limited=was_rate_limited,
                    freshness=result.freshness.value,
                    tags={"transport": self.transport.value},
                )
            )
            return result

        if not _external_query_allowed(query):
            return finish(
                _classification_blocked_result(
                    provider_id=self.provider_id,
                    transport=self.transport,
                    classification=query.data_classification,
                )
            )
        all_hits: list[SearchHit] = []
        diagnostics: list[ProviderDiagnostic] = []
        attempts = 0
        supported_request_count = 0
        successful_request_count = 0
        provider_failure = False
        request_descriptors: list[LawGoKrTargetDescriptor] = []
        seen_targets: set[str] = set()
        for resource_kind in query.resource_kinds:
            descriptor = LAW_GO_KR_TARGET_DESCRIPTORS.get(resource_kind)
            if descriptor is None:
                diagnostics.append(
                    ProviderDiagnostic(
                        code="LAW_GO_KR_RESOURCE_KIND_UNSUPPORTED",
                        message=(
                            "Law.go.kr requires a committee-specific target; "
                            "generic committee-decision search was not sent."
                        ),
                        retryable=False,
                    )
                )
                continue
            if descriptor.list_target in seen_targets:
                continue
            seen_targets.add(descriptor.list_target)
            request_descriptors.append(descriptor)

        for descriptor in request_descriptors:
            target = descriptor.list_target
            supported_request_count += 1
            request_url = self._request_url(query, target)
            rate_limit = self._rate_limiter.acquire(self.provider_id)
            if not rate_limit.allowed:
                was_rate_limited = True
                provider_failure = True
                diagnostics.append(
                    ProviderDiagnostic(
                        code="LAW_GO_KR_CLIENT_RATE_LIMITED",
                        message=(
                            "Law.go.kr request was not sent because the local "
                            "provider rate limit was exhausted; retry after "
                            f"{rate_limit.retry_after_seconds:.3f} seconds."
                        ),
                        retryable=True,
                        target=target,
                        retry_after_seconds=rate_limit.retry_after_seconds,
                    )
                )
                continue
            try:
                payload, call_attempts = resilient_call(
                    lambda: self._http_get_json(request_url, self._timeout_seconds),
                    retry_policy=self._retry,
                    circuit_breaker=self._circuit,
                    sleep=self._sleep,
                    classify_error=self._retry_classifier,
                    jitter=self._jitter,
                )
                attempts += call_attempts
            except CircuitOpenError:
                diagnostics.append(
                    ProviderDiagnostic(
                        code="LAW_GO_KR_CIRCUIT_OPEN",
                        message="Law.go.kr provider circuit is open.",
                        retryable=True,
                        sanitized_url=redact_secret_text(request_url, secrets=(self._oc,)),
                        target=target,
                    )
                )
                return finish(
                    self._failed_result(
                        ProviderStatus.CIRCUIT_OPEN,
                        diagnostics,
                        attempts,
                    )
                )
            except RetryExhaustedError as exc:
                provider_failure = True
                attempts += exc.attempts
                reason_code = "LAW_GO_KR_LOOKUP_FAILED"
                diagnostic_message = redact_secret_text(
                    "Law.go.kr lookup failed: "
                    f"{type(exc.cause).__name__}: {exc.cause}",
                    secrets=(self._oc,),
                )
                if isinstance(exc.cause, ProviderResponseBoundaryError):
                    reason_code = exc.cause.reason_code
                    diagnostic_message = str(exc.cause)
                    self._quarantine.store(
                        ProviderQuarantineRecord(
                            provider_id=self.provider_id,
                            operation="search",
                            reason_code=reason_code,
                            payload_digest=exc.cause.payload_digest,
                            target=target,
                            occurred_at=self._now(),
                        )
                    )
                self._dead_letters.enqueue(
                    ProviderDeadLetterRecord(
                        provider_id=self.provider_id,
                        operation="search",
                        reason_code=reason_code,
                        attempts=exc.attempts,
                        target=target,
                        occurred_at=self._now(),
                    )
                )
                diagnostics.append(
                    ProviderDiagnostic(
                        code=reason_code,
                        message=diagnostic_message,
                        retryable=exc.retryable,
                        sanitized_url=redact_secret_text(request_url, secrets=(self._oc,)),
                        target=target,
                        retry_after_seconds=exc.retry_after_seconds,
                    )
                )
                continue
            parsed_hits, schema_recognized, rejected_rows = self._parse_hits(
                payload,
                descriptor,
                query,
            )
            if not schema_recognized:
                provider_failure = True
                self._quarantine.store(
                    ProviderQuarantineRecord(
                        provider_id=self.provider_id,
                        operation="search",
                        reason_code="LAW_GO_KR_SCHEMA_MISMATCH",
                        payload_digest=canonical_content_hash(payload),
                        target=target,
                        occurred_at=self._now(),
                    )
                )
                diagnostics.append(
                    ProviderDiagnostic(
                        code="LAW_GO_KR_SCHEMA_MISMATCH",
                        message=(
                            "Law.go.kr response did not contain the expected "
                            f"{target} result container; payload was quarantined."
                        ),
                        retryable=False,
                        target=target,
                    )
                )
                continue
            successful_request_count += 1
            if rejected_rows:
                self._quarantine.store(
                    ProviderQuarantineRecord(
                        provider_id=self.provider_id,
                        operation="search",
                        reason_code="LAW_GO_KR_INVALID_ROWS_QUARANTINED",
                        payload_digest=canonical_content_hash(payload),
                        target=target,
                        occurred_at=self._now(),
                    )
                )
                diagnostics.append(
                    ProviderDiagnostic(
                        code="LAW_GO_KR_INVALID_ROWS_QUARANTINED",
                        message=(
                            f"{rejected_rows} Law.go.kr result row(s) lacked "
                            "a stable official identifier or title and were quarantined."
                        ),
                        retryable=False,
                        target=target,
                    )
                )
            all_hits.extend(parsed_hits)
        ordered = sort_hits(all_hits, limit=query.limit)
        if supported_request_count == 0:
            return finish(
                ProviderResult(
                    provider_id=self.provider_id,
                    status=ProviderStatus.SKIPPED,
                    transport=self.transport,
                    diagnostics=tuple(diagnostics),
                    attempts=0,
                )
            )
        if provider_failure and successful_request_count == 0:
            return finish(
                self._failed_result(ProviderStatus.FAILED, diagnostics, attempts)
            )
        status = (
            ProviderStatus.PARTIAL
            if provider_failure
            else ProviderStatus.SUCCESS if ordered else ProviderStatus.NO_MATCH
        )
        return finish(
            ProviderResult(
                provider_id=self.provider_id,
                status=status,
                transport=self.transport,
                hits=ordered,
                coverage=(
                    CoverageStatus.PARTIAL
                    if provider_failure
                    else CoverageStatus.UNKNOWN
                ),
                # Search-list rows are current transport observations, not
                # effective-dated/version-verified corpus snapshots.
                freshness=FreshnessStatus.UNKNOWN,
                diagnostics=tuple(diagnostics),
                attempts=max(1, attempts),
            )
        )

    def _request_url(self, query: LegalSearchQuery, target: str) -> str:
        params = {
            "OC": self._oc,
            "target": target,
            "type": "JSON",
            "query": query.text,
            "display": str(query.limit),
        }
        return f"{self._base_url}?{urllib.parse.urlencode(params)}"

    def _parse_hits(
        self,
        payload: Mapping[str, Any],
        descriptor: LawGoKrTargetDescriptor,
        query: LegalSearchQuery,
    ) -> tuple[list[SearchHit], bool, int]:
        container = _find_search_container(payload)
        rows: Any = []
        schema_recognized = False
        for key in descriptor.result_keys:
            if isinstance(container, Mapping) and key in container:
                rows = container[key]
                schema_recognized = True
                break
        if not schema_recognized:
            return [], False, 0
        if isinstance(rows, Mapping):
            rows = [rows]
        if not isinstance(rows, list):
            return [], False, 0

        fetched_at = self._now()
        hits: list[SearchHit] = []
        rejected_rows = 0
        for index, raw in enumerate(rows):
            if not isinstance(raw, Mapping):
                rejected_rows += 1
                continue
            content = dict(raw)
            resource_id = _first_text(raw, _RESOURCE_ID_KEYS)
            title = _first_text(raw, _TITLE_KEYS)
            if not resource_id or not title:
                rejected_rows += 1
                continue
            locator_value = _first_text(raw, _LOCATOR_KEYS)
            unresolved_locator = (
                "law-go-kr-candidate:"
                f"{descriptor.list_target}:"
                f"{urllib.parse.quote(resource_id, safe='')}"
            )
            locator = (
                _safe_law_locator(
                    locator_value,
                    unresolved_locator=unresolved_locator,
                )
                if locator_value
                else unresolved_locator
            )
            for locator_key in _LOCATOR_KEYS:
                if locator_key in content:
                    content[locator_key] = locator
            attestation = build_attestation(
                content=content,
                source_authority=_source_authority_for_row(
                    descriptor,
                    raw,
                ),
                source_transport=self.transport,
                trust_level=TrustLevel.SOURCE_ATTESTED,
                resource_kind=descriptor.resource_kind,
                official_resource_id=resource_id,
                official_version_id=_first_text(raw, ("법령일련번호", "versionId")),
                locator=locator,
                requested_as_of_date=query.as_of,
                fetched_at=fetched_at,
                observed_at=fetched_at,
            )
            hits.append(
                SearchHit(
                    title=title,
                    excerpt=_first_text(raw, ("판시사항", "질의요지", "본문", "summary")),
                    stage=_official_match_stage(raw, title, query),
                    lexical_score=1.0 if title.replace(" ", "") == query.text.replace(" ", "") else 0.5,
                    content=content,
                    attestation=attestation,
                )
            )
        return hits, True, rejected_rows

    def _failed_result(
        self,
        status: ProviderStatus,
        diagnostics: list[ProviderDiagnostic],
        attempts: int,
    ) -> ProviderResult:
        return ProviderResult(
            provider_id=self.provider_id,
            status=status,
            transport=self.transport,
            diagnostics=tuple(diagnostics),
            attempts=attempts,
        )


class McpBridgeProvider:
    """Optional MCP tool transport.

    Remote bridges are fail-closed by default.  MCP responses are candidates
    unless every hit contains an official source attestation that can be
    validated by a higher-trust provider.
    """

    def __init__(
        self,
        *,
        url: str,
        remote: bool = False,
        allow_remote: bool = False,
        allowed_hosts: tuple[str, ...] | frozenset[str] = (),
        timeout_seconds: float = 10.0,
        max_response_bytes: int = DEFAULT_PROVIDER_MAX_RESPONSE_BYTES,
        json_max_depth: int = DEFAULT_PROVIDER_JSON_MAX_DEPTH,
        json_max_collection_items: int = (
            DEFAULT_PROVIDER_JSON_MAX_COLLECTION_ITEMS
        ),
        json_max_nodes: int = DEFAULT_PROVIDER_JSON_MAX_NODES,
        post_json: Callable[[str, Mapping[str, Any], float], Mapping[str, Any]] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_classifier: Callable[[Exception], RetryDecision] | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        rate_limiter: ProviderRateLimiter | None = None,
        quarantine_sink: ProviderQuarantineSink | None = None,
        dead_letter_sink: ProviderDeadLetterSink | None = None,
        telemetry: ProviderTelemetrySink | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        normalized_allowed_hosts = frozenset(
            str(host).strip().casefold()
            for host in allowed_hosts
            if str(host).strip()
        )
        if remote:
            _validate_provider_url(
                url,
                allowed_hosts=None,
                require_https=True,
                label="remote MCP URL",
            )
        else:
            _validate_trusted_internal_mcp_url(
                url,
                allowed_hosts=normalized_allowed_hosts,
            )
        self.provider_id = "remote_legal_mcp" if remote else "self_hosted_legal_mcp"
        self.transport = SourceTransport.REMOTE_MCP if remote else SourceTransport.SELF_HOSTED_MCP
        self._url = url
        self._remote = remote
        self._allow_remote = allow_remote
        self._timeout_seconds = timeout_seconds
        _validate_response_boundary_configuration(
            max_response_bytes=max_response_bytes,
            json_max_depth=json_max_depth,
            json_max_collection_items=json_max_collection_items,
            json_max_nodes=json_max_nodes,
        )
        raw_post_json = post_json or (
            lambda target_url, payload, timeout: _urllib_post_json(
                target_url,
                payload,
                timeout,
                max_response_bytes=max_response_bytes,
                json_max_depth=json_max_depth,
                json_max_collection_items=json_max_collection_items,
                json_max_nodes=json_max_nodes,
            )
        )

        def bounded_post_json(
            target_url: str,
            payload: Mapping[str, Any],
            timeout: float,
        ) -> Mapping[str, Any]:
            response = raw_post_json(target_url, payload, timeout)
            return _validate_json_mapping_payload(
                response,
                max_response_bytes=max_response_bytes,
                json_max_depth=json_max_depth,
                json_max_collection_items=json_max_collection_items,
                json_max_nodes=json_max_nodes,
            )

        self._post_json = bounded_post_json
        self._retry = retry_policy or RetryPolicy()
        self._retry_classifier = _boundary_aware_retry_classifier(
            retry_classifier or classify_provider_error
        )
        self._circuit = circuit_breaker or CircuitBreaker()
        self._rate_limiter = rate_limiter or TokenBucketProviderRateLimiter(
            capacity=30,
            refill_tokens_per_second=5,
        )
        self._quarantine = quarantine_sink or NoOpProviderQuarantineSink()
        self._dead_letters = dead_letter_sink or NoOpProviderDeadLetterSink()
        self._telemetry = telemetry or NoOpProviderTelemetrySink()
        self._sleep = sleep
        self._jitter = jitter
        self._monotonic = monotonic
        self._now = now

    def search(self, query: LegalSearchQuery) -> ProviderResult:
        started_at = self._monotonic()
        was_rate_limited = False

        def finish(result: ProviderResult) -> ProviderResult:
            self._telemetry.record(
                ProviderMetric(
                    provider_id=self.provider_id,
                    operation="search",
                    outcome=result.status.value,
                    latency_ms=max(0.0, (self._monotonic() - started_at) * 1000),
                    attempts=result.attempts,
                    rate_limited=was_rate_limited,
                    freshness=result.freshness.value,
                    tags={"transport": self.transport.value},
                )
            )
            return result

        if self._remote and not self._allow_remote:
            return finish(
                ProviderResult(
                    provider_id=self.provider_id,
                    status=ProviderStatus.SKIPPED,
                    transport=self.transport,
                    diagnostics=(
                        ProviderDiagnostic(
                            code="REMOTE_MCP_DISABLED",
                            message="Remote MCP is disabled by default.",
                        ),
                    ),
                    attempts=0,
                )
            )
        if self._remote and not _external_query_allowed(query):
            return finish(
                _classification_blocked_result(
                    provider_id=self.provider_id,
                    transport=self.transport,
                    classification=query.data_classification,
                )
            )
        payload = {
            "tool": "search",
            "arguments": {
                "query": query.text,
                "resourceKinds": [kind.value for kind in query.resource_kinds],
                "asOf": query.as_of.isoformat(),
                "limit": query.limit,
            },
        }
        rate_limit = self._rate_limiter.acquire(self.provider_id)
        if not rate_limit.allowed:
            was_rate_limited = True
            return finish(
                self._failure(
                    "MCP_CLIENT_RATE_LIMITED",
                    (
                        "MCP request was not sent because the local provider "
                        "rate limit was exhausted; retry after "
                        f"{rate_limit.retry_after_seconds:.3f} seconds."
                    ),
                    0,
                    ProviderStatus.FAILED,
                    retry_after_seconds=rate_limit.retry_after_seconds,
                )
            )
        try:
            response, attempts = resilient_call(
                lambda: self._post_json(self._url, payload, self._timeout_seconds),
                retry_policy=self._retry,
                circuit_breaker=self._circuit,
                sleep=self._sleep,
                classify_error=self._retry_classifier,
                jitter=self._jitter,
            )
        except CircuitOpenError:
            return finish(
                self._failure(
                    "MCP_CIRCUIT_OPEN",
                    "MCP provider circuit is open.",
                    0,
                    ProviderStatus.CIRCUIT_OPEN,
                )
            )
        except RetryExhaustedError as exc:
            reason_code = "MCP_LOOKUP_FAILED"
            diagnostic_message = redact_secret_text(
                f"MCP lookup failed: {type(exc.cause).__name__}: {exc.cause}"
            )
            if isinstance(exc.cause, ProviderResponseBoundaryError):
                reason_code = exc.cause.reason_code
                diagnostic_message = str(exc.cause)
                self._quarantine.store(
                    ProviderQuarantineRecord(
                        provider_id=self.provider_id,
                        operation="search",
                        reason_code=reason_code,
                        payload_digest=exc.cause.payload_digest,
                        occurred_at=self._now(),
                    )
                )
            self._dead_letters.enqueue(
                ProviderDeadLetterRecord(
                    provider_id=self.provider_id,
                    operation="search",
                    reason_code=reason_code,
                    attempts=exc.attempts,
                    occurred_at=self._now(),
                )
            )
            return finish(
                self._failure(
                    reason_code,
                    diagnostic_message,
                    exc.attempts,
                    ProviderStatus.FAILED,
                    retryable=exc.retryable,
                    retry_after_seconds=exc.retry_after_seconds,
                )
            )

        # Do not promote bridge payloads to legal evidence merely because the
        # bridge returned HTTP 200.  They remain visible as candidates.
        candidate_hits: list[SearchHit] = []
        raw_hits = response.get("hits", []) if isinstance(response, Mapping) else []
        if isinstance(raw_hits, Mapping):
            raw_hits = [raw_hits]
        invalid_rows = 0
        if not isinstance(response, Mapping) or not isinstance(raw_hits, list):
            invalid_rows = 1
            self._quarantine.store(
                ProviderQuarantineRecord(
                    provider_id=self.provider_id,
                    operation="search",
                    reason_code="MCP_SCHEMA_MISMATCH",
                    payload_digest=canonical_content_hash(response),
                    occurred_at=self._now(),
                )
            )
        for index, raw in enumerate(raw_hits if isinstance(raw_hits, list) else []):
            if not isinstance(raw, Mapping):
                invalid_rows += 1
                continue
            content = _mcp_candidate_content(raw)
            title = _bounded_text(
                raw.get("title")
                or raw.get("lawName")
                or f"MCP result {index + 1}",
                limit=500,
            )
            excerpt = _bounded_text(
                raw.get("excerpt") or raw.get("summary"),
                limit=4_000,
            )
            attestation = build_attestation(
                content=content,
                source_authority=SourceAuthority.UNKNOWN,
                source_transport=self.transport,
                trust_level=TrustLevel.UNTRUSTED_CANDIDATE,
                resource_kind=query.resource_kinds[0],
                official_resource_id=str(raw.get("officialResourceId") or f"mcp-candidate:{index}"),
                # Never expose an internal bridge URL or trust a response URL.
                # ``content`` is an allowlisted discovery projection; raw MCP
                # payloads belong in a separately access-controlled audit sink.
                # Promotion requires independent official source resolution.
                locator=f"mcp-candidate:{self.provider_id}:{index}",
                as_of=query.as_of,
            )
            candidate_hits.append(
                SearchHit(
                    title=title,
                    excerpt=excerpt or None,
                    stage=SearchStage.SEMANTIC,
                    lexical_score=0.0,
                    content=content,
                    attestation=attestation,
                )
            )
        if invalid_rows:
            self._quarantine.store(
                ProviderQuarantineRecord(
                    provider_id=self.provider_id,
                    operation="search",
                    reason_code="MCP_INVALID_ROWS_QUARANTINED",
                    payload_digest=canonical_content_hash(response),
                    occurred_at=self._now(),
                )
            )
        status = (
            ProviderStatus.PARTIAL
            if invalid_rows and candidate_hits
            else ProviderStatus.FAILED
            if invalid_rows
            else ProviderStatus.SUCCESS
            if candidate_hits
            else ProviderStatus.NO_MATCH
        )
        diagnostics = (
            (
                ProviderDiagnostic(
                    code="MCP_INVALID_ROWS_QUARANTINED",
                    message=(
                        f"{invalid_rows} MCP result row(s) were rejected by "
                        "the discovery-candidate contract."
                    ),
                    retryable=False,
                ),
            )
            if invalid_rows
            else ()
        )
        return finish(
            ProviderResult(
                provider_id=self.provider_id,
                status=status,
                transport=self.transport,
                hits=tuple(candidate_hits[: query.limit]),
                coverage=(
                    CoverageStatus.PARTIAL
                    if invalid_rows
                    else CoverageStatus.UNKNOWN
                ),
                diagnostics=diagnostics,
                attempts=attempts,
            )
        )

    def _failure(
        self,
        code: str,
        message: str,
        attempts: int,
        status: ProviderStatus,
        *,
        retryable: bool = True,
        retry_after_seconds: float | None = None,
    ) -> ProviderResult:
        return ProviderResult(
            provider_id=self.provider_id,
            status=status,
            transport=self.transport,
            diagnostics=(
                ProviderDiagnostic(
                    code=code,
                    message=redact_secret_text(message),
                    retryable=retryable,
                    sanitized_url=redact_secret_text(self._url),
                    retry_after_seconds=retry_after_seconds,
                ),
            ),
            attempts=attempts,
        )


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects before urllib can contact the redirect target."""

    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, msg, headers, newurl
        raise ValueError(
            f"Legal provider redirects are not allowed (HTTP {code})."
        )


def _open_without_redirect(
    request: urllib.request.Request,
    *,
    timeout: float,
) -> Any:
    opener = urllib.request.build_opener(_RejectRedirectHandler())
    return opener.open(request, timeout=timeout)


def _urllib_get_json(
    url: str,
    timeout: float,
    *,
    allowed_hosts: frozenset[str] = DEFAULT_LAW_GO_KR_ALLOWED_HOSTS,
    max_response_bytes: int = DEFAULT_PROVIDER_MAX_RESPONSE_BYTES,
    json_max_depth: int = DEFAULT_PROVIDER_JSON_MAX_DEPTH,
    json_max_collection_items: int = (
        DEFAULT_PROVIDER_JSON_MAX_COLLECTION_ITEMS
    ),
    json_max_nodes: int = DEFAULT_PROVIDER_JSON_MAX_NODES,
) -> Mapping[str, Any]:
    _validate_provider_url(
        url,
        allowed_hosts=allowed_hosts,
        require_https=True,
        label="Law.go.kr request URL",
        allowed_sensitive_query_keys=frozenset({"oc"}),
    )
    request = urllib.request.Request(url, headers={"User-Agent": "REALYTICS/legal-intelligence-v1"})
    with _open_without_redirect(request, timeout=timeout) as response:
        _validate_provider_url(
            response.geturl(),
            allowed_hosts=allowed_hosts,
            require_https=True,
            label="Law.go.kr response URL",
            allowed_sensitive_query_keys=frozenset({"oc"}),
        )
        return _read_bounded_json_mapping(
            response,
            allowed_media_families=("json",),
            max_response_bytes=max_response_bytes,
            json_max_depth=json_max_depth,
            json_max_collection_items=json_max_collection_items,
            json_max_nodes=json_max_nodes,
        )


def _urllib_post_json(
    url: str,
    payload: Mapping[str, Any],
    timeout: float,
    *,
    max_response_bytes: int = DEFAULT_PROVIDER_MAX_RESPONSE_BYTES,
    json_max_depth: int = DEFAULT_PROVIDER_JSON_MAX_DEPTH,
    json_max_collection_items: int = (
        DEFAULT_PROVIDER_JSON_MAX_COLLECTION_ITEMS
    ),
    json_max_nodes: int = DEFAULT_PROVIDER_JSON_MAX_NODES,
) -> Mapping[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "REALYTICS/legal-intelligence-v1",
        },
        method="POST",
    )
    with _open_without_redirect(request, timeout=timeout) as response:
        return _read_bounded_json_mapping(
            response,
            allowed_media_families=("json",),
            max_response_bytes=max_response_bytes,
            json_max_depth=json_max_depth,
            json_max_collection_items=json_max_collection_items,
            json_max_nodes=json_max_nodes,
        )


def _validate_response_boundary_configuration(
    *,
    max_response_bytes: int,
    json_max_depth: int,
    json_max_collection_items: int,
    json_max_nodes: int,
) -> None:
    if not 1 <= max_response_bytes <= MAX_CONFIGURED_PROVIDER_RESPONSE_BYTES:
        raise ValueError(
            "max_response_bytes must be between 1 and "
            f"{MAX_CONFIGURED_PROVIDER_RESPONSE_BYTES}"
        )
    if not 1 <= json_max_depth <= 256:
        raise ValueError("json_max_depth must be between 1 and 256")
    if not 1 <= json_max_collection_items <= 1_000_000:
        raise ValueError(
            "json_max_collection_items must be between 1 and 1000000"
        )
    if not 1 <= json_max_nodes <= 2_000_000:
        raise ValueError("json_max_nodes must be between 1 and 2000000")


def _boundary_aware_retry_classifier(
    classifier: Callable[[Exception], RetryDecision],
) -> Callable[[Exception], RetryDecision]:
    def classify(error: Exception) -> RetryDecision:
        if isinstance(error, ProviderResponseBoundaryError):
            return RetryDecision(
                retryable=False,
                counts_toward_circuit=False,
            )
        return classifier(error)

    return classify


def _response_header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    value = headers.get(name) if headers is not None else None
    if value is None:
        getheader = getattr(response, "getheader", None)
        if callable(getheader):
            value = getheader(name)
    if value is None:
        return None
    return str(value).strip()


def _media_type_matches_family(media_type: str, family: str) -> bool:
    normalized = media_type.casefold()
    if family == "json":
        return (
            normalized in _JSON_MEDIA_TYPES
            or normalized.endswith("+json")
            and normalized.startswith("application/")
        )
    if family == "xml":
        return (
            normalized in _XML_MEDIA_TYPES
            or normalized.endswith("+xml")
            and normalized.startswith("application/")
        )
    return False


def _read_bounded_response_bytes(
    response: Any,
    *,
    allowed_media_families: tuple[str, ...],
    max_response_bytes: int,
) -> bytes:
    content_type_header = _response_header(response, "Content-Type")
    if not content_type_header:
        raise ProviderResponseBoundaryError(
            "PROVIDER_RESPONSE_CONTENT_TYPE_MISSING",
            "Provider response was rejected because Content-Type is missing.",
        )
    media_type = content_type_header.split(";", 1)[0].strip().casefold()
    if not any(
        _media_type_matches_family(media_type, family)
        for family in allowed_media_families
    ):
        raise ProviderResponseBoundaryError(
            "PROVIDER_RESPONSE_CONTENT_TYPE_REJECTED",
            "Provider response was rejected because its media type is not "
            "allowed for this endpoint contract.",
        )

    content_encoding = _response_header(response, "Content-Encoding")
    if content_encoding and content_encoding.casefold() != "identity":
        raise ProviderResponseBoundaryError(
            "PROVIDER_RESPONSE_CONTENT_ENCODING_REJECTED",
            "Provider response was rejected because encoded response bodies "
            "are not allowed at this boundary.",
        )

    content_length_header = _response_header(response, "Content-Length")
    if content_length_header is not None:
        try:
            content_length = int(content_length_header)
        except ValueError as exc:
            raise ProviderResponseBoundaryError(
                "PROVIDER_RESPONSE_CONTENT_LENGTH_INVALID",
                "Provider response was rejected because Content-Length is "
                "invalid.",
            ) from exc
        if content_length < 0:
            raise ProviderResponseBoundaryError(
                "PROVIDER_RESPONSE_CONTENT_LENGTH_INVALID",
                "Provider response was rejected because Content-Length is "
                "invalid.",
            )
        if content_length > max_response_bytes:
            raise ProviderResponseBoundaryError(
                "PROVIDER_RESPONSE_TOO_LARGE",
                "Provider response exceeded the configured byte limit.",
                payload_digest=canonical_content_hash(
                    {
                        "reasonCode": "PROVIDER_RESPONSE_TOO_LARGE",
                        "declaredBytes": content_length,
                        "limitBytes": max_response_bytes,
                    }
                ),
            )

    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        remaining = max_response_bytes + 1 - total_bytes
        if remaining <= 0:
            raise ProviderResponseBoundaryError(
                "PROVIDER_RESPONSE_TOO_LARGE",
                "Provider response exceeded the configured byte limit.",
                payload_digest=_bytes_digest(b"".join(chunks)),
            )
        chunk = response.read(min(_PROVIDER_READ_CHUNK_BYTES, remaining))
        if not chunk:
            break
        if not isinstance(chunk, (bytes, bytearray)):
            raise ProviderResponseBoundaryError(
                "PROVIDER_RESPONSE_BODY_INVALID",
                "Provider response body was not a byte stream.",
            )
        chunk_bytes = bytes(chunk)
        chunks.append(chunk_bytes)
        total_bytes += len(chunk_bytes)
        if total_bytes > max_response_bytes:
            raise ProviderResponseBoundaryError(
                "PROVIDER_RESPONSE_TOO_LARGE",
                "Provider response exceeded the configured byte limit.",
                payload_digest=_bytes_digest(b"".join(chunks)),
            )
    body = b"".join(chunks)
    if (
        content_length_header is not None
        and int(content_length_header) != len(body)
    ):
        raise ProviderResponseBoundaryError(
            "PROVIDER_RESPONSE_CONTENT_LENGTH_MISMATCH",
            "Provider response was rejected because the received byte count "
            "did not match Content-Length.",
            payload_digest=_bytes_digest(body),
        )
    return body


def _bytes_digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _read_bounded_json_mapping(
    response: Any,
    *,
    allowed_media_families: tuple[str, ...],
    max_response_bytes: int,
    json_max_depth: int,
    json_max_collection_items: int,
    json_max_nodes: int,
) -> Mapping[str, Any]:
    _validate_response_boundary_configuration(
        max_response_bytes=max_response_bytes,
        json_max_depth=json_max_depth,
        json_max_collection_items=json_max_collection_items,
        json_max_nodes=json_max_nodes,
    )
    body = _read_bounded_response_bytes(
        response,
        allowed_media_families=allowed_media_families,
        max_response_bytes=max_response_bytes,
    )
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProviderResponseBoundaryError(
            "PROVIDER_RESPONSE_UTF8_INVALID",
            "Provider response was rejected because it is not valid UTF-8.",
            payload_digest=_bytes_digest(body),
        ) from exc

    def reject_duplicate_keys(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ProviderResponseBoundaryError(
                    "PROVIDER_RESPONSE_JSON_DUPLICATE_KEY",
                    "Provider response was rejected because JSON object keys "
                    "must be unique.",
                    payload_digest=_bytes_digest(body),
                )
            result[key] = value
        return result

    def reject_non_finite(value: str) -> None:
        raise ProviderResponseBoundaryError(
            "PROVIDER_RESPONSE_JSON_NON_FINITE",
            "Provider response was rejected because non-finite JSON numbers "
            "are not allowed.",
            payload_digest=_bytes_digest(body),
        )

    try:
        value = json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite,
        )
    except ProviderResponseBoundaryError:
        raise
    except RecursionError as exc:
        raise ProviderResponseBoundaryError(
            "PROVIDER_RESPONSE_JSON_DEPTH_LIMIT",
            "Provider response exceeded the configured JSON depth limit.",
            payload_digest=_bytes_digest(body),
        ) from exc
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ProviderResponseBoundaryError(
            "PROVIDER_RESPONSE_JSON_INVALID",
            "Provider response was rejected because it is not valid JSON.",
            payload_digest=_bytes_digest(body),
        ) from exc
    return _validate_json_mapping_structure(
        value,
        json_max_depth=json_max_depth,
        json_max_collection_items=json_max_collection_items,
        json_max_nodes=json_max_nodes,
        payload_digest=_bytes_digest(body),
    )


def _validate_json_mapping_payload(
    value: Any,
    *,
    max_response_bytes: int,
    json_max_depth: int,
    json_max_collection_items: int,
    json_max_nodes: int,
) -> Mapping[str, Any]:
    """Apply equivalent limits to managed-client/injected JSON results."""

    _validate_response_boundary_configuration(
        max_response_bytes=max_response_bytes,
        json_max_depth=json_max_depth,
        json_max_collection_items=json_max_collection_items,
        json_max_nodes=json_max_nodes,
    )
    validated = _validate_json_mapping_structure(
        value,
        json_max_depth=json_max_depth,
        json_max_collection_items=json_max_collection_items,
        json_max_nodes=json_max_nodes,
        payload_digest=canonical_content_hash(
            {"reasonCode": "PROVIDER_RESPONSE_JSON_STRUCTURE_REJECTED"}
        ),
    )
    try:
        encoded = json.dumps(
            validated,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except RecursionError as exc:
        raise ProviderResponseBoundaryError(
            "PROVIDER_RESPONSE_JSON_DEPTH_LIMIT",
            "Provider response exceeded the configured JSON depth limit.",
        ) from exc
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProviderResponseBoundaryError(
            "PROVIDER_RESPONSE_JSON_INVALID",
            "Provider response was rejected because it is not valid JSON.",
        ) from exc
    if len(encoded) > max_response_bytes:
        raise ProviderResponseBoundaryError(
            "PROVIDER_RESPONSE_TOO_LARGE",
            "Provider response exceeded the configured byte limit.",
            payload_digest=_bytes_digest(encoded[: max_response_bytes + 1]),
        )
    return validated


def _validate_json_mapping_structure(
    value: Any,
    *,
    json_max_depth: int,
    json_max_collection_items: int,
    json_max_nodes: int,
    payload_digest: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderResponseBoundaryError(
            "PROVIDER_RESPONSE_JSON_ROOT_INVALID",
            "Provider response was rejected because the JSON root is not an "
            "object.",
            payload_digest=payload_digest,
        )

    node_count = 0
    seen_containers: set[int] = set()
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        node_count += 1
        if node_count > json_max_nodes:
            raise ProviderResponseBoundaryError(
                "PROVIDER_RESPONSE_JSON_NODE_LIMIT",
                "Provider response exceeded the configured JSON node limit.",
                payload_digest=payload_digest,
            )
        if isinstance(current, Mapping):
            container_id = id(current)
            if container_id in seen_containers:
                raise ProviderResponseBoundaryError(
                    "PROVIDER_RESPONSE_JSON_GRAPH_INVALID",
                    "Provider response was rejected because JSON containers "
                    "must form a tree.",
                    payload_digest=payload_digest,
                )
            seen_containers.add(container_id)
            if depth > json_max_depth:
                raise ProviderResponseBoundaryError(
                    "PROVIDER_RESPONSE_JSON_DEPTH_LIMIT",
                    "Provider response exceeded the configured JSON depth "
                    "limit.",
                    payload_digest=payload_digest,
                )
            if len(current) > json_max_collection_items:
                raise ProviderResponseBoundaryError(
                    "PROVIDER_RESPONSE_JSON_COLLECTION_LIMIT",
                    "Provider response exceeded the configured JSON collection "
                    "limit.",
                    payload_digest=payload_digest,
                )
            for key, child in current.items():
                if not isinstance(key, str):
                    raise ProviderResponseBoundaryError(
                        "PROVIDER_RESPONSE_JSON_KEY_INVALID",
                        "Provider response was rejected because JSON object "
                        "keys must be strings.",
                        payload_digest=payload_digest,
                    )
                stack.append((child, depth + 1))
        elif isinstance(current, (list, tuple)):
            container_id = id(current)
            if container_id in seen_containers:
                raise ProviderResponseBoundaryError(
                    "PROVIDER_RESPONSE_JSON_GRAPH_INVALID",
                    "Provider response was rejected because JSON containers "
                    "must form a tree.",
                    payload_digest=payload_digest,
                )
            seen_containers.add(container_id)
            if depth > json_max_depth:
                raise ProviderResponseBoundaryError(
                    "PROVIDER_RESPONSE_JSON_DEPTH_LIMIT",
                    "Provider response exceeded the configured JSON depth "
                    "limit.",
                    payload_digest=payload_digest,
                )
            if len(current) > json_max_collection_items:
                raise ProviderResponseBoundaryError(
                    "PROVIDER_RESPONSE_JSON_COLLECTION_LIMIT",
                    "Provider response exceeded the configured JSON collection "
                    "limit.",
                    payload_digest=payload_digest,
                )
            stack.extend((child, depth + 1) for child in current)
        elif isinstance(current, float) and not math.isfinite(current):
            raise ProviderResponseBoundaryError(
                "PROVIDER_RESPONSE_JSON_NON_FINITE",
                "Provider response was rejected because non-finite JSON "
                "numbers are not allowed.",
                payload_digest=payload_digest,
            )
        elif current is not None and not isinstance(
            current,
            (str, int, float, bool),
        ):
            raise ProviderResponseBoundaryError(
                "PROVIDER_RESPONSE_JSON_VALUE_INVALID",
                "Provider response contains a non-JSON value.",
                payload_digest=payload_digest,
            )
    return value


def _validate_provider_url(
    value: str,
    *,
    allowed_hosts: frozenset[str] | None,
    require_https: bool,
    label: str,
    allowed_sensitive_query_keys: frozenset[str] = frozenset(),
) -> None:
    parsed = urllib.parse.urlsplit(str(value).strip())
    allowed_schemes = {"https"} if require_https else {"http", "https"}
    if parsed.scheme.casefold() not in allowed_schemes:
        expected = "https" if require_https else "http or https"
        raise ValueError(f"{label} must use {expected}")
    host = (parsed.hostname or "").casefold()
    if not host:
        raise ValueError(f"{label} must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{label} must not contain URL credentials")
    normalized_allowed_query_keys = frozenset(
        _normalize_query_key(key)
        for key in allowed_sensitive_query_keys
    )
    if any(
        _is_sensitive_query_key(key)
        and _normalize_query_key(key) not in normalized_allowed_query_keys
        for key, _value in urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
    ):
        raise ValueError(
            f"{label} must not contain credential query parameters"
        )
    if allowed_hosts is not None and host not in allowed_hosts:
        raise ValueError(f"{label} host is not in the configured allowlist")


def _validate_trusted_internal_mcp_url(
    value: str,
    *,
    allowed_hosts: frozenset[str],
) -> None:
    parsed = urllib.parse.urlsplit(str(value).strip())
    host = (parsed.hostname or "").casefold().rstrip(".")
    normalized_allowed_hosts = frozenset(
        item.casefold().rstrip(".")
        for item in allowed_hosts
    )
    if _is_loopback_or_private_literal(host):
        _validate_provider_url(
            value,
            allowed_hosts=frozenset({host}),
            require_https=False,
            label="self-hosted MCP URL",
        )
        return
    if host and host in normalized_allowed_hosts:
        _validate_provider_url(
            value,
            allowed_hosts=normalized_allowed_hosts,
            require_https=True,
            label="allowlisted self-hosted MCP URL",
        )
        return
    raise ValueError(
        "self-hosted MCP URL must use localhost/a private IP literal, or "
        "an explicitly allowlisted host over HTTPS"
    )


def _is_loopback_or_private_literal(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in network for network in _PRIVATE_IPV4_NETWORKS)
    if address.ipv4_mapped is not None:
        mapped = address.ipv4_mapped
        return mapped.is_loopback or any(
            mapped in network
            for network in _PRIVATE_IPV4_NETWORKS
        )
    return address in _PRIVATE_IPV6_NETWORK


def _normalize_query_key(value: Any) -> str:
    return "".join(
        character
        for character in str(value).casefold()
        if character.isalnum()
    )


def _is_sensitive_query_key(value: Any) -> bool:
    normalized = _normalize_query_key(value)
    return (
        normalized in _SENSITIVE_QUERY_KEYS
        or normalized.endswith(_SENSITIVE_QUERY_SUFFIXES)
    )


def _external_query_allowed(query: LegalSearchQuery) -> bool:
    return query.data_classification in {
        DataClassification.PUBLIC,
        DataClassification.INTERNAL,
    }


def _classification_blocked_result(
    *,
    provider_id: str,
    transport: SourceTransport,
    classification: DataClassification,
) -> ProviderResult:
    return ProviderResult(
        provider_id=provider_id,
        status=ProviderStatus.SKIPPED,
        transport=transport,
        coverage=CoverageStatus.UNKNOWN,
        freshness=FreshnessStatus.UNKNOWN,
        diagnostics=(
            ProviderDiagnostic(
                code="EXTERNAL_EGRESS_BLOCKED_BY_CLASSIFICATION",
                message=(
                    "External legal lookup was not called because the query "
                    f"is classified as {classification.value}."
                ),
                retryable=False,
            ),
        ),
        attempts=0,
    )


def _mcp_candidate_content(raw: Mapping[str, Any]) -> dict[str, str]:
    allowed_keys = (
        "title",
        "lawName",
        "excerpt",
        "summary",
        "citation",
        "article",
        "officialResourceId",
        "resourceKind",
        "effectiveDate",
    )
    return {
        key: text
        for key in allowed_keys
        if (text := _bounded_text(raw.get(key), limit=4_000))
    }


def _bounded_text(value: Any, *, limit: int) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _find_search_container(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for key, value in payload.items():
        if key.casefold().endswith("search") and isinstance(value, Mapping):
            return value
    return payload


def _first_text(row: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _first_date(row: Mapping[str, Any], keys: tuple[str, ...]) -> date | None:
    value = _first_text(row, keys)
    if not value:
        return None
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) >= 8:
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return None
    return None


def _safe_law_locator(
    value: str,
    *,
    unresolved_locator: str,
) -> str:
    candidate = urllib.parse.urljoin(
        "https://www.law.go.kr/",
        str(value).strip(),
    )
    try:
        _validate_provider_url(
            candidate,
            allowed_hosts=DEFAULT_LAW_GO_KR_ALLOWED_HOSTS,
            require_https=True,
            label="Law.go.kr result locator",
        )
    except ValueError:
        # Never synthesize a statute URL for a precedent, constitutional
        # decision, ordinance, or other resource family. The opaque locator
        # preserves target + official ID while making the missing exact source
        # URL explicit to downstream UI and approval logic.
        return unresolved_locator
    return candidate


def _source_authority_for_row(
    descriptor: LawGoKrTargetDescriptor,
    row: Mapping[str, Any],
) -> SourceAuthority:
    """Resolve a more specific issuer only when the list row attests it."""

    if descriptor.resource_kind != LegalResourceKind.PRECEDENT:
        return descriptor.source_authority
    court_name = _first_text(
        row,
        ("법원명", "재판기관명", "issuingAuthority", "courtName"),
    )
    if court_name and "대법원" in court_name.replace(" ", ""):
        return SourceAuthority.SUPREME_COURT
    return SourceAuthority.COURT


def _official_match_stage(
    row: Mapping[str, Any],
    title: str,
    query: LegalSearchQuery,
) -> SearchStage:
    row_values = {str(value).replace(" ", "").casefold() for value in row.values()}
    identifiers = {value.replace(" ", "").casefold() for value in query.identifiers}
    if identifiers & row_values:
        return SearchStage.EXACT_IDENTIFIER
    if title.replace(" ", "").casefold() == query.text.replace(" ", "").casefold():
        return SearchStage.EXACT_NAME
    return SearchStage.LEXICAL
