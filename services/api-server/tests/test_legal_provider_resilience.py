from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest

from legal_intelligence.models import (
    CoverageStatus,
    DataClassification,
    LegalResourceKind,
    LegalSearchQuery,
    ProviderResult,
    ProviderStatus,
    SourceTransport,
)
from legal_intelligence.orchestrator import LegalIntelligenceOrchestrator
from legal_intelligence.providers import LawGoKrDirectProvider, McpBridgeProvider
from legal_intelligence.resilience import (
    CircuitBreaker,
    ProviderDeadLetterRecord,
    ProviderHttpError,
    ProviderMetric,
    ProviderQuarantineRecord,
    RateLimitDecision,
    RetryExhaustedError,
    RetryPolicy,
    TokenBucketProviderRateLimiter,
    shared_provider_rate_limiter,
    resilient_call,
)


def _public_query(
    *,
    kinds: tuple[LegalResourceKind, ...] = (LegalResourceKind.STATUTE,),
) -> LegalSearchQuery:
    return LegalSearchQuery(
        text="Housing Act",
        resource_kinds=kinds,
        as_of=date(2026, 7, 20),
        data_classification=DataClassification.PUBLIC,
    )


class _CollectingTelemetry:
    def __init__(self) -> None:
        self.items: list[ProviderMetric] = []

    def record(self, metric: ProviderMetric) -> None:
        self.items.append(metric)


class _CollectingQuarantine:
    def __init__(self) -> None:
        self.items: list[ProviderQuarantineRecord] = []

    def store(self, record: ProviderQuarantineRecord) -> None:
        self.items.append(record)


class _CollectingDeadLetters:
    def __init__(self) -> None:
        self.items: list[ProviderDeadLetterRecord] = []

    def enqueue(self, record: ProviderDeadLetterRecord) -> None:
        self.items.append(record)


class _AlwaysLimited:
    def acquire(
        self,
        provider_id: str,
        *,
        tokens: float = 1.0,
    ) -> RateLimitDecision:
        del provider_id, tokens
        return RateLimitDecision(allowed=False, retry_after_seconds=0.25)


def test_non_retryable_http_4xx_fails_once_without_opening_circuit() -> None:
    calls = 0
    sleeps: list[float] = []
    circuit = CircuitBreaker(failure_threshold=1)

    def operation() -> None:
        nonlocal calls
        calls += 1
        raise ProviderHttpError(400, "invalid request")

    with pytest.raises(RetryExhaustedError) as raised:
        resilient_call(
            operation,
            retry_policy=RetryPolicy(max_attempts=3),
            circuit_breaker=circuit,
            sleep=sleeps.append,
        )

    assert calls == 1
    assert sleeps == []
    assert raised.value.retryable is False
    assert circuit.is_open is False


def test_retry_after_is_honored_but_bounded_before_success() -> None:
    calls = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderHttpError(429, retry_after="60")
        return "ok"

    value, attempts = resilient_call(
        operation,
        retry_policy=RetryPolicy(
            max_attempts=2,
            initial_backoff_seconds=0.1,
            max_retry_after_seconds=2.0,
        ),
        circuit_breaker=CircuitBreaker(),
        sleep=sleeps.append,
        jitter=lambda low, high: high,
    )

    assert value == "ok"
    assert attempts == 2
    assert sleeps == [2.0]


def test_backoff_uses_bounded_full_jitter() -> None:
    calls = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("offline")
        return "ok"

    resilient_call(
        operation,
        retry_policy=RetryPolicy(
            max_attempts=2,
            initial_backoff_seconds=10,
            max_backoff_seconds=0.25,
        ),
        circuit_breaker=CircuitBreaker(),
        sleep=sleeps.append,
        jitter=lambda low, high: high,
    )

    assert sleeps == [0.25]


def test_token_bucket_is_independent_per_provider() -> None:
    current = 0.0
    limiter = TokenBucketProviderRateLimiter(
        capacity=1,
        refill_tokens_per_second=2,
        clock=lambda: current,
    )

    assert limiter.acquire("law").allowed is True
    denied = limiter.acquire("law")
    assert denied.allowed is False
    assert denied.retry_after_seconds == pytest.approx(0.5)
    assert limiter.acquire("mcp").allowed is True

    current = 0.5
    assert limiter.acquire("law").allowed is True


def test_shared_limiter_preserves_bucket_across_runtime_construction() -> None:
    first = shared_provider_rate_limiter(
        capacity=1,
        refill_tokens_per_second=0.000001,
    )
    second = shared_provider_rate_limiter(
        capacity=1,
        refill_tokens_per_second=0.000001,
    )

    assert first is second
    assert first.acquire("test-shared-runtime-provider").allowed is True
    assert second.acquire("test-shared-runtime-provider").allowed is False


def test_direct_provider_reports_partial_and_writes_dead_letter_when_one_target_fails() -> None:
    calls: list[str] = []
    dead_letters = _CollectingDeadLetters()
    telemetry = _CollectingTelemetry()

    def fake_get(url: str, timeout: float):
        del timeout
        target = parse_qs(urlsplit(url).query)["target"][0]
        calls.append(target)
        if target == "law":
            raise ProviderHttpError(400, "bad law target")
        return {
            "PrecSearch": {
                "prec": [
                    {
                        "id": "precedent-1",
                        "title": "Housing precedent",
                        "courtName": "Supreme Court",
                    }
                ]
            }
        }

    provider = LawGoKrDirectProvider(
        oc="test@example.com",
        http_get_json=fake_get,
        retry_policy=RetryPolicy(max_attempts=3, initial_backoff_seconds=0),
        dead_letter_sink=dead_letters,
        telemetry=telemetry,
    )
    result = provider.search(
        _public_query(
            kinds=(
                LegalResourceKind.STATUTE,
                LegalResourceKind.PRECEDENT,
            )
        )
    )

    assert calls == ["law", "prec"]
    assert result.status == ProviderStatus.PARTIAL
    assert result.coverage == CoverageStatus.PARTIAL
    assert len(result.hits) == 1
    assert result.diagnostics[0].retryable is False
    assert result.diagnostics[0].target == "law"
    assert dead_letters.items[0].target == "law"
    assert telemetry.items[-1].outcome == "partial"
    assert telemetry.items[-1].attempts == 2


def test_direct_provider_rate_limit_is_fail_closed_without_network_call() -> None:
    calls = 0
    telemetry = _CollectingTelemetry()

    def fake_get(url: str, timeout: float):
        nonlocal calls
        calls += 1
        return {}

    provider = LawGoKrDirectProvider(
        oc="test@example.com",
        http_get_json=fake_get,
        rate_limiter=_AlwaysLimited(),
        telemetry=telemetry,
    )
    result = provider.search(_public_query())

    assert calls == 0
    assert result.status == ProviderStatus.FAILED
    assert result.attempts == 0
    assert result.diagnostics[0].code == "LAW_GO_KR_CLIENT_RATE_LIMITED"
    assert result.diagnostics[0].target == "law"
    assert result.diagnostics[0].retry_after_seconds == pytest.approx(0.25)
    assert telemetry.items[-1].rate_limited is True


def test_schema_drift_is_sent_to_quarantine_without_raw_query_in_metric() -> None:
    quarantine = _CollectingQuarantine()
    telemetry = _CollectingTelemetry()
    provider = LawGoKrDirectProvider(
        oc="test@example.com",
        http_get_json=lambda *_args: {"unexpected": {"secretQuery": "private"}},
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0),
        quarantine_sink=quarantine,
        telemetry=telemetry,
    )

    result = provider.search(_public_query())

    assert result.status == ProviderStatus.FAILED
    assert quarantine.items[0].reason_code == "LAW_GO_KR_SCHEMA_MISMATCH"
    assert quarantine.items[0].payload_digest.startswith("sha256:")
    assert telemetry.items[0].tags == {
        "transport": SourceTransport.LAW_GO_KR_DIRECT_API.value
    }
    assert "private" not in repr(telemetry.items[0])


def test_mcp_mixed_valid_and_invalid_rows_reports_partial() -> None:
    quarantine = _CollectingQuarantine()
    provider = McpBridgeProvider(
        url="http://127.0.0.1:9000/mcp",
        post_json=lambda *_args: {
            "hits": [
                {"title": "candidate", "summary": "discovery only"},
                "malformed",
            ]
        },
        quarantine_sink=quarantine,
        retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0),
    )

    result = provider.search(_public_query())

    assert result.status == ProviderStatus.PARTIAL
    assert result.coverage == CoverageStatus.PARTIAL
    assert len(result.hits) == 1
    assert quarantine.items[0].reason_code == "MCP_INVALID_ROWS_QUARANTINED"


def test_orchestrator_emits_privacy_safe_local_hit_metric() -> None:
    telemetry = _CollectingTelemetry()

    class LocalProvider:
        provider_id = "local"
        transport = SourceTransport.LOCAL_VERIFIED_SNAPSHOT

        def search(self, query: LegalSearchQuery) -> ProviderResult:
            del query
            return ProviderResult(
                provider_id=self.provider_id,
                status=ProviderStatus.NO_MATCH,
                transport=self.transport,
                attempts=1,
            )

    class OfficialProvider:
        provider_id = "official"
        transport = SourceTransport.LAW_GO_KR_DIRECT_API

        def search(self, query: LegalSearchQuery) -> ProviderResult:
            del query
            return ProviderResult(
                provider_id=self.provider_id,
                status=ProviderStatus.NO_MATCH,
                transport=self.transport,
                attempts=1,
            )

    orchestrator = LegalIntelligenceOrchestrator(
        local_provider=LocalProvider(),
        official_provider=OfficialProvider(),
        telemetry=telemetry,
    )
    orchestrator.search(_public_query())

    metric = telemetry.items[0]
    assert metric.operation == "orchestrator_local_lookup"
    assert metric.local_hit is False
    assert set(metric.tags) == {"transport"}
