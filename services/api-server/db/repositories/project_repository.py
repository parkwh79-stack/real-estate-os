from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict

from use_cases.common import deep_merge


class ProjectRepository:
    """Repository wrapper for project records and project-scoped cleanup."""

    def __init__(
        self,
        projects_store: Dict[str, Dict[str, Any]],
        reports_store: Dict[str, Any],
        orchestrations_store: Dict[str, Any],
        market_evidence_store: Dict[str, Any],
        persist: Callable[[], None],
        allocate_project_id: Callable[[], str],
    ):
        self.projects_store = projects_store
        self.reports_store = reports_store
        self.orchestrations_store = orchestrations_store
        self.market_evidence_store = market_evidence_store
        self.persist = persist
        self.allocate_project_id = allocate_project_id

    def list(self) -> list[Dict[str, Any]]:
        return list(self.projects_store.values())

    def exists(self, project_id: str) -> bool:
        return project_id in self.projects_store

    def get(self, project_id: str) -> Dict[str, Any] | None:
        return self.projects_store.get(project_id)

    def create(self, project_data: Dict[str, Any]) -> Dict[str, Any]:
        project_id = self.allocate_project_id()
        record = self.normalize(project_id, project_data)
        self.projects_store[project_id] = record
        self.persist()
        return record

    def update(self, project_id: str, project_data: Dict[str, Any]) -> Dict[str, Any] | None:
        existing = self.projects_store.get(project_id)
        if not existing:
            return None
        self.projects_store[project_id] = self.normalize(project_id, deep_merge(existing, project_data))
        self.persist()
        return self.projects_store[project_id]

    def apply_parsed_data(self, project_id: str, parsed_data: Dict[str, Any]) -> Dict[str, Any] | None:
        project = self.projects_store.get(project_id)
        if not project:
            return None
        if "project" not in parsed_data:
            parsed_data["project"] = {}
        parsed_data["project"]["id"] = project_id
        project.update(parsed_data)
        project.setdefault("_meta", {})
        project["_meta"]["updated_at"] = self._now()
        self.persist()
        return project

    def delete(self, project_id: str) -> Dict[str, Any] | None:
        deleted = self.projects_store.pop(project_id, None)
        if deleted is None:
            return None
        self.reports_store.pop(project_id, None)
        self.orchestrations_store.pop(project_id, None)
        self.market_evidence_store.pop(project_id, None)
        self.persist()
        return deleted

    def normalize(self, project_id: str, project_data: Dict[str, Any]) -> Dict[str, Any]:
        now = self._now()
        record = dict(project_data)
        project = dict(record.get("project", {}))
        if not project:
            project = {
                key: record.pop(key)
                for key in list(record.keys())
                if key in {"name", "type", "status", "review_date"}
            }
        project["id"] = project_id
        project.setdefault("name", record.get("name", f"Project {project_id}"))
        project.setdefault("status", "draft")
        record["project"] = project
        record["id"] = project_id
        record.setdefault("_meta", {})
        record["_meta"].setdefault("created_at", now)
        record["_meta"]["updated_at"] = now
        return record

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
