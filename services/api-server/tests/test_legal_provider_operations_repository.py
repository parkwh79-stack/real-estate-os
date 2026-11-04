from __future__ import annotations

from datetime import date, datetime, timezone

from db.repositories import legal_provider_operations_repository
from db.repositories.legal_provider_operations_repository import (
    FailureIsolatedLegalProviderOperationsSink,
    PostgresLegalProviderOperationsSink,
)
from legal_intelligence.models import (
    CoverageStatus,
    DataClassification,
    FreshnessStatus,
    LegalResourceKind,
    ProviderResult,
    ProviderStatus,
    SourceTransport,
)
from legal_intelligence.resilience import (
    ProviderDeadLetterRecord,
    ProviderMetric,
    ProviderQuarantineRecord,
)
from use_cases import legal_intelligence as legal_runtime


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, statements: list[tuple[str, object]]) -> None:
        self.statements = statements

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params=None) -> None:
        self.statements.append((" ".join(sql.split()), params))


class _Connection:
    def __init__(self, statements: list[tuple[str, object]]) -> None:
        self.statements = statements

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self) -> _Cursor:
        return _Cursor(self.statements)


def test_postgres_sink_appends_privacy_safe_quarantine_dlq_and_metrics() -> None:
    statements: list[tuple[str, object]] = []
    sink = PostgresLegalProviderOperationsSink(
        connection_factory=lambda: _Connection(statements),
        schema_name="realytics",
    )

    sink.store(
        ProviderQuarantineRecord(
            provider_id="law",
            operation="search",
            reason_code="SCHEMA_MISMATCH",
            payload_digest="sha256:" + "a" * 64,
            target="prec",
            occurred_at=NOW,
        )
    )
    sink.enqueue(
        ProviderDeadLetterRecord(
            provider_id="law",
            operation="search",
            reason_code="HTTP_503",
            attempts=2,
            target="law",
            occurred_at=NOW,
        )
    )
    sink.record(
        ProviderMetric(
            provider_id="law",
            operation="search",
            outcome="partial",
            latency_ms=12.5,
            attempts=3,
            rate_limited=True,
            local_hit=False,
            freshness="unknown",
            tags={"transport": "law_go_kr_direct_api"},
        )
    )

    inserts = [item for item in statements if item[0].startswith("INSERT")]
    assert [sql.split()[2] for sql, _params in inserts] == [
        "legal_provider_quarantine",
        "legal_provider_dead_letters",
        "legal_provider_metrics",
    ]
    rendered = repr(inserts)
    assert "Housing Act" not in rendered
    assert "https://" not in rendered
    assert "password" not in rendered
    assert '"transport":"law_go_kr_direct_api"' in rendered
    assert sum(
        sql == "SET LOCAL search_path TO realytics, public, pg_temp"
        for sql, _params in statements
    ) == 3


def test_default_sink_uses_dedicated_legal_operations_connection(
    monkeypatch,
) -> None:
    statements: list[tuple[str, object]] = []
    calls = []

    def dedicated_connect():
        calls.append("legal-operations")
        return _Connection(statements)

    monkeypatch.setattr(
        legal_provider_operations_repository,
        "connect_legal_operations_database",
        dedicated_connect,
    )
    sink = PostgresLegalProviderOperationsSink(
        schema_name="realytics",
    )

    sink.record(
        ProviderMetric(
            provider_id="law",
            operation="search",
            outcome="completed",
            latency_ms=1,
        )
    )

    assert calls == ["legal-operations"]
    assert any(
        sql.startswith("INSERT INTO legal_provider_metrics")
        for sql, _params in statements
    )


def test_failure_isolated_sink_reports_storage_errors_without_raising() -> None:
    streams: list[tuple[str, str]] = []

    class BrokenSink:
        def store(self, record) -> None:
            del record
            raise RuntimeError("db offline")

        def enqueue(self, record) -> None:
            del record
            raise RuntimeError("db offline")

        def record(self, metric) -> None:
            del metric
            raise RuntimeError("db offline")

    sink = FailureIsolatedLegalProviderOperationsSink(
        BrokenSink(),
        on_error=lambda stream, error: streams.append(
            (stream, type(error).__name__)
        ),
    )
    sink.store(
        ProviderQuarantineRecord(
            provider_id="law",
            operation="search",
            reason_code="bad",
            payload_digest="sha256:" + "b" * 64,
        )
    )
    sink.enqueue(
        ProviderDeadLetterRecord(
            provider_id="law",
            operation="search",
            reason_code="offline",
            attempts=2,
        )
    )
    sink.record(
        ProviderMetric(
            provider_id="law",
            operation="search",
            outcome="failed",
            latency_ms=1,
        )
    )

    assert streams == [
        ("quarantine", "RuntimeError"),
        ("dead_letter", "RuntimeError"),
        ("metric", "RuntimeError"),
    ]


def test_default_runtime_wires_failure_isolated_postgres_telemetry(
    monkeypatch,
) -> None:
    metrics: list[ProviderMetric] = []

    class OperationsSink:
        def store(self, record) -> None:
            del record

        def enqueue(self, record) -> None:
            del record

        def record(self, metric: ProviderMetric) -> None:
            metrics.append(metric)

    operations = OperationsSink()

    class StaticProvider:
        def __init__(self, provider_id: str, transport: SourceTransport) -> None:
            self.provider_id = provider_id
            self.transport = transport

        def search(self, query):
            del query
            return ProviderResult(
                provider_id=self.provider_id,
                status=ProviderStatus.NO_MATCH,
                transport=self.transport,
                coverage=CoverageStatus.UNKNOWN,
                freshness=FreshnessStatus.UNKNOWN,
            )

    monkeypatch.setattr(legal_runtime, "postgres_configured", lambda: True)
    monkeypatch.setattr(
        legal_runtime,
        "legal_operations_database_configured",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        legal_runtime,
        "PostgresLegalProviderOperationsSink",
        lambda: operations,
    )

    service = legal_runtime.build_legal_intelligence_service(
        environ={},
        local_provider=StaticProvider(
            "local",
            SourceTransport.LOCAL_VERIFIED_SNAPSHOT,
        ),
        official_provider=StaticProvider(
            "official",
            SourceTransport.LAW_GO_KR_DIRECT_API,
        ),
    )
    response = service.search(
        query="Housing Act",
        resource_kinds=(LegalResourceKind.STATUTE,),
        as_of=date(2026, 7, 20),
        data_classification=DataClassification.PUBLIC,
    )

    assert response.provider_trace[0].provider_id == "local"
    assert metrics[0].operation == "orchestrator_local_lookup"
    assert metrics[0].local_hit is False


def test_runtime_does_not_construct_operations_sink_without_dedicated_dsn(
    monkeypatch,
) -> None:
    monkeypatch.setattr(legal_runtime, "postgres_configured", lambda: True)
    monkeypatch.setattr(
        legal_runtime,
        "legal_operations_database_configured",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        legal_runtime,
        "PostgresLegalProviderOperationsSink",
        lambda: (_ for _ in ()).throw(
            AssertionError("runtime DSN fallback is forbidden")
        ),
    )

    class StaticProvider:
        provider_id = "static"
        transport = SourceTransport.LOCAL_VERIFIED_SNAPSHOT

        def search(self, _query):
            return ProviderResult(
                provider_id=self.provider_id,
                status=ProviderStatus.NO_MATCH,
                transport=self.transport,
            )

    provider = StaticProvider()
    service = legal_runtime.build_legal_intelligence_service(
        environ={},
        local_provider=provider,
        official_provider=provider,
    )

    assert service.local_provider is provider


def test_project_preview_reads_legal_corpus_without_durable_provider_operations(monkeypatch):
    queries = []

    class ReadOnlyProvider:
        provider_id = "local"
        transport = SourceTransport.LOCAL_VERIFIED_SNAPSHOT

        def search(self, query):
            queries.append(query)
            return ProviderResult(
                provider_id=self.provider_id,
                status=ProviderStatus.NO_MATCH,
                transport=self.transport,
            )

    def unexpected_repository():
        raise AssertionError("a preview must use no durable legal operation repositories")

    monkeypatch.setattr(legal_runtime, "postgres_configured", lambda: True)
    monkeypatch.setattr(legal_runtime, "legal_operations_database_configured", lambda **_: True)
    monkeypatch.setattr(legal_runtime, "PostgresLegalProviderOperationsSink", unexpected_repository)
    monkeypatch.setattr(legal_runtime, "PostgresLegalQueryRunRepository", unexpected_repository)
    monkeypatch.setattr(legal_runtime, "PostgresVerifiedLegalSnapshotProvider", ReadOnlyProvider)

    result = legal_runtime.resolve_project_legal_context({}, max_queries=1, preview=True)

    assert len(queries) == 1
    assert queries[0].data_classification == DataClassification.PROJECT_CONFIDENTIAL
    assert result["legalIntelligence"]["providerTrace"][0]["providerId"] == "local"
