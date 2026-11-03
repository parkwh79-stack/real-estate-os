from __future__ import annotations

import hashlib
import os
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from db.malware_scanner import MalwareScanner, get_malware_scanner
from core.runtime_paths import runtime_data_dir
from db.object_storage import (
    LocalObjectStorage,
    ObjectStorage,
    get_object_storage,
    sha256_bytes,
)


class UploadRejectedError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        scan_status: str | None = None,
        archive_id: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.scan_status = scan_status
        self.archive_id = archive_id


class UploadArchiveRepository:
    """Repository wrapper for source document binary archive operations."""

    def __init__(
        self,
        api_server_root: Path | None = None,
        archive_root: Path | None = None,
        object_storage: ObjectStorage | None = None,
        malware_scanner: MalwareScanner | None = None,
    ):
        self.api_server_root = (api_server_root or Path(__file__).resolve().parents[2]).resolve()
        default_runtime = self.api_server_root / "data" / "runtime" if api_server_root else runtime_data_dir()
        self.archive_root = (archive_root or default_runtime / "uploads").resolve()
        self.object_storage = object_storage or get_object_storage(self.archive_root)
        self.malware_scanner = malware_scanner or get_malware_scanner()

    def archive_upload(
        self,
        project_id: str,
        content: bytes,
        original_file_name: str | None,
        content_type: str | None,
        tenant_id: str | None = None,
    ) -> Dict[str, Any]:
        archive_id = self.hash_content(content)
        suffix = self.safe_suffix(original_file_name)
        tenant_scope = self.tenant_scope(tenant_id)
        quarantine_key = self.object_key(
            project_id,
            archive_id,
            suffix,
            tenant_id=tenant_scope,
            stage="quarantine",
        )
        object_key = self.object_key(
            project_id,
            archive_id,
            suffix,
            tenant_id=tenant_scope,
            stage="active",
        )
        base_metadata = {
            "sha256": archive_id,
            "tenant-id": tenant_scope,
            "project-id": self.project_component(project_id),
        }
        quarantined = self.object_storage.put(
            quarantine_key,
            content,
            content_type=content_type,
            metadata={**base_metadata, "lifecycle-status": "quarantine"},
        )
        if quarantined.get("sha256") != archive_id:
            raise UploadRejectedError(
                "quarantine_hash_mismatch",
                archive_id=archive_id,
            )
        try:
            scan = self.malware_scanner.scan(
                content,
                filename=original_file_name,
                content_type=content_type,
            )
        except Exception as exc:
            raise UploadRejectedError(
                "malware_scan_failed",
                scan_status="error",
                archive_id=archive_id,
            ) from exc
        if scan.status == "infected":
            raise UploadRejectedError(
                "malware_detected",
                scan_status="infected",
                archive_id=archive_id,
            )
        if scan.status not in {"clean", "skipped"}:
            raise UploadRejectedError(
                "malware_scan_failed",
                scan_status=scan.status,
                archive_id=archive_id,
            )
        if scan.status == "skipped" and self.hardened_storage_required():
            raise UploadRejectedError(
                "malware_scan_required",
                scan_status="skipped",
                archive_id=archive_id,
            )
        retention_until = None
        retention_mode = None
        if self.object_storage.backend in {"s3", "gcs"}:
            if getattr(self.object_storage, "retention_days", None):
                retention_until = datetime.now(timezone.utc) + timedelta(
                    days=self.object_storage.retention_days
                )
            retention_mode = getattr(self.object_storage, "retention_mode", None)
        stored = self.object_storage.put(
            object_key,
            content,
            content_type=content_type,
            metadata={
                **base_metadata,
                "lifecycle-status": "active",
                "malware-scan-status": scan.status,
                "malware-scan-engine": scan.engine,
            },
            retention_until=retention_until,
            retention_mode=retention_mode,
        )
        if stored.get("sha256") != archive_id:
            raise UploadRejectedError(
                "active_archive_hash_mismatch",
                archive_id=archive_id,
            )
        quarantine_deleted = self.object_storage.delete(quarantine_key)
        local_path = self.object_storage.local_path(object_key)
        archive_path = self.relative_archive_path(local_path) if local_path else object_key

        return {
            "archiveId": archive_id,
            "archiveStored": True,
            "archivePath": archive_path,
            "archiveBackend": self.object_storage.backend,
            "archiveObjectKey": object_key,
            "archiveTenantId": tenant_scope,
            "archiveHashAlgorithm": "sha256",
            "archiveIntegrityStatus": "verified" if stored.get("sha256") == archive_id else "hash_mismatch",
            "fileSizeBytes": stored.get("size", len(content)),
            "downloadUrl": f"/projects/{project_id}/source-documents/{archive_id}/download",
            "contentType": content_type,
            "storageLifecycleStatus": "active",
            "quarantineObjectKey": quarantine_key,
            "quarantineCleanupStatus": (
                "deleted" if quarantine_deleted else "not_found_or_retained"
            ),
            "malwareScanStatus": scan.status,
            "malwareScanEngine": scan.engine,
            "malwareScannedAt": scan.scanned_at.isoformat(),
            "malwareSignature": scan.signature,
            "reportEligible": scan.status == "clean",
            "retentionMode": stored.get("retentionMode"),
            "retentionUntil": stored.get("retentionUntil"),
        }

    def resolve_download_metadata(
        self,
        project_id: str,
        document: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        binding = self._resolve_object_binding(project_id, document)
        if binding is None:
            return None
        archive_id, archive_tenant, object_key = binding
        if self.hardened_storage_required() and not archive_tenant:
            return None
        if not self.object_storage.exists(object_key):
            return None
        if self.hardened_storage_required() and (
            document.get("storageLifecycleStatus") != "active"
            or document.get("malwareScanStatus") != "clean"
        ):
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
            "archiveTenantId": archive_tenant,
            "archiveId": archive_id,
            "archiveHashAlgorithm": "sha256",
            "archiveIntegrityStatus": integrity_status,
            "fileSizeBytes": len(content),
            "mediaType": document.get("contentType") or "application/octet-stream",
            "filename": document.get("originalFileName") or f"{archive_id}{self.safe_suffix(document.get('originalFileName'))}",
            "downloadUrl": f"/projects/{project_id}/source-documents/{archive_id}/download",
        }

    def resolve_view_metadata(
        self,
        project_id: str,
        document: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        """Resolve an immutable object binding without downloading its body."""

        binding = self._resolve_object_binding(project_id, document)
        if binding is None:
            return None
        archive_id, archive_tenant, object_key = binding
        if self.hardened_storage_required() and not archive_tenant:
            return None
        if self.hardened_storage_required() and self.object_storage.backend not in {"s3", "gcs"}:
            return None
        if self.hardened_storage_required() and (
            document.get("storageLifecycleStatus") != "active"
            or document.get("malwareScanStatus") != "clean"
        ):
            return None
        try:
            head = self.object_storage.head(object_key)
        except Exception:
            return None
        try:
            size = int(head.get("size") or 0)
        except (TypeError, ValueError):
            return None
        if size < 0:
            return None
        expected_size = document.get("fileSizeBytes")
        if expected_size is not None:
            try:
                if int(expected_size) != size:
                    return None
            except (TypeError, ValueError):
                return None
        metadata = {
            str(name).lower(): str(value)
            for name, value in dict(head.get("metadata") or {}).items()
        }
        if self.object_storage.backend in {"s3", "gcs"}:
            if metadata.get("sha256", "").lower() != str(archive_id).lower():
                return None
            if archive_tenant and metadata.get("tenant-id") != str(archive_tenant):
                return None
            if metadata.get("project-id") != self.project_component(project_id):
                return None
            if metadata.get("lifecycle-status") != "active":
                return None
            if self.hardened_storage_required() and metadata.get("malware-scan-status") != "clean":
                return None
            if self.hardened_storage_required() and (
                not head.get("etag")
                or not head.get("versionId")
                or not head.get("objectLockMode")
                or not head.get("objectLockRetainUntil")
                or not head.get("serverSideEncryption")
            ):
                return None
        elif document.get("archiveIntegrityStatus") not in {None, "verified"}:
            return None

        return {
            "document": document,
            "archiveBackend": self.object_storage.backend,
            "archiveObjectKey": object_key,
            "archiveTenantId": archive_tenant,
            "archiveId": archive_id,
            "archiveHashAlgorithm": "sha256",
            "archiveIntegrityStatus": "verified_metadata_binding",
            "archiveIntegrityBasis": (
                f"{self.object_storage.backend}_head_sha256_metadata_version_bound"
                if self.object_storage.backend in {"s3", "gcs"}
                else "local_content_addressed_path_size_bound"
            ),
            "archiveETag": head.get("etag"),
            "archiveVersionId": head.get("versionId"),
            "fileSizeBytes": size,
            "mediaType": document.get("contentType") or head.get("contentType") or "application/octet-stream",
            "filename": document.get("originalFileName") or f"{archive_id}{self.safe_suffix(document.get('originalFileName'))}",
            "viewUrl": f"/projects/{project_id}/source-documents/{archive_id}/content",
        }

    def read_view_range(
        self,
        view_metadata: Dict[str, Any],
        start: int,
        end: int,
    ) -> bytes:
        size = int(view_metadata.get("fileSizeBytes") or 0)
        if size <= 0 or start < 0 or end < start or end >= size:
            raise ValueError("Invalid source-document byte range")
        return self.object_storage.get_range(
            str(view_metadata["archiveObjectKey"]),
            start,
            end,
            version_id=(str(view_metadata["archiveVersionId"]) if view_metadata.get("archiveVersionId") else None),
            if_match=(str(view_metadata["archiveETag"]) if view_metadata.get("archiveETag") else None),
        )

    def _resolve_object_binding(
        self,
        project_id: str,
        document: Dict[str, Any],
    ) -> tuple[str, Any, str] | None:
        archive_id = document.get("archiveId") or document.get("originalFileHash")
        if not self.is_valid_archive_id(archive_id):
            return None
        archive_id = str(archive_id)
        archive_tenant = document.get("archiveTenantId")
        object_key = document.get("archiveObjectKey")
        if not object_key:
            object_key = (
                self.object_key(
                    project_id,
                    archive_id,
                    self.safe_suffix(document.get("originalFileName")),
                    tenant_id=str(archive_tenant) if archive_tenant else None,
                    stage="active",
                )
                if archive_tenant
                else self.legacy_object_key(
                    project_id,
                    archive_id,
                    self.safe_suffix(document.get("originalFileName")),
                )
            )
        object_key = str(object_key)
        project_prefix = (
            self.project_prefix(
                project_id,
                tenant_id=str(archive_tenant),
                stage="active",
            )
            if archive_tenant
            else self.legacy_project_prefix(project_id)
        ) + "/"
        if not object_key.startswith(project_prefix):
            return None
        scoped_name = object_key[len(project_prefix) :]
        expected_name = f"{archive_id}{self.safe_suffix(document.get('originalFileName'))}"
        if "/" in scoped_name or "\\" in scoped_name or scoped_name != expected_name:
            return None
        return archive_id, archive_tenant, object_key

    def delete_project_archive(
        self,
        project_id: str,
        tenant_id: str | None = None,
    ) -> bool:
        return self.object_storage.delete_prefix(
            self.project_prefix(project_id, tenant_id=tenant_id)
        )

    def project_archive_dir(
        self,
        project_id: str,
        tenant_id: str | None = None,
    ) -> Path:
        if not isinstance(self.object_storage, LocalObjectStorage):
            raise RuntimeError("Remote object storage does not expose a local archive directory")
        return self.object_storage._resolve(
            self.project_prefix(project_id, tenant_id=tenant_id)
        )

    def relative_archive_path(self, archive_path: Path) -> str:
        resolved_path = archive_path.resolve()
        if self.api_server_root != resolved_path and self.api_server_root not in resolved_path.parents:
            # Configured local runtimes (notably tests) may live outside source.
            # Keep metadata relative; downloads use the verified archiveObjectKey.
            if self.archive_root not in resolved_path.parents:
                raise ValueError("Invalid archive path")
            return resolved_path.relative_to(self.archive_root).as_posix()
        return str(resolved_path.relative_to(self.api_server_root)).replace("\\", "/")

    @classmethod
    def project_component(cls, project_id: str) -> str:
        value = str(project_id or "").strip()
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("Invalid project id for object storage")
        return value

    @classmethod
    def tenant_scope(cls, tenant_id: str | None) -> str:
        value = str(tenant_id or "").strip()
        if not value:
            if cls.hardened_storage_required():
                raise ValueError("tenant_id is required for hardened object storage")
            return "local-development"
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("Invalid tenant id for object storage")
        return value

    @classmethod
    def project_prefix(
        cls,
        project_id: str,
        *,
        tenant_id: str | None = None,
        stage: str | None = None,
    ) -> str:
        tenant = cls.tenant_scope(tenant_id)
        project = cls.project_component(project_id)
        prefix = f"tenants/{tenant}/projects/{project}"
        if stage:
            if stage not in {"quarantine", "active"}:
                raise ValueError("Invalid object lifecycle stage")
            prefix = f"{prefix}/{stage}"
        return prefix

    @classmethod
    def object_key(
        cls,
        project_id: str,
        archive_id: str,
        suffix: str,
        *,
        tenant_id: str | None = None,
        stage: str = "active",
    ) -> str:
        return (
            f"{cls.project_prefix(project_id, tenant_id=tenant_id, stage=stage)}"
            f"/{archive_id}{suffix}"
        )

    @classmethod
    def legacy_project_prefix(cls, project_id: str) -> str:
        return cls.project_component(project_id)

    @classmethod
    def legacy_object_key(
        cls,
        project_id: str,
        archive_id: str,
        suffix: str,
    ) -> str:
        return f"{cls.legacy_project_prefix(project_id)}/{archive_id}{suffix}"

    @staticmethod
    def hardened_storage_required() -> bool:
        return str(
            os.environ.get("REALYTICS_ENVIRONMENT") or "development"
        ).strip().casefold() in {"production", "prod", "staging"} or str(
            os.environ.get("REALYTICS_REQUIRE_TENANT_SCOPED_OBJECTS") or ""
        ).strip().casefold() in {"1", "true", "yes", "on"}

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
