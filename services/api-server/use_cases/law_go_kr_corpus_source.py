"""Concrete Law.go.kr page source for the verified legal corpus pipeline.

The source owns acquisition, pagination, checkpoint continuity, immutable raw
page assembly, and source-verification metadata.  Resource-specific semantic
normalization remains an injected, versioned adapter: a search-list row alone
is never promoted into a verified corpus version.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from legal_intelligence.models import (
    LegalResourceKind,
    canonical_content_hash,
    redact_secret_text,
)
from legal_intelligence.corpus_coverage import (
    OfficialCorpusCoverageIdentity,
)
from legal_intelligence.law_go_kr_contracts import (
    LawGoKrExternalContractError,
    LawGoKrExternalPayloadAdapter,
)
from legal_intelligence.providers import (
    DEFAULT_PROVIDER_JSON_MAX_COLLECTION_ITEMS,
    DEFAULT_PROVIDER_JSON_MAX_DEPTH,
    DEFAULT_PROVIDER_JSON_MAX_NODES,
    DEFAULT_PROVIDER_MAX_RESPONSE_BYTES,
    LAW_GO_KR_TARGET_DESCRIPTORS,
    LawGoKrTargetDescriptor,
    ProviderResponseBoundaryError,
    _RESOURCE_ID_KEYS,
    _boundary_aware_retry_classifier,
    _first_text,
    _urllib_get_json,
    _validate_json_mapping_payload,
    _validate_provider_url,
    _validate_response_boundary_configuration,
)
from legal_intelligence.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    NoOpProviderDeadLetterSink,
    NoOpProviderQuarantineSink,
    NoOpProviderTelemetrySink,
    ProviderDeadLetterRecord,
    ProviderDeadLetterSink,
    ProviderHttpError,
    ProviderMetric,
    ProviderQuarantineRecord,
    ProviderQuarantineSink,
    ProviderRateLimiter,
    ProviderTelemetrySink,
    RetryDecision,
    RetryExhaustedError,
    RetryPolicy,
    classify_provider_error,
    resilient_call,
    shared_provider_rate_limiter,
)
from use_cases.legal_corpus_coordinator import (
    LegalCorpusCoordinationError,
    OfficialCorpusPage,
    OfficialCorpusPageDiagnostic,
)
from use_cases.legal_corpus_sync import (
    LegalCorpusSyncRequest,
    OfficialLegalRecordInput,
    OfficialSourceVerification,
    sanitize_checkpoint,
)


class LawGoKrCorpusSourceError(LegalCorpusCoordinationError):
    """The official page cannot be safely normalized or resumed."""


class LawGoKrListPageContract(BaseModel):
    """Normalized, target-specific view of one official list response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target: str = Field(pattern=r"^[A-Za-z0-9]+$")
    schema_version: str
    page: int = Field(ge=1)
    display: int = Field(ge=1, le=100)
    rows: tuple[Mapping[str, Any], ...]
    total_count: int | None = Field(default=None, ge=0)


@dataclass(frozen=True)
class LawGoKrRecordContext:
    descriptor: LawGoKrTargetDescriptor
    list_row: Mapping[str, Any]
    detail_payload: Mapping[str, Any]
    history_payload: Mapping[str, Any] | None
    requested_as_of_date: date | None
    observed_at: datetime
    list_page: int


LawGoKrRecordNormalizer = Callable[
    [LawGoKrRecordContext], OfficialLegalRecordInput
]
LawGoKrHistoryFetcher = Callable[
    [
        LawGoKrTargetDescriptor,
        Mapping[str, Any],
        Mapping[str, Any],
    ],
    Mapping[str, Any],
]


class LawGoKrOfficialCorpusPageSource:
    """Fetch Law.go.kr list/detail pages and emit fenced sync requests.

    A history fetcher is an explicit extension point because Law.go.kr history
    endpoints have resource-specific request/response contracts.  Until a
    fixture-backed history adapter is supplied, ``requested_as_of_date`` is
    context only and no verified historical applicability is asserted.
    """

    def __init__(
        self,
        *,
        oc: str,
        resource_kind: LegalResourceKind,
        query: str,
        requested_scope: str,
        requested_by: str,
        record_normalizer: LawGoKrRecordNormalizer,
        requested_as_of_date: date | None = None,
        display: int = 100,
        search_base_url: str = (
            "https://www.law.go.kr/DRF/lawSearch.do"
        ),
        detail_base_url: str = (
            "https://www.law.go.kr/DRF/lawService.do"
        ),
        allowed_hosts: tuple[str, ...] | frozenset[str] = (
            "law.go.kr",
            "www.law.go.kr",
            "open.law.go.kr",
        ),
        http_get_json: Callable[
            [str, float], Mapping[str, Any]
        ] | None = None,
        boundary_adapter: LawGoKrExternalPayloadAdapter | None = None,
        history_fetcher: LawGoKrHistoryFetcher | None = None,
        history_schema_version: str | None = None,
        timeout_seconds: float = 20.0,
        max_response_bytes: int = DEFAULT_PROVIDER_MAX_RESPONSE_BYTES,
        json_max_depth: int = DEFAULT_PROVIDER_JSON_MAX_DEPTH,
        json_max_collection_items: int = (
            DEFAULT_PROVIDER_JSON_MAX_COLLECTION_ITEMS
        ),
        json_max_nodes: int = DEFAULT_PROVIDER_JSON_MAX_NODES,
        retry_policy: RetryPolicy | None = None,
        retry_classifier: Callable[[Exception], RetryDecision] | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        rate_limiter: ProviderRateLimiter | None = None,
        dead_letter_sink: ProviderDeadLetterSink | None = None,
        quarantine_sink: ProviderQuarantineSink | None = None,
        telemetry: ProviderTelemetrySink | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        monotonic: Callable[[], float] = time.monotonic,
        provider_revision: int = 1,
        adapter_version: str = "realytics-law-go-kr-corpus-v1",
        coverage_identity: OfficialCorpusCoverageIdentity | None = None,
        terms_version: str | None = None,
        sync_kind: str = "incremental",
        lease_owner_id: str | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        descriptor = LAW_GO_KR_TARGET_DESCRIPTORS.get(resource_kind)
        if descriptor is None:
            raise ValueError(
                "resource kind has no registered Law.go.kr target contract"
            )
        if not oc.strip():
            raise ValueError("Law.go.kr OC is required")
        if not query.strip():
            raise ValueError("Law.go.kr corpus query is required")
        if not 1 <= display <= 100:
            raise ValueError("display must be between 1 and 100")
        if history_fetcher is not None and not str(
            history_schema_version or ""
        ).strip():
            raise ValueError(
                "history_fetcher requires a versioned history schema"
            )
        if history_fetcher is None and history_schema_version is not None:
            raise ValueError(
                "history_schema_version requires history_fetcher"
            )
        normalized_hosts = frozenset(
            str(host).strip().casefold()
            for host in allowed_hosts
            if str(host).strip()
        )
        if not normalized_hosts:
            raise ValueError("a non-empty Law.go.kr host allowlist is required")
        for label, url in (
            ("Law.go.kr search URL", search_base_url),
            ("Law.go.kr detail URL", detail_base_url),
        ):
            _validate_provider_url(
                url,
                allowed_hosts=normalized_hosts,
                require_https=True,
                label=label,
            )
        self._oc = oc.strip()
        self._descriptor = descriptor
        self._query = query.strip()
        self._requested_scope = requested_scope
        self._requested_by = requested_by
        self._record_normalizer = record_normalizer
        self._requested_as_of_date = requested_as_of_date
        self._display = display
        self._search_base_url = search_base_url.rstrip("?")
        self._detail_base_url = detail_base_url.rstrip("?")
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

        def validate_mapping_payload(
            payload: Mapping[str, Any],
        ) -> Mapping[str, Any]:
            return _validate_json_mapping_payload(
                payload,
                max_response_bytes=max_response_bytes,
                json_max_depth=json_max_depth,
                json_max_collection_items=json_max_collection_items,
                json_max_nodes=json_max_nodes,
            )

        def bounded_http_get_json(
            url: str,
            timeout: float,
        ) -> Mapping[str, Any]:
            payload = raw_http_get_json(url, timeout)
            return validate_mapping_payload(payload)

        self._http_get_json = bounded_http_get_json
        self._validate_mapping_payload = validate_mapping_payload
        self._retry = retry_policy or RetryPolicy(max_attempts=3)
        self._retry_classifier = _boundary_aware_retry_classifier(
            retry_classifier or classify_provider_error
        )
        self._circuit = circuit_breaker or CircuitBreaker()
        self._rate_limiter = (
            rate_limiter or shared_provider_rate_limiter()
        )
        self._dead_letters = (
            dead_letter_sink or NoOpProviderDeadLetterSink()
        )
        self._quarantine = (
            quarantine_sink or NoOpProviderQuarantineSink()
        )
        self._telemetry = telemetry or NoOpProviderTelemetrySink()
        self._sleep = sleep
        self._jitter = jitter
        self._monotonic = monotonic
        self._history_fetcher = history_fetcher
        self._boundary_adapter = (
            boundary_adapter
            or LawGoKrExternalPayloadAdapter(resource_kind)
        )
        registered_history_version = (
            self._boundary_adapter.contract.history_contract_version
        )
        if (
            history_fetcher is not None
            and history_schema_version != registered_history_version
        ):
            raise ValueError(
                "history_schema_version must match the registered "
                f"fixture-backed contract {registered_history_version}"
            )
        self._history_schema_version = history_schema_version
        self._provider_revision = provider_revision
        self._adapter_version = adapter_version
        self._terms_version = terms_version
        self._sync_kind = sync_kind
        self._lease_owner_id = lease_owner_id
        self._now = now
        self._query_hash = hashlib.sha256(
            self._query.encode("utf-8")
        ).hexdigest()
        boundary_contract_version = (
            f"{self._boundary_adapter.contract.list_contract_version}."
            f"{self._boundary_adapter.contract.detail_contract_version}."
            f"{self._boundary_adapter.contract.history_contract_version}"
        )
        self._coverage_identity = coverage_identity or (
            OfficialCorpusCoverageIdentity(
                provider_key="law-go-kr",
                provider_revision=provider_revision,
                registry_version="law-go-kr-uncertified-registry-v1",
                resource_kind=resource_kind.value,
                target=self._descriptor.list_target,
                query_sha256=self._query_hash,
                source_adapter_version=adapter_version,
                boundary_contract_version=boundary_contract_version,
                coverage_contract_version="law-go-kr-uncertified-v1",
            )
        )
        self._coverage_identity.assert_provider(
            provider_key="law-go-kr",
            provider_revision=provider_revision,
        )
        expected_coverage = {
            "resourceKind": resource_kind.value,
            "target": self._descriptor.list_target,
            "querySha256": self._query_hash,
            "sourceAdapterVersion": adapter_version,
            "boundaryContractVersion": boundary_contract_version,
        }
        actual_coverage = self._coverage_identity.as_dict()
        if any(
            actual_coverage[name] != value
            for name, value in expected_coverage.items()
        ):
            raise ValueError(
                "coverage identity does not match the Law.go.kr source"
            )

    def fetch_page(
        self,
        *,
        checkpoint_before: str | None,
    ) -> OfficialCorpusPage:
        checkpoint = sanitize_checkpoint(checkpoint_before)
        page_number = self._page_from_checkpoint(checkpoint)
        observed_at = self._aware_now()
        list_url = self._list_url(page_number)
        payload = self._provider_get(
            list_url,
            operation="corpus_list",
            target=self._descriptor.list_target,
        )
        list_page = self._parse_list_page(payload, page_number)
        if not list_page.rows:
            raise LawGoKrCorpusSourceError(
                "Law.go.kr returned an empty page; the previous page could "
                "not be certified terminal"
            )

        enriched: list[dict[str, Any]] = []
        diagnostics: list[OfficialCorpusPageDiagnostic] = []
        normalized_records: list[OfficialLegalRecordInput] = []
        for row in list_page.rows:
            official_identifier = _first_text(row, _RESOURCE_ID_KEYS)
            if not official_identifier:
                diagnostics.append(
                    OfficialCorpusPageDiagnostic(
                        code="LAW_GO_KR_LIST_IDENTITY_MISSING",
                        message=(
                            "List row has no stable official identifier."
                        ),
                        target=self._descriptor.list_target,
                    )
                )
                continue
            try:
                detail_payload = self._fetch_detail(
                    official_identifier
                )
                history_payload = self._fetch_history(
                    official_identifier=official_identifier,
                    list_row=row,
                    detail_payload=detail_payload,
                )
                record = self._record_normalizer(
                    LawGoKrRecordContext(
                        descriptor=self._descriptor,
                        list_row=dict(row),
                        detail_payload=dict(detail_payload),
                        history_payload=(
                            dict(history_payload)
                            if history_payload is not None
                            else None
                        ),
                        requested_as_of_date=(
                            self._requested_as_of_date
                        ),
                        observed_at=observed_at,
                        list_page=page_number,
                    )
                )
                if record.official_identifier != official_identifier:
                    raise ValueError(
                        "normalizer changed the official identifier"
                    )
                normalized_records.append(record)
                enriched.append(
                    {
                        "officialIdentifier": official_identifier,
                        "listRow": dict(row),
                        "detail": dict(detail_payload),
                        "history": (
                            dict(history_payload)
                            if history_payload is not None
                            else None
                        ),
                    }
                )
            except Exception as exc:
                retry = self._retry_classifier(exc)
                diagnostics.append(
                    OfficialCorpusPageDiagnostic(
                        code="LAW_GO_KR_RECORD_QUARANTINED",
                        message=(
                            "Record detail/history normalization failed: "
                            f"{type(exc).__name__}"
                        ),
                        retryable=retry.retryable,
                        official_identifier=official_identifier,
                        target=self._descriptor.detail_target,
                        retry_after_seconds=retry.retry_after_seconds,
                    )
                )

        if not normalized_records:
            codes = ", ".join(item.code for item in diagnostics)
            raise LawGoKrCorpusSourceError(
                "Law.go.kr page produced no publishable records "
                f"({codes or 'UNKNOWN_SCHEMA_FAILURE'})",
                diagnostics=tuple(diagnostics),
            )

        terminal = self._is_terminal(list_page)
        checkpoint_after = self._checkpoint(page_number + 1)
        raw_payload = {
            "schemaVersion": "realytics-law-go-kr-raw-page-v1",
            "target": self._descriptor.list_target,
            "listSchemaVersion": (
                self._boundary_adapter.contract.list_contract_version
            ),
            "detailSchemaVersion": (
                self._boundary_adapter.contract.detail_contract_version
            ),
            "historySchemaVersion": (
                self._history_schema_version
                if self._history_fetcher is not None
                else None
            ),
            "page": page_number,
            "display": self._display,
            "totalCount": list_page.total_count,
            "listResponse": dict(payload),
            "records": enriched,
        }
        raw_content = json.dumps(
            raw_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        raw_sha256 = hashlib.sha256(raw_content).hexdigest()
        page_idempotency_key = self._page_idempotency_key(
            raw_sha256=raw_sha256,
            checkpoint_before=checkpoint,
            checkpoint_after=checkpoint_after,
            page_number=page_number,
        )
        sanitized_list_url = redact_secret_text(
            list_url,
            secrets=(self._oc,),
        )

        requests = tuple(
            self._build_request(
                record=record,
                raw_content=raw_content,
                raw_sha256=raw_sha256,
                source_locator=sanitized_list_url,
                checkpoint_before=checkpoint,
                checkpoint_after=checkpoint_after,
                observed_at=observed_at,
                page_number=page_number,
                page_idempotency_key=page_idempotency_key,
            )
            for record in normalized_records
        )
        return OfficialCorpusPage(
            requests=requests,
            terminal=terminal,
            partial=bool(diagnostics),
            diagnostics=tuple(diagnostics),
        )

    def _build_request(
        self,
        *,
        record: OfficialLegalRecordInput,
        raw_content: bytes,
        raw_sha256: str,
        source_locator: str,
        checkpoint_before: str | None,
        checkpoint_after: str,
        observed_at: datetime,
        page_number: int,
        page_idempotency_key: str,
    ) -> LegalCorpusSyncRequest:
        authority = self._descriptor.source_authority.value
        verification = OfficialSourceVerification(
            response_sha256=raw_sha256,
            source_authority=authority,
            official_identifier=record.official_identifier,
            parser_schema_version=record.parser_schema_version,
            verification_basis="official_api_response",
            verifier_id="realytics.law_go_kr_corpus_source",
            verifier_version=self._adapter_version,
        )
        return LegalCorpusSyncRequest(
            provider_key="law-go-kr",
            provider_revision=self._provider_revision,
            source_authority=authority,
            authority_tier="official_primary",
            provider_kind="official_api",
            adapter_version=self._adapter_version,
            transport="direct_api",
            endpoint_key=(
                f"law_go_kr.{self._descriptor.list_target}.list_detail"
            ),
            source_locator=source_locator,
            raw_content=raw_content,
            raw_content_type="application/json",
            record=record,
            source_verification=verification,
            requested_scope=self._requested_scope,
            requested_by=self._requested_by,
            coverage_identity=self._coverage_identity,
            request_identity={
                "target": self._descriptor.list_target,
                "querySha256": self._query_hash,
                "page": page_number,
                "display": self._display,
            },
            canonical_base_url="https://www.law.go.kr",
            terms_version=self._terms_version,
            sync_kind=self._sync_kind,
            checkpoint_before=checkpoint_before,
            checkpoint_after=checkpoint_after,
            idempotency_key=page_idempotency_key,
            response_status=200,
            source_fetched_at=observed_at,
            lease_owner_id=self._lease_owner_id,
            provider_metadata={
                "aggregator": "law.go.kr",
                "sourceAuthority": authority,
                "listEndpoint": self._descriptor.list_endpoint,
                "detailEndpoint": self._descriptor.detail_endpoint,
                "historyEndpoint": self._descriptor.history_endpoint,
                "listSchemaVersion": (
                    self._boundary_adapter.contract.list_contract_version
                ),
                "detailSchemaVersion": (
                    self._boundary_adapter.contract.detail_contract_version
                ),
                "historySchemaVersion": self._history_schema_version,
            },
            fetch_metadata={
                "requestedAsOfDate": (
                    self._requested_as_of_date.isoformat()
                    if self._requested_as_of_date
                    else None
                ),
                "verifiedEffectiveDate": None,
                "observedAt": observed_at.isoformat(),
                "historyObserved": (
                    self._history_fetcher is not None
                ),
            },
        )

    def _parse_list_page(
        self,
        payload: Mapping[str, Any],
        page_number: int,
    ) -> LawGoKrListPageContract:
        try:
            external_page = self._boundary_adapter.parse_list(
                payload,
                requested_page=page_number,
                requested_display=self._display,
            )
        except LawGoKrExternalContractError as exc:
            self._quarantine.store(
                ProviderQuarantineRecord(
                    provider_id="law_go_kr_direct",
                    operation="corpus_list",
                    reason_code=(
                        "LAW_GO_KR_CORPUS_LIST_SCHEMA_MISMATCH"
                    ),
                    payload_digest=canonical_content_hash(payload),
                    target=self._descriptor.list_target,
                    occurred_at=self._aware_now(),
                )
            )
            raise LawGoKrCorpusSourceError(
                "Law.go.kr list response does not match the registered "
                f"{self._boundary_adapter.contract.list_contract_version} "
                "contract"
            ) from exc
        return LawGoKrListPageContract(
            target=self._descriptor.list_target,
            schema_version=(
                self._boundary_adapter.contract.list_contract_version
            ),
            page=page_number,
            display=self._display,
            rows=tuple(
                dict(row.raw_payload)
                for row in external_page.rows
            ),
            total_count=external_page.total_count,
        )

    def _is_terminal(self, page: LawGoKrListPageContract) -> bool:
        if page.total_count is not None:
            return page.page * page.display >= page.total_count
        return len(page.rows) < page.display

    def _fetch_detail(
        self,
        official_identifier: str,
    ) -> Mapping[str, Any]:
        params = {
            "OC": self._oc,
            "target": self._descriptor.detail_target,
            "type": "JSON",
            "ID": official_identifier,
        }
        url = (
            f"{self._detail_base_url}?"
            f"{urllib.parse.urlencode(params)}"
        )
        payload = self._provider_get(
            url,
            operation="corpus_detail",
            target=self._descriptor.detail_target,
        )
        if not isinstance(payload, Mapping) or not payload:
            raise LawGoKrCorpusSourceError(
                "Law.go.kr detail response is empty or not an object"
            )
        try:
            detail = self._boundary_adapter.parse_detail(
                payload,
                requested_official_identifier=official_identifier,
            )
        except LawGoKrExternalContractError as exc:
            self._quarantine.store(
                ProviderQuarantineRecord(
                    provider_id="law_go_kr_direct",
                    operation="corpus_detail",
                    reason_code=(
                        "LAW_GO_KR_CORPUS_DETAIL_SCHEMA_MISMATCH"
                    ),
                    payload_digest=canonical_content_hash(payload),
                    target=self._descriptor.detail_target,
                    occurred_at=self._aware_now(),
                )
            )
            raise LawGoKrCorpusSourceError(
                "Law.go.kr detail response does not match the registered "
                f"{self._boundary_adapter.contract.detail_contract_version} "
                "contract"
            ) from exc
        return detail.raw_payload

    def _fetch_history(
        self,
        *,
        official_identifier: str,
        list_row: Mapping[str, Any],
        detail_payload: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        if self._history_fetcher is None:
            return None
        try:
            raw_history = self._history_fetcher(
                self._descriptor,
                list_row,
                detail_payload,
            )
            bounded_history = self._validate_mapping_payload(raw_history)
            return self._boundary_adapter.parse_history(
                bounded_history,
                requested_official_identifier=official_identifier,
            ).raw_payload
        except ProviderResponseBoundaryError as exc:
            self._quarantine.store(
                ProviderQuarantineRecord(
                    provider_id="law_go_kr_direct",
                    operation="corpus_history",
                    reason_code=exc.reason_code,
                    payload_digest=exc.payload_digest,
                    target=self._descriptor.detail_target,
                    occurred_at=self._aware_now(),
                )
            )
            raise
        except LawGoKrExternalContractError as exc:
            self._quarantine.store(
                ProviderQuarantineRecord(
                    provider_id="law_go_kr_direct",
                    operation="corpus_history",
                    reason_code=(
                        "LAW_GO_KR_CORPUS_HISTORY_SCHEMA_MISMATCH"
                    ),
                    payload_digest=canonical_content_hash(bounded_history),
                    target=self._descriptor.detail_target,
                    occurred_at=self._aware_now(),
                )
            )
            raise LawGoKrCorpusSourceError(
                "Law.go.kr history response does not match the registered "
                f"{self._boundary_adapter.contract.history_contract_version} "
                "contract"
            ) from exc

    def _page_idempotency_key(
        self,
        *,
        raw_sha256: str,
        checkpoint_before: str | None,
        checkpoint_after: str,
        page_number: int,
    ) -> str:
        """Bind one page acquisition, not one row, to an immutable sync run.

        Identical response bytes replay the same page run after a crash. A
        correction changes the raw digest (or adapter revision) and therefore
        receives a new run identity while every record in one page shares the
        exact same key.
        """

        acquisition = {
            "schemaVersion": 1,
            "providerRevision": self._provider_revision,
            "adapterVersion": self._adapter_version,
            "target": self._descriptor.list_target,
            "querySha256": self._query_hash,
            "page": page_number,
            "checkpointBefore": checkpoint_before,
            "checkpointAfter": checkpoint_after,
            "rawSha256": raw_sha256,
        }
        encoded = json.dumps(
            acquisition,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "law-go-kr-page:" + hashlib.sha256(encoded).hexdigest()

    def _provider_get(
        self,
        url: str,
        *,
        operation: str,
        target: str,
    ) -> Mapping[str, Any]:
        """Call Law.go.kr with shared rate limiting and privacy-safe telemetry."""

        started_at = self._monotonic()
        locally_rate_limited = False

        def call() -> Mapping[str, Any]:
            nonlocal locally_rate_limited
            decision = self._rate_limiter.acquire(
                "law_go_kr_direct"
            )
            if not decision.allowed:
                locally_rate_limited = True
                raise ProviderHttpError(
                    429,
                    "local Law.go.kr provider rate limit exhausted",
                    retry_after=decision.retry_after_seconds,
                )
            return self._http_get_json(url, self._timeout_seconds)

        try:
            payload, attempts = resilient_call(
                call,
                retry_policy=self._retry,
                circuit_breaker=self._circuit,
                sleep=self._sleep,
                classify_error=self._retry_classifier,
                jitter=self._jitter,
            )
        except CircuitOpenError:
            self._dead_letters.enqueue(
                ProviderDeadLetterRecord(
                    provider_id="law_go_kr_direct",
                    operation=operation,
                    reason_code="LAW_GO_KR_CORPUS_CIRCUIT_OPEN",
                    # Opening the guarded delivery path is itself the first
                    # DLQ delivery attempt; the normalized table rejects zero.
                    attempts=1,
                    target=target,
                    occurred_at=self._aware_now(),
                )
            )
            self._record_provider_metric(
                operation=operation,
                outcome="circuit_open",
                attempts=0,
                target=target,
                started_at=started_at,
                rate_limited=locally_rate_limited,
            )
            raise
        except RetryExhaustedError as exc:
            reason_code = "LAW_GO_KR_CORPUS_FETCH_FAILED"
            if isinstance(exc.cause, ProviderResponseBoundaryError):
                reason_code = exc.cause.reason_code
                self._quarantine.store(
                    ProviderQuarantineRecord(
                        provider_id="law_go_kr_direct",
                        operation=operation,
                        reason_code=reason_code,
                        payload_digest=exc.cause.payload_digest,
                        target=target,
                        occurred_at=self._aware_now(),
                    )
                )
            self._dead_letters.enqueue(
                ProviderDeadLetterRecord(
                    provider_id="law_go_kr_direct",
                    operation=operation,
                    reason_code=reason_code,
                    attempts=exc.attempts,
                    target=target,
                    occurred_at=self._aware_now(),
                )
            )
            self._record_provider_metric(
                operation=operation,
                outcome="failed",
                attempts=exc.attempts,
                target=target,
                started_at=started_at,
                rate_limited=locally_rate_limited,
            )
            raise
        self._record_provider_metric(
            operation=operation,
            outcome="success",
            attempts=attempts,
            target=target,
            started_at=started_at,
            rate_limited=locally_rate_limited,
        )
        return payload

    def _record_provider_metric(
        self,
        *,
        operation: str,
        outcome: str,
        attempts: int,
        target: str,
        started_at: float,
        rate_limited: bool,
    ) -> None:
        self._telemetry.record(
            ProviderMetric(
                provider_id="law_go_kr_direct",
                operation=operation,
                outcome=outcome,
                latency_ms=max(
                    0.0,
                    (self._monotonic() - started_at) * 1000,
                ),
                attempts=attempts,
                rate_limited=rate_limited,
                tags={
                    "transport": "law_go_kr_direct_api",
                    "target": target,
                },
            )
        )

    def _list_url(self, page_number: int) -> str:
        params = {
            "OC": self._oc,
            "target": self._descriptor.list_target,
            "type": "JSON",
            "query": self._query,
            "display": str(self._display),
            "page": str(page_number),
        }
        return (
            f"{self._search_base_url}?"
            f"{urllib.parse.urlencode(params)}"
        )

    def _page_from_checkpoint(
        self,
        checkpoint: str | None,
    ) -> int:
        if checkpoint is None:
            return 1
        try:
            payload = json.loads(checkpoint)
        except json.JSONDecodeError as exc:
            raise LawGoKrCorpusSourceError(
                "Law.go.kr checkpoint is not valid JSON"
            ) from exc
        expected = {
            "schemaVersion": 1,
            "target": self._descriptor.list_target,
            "querySha256": self._query_hash,
        }
        if not isinstance(payload, Mapping) or any(
            payload.get(key) != value for key, value in expected.items()
        ):
            raise LawGoKrCorpusSourceError(
                "Law.go.kr checkpoint does not belong to this source scope"
            )
        next_page = payload.get("nextPage")
        if (
            not isinstance(next_page, int)
            or isinstance(next_page, bool)
            or next_page < 1
        ):
            raise LawGoKrCorpusSourceError(
                "Law.go.kr checkpoint nextPage is invalid"
            )
        return next_page

    def _checkpoint(self, next_page: int) -> str:
        return json.dumps(
            {
                "schemaVersion": 1,
                "target": self._descriptor.list_target,
                "querySha256": self._query_hash,
                "nextPage": next_page,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def _aware_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Law.go.kr source clock must be timezone-aware")
        return value


__all__ = [
    "LawGoKrCorpusSourceError",
    "LawGoKrHistoryFetcher",
    "LawGoKrListPageContract",
    "LawGoKrOfficialCorpusPageSource",
    "LawGoKrRecordContext",
    "LawGoKrRecordNormalizer",
]
