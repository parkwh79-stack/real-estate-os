from __future__ import annotations

import hashlib
import string
from pathlib import Path
from typing import Any, Dict

from db.object_storage import LocalObjectStorage, ObjectStorage, get_object_storage, sha256_bytes


class UploadArchiveRepository:
    """Repository wrapper for source document binary archive operations."""

    def __init__(
        self,
        api_server_root: Path | None = None,
        archive_root: Path | None = None,
        object_storage: ObjectStorage | None = None,
    ):
        self.api_server_root = (api_server_root or Path(__file__).resolve().parents[2]).resolve()
        self.archive_root = (archive_root or self.api_server_root / "data" / "runtime" / "uploads").resolve()
        self.object_storage = object_storage or get_object_storage(self.archive_root)

    def archive_upload(
        self,
        project_id: str,
        content: bytes,
        original_file_name: str | None,
        content_type: str | None,
    ) -> Dict[str, Any]:
        archive_id = self.hash_content(content)
        suffix = self.safe_suffix(original_file_name)
        object_key = self.object_key(project_id, archive_id, suffix)
        stored = self.object_storage.put(object_key, content, content_type=content_type)
        local_path = self.object_storage.local_path(object_key)
        archive_path = self.relative_archive_path(local_path) if local_path else object_key

        return {
            "archiveId": archive_id,
            "archiveStored": True,
            "archivePath": archive_path,
            "archiveBackend": self.object_storage.backend,
            "archiveObjectKey": object_key,
            "archiveHashAlgorithm": "sha256",
            "archiveIntegrityStatus": "verified" if stored.get("sha256") == archive_id else "hash_mismatch",
            "fileSizeBytes": stored.get("size", len(content)),
            "downloadUrl": f"/projects/{project_id}/source-documents/{archive_id}/download",
            "contentType": content_type,
        }

    def resolve_download_metadata(
        self,
        project_id: str,
        document: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        archive_id = document.get("archiveId") or document.get("originalFileHash")
        if not self.is_valid_archive_id(archive_id):
            return None

        object_key = document.get("archiveObjectKey") or self.object_key(
            project_id,
            archive_id,
            self.safe_suffix(document.get("originalFileName")),
        )
        object_key = str(object_key)
        project_prefix = self.project_prefix(project_id) + "/"
        if not object_key.startswith(project_prefix):
            return None
        scoped_name = object_key[len(project_prefix) :]
        if "/" in scoped_name or "\\" in scoped_name or not scoped_name.startswith(archive_id):
            return None
        if not self.object_storage.exists(object_key):
            return None
        content = self.object_storage.get(object_key)
        integrity_status = "verified" if sha256_bytes(content) == archive_id else "hash_mismatch"
        if integrity_status != "verified":
            return None

        archive_path = self.object_storage.local_path(object_key)

        return {
            "path": archive_path,
            "content": None if archive_path else content,
            "archiveBackend": self.object_storage.backend,
            "archiveObjectKey": object_key,
            "archiveId": archive_id,
            "archiveHashAlgorithm": "sha256",
            "archiveIntegrityStatus": integrity_status,
            "fileSizeBytes": len(content),
            "mediaType": document.get("contentType") or "application/octet-stream",
            "filename": document.get("originalFileName") or f"{archive_id}{self.safe_suffix(document.get('originalFileName'))}",
            "downloadUrl": f"/projects/{project_id}/source-documents/{archive_id}/download",
        }

    def delete_project_archive(self, project_id: str) -> bool:
        return self.object_storage.delete_prefix(self.project_prefix(project_id))

    def project_archive_dir(self, project_id: str) -> Path:
        if not isinstance(self.object_storage, LocalObjectStorage):
            raise RuntimeError("Remote object storage does not expose a local archive directory")
        archive_dir = (self.archive_root / str(project_id)).resolve()
        if self.archive_root != archive_dir and self.archive_root not in archive_dir.parents:
            raise ValueError("Invalid project archive path")
        return archive_dir

    def relative_archive_path(self, archive_path: Path) -> str:
        resolved_path = archive_path.resolve()
        if self.api_server_root != resolved_path and self.api_server_root not in resolved_path.parents:
            raise ValueError("Invalid archive path")
        return str(resolved_path.relative_to(self.api_server_root)).replace("\\", "/")

    @classmethod
    def project_prefix(cls, project_id: str) -> str:
        value = str(project_id or "").strip()
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("Invalid project id for object storage")
        return value

    @classmethod
    def object_key(cls, project_id: str, archive_id: str, suffix: str) -> str:
        return f"{cls.project_prefix(project_id)}/{archive_id}{suffix}"

    @staticmethod
    def safe_suffix(filename: str | None) -> str:
        suffix = Path(filename or "").suffix.lower()
        if not suffix or len(suffix) > 12:
            return ".bin"
        return suffix

    @staticmethod
    def hash_content(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def is_valid_archive_id(value: Any) -> bool:
        return bool(
            isinstance(value, str)
            and len(value) == 64
            and all(char in string.hexdigits for char in value)
        )

    @staticmethod
    def verify_file_hash(path: Path, expected_sha256: str) -> str:
        if not path.exists():
            return "missing"
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            return "hash_mismatch"
        return "verified"
