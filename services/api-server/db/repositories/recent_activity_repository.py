"""Tenant- and user-scoped recent project activity reads.

The activity log is an audit relation, not a project authority.  This
repository therefore accepts only project ids that have already passed the
project repository's tenant boundary and returns the latest successful API
request for the verified user.  Tenant scope is enforced both by the runtime
transaction (and FORCE RLS) and by explicit SQL predicates.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any

from db.postgres_runtime_pool import api_tenant_transaction


TransactionFactory = Callable[[str], AbstractContextManager[Any]]


class RecentActivityRepositoryError(RuntimeError):
    """The normalized activity authority could not provide a safe result."""


class PostgresRecentActivityRepository:
    """Read successful project-route activity for one verified principal."""

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

    def latest_successful_project_activity(
        self,
        project_ids: Iterable[str],
    ) -> dict[str, str]:
        """Return each requested project's latest successful route timestamp.

        Failed/denied requests are deliberately excluded so a rejected access
        attempt cannot make a project appear as legitimate resumable work.
        Unknown rows are treated as an integrity failure rather than being
        returned or silently re-scoped.
        """

        normalized_project_ids = tuple(
            dict.fromkeys(
                normalized
                for value in project_ids
                if (normalized := str(value or "").strip())
            )
        )
        if not normalized_project_ids:
            return {}

        sql = """
            SELECT
                tenant_id,
                user_id,
                project_id,
                MAX(created_at) AS last_activity_at
            FROM realytics.user_activity_logs
            WHERE tenant_id = %s
              AND user_id = %s
              AND project_id = ANY(%s::text[])
              AND event_type = 'api_request'
              AND status_code >= 200
              AND status_code < 400
              AND (
                    path = '/projects/' || project_id
                    OR starts_with(path, '/projects/' || project_id || '/')
              )
            GROUP BY tenant_id, user_id, project_id
        """
        try:
            with self._transaction(self.tenant_id) as connection:
                rows = connection.execute(
                    sql,
                    (
                        self.tenant_id,
                        self.user_id,
                        list(normalized_project_ids),
                    ),
                ).fetchall()
        except RecentActivityRepositoryError:
            raise
        except Exception as exc:
            raise RecentActivityRepositoryError(
                "Normalized recent activity is unavailable"
            ) from exc

        requested = frozenset(normalized_project_ids)
        result: dict[str, str] = {}
        try:
            for row in rows:
                row_tenant = str(
                    self._row_value(row, 0, "tenant_id") or ""
                )
                row_user = str(self._row_value(row, 1, "user_id") or "")
                project_id = str(
                    self._row_value(row, 2, "project_id") or ""
                )
                if (
                    row_tenant != self.tenant_id
                    or row_user != self.user_id
                    or project_id not in requested
                ):
                    raise RecentActivityRepositoryError(
                        "Normalized recent activity returned data outside its scope"
                    )
                timestamp = self._timestamp_text(
                    self._row_value(row, 3, "last_activity_at")
                )
                if not timestamp:
                    raise RecentActivityRepositoryError(
                        "Normalized recent activity returned a blank timestamp"
                    )
                result[project_id] = timestamp
        except RecentActivityRepositoryError:
            raise
        except Exception as exc:
            raise RecentActivityRepositoryError(
                "Normalized recent activity returned malformed data"
            ) from exc
        return result

    @staticmethod
    def _required_text(value: str, label: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{label} is required")
        return normalized

    @staticmethod
    def _timestamp_text(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value or "").strip()

    @staticmethod
    def _row_value(row: Any, index: int, key: str) -> Any:
        if isinstance(row, Mapping):
            return row[key]
        return row[index]


__all__ = [
    "PostgresRecentActivityRepository",
    "RecentActivityRepositoryError",
    "TransactionFactory",
]
