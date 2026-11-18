from __future__ import annotations

from contextlib import contextmanager

import pytest

import use_cases.parser_v2_runtime as parser_v2_runtime
from db.repositories.parser_v2_repository import (
    ParserV2AppendBatch,
    ParserV2ImmutableConflict,
)
from use_cases.parser_v2_profiles import ProjectParsingProfileConflict
from use_cases.parser_v2_records import ImmutableRecordConflict, ParserV2RecordStore
from use_cases.parser_v2_runtime import (
    NORMALIZED_POSTGRES_PERSISTENCE,
    PARSER_V2_PERSISTENCE_ENV,
    ParserV2PersistenceConfigurationError,
    build_parser_v2_runtime_dependencies,
)


TENANT = "tenant-a"
PROJECT = "project-a"


class FakeSession:
    def __init__(self, repository: "FakeRepository") -> None:
        self.repository = repository

    def append_record(self, record: dict) -> dict:
        saved = dict(record)
        self.repository.session_records.append(saved)
        return saved

    def list_profiles(self, *, limit: int = 1000) -> list[dict]:
        self.repository.profile_limits.append(limit)
        return [dict(item) for item in self.repository.profiles[:limit]]


class FakeRepository:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.session_records: list[dict] = []
        self.profiles: list[dict] = []
        self.reads: list[tuple[str, str, str, int]] = []
        self.transactions: list[tuple[str, str]] = []
        self.profile_limits: list[int] = []
        self.record_conflict = False
        self.profile_conflict = False
        self.batches: list[tuple[str, str, ParserV2AppendBatch]] = []

    @contextmanager
    def transaction(self, tenant_id: str, project_id: str):
        self.transactions.append((tenant_id, project_id))
        yield FakeSession(self)

    def append_record(
        self,
        tenant_id: str,
        project_id: str,
        record: dict,
    ) -> dict:
        if self.record_conflict:
            raise ParserV2ImmutableConflict("record changed")
        saved = dict(record)
        self.records.append(saved)
        return saved

    def append_batch(
        self,
        tenant_id: str,
        project_id: str,
        batch: ParserV2AppendBatch,
    ) -> dict[str, list[dict]]:
        self.batches.append((tenant_id, project_id, batch))
        saved = [dict(record) for record in batch.records]
        self.session_records.extend(saved)
        return {"profiles": [], "records": saved}

    def append_profile(
        self,
        tenant_id: str,
        project_id: str,
        record: dict,
    ) -> dict:
        if self.profile_conflict:
            raise ParserV2ImmutableConflict("profile changed")
        saved = dict(record)
        self.profiles.append(saved)
        return saved

    def list_records(
        self,
        tenant_id: str,
        project_id: str,
        record_type: str,
        *,
        limit: int = 1000,
    ) -> list[dict]:
        self.reads.append((tenant_id, project_id, record_type, limit))
        return []


def _project_lookup(project_id: str):
    if project_id != PROJECT:
        return None
    return {"_meta": {"tenantId": TENANT, "tenant_id": TENANT}}


def _normalized(fake: FakeRepository) -> dict:
    return build_parser_v2_runtime_dependencies(
        project_lookup=_project_lookup,
        persistence=NORMALIZED_POSTGRES_PERSISTENCE,
        backend="postgres",
        repository_factory=lambda: fake,
        postgres_configured_fn=lambda: True,
    )


def _record(record_id: str = "record-1") -> dict:
    return {
        "recordId": record_id,
        "recordType": "parser_v2_parsing_run",
        "projectId": PROJECT,
        "tenantId": TENANT,
    }


def test_legacy_is_the_default_and_does_not_construct_repository(monkeypatch):
    monkeypatch.delenv(PARSER_V2_PERSISTENCE_ENV, raising=False)
    constructed = False

    def repository_factory():
        nonlocal constructed
        constructed = True
        return FakeRepository()

    assert build_parser_v2_runtime_dependencies(
        project_lookup=_project_lookup,
        backend="sqlite",
        repository_factory=repository_factory,
        postgres_configured_fn=lambda: False,
    ) == {}
    assert constructed is False


@pytest.mark.parametrize("selected", ["unknown", "postgres", "normalized"])
def test_unknown_explicit_persistence_mode_fails_closed(selected):
    with pytest.raises(ParserV2PersistenceConfigurationError, match=PARSER_V2_PERSISTENCE_ENV):
        build_parser_v2_runtime_dependencies(
            project_lookup=_project_lookup,
            persistence=selected,
        )


def test_normalized_mode_requires_postgres_storage_and_dsn():
    with pytest.raises(ParserV2PersistenceConfigurationError, match="STORAGE_BACKEND=postgres"):
        build_parser_v2_runtime_dependencies(
            project_lookup=_project_lookup,
            persistence=NORMALIZED_POSTGRES_PERSISTENCE,
            backend="sqlite",
        )
    with pytest.raises(ParserV2PersistenceConfigurationError, match="configured PostgreSQL DSN"):
        build_parser_v2_runtime_dependencies(
            project_lookup=_project_lookup,
            persistence=NORMALIZED_POSTGRES_PERSISTENCE,
            backend="postgres",
            postgres_configured_fn=lambda: False,
        )


def test_environment_opt_in_builds_all_router_persistence_seams(monkeypatch):
    fake = FakeRepository()
    monkeypatch.setenv(PARSER_V2_PERSISTENCE_ENV, "POSTGRES_NORMALIZED")

    dependencies = build_parser_v2_runtime_dependencies(
        project_lookup=_project_lookup,
        backend="postgres",
        repository_factory=lambda: fake,
        postgres_configured_fn=lambda: True,
    )

    assert set(dependencies) == {
        "record_store_factory",
        "profile_upsert_fn",
        "profile_list_fn",
    }
    assert isinstance(dependencies["record_store_factory"](), ParserV2RecordStore)


def test_injected_readiness_check_runs_before_repository_factory():
    order: list[str] = []

    dependencies = build_parser_v2_runtime_dependencies(
        project_lookup=_project_lookup,
        persistence=NORMALIZED_POSTGRES_PERSISTENCE,
        backend="postgres",
        postgres_configured_fn=lambda: True,
        readiness_check_fn=lambda: order.append("ready"),
        repository_factory=lambda: (order.append("repository") or FakeRepository()),
    )

    assert set(dependencies) == {
        "record_store_factory",
        "profile_upsert_fn",
        "profile_list_fn",
    }
    assert order == ["ready", "repository"]


def test_real_repository_cannot_disable_migration_readiness():
    with pytest.raises(ParserV2PersistenceConfigurationError, match="cannot disable"):
        build_parser_v2_runtime_dependencies(
            project_lookup=_project_lookup,
            persistence=NORMALIZED_POSTGRES_PERSISTENCE,
            backend="postgres",
            postgres_configured_fn=lambda: True,
            readiness_check_fn=None,
        )


def test_default_repository_runs_read_only_runtime_readiness(monkeypatch):
    order: list[str] = []
    fake = FakeRepository()
    monkeypatch.setattr(
        parser_v2_runtime.postgres,
        "assert_parser_v2_runtime_ready",
        lambda: order.append("ready"),
        raising=False,
    )
    monkeypatch.setattr(
        parser_v2_runtime,
        "ParserV2PostgresRepository",
        lambda: (order.append("repository") or fake),
    )

    dependencies = build_parser_v2_runtime_dependencies(
        project_lookup=_project_lookup,
        persistence=NORMALIZED_POSTGRES_PERSISTENCE,
        backend="postgres",
        postgres_configured_fn=lambda: True,
    )

    assert dependencies["record_store_factory"]()._normalized_repository is fake
    assert order == ["ready", "repository"]


def test_record_store_reads_with_method_tenant_and_batches_in_one_transaction():
    fake = FakeRepository()
    store = _normalized(fake)["record_store_factory"]()

    assert store.list_parsing_runs(TENANT, PROJECT) == []
    assert fake.reads == [(TENANT, PROJECT, "parser_v2_parsing_run", 1000)]

    records = [_record("record-1"), _record("record-2")]
    assert store._persist_batch is not None
    assert store._persist_batch(PROJECT, records) == records
    assert [(tenant, project) for tenant, project, _ in fake.batches] == [
        (TENANT, PROJECT)
    ]
    assert fake.transactions == []
    assert fake.session_records == records


def test_record_store_rejects_cross_tenant_scope_before_repository_access():
    fake = FakeRepository()
    store = _normalized(fake)["record_store_factory"]()

    with pytest.raises(ParserV2PersistenceConfigurationError, match="persisted project tenant"):
        store.list_parsing_runs("tenant-b", PROJECT)
    with pytest.raises(ParserV2PersistenceConfigurationError, match="cannot cross tenant"):
        assert store._persist_batch is not None
        store._persist_batch(
            PROJECT,
            [_record(), {**_record("record-2"), "tenantId": "tenant-b"}],
        )

    assert fake.reads == []
    assert fake.transactions == []


def test_profile_callbacks_use_persisted_project_tenant():
    fake = FakeRepository()
    dependencies = _normalized(fake)
    profile = {
        **_record("profile-1"),
        "recordType": "parser_v2_project_profile",
    }

    assert dependencies["profile_upsert_fn"](PROJECT, profile) == profile
    assert dependencies["profile_list_fn"](
        PROJECT,
        record_type="parser_v2_project_profile",
        limit=7,
    ) == [profile]
    assert fake.transactions == [(TENANT, PROJECT)]
    assert fake.profile_limits == [7]

    with pytest.raises(ParserV2PersistenceConfigurationError, match="unsupported record type"):
        dependencies["profile_list_fn"](PROJECT, record_type="not-a-profile")


def test_missing_or_ambiguous_project_tenant_fails_closed():
    fake = FakeRepository()
    missing = build_parser_v2_runtime_dependencies(
        project_lookup=lambda project_id: {},
        persistence=NORMALIZED_POSTGRES_PERSISTENCE,
        backend="postgres",
        repository_factory=lambda: fake,
        postgres_configured_fn=lambda: True,
    )
    ambiguous = build_parser_v2_runtime_dependencies(
        project_lookup=lambda project_id: {
            "tenantId": "tenant-a",
            "_meta": {"tenantId": "tenant-b"},
        },
        persistence=NORMALIZED_POSTGRES_PERSISTENCE,
        backend="postgres",
        repository_factory=lambda: fake,
        postgres_configured_fn=lambda: True,
    )

    for dependencies in (missing, ambiguous):
        with pytest.raises(
            ParserV2PersistenceConfigurationError,
            match="unambiguous project tenant",
        ):
            dependencies["profile_list_fn"](PROJECT)


def test_repository_conflicts_are_translated_to_existing_domain_errors():
    fake = FakeRepository()
    dependencies = _normalized(fake)
    store = dependencies["record_store_factory"]()
    fake.record_conflict = True

    with pytest.raises(ImmutableRecordConflict, match="record changed"):
        store._upsert(PROJECT, _record())

    fake.profile_conflict = True
    with pytest.raises(ProjectParsingProfileConflict, match="profile changed"):
        dependencies["profile_upsert_fn"](
            PROJECT,
            {**_record("profile-1"), "recordType": "parser_v2_project_profile"},
        )
