"""Migration-role repository for immutable official-corpus coverage contracts."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from db.postgres_runtime_privileges import (
    DEFAULT_RUNTIME_SCHEMA,
    RUNTIME_SCHEMA_ENV,
    assert_current_schema,
    validate_database_identifier,
)
from use_cases.legal_corpus_coverage_registration import (
    LegalCorpusCoverageContractSetPlan,
    LegalCorpusCoverageRegistrationError,
    LegalCorpusCoverageRegistrationResult,
)


ConnectionFactory = Callable[[], Any]
_OWNED_RELATIONS = (
    "legal_corpus_required_target_contracts",
    "legal_corpus_coverage_registries",
    "legal_corpus_coverage_contracts",
)

_OWNERSHIP_SQL = """
SELECT current_user,
       COUNT(*)::INTEGER,
       COUNT(*) FILTER (
           WHERE pg_catalog.pg_get_userbyid(relation.relowner)
                 <> current_user
       )::INTEGER
FROM pg_catalog.pg_class AS relation
JOIN pg_catalog.pg_namespace AS namespace
  ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = %s
  AND relation.relname = ANY(%s)
  AND relation.relkind IN ('r', 'p')
"""
_REQUIRED_TARGETS_SQL = """
SELECT resource_kind, provider_target
FROM legal_corpus_required_target_contracts
WHERE provider_key = %s
  AND coverage_contract_version = %s
  AND required
ORDER BY resource_kind
"""
_PROVIDER_SQL = """
SELECT provider_id, adapter_version, provider_status
FROM legal_source_providers
WHERE provider_key = %s AND provider_revision = %s
"""
_CURRENT_LEAVES_SQL = """
SELECT registry.coverage_registry_id
FROM legal_corpus_coverage_registries AS registry
WHERE registry.provider_key = %s
  AND registry.provider_revision = %s
  AND registry.coverage_contract_version = %s
  AND NOT EXISTS (
      SELECT 1
      FROM legal_corpus_coverage_registries AS successor
      WHERE successor.supersedes_coverage_registry_id =
            registry.coverage_registry_id
  )
ORDER BY registry.registered_at DESC, registry.coverage_registry_id DESC
"""
_EXACT_REGISTRY_SQL = """
SELECT provider_key, provider_revision, registry_version,
       coverage_contract_version, expected_contract_count,
       expected_contracts_sha256, supersedes_coverage_registry_id
FROM legal_corpus_coverage_registries
WHERE coverage_registry_id = %s
"""
_REGISTRY_CONTRACTS_SQL = """
SELECT coverage_contract_id, resource_kind, provider_target, query_sha256,
       source_adapter_version, boundary_contract_version,
       coverage_identity_sha256
FROM legal_corpus_coverage_contracts
WHERE coverage_registry_id = %s
ORDER BY coverage_contract_id
"""
_INSERT_REGISTRY_SQL = """
INSERT INTO legal_corpus_coverage_registries(
    coverage_registry_id, provider_key, provider_revision, registry_version,
    coverage_contract_version, expected_contract_count,
    expected_contracts_sha256, supersedes_coverage_registry_id,
    registered_at, registered_by, metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s,
    statement_timestamp(), %s, %s::jsonb
)
"""
_INSERT_CONTRACT_SQL = """
INSERT INTO legal_corpus_coverage_contracts(
    coverage_contract_id, coverage_registry_id, resource_kind,
    provider_target, query_sha256, source_adapter_version,
    boundary_contract_version, coverage_identity_sha256,
    registered_at, registered_by, metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s,
    statement_timestamp(), %s, %s::jsonb
)
"""


class PostgresLegalCorpusCoverageRegistrationRepository:
    """Register one eight-target set through a table-owning deployment role.

    A connection factory is mandatory by design.  API and sync composition
    roots cannot silently fall back to this repository or a general runtime
    DSN; the deployment CLI is the only production composition supplied.
    """

    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory,
        schema_name: str | None = None,
    ) -> None:
        if connection_factory is None:
            raise TypeError("connection_factory is required")
        self._connect = connection_factory
        self._schema = validate_database_identifier(
            schema_name
            or os.environ.get(RUNTIME_SCHEMA_ENV)
            or DEFAULT_RUNTIME_SCHEMA,
            label="database schema",
        )

    def inspect(
        self,
        plan: LegalCorpusCoverageContractSetPlan,
    ) -> LegalCorpusCoverageRegistrationResult:
        return self._execute(plan, apply=False)

    def register(
        self,
        plan: LegalCorpusCoverageContractSetPlan,
    ) -> LegalCorpusCoverageRegistrationResult:
        return self._execute(plan, apply=True)

    def _execute(
        self,
        plan: LegalCorpusCoverageContractSetPlan,
        *,
        apply: bool,
    ) -> LegalCorpusCoverageRegistrationResult:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                assert_current_schema(cursor, schema_name=self._schema)
                cursor.execute(
                    f'SET LOCAL search_path TO "{self._schema}", '
                    '"extensions", pg_temp'
                )
                self._assert_migration_owner(cursor)
                cursor.execute(
                    "SELECT pg_catalog.pg_advisory_xact_lock("
                    "pg_catalog.hashtextextended(%s, 0))",
                    (
                        "legal-corpus-coverage-registration:"
                        f"{plan.provider_key}:"
                        f"{plan.provider_revision}:"
                        f"{plan.coverage_contract_version}",
                    ),
                )
                provider_registered = self._validate_database_contract(
                    cursor,
                    plan,
                )
                current_registry_id = self._current_registry_id(
                    cursor,
                    plan,
                )
                exact_registry = self._exact_registry(cursor, plan)

                if exact_registry is not None:
                    if current_registry_id != plan.coverage_registry_id:
                        raise LegalCorpusCoverageRegistrationError(
                            "manifest registry exists but is no longer the "
                            "current coverage leaf"
                        )
                    self._validate_exact_registry(
                        cursor,
                        plan,
                        exact_registry,
                    )
                    connection.rollback()
                    return self._result(
                        plan,
                        action="current",
                        provider_registered=provider_registered,
                        supersedes_coverage_registry_id=(
                            exact_registry[6]
                        ),
                    )

                if not apply:
                    connection.rollback()
                    return self._result(
                        plan,
                        action="would_register",
                        provider_registered=provider_registered,
                        supersedes_coverage_registry_id=(
                            current_registry_id
                        ),
                    )

                self._insert_registry(
                    cursor,
                    plan,
                    supersedes_coverage_registry_id=current_registry_id,
                )
                self._insert_contracts(cursor, plan)
            connection.commit()
            return self._result(
                plan,
                action="registered",
                provider_registered=provider_registered,
                supersedes_coverage_registry_id=current_registry_id,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _assert_migration_owner(self, cursor: Any) -> None:
        cursor.execute(
            _OWNERSHIP_SQL,
            (self._schema, list(_OWNED_RELATIONS)),
        )
        row = cursor.fetchone()
        if (
            row is None
            or int(row[1]) != len(_OWNED_RELATIONS)
            or int(row[2]) != 0
        ):
            raise LegalCorpusCoverageRegistrationError(
                "coverage registration requires the migration role that "
                "owns all coverage contract relations"
            )

    @staticmethod
    def _validate_database_contract(
        cursor: Any,
        plan: LegalCorpusCoverageContractSetPlan,
    ) -> bool:
        cursor.execute(
            _REQUIRED_TARGETS_SQL,
            (
                plan.provider_key,
                plan.coverage_contract_version,
            ),
        )
        database_targets = {
            str(row[0]): str(row[1]) for row in cursor.fetchall()
        }
        if database_targets != dict(plan.required_targets):
            raise LegalCorpusCoverageRegistrationError(
                "manifest target set does not match the migrated required "
                "target contract"
            )

        cursor.execute(
            _PROVIDER_SQL,
            (plan.provider_key, plan.provider_revision),
        )
        provider = cursor.fetchone()
        if provider is None:
            return False
        if (
            str(provider[0]) != plan.provider_id
            or str(provider[1]) != plan.source_adapter_version
            or str(provider[2]) != "active"
        ):
            raise LegalCorpusCoverageRegistrationError(
                "manifest provider identity, adapter, or active status does "
                "not match the registered source provider"
            )
        return True

    @staticmethod
    def _current_registry_id(
        cursor: Any,
        plan: LegalCorpusCoverageContractSetPlan,
    ) -> str | None:
        cursor.execute(
            _CURRENT_LEAVES_SQL,
            (
                plan.provider_key,
                plan.provider_revision,
                plan.coverage_contract_version,
            ),
        )
        rows = cursor.fetchall()
        if len(rows) > 1:
            raise LegalCorpusCoverageRegistrationError(
                "multiple current coverage registry leaves were detected"
            )
        return str(rows[0][0]) if rows else None

    @staticmethod
    def _exact_registry(
        cursor: Any,
        plan: LegalCorpusCoverageContractSetPlan,
    ) -> tuple[Any, ...] | None:
        cursor.execute(
            _EXACT_REGISTRY_SQL,
            (plan.coverage_registry_id,),
        )
        row = cursor.fetchone()
        return tuple(row) if row is not None else None

    @staticmethod
    def _validate_exact_registry(
        cursor: Any,
        plan: LegalCorpusCoverageContractSetPlan,
        registry: tuple[Any, ...],
    ) -> None:
        expected_registry = (
            plan.provider_key,
            plan.provider_revision,
            plan.registry_version,
            plan.coverage_contract_version,
            len(plan.identities),
            plan.expected_contracts_sha256,
        )
        if tuple(registry[:6]) != expected_registry:
            raise LegalCorpusCoverageRegistrationError(
                "existing coverage registry does not match the manifest"
            )

        cursor.execute(
            _REGISTRY_CONTRACTS_SQL,
            (plan.coverage_registry_id,),
        )
        actual_contracts = {
            tuple(row) for row in cursor.fetchall()
        }
        expected_contracts = {
            (
                identity.coverage_contract_id,
                identity.resource_kind,
                identity.target,
                identity.query_sha256,
                identity.source_adapter_version,
                identity.boundary_contract_version,
                identity.coverage_identity_sha256,
            )
            for identity in plan.identities
        }
        if actual_contracts != expected_contracts:
            raise LegalCorpusCoverageRegistrationError(
                "existing coverage registry members do not match the manifest"
            )

    @staticmethod
    def _insert_registry(
        cursor: Any,
        plan: LegalCorpusCoverageContractSetPlan,
        *,
        supersedes_coverage_registry_id: str | None,
    ) -> None:
        cursor.execute(
            _INSERT_REGISTRY_SQL,
            (
                plan.coverage_registry_id,
                plan.provider_key,
                plan.provider_revision,
                plan.registry_version,
                plan.coverage_contract_version,
                len(plan.identities),
                plan.expected_contracts_sha256,
                supersedes_coverage_registry_id,
                plan.registered_by,
                _canonical_json(plan.metadata),
            ),
        )

    @staticmethod
    def _insert_contracts(
        cursor: Any,
        plan: LegalCorpusCoverageContractSetPlan,
    ) -> None:
        metadata = _canonical_json(plan.metadata)
        for identity in plan.identities:
            cursor.execute(
                _INSERT_CONTRACT_SQL,
                (
                    identity.coverage_contract_id,
                    plan.coverage_registry_id,
                    identity.resource_kind,
                    identity.target,
                    identity.query_sha256,
                    identity.source_adapter_version,
                    identity.boundary_contract_version,
                    identity.coverage_identity_sha256,
                    plan.registered_by,
                    metadata,
                ),
            )

    @staticmethod
    def _result(
        plan: LegalCorpusCoverageContractSetPlan,
        *,
        action: str,
        provider_registered: bool,
        supersedes_coverage_registry_id: str | None,
    ) -> LegalCorpusCoverageRegistrationResult:
        return LegalCorpusCoverageRegistrationResult(
            action=action,
            coverage_registry_id=plan.coverage_registry_id,
            expected_contract_count=len(plan.identities),
            expected_contracts_sha256=(
                plan.expected_contracts_sha256
            ),
            provider_id=plan.provider_id,
            provider_registered=provider_registered,
            supersedes_coverage_registry_id=(
                supersedes_coverage_registry_id
            ),
        )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = [
    "PostgresLegalCorpusCoverageRegistrationRepository",
]
