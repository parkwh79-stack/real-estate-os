from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

import pytest

from agent_platform.contracts.operational import RuntimeOutboxEvent
from db.repositories import parser_v2_repository as repository_module
from db.repositories.parser_v2_repository import (
    APPROVED_INPUT_SNAPSHOT_RECORD_TYPE,
    CANONICAL_SELECTION_RECORD_TYPE,
    PARSING_RUN_RECORD_TYPE,
    PROJECT_PROFILE_RECORD_TYPE,
    ParserV2AppendBatch,
    ParserV2ImmutableConflict,
    ParserV2PostgresRepository,
    ParserV2TenantScopeError,
    ParserV2UnsupportedRecord,
)


GENERATED_TIMESTAMPS = {
    "createdAt", "updatedAt", "requestedAt", "ingestedAt", "detectedAt",
    "decidedAt", "selectedAt", "approvedAt", "resolvedAt", "startedAt",
    "completedAt",
}


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _with_record_hash(record: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(record)
    payload = {
        key: value
        for key, value in result.items()
        if key not in GENERATED_TIMESTAMPS.union({"audit", "immutablePayloadHash"})
    }
    result["immutablePayloadHash"] = _stable_hash(payload)
    return result


def _run(run_id: str = "run-1", *, input_hash: str | None = None) -> dict[str, Any]:
    return _with_record_hash(
        {
            "schemaVersion": "realytics-parser-v2-record-v1",
            "recordType": PARSING_RUN_RECORD_TYPE,
            "recordId": f"parser_v2_parsing_run:{run_id}",
            "eventKind": "created",
            "tenantId": "tenant-1",
            "projectId": "project-1",
            "actorId": "producer-1",
            "parsingRunId": run_id,
            "documentVersionId": "doc-version-1",
            "executionStatus": "pending",
            "approvalStatus": "not_requested",
            "parserVersion": "parser-1",
            "ontologyVersion": "ontology-1",
            "promptVersion": "prompt-1",
            "modelVersion": "model-1",
            "profileId": "profile-1",
            "profileVersion": "1.0.0",
            "planHash": "a" * 64,
            "requestedModules": ["structure"],
            "idempotencyKey": f"idempotency:{run_id}",
            "inputHash": input_hash or "b" * 64,
            "attemptNumber": 1,
            "requestedAt": "2026-07-15T00:00:00+00:00",
            "createdAt": "2026-07-15T00:00:00+00:00",
        }
    )


def _candidate(candidate_id: str, *, run_id: str = "run-1") -> dict[str, Any]:
    return _with_record_hash(
        {
            "schemaVersion": "realytics-parser-v2-record-v1",
            "recordType": "parser_v2_parsed_fact_candidate",
            "recordId": f"parser_v2_parsed_fact_candidate:{candidate_id}",
            "eventKind": "created",
            "tenantId": "tenant-1",
            "projectId": "project-1",
            "actorId": "producer-1",
            "candidateId": candidate_id,
            "parsingRunId": run_id,
            "documentVersionId": "doc-version-1",
            "fieldKey": "project.type",
            "entityId": None,
            "rawValue": "development",
            "normalizedValue": "development",
            "originType": "extracted",
            "presenceStatus": "present",
            "reviewStatus": "pending",
            "approvalStatus": "not_requested",
            "conflictStatus": "none",
            "authority": "business_plan",
            "parserVersion": "parser-1",
            "ontologyVersion": "ontology-1",
            "promptVersion": "prompt-1",
            "modelVersion": "model-1",
            "fieldRegistryVersion": "registry-1",
            "validationStatus": "passed",
            "validationCodes": [],
            "validationFindings": [],
            "inputFactIds": [],
            "createdAt": "2026-07-15T00:00:30+00:00",
        }
    )


def _seal(candidate: dict[str, Any], *, candidate_set_hash: str | None = None) -> dict[str, Any]:
    run_id = candidate["parsingRunId"]
    calculated = _stable_hash(
        {
            "parsingRunId": run_id,
            "candidates": [
                {
                    "candidateId": candidate["candidateId"],
                    "immutablePayloadHash": candidate["immutablePayloadHash"],
                }
            ],
            "conflicts": [],
        }
    )
    return _with_record_hash(
        {
            "schemaVersion": "realytics-parser-v2-record-v1",
            "recordType": "parser_v2_candidate_set_seal",
            "recordId": f"parser_v2_candidate_set_seal:{run_id}",
            "eventKind": "created",
            "tenantId": "tenant-1",
            "projectId": "project-1",
            "actorId": "producer-1",
            "sealId": f"candidate-set-seal:{run_id}",
            "parsingRunId": run_id,
            "candidateIds": [candidate["candidateId"]],
            "candidateCount": 1,
            "conflictIds": [],
            "conflictCount": 0,
            "candidateSetHash": candidate_set_hash or calculated,
            "sealedBy": "producer-1",
            "sealedAt": "2026-07-15T00:01:00+00:00",
            "createdAt": "2026-07-15T00:01:00+00:00",
        }
    )


def _profile() -> dict[str, Any]:
    profile = {"profileId": "profile-1", "version": "1.0.0", "selectedModules": []}
    plan = {"profileId": "profile-1", "profileVersion": "1.0.0", "modules": []}
    return {
        "schemaVersion": "realytics-project-parsing-profile-v1",
        "recordType": PROJECT_PROFILE_RECORD_TYPE,
        "recordId": "parser_v2_project_profile:profile-1:1.0.0",
        "tenantId": "tenant-1",
        "projectId": "project-1",
        "actorId": "configurer-1",
        "configurationStatus": "active",
        "profileHash": _stable_hash(profile),
        "executionPlanHash": _stable_hash(plan),
        "profile": profile,
        "executionPlan": plan,
        "createdAt": "2026-07-15T00:00:00+00:00",
    }


def _selection(
    selection_id: str,
    *,
    version: int = 1,
    supersedes: str | None = None,
) -> dict[str, Any]:
    return _with_record_hash(
        {
            "schemaVersion": "realytics-parser-v2-record-v1",
            "recordType": CANONICAL_SELECTION_RECORD_TYPE,
            "recordId": f"parser_v2_canonical_selection:{selection_id}",
            "eventKind": "created",
            "tenantId": "tenant-1",
            "projectId": "project-1",
            "actorId": "reviewer-1",
            "selectionId": selection_id,
            "candidateId": f"candidate:{selection_id}",
            "reviewDecisionId": f"review:{selection_id}",
            "documentVersionId": "doc-old",
            "fieldKey": "project.type",
            "entityId": None,
            "approvalStatus": "approved",
            "conflictStatus": "none",
            "normalizedValue": "development",
            "fieldRegistryVersion": "registry-1",
            "selectionVersion": version,
            "selectedBy": "reviewer-1",
            "selectedAt": "2026-07-15T00:01:00+00:00",
            "createdAt": "2026-07-15T00:01:00+00:00",
            "supersedesSelectionId": supersedes,
        }
    )


def _selection_invalidation(old_id: str, new_id: str) -> dict[str, Any]:
    return _with_record_hash(
        {
            "schemaVersion": "realytics-parser-v2-record-v1",
            "recordType": CANONICAL_SELECTION_RECORD_TYPE,
            "recordId": f"parser_v2_canonical_selection:invalidate:{old_id}:{new_id}",
            "eventKind": "invalidated",
            "tenantId": "tenant-1",
            "projectId": "project-1",
            "actorId": "reviewer-1",
            "invalidationId": f"invalidate:{old_id}:{new_id}",
            "selectionId": old_id,
            "replacementSelectionId": new_id,
            "reason": "superseded",
            "createdAt": "2026-07-15T00:02:00+00:00",
        }
    )


def _document_selection_invalidation(
    selection_id: str, *, old_document_id: str, new_document_id: str
) -> dict[str, Any]:
    return _with_record_hash(
        {
            "schemaVersion": "realytics-parser-v2-record-v1",
            "recordType": CANONICAL_SELECTION_RECORD_TYPE,
            "recordId": f"parser_v2_canonical_selection:doc-invalidate:{selection_id}",
            "eventKind": "invalidated",
            "tenantId": "tenant-1",
            "projectId": "project-1",
            "actorId": "system-1",
            "invalidationId": f"doc-invalidate:{selection_id}",
            "selectionId": selection_id,
            "supersededDocumentVersionId": old_document_id,
            "replacementDocumentVersionId": new_document_id,
            "reason": "source document superseded",
            "createdAt": "2026-07-15T00:02:00+00:00",
        }
    )


def _snapshot(snapshot_id: str = "snapshot-1") -> dict[str, Any]:
    selection = _selection("selection-1")
    return _with_record_hash(
        {
            "schemaVersion": "realytics-parser-v2-record-v1",
            "recordType": APPROVED_INPUT_SNAPSHOT_RECORD_TYPE,
            "recordId": f"parser_v2_approved_input_snapshot:{snapshot_id}",
            "eventKind": "created",
            "tenantId": "tenant-1",
            "projectId": "project-1",
            "actorId": "approver-1",
            "snapshotId": snapshot_id,
            "purpose": "cash_flow",
            "snapshotVersion": 1,
            "approvalStatus": "approved",
            "inputSchemaVersion": "inputs-1",
            "ontologyVersion": "ontology-1",
            "fieldRegistryVersion": "registry-1",
            "selectionIds": ["selection-1"],
            "documentVersionIds": ["doc-old"],
            "selections": [
                {
                    "selectionId": "selection-1",
                    "fieldKey": selection["fieldKey"],
                    "entityId": selection["entityId"],
                    "candidateId": selection["candidateId"],
                    "reviewDecisionId": selection["reviewDecisionId"],
                    "approvalStatus": selection["approvalStatus"],
                    "normalizedValue": selection["normalizedValue"],
                    "selectionVersion": selection["selectionVersion"],
                }
            ],
            "sourceParsingRunIds": ["run-1"],
            "approvedBy": "approver-1",
            "approvedAt": "2026-07-15T00:03:00+00:00",
            "createdAt": "2026-07-15T00:03:00+00:00",
        }
    )


class FakeDatabase:
    def __init__(self) -> None:
        self.projects = {("tenant-1", "project-1")}
        self.rows: dict[str, dict[Any, dict[str, Any]]] = {}
        self.connections: list[FakeConnection] = []
        self.fail_on_table: str | None = None
        self.open_conflict_candidates: set[str] = set()
        self.unselected_derived_inputs: set[str] = set()

    def connect(self) -> "FakeConnection":
        connection = FakeConnection(self)
        self.connections.append(connection)
        return connection

    def seed_candidate_review(
        self,
        selection_id: str,
        *,
        validation_status: str = "passed",
        origin_type: str = "extracted",
        review_sequence: int = 1,
        review_action: str = "approve",
    ) -> None:
        candidate_id = f"candidate:{selection_id}"
        review_id = (
            f"review:{selection_id}"
            if review_sequence == 1
            else f"review:{selection_id}:{review_sequence}"
        )
        candidate = {
            "candidateId": candidate_id,
            "fieldKey": "project.type",
            "entityId": None,
            "presenceStatus": "present",
            "validationStatus": validation_status,
            "originType": origin_type,
        }
        review = {
            "reviewDecisionId": review_id,
            "candidateId": candidate_id,
            "decisionSequence": review_sequence,
            "action": review_action,
            "resultingReviewStatus": "approved" if review_action == "approve" else "rejected",
            "resultingApprovalStatus": "approved" if review_action == "approve" else "rejected",
        }
        self.rows.setdefault("parsed_fact_candidates", {})[
            ("tenant-1", "project-1", candidate_id)
        ] = {"payload": candidate, "hashes": ("a" * 64,), "params": ()}
        self.rows.setdefault("parser_review_decisions", {})[
            ("tenant-1", "project-1", review_id)
        ] = {"payload": review, "hashes": ("b" * 64,), "params": ()}

    def seed_document_version(
        self, document_id: str, *, supersedes: str | None
    ) -> None:
        payload = {
            "documentVersionId": document_id,
            "supersedesDocumentVersionId": supersedes,
        }
        self.rows.setdefault("parser_document_versions", {})[
            ("tenant-1", "project-1", document_id)
        ] = {"payload": payload, "hashes": ("c" * 64,), "params": ()}


class FakeConnection:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database
        self.local = copy.deepcopy(database.rows)
        self.statements: list[tuple[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self)

    def commit(self) -> None:
        self.database.rows = copy.deepcopy(self.local)
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.one: Any = None
        self.many: list[Any] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def fetchone(self) -> Any:
        result = self.one
        self.one = None
        return result

    def fetchall(self) -> list[Any]:
        result = self.many
        self.many = []
        return result

    def execute(self, sql: str, params: Any = None) -> None:
        normalized = " ".join(sql.split())
        if params is not None:
            assert sql.count("%s") == len(params), normalized
        self.connection.statements.append((normalized, params))
        self.one = None
        self.many = []

        if normalized.startswith("SELECT set_config"):
            self.one = (params[0],)
            return
        if normalized.startswith("SELECT pg_advisory_xact_lock"):
            self.one = (None,)
            return
        if normalized.startswith("SELECT project_id FROM projects"):
            project_id, tenant_id = params
            if (tenant_id, project_id) in self.connection.database.projects:
                self.one = (project_id,)
            return
        if normalized.startswith("SELECT seal_id"):
            tenant_id, project_id, run_id = params
            self.one = next(
                (
                    (row["payload"]["sealId"],)
                    for key, row in self.connection.local.get(
                        "parser_candidate_set_seals", {}
                    ).items()
                    if key[:2] == (tenant_id, project_id)
                    and row["payload"]["parsingRunId"] == run_id
                ),
                None,
            )
            return
        if normalized.startswith("SELECT candidate_id, candidate_hash"):
            tenant_id, project_id, run_id = params
            candidates = [
                row["payload"]
                for key, row in self.connection.local.get(
                    "parsed_fact_candidates", {}
                ).items()
                if key[:2] == (tenant_id, project_id)
                and row["payload"].get("parsingRunId") == run_id
            ]
            candidates.sort(key=lambda item: item["candidateId"])
            self.many = [
                (item["candidateId"], item["immutablePayloadHash"])
                for item in candidates
            ]
            return
        if normalized.startswith("SELECT conflict_id, conflict_hash"):
            self.many = []
            return
        if normalized.startswith("SELECT field_key, entity_id, presence_status"):
            row = self.connection.local.get("parsed_fact_candidates", {}).get(
                (params[0], params[1], params[2])
            )
            if row:
                payload = row["payload"]
                self.one = (
                    payload["fieldKey"], payload.get("entityId"),
                    payload["presenceStatus"], payload["validationStatus"],
                    payload["originType"],
                )
            return
        if normalized.startswith("SELECT review_decision_id, action"):
            self.one = self._latest_review(params)
            return
        if normalized.startswith("SELECT conflict.conflict_id"):
            if params[2] in self.connection.database.open_conflict_candidates:
                self.one = ("conflict-open",)
            return
        if normalized.startswith("SELECT inputs.input_candidate_id"):
            if params[2] in self.connection.database.unselected_derived_inputs:
                self.one = ("input-candidate-missing-selection",)
            return
        if normalized.startswith("SELECT selection.selection_id"):
            self.one = self._snapshot_selection_state(params)
            return
        if normalized.startswith("SELECT snapshot.snapshot_id"):
            self.many = self._active_snapshots_for_selection(params)
            return
        if normalized.startswith("SELECT payload_json, NOT EXISTS"):
            if "FROM parser_canonical_selections selection" in normalized:
                self.one = self._validity_target(
                    "parser_canonical_selections", params, "selectionId"
                )
            else:
                self.one = self._validity_target(
                    "parser_approved_snapshots", params, "snapshotId"
                )
            return
        if "FROM parser_canonical_selections selection" in normalized:
            self.one = self._latest_selection(params)
            return
        if "FROM parser_approved_snapshots snapshot" in normalized:
            self.one = self._latest_snapshot(params)
            return
        if normalized.startswith("SELECT supersedes_selection_id"):
            self.one = self._replacement("parser_canonical_selections", params[2], "supersedesSelectionId")
            return
        if normalized.startswith("SELECT supersedes_snapshot_id"):
            self.one = self._replacement("parser_approved_snapshots", params[2], "supersedesSnapshotId")
            return
        if normalized.startswith("SELECT supersedes_document_version_id"):
            self.one = self._replacement_document(params[2])
            return
        if normalized.startswith("INSERT INTO"):
            self._insert(normalized, params)
            return
        if normalized.startswith("SELECT profile_hash"):
            key = (params[0], params[1], params[2], params[3])
            row = self.connection.local.get("parser_project_profiles", {}).get(key)
            if row:
                self.one = (row["hashes"][0], row["hashes"][1], row["payload"])
            return
        if normalized.startswith("SELECT event_id") and "runtime_outbox_events" in normalized:
            row = self.connection.local.get("runtime_outbox_events", {}).get(params[0])
            if row:
                payload = row["payload"]
                self.one = (
                    payload["eventId"], payload["tenantId"], payload["projectId"],
                    payload["aggregateType"], payload["aggregateId"], payload["eventType"],
                    payload,
                )
            return
        if normalized.startswith("SELECT") and "payload_json" in normalized:
            if "ORDER BY" in normalized and not "->>" in normalized:
                self._list(normalized, params)
            else:
                self._select_identity(normalized, params)
            return
        raise AssertionError(f"unexpected SQL: {normalized}")

    def _insert(self, sql: str, params: Any) -> None:
        table = re.search(r"INSERT INTO ([a-z_]+)", sql).group(1)
        if self.connection.database.fail_on_table == table:
            raise RuntimeError(f"injected {table} failure")
        payload = None
        if params and isinstance(params[-1], str) and params[-1].startswith("{"):
            payload = json.loads(params[-1])
        key, hashes = self._insert_identity(table, params, payload)
        table_rows = self.connection.local.setdefault(table, {})
        if key in table_rows:
            return
        table_rows[key] = {"payload": payload, "hashes": hashes, "params": params}
        if "RETURNING payload_json" in sql:
            self.one = (payload,)

    @staticmethod
    def _insert_identity(table: str, params: Any, payload: Any) -> tuple[Any, tuple[str, ...]]:
        if table == "parser_project_profiles":
            return (params[0], params[1], params[2], params[3]), (params[5], params[6])
        if table == "runtime_outbox_events":
            return params[6], ()
        if table == "parser_snapshot_selections":
            return (params[0], params[1], params[2], params[3]), ()
        if table == "parser_snapshot_source_runs":
            return (params[0], params[1], params[2], params[3]), ()
        if table == "parser_candidate_inputs":
            return (params[0], params[1], params[2], params[3]), ()
        if table == "parser_validation_findings":
            return (params[1], params[2], params[0]), (params[-2],)
        if table == "parser_validity_events":
            return (params[1], params[2], params[0]), (params[-2],)
        if table in {
            "parser_runs", "parser_candidate_set_seals", "parsed_fact_candidates",
            "parser_canonical_selections", "parser_approved_snapshots",
        }:
            return (params[1], params[2], params[0]), (payload["immutablePayloadHash"],)
        raise AssertionError(f"test fake does not support insert table {table}")

    def _select_identity(self, sql: str, params: Any) -> None:
        table = re.search(r"FROM ([a-z_]+)", sql).group(1)
        key = (params[1], params[2], params[0])
        row = self.connection.local.get(table, {}).get(key)
        if row:
            self.one = (*row["hashes"], row["payload"])

    def _list(self, sql: str, params: Any) -> None:
        table = re.search(r"FROM ([a-z_]+)", sql).group(1)
        tenant_id, project_id, limit = params
        values = [
            row["payload"]
            for key, row in self.connection.local.get(table, {}).items()
            if isinstance(key, tuple) and key[:2] == (tenant_id, project_id)
        ]
        self.many = [(value,) for value in values[:limit]]

    def _latest_selection(self, params: Any) -> Any:
        tenant_id, project_id, field_key, entity_id = params
        values = [
            row["payload"]
            for key, row in self.connection.local.get("parser_canonical_selections", {}).items()
            if key[:2] == (tenant_id, project_id)
            and row["payload"]["fieldKey"] == field_key
            and row["payload"].get("entityId") == entity_id
        ]
        if not values:
            return None
        latest = max(values, key=lambda item: item["selectionVersion"])
        active = not self._invalidated("canonical_selection", latest["selectionId"])
        return latest["selectionId"], latest["selectionVersion"], active

    def _latest_review(self, params: Any) -> Any:
        tenant_id, project_id, candidate_id = params
        values = [
            row["payload"]
            for key, row in self.connection.local.get("parser_review_decisions", {}).items()
            if key[:2] == (tenant_id, project_id)
            and row["payload"]["candidateId"] == candidate_id
        ]
        if not values:
            return None
        latest = max(values, key=lambda item: item["decisionSequence"])
        return (
            latest["reviewDecisionId"], latest["action"],
            latest["resultingReviewStatus"], latest["resultingApprovalStatus"],
        )

    def _snapshot_selection_state(self, params: Any) -> Any:
        tenant_id, project_id, selection_id = params
        row = self.connection.local.get("parser_canonical_selections", {}).get(
            (tenant_id, project_id, selection_id)
        )
        if not row:
            return None
        payload = row["payload"]
        active = not self._invalidated("canonical_selection", selection_id)
        latest = self._latest_selection(
            (tenant_id, project_id, payload["fieldKey"], payload.get("entityId"))
        )
        is_latest = bool(latest and latest[0] == selection_id)
        return (
            selection_id, payload["fieldKey"], payload.get("entityId"),
            payload["candidateId"], payload["reviewDecisionId"],
            payload["selectionVersion"], payload["approvalStatus"], payload,
            active, is_latest,
        )

    def _active_snapshots_for_selection(self, params: Any) -> list[Any]:
        tenant_id, project_id, selection_id = params
        memberships = self.connection.local.get("parser_snapshot_selections", {})
        snapshot_ids = {
            key[2]
            for key in memberships
            if key[:2] == (tenant_id, project_id) and key[3] == selection_id
        }
        rows = []
        for snapshot_id in snapshot_ids:
            if self._invalidated("approved_snapshot", snapshot_id):
                continue
            snapshot = self.connection.local.get("parser_approved_snapshots", {}).get(
                (tenant_id, project_id, snapshot_id)
            )
            if snapshot:
                rows.append((snapshot_id, snapshot["payload"]))
        return rows

    def _latest_snapshot(self, params: Any) -> Any:
        tenant_id, project_id, purpose = params
        values = [
            row["payload"]
            for key, row in self.connection.local.get("parser_approved_snapshots", {}).items()
            if key[:2] == (tenant_id, project_id) and row["payload"]["purpose"] == purpose
        ]
        if not values:
            return None
        latest = max(values, key=lambda item: item["snapshotVersion"])
        active = not self._invalidated("approved_snapshot", latest["snapshotId"])
        return latest["snapshotId"], latest["snapshotVersion"], active

    def _invalidated(self, aggregate_type: str, aggregate_id: str) -> bool:
        return any(
            row["payload"].get("selectionId") == aggregate_id
            or row["payload"].get("snapshotId") == aggregate_id
            for row in self.connection.local.get("parser_validity_events", {}).values()
            if row["params"][3] == aggregate_type
        )

    def _replacement(self, table: str, identity: str, key: str) -> Any:
        row = self.connection.local.get(table, {}).get(("tenant-1", "project-1", identity))
        return (row["payload"].get(key),) if row else None

    def _identity_exists(self, table: str, identity: str) -> Any:
        row = self.connection.local.get(table, {}).get(("tenant-1", "project-1", identity))
        return (identity,) if row else None

    def _validity_target(self, table: str, params: Any, identity_key: str) -> Any:
        event_id, tenant_id, project_id, identity = params
        row = self.connection.local.get(table, {}).get((tenant_id, project_id, identity))
        if not row:
            return None
        aggregate_type = (
            "canonical_selection"
            if table == "parser_canonical_selections"
            else "approved_snapshot"
        )
        invalidated = any(
            candidate["params"][0] != event_id
            and candidate["params"][3] == aggregate_type
            and candidate["params"][4] == identity
            for candidate in self.connection.local.get("parser_validity_events", {}).values()
        )
        assert row["payload"][identity_key] == identity
        return row["payload"], not invalidated

    def _replacement_document(self, identity: str) -> Any:
        row = self.connection.local.get("parser_document_versions", {}).get(
            ("tenant-1", "project-1", identity)
        )
        if not row:
            return None
        return row["payload"].get("supersedesDocumentVersionId"), row["payload"]


def test_transaction_sets_tenant_and_exclusive_project_lock_before_insert() -> None:
    database = FakeDatabase()
    repository = ParserV2PostgresRepository(database.connect)

    persisted = repository.append_record("tenant-1", "project-1", _run())

    assert persisted["parsingRunId"] == "run-1"
    connection = database.connections[-1]
    sql = [statement for statement, _ in connection.statements]
    assert sql[0].startswith("SELECT set_config")
    assert sql[1].startswith("SELECT pg_advisory_xact_lock")
    assert connection.statements[1][1] == ("parser_v2:tenant-1:project-1",)
    assert sql[2].startswith("SELECT project_id FROM projects")
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed is True


def test_identical_hash_replay_returns_existing_but_changed_hash_conflicts() -> None:
    database = FakeDatabase()
    repository = ParserV2PostgresRepository(database.connect)
    original = _run()

    first = repository.append_record("tenant-1", "project-1", original)
    replay = repository.append_record("tenant-1", "project-1", copy.deepcopy(original))

    assert replay == first
    with pytest.raises(ParserV2ImmutableConflict, match="different immutable hash"):
        repository.append_record(
            "tenant-1", "project-1", _run(input_hash="c" * 64)
        )
    assert database.connections[-1].rollbacks == 1


def test_profile_uses_profile_and_plan_hash_for_idempotency() -> None:
    database = FakeDatabase()
    repository = ParserV2PostgresRepository(database.connect)

    first = repository.append_profile("tenant-1", "project-1", _profile())
    replay = repository.append_profile("tenant-1", "project-1", _profile())

    assert replay == first
    assert replay["recordType"] == PROJECT_PROFILE_RECORD_TYPE


def test_transaction_rejects_absent_project_tenant_scope() -> None:
    database = FakeDatabase()
    repository = ParserV2PostgresRepository(database.connect)

    with pytest.raises(ParserV2TenantScopeError):
        repository.append_record("tenant-2", "project-1", _run())

    assert database.connections[-1].rollbacks == 1
    assert "parser_runs" not in database.rows


def test_get_approved_snapshot_direct_lookup_is_not_capped_by_list_limit() -> None:
    database = FakeDatabase()
    snapshots = database.rows.setdefault("parser_approved_snapshots", {})
    for index in range(1001):
        snapshot_id = f"snapshot-{index}"
        snapshots[("tenant-1", "project-1", snapshot_id)] = {
            "payload": {
                "eventKind": "created",
                "snapshotId": snapshot_id,
                "selectionIds": [f"selection-{index}"],
            },
            "hashes": (f"{index:064x}",),
            "params": (),
        }
    repository = ParserV2PostgresRepository(database.connect)

    snapshot = repository.get_approved_snapshot(
        "tenant-1",
        "project-1",
        "snapshot-1000",
    )

    assert snapshot is not None
    assert snapshot["snapshotId"] == "snapshot-1000"
    assert snapshot["selectionIds"] == ["selection-1000"]
    assert all(
        "LIMIT" not in statement
        for statement, _params in database.connections[-1].statements
    )


def test_get_approved_snapshot_preserves_scope_and_requires_created_event() -> None:
    database = FakeDatabase()
    database.rows.setdefault("parser_approved_snapshots", {})[
        ("tenant-1", "project-1", "snapshot-invalidated")
    ] = {
        "payload": {
            "eventKind": "invalidated",
            "snapshotId": "snapshot-invalidated",
            "selectionIds": ["selection-1"],
        },
        "hashes": ("a" * 64,),
        "params": (),
    }
    repository = ParserV2PostgresRepository(database.connect)

    assert (
        repository.get_approved_snapshot(
            "tenant-1",
            "project-1",
            "snapshot-invalidated",
        )
        is None
    )
    with pytest.raises(ParserV2TenantScopeError):
        repository.get_approved_snapshot(
            "tenant-2",
            "project-1",
            "snapshot-invalidated",
        )


def test_append_batch_rolls_back_all_prior_inserts_on_conflict() -> None:
    database = FakeDatabase()
    repository = ParserV2PostgresRepository(database.connect)

    with pytest.raises(ParserV2ImmutableConflict):
        repository.append_batch(
            "tenant-1",
            "project-1",
            ParserV2AppendBatch(
                records=(_run(), _run(input_hash="c" * 64)),
            ),
        )

    assert "parser_runs" not in database.rows
    assert database.connections[-1].commits == 0
    assert database.connections[-1].rollbacks == 1


def test_concurrent_first_selection_is_rechecked_against_database_state() -> None:
    database = FakeDatabase()
    database.seed_candidate_review("selection-1")
    repository = ParserV2PostgresRepository(database.connect)
    repository.append_record("tenant-1", "project-1", _selection("selection-1"))

    database.seed_candidate_review("selection-racer")
    with pytest.raises(ParserV2ImmutableConflict, match="supersedes/version"):
        repository.append_record(
            "tenant-1", "project-1", _selection("selection-racer")
        )

    database.seed_candidate_review("selection-2")
    persisted = repository.append_batch(
        "tenant-1",
        "project-1",
        ParserV2AppendBatch(
            records=(
                _selection("selection-2", version=2, supersedes="selection-1"),
                _selection_invalidation("selection-1", "selection-2"),
            )
        ),
    )
    assert [item["eventKind"] for item in persisted["records"]] == ["created", "invalidated"]


def test_superseding_selection_without_same_transaction_invalidation_rolls_back() -> None:
    database = FakeDatabase()
    database.seed_candidate_review("selection-1")
    repository = ParserV2PostgresRepository(database.connect)
    repository.append_record("tenant-1", "project-1", _selection("selection-1"))

    database.seed_candidate_review("selection-2")
    with pytest.raises(ParserV2ImmutableConflict, match="matching invalidation"):
        repository.append_record(
            "tenant-1",
            "project-1",
            _selection("selection-2", version=2, supersedes="selection-1"),
        )

    selections = database.rows["parser_canonical_selections"]
    assert len(selections) == 1


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("missing", "candidate is absent"),
        ("blocked", "stale, absent, or blocked"),
        ("open_conflict", "open conflict"),
        ("derived_input", "inputs are not all active"),
    ],
)
def test_selection_rechecks_candidate_approval_authority(
    mode: str, message: str
) -> None:
    database = FakeDatabase()
    if mode != "missing":
        database.seed_candidate_review(
            "selection-1",
            validation_status="blocked" if mode == "blocked" else "passed",
            origin_type="derived" if mode == "derived_input" else "extracted",
        )
    if mode == "open_conflict":
        database.open_conflict_candidates.add("candidate:selection-1")
    if mode == "derived_input":
        database.unselected_derived_inputs.add("candidate:selection-1")
    repository = ParserV2PostgresRepository(database.connect)

    with pytest.raises(ParserV2ImmutableConflict, match=message):
        repository.append_record(
            "tenant-1", "project-1", _selection("selection-1")
        )


def test_selection_rejects_a_superseding_non_approving_review() -> None:
    database = FakeDatabase()
    database.seed_candidate_review("selection-1")
    database.seed_candidate_review(
        "selection-1", review_sequence=2, review_action="reject"
    )
    repository = ParserV2PostgresRepository(database.connect)

    with pytest.raises(ParserV2ImmutableConflict, match="latest approving review"):
        repository.append_record(
            "tenant-1", "project-1", _selection("selection-1")
        )


def test_snapshot_membership_and_outbox_are_one_rollback_boundary() -> None:
    database = FakeDatabase()
    database.seed_candidate_review("selection-1")
    repository = ParserV2PostgresRepository(database.connect)
    repository.append_record("tenant-1", "project-1", _selection("selection-1"))
    database.fail_on_table = "runtime_outbox_events"

    with pytest.raises(RuntimeError, match="injected runtime_outbox_events failure"):
        repository.append_record("tenant-1", "project-1", _snapshot())

    assert "parser_approved_snapshots" not in database.rows
    assert "parser_snapshot_selections" not in database.rows
    assert "parser_snapshot_source_runs" not in database.rows
    assert "runtime_outbox_events" not in database.rows
    assert database.connections[-1].rollbacks == 1


def test_snapshot_writes_deterministic_pending_outbox_and_replays_exactly() -> None:
    database = FakeDatabase()
    database.seed_candidate_review("selection-1")
    repository = ParserV2PostgresRepository(database.connect)
    repository.append_record("tenant-1", "project-1", _selection("selection-1"))

    repository.append_record("tenant-1", "project-1", _snapshot())
    replay = _snapshot()
    replay["approvedAt"] = "2026-07-15T01:03:00+00:00"
    replay["createdAt"] = "2026-07-15T01:03:00+00:00"
    replay = _with_record_hash(replay)
    repository.append_record("tenant-1", "project-1", replay)

    outbox = list(database.rows["runtime_outbox_events"].values())
    assert len(outbox) == 1
    payload = outbox[0]["payload"]
    assert payload["eventType"] == "parser.approved_snapshot.created"
    assert payload["aggregateId"] == "snapshot-1"
    assert payload["payload"]["selectionIds"] == ["selection-1"]
    assert payload["status"] == "pending"
    assert payload["availableAt"] == payload["createdAt"] == payload["updatedAt"]
    assert payload["attemptCount"] == 0
    assert payload["maxAttempts"] == 8
    assert RuntimeOutboxEvent.model_validate(payload).event_type == (
        "parser.approved_snapshot.created"
    )


def test_snapshot_replay_accepts_outbox_lifecycle_changes_only() -> None:
    database = FakeDatabase()
    database.seed_candidate_review("selection-1")
    repository = ParserV2PostgresRepository(database.connect)
    repository.append_record("tenant-1", "project-1", _selection("selection-1"))
    repository.append_record("tenant-1", "project-1", _snapshot())

    outbox_row = next(iter(database.rows["runtime_outbox_events"].values()))
    outbox_row["payload"].update(
        {
            "status": "published",
            "attemptCount": 1,
            "availableAt": "2026-07-15T00:04:00+00:00",
            "leaseOwner": None,
            "leaseExpiresAt": None,
            "lastError": None,
            "updatedAt": "2026-07-15T00:05:00+00:00",
            "publishedAt": "2026-07-15T00:05:00+00:00",
        }
    )

    replay = repository.append_record("tenant-1", "project-1", _snapshot())
    assert replay["snapshotId"] == "snapshot-1"
    assert len(database.rows["runtime_outbox_events"]) == 1
    assert next(iter(database.rows["runtime_outbox_events"].values()))[
        "payload"
    ]["status"] == "published"

    outbox_row = next(iter(database.rows["runtime_outbox_events"].values()))
    outbox_row["payload"]["payload"]["selectionIds"] = ["selection-tampered"]
    with pytest.raises(ParserV2ImmutableConflict, match="outbox payload differs"):
        repository.append_record("tenant-1", "project-1", _snapshot())


def test_snapshot_rechecks_materialized_selection_and_latest_review() -> None:
    database = FakeDatabase()
    database.seed_candidate_review("selection-1")
    repository = ParserV2PostgresRepository(database.connect)
    repository.append_record("tenant-1", "project-1", _selection("selection-1"))

    tampered = _snapshot("snapshot-tampered")
    tampered["selections"][0]["selectionVersion"] = 99
    tampered = _with_record_hash(tampered)
    with pytest.raises(ParserV2ImmutableConflict, match="differs from database"):
        repository.append_record("tenant-1", "project-1", tampered)

    database.seed_candidate_review(
        "selection-1", review_sequence=2, review_action="reject"
    )
    with pytest.raises(ParserV2ImmutableConflict, match="latest approving review"):
        repository.append_record(
            "tenant-1", "project-1", _snapshot("snapshot-stale-review")
        )


def test_selection_invalidation_cascades_to_newly_active_snapshot() -> None:
    database = FakeDatabase()
    database.seed_candidate_review("selection-1")
    repository = ParserV2PostgresRepository(database.connect)
    repository.append_record("tenant-1", "project-1", _selection("selection-1"))
    repository.append_record("tenant-1", "project-1", _snapshot())
    database.seed_candidate_review("selection-2")

    repository.append_batch(
        "tenant-1",
        "project-1",
        ParserV2AppendBatch(
            records=(
                _selection("selection-2", version=2, supersedes="selection-1"),
                _selection_invalidation("selection-1", "selection-2"),
            )
        ),
    )

    validity_payloads = [
        item["payload"]
        for item in database.rows["parser_validity_events"].values()
    ]
    assert any(
        item.get("snapshotId") == "snapshot-1"
        and item.get("sourceType") == "parser_v2_transactional_cascade"
        for item in validity_payloads
    )


def test_document_replacement_validity_checks_lineage_and_target_source() -> None:
    database = FakeDatabase()
    database.seed_candidate_review("selection-1")
    database.seed_document_version("doc-new", supersedes="doc-old")
    repository = ParserV2PostgresRepository(database.connect)
    repository.append_record("tenant-1", "project-1", _selection("selection-1"))

    persisted = repository.append_record(
        "tenant-1",
        "project-1",
        _document_selection_invalidation(
            "selection-1", old_document_id="doc-old", new_document_id="doc-new"
        ),
    )
    assert persisted["supersededDocumentVersionId"] == "doc-old"


def test_document_replacement_and_missing_validity_target_fail_closed() -> None:
    database = FakeDatabase()
    database.seed_candidate_review("selection-1")
    database.seed_document_version("doc-bad", supersedes="another-doc")
    repository = ParserV2PostgresRepository(database.connect)
    repository.append_record("tenant-1", "project-1", _selection("selection-1"))

    with pytest.raises(ParserV2ImmutableConflict, match="does not supersede"):
        repository.append_record(
            "tenant-1",
            "project-1",
            _document_selection_invalidation(
                "selection-1", old_document_id="doc-old", new_document_id="doc-bad"
            ),
        )
    missing = _selection_invalidation("missing-selection", "selection-2")
    with pytest.raises(ParserV2ImmutableConflict, match="target is absent"):
        repository.append_record("tenant-1", "project-1", missing)


def test_candidate_set_seal_rechecks_database_membership_and_closes_run() -> None:
    database = FakeDatabase()
    repository = ParserV2PostgresRepository(database.connect)
    candidate = _candidate("candidate-1")

    repository.append_record("tenant-1", "project-1", candidate)
    repository.append_record("tenant-1", "project-1", _seal(candidate))

    # An exact replay remains idempotent after the run is sealed.
    repository.append_record("tenant-1", "project-1", candidate)
    with pytest.raises(ParserV2ImmutableConflict, match="after the parsing run is sealed"):
        repository.append_record(
            "tenant-1", "project-1", _candidate("candidate-late")
        )

    assert len(database.rows["parsed_fact_candidates"]) == 1
    assert len(database.rows["parser_candidate_set_seals"]) == 1


def test_candidate_set_seal_rejects_untrusted_hash_or_membership() -> None:
    database = FakeDatabase()
    repository = ParserV2PostgresRepository(database.connect)
    candidate = _candidate("candidate-1")
    repository.append_record("tenant-1", "project-1", candidate)

    with pytest.raises(ParserV2ImmutableConflict, match="candidate-set seal"):
        repository.append_record(
            "tenant-1", "project-1", _seal(candidate, candidate_set_hash="f" * 64)
        )

    mismatched = _seal(candidate)
    mismatched["candidateIds"] = ["candidate-from-client-only"]
    mismatched = _with_record_hash(mismatched)
    with pytest.raises(ParserV2ImmutableConflict, match="candidate-set seal"):
        repository.append_record("tenant-1", "project-1", mismatched)

    assert "parser_candidate_set_seals" not in database.rows


def test_candidate_finding_replay_uses_authoritative_stored_timestamp() -> None:
    database = FakeDatabase()
    repository = ParserV2PostgresRepository(database.connect)
    candidate = _candidate("candidate-with-finding")
    candidate["validationFindings"] = [
        {
            "code": "PARSER_WARNING",
            "severity": "warning",
            "message": "Controlled warning",
            "blocking": False,
        }
    ]
    candidate["validationCodes"] = ["PARSER_WARNING"]
    candidate = _with_record_hash(candidate)

    repository.append_record("tenant-1", "project-1", candidate)
    replay = copy.deepcopy(candidate)
    replay["createdAt"] = "2026-07-15T02:00:30+00:00"
    replay = _with_record_hash(replay)
    repository.append_record("tenant-1", "project-1", replay)

    findings = list(database.rows["parser_validation_findings"].values())
    assert len(findings) == 1
    assert findings[0]["payload"]["createdAt"] == candidate["createdAt"]


def test_fixed_dispatch_never_uses_update_upsert_or_schema_bootstrap() -> None:
    insert_sql = [
        value
        for name, value in vars(repository_module).items()
        if name.startswith("_INSERT_") and name.endswith("_SQL")
    ]
    assert insert_sql
    assert all("DO UPDATE" not in statement for statement in insert_sql)
    assert all("ON CONFLICT" in statement for statement in insert_sql)
    source = repository_module.__file__
    assert source is not None

    database = FakeDatabase()
    repository = ParserV2PostgresRepository(database.connect)
    unknown = _with_record_hash(
        {
            "recordType": "parser_v2_unknown",
            "eventKind": "created",
            "tenantId": "tenant-1",
            "projectId": "project-1",
        }
    )
    with pytest.raises(ParserV2UnsupportedRecord):
        repository.append_record("tenant-1", "project-1", unknown)


def test_bind_uses_caller_owned_connection_without_commit_or_close() -> None:
    database = FakeDatabase()
    connection = database.connect()
    repository = ParserV2PostgresRepository(database.connect)

    session = repository.bind(connection, "tenant-1", "project-1")
    session.append_record(_run())
    session.validate_batch()

    assert connection.commits == 0
    assert connection.rollbacks == 0
    assert connection.closed is False
    assert "parser_runs" not in database.rows


def test_project_scope_check_requires_only_the_manifest_select_privilege() -> None:
    assert "FOR KEY SHARE" not in repository_module._LOCK_PROJECT_SCOPE_SQL
    assert repository_module._LOCK_PROJECT_SCOPE_SQL.startswith(
        "SELECT project_id FROM projects"
    )
