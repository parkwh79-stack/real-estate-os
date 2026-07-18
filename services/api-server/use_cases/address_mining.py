from __future__ import annotations

"""Deterministic Korean jibun-address mining from archived source documents.

LLM parsing sometimes misses the site address (image-heavy PDFs, no-space
cover text like "경기도용인시기흥구상갈동13번지일대"). This module extracts
addresses with a regex over the archived PDF text — deterministic, offline,
and with provenance (which file, which excerpt) so the auto-supplement log can
show exactly where the address came from. Nothing is guessed: no match → None.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_SIDO = (
    "서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|"
    "세종특별자치시|경기도|강원특별자치도|강원도|충청북도|충청남도|전북특별자치도|"
    "전라북도|전라남도|경상북도|경상남도|제주특별자치도|제주도"
)

# Matches spaced AND no-space jibun addresses:
#   "경기도 용인시 기흥구 상갈동 13번지 일원"
#   "경기도용인시기흥구상갈동13번지일대"
#   "대구광역시 달서구 본동 743"
ADDRESS_RE = re.compile(
    rf"(?P<sido>{_SIDO})?\s*"
    r"(?P<si>[가-힣]{1,8}[시군])\s*"
    r"(?P<gu>[가-힣]{1,6}구)?\s*"
    r"(?P<dong>[가-힣]{1,10}(?:동|읍|면|리))\s*"
    r"(?P<jibun>산?\s*\d+(?:-\d+)?)\s*(?:번지)?"
)

_DOCUMENT_TYPES_TO_SCAN = {"business_plan", "basic_architectural_plan", "land_evidence", "land_ledger"}
_MAX_PAGES = 8
_MAX_CHARS = 40_000


_SIDO_LIST = sorted(_SIDO.split("|"), key=len, reverse=True)


def normalize_address_match(match: re.Match) -> str:
    sido = match.group("sido")
    si = match.group("si") or ""
    # Leftmost matching can start at preceding whitespace and skip the optional
    # sido group, leaving it fused into si ("경기도용인시") — split it back out.
    if not sido:
        for candidate in _SIDO_LIST:
            if si.startswith(candidate) and len(si) > len(candidate):
                sido, si = candidate, si[len(candidate):]
                break
    parts = [
        sido,
        si,
        match.group("gu"),
        match.group("dong"),
        (match.group("jibun") or "").replace(" ", ""),
    ]
    return " ".join(p for p in parts if p)


def mine_addresses_from_text(text: str) -> List[Dict[str, str]]:
    """All distinct jibun addresses found in text, in order of appearance."""
    found: List[Dict[str, str]] = []
    seen: set[str] = set()
    for match in ADDRESS_RE.finditer(text or ""):
        normalized = normalize_address_match(match)
        # Require at least si+dong+jibun; skip degenerate fragments.
        if not (match.group("si") and match.group("dong") and match.group("jibun")):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        found.append({"address": normalized, "excerpt": match.group(0).strip()[:120]})
    return found


def _pdf_text(path: Path) -> str:
    import fitz  # PyMuPDF — lazy so non-PDF callers don't pay the import

    try:
        doc = fitz.open(str(path))
    except Exception:
        return ""
    try:
        return "".join(page.get_text() for page in doc[:_MAX_PAGES])[:_MAX_CHARS]
    finally:
        doc.close()


def _pdf_text_bytes(content: bytes) -> str:
    import fitz

    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception:
        return ""
    try:
        return "".join(page.get_text() for page in doc[:_MAX_PAGES])[:_MAX_CHARS]
    finally:
        doc.close()


def _default_archive_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "runtime" / "uploads"


def mine_project_address(
    project_id: str,
    project_data: Dict[str, Any],
    *,
    archive_root: Optional[Path] = None,
    upload_archive_repository=None,
) -> Optional[Dict[str, Any]]:
    """Best jibun address mined from the project's archived documents.

    Returns {"address", "provenance": {"source", "documentFileName", "excerpt"}}
    or None when nothing matches — never invents an address.
    """
    root = (archive_root or _default_archive_root()) / str(project_id)
    archive_repository = upload_archive_repository
    if archive_repository is None and archive_root is None:
        from db.repositories.upload_archive_repository import UploadArchiveRepository

        archive_repository = UploadArchiveRepository()

    documents = list(project_data.get("source_documents") or [])
    # Scan classified documents first (provenance-rich), then any leftover PDFs.
    scanned: set[str] = set()
    ordered: List[Dict[str, Any]] = [
        doc
        for doc in documents
        if str(doc.get("documentType") or "") in _DOCUMENT_TYPES_TO_SCAN
    ] + [doc for doc in documents if str(doc.get("documentType") or "") not in _DOCUMENT_TYPES_TO_SCAN]

    candidates: List[Dict[str, Any]] = []
    for doc in ordered:
        archive_id = str(doc.get("archiveId") or doc.get("originalFileHash") or "")
        if not archive_id:
            continue
        filename = str(doc.get("originalFileName") or "")
        suffix = Path(filename).suffix.lower() or ".pdf"
        if suffix != ".pdf":
            continue
        text = ""
        source_identity = ""
        if archive_repository is not None:
            resolved = archive_repository.resolve_download_metadata(project_id, doc)
            if resolved:
                resolved_path = resolved.get("path")
                if resolved_path:
                    source_identity = str(resolved_path)
                    text = _pdf_text(Path(resolved_path))
                elif resolved.get("content") is not None:
                    source_identity = str(resolved.get("archiveObjectKey") or archive_id)
                    text = _pdf_text_bytes(resolved["content"])
        if not text:
            path = root / f"{archive_id}{suffix}"
            if path.exists():
                source_identity = str(path)
                text = _pdf_text(path)
        if not text or source_identity in scanned:
            continue
        scanned.add(source_identity)
        for hit in mine_addresses_from_text(text):
            candidates.append(
                {
                    "address": hit["address"],
                    "provenance": {
                        "source": "document_text",
                        "documentFileName": doc.get("originalFileName"),
                        "documentType": doc.get("documentType"),
                        "excerpt": hit["excerpt"],
                    },
                }
            )
        if candidates:
            break  # first document with addresses wins (usually the business plan)

    if not candidates and root.exists():
        # Fallback: any archived PDF not tracked in source_documents metadata.
        for path in sorted(root.glob("*.pdf")):
            if str(path) in scanned:
                continue
            for hit in mine_addresses_from_text(_pdf_text(path)):
                candidates.append(
                    {
                        "address": hit["address"],
                        "provenance": {
                            "source": "document_text",
                            "documentFileName": path.name,
                            "excerpt": hit["excerpt"],
                        },
                    }
                )
            if candidates:
                break

    return candidates[0] if candidates else None
