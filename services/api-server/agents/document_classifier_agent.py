from __future__ import annotations

"""Deterministic document classifier for the smart intake.

Given an uploaded file (name + content type + optional sheet names / sample
text), decides which canonical document type it is so the intake can route it
to the right parser automatically — instead of making the user pick a slot.

Per AGENTS.md the classification is rule-based and explainable (every result
carries `reasons`); an LLM may *augment* ambiguous cases later, but never
fabricates or overrides the deterministic evidence. No numbers/decisions here —
this only routes documents.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, Dict, List, Optional

from parsing_platform.document_taxonomy import (
    CLASSIFIER_DOCUMENT_TYPES,
    DOCUMENT_TAXONOMY_VERSION,
    document_type_contract,
)


DOCUMENT_CLASSIFIER_SCHEMA_VERSION = "realytics-document-classifier-v1"

# Compatibility export: labels/actions now come from the canonical taxonomy.
DOC_TYPES = {
    document_type: (
        document_type_contract(document_type).label,
        document_type_contract(document_type).suggested_action,
    )
    for document_type in CLASSIFIER_DOCUMENT_TYPES
}
_INTAKE_MEMO_TYPE = "investment_committee_memo"

# Keyword cues (lowercased, spaces stripped) → document type. Korean + English.
_KEYWORDS: Dict[str, List[str]] = {
    "business_plan": ["사업계획", "계획서", "businessplan", "business_plan", "사업제안", "제안서", "ir", "투자설명"],
    "investment_committee_memo": [
        "투자심사위원회", "투자심의위원회", "투자위원회", "투자심의안건",
        "투자심사안건", "부의안건", "투자심의메모", "investmentcommitteememo",
    ],
    "basic_architectural_plan": ["기본설계", "설계도서", "배치도", "건축계획", "도면", "architectur"],
    "land_ledger": ["토지조서", "토지대장", "지번", "필지", "소유자", "landledger", "land_ledger", "토지목록", "지적"],
    "feasibility_workbook": ["사업수지", "수지분석", "현금흐름", "수지", "feasibility", "cashflow", "cash_flow", "pf검토", "수지표"],
    "risk_report_workbook": ["리스크", "위험", "risk"],
    "land_evidence": ["land_evidence", "토지증빙", "공시지가", "등기", "감정평가", "토지이용계획"],
    "market_evidence": ["실거래", "시세", "분양가", "시장조사", "market", "거래사례", "comparable", "분양"],
}

# Container compatibility precedes scoring so a document that mentions cash
# flow cannot be sent to an Excel parser. Ordered first type supplies the prior.
_NARRATIVE_TYPES = ("business_plan", "basic_architectural_plan", _INTAKE_MEMO_TYPE)
_WORKBOOK_TYPES = ("feasibility_workbook", "land_ledger", "risk_report_workbook")
_PROCESSING_TYPES = {
    "pdf": _NARRATIVE_TYPES,
    "docx": _NARRATIVE_TYPES,
    "xls": ("land_ledger",),
    "xlsx": _WORKBOOK_TYPES,
    "xlsm": _WORKBOOK_TYPES,
    "json": ("land_evidence",),
}
_CLASSIFIABLE_TYPES = {
    **_PROCESSING_TYPES,
    "doc": ("business_plan",),
    "hwp": ("business_plan",),
    "hwpx": ("business_plan",),
    "txt": _NARRATIVE_TYPES,
    "xlsx": (*_WORKBOOK_TYPES, "market_evidence"),
    "xlsm": (*_WORKBOOK_TYPES, "market_evidence"),
    "json": ("land_evidence", "market_evidence"),
    "csv": ("market_evidence",),
}

_PDF_SAMPLE_PAGES = 3
_PDF_SAMPLE_BYTES = 16 * 1024 * 1024
_PDF_SAMPLE_TIMEOUT_SECONDS = 4.0
_PDF_SAMPLE_SLOTS = BoundedSemaphore(1)
_SAMPLE_CHARS = 4_000

_TOPIC_CUES = {
    "사업수지·현금흐름": ("사업수지", "수지분석", "현금흐름", "cashflow", "cash_flow"),
    "금융구조·대출": ("금융구조", "대출", "상환계획", "트랜치", "fundingstructure"),
    "시장·분양": ("시장조사", "실거래", "분양", "거래사례", "분양가"),
    "토지·인허가": ("토지", "지번", "인허가", "건축허가"),
    "리스크": ("리스크", "위험요인", "risk"),
}

# Sheet-name cues for spreadsheets (strong signal).
_SHEET_CUES: Dict[str, List[str]] = {
    "feasibility_workbook": ["사업수지", "수지", "현금흐름", "분양", "제경비", "수지총괄", "공정율", "공정률"],
    "land_ledger": ["토지조서", "토지", "지번", "소유자", "필지"],
    "risk_report_workbook": ["리스크", "위험", "risk"],
}


def _norm(text: Optional[str]) -> str:
    return "".join((text or "").lower().split())


def _ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def sniff_intake_format(filename: str, header: bytes) -> str:
    """Return a conservative container hint without opening the file."""
    suffix = _ext(filename).lstrip(".")
    if header.startswith(b"%PDF-"):
        return "pdf"
    if header.startswith(b"PK\x03\x04"):
        return suffix if suffix in {"xlsx", "xlsm", "docx", "hwpx"} else "unknown"
    if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return suffix if suffix in {"xls", "doc", "hwp"} else "unknown"
    if suffix in {"json", "csv", "txt"} and header and b"\x00" not in header:
        return suffix
    return "unknown"


def read_intake_pdf_sample(content: bytes) -> str:
    """Read native text in a disposable process with a hard time limit."""
    if not content.startswith(b"%PDF-") or len(content) > _PDF_SAMPLE_BYTES:
        return ""
    if not _PDF_SAMPLE_SLOTS.acquire(blocking=False):
        return ""
    try:
        env = {"SystemRoot": os.environ["SystemRoot"]} if "SystemRoot" in os.environ else {}
        result = subprocess.run(
            [sys.executable, "-B", "-m", "agents.document_classifier_agent", "--pdf-sample-worker"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_PDF_SAMPLE_TIMEOUT_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            check=False,
        )
        if result.returncode != 0 or len(result.stdout) > _SAMPLE_CHARS * 4:
            return ""
        return result.stdout.decode("utf-8")[:_SAMPLE_CHARS]
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return ""
    finally:
        _PDF_SAMPLE_SLOTS.release()


def _read_pdf_native_sample(content: bytes) -> str:
    import fitz

    try:
        with fitz.open(stream=content, filetype="pdf") as document:
            if document.needs_pass:
                return ""
            remaining = _SAMPLE_CHARS
            samples = []
            for page_number in range(min(len(document), _PDF_SAMPLE_PAGES)):
                sample = document[page_number].get_text().strip()[:remaining]
                samples.append(sample)
                remaining -= len(sample) + 1
                if remaining <= 0:
                    break
            return "\n".join(samples)
    except (fitz.FileDataError, RuntimeError, ValueError):
        return ""


def intake_processing_types(filename: str, *, detected_format: str | None = None) -> tuple[str, ...]:
    media_format = detected_format if detected_format is not None else _ext(filename).lstrip(".")
    return _PROCESSING_TYPES.get(media_format, ())


def _matches_cue(cue: str, raw_text: str, normalized: str) -> bool:
    if len(cue) <= 2 and cue.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(cue)}(?![a-z0-9])", raw_text, re.IGNORECASE) is not None
    return cue in normalized


def classify_document(
    filename: str,
    *,
    content_type: Optional[str] = None,
    size: Optional[int] = None,
    sheet_names: Optional[List[str]] = None,
    sample_text: Optional[str] = None,
    json_keys: Optional[List[str]] = None,
    detected_format: str | None = None,
) -> Dict[str, Any]:
    """Classify one document. Returns documentType + Korean label + confidence
    (0–1) + the reasons behind it + a ranked candidate list."""
    ext = _ext(filename)
    name = os.path.splitext(filename or "")[0]
    name_norm = _norm(name)
    sample = (sample_text or "")[:_SAMPLE_CHARS]
    sample_norm = _norm(sample)
    media_format = detected_format if detected_format is not None else ext.lstrip(".")
    eligible = _CLASSIFIABLE_TYPES.get(media_format, ())
    if detected_format is None:
        eligible = tuple(doc_type for doc_type in eligible if doc_type in DOC_TYPES)
    scores: Dict[str, float] = dict.fromkeys(eligible, 0.0)
    reasons: List[str] = []

    for doc_type in eligible:
        cues = _KEYWORDS[doc_type]
        name_cue = next((cue for cue in cues if _matches_cue(cue, name, name_norm)), None)
        sample_cue = next((cue for cue in cues if _matches_cue(cue, sample, sample_norm)), None)
        specific_memo = doc_type == _INTAKE_MEMO_TYPE
        label = document_type_contract(doc_type).label
        if name_cue:
            scores[doc_type] += 4.0 if specific_memo else 2.0
            reasons.append(f"파일명에 '{name_cue}' → {label}")
        if sample_cue:
            scores[doc_type] += 3.0 if specific_memo else 1.0
            reasons.append(f"본문 일부에 '{sample_cue}' → {label}")

    for doc_type, cues in _SHEET_CUES.items():
        if doc_type not in scores:
            continue
        for sheet in sheet_names or []:
            s = _norm(sheet)
            for cue in cues:
                if cue in s:
                    scores[doc_type] += 3.0
                    reasons.append(f"시트 '{sheet}' → {DOC_TYPES[doc_type][0]}")
                    break

    for key in (json_keys or []) if "land_evidence" in scores else []:
        k = _norm(key)
        if any(c in k for c in ("evidence", "공시지가", "관련", "pnu", "registry", "등기")):
            scores["land_evidence"] += 2.5
            reasons.append(f"JSON 키 '{key}' → 토지 증빙")

    # Extension prior (small weight) so a bare filename still routes sensibly.
    if eligible:
        prior = eligible[0]
        scores[prior] += 1.0
        reasons.append(f"파일 형식 {media_format.upper()} → {document_type_contract(prior).label} 가능성")

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_type, best_score = ranked[0] if ranked else ("unknown", 0.0)

    # Confidence: dominant score vs runner-up, capped.
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_type == "unknown":
        confidence = 0.0
    else:
        spread = best_score - runner_up
        confidence = round(min(0.98, 0.30 + 0.10 * best_score + 0.08 * spread), 2)

    contract = document_type_contract(best_type)
    text_review_needed = media_format == "pdf" and sample_text is not None and not sample_norm
    if text_review_needed:
        reasons.append("앞쪽 페이지의 텍스트를 확인하지 못해 파일명과 형식만 사용했습니다.")
    result = {
        "schemaVersion": DOCUMENT_CLASSIFIER_SCHEMA_VERSION,
        "taxonomyVersion": DOCUMENT_TAXONOMY_VERSION,
        "filename": filename,
        "ext": ext,
        "contentType": content_type,
        "size": size,
        "documentType": best_type,
        "label": contract.label,
        "suggestedAction": contract.suggested_action,
        "confidence": confidence,
        "confidenceBasis": "파일 형식, 파일명, 읽을 수 있는 본문 일부, 시트명, JSON 키 신호를 점수화한 자동분류 신뢰도입니다.",
        "needsReview": confidence < 0.55 or best_score - runner_up < 0.75 or text_review_needed,
        "reasons": list(dict.fromkeys(reasons))[:6],
        "candidates": [
            {"documentType": t, "label": document_type_contract(t).label, "score": round(s, 1)}
            for t, s in ranked
            if s > 0
        ][:4],
    }
    if detected_format is not None:
        result.update(
            detectedFormat=media_format,
            processingTypes=list(intake_processing_types(filename, detected_format=media_format)),
            topics=[
                topic for topic, cues in _TOPIC_CUES.items()
                if any(cue in sample_norm for cue in cues)
            ],
        )
    # Debt-status tables are not parcel registers or attested loan contracts.
    # Require both a specific title and the actual table headers before choosing
    # a source-review path; the presence of collateral alone proves nothing.
    debt_title = any(cue in name_norm + sample_norm[:200] for cue in ("브릿지론현황", "브리지론현황", "대출현황"))
    debt_headers = sum(cue in sample_norm for cue in ("대출금", "대주", "만기", "금리"))
    if media_format in {"xlsx", "xlsm"} and debt_title and debt_headers >= 3:
        result.update(documentType="unknown", label="금융현황 · 항목 매핑 필요", suggestedAction="manual_review",
                      needsReview=True, confidence=0.0, candidates=[])
        if detected_format is not None:
            result.update(processingMode="document_review", processingTypes=[])
        result["reasons"] = ["브릿지론·대출 현황 제목과 대출금·대주·만기·금리 열이 확인되어 원본 검토로 연결합니다.",
                             "토지조서나 체결된 대출약정으로 확정하지 않습니다."]
        if detected_format is not None:
            result["topics"] = list(dict.fromkeys([*(result.get("topics") or []), "금융구조·대출"]))
    return result


def _run_pdf_sample_worker() -> None:
    if sys.platform.startswith("linux"):
        import resource

        memory_limit = 256 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    content = sys.stdin.buffer.read(_PDF_SAMPLE_BYTES + 1)
    if content.startswith(b"%PDF-") and len(content) <= _PDF_SAMPLE_BYTES:
        sys.stdout.buffer.write(_read_pdf_native_sample(content).encode("utf-8"))


if __name__ == "__main__":
    if sys.argv[1:] != ["--pdf-sample-worker"]:
        raise SystemExit(2)
    _run_pdf_sample_worker()
