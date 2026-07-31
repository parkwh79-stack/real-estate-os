"""Tenant-scoped CalculationRun selection for authoritative exports."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from copy import deepcopy
from typing import Any

from db.postgres_runtime_pool import api_tenant_transaction


TransactionFactory = Callable[[str], AbstractContextManager[Any]]


class CalculationRunRepositoryError(RuntimeError):
    """The normalized CalculationRun authority could not be read safely."""


class CalculationRunPayloadError(CalculationRunRepositoryError):
    """A normalized CalculationRun row violates its persisted JSON contract."""


def calculation_run_parent_id(record: Mapping[str, Any]) -> str | None:
    for candidate in (
        record.get("parentCalculationRunId"),
        record.get("parent_calculation_run_id"),
        (record.get("inputSnapshot") or {}).get("parentCalculationRunId")
        if isinstance(record.get("inputSnapshot"), Mapping)
        else None,
        (record.get("sourceLocator") or {}).get("parentCalculationRunId")
        if isinstance(record.get("sourceLocator"), Mapping)
        else None,
    ):
        normalized = str(candidate or "").strip()
        if normalized:
            return normalized
    return None


def select_local_export_runs(
    records: Sequence[Mapping[str, Any]],
    *,
    dependent_engine_keys: Sequence[str],
    anchor_calculation_run_id: str | None = None,
) -> dict[str, Any]:
    """Compatibility selector with the same lineage rules as PostgreSQL."""

    normalized = [deepcopy(dict(record)) for record in records]
    completed = [
        record
        for record in normalized
        if str(record.get("status") or "completed").strip().casefold()
        == "completed"
    ]
    completed.sort(
        key=lambda record: (
            str(record.get("createdAt") or record.get("created_at") or ""),
            str(
                record.get("calculationRunId")
                or record.get("calculation_run_id")
                or ""
            ),
        ),
        reverse=True,
    )
    anchors = [
        record
        for record in completed
        if str(record.get("engineKey") or record.get("engine_key") or "")
        == "cash_flow_engine"
        and (
            not anchor_calculation_run_id
            or str(
                record.get("calculationRunId")
                or record.get("calculation_run_id")
                or ""
            )
            == anchor_calculation_run_id
        )
    ]
    anchor = anchors[0] if anchors else None
    anchor_id = str((anchor or {}).get("calculationRunId") or "").strip()
    runs: dict[str, dict[str, Any] | None] = {
        "cash_flow_engine": anchor,
        **{key: None for key in dependent_engine_keys},
    }
    missing: list[str] = []
    ineligible: list[str] = []
    if anchor is not None:
        for engine_key in dependent_engine_keys:
            candidates = [
                record
                for record in completed
                if str(record.get("engineKey") or record.get("engine_key") or "")
                == engine_key
            ]
            eligible = [
                record
                for record in candidates
                if calculation_run_parent_id(record) == anchor_id
            ]
            if eligible:
                selected = eligible[0]
                selected["parentCalculationRunId"] = anchor_id
                runs[engine_key] = selected
            elif candidates:
                ineligible.append(engine_key)
            else:
                missing.append(engine_key)
    else:
        missing.extend(dependent_engine_keys)
    return {
        "anchor": anchor,
        "runs": runs,
        "missingEngineKeys": missing,
        "ineligibleEngineKeys": ineligible,
        "reportEligible": bool(anchor) and not missing and not ineligible,
    }


class PostgresCalculationRunRepository:
    """Read CalculationRun authority inside one tenant-scoped transaction."""

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

    def list_runs(
        self,
        project_id: str,
        *,
        engine_key: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List normalized runs without bypassing the project tenant's RLS.

        The runtime transaction establishes the transaction-local tenant
        setting used by FORCE RLS.  Explicit tenant/project predicates keep
        the query indexable and the returned rows are checked again before
        they cross the repository boundary.
        """

        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise ValueError("project_id is required")
        normalized_engine_key = str(engine_key or "").strip() or None
        normalized_limit = max(1, min(int(limit or 100), 500))
        predicates = ["tenant_id = %s", "project_id = %s"]
        params: list[Any] = [self.tenant_id, normalized_project_id]
        if normalized_engine_key is not None:
            predicates.append("engine_key = %s")
            params.append(normalized_engine_key)
        params.append(normalized_limit)
        sql = f"""
            SELECT
                'run' AS row_kind,
                tenant_id,
                project_id,
                calculation_run_id,
                engine_key,
                status,
                created_at,
                payload_json
            FROM realytics.calculation_runs
            WHERE {' AND '.join(predicates)}
            ORDER BY created_at DESC NULLS LAST, calculation_run_id DESC
            LIMIT %s
        """
        try:
            with self._transaction(self.tenant_id) as connection:
                rows = connection.execute(sql, tuple(params)).fetchall()
        except CalculationRunRepositoryError:
            raise
        except Exception as exc:
            raise CalculationRunRepositoryError(
                "Normalized CalculationRun authority is unavailable"
            ) from exc
        return self._records_from_rows(
            rows,
            project_id=normalized_project_id,
            engine_key=normalized_engine_key,
        )

    def get_run(
        self,
        project_id: str,
        calculation_run_id: str,
    ) -> dict[str, Any] | None:
        """Resolve an exact CalculationRun without a bounded list scan."""

        normalized_project_id = str(project_id or "").strip()
        normalized_run_id = str(calculation_run_id or "").strip()
        if not normalized_project_id:
            raise ValueError("project_id is required")
        if not normalized_run_id:
            raise ValueError("calculation_run_id is required")
        sql = """
            SELECT
                'run' AS row_kind,
                tenant_id,
                project_id,
                calculation_run_id,
                engine_key,
                status,
                created_at,
                payload_json
            FROM realytics.calculation_runs
            WHERE tenant_id = %s
              AND project_id = %s
              AND calculation_run_id = %s
            LIMIT 1
        """
        try:
            with self._transaction(self.tenant_id) as connection:
                rows = connection.execute(
                    sql,
                    (self.tenant_id, normalized_project_id, normalized_run_id),
                ).fetchall()
        except CalculationRunRepositoryError:
            raise
        except Exception as exc:
            raise CalculationRunRepositoryError(
                "Normalized CalculationRun authority is unavailable"
            ) from exc
        records = self._records_from_rows(
            rows,
            project_id=normalized_project_id,
            calculation_run_id=normalized_run_id,
        )
        if len(records) > 1:
            raise CalculationRunPayloadError(
                "CalculationRun exact query returned more than one row"
            )
        return records[0] if records else None

    def latest_runs_by_engine(self, project_id: str) -> list[dict[str, Any]]:
        """Return the latest normalized run for every populated engine key."""

        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise ValueError("project_id is required")
        sql = """
            SELECT DISTINCT ON (engine_key)
                'run' AS row_kind,
                tenant_id,
                project_id,
                calculation_run_id,
                engine_key,
                status,
                created_at,
                payload_json
            FROM realytics.calculation_runs
            WHERE tenant_id = %s
              AND project_id = %s
              AND engine_key IS NOT NULL
              AND btrim(engine_key) <> ''
            ORDER BY engine_key,
                     created_at DESC NULLS LAST,
                     calculation_run_id DESC
        """
        try:
            with self._transaction(self.tenant_id) as connection:
                rows = connection.execute(
                    sql,
                    (self.tenant_id, normalized_project_id),
                ).fetchall()
        except CalculationRunRepositoryError:
            raise
        except Exception as exc:
            raise CalculationRunRepositoryError(
                "Normalized CalculationRun authority is unavailable"
            ) from exc
        records = self._records_from_rows(
            rows,
            project_id=normalized_project_id,
        )
        engine_keys = [str(record.get("engineKey") or "") for record in records]
        if len(engine_keys) != len(set(engine_keys)):
            raise CalculationRunPayloadError(
                "Latest CalculationRun query returned duplicate engine keys"
            )
        return records

    def select_export_runs(
        self,
        project_id: str,
        *,
        dependent_engine_keys: Sequence[str],
        anchor_calculation_run_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise ValueError("project_id is required")
        engine_keys = tuple(
            dict.fromkeys(str(key or "").strip() for key in dependent_engine_keys)
        )
        if not engine_keys or any(not key for key in engine_keys):
            raise ValueError("dependent_engine_keys are required")
        normalized_anchor = str(anchor_calculation_run_id or "").strip() or None
        sql = """
            WITH requested(engine_key) AS (
                SELECT unnest(%s::TEXT[])
            ),
            anchor AS (
                SELECT
                    tenant_id,
                    project_id,
                    calculation_run_id,
                    engine_key,
                    status,
                    created_at,
                    payload_json
                FROM realytics.calculation_runs
                WHERE tenant_id = %s
                  AND project_id = %s
                  AND engine_key = 'cash_flow_engine'
                  AND status = 'completed'
                  AND (%s::TEXT IS NULL OR calculation_run_id = %s)
                ORDER BY created_at DESC, calculation_run_id DESC
                LIMIT 1
            ),
            candidates AS (
                SELECT
                    run.tenant_id,
                    run.project_id,
                    run.calculation_run_id,
                    run.engine_key,
                    run.status,
                    run.created_at,
                    run.payload_json,
                    COALESCE(
                        run.payload_json ->> 'parentCalculationRunId',
                        run.payload_json #>> '{inputSnapshot,parentCalculationRunId}',
                        run.payload_json #>> '{sourceLocator,parentCalculationRunId}'
                    ) AS parent_calculation_run_id
                FROM realytics.calculation_runs AS run
                WHERE run.tenant_id = %s
                  AND run.project_id = %s
                  AND run.engine_key IN (SELECT engine_key FROM requested)
                  AND run.status = 'completed'
            ),
            eligible_ranked AS (
                SELECT
                    candidate.*,
                    row_number() OVER (
                        PARTITION BY candidate.engine_key
                        ORDER BY candidate.created_at DESC,
                                 candidate.calculation_run_id DESC
                    ) AS lineage_rank
                FROM candidates AS candidate
                JOIN anchor
                  ON candidate.parent_calculation_run_id
                     = anchor.calculation_run_id
            ),
            presence AS (
                SELECT
                    requested.engine_key,
                    count(candidate.calculation_run_id) AS candidate_count,
                    count(candidate.calculation_run_id) FILTER (
                        WHERE candidate.parent_calculation_run_id
                              = anchor.calculation_run_id
                    ) AS eligible_count
                FROM requested
                LEFT JOIN candidates AS candidate
                  ON candidate.engine_key = requested.engine_key
                LEFT JOIN anchor ON TRUE
                GROUP BY requested.engine_key
            )
            SELECT
                'anchor' AS row_kind,
                anchor.tenant_id,
                anchor.project_id,
                anchor.calculation_run_id,
                anchor.engine_key,
                anchor.status,
                anchor.created_at,
                anchor.payload_json,
                NULL::TEXT AS parent_calculation_run_id,
                NULL::BIGINT AS candidate_count,
                NULL::BIGINT AS eligible_count
            FROM anchor
            UNION ALL
            SELECT
                'eligible',
                eligible.tenant_id,
                eligible.project_id,
                eligible.calculation_run_id,
                eligible.engine_key,
                eligible.status,
                eligible.created_at,
                eligible.payload_json,
                eligible.parent_calculation_run_id,
                NULL::BIGINT,
                NULL::BIGINT
            FROM eligible_ranked AS eligible
            WHERE eligible.lineage_rank = 1
            UNION ALL
            SELECT
                'presence',
                %s::TEXT,
                %s::TEXT,
                NULL::TEXT,
                presence.engine_key,
                NULL::TEXT,
                NULL::TIMESTAMPTZ,
                NULL::JSONB,
                NULL::TEXT,
                presence.candidate_count,
                presence.eligible_count
            FROM presence
            ORDER BY row_kind, engine_key
        """
        params = (
            list(engine_keys),
            self.tenant_id,
            normalized_project_id,
            normalized_anchor,
            normalized_anchor,
            self.tenant_id,
            normalized_project_id,
            self.tenant_id,
            normalized_project_id,
        )
        try:
            with self._transaction(self.tenant_id) as connection:
                rows = connection.execute(sql, params).fetchall()
        except CalculationRunRepositoryError:
            raise
        except Exception as exc:
            raise CalculationRunRepositoryError(
                "Normalized CalculationRun authority is unavailable"
            ) from exc
        return self._selection_from_rows(
            rows,
            project_id=normalized_project_id,
            dependent_engine_keys=engine_keys,
        )

    def _selection_from_rows(
        self,
        rows: Sequence[Any],
        *,
        project_id: str,
        dependent_engine_keys: Sequence[str],
    ) -> dict[str, Any]:
        anchor: dict[str, Any] | None = None
        runs: dict[str, dict[str, Any] | None] = {
            "cash_flow_engine": None,
            **{key: None for key in dependent_engine_keys},
        }
        presence: dict[str, tuple[int, int]] = {
            key: (0, 0) for key in dependent_engine_keys
        }
        for row in rows:
            row_kind = str(self._value(row, 0, "row_kind") or "")
            tenant_id = str(self._value(row, 1, "tenant_id") or "")
            row_project_id = str(self._value(row, 2, "project_id") or "")
            engine_key = str(self._value(row, 4, "engine_key") or "")
            if tenant_id != self.tenant_id or row_project_id != project_id:
                raise CalculationRunPayloadError(
                    "CalculationRun query returned data outside its tenant scope"
                )
            if row_kind == "presence":
                presence[engine_key] = (
                    int(self._value(row, 9, "candidate_count") or 0),
                    int(self._value(row, 10, "eligible_count") or 0),
                )
                continue
            record = self._record(row)
            if row_kind == "anchor":
                if engine_key != "cash_flow_engine":
                    raise CalculationRunPayloadError("Invalid cash-flow anchor")
                anchor = record
                runs["cash_flow_engine"] = record
            elif row_kind == "eligible":
                if engine_key not in dependent_engine_keys:
                    raise CalculationRunPayloadError("Unexpected dependent engine")
                parent_id = str(
                    self._value(row, 8, "parent_calculation_run_id") or ""
                )
                record["parentCalculationRunId"] = parent_id
                runs[engine_key] = record

        if anchor is None:
            missing = list(dependent_engine_keys)
            ineligible: list[str] = []
        else:
            missing = [key for key, counts in presence.items() if counts[0] == 0]
            ineligible = [
                key
                for key, counts in presence.items()
                if counts[0] > 0 and counts[1] == 0
            ]
        for key, counts in presence.items():
            if counts[1] > 0 and runs[key] is None:
                raise CalculationRunPayloadError(
                    f"Eligible CalculationRun missing for {key}"
                )
        return {
            "anchor": anchor,
            "runs": runs,
            "missingEngineKeys": missing,
            "ineligibleEngineKeys": ineligible,
            "reportEligible": bool(anchor) and not missing and not ineligible,
        }

    @classmethod
    def _record(cls, row: Any) -> dict[str, Any]:
        payload = cls._value(row, 7, "payload_json")
        if not isinstance(payload, Mapping):
            raise CalculationRunPayloadError(
                "CalculationRun payload_json must be an object"
            )
        record = deepcopy(dict(payload))
        record.update(
            {
                "tenantId": str(cls._value(row, 1, "tenant_id") or ""),
                "projectId": str(cls._value(row, 2, "project_id") or ""),
                "calculationRunId": str(
                    cls._value(row, 3, "calculation_run_id") or ""
                ),
                "engineKey": str(cls._value(row, 4, "engine_key") or ""),
                "status": str(cls._value(row, 5, "status") or ""),
            }
        )
        return record

    def _records_from_rows(
        self,
        rows: Sequence[Any],
        *,
        project_id: str,
        engine_key: str | None = None,
        calculation_run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            for row in rows:
                row_tenant_id = str(self._value(row, 1, "tenant_id") or "")
                row_project_id = str(self._value(row, 2, "project_id") or "")
                row_run_id = str(
                    self._value(row, 3, "calculation_run_id") or ""
                )
                row_engine_key = str(self._value(row, 4, "engine_key") or "")
                if (
                    row_tenant_id != self.tenant_id
                    or row_project_id != project_id
                    or (engine_key is not None and row_engine_key != engine_key)
                    or (
                        calculation_run_id is not None
                        and row_run_id != calculation_run_id
                    )
                ):
                    raise CalculationRunPayloadError(
                        "CalculationRun query returned data outside its scope"
                    )
                records.append(self._record(row))
        except CalculationRunRepositoryError:
            raise
        except Exception as exc:
            raise CalculationRunPayloadError(
                "CalculationRun query returned malformed data"
            ) from exc
        return records

    @staticmethod
    def _value(row: Any, index: int, key: str) -> Any:
        if isinstance(row, Mapping):
            return row.get(key)
        return row[index]


__all__ = [
    "CalculationRunPayloadError",
    "CalculationRunRepositoryError",
    "PostgresCalculationRunRepository",
    "calculation_run_parent_id",
    "select_local_export_runs",
]
