from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

import legal_intelligence.providers as legal_providers
from legal_intelligence import (
    LAW_GO_KR_TARGETS,
    CoverageStatus,
    DataClassification,
    FreshnessStatus,
    InMemoryVerifiedSnapshotProvider,
    LawGoKrDirectProvider,
    LegalIntelligenceOrchestrator,
    LegalResourceKind,
    LegalSearchQuery,
    LegalSnapshot,
    McpBridgeProvider,
    ProviderResult,
    ProviderStatus,
    SourceAuthority,
    SourceTransport,
    TrustLevel,
    build_attestation,
)
from legal_intelligence.resilience import CircuitBreaker, RetryPolicy


NOW = datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)


def _snapshot(
    *,
    resource_id: str = "001234",
    title: str = "주택법",
    identifiers: tuple[str, ...] = ("법률 제001234호",),
    aliases: tuple[str, ...] = ("주법",),
    searchable_text: str = "주택건설사업계획 승인",
) -> LegalSnapshot:
    return LegalSnapshot(
        resource_kind=LegalResourceKind.STATUTE,
        official_resource_id=resource_id,
        official_version_id="20260718",
        title=title,
        content={"법령ID": resource_id, "법령명": title, "본문": searchable_text},
        locator=f"https://www.law.go.kr/법령/{title}",
        source_authority=SourceAuthority.LAW_GO_KR,
        identifiers=identifiers,
        approved_aliases=aliases,
        searchable_text=searchable_text,
        fetched_at=NOW - timedelta(hours=1),
        verified_at=NOW,
        effective_from=date(2026, 1, 1),
    )


class _FakeProvider:
    def __init__(self, result: ProviderResult) -> None:
        self.provider_id = result.provider_id
        self.transport = result.transport
        self.result = result
        self.calls = 0

    def search(self, query: LegalSearchQuery) -> ProviderResult:
        self.calls += 1
        return self.result


def _empty_result(
    provider_id: str,
    transport: SourceTransport,
    *,
    status: ProviderStatus = ProviderStatus.NO_MATCH,
    coverage: CoverageStatus = CoverageStatus.UNKNOWN,
    freshness: FreshnessStatus = FreshnessStatus.UNKNOWN,
) -> ProviderResult:
    return ProviderResult(
        provider_id=provider_id,
        status=status,
        transport=transport,
        coverage=coverage,
        freshness=freshness,
    )


def _public_query(
    text: str,
    *,
    resource_kinds: tuple[LegalResourceKind, ...] = (
        LegalResourceKind.STATUTE,
    ),
) -> LegalSearchQuery:
    return LegalSearchQuery(
        text=text,
        resource_kinds=resource_kinds,
        data_classification=DataClassification.PUBLIC,
    )


def test_local_snapshot_keeps_official_authority_separate_from_local_transport():
    provider = InMemoryVerifiedSnapshotProvider(
        [_snapshot()],
        coverage_complete=True,
        fresh_until=NOW + timedelta(hours=1),
        now=lambda: NOW,
    )

    result = provider.search(LegalSearchQuery(text="주택법"))

    assert result.hits[0].attestation.source_authority == SourceAuthority.LAW_GO_KR
    assert result.hits[0].attestation.source_transport == SourceTransport.LOCAL_VERIFIED_SNAPSHOT
    assert result.hits[0].attestation.trust_level == TrustLevel.VERIFIED_SNAPSHOT
    assert result.hits[0].is_authoritative_evidence is True


def test_verified_fresh_complete_local_snapshot_prevents_external_calls():
    local = InMemoryVerifiedSnapshotProvider(
        [_snapshot()],
        coverage_complete=True,
        fresh_until=NOW + timedelta(hours=1),
        now=lambda: NOW,
    )
    official = _FakeProvider(
        _empty_result("official", SourceTransport.LAW_GO_KR_DIRECT_API)
    )
    bridge = _FakeProvider(
        _empty_result("bridge", SourceTransport.SELF_HOSTED_MCP)
    )
    orchestrator = LegalIntelligenceOrchestrator(
        local_provider=local,
        official_provider=official,
        self_hosted_mcp=bridge,
    )

    result = orchestrator.search(
        LegalSearchQuery(
            text="주택법",
            data_classification=DataClassification.PUBLIC,
        )
    )

    assert result.satisfied_by_local_snapshot is True
    assert [hit.title for hit in result.authoritative_hits] == ["주택법"]
    assert bridge.calls == 0
    assert official.calls == 0


def test_untrusted_connected_caller_context_never_becomes_authoritative():
    local = _FakeProvider(
        _empty_result(
            "local",
            SourceTransport.LOCAL_VERIFIED_SNAPSHOT,
            coverage=CoverageStatus.PARTIAL,
            freshness=FreshnessStatus.STALE,
        )
    )
    official = _FakeProvider(
        _empty_result("official", SourceTransport.LAW_GO_KR_DIRECT_API)
    )
    orchestrator = LegalIntelligenceOrchestrator(
        local_provider=local,
        official_provider=official,
    )

    result = orchestrator.search(
        LegalSearchQuery(text="주택법"),
        caller_context={
            "connected": True,
            "references": [
                {
                    "lawId": "caller-law-1",
                    "lawName": "주택법",
                    "url": "https://untrusted.example/law",
                    "apiKey": "caller-secret",
                    "internalMetadata": {"tenant": "secret-tenant"},
                }
            ],
        },
    )

    assert result.authoritative_hits == ()
    assert len(result.candidate_hits) == 1
    candidate = result.candidate_hits[0]
    assert candidate.attestation.source_authority == SourceAuthority.CALLER_SUPPLIED
    assert candidate.attestation.source_transport == SourceTransport.CALLER_CONTEXT
    assert candidate.is_authoritative_evidence is False
    assert candidate.attestation.locator == "caller-context:caller-candidate:0"
    assert set(candidate.content) == {"lawId", "lawName"}
    assert "caller-secret" not in str(candidate.content)
    assert "secret-tenant" not in str(candidate.content)


def test_stale_local_uses_direct_official_before_self_hosted_bridge():
    local = _FakeProvider(
        _empty_result(
            "local",
            SourceTransport.LOCAL_VERIFIED_SNAPSHOT,
            coverage=CoverageStatus.COMPLETE,
            freshness=FreshnessStatus.STALE,
        )
    )
    bridge = McpBridgeProvider(
        url="http://127.0.0.1:8123/search",
        post_json=lambda *_: (_ for _ in ()).throw(ConnectionError("bridge down")),
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0),
    )

    def official_get(url: str, timeout: float):
        assert parse_qs(urlsplit(url).query)["target"] == ["law"]
        return {
            "LawSearch": {
                "law": [
                    {
                        "법령ID": "001234",
                        "법령명한글": "주택법",
                        "법령상세링크": "/법령/주택법",
                    }
                ]
            }
        }

    official = LawGoKrDirectProvider(
        oc="test@example.com",
        http_get_json=official_get,
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0),
        now=lambda: NOW,
    )
    orchestrator = LegalIntelligenceOrchestrator(
        local_provider=local,
        official_provider=official,
        self_hosted_mcp=bridge,
    )

    result = orchestrator.search(
        LegalSearchQuery(
            text="주택법",
            data_classification=DataClassification.PUBLIC,
        )
    )

    assert [item.provider_id for item in result.provider_trace] == [
        "local",
        "law_go_kr_direct",
        "self_hosted_legal_mcp",
    ]
    assert result.provider_trace[2].status == ProviderStatus.FAILED
    assert result.authoritative_hits == ()
    direct_candidate = result.candidate_hits[0]
    assert (
        direct_candidate.attestation.source_authority
        == SourceAuthority.CENTRAL_GOVERNMENT
    )
    assert direct_candidate.attestation.source_transport == SourceTransport.LAW_GO_KR_DIRECT_API
    assert direct_candidate.attestation.trust_level == TrustLevel.SOURCE_ATTESTED


def test_remote_mcp_is_disabled_by_default_without_calling_transport():
    calls = []
    remote = McpBridgeProvider(
        url="https://remote.example/mcp",
        remote=True,
        post_json=lambda *args: calls.append(args) or {},
    )

    result = remote.search(LegalSearchQuery(text="주택법"))

    assert result.status == ProviderStatus.SKIPPED
    assert result.diagnostics[0].code == "REMOTE_MCP_DISABLED"
    assert result.attempts == 0
    assert calls == []


def test_remote_mcp_capability_never_receives_confidential_project_queries():
    calls = []
    remote = McpBridgeProvider(
        url="https://remote.example/mcp",
        remote=True,
        allow_remote=True,
        post_json=lambda *args: calls.append(args) or {"hits": []},
    )
    official = _FakeProvider(
        _empty_result("official", SourceTransport.LAW_GO_KR_DIRECT_API)
    )
    orchestrator = LegalIntelligenceOrchestrator(
        local_provider=_FakeProvider(
            _empty_result("local", SourceTransport.LOCAL_VERIFIED_SNAPSHOT)
        ),
        official_provider=official,
        remote_mcp=remote,
        allow_remote_mcp=True,
    )

    confidential = orchestrator.search(LegalSearchQuery(text="비공개 사업 주소"))
    assert calls == []
    assert official.calls == 0
    skipped_external = confidential.provider_trace[1:]
    assert {item.transport for item in skipped_external} == {
        SourceTransport.LAW_GO_KR_DIRECT_API,
        SourceTransport.REMOTE_MCP,
    }
    assert all(item.status == ProviderStatus.SKIPPED for item in skipped_external)
    assert all(item.attempts == 0 for item in skipped_external)
    assert {
        diagnostic.code
        for item in skipped_external
        for diagnostic in item.diagnostics
    } == {"EXTERNAL_EGRESS_BLOCKED_BY_CLASSIFICATION"}

    orchestrator.search(
        LegalSearchQuery(
            text="공개 법령명",
            data_classification=DataClassification.PUBLIC,
        )
    )
    assert official.calls == 1
    assert len(calls) == 1


def test_legally_privileged_query_skips_all_external_egress():
    official = _FakeProvider(
        _empty_result("official", SourceTransport.LAW_GO_KR_DIRECT_API)
    )
    remote = _FakeProvider(
        _empty_result("remote", SourceTransport.REMOTE_MCP)
    )
    orchestrator = LegalIntelligenceOrchestrator(
        local_provider=_FakeProvider(
            _empty_result("local", SourceTransport.LOCAL_VERIFIED_SNAPSHOT)
        ),
        official_provider=official,
        remote_mcp=remote,
        allow_remote_mcp=True,
    )

    result = orchestrator.search(
        LegalSearchQuery(
            text="변호사 검토 메모",
            data_classification=DataClassification.LEGALLY_PRIVILEGED,
        )
    )

    assert official.calls == 0
    assert remote.calls == 0
    assert [item.status for item in result.provider_trace] == [
        ProviderStatus.NO_MATCH,
        ProviderStatus.SKIPPED,
        ProviderStatus.SKIPPED,
    ]


@pytest.mark.parametrize(
    "classification",
    [DataClassification.PUBLIC, DataClassification.INTERNAL],
)
def test_public_and_internal_queries_may_call_external_providers(
    classification: DataClassification,
):
    official = _FakeProvider(
        _empty_result("official", SourceTransport.LAW_GO_KR_DIRECT_API)
    )
    remote = _FakeProvider(
        _empty_result("remote", SourceTransport.REMOTE_MCP)
    )
    orchestrator = LegalIntelligenceOrchestrator(
        local_provider=_FakeProvider(
            _empty_result("local", SourceTransport.LOCAL_VERIFIED_SNAPSHOT)
        ),
        official_provider=official,
        remote_mcp=remote,
        allow_remote_mcp=True,
    )

    result = orchestrator.search(
        LegalSearchQuery(
            text="공개 가능한 법령 질의",
            data_classification=classification,
        )
    )

    assert official.calls == 1
    assert remote.calls == 1
    assert all(
        item.status != ProviderStatus.SKIPPED
        for item in result.provider_trace
    )


def test_mcp_candidate_locator_does_not_disclose_bridge_or_response_urls():
    bridge = McpBridgeProvider(
        url="http://127.0.0.1:8123/search",
        post_json=lambda *_args: {
            "hits": [
                {
                    "title": "후보",
                    "url": "http://another-internal-host/source",
                    "apiKey": "must-not-be-echoed",
                    "internalMetadata": {"tenantSecret": "hidden"},
                }
            ]
        },
    )

    result = bridge.search(LegalSearchQuery(text="후보"))

    assert len(result.hits) == 1
    locator = result.hits[0].attestation.locator
    assert locator == "mcp-candidate:self_hosted_legal_mcp:0"
    assert "internal" not in locator
    assert result.hits[0].content == {"title": "후보"}


@pytest.mark.parametrize(
    ("resource_kind", "expected_target"),
    tuple(LAW_GO_KR_TARGETS.items()),
)
def test_official_adapter_uses_resource_specific_search_target(
    resource_kind: LegalResourceKind,
    expected_target: str,
):
    seen_targets = []

    def fake_get(url: str, timeout: float):
        seen_targets.extend(parse_qs(urlsplit(url).query)["target"])
        return {f"{expected_target.title()}Search": {expected_target: []}}

    provider = LawGoKrDirectProvider(
        oc="test@example.com",
        http_get_json=fake_get,
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0),
    )

    result = provider.search(
        _public_query("테스트", resource_kinds=(resource_kind,))
    )

    assert result.status == ProviderStatus.NO_MATCH
    assert seen_targets == [expected_target]


def test_generic_committee_search_is_skipped_until_subtype_is_explicit():
    calls = []
    provider = LawGoKrDirectProvider(
        oc="test@example.com",
        http_get_json=lambda *args: calls.append(args) or {},
    )

    result = provider.search(
        _public_query(
            "위원회 결정",
            resource_kinds=(LegalResourceKind.COMMITTEE_DECISION,),
        )
    )

    assert result.status == ProviderStatus.SKIPPED
    assert result.attempts == 0
    assert result.diagnostics[0].code == "LAW_GO_KR_RESOURCE_KIND_UNSUPPORTED"
    assert calls == []


def test_official_adapter_quarantines_schema_drift_instead_of_reporting_no_match():
    provider = LawGoKrDirectProvider(
        oc="test@example.com",
        http_get_json=lambda *_args: {"unexpected": {"rows": []}},
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0),
    )

    result = provider.search(_public_query("주택법"))

    assert result.status == ProviderStatus.FAILED
    assert result.hits == ()
    assert result.diagnostics[0].code == "LAW_GO_KR_SCHEMA_MISMATCH"


def test_official_adapter_quarantines_rows_without_stable_identity():
    provider = LawGoKrDirectProvider(
        oc="test@example.com",
        http_get_json=lambda *_args: {
            "LawSearch": {
                "law": [
                    {"법령명한글": "식별자 없는 법령"},
                    {"법령ID": "id-without-title"},
                ]
            }
        },
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0),
    )

    result = provider.search(_public_query("주택법"))

    assert result.status == ProviderStatus.NO_MATCH
    assert result.hits == ()
    assert result.diagnostics[0].code == "LAW_GO_KR_INVALID_ROWS_QUARANTINED"


@pytest.mark.parametrize(
    ("query", "expected_stage"),
    (
        (LegalSearchQuery(text="법률 제001234호"), "exact_identifier"),
        (LegalSearchQuery(text="주택법"), "exact_name"),
        (LegalSearchQuery(text="주법"), "approved_alias"),
        (LegalSearchQuery(text="사업계획 승인"), "lexical"),
    ),
)
def test_local_search_has_deterministic_exact_alias_then_lexical_stages(
    query: LegalSearchQuery,
    expected_stage: str,
):
    provider = InMemoryVerifiedSnapshotProvider(
        [_snapshot()],
        coverage_complete=True,
        fresh_until=NOW + timedelta(hours=1),
        now=lambda: NOW,
    )

    result = provider.search(query)

    assert len(result.hits) == 1
    assert result.hits[0].stage.value == expected_stage


def test_citation_attestation_binds_content_hash_and_as_of_date():
    content = {"법령ID": "001234", "본문": "원문"}
    attestation = build_attestation(
        content=content,
        source_authority=SourceAuthority.LAW_GO_KR,
        source_transport=SourceTransport.LAW_GO_KR_DIRECT_API,
        trust_level=TrustLevel.SOURCE_ATTESTED,
        resource_kind=LegalResourceKind.STATUTE,
        official_resource_id="001234",
        locator="https://www.law.go.kr/법령/주택법",
        as_of=date(2026, 7, 18),
        fetched_at=NOW,
    )

    assert attestation.as_of == date(2026, 7, 18)
    assert attestation.requested_as_of_date == date(2026, 7, 18)
    assert attestation.observed_at == NOW
    assert attestation.verified_effective_date is None
    assert attestation.is_applicability_verified is False
    assert attestation.model_dump()["as_of"] == date(2026, 7, 18)
    assert attestation.verifies(content) is True
    assert attestation.verifies({**content, "본문": "변조"}) is False
    assert attestation.is_authoritative_evidence is False


def test_source_attested_search_candidate_cannot_claim_verified_applicability():
    with pytest.raises(
        ValueError,
        match="cannot assert verified historical applicability",
    ):
        build_attestation(
            content={"id": "candidate"},
            source_authority=SourceAuthority.CENTRAL_GOVERNMENT,
            source_transport=SourceTransport.LAW_GO_KR_DIRECT_API,
            trust_level=TrustLevel.SOURCE_ATTESTED,
            resource_kind=LegalResourceKind.STATUTE,
            official_resource_id="law-1",
            official_version_id="law-1-v1",
            locator="law-go-kr-candidate:law:law-1",
            requested_as_of_date=date(2026, 7, 18),
            fetched_at=NOW,
            verified_at=NOW,
            verified_effective_date=date(2026, 1, 1),
            effective_from=date(2026, 1, 1),
        )


def _verified_attestation(**overrides):
    options = {
        "content": {"id": "verified-version"},
        "source_authority": SourceAuthority.CENTRAL_GOVERNMENT,
        "source_transport": SourceTransport.LOCAL_VERIFIED_SNAPSHOT,
        "trust_level": TrustLevel.VERIFIED_SNAPSHOT,
        "resource_kind": LegalResourceKind.STATUTE,
        "official_resource_id": "law-1",
        "official_version_id": "law-1-v1",
        "locator": "legal-corpus:law-1:v1",
        "requested_as_of_date": date(2026, 7, 18),
        "observed_at": NOW - timedelta(minutes=2),
        "fetched_at": NOW - timedelta(minutes=1),
        "verified_at": NOW,
        "verified_effective_date": date(2026, 1, 1),
        "effective_from": date(2026, 1, 1),
        "effective_to": date(2026, 12, 31),
    }
    options.update(overrides)
    return build_attestation(**options)


def test_verified_attestation_requires_version_and_effective_window_membership():
    attestation = _verified_attestation()
    assert attestation.is_applicability_verified is True

    with pytest.raises(ValueError, match="official_version_id"):
        _verified_attestation(official_version_id=None)
    with pytest.raises(ValueError, match="verified_effective_date"):
        _verified_attestation(verified_effective_date=None)
    with pytest.raises(ValueError, match="outside the verified validity window"):
        _verified_attestation(
            requested_as_of_date=date(2027, 1, 1),
        )
    with pytest.raises(ValueError, match="must match effective_from"):
        _verified_attestation(
            verified_effective_date=date(2026, 2, 1),
        )


@pytest.mark.parametrize(
    "overrides",
    (
        {
            "observed_at": datetime.now(timezone.utc)
            + timedelta(minutes=10),
        },
        {
            "fetched_at": datetime.now(timezone.utc)
            + timedelta(minutes=10),
        },
        {
            "verified_at": datetime.now(timezone.utc)
            + timedelta(minutes=10),
        },
        {
            "fetched_at": NOW,
            "verified_at": NOW - timedelta(seconds=1),
        },
    ),
)
def test_verified_attestation_rejects_future_or_noncausal_times(overrides):
    with pytest.raises(ValueError):
        _verified_attestation(**overrides)


@pytest.mark.parametrize(
    ("transport", "trust_level"),
    (
        (SourceTransport.SELF_HOSTED_MCP, TrustLevel.SOURCE_ATTESTED),
        (SourceTransport.REMOTE_MCP, TrustLevel.VERIFIED_SNAPSHOT),
        (SourceTransport.CALLER_CONTEXT, TrustLevel.HUMAN_REVIEWED),
        (
            SourceTransport.LAW_GO_KR_DIRECT_API,
            TrustLevel.VERIFIED_SNAPSHOT,
        ),
        (
            SourceTransport.LOCAL_VERIFIED_SNAPSHOT,
            TrustLevel.SOURCE_ATTESTED,
        ),
    ),
)
def test_citation_attestation_rejects_invalid_trust_transport_matrix(
    transport,
    trust_level,
):
    with pytest.raises(ValueError):
        build_attestation(
            content={"id": "candidate"},
            source_authority=SourceAuthority.SUPREME_COURT,
            source_transport=transport,
            trust_level=trust_level,
            resource_kind=LegalResourceKind.PRECEDENT,
            official_resource_id="precedent-1",
            locator="candidate:precedent-1",
            as_of=date(2026, 7, 18),
            fetched_at=NOW,
            verified_at=(
                NOW
                if trust_level
                in {TrustLevel.VERIFIED_SNAPSHOT, TrustLevel.HUMAN_REVIEWED}
                else None
            ),
        )


def test_direct_provider_deduplicates_shared_law_target_and_does_not_verify_requested_date():
    seen_targets = []

    def fake_get(url: str, timeout: float):
        seen_targets.extend(parse_qs(urlsplit(url).query)["target"])
        return {
            "LawSearch": {
                "law": [
                    {
                        "id": "law-1",
                        "title": "Housing Act",
                        "effectiveDate": "20260101",
                    }
                ]
            }
        }

    provider = LawGoKrDirectProvider(
        oc="test@example.com",
        http_get_json=fake_get,
        retry_policy=RetryPolicy(
            max_attempts=1,
            initial_backoff_seconds=0,
        ),
        now=lambda: NOW,
    )

    query = _public_query(
            "Housing Act",
            resource_kinds=(
                LegalResourceKind.STATUTE,
                LegalResourceKind.PROVISION,
            ),
        )
    result = provider.search(query)

    assert seen_targets == ["law"]
    attestation = result.hits[0].attestation
    assert (
        attestation.source_authority
        == SourceAuthority.CENTRAL_GOVERNMENT
    )
    assert attestation.requested_as_of_date == query.as_of
    assert attestation.verified_effective_date is None
    assert attestation.verified_at is None


@pytest.mark.parametrize(
    ("court_name", "expected_authority"),
    (
        ("대법원", SourceAuthority.SUPREME_COURT),
        ("서울고등법원", SourceAuthority.COURT),
    ),
)
def test_precedent_authority_comes_from_issuing_court_not_transport(
    court_name,
    expected_authority,
):
    provider = LawGoKrDirectProvider(
        oc="test@example.com",
        http_get_json=lambda *_args: {
            "PrecSearch": {
                "prec": [
                    {
                        "id": "precedent-1",
                        "title": "Housing precedent",
                        "courtName": court_name,
                    }
                ]
            }
        },
        retry_policy=RetryPolicy(
            max_attempts=1,
            initial_backoff_seconds=0,
        ),
        now=lambda: NOW,
    )

    result = provider.search(
        _public_query(
            "Housing precedent",
            resource_kinds=(LegalResourceKind.PRECEDENT,),
        )
    )

    assert result.hits[0].attestation.source_authority == expected_authority
    assert (
        result.hits[0].attestation.source_transport
        == SourceTransport.LAW_GO_KR_DIRECT_API
    )


def test_direct_provider_redacts_oc_and_tokens_from_failures_and_urls():
    secret = "very-secret-oc"

    def failing_get(url: str, timeout: float):
        raise RuntimeError(f"failed URL={url}&access_token=token-secret")

    provider = LawGoKrDirectProvider(
        oc=secret,
        http_get_json=failing_get,
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0),
    )

    result = provider.search(_public_query("주택법"))
    diagnostic = result.diagnostics[0]
    rendered = f"{diagnostic.message} {diagnostic.sanitized_url}"

    assert result.status == ProviderStatus.FAILED
    assert secret not in rendered
    assert "token-secret" not in rendered
    assert "[REDACTED]" in rendered


@pytest.mark.parametrize(
    "base_url",
    (
        "http://www.law.go.kr/DRF/lawSearch.do",
        "https://law-go-kr.example/DRF/lawSearch.do",
        "https://user:password@www.law.go.kr/DRF/lawSearch.do",
    ),
)
def test_direct_provider_rejects_non_tls_unlisted_or_credentialed_urls(base_url):
    with pytest.raises(ValueError):
        LawGoKrDirectProvider(oc="test@example.com", base_url=base_url)


def test_direct_provider_accepts_an_explicitly_allowlisted_test_proxy():
    provider = LawGoKrDirectProvider(
        oc="test@example.com",
        base_url="https://legal-proxy.internal/law-search",
        allowed_hosts=("legal-proxy.internal",),
        http_get_json=lambda *_args: {"LawSearch": {"law": []}},
    )

    result = provider.search(_public_query("주택법"))

    assert result.status == ProviderStatus.NO_MATCH


@pytest.mark.parametrize(
    "base_url",
    (
        "https://www.law.go.kr/DRF/lawSearch.do?token=secret",
        "https://www.law.go.kr/DRF/lawSearch.do?key=secret",
        "https://www.law.go.kr/DRF/lawSearch.do?authorization=Bearer",
        "https://www.law.go.kr/DRF/lawSearch.do?x-api-key=secret",
        "https://www.law.go.kr/DRF/lawSearch.do?OC=secret",
    ),
)
def test_direct_provider_rejects_credential_query_parameters(base_url):
    with pytest.raises(ValueError, match="credential query parameters"):
        LawGoKrDirectProvider(oc="test@example.com", base_url=base_url)


@pytest.mark.parametrize(
    "url,remote",
    (
        ("http://127.0.0.1:9000/mcp?token=secret", False),
        ("http://127.0.0.1:9000/mcp?key=secret", False),
        ("https://remote.example/mcp?authorization=Bearer", True),
        ("https://remote.example/mcp?client_secret=secret", True),
    ),
)
def test_mcp_provider_rejects_credential_query_parameters(url, remote):
    with pytest.raises(ValueError, match="credential query parameters"):
        McpBridgeProvider(
            url=url,
            remote=remote,
            allow_remote=remote,
        )


@pytest.mark.parametrize(
    "untrusted_locator",
    (
        "https://evil.example/law?token=secret",
        "http://www.law.go.kr/law",
        "//evil.example/law",
        "https://www.law.go.kr/law?authorization=secret",
    ),
)
def test_direct_provider_replaces_untrusted_result_locator(
    untrusted_locator,
):
    provider = LawGoKrDirectProvider(
        oc="test@example.com",
        http_get_json=lambda *_args: {
            "LawSearch": {
                "law": [
                    {
                        "id": "law-safe-locator-1",
                        "title": "Housing Act",
                        "url": untrusted_locator,
                    }
                ]
            }
        },
        retry_policy=RetryPolicy(
            max_attempts=1,
            initial_backoff_seconds=0,
        ),
    )

    result = provider.search(_public_query("Housing Act"))
    locator = result.hits[0].attestation.locator

    assert locator == "law-go-kr-candidate:law:law-safe-locator-1"
    assert "evil.example" not in locator
    assert "secret" not in locator
    assert result.hits[0].content["url"] == locator


def test_direct_provider_does_not_fabricate_statute_url_for_precedent():
    provider = LawGoKrDirectProvider(
        oc="test@example.com",
        http_get_json=lambda *_args: {
            "PrecSearch": {
                "prec": [
                    {
                        "id": "precedent-without-link",
                        "title": "Housing precedent",
                        "courtName": "서울고등법원",
                    }
                ]
            }
        },
        retry_policy=RetryPolicy(
            max_attempts=1,
            initial_backoff_seconds=0,
        ),
    )

    result = provider.search(
        _public_query(
            "Housing precedent",
            resource_kinds=(LegalResourceKind.PRECEDENT,),
        )
    )

    locator = result.hits[0].attestation.locator
    assert locator == "law-go-kr-candidate:prec:precedent-without-link"
    assert not locator.startswith(("http://", "https://"))


def test_remote_mcp_requires_tls_and_all_mcp_urls_reject_credentials():
    with pytest.raises(ValueError, match="https"):
        McpBridgeProvider(
            url="http://remote.example/mcp",
            remote=True,
            allow_remote=True,
        )
    with pytest.raises(ValueError, match="credentials"):
        McpBridgeProvider(url="http://user:secret@localhost:9000/mcp")


@pytest.mark.parametrize(
    "url",
    (
        "http://public-mcp.example/mcp",
        "https://public-mcp.example/mcp",
    ),
)
def test_self_hosted_mcp_rejects_non_private_hosts_without_allowlist(url):
    with pytest.raises(ValueError, match="allowlisted"):
        McpBridgeProvider(url=url)


def test_self_hosted_mcp_allows_private_literal_or_tls_allowlisted_host():
    private = McpBridgeProvider(
        url="http://10.20.30.40:8123/mcp",
        post_json=lambda *_args: {"hits": []},
    )
    allowlisted = McpBridgeProvider(
        url="https://legal-mcp.example/mcp",
        allowed_hosts=("legal-mcp.example",),
        post_json=lambda *_args: {"hits": []},
    )

    assert private.search(LegalSearchQuery(text="내부 질의")).status == (
        ProviderStatus.NO_MATCH
    )
    assert allowlisted.search(
        LegalSearchQuery(text="내부 질의")
    ).status == ProviderStatus.NO_MATCH


def test_allowlisted_self_hosted_mcp_still_requires_tls():
    with pytest.raises(ValueError, match="https"):
        McpBridgeProvider(
            url="http://legal-mcp.example/mcp",
            allowed_hosts=("legal-mcp.example",),
        )


def test_direct_provider_guards_confidential_query_without_orchestrator():
    law_calls = []
    law = LawGoKrDirectProvider(
        oc="test@example.com",
        http_get_json=lambda *args: law_calls.append(args) or {},
    )
    remote_calls = []
    remote = McpBridgeProvider(
        url="https://remote.example/mcp",
        remote=True,
        allow_remote=True,
        post_json=lambda *args: remote_calls.append(args) or {"hits": []},
    )

    law_result = law.search(LegalSearchQuery(text="비공개 프로젝트"))
    remote_result = remote.search(
        LegalSearchQuery(
            text="법률자문 비밀",
            data_classification=DataClassification.LEGALLY_PRIVILEGED,
        )
    )

    assert law_result.status == ProviderStatus.SKIPPED
    assert remote_result.status == ProviderStatus.SKIPPED
    assert law_calls == []
    assert remote_calls == []
    assert law_result.diagnostics[0].code == (
        "EXTERNAL_EGRESS_BLOCKED_BY_CLASSIFICATION"
    )


def test_legal_http_client_rejects_get_and_post_redirect_before_target_call():
    target_calls: list[str] = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            target_calls.append("GET")
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            target_calls.append("POST")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    target_thread.start()
    target_url = f"http://127.0.0.1:{target.server_port}/target"

    class RedirectHandler(BaseHTTPRequestHandler):
        def _redirect(self):
            self.send_response(307)
            self.send_header("Location", target_url)
            self.end_headers()

        do_GET = _redirect
        do_POST = _redirect

        def log_message(self, *_args):
            return

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    redirect_thread = threading.Thread(
        target=redirect.serve_forever,
        daemon=True,
    )
    redirect_thread.start()
    redirect_url = f"http://127.0.0.1:{redirect.server_port}/redirect"

    try:
        for method in ("GET", "POST"):
            request = urllib.request.Request(
                redirect_url,
                data=b"confidential" if method == "POST" else None,
                method=method,
            )
            with pytest.raises(ValueError, match="redirects are not allowed"):
                legal_providers._open_without_redirect(
                    request,
                    timeout=1,
                )
        assert target_calls == []
    finally:
        redirect.shutdown()
        redirect.server_close()
        target.shutdown()
        target.server_close()


def test_circuit_breaker_stops_calls_after_failure_threshold():
    calls = []

    def failing_get(url: str, timeout: float):
        calls.append(url)
        raise ConnectionError("offline")

    provider = LawGoKrDirectProvider(
        oc="test@example.com",
        http_get_json=failing_get,
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0),
        circuit_breaker=CircuitBreaker(failure_threshold=1, reset_timeout_seconds=60),
    )

    first = provider.search(_public_query("주택법"))
    second = provider.search(_public_query("주택법"))

    assert first.status == ProviderStatus.FAILED
    assert second.status == ProviderStatus.CIRCUIT_OPEN
    assert len(calls) == 1
