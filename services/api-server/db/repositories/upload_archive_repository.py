from __future__ import annotations

import hashlib
import shutil
import string
from pathlib import Path
from typing import Any, Dict


class UploadArchiveRepository:
    """Repository wrapper for source document binary archive operations."""

    def __init__(self, api_server_root: Path | None = None, archive_root: Path | None = None):
        self.api_server_root = (api_server_root or Path(__file__).resolve().parents[2]).resolve()
        self.archive_root = (archive_root or self.api_server_root / "data" / "runtime" / "uploads").resolve()

    def archive_upload(
        self,
        project_id: str,
        content: bytes,
        original_file_name: str | None,
        content_type: str | None,
    ) -> Dict[str, Any]:
        archive_id = self.hash_content(content)
        suffix = self.safe_suffix(original_file_name)
        project_archive_dir = self.project_archive_dir(project_id)
        project_archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = project_archive_dir / f"{archive_id}{suffix}"
        if not archive_path.exists():
            archive_path.write_bytes(content)

        return {
            "archiveId": archive_id,
            "archiveStored": True,
            "archivePath": self.relative_archive_path(archive_path),
            "archiveHashAlgorithm": "sha256",
            "archiveIntegrityStatus": self.verify_file_hash(archive_path, archive_id),
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

        archive_path = self.project_archive_dir(project_id) / f"{archive_id}{self.safe_suffix(document.get('originalFileName'))}"
        if not archive_path.exists():
            return None

        integrity_status = self.verify_file_hash(archive_path, archive_id)
        if integrity_status != "verified":
            return None

        return {
            "path": archive_path,
            "archiveId": archive_id,
            "archiveHashAlgorithm": "sha256",
            "archiveIntegrityStatus": integrity_status,
            "fileSizeBytes": archive_path.stat().st_size,
            "mediaType": document.get("contentType") or "application/octet-stream",
            "filename": document.get("originalFileName") or f"{archive_id}{archive_path.suffix}",
            "downloadUrl": f"/projects/{project_id}/source-documents/{archive_id}/download",
        }

    def delete_project_archive(self, project_id: str) -> bool:
        archive_dir = self.project_archive_dir(project_id)
        if not archive_dir.exists():
            return False
        shutil.rmtree(archive_dir)
        return True

    def project_archive_dir(self, project_id: str) -> Path:
        archive_dir = (self.archive_root / str(project_id)).resolve()
        if self.archive_root != archive_dir and self.archive_root not in archive_dir.parents:
            raise ValueError("Invalid project archive path")
        return archive_dir

    def relative_archive_path(self, archive_path: Path) -> str:
        resolved_path = archive_path.resolve()
        if self.api_server_root != resolved_path and self.api_server_root not in resolved_path.parents:
            raise ValueError("Invalid archive path")
        return str(resolved_path.relative_to(self.api_server_root)).replace("\\", "/")

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
