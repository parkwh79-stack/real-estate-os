"""Bounded, project/actor-scoped upload transport; never an evidence authority.

Parts remain quarantined until the existing archive/scan/parser path accepts the
assembled bytes. Expiry is an access deadline, not a promise of bucket TTL GC.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import tempfile
from typing import Any, Iterator
from uuid import uuid4

from fastapi import HTTPException

from db.object_storage import LocalObjectStorage, ObjectStorage
from db.repositories.upload_archive_repository import UploadArchiveRepository
from schemas.feasibility_study import FEASIBILITY_STUDY_SCHEMA_VERSION

PART_SIZE_BYTES = 3 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
TRANSFER_TTL_SECONDS = 3600


def processing_succeeded(result: dict) -> bool:
    """Match extraction failure signals used by the intake UI.

    Downstream autoReanalysis can fail after the source was already applied;
    cache that response so a retry cannot apply the source a second time.
    """
    def record(value):
        return value if isinstance(value, dict) else {}
    project = record(result.get("project"))
    validation = record(result.get("validation"))
    validation_status = validation.get("status")
    upload = record(result.get("upload"))
    archive_id = upload.get("archiveId")
    draft = record(result.get("feasibilityStudyV2Draft"))
    # Source formula failures still allow a separate, unapproved review draft.
    # Cache the validation output so candidate-save retries reuse the same read.
    if (
        validation_status == "failed"
        and draft.get("version") == FEASIBILITY_STUDY_SCHEMA_VERSION
        and upload.get("archiveStored") is True
        and isinstance(archive_id, str)
        and re.fullmatch(r"[0-9a-f]{64}", archive_id)
        and validation.get("sourceDocumentHash") == archive_id
    ):
        validation_status = None
    statuses = [result.get("status"), validation_status,
                record(result.get("_meta")).get("parsing_source"),
                record(project.get("_meta")).get("parsing_source"),
                record(record(result.get("upload")).get("_meta")).get("parsing_source")]
    failures = {"host_llm_required", "parse_failed", "extraction_unavailable", "failed", "error"}
    return not any(isinstance(status, str) and status in failures for status in statuses)


def _reject(code: str, status: int = 400) -> None:
    raise HTTPException(status_code=status, detail={"error": code})


class IntakeTransferStore:
    def __init__(self, storage: ObjectStorage):
        # These implementations support create-only writes. S3's current put
        # overwrites existing objects, so it cannot provide this transport's
        # part/processing-lease concurrency contract.
        if storage.backend not in {"local", "gcs"}:
            _reject("intake_transfer_storage_unsupported", 503)
        if isinstance(storage, LocalObjectStorage) and os.name == "nt":
            root = str(storage.base_dir)
            if not root.startswith("\\\\?\\"):
                root = "\\\\?\\UNC\\" + root[2:] if root.startswith("\\\\") else "\\\\?\\" + root
            storage = LocalObjectStorage(root)
        self.storage = storage

    def _prefix(self, scope: dict[str, str], transfer_id: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{32}", transfer_id):
            _reject("intake_transfer_not_found", 404)
        project_prefix = UploadArchiveRepository.project_prefix(
            scope["projectId"], tenant_id=scope["tenantId"]
        )
        actor = hashlib.sha256(scope["principalId"].encode()).hexdigest()
        return f"intake-transfers/quarantine/{project_prefix}/{actor}/{transfer_id}"

    @staticmethod
    def _encode(value: Any) -> bytes:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")

    def _put_once(self, key: str, content: bytes, manifest: dict) -> None:
        if isinstance(self.storage, LocalObjectStorage):
            path = self.storage._resolve(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("xb") as target:
                    target.write(content)
            except FileExistsError:
                pass
        else:
            try:
                self.storage.put(key, content, content_type="application/octet-stream", metadata={
                    "lifecycle-status": "quarantine",
                    "transfer-expires-at": manifest["expiresAt"],
                })
            except RuntimeError:
                # A conditional-create conflict can be a safe retry only when
                # the stored bytes agree, never just because an object exists.
                if not self.storage.exists(key):
                    _reject("intake_transfer_storage_unavailable", 503)
        if self.storage.head(key).get("size") != len(content) or self.storage.get(key) != content:
            _reject("intake_transfer_conflict", 409)

    def create(self, scope: dict[str, str], payload: dict) -> dict:
        filename = payload.get("filename")
        content_type = payload.get("contentType") or "application/octet-stream"
        size = payload.get("sizeBytes")
        digest = payload.get("sha256")
        if (not isinstance(filename, str) or not filename.strip() or len(filename) > 255
                or any(ord(c) < 32 or c in "/\\" for c in filename)):
            _reject("intake_transfer_invalid_filename")
        if (not isinstance(content_type, str) or len(content_type) > 255
                or any(ord(c) < 32 or ord(c) > 126 for c in content_type)):
            _reject("intake_transfer_invalid_content_type")
        if type(size) is not int or size < 1 or size > MAX_FILE_BYTES:
            _reject("intake_transfer_file_size_limit", 413)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            _reject("intake_transfer_invalid_sha256")
        transfer_id = uuid4().hex
        manifest = {
            "schemaVersion": "realytics-intake-transfer-v1", "transferId": transfer_id,
            "filename": filename, "contentType": content_type, "sizeBytes": size,
            "sha256": digest, "partSizeBytes": PART_SIZE_BYTES,
            "partCount": (size + PART_SIZE_BYTES - 1) // PART_SIZE_BYTES,
            "expiresAt": (datetime.now(timezone.utc) + timedelta(seconds=TRANSFER_TTL_SECONDS)).isoformat(),
            "scope": dict(scope),
        }
        self._put_once(f"{self._prefix(scope, transfer_id)}/manifest", self._encode(manifest), manifest)
        return {key: manifest[key] for key in ("transferId", "partSizeBytes", "partCount", "expiresAt")}

    def load(self, scope: dict[str, str], transfer_id: str) -> dict:
        prefix = self._prefix(scope, transfer_id)
        key = f"{prefix}/manifest"
        if not self.storage.exists(key):
            _reject("intake_transfer_not_found", 404)
        if not 0 < int(self.storage.head(key).get("size", 0)) <= 4096:
            _reject("intake_transfer_invalid_manifest", 409)
        try:
            manifest = json.loads(self.storage.get(key))
            valid = (manifest["scope"] == scope and manifest["transferId"] == transfer_id
                     and manifest["partSizeBytes"] == PART_SIZE_BYTES
                     and 0 < manifest["sizeBytes"] <= MAX_FILE_BYTES
                     and manifest["partCount"] == (manifest["sizeBytes"] + PART_SIZE_BYTES - 1) // PART_SIZE_BYTES)
            expires = datetime.fromisoformat(manifest["expiresAt"])
            if not valid or expires.tzinfo is None:
                raise ValueError("invalid manifest")
        except (ValueError, KeyError, TypeError):
            _reject("intake_transfer_invalid_manifest", 409)
        if expires <= datetime.now(timezone.utc):
            self._cleanup(prefix)
            _reject("intake_transfer_expired", 410)
        return manifest

    @staticmethod
    def expected_part_size(manifest: dict, index: int) -> int:
        if index < 0 or index >= manifest["partCount"]:
            _reject("intake_transfer_invalid_part_index")
        return min(PART_SIZE_BYTES, manifest["sizeBytes"] - index * PART_SIZE_BYTES)

    def put_part(self, scope: dict[str, str], transfer_id: str, index: int, content: bytes) -> dict:
        manifest = self.load(scope, transfer_id)
        if len(content) != self.expected_part_size(manifest, index):
            _reject("intake_transfer_invalid_part_size", 413)
        prefix = self._prefix(scope, transfer_id)
        if self.storage.exists(f"{prefix}/process-result"):
            _reject("intake_transfer_already_processed", 409)
        self._put_once(f"{prefix}/parts/{index}", content, manifest)
        return {"transferId": transfer_id, "partIndex": index, "receivedBytes": len(content)}

    @contextmanager
    def assembled_file(self, scope: dict[str, str], transfer_id: str) -> Iterator[tuple[dict, Any]]:
        manifest = self.load(scope, transfer_id)
        prefix = self._prefix(scope, transfer_id)
        digest = hashlib.sha256()
        with tempfile.TemporaryFile(mode="w+b") as target:
            for index in range(manifest["partCount"]):
                key = f"{prefix}/parts/{index}"
                expected = self.expected_part_size(manifest, index)
                if not self.storage.exists(key):
                    _reject("intake_transfer_incomplete", 409)
                if self.storage.head(key).get("size") != expected:
                    _reject("intake_transfer_integrity_failed", 409)
                content = self.storage.get(key)
                if len(content) != expected:
                    _reject("intake_transfer_integrity_failed", 409)
                digest.update(content)
                target.write(content)
            if target.tell() != manifest["sizeBytes"] or digest.hexdigest() != manifest["sha256"]:
                _reject("intake_transfer_integrity_failed", 409)
            target.seek(0)
            yield manifest, target

    @contextmanager
    def operation(self, scope: dict[str, str], transfer_id: str) -> Iterator[dict]:
        manifest = self.load(scope, transfer_id)
        key = f"{self._prefix(scope, transfer_id)}/operation"
        token = uuid4().hex.encode()
        try:
            self._put_once(key, token, manifest)
        except HTTPException as exc:
            if exc.status_code == 409:
                _reject("intake_transfer_busy", 409)
            raise
        lease = {"retainOnExit": False}
        try:
            yield lease
        finally:
            # A crashed operation stays locked until expiry rather than risking
            # duplicate project writes. Ordinary failures release for retry.
            try:
                if not lease["retainOnExit"] and self.storage.get(key) == token:
                    self.storage.delete(key)
            except Exception:
                pass

    def read_result(self, scope: dict[str, str], transfer_id: str, operation: str) -> dict | None:
        self.load(scope, transfer_id)
        key = f"{self._prefix(scope, transfer_id)}/{operation}-result"
        if not self.storage.exists(key):
            return None
        return json.loads(self.storage.get(key))

    def save_result(self, scope: dict[str, str], transfer_id: str, operation: str, result: dict) -> None:
        manifest = self.load(scope, transfer_id)
        prefix = self._prefix(scope, transfer_id)
        self._put_once(f"{prefix}/{operation}-result", self._encode(result), manifest)
        if operation == "process":
            self._cleanup(f"{prefix}/parts")

    def _cleanup(self, prefix: str) -> bool:
        try:
            return self.storage.delete_prefix(prefix)
        except Exception:
            return False

    def cancel(self, scope: dict[str, str], transfer_id: str) -> dict:
        prefix = self._prefix(scope, transfer_id)
        # DELETE is idempotent, including after expiry; never touches active
        # originals or another actor's pending upload.
        cleaned = False
        if self.storage.exists(f"{prefix}/manifest"):
            try:
                with self.operation(scope, transfer_id):
                    cleaned = self._cleanup(prefix)
            except HTTPException as exc:
                if exc.status_code != 410:
                    raise
        else:
            cleaned = self._cleanup(prefix)
        if self.storage.exists(f"{prefix}/manifest"):
            _reject("intake_transfer_cleanup_failed", 503)
        return {"transferId": transfer_id, "deleted": True, "cleanupPerformed": cleaned}
