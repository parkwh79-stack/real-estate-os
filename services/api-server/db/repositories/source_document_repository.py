from __future__ import annotations

from typing import Any, Callable, Dict, List


class SourceDocumentRepository:
    """Repository wrapper for source document metadata attached to projects."""

    def __init__(
        self,
        projects_store: Dict[str, Dict[str, Any]],
        persist: Callable[[], None],
        persist_project: Callable[[str], None] | None = None,
    ):
        self.projects_store = projects_store
        self.persist = persist
        self.persist_project = persist_project

    def list(self, project_id: str) -> List[Dict[str, Any]]:
        project = self.projects_store.get(project_id, {})
        documents = list(project.get("source_documents") or [])
        upload_meta = project.get("_meta", {}).get("upload")
        if isinstance(upload_meta, dict):
            upload_archive_id = upload_meta.get("archiveId") or upload_meta.get("originalFileHash")
            already_listed = any(
                (document.get("archiveId") or document.get("originalFileHash")) == upload_archive_id
                for document in documents
            )
            if not already_listed:
                documents.append(upload_meta)
        return documents

    def save_upload_metadata(self, project_id: str, upload_meta: Dict[str, Any]) -> Dict[str, Any] | None:
        project = self.projects_store.get(project_id)
        if not project:
            return None
        project.setdefault("_meta", {})
        project["_meta"]["upload"] = upload_meta
        documents = project.setdefault("source_documents", [])
        upload_archive_id = upload_meta.get("archiveId") or upload_meta.get("originalFileHash")
        existing_index = next(
            (
                index
                for index, document in enumerate(documents)
                if (document.get("archiveId") or document.get("originalFileHash")) == upload_archive_id
            ),
            None,
        )
        if existing_index is None:
            documents.append(upload_meta)
        else:
            # The archive identifier is content-addressed. A later validation
            # of the same bytes must enrich the existing metadata record rather
            # than silently retain an earlier record without validation facts.
            documents[existing_index] = {**documents[existing_index], **upload_meta}
        if self.persist_project is not None:
            self.persist_project(project_id)
        else:
            self.persist()
        return upload_meta

    def find_by_archive_id(self, project_id: str, archive_id: str) -> Dict[str, Any] | None:
        return next(
            (
                item
                for item in self.list(project_id)
                if (item.get("archiveId") or item.get("originalFileHash")) == archive_id
            ),
            None,
        )
