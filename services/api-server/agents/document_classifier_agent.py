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

# Keyword cues (lowercased, spaces stripped) → document type. Korean + English.
_KEYWORDS: Dict[str, List[str]] = {
    "business_plan": ["사업계획", "계획서", "businessplan", "business_plan", "사업제안", "제안서", "ir", "투자설명"],
    "basic_architectural_plan": ["기본설계", "설계도서", "배치도", "건축계획", "도면", "architectur"],
    "land_ledger": ["토지조서", "토지대장", "지번", "필지", "소유자", "landledger", "land_ledger", "토지목록", "지적"],
    "feasibility_workbook": ["사업수지", "수지분석", "현금흐름", "수지", "feasibility", "cashflow", "cash_flow", "pf검토", "수지표"],
    "risk_report_workbook": ["리스크", "위험", "risk"],
    "land_evidence": ["land_evidence", "토지증빙", "공시지가", "등기", "감정평가", "토지이용계획"],
    "market_evidence": ["실거래", "시세", "분양가", "시장조사", "market", "거래사례", "comparable", "분양"],
}

# Extension → plausible types (ordered). Used as a prior when keywords are weak.
_EXT_HINTS: Dict[str, List[str]] = {
    ".pdf": ["business_plan", "basic_architectural_plan"],
    ".docx": ["business_plan"],
    ".doc": ["business_plan"],
    ".hwp": ["business_plan"],
    ".hwpx": ["business_plan"],
    ".xls": ["land_ledger", "feasibility_workbook"],
    ".xlsx": ["feasibility_workbook", "land_ledger", "risk_report_workbook", "market_evidence"],
    ".xlsm": ["feasibility_workbook"],
    ".json": ["land_evidence", "market_evidence"],
    ".csv": ["market_evidence", "land_ledger"],
}

# Sheet-name cues for spreadsheets (strong signal).
_SHEET_CUES: Dict[str, List[str]] = {
    "feasibility_workbook": ["사업수지", "수지", "현금흐름", "분양", "제경비", "수지총괄", "공정율", "공정률"],
    "land_ledger": ["토지조서", "토지", "지번", "소유자", "필지"],
    "risk_report_workbook": ["리스크", "위험", "risk"],
}


def _norm(text: Optional[str]) -> str:
    return (text or "").lower().replace(" ", "")


def _ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def classify_document(
    filename: str,
    *,
    content_type: Optional[str] = None,
    size: Optional[int] = None,
    sheet_names: Optional[List[str]] = None,
    sample_text: Optional[str] = None,
    json_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Classify one document. Returns documentType + Korean label + confidence
    (0–1) + the reasons behind it + a ranked candidate list."""
    ext = _ext(filename)
    name_norm = _norm(os.path.splitext(filename or "")[0])
    sample_norm = _norm(sample_text)[:4000]
    scores: Dict[str, float] = {t: 0.0 for t in _KEYWORDS}
    reasons: List[str] = []

    for doc_type, cues in _KEYWORDS.items():
        for cue in cues:
            if cue in name_norm:
                scores[doc_type] += 2.0
                reasons.append(f"파일명에 '{cue}' → {DOC_TYPES[doc_type][0]}")
            if sample_norm and cue in sample_norm:
                scores[doc_type] += 1.0

    for doc_type, cues in _SHEET_CUES.items():
        for sheet in sheet_names or []:
            s = _norm(sheet)
            for cue in cues:
                if cue in s:
                    scores[doc_type] += 3.0
                    reasons.append(f"시트 '{sheet}' → {DOC_TYPES[doc_type][0]}")
                    break

    for key in json_keys or []:
        k = _norm(key)
        if any(c in k for c in ("evidence", "공시지가", "관련", "pnu", "registry", "등기")):
            scores["land_evidence"] += 2.5
            reasons.append(f"JSON 키 '{key}' → 토지 증빙")

    # Extension prior (small weight) so a bare filename still routes sensibly.
    ext_candidates = _EXT_HINTS.get(ext, [])
    if ext_candidates:
        scores[ext_candidates[0]] += 1.0
        reasons.append(f"확장자 {ext} → {DOC_TYPES[ext_candidates[0]][0]} 가능성")

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_type, best_score = ranked[0]
    if best_score <= 0:
        best_type = "unknown"

    # Confidence: dominant score vs runner-up, capped.
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_type == "unknown":
        confidence = 0.0
    else:
        spread = best_score - runner_up
        confidence = round(min(0.98, 0.30 + 0.10 * best_score + 0.08 * spread), 2)

    label, action = DOC_TYPES[best_type]
    return {
        "schemaVersion": DOCUMENT_CLASSIFIER_SCHEMA_VERSION,
        "taxonomyVersion": DOCUMENT_TAXONOMY_VERSION,
        "filename": filename,
        "ext": ext,
        "contentType": content_type,
        "size": size,
        "documentType": best_type,
        "label": label,
        "suggestedAction": action,
        "confidence": confidence,
        "confidenceBasis": "파일명 키워드, 확장자, 시트명, JSON 키 신호를 점수화한 자동분류 신뢰도입니다. 문서 내용 검토나 승인 가능성 점수는 아닙니다.",
        "needsReview": confidence < 0.55,
        "reasons": list(dict.fromkeys(reasons))[:6],
        "candidates": [
            {"documentType": t, "label": DOC_TYPES[t][0], "score": round(s, 1)}
            for t, s in ranked
            if s > 0
        ][:4],
    }
