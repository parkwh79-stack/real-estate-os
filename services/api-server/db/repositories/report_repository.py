from __future__ import annotations

import hashlib
import base64
import json
from collections.abc import Mapping
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Dict, List
from uuid import uuid4

from db.postgres_runtime_pool import api_tenant_transaction


TransactionFactory = Callable[[str], AbstractContextManager[Any]]
_LOCAL_REPORT_LOCK = RLock()


class ReportPublicationConflict(ValueError):
    """A report changed or was sealed against another decision package."""


class ReportPayloadDataError(RuntimeError):
    """A normalized PostgreSQL report payload is not a JSON object."""


COMPARISON_REPORT_TYPE = "cash-flow-comparison-review"
COMPARISON_REPORT_TEMPLATE = "cash-flow-comparison-review-v1"


def has_cash_flow_basis(record: Mapping[str, Any]) -> bool:
    report = record.get("report") or {}
    return ("cashFlowCalculationSnapshot" in report or "cashFlowCalculationSnapshotHash" in report
            or "cash_flow_snapshot_hash" in (record.get("metadata") or {}))


def is_cash_flow_comparison_report(record: Mapping[str, Any]) -> bool:
    report = record.get("report")
    metadata = record.get("metadata")
    return record.get("template") == COMPARISON_REPORT_TEMPLATE or (isinstance(metadata, Mapping) and metadata.get("report_type") == COMPARISON_REPORT_TYPE) or (isinstance(report, Mapping) and (
        report.get("reportType") == COMPARISON_REPORT_TYPE
        or report.get("template") == COMPARISON_REPORT_TEMPLATE
        or "cashFlowComparisonSnapshot" in report
    ))


def _comparison_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def verify_report_snapshots(record: Mapping[str, Any], *, project_id: str | None = None,
    tenant_id: str | None = None) -> None:
    """Verify the saved document, never rebind it to mutable current calculations."""
    report = record.get("report") or {}
    if has_cash_flow_basis(record):
        from use_cases.report_calculation_basis import verify_cash_flow_basis

        try:
            if (record.get("status") != "draft" or record.get("run_id")
                    or (record.get("metadata") or {}).get("report_eligible") is not False):
                raise ValueError("Selected cash-flow reports remain analysis drafts")
            verify_cash_flow_basis(report, project_id=str(project_id or record["project_id"]), tenant_id=tenant_id)
            if not (record.get("metadata") or {}).get("content_sha256"):
                raise ValueError("Report content seal missing")
            if record["metadata"].get("cash_flow_snapshot_hash") != report["cashFlowCalculationSnapshotHash"]:
                raise ValueError("Report snapshot metadata differs")
            _verified_report_content_sha256(record)
            for exported in record.get("exports") or []:
                _verify_comparison_export(record, exported)
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ReportPublicationConflict("보고서에 고정한 현금흐름 계산·본문 검증에 실패했습니다.") from exc
    if not is_cash_flow_comparison_report(record):
        return
    try:
        report = record["report"]
        snapshot = report["cashFlowComparisonSnapshot"]
        if (report.get("reportType") != COMPARISON_REPORT_TYPE
                or report.get("template") != COMPARISON_REPORT_TEMPLATE
                or record.get("status") != "draft" or record.get("run_id")
                or report.get("reportEligible") is not False
                or (record.get("metadata") or {}).get("report_eligible") is not False):
            raise ValueError("comparison draft policy changed")
        if not isinstance(snapshot, dict) or snapshot.get("schemaVersion") != "realytics-frozen-cash-flow-comparison-v1":
            raise ValueError("comparison snapshot missing")
        if (snapshot.get("projectId") != record.get("project_id")
                or (project_id is not None and snapshot.get("projectId") != str(project_id))
                or (tenant_id is not None and snapshot.get("tenantId") != str(tenant_id))):
            raise ValueError("comparison scope changed")
        if report.get("cashFlowComparisonSnapshotHash") != _comparison_hash(snapshot):
            raise ValueError("comparison snapshot hash changed")
        selection = snapshot["selection"]
        for side, field in (("baseline", "baselineRun"), ("comparison", "comparisonRun")):
            run = snapshot[field]
            if (run.get("projectId") != snapshot["projectId"]
                    or str(run.get("tenantId") or "legacy") != snapshot["tenantId"]
                    or run.get("calculationRunId") != selection[side + "RunId"]
                    or _comparison_hash(run["inputSnapshot"]) != selection[side + "InputHash"]
                    or _comparison_hash(run["resultSnapshot"]) != selection[side + "ResultHash"]):
                raise ValueError("comparison source changed")
        if not (record.get("metadata") or {}).get("content_sha256"):
            raise ValueError("comparison report seal missing")
        _verified_report_content_sha256(record)
        for exported in record.get("exports") or []:
            _verify_comparison_export(record, exported)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ReportPublicationConflict("저장된 비교 검토 초안의 원본·본문 검증에 실패했습니다.") from exc


def _verify_comparison_export(record: Mapping[str, Any], exported: Mapping[str, Any]) -> None:
    content = exported.get("content", "")
    data = base64.b64decode(content, validate=True) if exported.get("content_encoding") == "base64" else content.encode("utf-8")
    if (exported.get("report_id") != record["report_id"] or exported.get("project_id") != record["project_id"]
            or exported.get("report_content_sha256") != record["metadata"]["content_sha256"]
            or exported.get("sha256") != hashlib.sha256(data).hexdigest()
            or exported.get("byte_size") != len(data)):
        raise ReportPublicationConflict("저장된 비교 검토 초안의 내보내기 파일이 변경되었습니다.")


class ReportRepository:
    """Repository wrapper for saved report records."""

    def __init__(
        self,
        reports_store: Dict[str, List[Dict[str, Any]]],
        persist: Callable[[], None],
        persist_report: Callable[[str, Dict[str, Any]], None] | None = None,
    ):
        self.reports_store = reports_store
        self.persist = persist
        self.persist_report = persist_report

    def _persist_record(self, project_id: str, record: Dict[str, Any]) -> None:
        if self.persist_report is not None:
            self.persist_report(project_id, record)
        else:
            self.persist()

    def create(
        self,
        project_id: str,
        structured_report: Dict[str, Any],
        report_markdown: str,
        run_id: str | None = None,
    ) -> Dict[str, Any]:
        with _LOCAL_REPORT_LOCK:
            version = len(self.reports_store.get(project_id, [])) + 1
            record = _build_draft_report_record(
                project_id=project_id,
                version=version,
                structured_report=structured_report,
                report_markdown=report_markdown,
                run_id=run_id,
            )
            self.reports_store.setdefault(project_id, []).append(record)
            self._persist_record(project_id, record)
            return record

    def list_metadata(self, project_id: str) -> List[Dict[str, Any]]:
        with _LOCAL_REPORT_LOCK:
            for record in self.reports_store.get(project_id, []):
                verify_report_snapshots(record, project_id=project_id)
            return [
                self.metadata(record)
                for record in self.reports_store.get(project_id, [])
            ]

    def get(self, project_id: str, report_id: str) -> Dict[str, Any] | None:
        with _LOCAL_REPORT_LOCK:
            for record in self.reports_store.get(project_id, []):
                if record.get("report_id") == report_id:
                    record.setdefault("exports", [])
                    record.setdefault("version", 1)
                    verify_report_snapshots(record, project_id=project_id)
                    return record
        return None

    def add_export(self, report_record: Dict[str, Any], rendered: Dict[str, Any]) -> Dict[str, Any]:
        with _LOCAL_REPORT_LOCK:
            verify_report_snapshots(report_record)
            export_record = _build_export_record(report_record, rendered)
            if is_cash_flow_comparison_report(report_record):
                _verify_comparison_export(report_record, export_record)
            report_record.setdefault("exports", []).append(export_record)
            self._persist_record(str(report_record["project_id"]), report_record)
            return export_record

    def publish(
        self,
        report_record: Dict[str, Any],
        *,
        published_by: str,
        published_at: str,
        decision_package: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Promote one immutable draft after a verified decision-package gate.

        Repeating the exact publication is idempotent. A published report cannot
        be rebound to a different run/snapshot or content hash.
        """
        with _LOCAL_REPORT_LOCK:
            if is_cash_flow_comparison_report(report_record) or has_cash_flow_basis(report_record):
                raise ReportPublicationConflict("현금흐름을 선택한 분석 초안은 최종 승인 보고서로 발행할 수 없습니다.")
            content_sha256 = _verified_report_content_sha256(report_record)
            publication = _build_publication_manifest(
                published_by=published_by,
                published_at=published_at,
                content_sha256=content_sha256,
                decision_package=decision_package,
            )
            existing = report_record.get("publication")
            if report_record.get("status") == "published":
                if not isinstance(existing, dict):
                    raise ValueError(
                        "Published report is missing its publication manifest"
                    )
                if (
                    existing.get("contentSha256") != content_sha256
                    or existing.get("decisionPackage") != decision_package
                ):
                    raise ReportPublicationConflict(
                        "Published report cannot be rebound to another decision package"
                    )
                return report_record

            _apply_publication_manifest(report_record, publication)
            self._persist_record(
                str(report_record["project_id"]),
                report_record,
            )
            return report_record

    def get_export(self, report_record: Dict[str, Any], export_id: str) -> Dict[str, Any] | None:
        verify_report_snapshots(report_record)
        for record in report_record.get("exports", []):
            if record.get("export_id") == export_id:
                return record
        return None

    @staticmethod
    def metadata(record: Dict[str, Any]) -> Dict[str, Any]:
        verify_report_snapshots(record)
        return {
            "report_id": record["report_id"],
            "project_id": record["project_id"],
            "run_id": record.get("run_id"),
            "version": record.get("version", 1),
            "template": record["template"],
            "title": record["title"],
            "status": record["status"],
            "created_at": record["created_at"],
            "metadata": record["metadata"],
            "export_count": len(record.get("exports", [])),
            "latest_exported_at": record.get("exports", [{}])[-1].get("created_at") if record.get("exports") else None,
            "published_at": record.get("published_at"),
            "published_by": record.get("published_by"),
            "publication": record.get("publication"),
        }

    @staticmethod
    def export_metadata(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value
            for key, value in record.items()
            if key != "content"
        }


class PostgresReportRepository:
    """Tenant-scoped report drafts plus immutable publication authority.

    ``reports.payload_json`` remains the compatibility document for the report
    body and exports. Finality comes exclusively from
    ``realytics.report_publications``. The report row is locked only for the
    short compare-and-publish transaction, and the publication primary key plus
    ``ON CONFLICT DO NOTHING`` makes concurrent identical requests idempotent.
    """

    _REPORT_SELECT = """
        SELECT
            report.project_id,
            report.report_id,
            report.version,
            report.created_at,
            report.payload_json,
            publication.report_version,
            publication.orchestration_run_id,
            publication.project_snapshot_id,
            publication.project_snapshot_hash,
            publication.content_sha256,
            publication.decision_package_sha256,
            publication.idempotency_key,
            publication.decision_package_json,
            publication.published_by,
            publication.published_at,
            publication.created_at
        FROM realytics.reports AS report
        LEFT JOIN realytics.report_publications AS publication
          ON publication.tenant_id = report.tenant_id
         AND publication.project_id = report.project_id
         AND publication.report_id = report.report_id
    """
    _PUBLICATION_COLUMNS = """
        report_version,
        orchestration_run_id,
        project_snapshot_id,
        project_snapshot_hash,
        content_sha256,
        decision_package_sha256,
        idempotency_key,
        decision_package_json,
        published_by,
        published_at,
        created_at
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

    def create(
        self,
        project_id: str,
        structured_report: Dict[str, Any],
        report_markdown: str,
        run_id: str | None = None,
    ) -> Dict[str, Any]:
        with self._transaction(self.tenant_id) as connection:
            return self.create_in_transaction(
                connection,
                project_id,
                structured_report,
                report_markdown,
                run_id=run_id,
            )

    def create_in_transaction(
        self,
        connection: Any,
        project_id: str,
        structured_report: Dict[str, Any],
        report_markdown: str,
        run_id: str | None = None,
    ) -> Dict[str, Any]:
        """Insert a draft on an existing tenant transaction.

        The orchestration coordinator uses this seam so run, report, Snapshot,
        and final run metadata commit or roll back as one unit.
        """
        normalized_project_id = _required_text(project_id, "project_id")
        # Serialize version allocation without requiring UPDATE on the
        # read-only projects table. The transaction lock is released on
        # commit/rollback and only over-locks in the unlikely hash collision.
        connection.execute(
            """
            SELECT pg_advisory_xact_lock(
                hashtextextended(%s, 0)
            )
            """,
            (f"report-version:{self.tenant_id}:{normalized_project_id}",),
        )
        project_row = connection.execute(
            """
            SELECT project_id
            FROM realytics.projects
            WHERE tenant_id = %s AND project_id = %s
            """,
            (self.tenant_id, normalized_project_id),
        ).fetchone()
        if project_row is None:
            raise LookupError("Project not found in normalized PostgreSQL")
        version_row = connection.execute(
            """
            SELECT COALESCE(MAX(version), 0) + 1
            FROM realytics.reports
            WHERE tenant_id = %s AND project_id = %s
            """,
            (self.tenant_id, normalized_project_id),
        ).fetchone()
        version = int(_row_value(version_row, 0, "version"))
        record = _build_draft_report_record(
            project_id=normalized_project_id,
            version=version,
            structured_report=structured_report,
            report_markdown=report_markdown,
            run_id=run_id,
        )
        verify_report_snapshots(record, project_id=normalized_project_id, tenant_id=self.tenant_id)
        connection.execute(
            """
            INSERT INTO realytics.reports (
                tenant_id,
                project_id,
                report_id,
                version,
                created_at,
                payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                self.tenant_id,
                normalized_project_id,
                record["report_id"],
                version,
                record["created_at"],
                _json_payload(record),
            ),
        )
        return deepcopy(record)

    def list_metadata(self, project_id: str) -> List[Dict[str, Any]]:
        normalized_project_id = _required_text(project_id, "project_id")
        with self._transaction(self.tenant_id) as connection:
            rows = connection.execute(
                self._REPORT_SELECT
                + """
                WHERE report.tenant_id = %s
                  AND report.project_id = %s
                ORDER BY report.version, report.created_at, report.report_id
                """,
                (self.tenant_id, normalized_project_id),
            ).fetchall()
            return [self.metadata(self._report_from_join_row(row)) for row in rows]

    def get(self, project_id: str, report_id: str) -> Dict[str, Any] | None:
        normalized_project_id = _required_text(project_id, "project_id")
        normalized_report_id = _required_text(report_id, "report_id")
        with self._transaction(self.tenant_id) as connection:
            row = connection.execute(
                self._REPORT_SELECT
                + """
                WHERE report.tenant_id = %s
                  AND report.project_id = %s
                  AND report.report_id = %s
                """,
                (
                    self.tenant_id,
                    normalized_project_id,
                    normalized_report_id,
                ),
            ).fetchone()
            return self._report_from_join_row(row) if row is not None else None

    def add_export(
        self,
        report_record: Dict[str, Any],
        rendered: Dict[str, Any],
    ) -> Dict[str, Any]:
        project_id, report_id = self._record_identity(report_record)
        with self._transaction(self.tenant_id) as connection:
            current = self._locked_report(connection, project_id, report_id)
            if current is None:
                raise LookupError("Report not found")
            verify_report_snapshots(current, project_id=project_id, tenant_id=self.tenant_id)
            publication = self._select_publication(
                connection,
                project_id,
                report_id,
            )
            if publication is not None:
                self._assert_publication_content(current, publication)
                raise ReportPublicationConflict(
                    "Published reports are immutable; create exports before publication"
                )
            export_record = _build_export_record(current, rendered)
            if is_cash_flow_comparison_report(current):
                _verify_comparison_export(current, export_record)
            current.setdefault("exports", []).append(export_record)
            self._update_payload(connection, current)
            return export_record

    def publish(
        self,
        report_record: Dict[str, Any],
        *,
        published_by: str,
        published_at: str,
        decision_package: Dict[str, Any],
    ) -> Dict[str, Any]:
        if is_cash_flow_comparison_report(report_record) or has_cash_flow_basis(report_record):
            raise ReportPublicationConflict("현금흐름 비교 검토 초안은 최종 승인 보고서로 발행할 수 없습니다.")
        project_id, report_id = self._record_identity(report_record)
        actor = _required_text(published_by, "published_by")
        publication_time = _required_text(published_at, "published_at")
        if not isinstance(decision_package, Mapping):
            raise TypeError("decision_package must be a mapping")

        # The caller's sealed draft is the optimistic-concurrency token. If
        # another writer changed the report body after it was read, publication
        # fails before an immutable row is appended.
        expected_content_sha256 = _verified_report_content_sha256(report_record)
        with self._transaction(self.tenant_id) as connection:
            current = self._locked_report(connection, project_id, report_id)
            if current is None:
                raise LookupError("Report not found")
            current_content_sha256 = _verified_report_content_sha256(current)
            if current_content_sha256 != expected_content_sha256:
                raise ReportPublicationConflict(
                    "Report content changed during publication"
                )

            decision = deepcopy(dict(decision_package))
            decision_sha256 = _canonical_sha256(decision)
            idempotency_key = _canonical_sha256(
                {
                    "tenantId": self.tenant_id,
                    "projectId": project_id,
                    "reportId": report_id,
                    "reportVersion": int(current.get("version", 1)),
                    "contentSha256": current_content_sha256,
                    "decisionPackageSha256": decision_sha256,
                }
            )
            run_id = _required_text(
                decision.get("orchestrationRunId"),
                "decision_package.orchestrationRunId",
            )
            decision_project_id = _required_text(
                decision.get("projectId"),
                "decision_package.projectId",
            )
            if decision_project_id != project_id:
                raise ReportPublicationConflict(
                    "Decision package project does not match the report"
                )
            report_run_id = _required_text(
                current.get("run_id"),
                "report.run_id",
            )
            if report_run_id != run_id:
                raise ReportPublicationConflict(
                    "Decision package orchestration run does not match the report"
                )
            snapshot_id = _required_text(
                decision.get("projectSnapshotId"),
                "decision_package.projectSnapshotId",
            )
            snapshot_hash = _required_sha256(
                decision.get("projectSnapshotHash"),
                "decision_package.projectSnapshotHash",
            )

            existing = self._select_publication(
                connection,
                project_id,
                report_id,
            )
            if existing is None:
                inserted = connection.execute(
                    f"""
                    INSERT INTO realytics.report_publications (
                        tenant_id,
                        project_id,
                        report_id,
                        report_version,
                        orchestration_run_id,
                        project_snapshot_id,
                        project_snapshot_hash,
                        content_sha256,
                        decision_package_sha256,
                        idempotency_key,
                        decision_package_json,
                        published_by,
                        published_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s::jsonb, %s, %s
                    )
                    ON CONFLICT (tenant_id, project_id, report_id)
                    DO NOTHING
                    RETURNING {self._PUBLICATION_COLUMNS}
                    """,
                    (
                        self.tenant_id,
                        project_id,
                        report_id,
                        int(current.get("version", 1)),
                        run_id,
                        snapshot_id,
                        snapshot_hash,
                        current_content_sha256,
                        decision_sha256,
                        idempotency_key,
                        _json_payload(decision),
                        actor,
                        publication_time,
                    ),
                ).fetchone()
                existing = (
                    self._publication_from_row(inserted)
                    if inserted is not None
                    else self._select_publication(
                        connection,
                        project_id,
                        report_id,
                    )
                )
            if existing is None:  # pragma: no cover - database contract breach
                raise RuntimeError("Report publication INSERT returned no authority row")

            expected_identity = {
                "report_version": int(current.get("version", 1)),
                "orchestration_run_id": run_id,
                "project_snapshot_id": snapshot_id,
                "project_snapshot_hash": snapshot_hash,
                "content_sha256": current_content_sha256,
                "decision_package_sha256": decision_sha256,
                "idempotency_key": idempotency_key,
            }
            if any(existing.get(key) != value for key, value in expected_identity.items()):
                raise ReportPublicationConflict(
                    "Published report cannot be rebound to another content or decision package"
                )

            _apply_publication_manifest(
                current,
                self._publication_manifest(existing),
            )
            # Finality is projected from the immutable publication row. Do not
            # write a legacy ``status=published`` flag after INSERT: the
            # database rejects every mutation of a published report payload.
            return current

    def get_export(
        self,
        report_record: Dict[str, Any],
        export_id: str,
    ) -> Dict[str, Any] | None:
        return ReportRepository.get_export(self, report_record, export_id)

    metadata = staticmethod(ReportRepository.metadata)
    export_metadata = staticmethod(ReportRepository.export_metadata)

    def _locked_report(
        self,
        connection: Any,
        project_id: str,
        report_id: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT project_id, report_id, version, created_at, payload_json
            FROM realytics.reports
            WHERE tenant_id = %s AND project_id = %s AND report_id = %s
            FOR UPDATE
            """,
            (self.tenant_id, project_id, report_id),
        ).fetchone()
        if row is None:
            return None
        return self._report_from_base_row(row)

    def _select_publication(
        self,
        connection: Any,
        project_id: str,
        report_id: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            f"""
            SELECT {self._PUBLICATION_COLUMNS}
            FROM realytics.report_publications
            WHERE tenant_id = %s AND project_id = %s AND report_id = %s
            """,
            (self.tenant_id, project_id, report_id),
        ).fetchone()
        return self._publication_from_row(row) if row is not None else None

    def _update_payload(
        self,
        connection: Any,
        record: Mapping[str, Any],
    ) -> None:
        updated = connection.execute(
            """
            UPDATE realytics.reports
            SET payload_json = %s::jsonb
            WHERE tenant_id = %s AND project_id = %s AND report_id = %s
            RETURNING report_id
            """,
            (
                _json_payload(record),
                self.tenant_id,
                record["project_id"],
                record["report_id"],
            ),
        ).fetchone()
        if updated is None:  # pragma: no cover - row locked in same transaction
            raise ReportPublicationConflict("Report disappeared during update")

    def _report_from_join_row(self, row: Any) -> dict[str, Any]:
        record = self._report_from_base_row(row)
        if _row_value(row, 5, "report_version") is not None:
            publication = self._publication_from_row(row, offset=5)
            self._assert_publication_content(record, publication)
            _apply_publication_manifest(
                record,
                self._publication_manifest(publication),
            )
        verify_report_snapshots(record, tenant_id=self.tenant_id)
        return record

    @staticmethod
    def _assert_publication_content(
        record: Mapping[str, Any],
        publication: Mapping[str, Any],
    ) -> None:
        actual_sha256 = _verified_report_content_sha256(record)
        authority_sha256 = _required_sha256(
            publication.get("content_sha256"),
            "publication.content_sha256",
        )
        if actual_sha256 != authority_sha256:
            raise ReportPublicationConflict(
                "Published report content no longer matches its immutable seal"
            )

    def _report_from_base_row(self, row: Any) -> dict[str, Any]:
        project_id = str(_row_value(row, 0, "project_id"))
        report_id = str(_row_value(row, 1, "report_id"))
        version = int(_row_value(row, 2, "version"))
        created_at = _iso_value(_row_value(row, 3, "created_at"))
        record = _decode_json_object(
            _row_value(row, 4, "payload_json"),
            label="report payload",
        )
        # Normalized columns always override legacy JSON authority fields.
        record["project_id"] = project_id
        record["report_id"] = report_id
        record["version"] = version
        record["created_at"] = created_at
        record.setdefault("exports", [])
        return record

    @staticmethod
    def _publication_from_row(
        row: Any,
        *,
        offset: int = 0,
    ) -> dict[str, Any]:
        keys = (
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
        values = {
            key: _row_value(row, offset + index, key)
            for index, key in enumerate(keys)
        }
        values["report_version"] = int(values["report_version"])
        values["decision_package_json"] = _decode_json_object(
            values["decision_package_json"],
            label="decision package",
        )
        values["published_at"] = _iso_value(values["published_at"])
        values["created_at"] = _iso_value(values["created_at"])
        return values

    @staticmethod
    def _publication_manifest(publication: Mapping[str, Any]) -> dict[str, Any]:
        return _build_publication_manifest(
            published_by=str(publication["published_by"]),
            published_at=str(publication["published_at"]),
            content_sha256=str(publication["content_sha256"]),
            decision_package=dict(publication["decision_package_json"]),
            decision_package_sha256=str(
                publication["decision_package_sha256"]
            ),
            idempotency_key=str(publication["idempotency_key"]),
            repository="normalized_postgres",
        )

    @staticmethod
    def _record_identity(record: Mapping[str, Any]) -> tuple[str, str]:
        if not isinstance(record, Mapping):
            raise TypeError("report_record must be a mapping")
        return (
            _required_text(record.get("project_id"), "project_id"),
            _required_text(record.get("report_id"), "report_id"),
        )


def _build_draft_report_record(
    *,
    project_id: str,
    version: int,
    structured_report: Mapping[str, Any],
    report_markdown: str,
    run_id: str | None,
) -> Dict[str, Any]:
    report = deepcopy(dict(structured_report))
    content_sha256 = _report_content_sha256(report, report_markdown)
    quality_checks = report["qualityChecks"]
    record = {
        "report_id": str(uuid4()),
        "project_id": project_id,
        "run_id": run_id,
        "version": version,
        "template": report["template"],
        "title": report.get("title", "AI PF Review Report"),
        # Generation is always a draft. Only a separate publication record can
        # promote the report after the decision-package gates pass.
        "status": "draft",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            "section_count": quality_checks["actualSectionCount"],
            "expected_section_count": quality_checks[
                "expectedSectionCount"
            ],
            "complete": quality_checks["isComplete"],
            "applied_template_id": (
                report.get("appliedTemplate") or {}
            ).get("templateId"),
            "applied_template_version": (
                report.get("appliedTemplate") or {}
            ).get("version"),
            "template_unmapped_section_count": quality_checks.get(
                "templateUnmappedSectionCount",
                0,
            ),
            "paragraph_evidence_link_count": (
                report.get("paragraphEvidenceLinks") or {}
            ).get("sectionCount", 0),
            "paragraph_evidence_linked_record_count": (
                report.get("paragraphEvidenceLinks") or {}
            ).get("linkedRecordCount", 0),
            "paragraph_evidence_source_locator_ready_section_count": (
                report.get("paragraphEvidenceLinks") or {}
            ).get("sourceLocatorReadySectionCount", 0),
            "report_eligible": False,
            "usage_restriction": "analysis_only_not_final_approval",
            "content_sha256": content_sha256,
        },
        "report": report,
        "report_markdown": report_markdown,
        "exports": [],
    }
    if is_cash_flow_comparison_report(record):
        record["metadata"].update({"report_type": COMPARISON_REPORT_TYPE,
            "comparison_snapshot_hash": report.get("cashFlowComparisonSnapshotHash")})
    if "cashFlowCalculationSnapshot" in report:
        record["metadata"]["cash_flow_snapshot_hash"] = report["cashFlowCalculationSnapshotHash"]
    verify_report_snapshots(record, project_id=project_id)
    return record


def _build_export_record(
    report_record: Mapping[str, Any],
    rendered: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "export_id": str(uuid4()),
        "report_id": report_record["report_id"],
        "project_id": report_record["project_id"],
        "version": report_record.get("version", 1),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "report_status": report_record.get("status", "draft"),
        "report_content_sha256": (
            report_record.get("metadata") or {}
        ).get("content_sha256"),
        "usage_restriction": (
            None
            if report_record.get("status") == "published"
            else "analysis_only_not_final_approval"
        ),
        **deepcopy(dict(rendered)),
    }


def _build_publication_manifest(
    *,
    published_by: str,
    published_at: str,
    content_sha256: str,
    decision_package: Mapping[str, Any],
    decision_package_sha256: str | None = None,
    idempotency_key: str | None = None,
    repository: str | None = None,
) -> Dict[str, Any]:
    decision = deepcopy(dict(decision_package))
    manifest: Dict[str, Any] = {
        "schemaVersion": "realytics-report-publication-v1",
        "publishedBy": published_by,
        "publishedAt": published_at,
        "contentSha256": content_sha256,
        "decisionPackageSha256": (
            decision_package_sha256 or _canonical_sha256(decision)
        ),
        "decisionPackage": decision,
    }
    if idempotency_key:
        manifest["idempotencyKey"] = idempotency_key
    if repository:
        manifest["repository"] = repository
    return manifest


def _apply_publication_manifest(
    report_record: Dict[str, Any],
    publication: Mapping[str, Any],
) -> None:
    report_record["status"] = "published"
    report_record["published_at"] = publication["publishedAt"]
    report_record["published_by"] = publication["publishedBy"]
    report_record["publication"] = deepcopy(dict(publication))
    report_record.setdefault("metadata", {}).update(
        {
            "report_eligible": True,
            "usage_restriction": None,
            "content_sha256": publication["contentSha256"],
        }
    )


def _verified_report_content_sha256(
    report_record: Mapping[str, Any],
) -> str:
    content_sha256 = _report_content_sha256(
        dict(report_record.get("report") or {}),
        str(report_record.get("report_markdown") or ""),
    )
    stored_sha256 = (report_record.get("metadata") or {}).get(
        "content_sha256"
    )
    if stored_sha256 and stored_sha256 != content_sha256:
        raise ReportPublicationConflict(
            "Report content hash changed after draft creation"
        )
    return content_sha256


def _canonical_sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _json_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _decode_json_object(value: Any, *, label: str) -> Dict[str, Any]:
    decoded = value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ReportPayloadDataError(f"{label} is not valid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise ReportPayloadDataError(f"{label} must be a JSON object")
    return deepcopy(dict(decoded))


def _row_value(row: Any, index: int, key: str) -> Any:
    if row is None:
        raise ReportPayloadDataError(f"PostgreSQL row is missing {key}")
    if isinstance(row, Mapping):
        return row.get(key)
    return row[index]


def _iso_value(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _required_text(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _required_sha256(value: Any, label: str) -> str:
    normalized = _required_text(value, label)
    digest = normalized.removeprefix("sha256:")
    if (
        not normalized.startswith("sha256:")
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError(f"{label} must be a canonical lowercase SHA-256")
    return normalized


def _report_content_sha256(
    structured_report: Dict[str, Any],
    report_markdown: str,
) -> str:
    canonical = json.dumps(
        {
            "report": structured_report,
            "reportMarkdown": report_markdown,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"
