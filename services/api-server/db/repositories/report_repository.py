from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List
from uuid import uuid4


class ReportRepository:
    """Repository wrapper for saved report records."""

    def __init__(self, reports_store: Dict[str, List[Dict[str, Any]]], persist: Callable[[], None]):
        self.reports_store = reports_store
        self.persist = persist

    def create(
        self,
        project_id: str,
        structured_report: Dict[str, Any],
        report_markdown: str,
        run_id: str | None = None,
    ) -> Dict[str, Any]:
        version = len(self.reports_store.get(project_id, [])) + 1
        record = {
            "report_id": str(uuid4()),
            "project_id": project_id,
            "run_id": run_id,
            "version": version,
            "template": structured_report["template"],
            "title": structured_report.get("title", "AI PF Review Report"),
            "status": structured_report.get("status", "draft"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "section_count": structured_report["qualityChecks"]["actualSectionCount"],
                "expected_section_count": structured_report["qualityChecks"]["expectedSectionCount"],
                "complete": structured_report["qualityChecks"]["isComplete"],
                "applied_template_id": (structured_report.get("appliedTemplate") or {}).get("templateId"),
                "applied_template_version": (structured_report.get("appliedTemplate") or {}).get("version"),
                "template_unmapped_section_count": structured_report.get("qualityChecks", {}).get("templateUnmappedSectionCount", 0),
            },
            "report": structured_report,
            "report_markdown": report_markdown,
            "exports": [],
        }
        self.reports_store.setdefault(project_id, []).append(record)
        self.persist()
        return record

    def list_metadata(self, project_id: str) -> List[Dict[str, Any]]:
        return [self.metadata(record) for record in self.reports_store.get(project_id, [])]

    def get(self, project_id: str, report_id: str) -> Dict[str, Any] | None:
        for record in self.reports_store.get(project_id, []):
            if record.get("report_id") == report_id:
                record.setdefault("exports", [])
                record.setdefault("version", 1)
                return record
        return None

    def add_export(self, report_record: Dict[str, Any], rendered: Dict[str, Any]) -> Dict[str, Any]:
        export_record = {
            "export_id": str(uuid4()),
            "report_id": report_record["report_id"],
            "project_id": report_record["project_id"],
            "version": report_record.get("version", 1),
            "created_at": datetime.now(timezone.utc).isoformat(),
            **rendered,
        }
        report_record.setdefault("exports", []).append(export_record)
        self.persist()
        return export_record

    def get_export(self, report_record: Dict[str, Any], export_id: str) -> Dict[str, Any] | None:
        for record in report_record.get("exports", []):
            if record.get("export_id") == export_id:
                return record
        return None

    @staticmethod
    def metadata(record: Dict[str, Any]) -> Dict[str, Any]:
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
        }

    @staticmethod
    def export_metadata(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value
            for key, value in record.items()
            if key != "content"
        }
