from __future__ import annotations

import re
import unicodedata
from typing import Any


ZERO_WIDTH_CHARS = ("\ufeff", "\u200b", "\u200c", "\u200d", "\u2060", "\x00")
_SPACE_RE = re.compile(r"\s+")


def normalize_unicode_text(value: Any) -> str:
    """Return a stable Unicode string while preserving the original elsewhere.

    This is intended for matching, indexing, and diagnostics. Source payloads
    should keep their original raw text for audit and evidence purposes.
    """
    text = "" if value is None else str(value)
    for marker in ZERO_WIDTH_CHARS:
        text = text.replace(marker, "")
    return unicodedata.normalize("NFC", text)


def repair_utf8_mojibake(value: Any) -> str | None:
    """Best-effort repair for UTF-8 text that was decoded as a western codec.

    If the text was already replaced with question marks, it cannot be repaired.
    The caller should only use the repaired value when it increases Hangul
    coverage; otherwise keep the original.
    """
    text = normalize_unicode_text(value)
    if not text:
        return None
    for codec in ("latin1", "cp1252"):
        try:
            repaired = text.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if _hangul_count(repaired) > _hangul_count(text):
            return unicodedata.normalize("NFC", repaired)
    return None


def normalize_match_text(value: Any) -> str:
    """Normalize text for Korean/English glossary alias matching."""
    text = normalize_unicode_text(value)
    repaired = repair_utf8_mojibake(text)
    if repaired:
        text = repaired
    text = unicodedata.normalize("NFKC", text)
    text = text.strip().lower().replace("-", "_")
    return _SPACE_RE.sub(" ", text)


def text_quality_flags(value: Any) -> dict[str, bool]:
    """Small diagnostics used by import pipelines and glossary review queues."""
    raw = "" if value is None else str(value)
    normalized = normalize_unicode_text(raw)
    return {
        "hasBomOrZeroWidth": raw != normalized and any(marker in raw for marker in ZERO_WIDTH_CHARS),
        "hasReplacementChar": "\ufffd" in raw,
        "looksLikeMojibake": _looks_like_mojibake(raw),
        "canRepairUtf8Mojibake": repair_utf8_mojibake(raw) is not None,
        "isUnicodeNormalized": raw == normalized,
    }


def build_term_surface_record(
    raw_text: Any,
    *,
    source_field: str | None = None,
    source_locator: Any = None,
    term_id: str | None = None,
    canonical_field: str | None = None,
    match_type: str | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    """Build the DB-to-Pod terminology boundary payload.

    The record deliberately carries raw and normalized text side by side:
    ``rawText`` remains the evidence-facing value, while
    ``normalizedSurface`` is only used for controlled-vocabulary matching.
    """
    normalized = normalize_match_text(raw_text)
    record: dict[str, Any] = {
        "rawText": "" if raw_text is None else str(raw_text),
        "normalizedSurface": normalized,
        "textQualityFlags": text_quality_flags(raw_text),
        "termId": term_id,
        "canonicalField": canonical_field,
    }
    if source_field is not None:
        record["sourceField"] = source_field
    if source_locator is not None:
        record["sourceLocator"] = source_locator
    if match_type is not None:
        record["matchType"] = match_type
    if confidence is not None:
        record["confidence"] = confidence
    return record


def _looks_like_mojibake(text: str) -> bool:
    if "\ufffd" in text:
        return True
    if _hangul_count(text) > 0:
        return False
    mojibake_markers = sum(1 for ch in text if ch in "ìíëêãâäåðñ¼½¾±°")
    return mojibake_markers >= 2


def _hangul_count(text: str) -> int:
    return sum(1 for ch in text if "\uac00" <= ch <= "\ud7a3")
