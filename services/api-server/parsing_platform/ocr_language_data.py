"""Pinned, offline Korean/English OCR language data (Apache-2.0)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import fitz

MODEL_COMMIT = "87416418657359cb625c412a48b6e1d6d41c29bd"
MODEL_BASE_URL = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{MODEL_COMMIT}"
TESSDATA_ENV = "REALYTICS_OCR_TESSDATA_DIR"
MODEL_FILES = {
    "kor.traineddata": {"bytes": 1677415, "sha256": "6b85e11d9bbf07863b97b3523b1b112844c43e713df8b66418a081fd1060b3b2"},
    "eng.traineddata": {"bytes": 4113088, "sha256": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"},
    "LICENSE": {"bytes": 11358, "sha256": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"},
}


class OcrLanguageDataUnavailable(ValueError):
    """No OCR is attempted with absent, changed or unverified language data."""


def file_matches_manifest(path: Path, expected: dict) -> bool:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != expected["bytes"]:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected["sha256"]


def verified_tessdata_dir(directory: str | Path | None = None) -> Path:
    value = directory or os.environ.get(TESSDATA_ENV)
    if not value:
        raise OcrLanguageDataUnavailable("OCR_LANGUAGE_DATA_UNAVAILABLE")
    path = Path(value)
    if path.is_symlink() or not path.is_dir():
        raise OcrLanguageDataUnavailable("OCR_LANGUAGE_DATA_UNAVAILABLE")
    try:
        if any(not file_matches_manifest(path / name, expected) for name, expected in MODEL_FILES.items()):
            raise OcrLanguageDataUnavailable("OCR_LANGUAGE_DATA_INTEGRITY_FAILED")
    except OSError as exc:
        raise OcrLanguageDataUnavailable("OCR_LANGUAGE_DATA_UNAVAILABLE") from exc
    return path.resolve()


def reading_engine_fingerprint() -> str:
    # No network or model loading is required to look up an existing result.
    identity = {
        "engine": "pymupdf-tesseract",
        "pymupdfVersion": str(getattr(fitz, "VersionBind", "unknown")),
        "languageDataCommit": MODEL_COMMIT,
        "languages": "kor+eng",
        "models": MODEL_FILES,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def reading_engine_metadata() -> dict:
    return {
        "id": "pymupdf-tesseract", "pymupdfVersion": str(getattr(fitz, "VersionBind", "unknown")),
        "languages": ["kor", "eng"], "languageDataCommit": MODEL_COMMIT,
        "languageDataLicense": "Apache-2.0", "fingerprint": reading_engine_fingerprint(),
        "confidenceProvided": False,
    }
