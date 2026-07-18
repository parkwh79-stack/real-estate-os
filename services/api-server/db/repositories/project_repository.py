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
        project_snapshots_store: Dict[str, Any],
        persist: Callable[[], None],
        allocate_project_id: Callable[[], str],
        persist_project: Callable[[str], None] | None = None,
    ):
        self.projects_store = projects_store
        self.reports_store = reports_store
        self.orchestrations_store = orchestrations_store
        self.market_evidence_store = market_evidence_store
        self.project_snapshots_store = project_snapshots_store
        self.persist = persist
        self.allocate_project_id = allocate_project_id
        # Optional single-row persistence (fast path); falls back to full persist().
        self.persist_project = persist_project

    def _persist_one(self, project_id: str) -> None:
        if self.persist_project is not None:
            self.persist_project(project_id)
        else:
            self.persist()

    def list(self) -> list[Dict[str, Any]]:
        # Historical/imported records may predate the canonical project
        # envelope.  Keep the stored evidence untouched while returning a
        # stable API projection so clients never have to guess whether core
        # identity fields exist.
        return [self._contract_projection(record) for record in self.projects_store.values()]

    def exists(self, project_id: str) -> bool:
        return project_id in self.projects_store

    def get(self, project_id: str) -> Dict[str, Any] | None:
        return self.projects_store.get(project_id)

    def create(self, project_data: Dict[str, Any]) -> Dict[str, Any]:
        project_id = self.allocate_project_id()
        record = self.normalize(project_id, project_data)
        self.projects_store[project_id] = record
        self._persist_one(project_id)
        return record

    def update(self, project_id: str, project_data: Dict[str, Any]) -> Dict[str, Any] | None:
        existing = self.projects_store.get(project_id)
        if not existing:
            return None
        self.projects_store[project_id] = self.normalize(project_id, deep_merge(existing, project_data))
        self._persist_one(project_id)
        return self.projects_store[project_id]

    def apply_parsed_data(self, project_id: str, parsed_data: Dict[str, Any]) -> Dict[str, Any] | None:
        project = self.projects_store.get(project_id)
        if not project:
            return None
        incoming = dict(parsed_data or {})
        existing_project = project.get("project") if isinstance(project.get("project"), dict) else {}
        incoming_project = incoming.get("project") if isinstance(incoming.get("project"), dict) else {}
        merged_project = deep_merge(existing_project, incoming_project)
        merged_project["id"] = project_id
        if not merged_project.get("name"):
            merged_project["name"] = existing_project.get("name") or project.get("projectName") or f"Project {project_id}"
        incoming["project"] = merged_project
        merged = deep_merge(project, incoming)
        merged["id"] = project_id
        merged.setdefault("_meta", {})
        merged["_meta"]["updated_at"] = self._now()
        self.projects_store[project_id] = merged
        self._persist_one(project_id)
        return merged

    def delete(self, project_id: str) -> Dict[str, Any] | None:
        deleted = self.projects_store.pop(project_id, None)
        if deleted is None:
            return None
        self.reports_store.pop(project_id, None)
        self.orchestrations_store.pop(project_id, None)
        self.market_evidence_store.pop(project_id, None)
        self.project_snapshots_store.pop(project_id, None)
        self._persist_one(project_id)
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
        project["type"] = project.get("type") or record.get("type") or "unknown"
        project.setdefault("status", "draft")
        record["project"] = project
        record["id"] = project_id
        record.setdefault("_meta", {})
        record["_meta"].setdefault("created_at", now)
        record["_meta"]["updated_at"] = now
        return record

    @staticmethod
    def _contract_projection(source: Dict[str, Any]) -> Dict[str, Any]:
        record = dict(source or {})
        project = dict(record.get("project") or {})
        project_id = str(project.get("id") or record.get("id") or "")
        project["id"] = project_id
        project["name"] = project.get("name") or record.get("name") or f"Project {project_id}"
        project["type"] = project.get("type") or record.get("type") or "unknown"
        project["status"] = project.get("status") or record.get("status") or "draft"
        record["project"] = project
        if project_id:
            record["id"] = project_id
        return record

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
