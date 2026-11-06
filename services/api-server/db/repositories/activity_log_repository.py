"""Append-only normalized PostgreSQL activity events."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any
from uuid import uuid4

from db.postgres_runtime_pool import api_tenant_transaction


TransactionFactory = Callable[[str], AbstractContextManager[Any]]


class PostgresActivityLogRepository:
    """Append verified request activity inside the request tenant's RLS scope."""

    def __init__(
        self,
        *,
        tenant_id: str,
        user_id: str,
        transaction_factory: TransactionFactory = api_tenant_transaction,
    ) -> None:
        self.tenant_id = self._required_text(tenant_id, "tenant_id")
        self.user_id = self._required_text(user_id, "user_id")
        if transaction_factory is None:
            raise TypeError("transaction_factory is required")
        self._transaction = transaction_factory

    def append_api_request(self, event: Mapping[str, Any]) -> str:
        """Idempotently append one server-observed request event.

        Tenant and user identities come from the normalized membership
        projection, never from the event body.  ``statement_timestamp()`` is
        authoritative so callers cannot move an action forward in the feed by
        supplying a forged client timestamp.
        """

        return self._append("api_request", event)

    def append_project_event(self, event_type: str, event: Mapping[str, Any]) -> str:
        """Append a server-owned project event without changing recent-feed ranking."""
        normalized_type = self._required_text(event_type, "event_type")
        if normalized_type == "api_request":
            raise ValueError("Use append_api_request for observed HTTP activity")
        self._required_text(event.get("projectId"), "project_id")
        return self._append(normalized_type, event)

    def _append(self, event_type: str, event: Mapping[str, Any]) -> str:
        event_id = self._optional_text(event.get("eventId")) or str(uuid4())
        metadata = event.get("metadata")
        metadata_json = json.dumps(
            dict(metadata) if isinstance(metadata, Mapping) else {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=self._json_default,
        )
        sql = """
            INSERT INTO realytics.user_activity_logs (
                event_id,
                event_type,
                created_at,
                user_id,
                project_id,
                run_id,
                method,
                path,
                status_code,
                duration_ms,
                client_host,
                user_agent,
                request_id,
                metadata_json,
                tenant_id
            )
            VALUES (
                %s, %s, statement_timestamp(), %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s::jsonb, %s
            )
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
        """
        params = (
            event_id,
            event_type,
            self.user_id,
            self._optional_text(event.get("projectId")),
            self._optional_text(event.get("runId")),
            self._optional_text(event.get("method")),
            self._optional_text(event.get("path")),
            self._optional_int(event.get("statusCode")),
            self._optional_float(event.get("durationMs")),
            self._optional_text(event.get("clientHost")),
            self._optional_text(event.get("userAgent")),
            self._optional_text(event.get("requestId")),
            metadata_json,
            self.tenant_id,
        )
        with self._transaction(self.tenant_id) as connection:
            row = connection.execute(sql, params).fetchone()
        if row is not None and str(row[0] or "") != event_id:
            raise ValueError("Activity append returned an unexpected event id")
        return event_id

    @staticmethod
    def _required_text(value: Any, label: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{label} is required")
        return normalized

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        return int(value)

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        return float(value)

    @staticmethod
    def _json_default(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)


__all__ = ["PostgresActivityLogRepository", "TransactionFactory"]
