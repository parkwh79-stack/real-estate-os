"""PostgreSQL-authoritative project persistence.

This repository is deliberately separate from the legacy in-memory
``ProjectRepository``.  Every operation enters a tenant-scoped runtime
transaction, repeats the tenant predicate in SQL, and treats normalized
PostgreSQL columns as the authority for tenant identity and state version.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from db.postgres_runtime_pool import api_tenant_transaction
from use_cases.common import deep_merge


TransactionFactory = Callable[[str], AbstractContextManager[Any]]

_PROJECT_COLUMNS = (
    "project_id",
    "tenant_id",
    "state_version",
    "payload_json",
)
_SERVER_META_KEYS = frozenset(
    {
        "tenantId",
        "tenant_id",
        "stateVersion",
        "state_version",
        "createdAt",
        "created_at",
        "updatedAt",
        "updated_at",
        "deletedAt",
        "deleted_at",
    }
)
_SERVER_TOP_LEVEL_KEYS = frozenset(
    {
        "tenantId",
        "tenant_id",
        "stateVersion",
        "state_version",
    }
)

_ACTIVE_PROJECT_PREDICATE = """
    payload_json #>> '{_meta,deletedAt}' IS NULL
    AND COALESCE(payload_json #>> '{project,status}', '') <> 'deleted'
"""


class ProjectVersionConflict(RuntimeError):
    """A project write lost an optimistic-concurrency race."""

    def __init__(
        self,
        *,
        project_id: str,
        expected_state_version: int,
        actual_state_version: int | None,
    ) -> None:
        self.project_id = project_id
        self.expected_state_version = expected_state_version
        self.actual_state_version = actual_state_version
        actual = (
            str(actual_state_version)
            if actual_state_version is not None
            else "unavailable"
        )
        super().__init__(
            "Project state version conflict for "
            f"{project_id!r}: expected {expected_state_version}, actual {actual}"
        )


class ProjectPayloadDataError(RuntimeError):
    """The authoritative JSONB project payload is not a JSON object."""


class PostgresProjectRepository:
    """Tenant-scoped CRUD over ``realytics.projects``.

    ``tenant_id`` must already have been resolved by the verified database
    membership boundary.  Request payloads can never choose or override it.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        transaction_factory: TransactionFactory = api_tenant_transaction,
    ) -> None:
        normalized_tenant_id = str(tenant_id or "").strip()
        if not normalized_tenant_id:
            raise ValueError("tenant_id is required")
        if transaction_factory is None:
            raise TypeError("transaction_factory is required")
        self.tenant_id = normalized_tenant_id
        self._transaction = transaction_factory

    def list(self) -> list[dict[str, Any]]:
        sql = f"""
            SELECT {", ".join(_PROJECT_COLUMNS)}
            FROM realytics.projects
            WHERE tenant_id = %s
              AND {_ACTIVE_PROJECT_PREDICATE}
            ORDER BY updated_at DESC NULLS LAST, project_id
        """
        with self._transaction(self.tenant_id) as connection:
            rows = connection.execute(sql, (self.tenant_id,)).fetchall()
            return [self._record_from_row(row) for row in rows]

    def exists(self, project_id: str) -> bool:
        return self.get(project_id) is not None

    def get(self, project_id: str) -> dict[str, Any] | None:
        normalized_project_id = self._required_project_id(project_id)
        sql = f"""
            SELECT {", ".join(_PROJECT_COLUMNS)}
            FROM realytics.projects
            WHERE tenant_id = %s
              AND project_id = %s
              AND {_ACTIVE_PROJECT_PREDICATE}
        """
        with self._transaction(self.tenant_id) as connection:
            row = connection.execute(
                sql,
                (self.tenant_id, normalized_project_id),
            ).fetchone()
            return self._record_from_row(row) if row is not None else None

    def create(
        self,
        project_data: Mapping[str, Any],
        *,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        with self._transaction(self.tenant_id) as connection:
            project_id_row = connection.execute(
                """
                SELECT nextval(
                    'realytics.project_id_sequence'::regclass
                )::TEXT
                """
            ).fetchone()
            project_id = str(self._row_value(project_id_row, 0, "nextval"))
            record = self._normalize_create(project_id, project_data)
            row = connection.execute(
                f"""
                INSERT INTO realytics.projects (
                    project_id,
                    tenant_id,
                    created_by,
                    payload_json,
                    state_version,
                    updated_at
                )
                VALUES (%s, %s, %s, %s::jsonb, 1, statement_timestamp())
                RETURNING {", ".join(_PROJECT_COLUMNS)}
                """,
                (
                    project_id,
                    self.tenant_id,
                    self._optional_text(created_by),
                    self._json_payload(record),
                ),
            ).fetchone()
            if row is None:  # pragma: no cover - PostgreSQL contract violation
                raise RuntimeError("project INSERT did not return a row")
            return self._record_from_row(row)

    def update(
        self,
        project_id: str,
        project_data: Mapping[str, Any],
        *,
        expected_state_version: int | None = None,
    ) -> dict[str, Any] | None:
        normalized_project_id = self._required_project_id(project_id)
        with self._transaction(self.tenant_id) as connection:
            current_row = self._select_active_for_write(
                connection,
                normalized_project_id,
            )
            if current_row is None:
                return None

            current = self._record_from_row(current_row)
            current_version = self._state_version_from_row(current_row)
            expected = self._expected_version(
                expected_state_version,
                current_version=current_version,
            )
            if expected != current_version:
                raise ProjectVersionConflict(
                    project_id=normalized_project_id,
                    expected_state_version=expected,
                    actual_state_version=current_version,
                )

            patch = self._untrusted_patch(project_data)
            merged = deep_merge(current, patch)
            record = self._normalize_update(
                normalized_project_id,
                merged,
                prior=current,
                next_state_version=current_version + 1,
            )
            return self._cas_update(
                connection,
                project_id=normalized_project_id,
                expected_state_version=expected,
                record=record,
            )

    def delete(
        self,
        project_id: str,
        *,
        expected_state_version: int | None = None,
    ) -> dict[str, Any] | None:
        """Soft-delete a project and return the prior authoritative record."""

        normalized_project_id = self._required_project_id(project_id)
        with self._transaction(self.tenant_id) as connection:
            current_row = self._select_active_for_write(
                connection,
                normalized_project_id,
            )
            if current_row is None:
                return None

            prior = self._record_from_row(current_row)
            current_version = self._state_version_from_row(current_row)
            expected = self._expected_version(
                expected_state_version,
                current_version=current_version,
            )
            if expected != current_version:
                raise ProjectVersionConflict(
                    project_id=normalized_project_id,
                    expected_state_version=expected,
                    actual_state_version=current_version,
                )

            tombstone = deepcopy(prior)
            now = self._now()
            project = dict(tombstone.get("project") or {})
            project["id"] = normalized_project_id
            project["status"] = "deleted"
            tombstone["project"] = project
            metadata = dict(tombstone.get("_meta") or {})
            metadata["deletedAt"] = now
            metadata["updated_at"] = now
            metadata["tenantId"] = self.tenant_id
            metadata["stateVersion"] = current_version + 1
            tombstone["_meta"] = metadata

            self._cas_update(
                connection,
                project_id=normalized_project_id,
                expected_state_version=expected,
                record=tombstone,
                active_only=True,
            )
            return prior

    def _select_active_for_write(
        self,
        connection: Any,
        project_id: str,
    ) -> Any | None:
        return connection.execute(
            f"""
            SELECT {", ".join(_PROJECT_COLUMNS)}
            FROM realytics.projects
            WHERE tenant_id = %s
              AND project_id = %s
              AND {_ACTIVE_PROJECT_PREDICATE}
            """,
            (self.tenant_id, project_id),
        ).fetchone()

    def _cas_update(
        self,
        connection: Any,
        *,
        project_id: str,
        expected_state_version: int,
        record: Mapping[str, Any],
        active_only: bool = True,
    ) -> dict[str, Any]:
        active_predicate = (
            f"AND {_ACTIVE_PROJECT_PREDICATE}" if active_only else ""
        )
        row = connection.execute(
            f"""
            UPDATE realytics.projects
            SET payload_json = %s::jsonb,
                state_version = state_version + 1,
                updated_at = statement_timestamp()
            WHERE tenant_id = %s
              AND project_id = %s
              AND state_version = %s
              {active_predicate}
            RETURNING {", ".join(_PROJECT_COLUMNS)}
            """,
            (
                self._json_payload(record),
                self.tenant_id,
                project_id,
                expected_state_version,
            ),
        ).fetchone()
        if row is not None:
            return self._record_from_row(row)

        version_row = connection.execute(
            """
            SELECT state_version
            FROM realytics.projects
            WHERE tenant_id = %s
              AND project_id = %s
            """,
            (self.tenant_id, project_id),
        ).fetchone()
        actual_version = (
            int(self._row_value(version_row, 0, "state_version"))
            if version_row is not None
            else None
        )
        raise ProjectVersionConflict(
            project_id=project_id,
            expected_state_version=expected_state_version,
            actual_state_version=actual_version,
        )

    def _record_from_row(self, row: Any) -> dict[str, Any]:
        project_id = str(self._row_value(row, 0, "project_id"))
        tenant_id = str(self._row_value(row, 1, "tenant_id"))
        state_version = int(self._row_value(row, 2, "state_version"))
        if tenant_id != self.tenant_id:
            raise ProjectPayloadDataError(
                "PostgreSQL returned a project outside the repository tenant"
            )

        payload = self._decode_payload(
            self._row_value(row, 3, "payload_json")
        )
        record = self._contract_projection(payload)
        for key in _SERVER_TOP_LEVEL_KEYS:
            record.pop(key, None)
        record["id"] = project_id
        project = dict(record.get("project") or {})
        self._clear_project_authority(project)
        project["id"] = project_id
        record["project"] = project
        metadata = dict(record.get("_meta") or {})
        self._clear_tenant_version_metadata(metadata)
        metadata["tenantId"] = tenant_id
        metadata["stateVersion"] = state_version
        record["_meta"] = metadata
        return record

    def _normalize_create(
        self,
        project_id: str,
        project_data: Mapping[str, Any],
    ) -> dict[str, Any]:
        now = self._now()
        record = self._untrusted_patch(project_data)
        project = dict(record.get("project") or {})
        if not project:
            project = {
                key: record.pop(key)
                for key in tuple(record)
                if key in {"name", "type", "status", "review_date"}
            }
        self._clear_project_authority(project)
        project["id"] = project_id
        project["name"] = (
            project.get("name")
            or record.get("name")
            or f"Project {project_id}"
        )
        project["type"] = (
            project.get("type") or record.get("type") or "unknown"
        )
        project.setdefault("status", "draft")
        record["id"] = project_id
        record["project"] = project
        metadata = dict(record.get("_meta") or {})
        self._clear_server_metadata(metadata)
        metadata.update(
            {
                "created_at": now,
                "updated_at": now,
                "tenantId": self.tenant_id,
                "stateVersion": 1,
            }
        )
        record["_meta"] = metadata
        return record

    def _normalize_update(
        self,
        project_id: str,
        merged: Mapping[str, Any],
        *,
        prior: Mapping[str, Any],
        next_state_version: int,
    ) -> dict[str, Any]:
        now = self._now()
        record = deepcopy(dict(merged))
        record["id"] = project_id
        project = dict(record.get("project") or {})
        self._clear_project_authority(project)
        project["id"] = project_id
        project["name"] = (
            project.get("name")
            or (prior.get("project") or {}).get("name")
            or f"Project {project_id}"
        )
        project["type"] = project.get("type") or "unknown"
        project.setdefault("status", "draft")
        record["project"] = project

        prior_metadata = dict(prior.get("_meta") or {})
        metadata = dict(record.get("_meta") or {})
        self._clear_server_metadata(metadata)
        metadata["created_at"] = (
            prior_metadata.get("created_at")
            or prior_metadata.get("createdAt")
            or now
        )
        metadata["updated_at"] = now
        metadata["tenantId"] = self.tenant_id
        metadata["stateVersion"] = next_state_version
        record["_meta"] = metadata
        return record

    @staticmethod
    def _decode_payload(value: Any) -> dict[str, Any]:
        decoded = value
        if isinstance(value, (str, bytes, bytearray)):
            try:
                decoded = json.loads(value)
            except (TypeError, ValueError) as exc:
                raise ProjectPayloadDataError(
                    "PostgreSQL project payload is not valid JSON"
                ) from exc
        if not isinstance(decoded, Mapping):
            raise ProjectPayloadDataError(
                "PostgreSQL project payload must be a JSON object"
            )
        return deepcopy(dict(decoded))

    @classmethod
    def _untrusted_patch(
        cls,
        project_data: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(project_data, Mapping):
            raise TypeError("project_data must be a mapping")
        record = deepcopy(dict(project_data))
        for key in _SERVER_TOP_LEVEL_KEYS:
            record.pop(key, None)
        project = record.get("project")
        if isinstance(project, Mapping):
            normalized_project = dict(project)
            cls._clear_project_authority(normalized_project)
            record["project"] = normalized_project
        metadata = record.get("_meta")
        if isinstance(metadata, Mapping):
            normalized_metadata = dict(metadata)
            cls._clear_server_metadata(normalized_metadata)
            record["_meta"] = normalized_metadata
        elif "_meta" in record:
            record["_meta"] = {}
        return record

    @staticmethod
    def _contract_projection(source: Mapping[str, Any]) -> dict[str, Any]:
        record = deepcopy(dict(source))
        project = dict(record.get("project") or {})
        project_id = str(project.get("id") or record.get("id") or "")
        project["id"] = project_id
        project["name"] = (
            project.get("name")
            or record.get("name")
            or f"Project {project_id}"
        )
        project["type"] = (
            project.get("type") or record.get("type") or "unknown"
        )
        project["status"] = (
            project.get("status") or record.get("status") or "draft"
        )
        record["project"] = project
        if project_id:
            record["id"] = project_id
        return record

    @staticmethod
    def _clear_server_metadata(metadata: dict[str, Any]) -> None:
        for key in _SERVER_META_KEYS:
            metadata.pop(key, None)

    @staticmethod
    def _clear_tenant_version_metadata(metadata: dict[str, Any]) -> None:
        for key in (
            "tenantId",
            "tenant_id",
            "stateVersion",
            "state_version",
        ):
            metadata.pop(key, None)

    @staticmethod
    def _clear_project_authority(project: dict[str, Any]) -> None:
        for key in (
            "id",
            "tenantId",
            "tenant_id",
            "stateVersion",
            "state_version",
        ):
            project.pop(key, None)

    @staticmethod
    def _json_payload(record: Mapping[str, Any]) -> str:
        return json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _row_value(
        row: Any,
        index: int,
        key: str,
    ) -> Any:
        if isinstance(row, Mapping):
            return row[key]
        return row[index]

    @staticmethod
    def _state_version_from_row(row: Any) -> int:
        return int(
            PostgresProjectRepository._row_value(
                row,
                2,
                "state_version",
            )
        )

    @staticmethod
    def _expected_version(
        value: int | None,
        *,
        current_version: int,
    ) -> int:
        if value is None:
            return current_version
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("expected_state_version must be a positive integer")
        return value

    @staticmethod
    def _required_project_id(project_id: str) -> str:
        normalized = str(project_id or "").strip()
        if not normalized:
            raise ValueError("project_id is required")
        return normalized

    @staticmethod
    def _optional_text(value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


__all__ = [
    "PostgresProjectRepository",
    "ProjectPayloadDataError",
    "ProjectVersionConflict",
    "TransactionFactory",
]
