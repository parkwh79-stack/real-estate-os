"""Tenant-scoped PostgreSQL authority for materialized Deal State rows."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from copy import deepcopy
from typing import Any

from db.postgres_runtime_pool import api_tenant_transaction


TransactionFactory = Callable[[str], AbstractContextManager[Any]]


class DealStateRepositoryError(RuntimeError):
    """Normalized Deal State authority could not be accessed safely."""


class DealStatePayloadError(DealStateRepositoryError):
    """A persisted Deal State row violates tenant/project lineage."""


class PostgresDealStateRepository:
    def __init__(
        self,
        *,
        tenant_id: str,
        transaction_factory: TransactionFactory = api_tenant_transaction,
    ) -> None:
        self.tenant_id = str(tenant_id or "").strip()
        if not self.tenant_id:
            raise ValueError("tenant_id is required")
        if transaction_factory is None:
            raise TypeError("transaction_factory is required")
        self._transaction = transaction_factory

    def upsert_records(
        self,
        project_id: str,
        records: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized_project_id = self._required(project_id, "project_id")
        normalized = [deepcopy(dict(record)) for record in records]
        if not normalized:
            return []
        sql = """
            INSERT INTO realytics.deal_state_records (
                tenant_id,
                project_id,
                record_id,
                record_type,
                key,
                calculation_run_id,
                source_type,
                review_status,
                updated_at,
                payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (project_id, record_id) DO UPDATE SET
                record_type = EXCLUDED.record_type,
                key = EXCLUDED.key,
                calculation_run_id = EXCLUDED.calculation_run_id,
                source_type = EXCLUDED.source_type,
                review_status = EXCLUDED.review_status,
                updated_at = EXCLUDED.updated_at,
                payload_json = EXCLUDED.payload_json
            WHERE deal_state_records.tenant_id = EXCLUDED.tenant_id
            RETURNING tenant_id, project_id, record_id
        """
        try:
            with self._transaction(self.tenant_id) as connection:
                for record in normalized:
                    self._validate_record_scope(record, normalized_project_id)
                    row = connection.execute(
                        sql,
                        (
                            self.tenant_id,
                            normalized_project_id,
                            record["recordId"],
                            record.get("recordType"),
                            record.get("key"),
                            record.get("calculationRunId"),
                            record.get("sourceType"),
                            record.get("reviewStatus"),
                            record.get("updatedAt"),
                            self._json_payload(record),
                        ),
                    ).fetchone()
                    if row is None or (
                        str(self._value(row, 0, "tenant_id") or "")
                        != self.tenant_id
                        or str(self._value(row, 1, "project_id") or "")
                        != normalized_project_id
                        or str(self._value(row, 2, "record_id") or "")
                        != str(record["recordId"])
                    ):
                        raise DealStatePayloadError(
                            "Deal State write returned an invalid scope"
                        )
        except DealStateRepositoryError:
            raise
        except Exception as exc:
            raise DealStateRepositoryError(
                "Normalized Deal State write authority is unavailable"
            ) from exc
        return normalized

    def list_records(
        self,
        project_id: str,
        *,
        record_type: str | None = None,
        calculation_run_id: str | None = None,
        review_status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        normalized_project_id = self._required(project_id, "project_id")
        predicates = ["tenant_id = %s", "project_id = %s"]
        params: list[Any] = [self.tenant_id, normalized_project_id]
        optional_filters = (
            ("record_type", record_type),
            ("calculation_run_id", calculation_run_id),
            ("review_status", review_status),
        )
        for column, value in optional_filters:
            normalized_value = str(value or "").strip()
            if normalized_value:
                predicates.append(f"{column} = %s")
                params.append(normalized_value)
        params.append(max(1, min(int(limit or 200), 1000)))
        sql = f"""
            SELECT
                tenant_id,
                project_id,
                record_id,
                record_type,
                calculation_run_id,
                review_status,
                payload_json
            FROM realytics.deal_state_records
            WHERE {' AND '.join(predicates)}
            ORDER BY updated_at DESC NULLS LAST, record_id DESC
            LIMIT %s
        """
        try:
            with self._transaction(self.tenant_id) as connection:
                rows = connection.execute(sql, tuple(params)).fetchall()
        except DealStateRepositoryError:
            raise
        except Exception as exc:
            raise DealStateRepositoryError(
                "Normalized Deal State read authority is unavailable"
            ) from exc

        records: list[dict[str, Any]] = []
        try:
            for row in rows:
                if (
                    str(self._value(row, 0, "tenant_id") or "")
                    != self.tenant_id
                    or str(self._value(row, 1, "project_id") or "")
                    != normalized_project_id
                ):
                    raise DealStatePayloadError(
                        "Deal State query returned data outside its scope"
                    )
                payload = self._value(row, 6, "payload_json")
                if not isinstance(payload, Mapping):
                    raise DealStatePayloadError(
                        "Deal State payload_json must be an object"
                    )
                record = deepcopy(dict(payload))
                record.update(
                    {
                        "tenantId": self.tenant_id,
                        "projectId": normalized_project_id,
                        "recordId": str(
                            self._value(row, 2, "record_id") or ""
                        ),
                        "recordType": self._value(row, 3, "record_type"),
                        "calculationRunId": self._value(
                            row, 4, "calculation_run_id"
                        ),
                        "reviewStatus": self._value(
                            row, 5, "review_status"
                        ),
                    }
                )
                records.append(record)
        except DealStateRepositoryError:
            raise
        except Exception as exc:
            raise DealStatePayloadError(
                "Deal State query returned malformed data"
            ) from exc
        return records

    def _validate_record_scope(
        self,
        record: Mapping[str, Any],
        project_id: str,
    ) -> None:
        if (
            str(record.get("tenantId") or "").strip() != self.tenant_id
            or str(record.get("projectId") or "").strip() != project_id
            or not str(record.get("recordId") or "").strip()
        ):
            raise DealStatePayloadError(
                "Deal State write scope does not match its repository"
            )

    @staticmethod
    def _required(value: Any, label: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{label} is required")
        return normalized

    @staticmethod
    def _value(row: Any, index: int, key: str) -> Any:
        if isinstance(row, Mapping):
            return row.get(key)
        return row[index]

    @staticmethod
    def _json_payload(value: Mapping[str, Any]) -> str:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )


__all__ = [
    "DealStatePayloadError",
    "DealStateRepositoryError",
    "PostgresDealStateRepository",
]
