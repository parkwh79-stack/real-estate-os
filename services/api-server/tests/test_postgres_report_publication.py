from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

import pytest

from agents.report_agent import ReportAgent
from db.postgres_runtime_privileges import (
    REPORT_PUBLICATION_FORBIDDEN_PRIVILEGES,
    REPORT_PUBLICATION_PRIVILEGES,
    forbidden_table_privileges,
    required_table_privileges,
)
from db.repositories.orchestration_repository import (
    PostgresOrchestrationRepository,
)
from db.repositories.project_snapshot_repository import (
    PostgresProjectSnapshotRepository,
    project_snapshot_content_hash,
)
from db.repositories.report_repository import (
    PostgresReportRepository,
    ReportPublicationConflict,
    ReportRepository,
)
from scripts.render_supabase_migrations import (
    GENERATED_MARKER,
    source_checksum,
    sync_migrations,
)
from use_cases import report_generation
from use_cases.report_generation import publish_saved_report_use_case


ROOT = Path(__file__).resolve().parents[3]
MIGRATION_NAME = "20260727002612_normalized_report_publication.sql"
SOURCE_MIGRATION = (
    ROOT / "services" / "api-server" / "db" / "migrations" / MIGRATION_NAME
)
SUPABASE_MIGRATION = ROOT / "supabase" / "migrations" / MIGRATION_NAME
LOOKUP_FIX_MIGRATION_NAME = (
    "20260727002700_report_publication_lineage_lookup_fix.sql"
)
LOOKUP_FIX_SOURCE_MIGRATION = (
    ROOT
    / "services"
    / "api-server"
    / "db"
    / "migrations"
    / LOOKUP_FIX_MIGRATION_NAME
)
LOOKUP_FIX_SUPABASE_MIGRATION = (
    ROOT / "supabase" / "migrations" / LOOKUP_FIX_MIGRATION_NAME
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


class _FakeReportDatabase:
    def __init__(self) -> None:
        self.lock = RLock()
        self.projects = {("tenant-a", "project-1")}
        self.orchestrations: dict[
            tuple[str, str, str], dict[str, Any]
        ] = {}
        self.snapshots: dict[
            tuple[str, str, str], dict[str, Any]
        ] = {}
        self.market_evidence: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.risk_registers: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.evidence_graphs: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.assumption_proposals: dict[
            tuple[str, str], dict[str, Any]
        ] = {}
        self.reports: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.publications: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.statements: list[tuple[str, tuple[Any, ...] | None]] = []

    def execute(
        self,
        sql: str,
        params: tuple[Any, ...] | None = None,
    ) -> _Result:
        compact = " ".join(sql.split())
        lowered = compact.casefold()
        self.statements.append((compact, params))

        if lowered.startswith("select pg_advisory_xact_lock"):
            return _Result(one=(None,))

        if (
            lowered.startswith("select project_id")
            and "from realytics.projects" in lowered
        ):
            assert params is not None
            return _Result(one=(params[1],) if tuple(params) in self.projects else None)

        if lowered.startswith("select coalesce(max(version), 0) + 1"):
            assert params is not None
            tenant_id, project_id = params
            versions = [
                row["version"]
                for (tenant, project, _report), row in self.reports.items()
                if (tenant, project) == (tenant_id, project_id)
            ]
            return _Result(one=((max(versions) if versions else 0) + 1,))

        if lowered.startswith("insert into realytics.orchestrations"):
            assert params is not None
            (
                tenant_id,
                project_id,
                run_id,
                status,
                started_at,
                completed_at,
                payload,
            ) = params
            self.orchestrations[(tenant_id, project_id, run_id)] = {
                "project_id": project_id,
                "run_id": run_id,
                "status": status,
                "started_at": started_at,
                "completed_at": completed_at,
                "payload_json": json.loads(payload),
            }
            return _Result(one=(run_id,))

        if (
            lowered.startswith("select project_id, run_id, status")
            and "from realytics.orchestrations" in lowered
        ):
            assert params is not None
            run = self.orchestrations.get(tuple(params))
            if run is None:
                return _Result(one=None)
            return _Result(
                one=(
                    run["project_id"],
                    run["run_id"],
                    run["status"],
                    run["started_at"],
                    run["completed_at"],
                    deepcopy(run["payload_json"]),
                )
            )

        if (
            lowered.startswith("select run_id")
            and "from realytics.orchestrations" in lowered
        ):
            assert params is not None
            run = self.orchestrations.get(tuple(params))
            return _Result(one=(run["run_id"],) if run else None)

        if (
            lowered.startswith("select payload_json")
            and "from realytics.project_snapshots" in lowered
        ):
            assert params is not None
            snapshot = self.snapshots.get(tuple(params))
            return _Result(
                one=(deepcopy(snapshot["payload_json"]),)
                if snapshot
                else None
            )

        if lowered.startswith("insert into realytics.project_snapshots"):
            assert params is not None
            tenant_id, project_id, snapshot_id, run_id, created_at, payload = params
            key = (tenant_id, project_id, snapshot_id)
            if key in self.snapshots:
                return _Result(one=None)
            self.snapshots[key] = {
                "run_id": run_id,
                "created_at": created_at,
                "payload_json": json.loads(payload),
            }
            return _Result(one=(snapshot_id,))

        if lowered.startswith("insert into realytics.market_evidence"):
            assert params is not None
            key = (params[0], params[1], params[2])
            self.market_evidence[key] = json.loads(params[-1])
            return _Result(one=(params[2],))

        if lowered.startswith("insert into realytics.risk_registers"):
            assert params is not None
            key = (params[0], params[1], params[2])
            self.risk_registers[key] = json.loads(params[-1])
            return _Result(one=(params[2],))

        if lowered.startswith("insert into realytics.evidence_graphs"):
            assert params is not None
            key = (params[0], params[1], params[2])
            self.evidence_graphs[key] = json.loads(params[-1])
            return _Result(one=(params[2],))

        if lowered.startswith("insert into realytics.assumption_proposals"):
            assert params is not None
            key = (params[0], params[1])
            self.assumption_proposals[key] = json.loads(params[-1])
            return _Result(one=(params[1],))

        if lowered.startswith("insert into realytics.reports"):
            assert params is not None
            tenant_id, project_id, report_id, version, created_at, payload = params
            self.reports[(tenant_id, project_id, report_id)] = {
                "project_id": project_id,
                "report_id": report_id,
                "version": version,
                "created_at": created_at,
                "payload_json": json.loads(payload),
            }
            return _Result()

        if "left join realytics.report_publications" in lowered:
            assert params is not None
            tenant_id, project_id, report_id = params
            report = self.reports.get((tenant_id, project_id, report_id))
            if report is None:
                return _Result(one=None)
            publication = self.publications.get(
                (tenant_id, project_id, report_id)
            )
            return _Result(one=self._joined_row(report, publication))

        if (
            lowered.startswith("select project_id, report_id, version")
            and "from realytics.reports" in lowered
        ):
            assert params is not None
            report = self.reports.get(tuple(params))
            return _Result(one=self._report_row(report) if report else None)

        if (
            lowered.startswith("select report_version")
            and "from realytics.report_publications" in lowered
        ):
            assert params is not None
            publication = self.publications.get(tuple(params))
            return _Result(
                one=self._publication_row(publication)
                if publication
                else None
            )

        if lowered.startswith("insert into realytics.report_publications"):
            assert params is not None
            key = tuple(params[:3])
            if key in self.publications:
                return _Result(one=None)
            publication = {
                "report_version": params[3],
                "orchestration_run_id": params[4],
                "project_snapshot_id": params[5],
                "project_snapshot_hash": params[6],
                "content_sha256": params[7],
                "decision_package_sha256": params[8],
                "idempotency_key": params[9],
                "decision_package_json": json.loads(params[10]),
                "published_by": params[11],
                "published_at": params[12],
                "created_at": "2026-07-27T00:00:00+00:00",
            }
            self.publications[key] = publication
            return _Result(one=self._publication_row(publication))

        if lowered.startswith("update realytics.reports"):
            assert params is not None
            payload, tenant_id, project_id, report_id = params
            report = self.reports.get((tenant_id, project_id, report_id))
            if report is None:
                return _Result(one=None)
            report["payload_json"] = json.loads(payload)
            return _Result(one=(report_id,))

        raise AssertionError(f"Unexpected SQL: {compact}")

    @staticmethod
    def _report_row(report: dict[str, Any]) -> tuple[Any, ...]:
        return (
            report["project_id"],
            report["report_id"],
            report["version"],
            report["created_at"],
            deepcopy(report["payload_json"]),
        )

    @classmethod
    def _joined_row(
        cls,
        report: dict[str, Any],
        publication: dict[str, Any] | None,
    ) -> tuple[Any, ...]:
        return cls._report_row(report) + (
            cls._publication_row(publication)
            if publication
            else (None,) * 11
        )

    @staticmethod
    def _publication_row(publication: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(
            publication[key]
            for key in (
                "report_version",
                "orchestration_run_id",
                "project_snapshot_id",
                "project_snapshot_hash",
                "content_sha256",
                "decision_package_sha256",
                "idempotency_key",
                "decision_package_json",
                "published_by",
                "published_at",
                "created_at",
            )
        )


class _Transactions:
    def __init__(self, database: _FakeReportDatabase) -> None:
        self.database = database
        self.tenant_ids: list[str] = []

    @contextmanager
    def __call__(self, tenant_id: str):
        self.tenant_ids.append(tenant_id)
        with self.database.lock:
            yield self.database


def _structured_report() -> dict[str, Any]:
    return {
        "template": "phase-11-pf-review",
        "title": "PF review",
        "qualityChecks": {
            "actualSectionCount": 1,
            "expectedSectionCount": 1,
            "isComplete": True,
        },
        "sections": {"cover": "evidence backed"},
    }


def _decision_package(snapshot_hash: str | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": "realytics-decision-package-manifest-v1",
        "projectId": "project-1",
        "projectSnapshotId": "snapshot-1",
        "projectSnapshotHash": snapshot_hash or "sha256:" + ("1" * 64),
        "orchestrationRunId": "run-1",
        "approvalStatus": "approved",
    }


def _postgres_repository():
    database = _FakeReportDatabase()
    transactions = _Transactions(database)
    repository = PostgresReportRepository(
        tenant_id="tenant-a",
        transaction_factory=transactions,
    )
    return repository, database, transactions


def test_normalized_publication_is_idempotent_under_concurrent_requests() -> None:
    repository, database, transactions = _postgres_repository()
    created = repository.create(
        "project-1",
        _structured_report(),
        "# report",
        run_id="run-1",
    )
    read_token = repository.get("project-1", created["report_id"])
    assert read_token is not None

    def publish(actor: str):
        return repository.publish(
            deepcopy(read_token),
            published_by=actor,
            published_at="2026-07-27T01:00:00+00:00",
            decision_package=_decision_package(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, ("checker-a", "checker-b")))

    assert len(database.publications) == 1
    assert {result["status"] for result in results} == {"published"}
    assert len(
        {
            result["publication"]["idempotencyKey"]
            for result in results
        }
    ) == 1
    assert len(
        {
            result["publication"]["publishedBy"]
            for result in results
        }
    ) == 1
    assert transactions.tenant_ids == ["tenant-a"] * 4
    sql = " ".join(statement for statement, _params in database.statements)
    assert "ON CONFLICT (tenant_id, project_id, report_id) DO NOTHING" in sql
    assert "FOR UPDATE" in sql
    assert "app_state" not in sql.casefold()


def test_normalized_run_report_and_snapshot_share_one_transaction() -> None:
    report_repository, database, transactions = _postgres_repository()
    snapshot_repository = PostgresProjectSnapshotRepository(
        tenant_id="tenant-a",
        transaction_factory=transactions,
    )
    repository = PostgresOrchestrationRepository(
        tenant_id="tenant-a",
        report_repository=report_repository,
        project_snapshot_repository=snapshot_repository,
        transaction_factory=transactions,
    )
    run = {
        "run_id": "run-1",
        "project_id": "project-1",
        "workflow": "pf-review",
        "status": "completed",
        "executionStatus": "completed",
        "approvalStatus": "not_requested",
        "started_at": "2026-07-27T00:00:00+00:00",
        "completed_at": "2026-07-27T00:01:00+00:00",
        "steps": [],
        "errors": [],
        "options": {"tenantId": "tenant-a", "requestedBy": "maker-1"},
        "artifacts": {
            "phase11Report": {
                "report": _structured_report(),
                "report_markdown": "# report",
            },
            "marketEvidenceReview": {
                "evidenceRecords": [
                    {
                        "evidenceId": "evidence-1",
                        "evidenceType": "transaction",
                        "validationStatus": "pass",
                    }
                ]
            },
            "riskRegister": {
                "status": "review_required",
                "risks": [{"riskId": "risk-1"}],
            },
            "evidenceGraph": {
                "nodes": [{"id": "node-1", "type": "source_locator"}],
                "edges": [],
            },
            "assumptionProposals": {
                "proposals": [
                    {
                        "proposalId": "proposal-1",
                        "fieldPath": "cost.contingency",
                        "sourceAgent": "feasibility",
                        "status": "pending_review",
                    }
                ]
            },
        },
    }

    result = repository.save_run(
        "project-1",
        run,
        {"project": {"id": "project-1", "name": "Project 1"}},
    )

    assert transactions.tenant_ids == ["tenant-a"]
    assert len(database.orchestrations) == 1
    assert len(database.reports) == 1
    assert len(database.snapshots) == 1
    assert len(database.market_evidence) == 1
    assert len(database.risk_registers) == 1
    assert len(database.evidence_graphs) == 1
    assert len(database.assumption_proposals) == 1
    report_meta = result["artifacts"]["reportRecord"]
    snapshot_meta = result["artifacts"]["projectSnapshot"]
    assert report_meta["report_id"]
    assert snapshot_meta["snapshotId"] == "snapshot-run-1"
    stored_run = database.orchestrations[
        ("tenant-a", "project-1", "run-1")
    ]["payload_json"]
    assert stored_run["artifacts"]["reportRecord"]["report_id"] == (
        report_meta["report_id"]
    )
    assert stored_run["artifacts"]["projectSnapshot"]["snapshotHash"] == (
        snapshot_meta["snapshotHash"]
    )
    sql = " ".join(statement for statement, _params in database.statements)
    assert "INSERT INTO realytics.orchestrations" in sql
    assert "INSERT INTO realytics.reports" in sql
    assert "INSERT INTO realytics.project_snapshots" in sql
    assert "INSERT INTO realytics.market_evidence" in sql
    assert "INSERT INTO realytics.risk_registers" in sql
    assert "INSERT INTO realytics.evidence_graphs" in sql
    assert "INSERT INTO realytics.assumption_proposals" in sql
    assert "app_state" not in sql.casefold()


def test_concurrent_different_decision_package_is_a_conflict() -> None:
    repository, database, _transactions = _postgres_repository()
    created = repository.create(
        "project-1",
        _structured_report(),
        "# report",
        run_id="run-1",
    )
    read_token = repository.get("project-1", created["report_id"])
    assert read_token is not None

    decisions = (
        _decision_package("sha256:" + ("2" * 64)),
        _decision_package("sha256:" + ("3" * 64)),
    )

    def publish(index: int):
        try:
            return repository.publish(
                deepcopy(read_token),
                published_by=f"checker-{index}",
                published_at="2026-07-27T01:00:00+00:00",
                decision_package=decisions[index],
            )
        except ReportPublicationConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, (0, 1)))

    assert len(database.publications) == 1
    assert len([result for result in results if isinstance(result, dict)]) == 1
    conflicts = [
        result
        for result in results
        if isinstance(result, ReportPublicationConflict)
    ]
    assert len(conflicts) == 1
    assert "cannot be rebound" in str(conflicts[0])


def test_published_report_fails_closed_when_compatibility_payload_is_tampered() -> None:
    repository, database, _transactions = _postgres_repository()
    created = repository.create(
        "project-1",
        _structured_report(),
        "# report",
        run_id="run-1",
    )
    published = repository.publish(
        created,
        published_by="checker-1",
        published_at="2026-07-27T01:00:00+00:00",
        decision_package=_decision_package(),
    )

    key = ("tenant-a", "project-1", published["report_id"])
    payload = database.reports[key]["payload_json"]
    payload["report"]["sections"]["cover"] = "tampered after publication"
    payload["metadata"].pop("content_sha256", None)

    with pytest.raises(
        ReportPublicationConflict,
        match="immutable seal",
    ):
        repository.get("project-1", published["report_id"])


def test_published_report_rejects_late_export_mutation() -> None:
    repository, _database, _transactions = _postgres_repository()
    created = repository.create(
        "project-1",
        _structured_report(),
        "# report",
        run_id="run-1",
    )
    published = repository.publish(
        created,
        published_by="checker-1",
        published_at="2026-07-27T01:00:00+00:00",
        decision_package=_decision_package(),
    )

    with pytest.raises(ReportPublicationConflict, match="immutable"):
        repository.add_export(
            published,
            {
                "format": "markdown",
                "file_name": "report.md",
                "content_type": "text/markdown",
                "content": "# report",
            },
        )


def test_publication_rejects_mismatched_project_or_run_lineage() -> None:
    repository, _database, _transactions = _postgres_repository()
    created = repository.create(
        "project-1",
        _structured_report(),
        "# report",
        run_id="run-1",
    )

    wrong_project = _decision_package()
    wrong_project["projectId"] = "project-2"
    with pytest.raises(ReportPublicationConflict, match="project"):
        repository.publish(
            deepcopy(created),
            published_by="checker-1",
            published_at="2026-07-27T01:00:00+00:00",
            decision_package=wrong_project,
        )

    wrong_run = _decision_package()
    wrong_run["orchestrationRunId"] = "run-2"
    with pytest.raises(ReportPublicationConflict, match="orchestration run"):
        repository.publish(
            deepcopy(created),
            published_by="checker-1",
            published_at="2026-07-27T01:00:00+00:00",
            decision_package=wrong_run,
        )


def test_report_publication_migration_is_tenant_native_and_immutable() -> None:
    source = SOURCE_MIGRATION.read_text(encoding="utf-8")
    rendered = SUPABASE_MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS report_publications" in source
    assert "PRIMARY KEY (tenant_id, project_id, report_id)" in source
    assert "FOREIGN KEY (tenant_id, project_id, report_id)" in source
    assert "FOREIGN KEY (tenant_id, project_id, orchestration_run_id)" in source
    assert "FOREIGN KEY (tenant_id, project_id, project_snapshot_id)" in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "current_setting('realytics.tenant_id', true)" in source
    assert "BEFORE UPDATE OR DELETE" in source
    assert "report_publication_reject_mutation" in source
    assert "report_publication_validate_insert" in source
    assert "published_report_reject_mutation" in source
    assert "stored_snapshot_hash IS DISTINCT FROM NEW.project_snapshot_hash" in source
    assert "stored_report_content_sha256 IS DISTINCT FROM NEW.content_sha256" in source
    assert "lower(stored_run_maker) = lower(stored_run_approver)" in source
    assert (
        "NEW.decision_package_json->>'approvedBy'"
        in source
    )
    assert "lower(stored_run_approval_status, '') <> 'approved'" not in source
    assert "lower(COALESCE(stored_run_approval_status, '')) <> 'approved'" in source
    assert rendered.startswith(GENERATED_MARKER)
    assert source_checksum(SOURCE_MIGRATION) in rendered
    assert sync_migrations(check=True) == ()


def test_report_publication_lookup_fix_uses_dynamic_row_count() -> None:
    source = LOOKUP_FIX_SOURCE_MIGRATION.read_text(encoding="utf-8")
    rendered = LOOKUP_FIX_SUPABASE_MIGRATION.read_text(encoding="utf-8")

    assert source.count("GET DIAGNOSTICS matched_rows = ROW_COUNT") == 3
    assert source.count("IF matched_rows <> 1 THEN") == 3
    assert "IF NOT FOUND THEN" not in source
    assert "CREATE OR REPLACE FUNCTION report_publication_validate_insert" in source
    assert rendered.startswith(GENERATED_MARKER)
    assert source_checksum(LOOKUP_FIX_SOURCE_MIGRATION) in rendered


def test_runtime_privileges_are_exact_for_report_publication() -> None:
    assert REPORT_PUBLICATION_PRIVILEGES == {
        "assumption_proposals": ("SELECT", "INSERT", "UPDATE"),
        "evidence_graphs": ("SELECT", "INSERT", "UPDATE"),
        "market_evidence": ("SELECT", "INSERT", "UPDATE"),
        "orchestrations": ("SELECT", "INSERT", "UPDATE"),
        "project_snapshots": ("SELECT", "INSERT", "UPDATE"),
        "reports": ("SELECT", "INSERT", "UPDATE"),
        "report_publications": ("SELECT", "INSERT"),
        "risk_registers": ("SELECT", "INSERT", "UPDATE"),
    }
    assert required_table_privileges()["report_publications"] == (
        "SELECT",
        "INSERT",
    )
    assert forbidden_table_privileges()["report_publications"] == (
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    )
    assert forbidden_table_privileges()["reports"] == (
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    )
    assert REPORT_PUBLICATION_FORBIDDEN_PRIVILEGES[
        "report_publications"
    ] == forbidden_table_privileges()["report_publications"]


def test_final_report_publication_rejects_the_pf_review_maker(
    monkeypatch,
) -> None:
    store: dict[str, list[dict[str, Any]]] = {}
    repository = ReportRepository(store, lambda: None)
    record = repository.create(
        "project-1",
        _structured_report(),
        "# report",
        run_id="run-1",
    )
    run = {
        "run_id": "run-1",
        "project_id": "project-1",
        "workflow": "pf-review",
        "approvalStatus": "approved",
        "user_id": "maker-1",
        "approval": {
            "approved_by": "approval-checker",
            "approved_at": "2026-07-27T00:00:00+00:00",
        },
    }

    class Orchestrations:
        def get(self, project_id, run_id):
            return run

    monkeypatch.setattr(
        report_generation,
        "refresh_approval_readiness_gate",
        lambda _run: {"pass": True},
    )

    with pytest.raises(ValueError, match="Maker-checker violation"):
        publish_saved_report_use_case(
            repository,
            Orchestrations(),
            object(),
            "project-1",
            record["report_id"],
            published_by="MAKER-1",
            published_at=datetime.now(timezone.utc).isoformat(),
        )
