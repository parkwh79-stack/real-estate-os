from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

import pytest

from db.repositories.legal_query_run_repository import (
    LegalQueryAuditContext,
    LegalQueryRunConflict,
    PostgresLegalQueryRunRepository,
)
from legal_intelligence.models import (
    DataClassification,
    LegalResourceKind,
    LegalSearchQuery,
    LegalSearchResponse,
    ProviderDiagnostic,
    ProviderResult,
    ProviderStatus,
    SourceTransport,
)
from legal_intelligence.orchestrator import LegalIntelligenceOrchestrator
from use_cases.legal_intelligence import LegalIntelligenceService


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, statements, *, one=()):
        self.statements = statements
        self.one = list(one)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.one.pop(0) if self.one else None


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def _context(**overrides):
    values = {
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "requested_by": "analyst-1",
        "idempotency_key": "legal-search:1",
    }
    values.update(overrides)
    return LegalQueryAuditContext(**values)


def _query():
    return LegalSearchQuery(
        text="Housing Act",
        resource_kinds=(LegalResourceKind.STATUTE,),
        as_of=date(2026, 7, 20),
        data_classification=DataClassification.PUBLIC,
    )


def _response(query):
    return LegalSearchResponse(
        query=query,
        authoritative_hits=(),
        candidate_hits=(),
        provider_trace=(
            ProviderResult(
                provider_id="law-go-kr",
                status=ProviderStatus.PARTIAL,
                transport=SourceTransport.LAW_GO_KR_DIRECT_API,
                diagnostics=(
                    ProviderDiagnostic(
                        code="DETAIL_FETCH_FAILED",
                        message="target failed",
                        retryable=True,
                        target="prec",
                        retry_after_seconds=1.25,
                    ),
                ),
                attempts=2,
            ),
        ),
    )


def test_repository_appends_run_and_privacy_safe_ordered_events():
    statements = []
    cursor = _Cursor(statements, one=[("inserted",)])
    repository = PostgresLegalQueryRunRepository(
        connection_factory=lambda: _Connection(cursor),
        schema_name="realytics",
        clock=lambda: NOW,
    )
    query = _query()

    run_id = repository.record_search(
        _context(),
        query,
        _response(query),
        freshness_policy_version="freshness-v1",
        ranking_policy_version="ranking-v1",
    )

    assert run_id.startswith("legal-query-run:")
    event_inserts = [
        (sql, params)
        for sql, params in statements
        if sql.startswith("INSERT INTO legal_query_run_events")
    ]
    assert len(event_inserts) == 4
    assert [params[4] for _sql, params in event_inserts] == [1, 2, 3, 4]
    provider_metadata = event_inserts[2][1][-1]
    assert '"target":"prec"' in provider_metadata
    assert '"retryAfterSeconds":1.25' in provider_metadata
    assert all(
        "provider_id, transport" in sql
        and "NULL, %s" in sql
        for sql, _params in event_inserts
    )
    assert any(
        "set_config('realytics.tenant_id'" in sql
        for sql, _params in statements
    )


def test_repository_rejects_changed_query_for_same_idempotency_key():
    statements = []
    query = _query()
    query_hash = hashlib.sha256(query.text.encode("utf-8")).hexdigest()
    cursor = _Cursor(
        statements,
        one=[
            None,
            (
                "existing-run",
                query_hash,
                query.as_of,
                query.data_classification.value,
                ["direct_api"],
                "different-freshness-policy",
                "ranking-v1",
                "analyst-1",
            ),
        ],
    )
    repository = PostgresLegalQueryRunRepository(
        connection_factory=lambda: _Connection(cursor),
        schema_name="realytics",
        clock=lambda: NOW,
    )

    with pytest.raises(LegalQueryRunConflict):
        repository.record_search(
            _context(),
            query,
            _response(query),
            freshness_policy_version="freshness-v1",
            ranking_policy_version="ranking-v1",
        )


def test_audit_context_rejects_anonymous_and_unsafe_idempotency_key():
    with pytest.raises(ValueError):
        _context(requested_by="anonymous")
    with pytest.raises(ValueError):
        _context(idempotency_key="query=Housing Act")


def test_service_returns_the_persisted_query_run_identity():
    class StaticProvider:
        provider_id = "local"
        transport = SourceTransport.LOCAL_VERIFIED_SNAPSHOT

        def search(self, _query):
            return ProviderResult(
                provider_id=self.provider_id,
                status=ProviderStatus.NO_MATCH,
                transport=self.transport,
            )

    class Recorder:
        def __init__(self):
            self.calls = []

        def record_search(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return "legal-query-run:recorded"

    local = StaticProvider()
    recorder = Recorder()
    service = LegalIntelligenceService(
        orchestrator=LegalIntelligenceOrchestrator(
            local_provider=local,
            official_provider=local,
        ),
        local_provider=local,
        query_run_repository=recorder,
    )

    response = service.search(
        query="Housing Act",
        resource_kinds=(LegalResourceKind.STATUTE,),
        as_of=date(2026, 7, 20),
        data_classification=DataClassification.PUBLIC,
        audit_context=_context(),
    )

    assert response.query_run_id == "legal-query-run:recorded"
    assert len(recorder.calls) == 1
