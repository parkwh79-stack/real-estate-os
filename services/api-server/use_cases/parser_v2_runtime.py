from __future__ import annotations

"""Opt-in runtime wiring for normalized Parser V2 PostgreSQL persistence.

The HTTP router still defaults to the legacy deal-state persistence seam.  The
normalized repository is selected only through an explicit environment value
and only when the application itself is running on PostgreSQL.
"""

import os
from contextvars import ContextVar
from typing import Any, Callable, Mapping

from db import postgres
from db.repositories.parser_v2_repository import (
    ParserV2AppendBatch,
    ParserV2ImmutableConflict,
    ParserV2PostgresRepository,
)
from storage import storage_backend
from use_cases.parser_v2_profiles import (
    PROJECT_PARSING_PROFILE_RECORD_TYPE,
    ProjectParsingProfileConflict,
)
from use_cases.parser_v2_records import ImmutableRecordConflict, ParserV2RecordStore


PARSER_V2_PERSISTENCE_ENV = "REALYTICS_PARSER_V2_PERSISTENCE"
LEGACY_PERSISTENCE = "legacy"
NORMALIZED_POSTGRES_PERSISTENCE = "postgres_normalized"

ProjectLookup = Callable[[str], Mapping[str, Any] | None]
RepositoryFactory = Callable[[], ParserV2PostgresRepository]
_DEFAULT_READINESS = object()


class ParserV2PersistenceConfigurationError(RuntimeError):
    """Raised before serving traffic when persistence settings are unsafe."""


class _NormalizedParserV2RecordStore(ParserV2RecordStore):
    """Adapt the tenant-aware repository to the legacy Store callback shape.

    ``ParserV2RecordStore`` deliberately passes tenant identity to its public
    methods, but its historical list callback predates tenant-scoped RLS.  A
    context variable carries that identity only for the duration of a single
    synchronous read, avoiding process-global or cross-request tenant state.
    """

    def __init__(
        self,
        repository: ParserV2PostgresRepository,
        require_project_tenant: Callable[[str, str], None],
    ) -> None:
        self._normalized_repository = repository
        self._require_project_tenant = require_project_tenant
        self._read_scope: ContextVar[tuple[str, str] | None] = ContextVar(
            "parser_v2_normalized_read_scope",
            default=None,
        )
        super().__init__(
            upsert_fn=self._append_record,
            persist_batch_fn=self._append_batch,
            list_fn=self._list_records_in_scope,
        )

    def _records(
        self,
        tenant_id: str,
        project_id: str,
        record_type: str,
    ) -> list[dict[str, Any]]:
        self._require_project_tenant(project_id, tenant_id)
        token = self._read_scope.set((tenant_id, project_id))
        try:
            return super()._records(tenant_id, project_id, record_type)
        finally:
            self._read_scope.reset(token)

    def _list_records_in_scope(
        self,
        project_id: str,
        *,
        record_type: str,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        scope = self._read_scope.get()
        if scope is None or scope[1] != project_id:
            raise ParserV2PersistenceConfigurationError(
                "normalized Parser V2 reads require an explicit tenant/project scope"
            )
        return self._normalized_repository.list_records(
            scope[0],
            project_id,
            record_type,
            limit=limit,
        )

    def _append_record(
        self,
        project_id: str,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        tenant_id = _record_scope(record, project_id)
        self._require_project_tenant(project_id, tenant_id)
        try:
            return self._normalized_repository.append_record(
                tenant_id,
                project_id,
                record,
            )
        except ParserV2ImmutableConflict as exc:
            raise ImmutableRecordConflict(str(exc)) from exc

    def _append_batch(
        self,
        project_id: str,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not records:
            return []
        tenant_id = _record_scope(records[0], project_id)
        if any(_record_scope(record, project_id) != tenant_id for record in records[1:]):
            raise ParserV2PersistenceConfigurationError(
                "a normalized Parser V2 batch cannot cross tenant scope"
            )
        self._require_project_tenant(project_id, tenant_id)
        try:
            result = self._normalized_repository.append_batch(
                tenant_id,
                project_id,
                ParserV2AppendBatch(records=tuple(records)),
            )
            return result["records"]
        except ParserV2ImmutableConflict as exc:
            raise ImmutableRecordConflict(str(exc)) from exc


class _NormalizedParserV2PersistenceAdapter:
    def __init__(
        self,
        repository: ParserV2PostgresRepository,
        project_lookup: ProjectLookup,
    ) -> None:
        self._repository = repository
        self._project_lookup = project_lookup

    def record_store(self) -> ParserV2RecordStore:
        return _NormalizedParserV2RecordStore(
            self._repository,
            self._require_project_tenant,
        )

    def append_profile(
        self,
        project_id: str,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        tenant_id = _record_scope(record, project_id)
        self._require_project_tenant(project_id, tenant_id)
        try:
            return self._repository.append_profile(tenant_id, project_id, record)
        except ParserV2ImmutableConflict as exc:
            raise ProjectParsingProfileConflict(str(exc)) from exc

    def list_profiles(
        self,
        project_id: str,
        *,
        record_type: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        if record_type not in {None, PROJECT_PARSING_PROFILE_RECORD_TYPE}:
            raise ParserV2PersistenceConfigurationError(
                "normalized profile persistence received an unsupported record type"
            )
        tenant_id = self._project_tenant(project_id)
        with self._repository.transaction(tenant_id, project_id) as session:
            return session.list_profiles(limit=limit)

    def _project_tenant(self, project_id: str) -> str:
        project = self._project_lookup(str(project_id))
        if not isinstance(project, Mapping):
            raise ParserV2PersistenceConfigurationError(
                "normalized Parser V2 persistence requires an existing project"
            )
        metadata = project.get("_meta")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        candidates = {
            str(value).strip()
            for value in (
                metadata.get("tenantId"),
                metadata.get("tenant_id"),
                project.get("tenantId"),
                project.get("tenant_id"),
            )
            if value is not None and str(value).strip()
        }
        if len(candidates) != 1:
            raise ParserV2PersistenceConfigurationError(
                "normalized Parser V2 persistence requires one unambiguous project tenant"
            )
        return next(iter(candidates))

    def _require_project_tenant(self, project_id: str, tenant_id: str) -> None:
        if self._project_tenant(project_id) != str(tenant_id):
            raise ParserV2PersistenceConfigurationError(
                "normalized Parser V2 tenant does not match the persisted project tenant"
            )


def build_parser_v2_runtime_dependencies(
    *,
    project_lookup: ProjectLookup,
    persistence: str | None = None,
    backend: str | None = None,
    repository_factory: RepositoryFactory | None = None,
    postgres_configured_fn: Callable[[], bool] | None = None,
    readiness_check_fn: Callable[[], None] | None | object = _DEFAULT_READINESS,
) -> dict[str, Any]:
    """Return optional kwargs for ``build_project_parser_v2_router``.

    Keeping the legacy result empty is intentional: existing SQLite/JSON and
    generic PostgreSQL deployments retain their current persistence behaviour.
    """

    selected = (
        persistence
        if persistence is not None
        else os.environ.get(PARSER_V2_PERSISTENCE_ENV, "")
    ).strip().lower()
    if selected in {"", LEGACY_PERSISTENCE}:
        return {}
    if selected != NORMALIZED_POSTGRES_PERSISTENCE:
        raise ParserV2PersistenceConfigurationError(
            f"{PARSER_V2_PERSISTENCE_ENV} must be unset, {LEGACY_PERSISTENCE!r}, "
            f"or {NORMALIZED_POSTGRES_PERSISTENCE!r}"
        )

    active_backend = (backend if backend is not None else storage_backend()).lower()
    if active_backend != "postgres":
        raise ParserV2PersistenceConfigurationError(
            f"{NORMALIZED_POSTGRES_PERSISTENCE!r} requires "
            "REALYTICS_STORAGE_BACKEND=postgres"
        )
    is_postgres_configured = postgres_configured_fn or postgres.postgres_configured
    if not is_postgres_configured():
        raise ParserV2PersistenceConfigurationError(
            f"{NORMALIZED_POSTGRES_PERSISTENCE!r} requires a configured PostgreSQL DSN"
        )

    # Production startup verifies the immutable migration ledger without
    # applying DDL. Tests that inject a fake repository may omit or explicitly
    # disable the check; a real repository may not disable it.
    if readiness_check_fn is _DEFAULT_READINESS:
        readiness_check = (
            None
            if repository_factory is not None
            else postgres.assert_parser_v2_runtime_ready
        )
    elif readiness_check_fn is None:
        if repository_factory is None:
            raise ParserV2PersistenceConfigurationError(
                "normalized Parser V2 production persistence cannot disable "
                "the PostgreSQL migration readiness check"
            )
        readiness_check = None
    elif callable(readiness_check_fn):
        readiness_check = readiness_check_fn
    else:
        raise TypeError("readiness_check_fn must be callable or None")
    if readiness_check is not None:
        readiness_check()

    repository = (
        repository_factory() if repository_factory is not None else ParserV2PostgresRepository()
    )
    adapter = _NormalizedParserV2PersistenceAdapter(repository, project_lookup)
    return {
        "record_store_factory": adapter.record_store,
        "profile_upsert_fn": adapter.append_profile,
        "profile_list_fn": adapter.list_profiles,
    }


def _record_scope(record: Mapping[str, Any], project_id: str) -> str:
    record_project = str(record.get("projectId") or "").strip()
    tenant_id = str(record.get("tenantId") or "").strip()
    if record_project != str(project_id) or not tenant_id:
        raise ParserV2PersistenceConfigurationError(
            "normalized Parser V2 records require matching projectId and tenantId"
        )
    return tenant_id


__all__ = [
    "LEGACY_PERSISTENCE",
    "NORMALIZED_POSTGRES_PERSISTENCE",
    "PARSER_V2_PERSISTENCE_ENV",
    "ParserV2PersistenceConfigurationError",
    "build_parser_v2_runtime_dependencies",
]
