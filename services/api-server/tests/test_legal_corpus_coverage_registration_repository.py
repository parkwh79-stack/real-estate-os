from __future__ import annotations

import hashlib

import pytest

from db.repositories.legal_corpus_coverage_registration_repository import (
    PostgresLegalCorpusCoverageRegistrationRepository,
)
from db.postgres_legal_sync_privileges import (
    LEGAL_SYNC_TABLE_PRIVILEGES,
)
from db.postgres_runtime_privileges import (
    LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES,
)
from legal_intelligence.corpus_coverage import (
    OfficialCorpusCoverageIdentity,
)
from legal_intelligence.law_go_kr_contracts import (
    LAW_GO_KR_BOUNDARY_CONTRACTS,
)
from use_cases.legal_corpus_coverage_registration import (
    LegalCorpusCoverageRegistrationError,
    build_coverage_contract_set_plan,
)


def plan():
    identities = tuple(
        OfficialCorpusCoverageIdentity(
            provider_key="law-go-kr",
            provider_revision=2,
            registry_version="official-corpus-registry-v2",
            resource_kind=kind.value,
            target=contract.list_target,
            query_sha256=hashlib.sha256(
                f"reviewed-full-query:{kind.value}".encode("utf-8")
            ).hexdigest(),
            source_adapter_version="law-go-kr-approved-adapter-v2",
            boundary_contract_version=(
                f"{contract.list_contract_version}."
                f"{contract.detail_contract_version}."
                f"{contract.history_contract_version}"
            ),
            coverage_contract_version="law-go-kr-full-corpus-v1",
        )
        for kind, contract in sorted(
            LAW_GO_KR_BOUNDARY_CONTRACTS.items(),
            key=lambda item: item[0].value,
        )
    )
    return build_coverage_contract_set_plan(
        identities,
        registered_by="deployment:test",
    )


class FakeCursor:
    def __init__(
        self,
        registration_plan,
        *,
        schema="realytics",
        ownership=("migration_owner", 3, 0),
        provider=None,
        current_leaves=(),
        exact_registry=None,
        contract_rows=(),
    ):
        self.plan = registration_plan
        self.schema = schema
        self.ownership = ownership
        self.provider = provider
        self.current_leaves = list(current_leaves)
        self.exact_registry = exact_registry
        self.contract_rows = list(contract_rows)
        self.statement = ""
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        self.statement = statement
        self.calls.append((statement, params))

    def fetchone(self):
        if "SELECT current_schema()" in self.statement:
            return (self.schema,)
        if "pg_get_userbyid" in self.statement:
            return self.ownership
        if "FROM legal_source_providers" in self.statement:
            return self.provider
        if (
            "FROM legal_corpus_coverage_registries"
            in self.statement
            and "WHERE coverage_registry_id = %s" in self.statement
        ):
            return self.exact_registry
        return None

    def fetchall(self):
        if (
            "FROM legal_corpus_required_target_contracts"
            in self.statement
        ):
            return sorted(self.plan.required_targets.items())
        if "AS successor" in self.statement:
            return self.current_leaves
        if (
            "FROM legal_corpus_coverage_contracts"
            in self.statement
        ):
            return self.contract_rows
        return []


class FakeConnection:
    def __init__(self, cursor):
        self.value = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self):
        return self.value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


def repository(connection):
    return PostgresLegalCorpusCoverageRegistrationRepository(
        connection_factory=lambda: connection,
        schema_name="realytics",
    )


def test_apply_registers_header_and_eight_members_in_one_transaction():
    registration_plan = plan()
    previous = "legal-corpus-coverage-registry:" + ("f" * 64)
    cursor = FakeCursor(
        registration_plan,
        current_leaves=((previous,),),
    )
    connection = FakeConnection(cursor)

    result = repository(connection).register(registration_plan)

    registry_inserts = [
        call
        for call in cursor.calls
        if "INSERT INTO legal_corpus_coverage_registries" in call[0]
    ]
    contract_inserts = [
        call
        for call in cursor.calls
        if "INSERT INTO legal_corpus_coverage_contracts" in call[0]
    ]
    assert result.action == "registered"
    assert result.supersedes_coverage_registry_id == previous
    assert result.provider_registered is False
    assert len(registry_inserts) == 1
    assert len(contract_inserts) == 8
    assert registry_inserts[0][1][7] == previous
    assert all("statement_timestamp()" in call[0] for call in [
        *registry_inserts,
        *contract_inserts,
    ])
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closes == 1
    assert any(
        "pg_advisory_xact_lock" in statement
        for statement, _ in cursor.calls
    )


def test_inspect_validates_everything_but_never_inserts():
    registration_plan = plan()
    cursor = FakeCursor(registration_plan)
    connection = FakeConnection(cursor)

    result = repository(connection).inspect(registration_plan)

    assert result.action == "would_register"
    assert not any(
        "INSERT INTO" in statement for statement, _ in cursor.calls
    )
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closes == 1


def test_exact_current_registry_is_idempotently_verified():
    registration_plan = plan()
    header = (
        registration_plan.provider_key,
        registration_plan.provider_revision,
        registration_plan.registry_version,
        registration_plan.coverage_contract_version,
        8,
        registration_plan.expected_contracts_sha256,
        None,
    )
    members = [
        (
            identity.coverage_contract_id,
            identity.resource_kind,
            identity.target,
            identity.query_sha256,
            identity.source_adapter_version,
            identity.boundary_contract_version,
            identity.coverage_identity_sha256,
        )
        for identity in registration_plan.identities
    ]
    cursor = FakeCursor(
        registration_plan,
        provider=(
            registration_plan.provider_id,
            registration_plan.source_adapter_version,
            "active",
        ),
        current_leaves=((registration_plan.coverage_registry_id,),),
        exact_registry=header,
        contract_rows=members,
    )
    connection = FakeConnection(cursor)

    result = repository(connection).register(registration_plan)

    assert result.action == "current"
    assert result.provider_registered is True
    assert not any(
        "INSERT INTO" in statement for statement, _ in cursor.calls
    )
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_registration_requires_relation_owner_and_private_current_schema():
    registration_plan = plan()
    wrong_owner = FakeConnection(
        FakeCursor(
            registration_plan,
            ownership=("delegated_role", 3, 1),
        )
    )

    with pytest.raises(
        LegalCorpusCoverageRegistrationError,
        match="migration role",
    ):
        repository(wrong_owner).register(registration_plan)

    assert wrong_owner.commits == 0
    assert wrong_owner.rollbacks == 1
    assert not any(
        "INSERT INTO" in statement
        for statement, _ in wrong_owner.value.calls
    )

    wrong_schema = FakeConnection(
        FakeCursor(registration_plan, schema="public")
    )
    with pytest.raises(Exception, match="expected 'realytics'"):
        repository(wrong_schema).register(registration_plan)
    assert wrong_schema.rollbacks == 1


def test_existing_provider_must_match_adapter_identity_and_be_active():
    registration_plan = plan()
    connection = FakeConnection(
        FakeCursor(
            registration_plan,
            provider=(
                registration_plan.provider_id,
                "different-adapter-v1",
                "active",
            ),
        )
    )

    with pytest.raises(
        LegalCorpusCoverageRegistrationError,
        match="provider identity",
    ):
        repository(connection).inspect(registration_plan)

    assert connection.rollbacks == 1
    assert connection.commits == 0


def test_runtime_and_sync_roles_can_only_read_registration_tables():
    tables = (
        "legal_corpus_required_target_contracts",
        "legal_corpus_coverage_registries",
        "legal_corpus_coverage_contracts",
    )

    assert {
        table: LEGAL_SYNC_TABLE_PRIVILEGES[table] for table in tables
    } == {table: ("SELECT",) for table in tables}
    assert {
        table: LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES[table]
        for table in tables
    } == {table: ("SELECT",) for table in tables}
