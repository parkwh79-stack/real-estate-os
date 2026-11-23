from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
from typing import Any

import pytest

from db.repositories.postgres_project_repository import (
    PostgresProjectRepository,
    ProjectVersionConflict,
)


class _Result:
    def __init__(
        self,
        *,
        one: Any | None = None,
        many: list[Any] | None = None,
    ) -> None:
        self._one = one
        self._many = many

    def fetchone(self):
        return self._one

    def fetchall(self):
        if self._many is not None:
            return self._many
        return [] if self._one is None else [self._one]


class _FakeProjectConnection:
    def __init__(self, *, json_as_text: bool = False) -> None:
        self.sequence = 0
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.statements: list[tuple[str, tuple[Any, ...] | None]] = []
        self.json_as_text = json_as_text
        self.fail_next_cas = False

    def execute(
        self,
        sql: str,
        params: tuple[Any, ...] | None = None,
    ) -> _Result:
        compact = " ".join(sql.split())
        lowered = compact.casefold()
        self.statements.append((compact, params))

        if "nextval(" in lowered:
            self.sequence += 1
            return _Result(one=(str(self.sequence),))

        if lowered.startswith("insert into realytics.projects"):
            assert params is not None
            project_id, tenant_id, created_by, payload_json = params
            payload = json.loads(payload_json)
            row = {
                "project_id": project_id,
                "tenant_id": tenant_id,
                "created_by": created_by,
                "state_version": 1,
                "payload_json": payload,
            }
            self.rows[(tenant_id, project_id)] = row
            return _Result(one=self._return_row(row))

        if lowered.startswith("update realytics.projects"):
            assert params is not None
            payload_json, tenant_id, project_id, expected_version = params
            stored = self.rows.get((tenant_id, project_id))
            if self.fail_next_cas and stored is not None:
                self.fail_next_cas = False
                stored["state_version"] += 1
                stored["payload_json"]["_meta"]["stateVersion"] = stored[
                    "state_version"
                ]
                return _Result(one=None)
            if (
                stored is None
                or stored["state_version"] != expected_version
                or self._is_deleted(stored)
            ):
                return _Result(one=None)
            stored["state_version"] += 1
            stored["payload_json"] = json.loads(payload_json)
            return _Result(one=self._return_row(stored))

        if (
            lowered.startswith("select state_version")
            and "from realytics.projects" in lowered
        ):
            assert params is not None
            tenant_id, project_id = params
            stored = self.rows.get((tenant_id, project_id))
            if stored is None:
                return _Result(one=None)
            return _Result(one=(stored["state_version"],))

        if (
            lowered.startswith("select project_id")
            and "from realytics.projects" in lowered
        ):
            assert params is not None
            if "project_id = %s" in lowered:
                tenant_id, project_id = params
                stored = self.rows.get((tenant_id, project_id))
                if stored is None or self._is_deleted(stored):
                    return _Result(one=None)
                return _Result(one=self._return_row(stored))

            (tenant_id,) = params
            rows = [
                self._return_row(row)
                for (row_tenant_id, _project_id), row in sorted(
                    self.rows.items()
                )
                if row_tenant_id == tenant_id and not self._is_deleted(row)
            ]
            return _Result(many=rows)

        raise AssertionError(f"Unexpected SQL: {compact}")

    def _return_row(self, stored: dict[str, Any]):
        payload: Any = deepcopy(stored["payload_json"])
        if self.json_as_text:
            payload = json.dumps(payload)
        return (
            stored["project_id"],
            stored["tenant_id"],
            stored["state_version"],
            payload,
        )

    @staticmethod
    def _is_deleted(stored: dict[str, Any]) -> bool:
        payload = stored["payload_json"]
        return bool((payload.get("_meta") or {}).get("deletedAt")) or (
            (payload.get("project") or {}).get("status") == "deleted"
        )


class _TransactionRecorder:
    def __init__(self, connection: _FakeProjectConnection) -> None:
        self.connection = connection
        self.tenant_ids: list[str] = []

    @contextmanager
    def __call__(self, tenant_id: str):
        self.tenant_ids.append(tenant_id)
        yield self.connection


def _repository(
    tenant_id: str = "tenant-a",
    *,
    connection: _FakeProjectConnection | None = None,
) -> tuple[
    PostgresProjectRepository,
    _FakeProjectConnection,
    _TransactionRecorder,
]:
    selected_connection = connection or _FakeProjectConnection()
    transactions = _TransactionRecorder(selected_connection)
    return (
        PostgresProjectRepository(
            tenant_id=tenant_id,
            transaction_factory=transactions,
        ),
        selected_connection,
        transactions,
    )


def test_create_uses_atomic_sequence_and_overwrites_payload_authority() -> None:
    repository, connection, transactions = _repository()

    created = repository.create(
        {
            "id": "attacker-id",
            "tenantId": "tenant-evil",
            "tenant_id": "tenant-evil",
            "stateVersion": 999,
            "project": {
                "id": "attacker-id",
                "name": "DB Authority",
                "tenantId": "tenant-evil",
                "stateVersion": 999,
            },
            "_meta": {
                "tenantId": "tenant-evil",
                "stateVersion": 999,
                "created_at": "forged",
                "deletedAt": "forged",
            },
        },
        created_by="verified-user",
    )

    assert created["id"] == "1"
    assert created["project"]["id"] == "1"
    assert created["project"]["type"] == "unknown"
    assert created["_meta"]["tenantId"] == "tenant-a"
    assert created["_meta"]["stateVersion"] == 1
    assert created["_meta"]["created_at"] != "forged"
    assert "deletedAt" not in created["_meta"]
    assert "tenantId" not in created["project"]
    assert "stateVersion" not in created["project"]
    assert "tenantId" not in {
        key: value
        for key, value in created.items()
        if key != "_meta"
    }
    assert transactions.tenant_ids == ["tenant-a"]
    assert connection.rows[("tenant-a", "1")]["created_by"] == "verified-user"
    sql = " ".join(statement for statement, _params in connection.statements)
    assert (
        "'realytics.project_id_sequence'::regclass"
        in sql
    )
    assert "INSERT INTO realytics.projects" in sql


def test_list_and_get_repeat_tenant_scope_and_exclude_soft_deleted_rows() -> None:
    connection = _FakeProjectConnection()
    tenant_a, _, tenant_a_transactions = _repository(
        "tenant-a",
        connection=connection,
    )
    tenant_b, _, tenant_b_transactions = _repository(
        "tenant-b",
        connection=connection,
    )
    first = tenant_a.create({"project": {"name": "A"}})
    second = tenant_b.create({"project": {"name": "B"}})

    assert [item["id"] for item in tenant_a.list()] == [first["id"]]
    assert tenant_a.get(second["id"]) is None
    assert tenant_b.get(second["id"])["project"]["name"] == "B"
    tenant_a.delete(first["id"])
    assert tenant_a.list() == []
    assert tenant_a.get(first["id"]) is None

    scoped_sql = [
        (sql, params)
        for sql, params in connection.statements
        if "FROM realytics.projects" in sql
    ]
    assert scoped_sql
    assert all("tenant_id = %s" in sql for sql, _params in scoped_sql)
    assert any(
        "payload_json #>> '{_meta,deletedAt}' IS NULL" in sql
        for sql, _params in scoped_sql
    )
    assert tenant_a_transactions.tenant_ids == [
        "tenant-a",
        "tenant-a",
        "tenant-a",
        "tenant-a",
        "tenant-a",
        "tenant-a",
    ]
    assert tenant_b_transactions.tenant_ids == ["tenant-b", "tenant-b"]


@pytest.mark.parametrize("json_as_text", [False, True])
def test_jsonb_driver_dict_and_text_are_both_supported(
    json_as_text: bool,
) -> None:
    repository, _connection, _transactions = _repository(
        connection=_FakeProjectConnection(json_as_text=json_as_text)
    )
    created = repository.create({"project": {"name": "JSONB"}})

    assert repository.get(created["id"]) == created


def test_read_projection_ignores_legacy_payload_tenant_and_version_fields() -> None:
    repository, connection, _transactions = _repository()
    connection.rows[("tenant-a", "17")] = {
        "project_id": "17",
        "tenant_id": "tenant-a",
        "state_version": 7,
        "payload_json": {
            "id": "forged",
            "tenantId": "tenant-evil",
            "stateVersion": 88,
            "project": {
                "id": "forged",
                "name": "Legacy",
                "tenantId": "tenant-evil",
                "stateVersion": 88,
            },
            "_meta": {
                "tenantId": "tenant-evil",
                "stateVersion": 88,
            },
        },
    }

    record = repository.get("17")

    assert record is not None
    assert record["id"] == "17"
    assert record["project"]["id"] == "17"
    assert record["_meta"]["tenantId"] == "tenant-a"
    assert record["_meta"]["stateVersion"] == 7
    assert "tenantId" not in record["project"]
    assert "stateVersion" not in record["project"]
    assert "tenantId" not in {
        key: value
        for key, value in record.items()
        if key != "_meta"
    }
    assert "stateVersion" not in {
        key: value
        for key, value in record.items()
        if key != "_meta"
    }


def test_update_deep_merges_and_cas_bumps_server_state_version() -> None:
    repository, connection, _transactions = _repository()
    created = repository.create(
        {
            "project": {"name": "Merge", "status": "draft"},
            "land": {"address": "Seoul", "area": 100},
        }
    )

    updated = repository.update(
        created["id"],
        {
            "tenantId": "tenant-evil",
            "stateVersion": 41,
            "project": {
                "tenantId": "tenant-evil",
                "status": "review",
            },
            "land": {"area": 120},
            "_meta": {
                "tenantId": "tenant-evil",
                "stateVersion": 41,
                "created_at": "forged",
                "deletedAt": "forged",
            },
        },
        expected_state_version=1,
    )

    assert updated is not None
    assert updated["land"] == {"address": "Seoul", "area": 120}
    assert updated["project"]["name"] == "Merge"
    assert updated["project"]["status"] == "review"
    assert updated["_meta"]["tenantId"] == "tenant-a"
    assert updated["_meta"]["stateVersion"] == 2
    assert updated["_meta"]["created_at"] == created["_meta"]["created_at"]
    assert "deletedAt" not in updated["_meta"]
    update_sql = next(
        sql
        for sql, _params in connection.statements
        if sql.startswith("UPDATE realytics.projects")
    )
    assert "state_version = state_version + 1" in update_sql
    assert "tenant_id = %s" in update_sql
    assert "project_id = %s" in update_sql
    assert "state_version = %s" in update_sql


def test_update_exposes_typed_cas_conflict_with_actual_version() -> None:
    repository, connection, _transactions = _repository()
    created = repository.create({"project": {"name": "Race"}})
    connection.fail_next_cas = True

    with pytest.raises(ProjectVersionConflict) as raised:
        repository.update(
            created["id"],
            {"project": {"status": "review"}},
            expected_state_version=1,
        )

    assert raised.value.project_id == created["id"]
    assert raised.value.expected_state_version == 1
    assert raised.value.actual_state_version == 2
    assert repository.get(created["id"])["_meta"]["stateVersion"] == 2


def test_update_rejects_a_stale_explicit_version_before_writing() -> None:
    repository, _connection, _transactions = _repository()
    created = repository.create({"project": {"name": "Stale"}})
    updated = repository.update(
        created["id"],
        {"project": {"status": "review"}},
    )
    assert updated is not None

    with pytest.raises(ProjectVersionConflict) as raised:
        repository.update(
            created["id"],
            {"project": {"status": "approved"}},
            expected_state_version=1,
        )

    assert raised.value.actual_state_version == 2


def test_delete_is_cas_soft_delete_and_returns_prior_evidence() -> None:
    repository, connection, _transactions = _repository()
    created = repository.create(
        {
            "project": {"name": "Evidence Project"},
            "evidence": {"locator": "document-1:p.7"},
        }
    )

    prior = repository.delete(
        created["id"],
        expected_state_version=1,
    )

    assert prior == created
    assert prior["evidence"]["locator"] == "document-1:p.7"
    stored = connection.rows[("tenant-a", created["id"])]
    assert stored["state_version"] == 2
    assert stored["payload_json"]["project"]["status"] == "deleted"
    assert stored["payload_json"]["_meta"]["deletedAt"]
    assert stored["payload_json"]["evidence"]["locator"] == "document-1:p.7"
    assert repository.get(created["id"]) is None
    assert repository.list() == []
    all_sql = " ".join(sql for sql, _params in connection.statements)
    assert "DELETE FROM" not in all_sql


def test_every_method_uses_tenant_transaction_and_no_schema_or_app_state_sql() -> None:
    repository, connection, transactions = _repository()
    created = repository.create({"project": {"name": "No DDL"}})
    repository.list()
    repository.get(created["id"])
    repository.update(created["id"], {"land": {"address": "Seoul"}})
    repository.delete(created["id"])

    assert transactions.tenant_ids == ["tenant-a"] * 5
    all_sql = " ".join(sql for sql, _params in connection.statements).casefold()
    assert "realytics.projects" in all_sql
    assert "app_state" not in all_sql
    assert "ensure_schema" not in all_sql
    assert "create table" not in all_sql
    assert "alter table" not in all_sql
    assert "drop table" not in all_sql
