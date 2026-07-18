"""External legal provider adapters with explicit source and transport metadata."""

from __future__ import annotations

import ipaddress
import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Protocol

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
    redact_secret_text,
    sort_hits,
)
from .resilience import (
    CircuitBreaker,
    CircuitOpenError,
    RetryExhaustedError,
    RetryPolicy,
    resilient_call,
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
        http_get_json: Callable[[str, float], Mapping[str, Any]] | None = None,
        retry_policy: RetryPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
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
        self._http_get_json = http_get_json or (
            lambda url, timeout: _urllib_get_json(
                url,
                timeout,
                allowed_hosts=self._allowed_hosts,
            )
        )
        self._retry = retry_policy or RetryPolicy()
        self._circuit = circuit_breaker or CircuitBreaker()
        self._now = now

    def search(self, query: LegalSearchQuery) -> ProviderResult:
        if not _external_query_allowed(query):
            return _classification_blocked_result(
                provider_id=self.provider_id,
                transport=self.transport,
                classification=query.data_classification,
            )
        all_hits: list[SearchHit] = []
        diagnostics: list[ProviderDiagnostic] = []
        attempts = 0
        supported_request_count = 0
        provider_failure = False
        for resource_kind in query.resource_kinds:
            target = LAW_GO_KR_TARGETS.get(resource_kind)
            if target is None:
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
            supported_request_count += 1
            request_url = self._request_url(query, target)
            try:
                payload, call_attempts = resilient_call(
                    lambda: self._http_get_json(request_url, self._timeout_seconds),
                    retry_policy=self._retry,
                    circuit_breaker=self._circuit,
                )
                attempts += call_attempts
            except CircuitOpenError:
                diagnostics.append(
                    ProviderDiagnostic(
                        code="LAW_GO_KR_CIRCUIT_OPEN",
                        message="Law.go.kr provider circuit is open.",
                        retryable=True,
                        sanitized_url=redact_secret_text(request_url, secrets=(self._oc,)),
                    )
                )
                return self._failed_result(ProviderStatus.CIRCUIT_OPEN, diagnostics, attempts)
            except RetryExhaustedError as exc:
                provider_failure = True
                attempts += exc.attempts
                diagnostics.append(
                    ProviderDiagnostic(
                        code="LAW_GO_KR_LOOKUP_FAILED",
                        message=redact_secret_text(
                            f"Law.go.kr lookup failed: {type(exc.cause).__name__}: {exc.cause}",
                            secrets=(self._oc,),
                        ),
                        retryable=True,
                        sanitized_url=redact_secret_text(request_url, secrets=(self._oc,)),
                    )
                )
                continue
            parsed_hits, schema_recognized, rejected_rows = self._parse_hits(
                payload,
                target,
                resource_kind,
                query,
            )
            if not schema_recognized:
                provider_failure = True
                diagnostics.append(
                    ProviderDiagnostic(
                        code="LAW_GO_KR_SCHEMA_MISMATCH",
                        message=(
                            "Law.go.kr response did not contain the expected "
                            f"{target} result container; payload was quarantined."
                        ),
                        retryable=False,
                    )
                )
                continue
            if rejected_rows:
                diagnostics.append(
                    ProviderDiagnostic(
                        code="LAW_GO_KR_INVALID_ROWS_QUARANTINED",
                        message=(
                            f"{rejected_rows} Law.go.kr result row(s) lacked "
                            "a stable official identifier or title and were quarantined."
                        ),
                        retryable=False,
                    )
                )
            all_hits.extend(parsed_hits)
        ordered = sort_hits(all_hits, limit=query.limit)
        if supported_request_count == 0:
            return ProviderResult(
                provider_id=self.provider_id,
                status=ProviderStatus.SKIPPED,
                transport=self.transport,
                diagnostics=tuple(diagnostics),
                attempts=0,
            )
        if provider_failure and not ordered:
            return self._failed_result(ProviderStatus.FAILED, diagnostics, attempts)
        return ProviderResult(
            provider_id=self.provider_id,
            status=ProviderStatus.SUCCESS if ordered else ProviderStatus.NO_MATCH,
            transport=self.transport,
            hits=ordered,
            coverage=CoverageStatus.UNKNOWN,
            # Search-list rows are current transport observations, not
            # effective-dated/version-verified corpus snapshots.
            freshness=FreshnessStatus.UNKNOWN,
            diagnostics=tuple(diagnostics),
            attempts=max(1, attempts),
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
        target: str,
        resource_kind: LegalResourceKind,
        query: LegalSearchQuery,
    ) -> tuple[list[SearchHit], bool, int]:
        container = _find_search_container(payload)
        rows: Any = []
        schema_recognized = False
        for key in LAW_GO_KR_RESULT_KEYS[target]:
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
            locator = _safe_law_locator(locator_value, title=title) if locator_value else (
                f"https://www.law.go.kr/법령/{urllib.parse.quote(title)}"
            )
            for locator_key in _LOCATOR_KEYS:
                if locator_key in content:
                    content[locator_key] = locator
            attestation = build_attestation(
                content=content,
                source_authority=SourceAuthority.LAW_GO_KR,
                source_transport=self.transport,
                trust_level=TrustLevel.SOURCE_ATTESTED,
                resource_kind=resource_kind,
                official_resource_id=resource_id,
                official_version_id=_first_text(raw, ("법령일련번호", "versionId")),
                locator=locator,
                as_of=query.as_of,
                fetched_at=fetched_at,
                effective_from=_first_date(raw, ("시행일자", "선고일자", "effectiveDate")),
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
        post_json: Callable[[str, Mapping[str, Any], float], Mapping[str, Any]] | None = None,
        retry_policy: RetryPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
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
        self._post_json = post_json or _urllib_post_json
        self._retry = retry_policy or RetryPolicy()
        self._circuit = circuit_breaker or CircuitBreaker()

    def search(self, query: LegalSearchQuery) -> ProviderResult:
        if self._remote and not self._allow_remote:
            return ProviderResult(
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
        if self._remote and not _external_query_allowed(query):
            return _classification_blocked_result(
                provider_id=self.provider_id,
                transport=self.transport,
                classification=query.data_classification,
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
        try:
            response, attempts = resilient_call(
                lambda: self._post_json(self._url, payload, self._timeout_seconds),
                retry_policy=self._retry,
                circuit_breaker=self._circuit,
            )
        except CircuitOpenError:
            return self._failure("MCP_CIRCUIT_OPEN", "MCP provider circuit is open.", 0, ProviderStatus.CIRCUIT_OPEN)
        except RetryExhaustedError as exc:
            return self._failure(
                "MCP_LOOKUP_FAILED",
                redact_secret_text(f"MCP lookup failed: {type(exc.cause).__name__}: {exc.cause}"),
                exc.attempts,
                ProviderStatus.FAILED,
            )

        # Do not promote bridge payloads to legal evidence merely because the
        # bridge returned HTTP 200.  They remain visible as candidates.
        candidate_hits: list[SearchHit] = []
        raw_hits = response.get("hits", []) if isinstance(response, Mapping) else []
        if isinstance(raw_hits, Mapping):
            raw_hits = [raw_hits]
        for index, raw in enumerate(raw_hits if isinstance(raw_hits, list) else []):
            if not isinstance(raw, Mapping):
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
        return ProviderResult(
            provider_id=self.provider_id,
            status=ProviderStatus.SUCCESS if candidate_hits else ProviderStatus.NO_MATCH,
            transport=self.transport,
            hits=tuple(candidate_hits[: query.limit]),
            diagnostics=(),
            attempts=attempts,
        )

    def _failure(
        self,
        code: str,
        message: str,
        attempts: int,
        status: ProviderStatus,
    ) -> ProviderResult:
        return ProviderResult(
            provider_id=self.provider_id,
            status=status,
            transport=self.transport,
            diagnostics=(
                ProviderDiagnostic(
                    code=code,
                    message=redact_secret_text(message),
                    retryable=True,
                    sanitized_url=redact_secret_text(self._url),
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
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("Law.go.kr returned a non-object JSON payload")
    return value


def _urllib_post_json(
    url: str,
    payload: Mapping[str, Any],
    timeout: float,
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
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("MCP returned a non-object JSON payload")
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


def _safe_law_locator(value: str, *, title: str) -> str:
    canonical = (
        "https://www.law.go.kr/법령/"
        f"{urllib.parse.quote(title, safe='')}"
    )
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
        return canonical
    return candidate


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
