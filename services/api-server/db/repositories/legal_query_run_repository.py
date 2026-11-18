"""Append-only audit publisher for project-scoped legal search executions."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from db.postgres import connect
from db.postgres_runtime_privileges import validate_database_identifier
from legal_intelligence.models import (
    LegalSearchQuery,
    LegalSearchResponse,
    ProviderResult,
    ProviderStatus,
    SourceTransport,
)


ConnectionFactory = Callable[[], Any]
_SAFE_IDEMPOTENCY_KEY = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$"
)
_TRANSPORTS = {
    SourceTransport.LOCAL_VERIFIED_SNAPSHOT: "local_db",
    SourceTransport.LAW_GO_KR_DIRECT_API: "direct_api",
    SourceTransport.SELF_HOSTED_MCP: "self_hosted_mcp",
    SourceTransport.REMOTE_MCP: "remote_mcp",
}


class LegalQueryRunConflict(ValueError):
    """An idempotency key was reused for a different legal search."""


@dataclass(frozen=True)
class LegalQueryAuditContext:
    tenant_id: str
    project_id: str
    requested_by: str
    idempotency_key: str

    def __post_init__(self) -> None:
        for name in ("tenant_id", "project_id", "requested_by"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            if value.casefold() in {"anonymous", "unknown"}:
                raise ValueError(f"{name} must identify a governed principal")
        if not _SAFE_IDEMPOTENCY_KEY.fullmatch(self.idempotency_key):
            raise ValueError(
                "idempotency_key may contain only letters, digits, colon, "
                "dot, underscore, and hyphen"
            )


class PostgresLegalQueryRunRepository:
    """Write one immutable run and ordered lifecycle/provider events."""

    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory = connect,
        schema_name: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._connect = connection_factory
        self._schema = validate_database_identifier(
            schema_name
            or os.environ.get("REALYTICS_DATABASE_SCHEMA")
            or "public",
            label="database schema",
        )
        self._clock = clock

    def record_search(
        self,
        context: LegalQueryAuditContext,
        query: LegalSearchQuery,
        response: LegalSearchResponse,
        *,
        freshness_policy_version: str,
        ranking_policy_version: str,
    ) -> str:
        requested_at = _aware(self._clock())
        query_hash = hashlib.sha256(
            query.text.encode("utf-8")
        ).hexdigest()
        transports = _requested_transports(response)
        run_id = _stable_id(
            "legal-query-run",
            {
                "tenantId": context.tenant_id,
                "projectId": context.project_id,
                "idempotencyKey": context.idempotency_key,
            },
        )
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SET LOCAL search_path TO {self._schema}, public, pg_temp"
                )
                cursor.execute(
                    "SELECT set_config('realytics.tenant_id', %s, true)",
                    (context.tenant_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO legal_query_runs (
                        legal_query_run_id,
                        tenant_id,
                        project_id,
                        query_text,
                        query_hash,
                        as_of_date,
                        data_classification,
                        requested_transports,
                        freshness_policy_version,
                        ranking_policy_version,
                        idempotency_key,
                        requested_by,
                        requested_at,
                        metadata_json
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s::jsonb
                    )
                    ON CONFLICT (tenant_id, project_id, idempotency_key)
                    DO NOTHING
                    RETURNING legal_query_run_id
                    """,
                    (
                        run_id,
                        context.tenant_id,
                        context.project_id,
                        query.text,
                        query_hash,
                        query.as_of,
                        query.data_classification.value,
                        transports,
                        freshness_policy_version,
                        ranking_policy_version,
                        context.idempotency_key,
                        context.requested_by,
                        requested_at,
                        _json(
                            {
                                "resourceKinds": [
                                    item.value for item in query.resource_kinds
                                ],
                                "limit": query.limit,
                                "identifierCount": len(query.identifiers),
                                "approvedAliasCount": len(
                                    query.approved_aliases
                                ),
                            }
                        ),
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    return self._assert_exact_replay(
                        cursor,
                        context,
                        query_hash=query_hash,
                        as_of_date=query.as_of,
                        data_classification=query.data_classification.value,
                        requested_transports=transports,
                        freshness_policy_version=freshness_policy_version,
                        ranking_policy_version=ranking_policy_version,
                    )
                self._append_events(
                    cursor,
                    run_id,
                    context,
                    response,
                    occurred_at=requested_at,
                )
        return run_id

    def _assert_exact_replay(
        self,
        cursor: Any,
        context: LegalQueryAuditContext,
        *,
        query_hash: str,
        as_of_date: Any,
        data_classification: str,
        requested_transports: list[str],
        freshness_policy_version: str,
        ranking_policy_version: str,
    ) -> str:
        cursor.execute(
            """
            SELECT
                legal_query_run_id,
                query_hash,
                as_of_date,
                data_classification,
                requested_transports,
                freshness_policy_version,
                ranking_policy_version,
                requested_by
            FROM legal_query_runs
            WHERE tenant_id = %s
              AND project_id = %s
              AND idempotency_key = %s
            """,
            (
                context.tenant_id,
                context.project_id,
                context.idempotency_key,
            ),
        )
        row = cursor.fetchone()
        expected = (
            query_hash,
            as_of_date,
            data_classification,
            requested_transports,
            freshness_policy_version,
            ranking_policy_version,
            context.requested_by,
        )
        if row is None or tuple(row[1:]) != expected:
            raise LegalQueryRunConflict(
                "legal query idempotency key was reused with different input"
            )
        return str(row[0])

    def _append_events(
        self,
        cursor: Any,
        run_id: str,
        context: LegalQueryAuditContext,
        response: LegalSearchResponse,
        *,
        occurred_at: datetime,
    ) -> None:
        events: list[dict[str, Any]] = [
            {
                "from": None,
                "to": "pending",
                "transport": None,
                "outputHash": None,
                "errorCode": None,
                "metadata": {"event": "query_accepted"},
            },
            {
                "from": "pending",
                "to": "running",
                "transport": None,
                "outputHash": None,
                "errorCode": None,
                "metadata": {"event": "provider_execution_started"},
            },
        ]
        for provider in response.provider_trace:
            events.append(_provider_event(provider))
        terminal = (
            "failed"
            if response.provider_trace
            and all(
                item.status
                in {ProviderStatus.FAILED, ProviderStatus.CIRCUIT_OPEN}
                for item in response.provider_trace
            )
            else "completed"
        )
        events.append(
            {
                "from": "running",
                "to": terminal,
                "transport": None,
                "outputHash": _hash_payload(
                    response.model_dump(mode="json")
                ),
                "errorCode": (
                    "ALL_PROVIDERS_FAILED" if terminal == "failed" else None
                ),
                "metadata": {
                    "authoritativeHitCount": len(
                        response.authoritative_hits
                    ),
                    "candidateHitCount": len(response.candidate_hits),
                    "providerCount": len(response.provider_trace),
                    "satisfiedByLocalSnapshot": (
                        response.satisfied_by_local_snapshot
                    ),
                },
            }
        )
        for sequence, event in enumerate(events, start=1):
            cursor.execute(
                """
                INSERT INTO legal_query_run_events (
                    legal_query_event_id,
                    tenant_id,
                    project_id,
                    legal_query_run_id,
                    sequence_number,
                    from_status,
                    to_status,
                    provider_id,
                    transport,
                    output_hash,
                    error_code,
                    occurred_at,
                    metadata_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, NULL,
                    %s, %s, %s, %s, %s::jsonb
                )
                """,
                (
                    _stable_id(
                        "legal-query-event",
                        {"runId": run_id, "sequence": sequence},
                    ),
                    context.tenant_id,
                    context.project_id,
                    run_id,
                    sequence,
                    event["from"],
                    event["to"],
                    event["transport"],
                    event["outputHash"],
                    event["errorCode"],
                    occurred_at,
                    _json(event["metadata"]),
                ),
            )


def _provider_event(provider: ProviderResult) -> dict[str, Any]:
    diagnostics = tuple(item.code for item in provider.diagnostics)
    transport = _TRANSPORTS.get(provider.transport)
    return {
        "from": "running",
        "to": "running",
        "transport": transport,
        "outputHash": _hash_payload(provider.model_dump(mode="json")),
        "errorCode": diagnostics[0] if diagnostics else None,
        "metadata": {
            "providerKey": provider.provider_id,
            "status": provider.status.value,
            "attempts": provider.attempts,
            "coverage": provider.coverage.value,
            "freshness": provider.freshness.value,
            "diagnosticCodes": list(diagnostics),
            "targetErrors": [
                {
                    "target": item.target,
                    "code": item.code,
                    "retryable": item.retryable,
                    "retryAfterSeconds": item.retry_after_seconds,
                }
                for item in provider.diagnostics
                if item.target is not None
            ],
        },
    }


def _requested_transports(response: LegalSearchResponse) -> list[str]:
    return sorted(
        {
            mapped
            for provider in response.provider_trace
            if (mapped := _TRANSPORTS.get(provider.transport)) is not None
        }
    )


def _stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}:{_hash_payload(payload)[:32]}"


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def _json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("legal query audit clock must be timezone-aware")
    return value


__all__ = [
    "LegalQueryAuditContext",
    "LegalQueryRunConflict",
    "PostgresLegalQueryRunRepository",
]
