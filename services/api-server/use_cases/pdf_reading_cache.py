"""Disposable, non-authoritative PDF reading cache bound to a verified original.

Callers must authorize the project and revalidate the original, including its
full SHA-256, before using this cache. No project fields or approved facts are
written here. Only completed page readings are reusable; failures remain
retryable and are returned by the reading request rather than cached as success.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from typing import Any

from db.object_storage import LocalObjectStorage, ObjectStorage
from db.repositories.upload_archive_repository import UploadArchiveRepository
from parsing_platform.adapters import ArchivedDocument, MAX_PDF_PAGES

READING_SCHEMA_VERSION = "realytics-local-pdf-reading-v1"
CACHE_SCHEMA_VERSION = "realytics-pdf-reading-cache-v1"
MAX_READING_RESPONSE_BYTES = 3 * 1024 * 1024
CACHE_TTL_DAYS = 7
PAGE_STATUSES = {"completed", "empty", "unavailable", "timeout", "failed"}


class PdfReadingCacheError(ValueError):
    pass


def encoded_reading(value: Any) -> bytes:
    try:
        content = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PdfReadingCacheError("pdf_reading_invalid_result") from exc
    if len(content) >= MAX_READING_RESPONSE_BYTES:
        raise PdfReadingCacheError("pdf_reading_response_too_large")
    return content


def selected_reading_pages(value: Any) -> list[int]:
    if (not isinstance(value, list) or not 1 <= len(value) <= 3
            or any(type(page) is not int or not 1 <= page <= MAX_PDF_PAGES for page in value)
            or len(set(value)) != len(value)):
        raise PdfReadingCacheError("pdf_reading_select_one_to_three_unique_pages")
    return sorted(value)


class PdfReadingCache:
    def __init__(self, storage: ObjectStorage, *, project_id: str, view: dict, reader_fingerprint: str):
        if isinstance(storage, LocalObjectStorage) and os.name == "nt":
            root = str(storage.base_dir)
            if not root.startswith("\\\\?\\"):
                root = "\\\\?\\UNC\\" + root[2:] if root.startswith("\\\\") else "\\\\?\\" + root
            storage = LocalObjectStorage(root)
        self.storage = storage
        tenant = view.get("archiveTenantId")
        version = view.get("archiveVersionId")
        if not version and view.get("archiveBackend") == "local" and view.get("archiveETag"):
            version = f"local-etag:{view['archiveETag']}"
        if not tenant or not version or not view.get("archiveObjectKey"):
            raise PdfReadingCacheError("pdf_reading_source_binding_incomplete")
        if not isinstance(reader_fingerprint, str) or len(reader_fingerprint) != 64:
            raise PdfReadingCacheError("pdf_reading_reader_binding_invalid")
        document = view.get("document") if isinstance(view.get("document"), dict) else {}
        self.binding = {
            "tenantId": str(tenant), "projectId": project_id,
            "archiveId": str(view["archiveId"]).lower(),
            "objectKey": str(view["archiveObjectKey"]), "objectVersion": str(version),
            "documentVersionId": document.get("documentVersionId") or document.get("document_version_id"),
            "readerFingerprint": reader_fingerprint,
            "filename": str(view["filename"]),
        }
        self.source_verification = {
            "hashAlgorithm": "sha256", "hashVerified": True,
            "archiveVersionId": view.get("archiveVersionId"),
            "documentVersionId": self.binding["documentVersionId"],
            "bindingMode": "local_hash_etag" if view.get("archiveBackend") == "local" else "object_generation_etag",
        }
        self.binding_hash = hashlib.sha256(encoded_reading(self.binding)).hexdigest()
        project_prefix = UploadArchiveRepository.project_prefix(project_id, tenant_id=str(tenant))
        self.prefix = f"derived/pdf-reading/{project_prefix}/{self.binding['archiveId']}/{self.binding_hash}/pages"

    def document(self, content: bytes) -> ArchivedDocument:
        return ArchivedDocument(
            project_id=self.binding["projectId"], tenant_id=self.binding["tenantId"],
            archive_id=self.binding["archiveId"], filename=self.binding["filename"],
            content_type="application/pdf", content=content,
            object_key=self.binding["objectKey"], object_version=self.binding["objectVersion"],
        )

    def not_generated(self, page_number: int) -> dict:
        return {
            "schemaVersion": READING_SCHEMA_VERSION, "status": "not_generated",
            "projectId": self.binding["projectId"], "archiveId": self.binding["archiveId"],
            "fileSha256": self.binding["archiveId"], "objectVersion": self.binding["objectVersion"],
            "readerFingerprint": self.binding["readerFingerprint"], "pageNumbers": [page_number],
            "pageCount": None, "pages": [], "ocrRequiredPages": [], "completedOcrPages": [],
            "pendingOcrPages": [], "failedPages": [], "coverageAssessed": False,
            "coverageScope": "returned_pages_only", "analysisOnly": True,
            "approvalEligible": False, "reportEligible": False, "factPromotionAllowed": False,
            "sourceVerification": dict(self.source_verification),
        }

    def normalize(self, result: dict, page_numbers: list[int]) -> dict:
        if not isinstance(result, dict) or result.get("schemaVersion") != READING_SCHEMA_VERSION:
            raise PdfReadingCacheError("pdf_reading_invalid_result")
        for name in ("projectId", "archiveId", "objectVersion", "readerFingerprint"):
            if result.get(name) != self.binding[name]:
                raise PdfReadingCacheError("pdf_reading_source_binding_mismatch")
        for name in ("tenantId", "objectKey"):
            if name in result and result[name] != self.binding[name]:
                raise PdfReadingCacheError("pdf_reading_source_binding_mismatch")
        if result.get("fileSha256") != self.binding["archiveId"]:
            raise PdfReadingCacheError("pdf_reading_source_binding_mismatch")
        if (result.get("analysisOnly") is not True or result.get("reportEligible") is not False
                or result.get("factPromotionAllowed") is not False):
            raise PdfReadingCacheError("pdf_reading_authority_invalid")
        pages = result.get("pages")
        page_count = result.get("pageCount")
        if (type(page_count) is not int or not 1 <= page_count <= MAX_PDF_PAGES
                or not isinstance(pages, list) or len(pages) != len(page_numbers)
                or any(not isinstance(page, dict) for page in pages)
                or any(type(page.get("pageNumber")) is not int for page in pages)
                or sorted(page.get("pageNumber", 0) for page in pages) != sorted(page_numbers)
                or max(page_numbers) > page_count):
            raise PdfReadingCacheError("pdf_reading_page_binding_invalid")
        for page in pages:
            self._validate_page(page)
        required = result.get("ocrRequiredPages")
        if (not isinstance(required, list)
                or any(type(number) is not int or not 1 <= number <= page_count for number in required)
                or len(set(required)) != len(required)):
            raise PdfReadingCacheError("pdf_reading_coverage_invalid")
        if any((page["pageNumber"] in required) != (page["sourceKind"] == "ocr") for page in pages):
            raise PdfReadingCacheError("pdf_reading_coverage_invalid")
        normalized = deepcopy(result)
        # Internal bucket keys are never supplied as caller-controlled inputs or
        # needed by the browser. Locators retain the original archive/version.
        normalized.pop("objectKey", None)
        normalized.pop("tenantId", None)
        normalized["pages"] = sorted(normalized["pages"], key=lambda page: page["pageNumber"])
        for page in normalized["pages"]:
            page.update({"reviewRequired": True, "approvalEligible": False,
                         "reportEligible": False, "factPromotionAllowed": False})
        completed = [page["pageNumber"] for page in pages
                     if page["sourceKind"] == "ocr" and page["status"] == "completed"]
        normalized.update({
            "pageNumbers": sorted(page_numbers), "ocrRequiredPages": sorted(required),
            "completedOcrPages": sorted(completed), "pendingOcrPages": sorted(set(required) - set(completed)),
            "failedPages": sorted(page["pageNumber"] for page in pages if page["status"] not in {"completed", "empty"}),
            "coverageAssessed": True, "coverageScope": "returned_pages_only", "approvalEligible": False,
            "sourceVerification": dict(self.source_verification),
        })
        encoded_reading(normalized)
        return normalized

    @staticmethod
    def _validate_page(page: dict) -> None:
        text = page.get("text")
        blocks = page.get("blocks")
        if (page.get("status") not in PAGE_STATUSES or page.get("sourceKind") not in {"native", "ocr"}
                or not isinstance(text, str) or not isinstance(blocks, list)
                or page.get("textSha256") != hashlib.sha256(text.encode("utf-8")).hexdigest()
                or page.get("coordinateSpace") != "pdf_points_top_left"):
            raise PdfReadingCacheError("pdf_reading_page_integrity_invalid")
        width, height = page.get("width"), page.get("height")
        if any(isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0
               for value in (width, height)):
            raise PdfReadingCacheError("pdf_reading_page_geometry_invalid")
        if page["status"] not in {"completed", "empty"} and (text or blocks):
            raise PdfReadingCacheError("pdf_reading_failed_page_contains_text")
        for block in blocks:
            bbox = block.get("bbox") if isinstance(block, dict) else None
            if (not isinstance(block, dict) or not isinstance(block.get("text"), str)
                    or not isinstance(bbox, list) or len(bbox) != 4
                    or any(isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) for value in bbox)
                    or not (0 <= bbox[0] < bbox[2] <= width + 0.001 and 0 <= bbox[1] < bbox[3] <= height + 0.001)):
                raise PdfReadingCacheError("pdf_reading_block_geometry_invalid")

    def _key(self, page_number: int) -> str:
        selected_reading_pages([page_number])
        return f"{self.prefix}/{page_number}.json"

    def load(self, page_number: int) -> dict | None:
        key = self._key(page_number)
        if not self.storage.exists(key):
            return None
        head = self.storage.head(key)
        size = head.get("size")
        if type(size) is not int or not 0 < size <= MAX_READING_RESPONSE_BYTES:
            raise PdfReadingCacheError("pdf_reading_cache_integrity_failed")
        content = self.storage.get_range(key, 0, size - 1, version_id=head.get("versionId"), if_match=head.get("etag"))
        metadata = head.get("metadata") or {}
        if self.storage.backend != "local" and metadata.get("sha256") != hashlib.sha256(content).hexdigest():
            raise PdfReadingCacheError("pdf_reading_cache_integrity_failed")
        try:
            saved = json.loads(content)
            if saved["schemaVersion"] != CACHE_SCHEMA_VERSION or saved["binding"] != self.binding:
                raise ValueError("cache binding changed")
            expires = datetime.fromisoformat(saved["expiresAt"])
            if expires.tzinfo is None:
                raise ValueError("cache expiry missing timezone")
        except (ValueError, TypeError, KeyError) as exc:
            raise PdfReadingCacheError("pdf_reading_cache_integrity_failed") from exc
        if expires <= datetime.now(timezone.utc):
            self.storage.delete(key)
            return None
        reading = self.normalize(saved["reading"], [page_number])
        if reading["pages"][0]["status"] not in {"completed", "empty"}:
            raise PdfReadingCacheError("pdf_reading_cache_incomplete")
        return reading

    def save(self, reading: dict, page_number: int) -> dict:
        existing = self.load(page_number)
        if existing is not None:
            return existing
        page_result = deepcopy(reading)
        page_result["pages"] = [page for page in reading["pages"] if page["pageNumber"] == page_number]
        normalized = self.normalize(page_result, [page_number])
        if normalized["pages"][0]["status"] not in {"completed", "empty"}:
            raise PdfReadingCacheError("pdf_reading_cache_incomplete")
        key = self._key(page_number)
        now = datetime.now(timezone.utc)
        content = encoded_reading({"schemaVersion": CACHE_SCHEMA_VERSION, "binding": self.binding,
                                   "generatedAt": now.isoformat(),
                                   "expiresAt": (now + timedelta(days=CACHE_TTL_DAYS)).isoformat(),
                                   "reading": normalized})
        try:
            self.storage.put(key, content, content_type="application/json", metadata={
                "sha256": hashlib.sha256(content).hexdigest(), "source-sha256": self.binding["archiveId"],
                "source-binding-sha256": self.binding_hash, "lifecycle-status": "derived",
                "authority-status": "analysis-only",
            })
        except RuntimeError:
            # Concurrent GCS create-only writes may contain different creation
            # timestamps. Reuse a fully validated winner; never overwrite it.
            existing = self.load(page_number)
            if existing is not None:
                return existing
            raise
        stored = self.load(page_number)
        if stored is None:
            raise PdfReadingCacheError("pdf_reading_cache_write_unverified")
        return stored
