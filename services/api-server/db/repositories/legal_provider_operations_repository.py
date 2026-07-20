"""Append-only PostgreSQL sinks for legal-provider operational records.

Only privacy-safe, global provider metadata reaches these tables: query text,
tenant payloads, response bodies, credentials, and URLs are deliberately
absent. Durable payload retention, if needed for replay, belongs in the
separately controlled raw-object store. Tenant/project authority begins at the
trusted worker's replay-object receipt and is enforced by forced RLS through
the catalog, approval, request, and event streams.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any, Protocol
from uuid import uuid4

from db.legal_operations_database import connect_legal_operations_database
from db.postgres_runtime_privileges import validate_database_identifier
from legal_intelligence.resilience import (
    ProviderDeadLetterRecord,
    ProviderDeadLetterSink,
    ProviderMetric,
    ProviderQuarantineRecord,
    ProviderQuarantineSink,
    ProviderTelemetrySink,
)


ConnectionFactory = Callable[[], Any]
_LOGGER = logging.getLogger(__name__)


class LegalProviderOperationsSink(
    ProviderQuarantineSink,
    ProviderDeadLetterSink,
    ProviderTelemetrySink,
    Protocol,
):
    """One injected boundary for all provider operational append streams."""


class PostgresLegalProviderOperationsSink:
    """Persist operational observations using one transaction per append."""

    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory | None = None,
        schema_name: str | None = None,
    ) -> None:
        self._connect = (
            connection_factory or connect_legal_operations_database
        )
        self._schema = validate_database_identifier(
            schema_name
            or os.environ.get("REALYTICS_DATABASE_SCHEMA")
            or "public",
            label="database schema",
        )

    def store(self, record: ProviderQuarantineRecord) -> None:
        self._execute(
            """
            INSERT INTO legal_provider_quarantine (
                legal_provider_quarantine_id,
                provider_id, operation, reason_code, payload_sha256,
                target, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                f"legal-provider-quarantine:{uuid4()}",
                record.provider_id,
                record.operation,
                record.reason_code,
                record.payload_digest,
                record.target,
                record.occurred_at,
            ),
        )

    def enqueue(self, record: ProviderDeadLetterRecord) -> None:
        self._execute(
            """
            INSERT INTO legal_provider_dead_letters (
                legal_provider_dead_letter_id,
                provider_id, operation, reason_code, attempts,
                target, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                f"legal-provider-dead-letter:{uuid4()}",
                record.provider_id,
                record.operation,
                record.reason_code,
                record.attempts,
                record.target,
                record.occurred_at,
            ),
        )

    def record(self, metric: ProviderMetric) -> None:
        self._execute(
            """
            INSERT INTO legal_provider_metrics (
                legal_provider_metric_id,
                provider_id, operation, outcome, latency_ms, attempts,
                rate_limited, local_hit, freshness, tags_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                f"legal-provider-metric:{uuid4()}",
                metric.provider_id,
                metric.operation,
                metric.outcome,
                metric.latency_ms,
                metric.attempts,
                metric.rate_limited,
                metric.local_hit,
                metric.freshness,
                _json_object(metric.tags),
            ),
        )

    def _execute(self, sql: str, params: tuple[Any, ...]) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SET LOCAL search_path TO {self._schema}, public, pg_temp"
                )
                cursor.execute(sql, params)


class FailureIsolatedLegalProviderOperationsSink:
    """Keep optional observability persistence from breaking legal retrieval."""

    def __init__(
        self,
        delegate: LegalProviderOperationsSink,
        *,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self._delegate = delegate
        self._on_error = on_error or _log_sink_error

    def store(self, record: ProviderQuarantineRecord) -> None:
        self._call("quarantine", lambda: self._delegate.store(record))

    def enqueue(self, record: ProviderDeadLetterRecord) -> None:
        self._call("dead_letter", lambda: self._delegate.enqueue(record))

    def record(self, metric: ProviderMetric) -> None:
        self._call("metric", lambda: self._delegate.record(metric))

    def _call(self, stream: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except Exception as exc:  # pragma: no cover - exact driver errors vary
            self._on_error(stream, exc)


def _json_object(value: Any) -> str:
    import json

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _log_sink_error(stream: str, error: Exception) -> None:
    _LOGGER.warning(
        "Legal provider %s sink append failed (%s).",
        stream,
        type(error).__name__,
    )


__all__ = [
    "FailureIsolatedLegalProviderOperationsSink",
    "LegalProviderOperationsSink",
    "PostgresLegalProviderOperationsSink",
]
