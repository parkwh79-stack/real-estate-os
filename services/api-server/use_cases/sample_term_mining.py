from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Sequence

from core.text_normalization import build_term_surface_record, normalize_match_text


SAMPLE_TERM_MINING_SCHEMA_VERSION = "realytics-sample-term-mining-v1"

DOMAIN_MARKERS = (
    "PF",
    "Tr.",
    "Tranche",
    "LTV",
    "LTC",
    "DSCR",
    "DSRA",
    "NOI",
    "Cap Rate",
    "Waterfall",
    "Covenant",
    "EOD",
    "Exit",
    "PNU",
    "사업수지",
    "총분양",
    "분양수입",
    "분양가",
    "매출",
    "사업비",
    "공사비",
    "토지비",
    "금융비",
    "대지면적",
    "연면적",
    "용적률",
    "건폐율",
    "공시지가",
    "지번",
    "지목",
    "소유자",
    "근저당",
    "가압류",
    "가처분",
    "제한물권",
    "관리형토지신탁",
    "우선수익권",
    "책임준공",
    "채무인수",
    "기한이익상실",
    "인허가",
    "건축허가",
    "확약",
    "확약서",
    "보증",
    "보증금",
    "임대차",
    "임차",
    "임대",
    "입주",
    "착공",
    "준공",
    "임대료",
    "공실",
    "리츠",
    "펀드",
    "선매각",
    "선매입",
    "상환",
    "분양률",
    "미분양",
    "할인분양",
)

_ABBREVIATION_RE = re.compile(
    r"\b(?:PF|PNU|LTV|LTC|DSCR|DSRA|NOI|EOD|Tr\.[A-Z]|Tranche|Cap\s*Rate|Waterfall|Covenant|Exit)\b",
    flags=re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"[\t\r\n,;:：|/\\()\[\]{}<>]+")
_MULTISPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class TextSource:
    source_name: str
    text: str
    source_type: str = "sample_corpus"
    source_locator: Dict[str, Any] | None = None


def build_sample_term_mining_package(
    sources: Sequence[TextSource | Dict[str, Any]],
    glossary_terms: Sequence[Dict[str, Any]],
    *,
    project_id: str = "sample-corpus",
    run_id: str = "sample-term-mining",
    min_suggestion_score: float = 0.72,
    max_items_per_source: int = 500,
) -> Dict[str, Any]:
    """Mine local sample documents for glossary-controlled term surfaces.

    The package is intentionally review-first: exact matches are marked as
    ``matched`` and unfamiliar surfaces are marked as ``candidate`` or
    ``needs_ai_review``. It does not by itself approve a new canonical term.
    """
    items: List[Dict[str, Any]] = []
    source_summaries: List[Dict[str, Any]] = []
    for raw_source in sources:
        source = _coerce_source(raw_source)
        extracted = extract_term_surface_items_from_text(
            source.text,
            source_name=source.source_name,
            source_type=source.source_type,
            source_locator=source.source_locator,
            max_items=max_items_per_source,
        )
        items.extend(extracted)
        source_summaries.append(
            {
                "sourceName": source.source_name,
                "sourceType": source.source_type,
                "surfaceCount": len(extracted),
                "sourceLocator": source.source_locator or {},
            }
        )

    classified = classify_term_surface_items(
        items,
        glossary_terms,
        min_suggestion_score=min_suggestion_score,
    )
    return {
        "schemaVersion": SAMPLE_TERM_MINING_SCHEMA_VERSION,
        "analysisType": "sample_corpus_term_mining",
        "projectId": project_id,
        "runId": run_id,
        "sourceCount": len(sources),
        "sourceSummaries": source_summaries,
        "surfaceCount": len(items),
        "summary": classified["summary"],
        "results": classified["results"],
        "reviewPolicy": {
            "exactMatch": "Use as an approved glossary mapping.",
            "fuzzySuggestion": "Send to glossary review; AI may suggest same-as, but reviewer approval is required before canonical use.",
            "unmapped": "Create a new candidate term or mark as free text.",
        },
    }


def extract_term_surface_items_from_text(
    text: str,
    *,
    source_name: str,
    source_type: str = "sample_corpus",
    source_locator: Dict[str, Any] | None = None,
    max_items: int = 500,
) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    items: List[Dict[str, Any]] = []
    for line_number, line in enumerate((text or "").splitlines(), start=1):
        if len(items) >= max_items:
            break
        clean_line = _clean_surface(line)
        if not clean_line:
            continue
        for surface in _candidate_surfaces(clean_line):
            if len(items) >= max_items:
                break
            key = normalize_match_text(surface)
            if not key or key in seen or _is_noise_surface(surface):
                continue
            seen.add(key)
            locator = {
                **(source_locator or {}),
                "sourceName": source_name,
                "sourceType": source_type,
                "lineNumber": line_number,
                "textExcerpt": clean_line[:160],
            }
            items.append(
                {
                    "surface": surface,
                    "sourceField": "sampleCorpus.termSurface",
                    "sourceLocator": locator,
                    "sourceName": source_name,
                    "sourceType": source_type,
                    "context": clean_line[:240],
                    "termSurface": build_term_surface_record(
                        surface,
                        source_field="sampleCorpus.termSurface",
                        source_locator=locator,
                        match_type="sample_corpus_extracted",
                        confidence=0.0,
                    ),
                }
            )
    return items


def classify_term_surface_items(
    items: Sequence[Dict[str, Any]],
    glossary_terms: Sequence[Dict[str, Any]],
    *,
    min_suggestion_score: float = 0.72,
) -> Dict[str, Any]:
    approved_terms = [
        term
        for term in glossary_terms
        if term.get("reviewStatus") == "approved" and term.get("canonicalField")
    ]
    surface_index = _surface_index(approved_terms)
    results: List[Dict[str, Any]] = []
    for item in items:
        surface = str(item.get("surface") or "").strip()
        if not surface:
            continue
        normalized = normalize_match_text(surface)
        exact = surface_index.get(normalized)
        if exact:
            results.append(_classification_result(item, "matched", "exact_alias", 1.0, exact, None))
            continue

        nearest, score = _nearest_term(surface, approved_terms)
        if nearest and score >= min_suggestion_score:
            results.append(_classification_result(item, "candidate", "fuzzy_suggestion", score, None, nearest))
        else:
            results.append(_classification_result(item, "needs_ai_review", "unmapped", score, None, nearest))

    action_counts: Dict[str, int] = {}
    category_counts: Dict[str, int] = {}
    for result in results:
        action = result["action"]
        action_counts[action] = action_counts.get(action, 0) + 1
        category = (
            (result.get("term") or {}).get("category")
            or (result.get("suggestedTerm") or {}).get("category")
            or "unmapped"
        )
        category_counts[category] = category_counts.get(category, 0) + 1

    return {
        "summary": {
            "inputCount": len(items),
            "matchedCount": action_counts.get("matched", 0),
            "candidateCount": action_counts.get("candidate", 0),
            "needsAiReviewCount": action_counts.get("needs_ai_review", 0),
            "actionCounts": dict(sorted(action_counts.items())),
            "categoryCounts": dict(sorted(category_counts.items())),
        },
        "results": results,
    }


def _classification_result(
    item: Dict[str, Any],
    action: str,
    match_type: str,
    confidence: float,
    term: Dict[str, Any] | None,
    suggested_term: Dict[str, Any] | None,
) -> Dict[str, Any]:
    selected = term or suggested_term or {}
    term_surface = build_term_surface_record(
        item.get("surface"),
        source_field=item.get("sourceField"),
        source_locator=item.get("sourceLocator"),
        term_id=selected.get("termId") if term else None,
        canonical_field=selected.get("canonicalField") if term else None,
        match_type=match_type,
        confidence=round(float(confidence or 0), 4),
    )
    return {
        "surface": item.get("surface"),
        "action": action,
        "matchType": match_type,
        "confidence": round(float(confidence or 0), 4),
        "termSurface": term_surface,
        "sourceName": item.get("sourceName"),
        "sourceType": item.get("sourceType"),
        "sourceField": item.get("sourceField"),
        "sourceLocator": item.get("sourceLocator") or {},
        "contextExcerpt": item.get("context"),
        "term": _term_ref(term),
        "suggestedTerm": _term_ref(suggested_term),
        "reviewRequired": action != "matched",
    }


def _candidate_surfaces(line: str) -> Iterable[str]:
    for match in _ABBREVIATION_RE.finditer(line):
        yield _clean_surface(match.group(0))

    for chunk in _SPLIT_RE.split(line):
        surface = _clean_surface(chunk)
        if not surface:
            continue
        if any(marker.lower() in surface.lower() for marker in DOMAIN_MARKERS):
            yield surface


def _surface_index(terms: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for term in terms:
        for surface in _term_surfaces(term):
            normalized = normalize_match_text(surface)
            if normalized:
                index.setdefault(normalized, term)
    return index


def _nearest_term(surface: str, terms: Sequence[Dict[str, Any]]) -> tuple[Dict[str, Any] | None, float]:
    normalized = normalize_match_text(surface)
    best: tuple[Dict[str, Any] | None, float] = (None, 0.0)
    for term in terms:
        for candidate in _term_surfaces(term):
            score = SequenceMatcher(None, normalized, normalize_match_text(candidate)).ratio()
            if score > best[1]:
                best = (term, score)
    return best


def _term_surfaces(term: Dict[str, Any]) -> List[str]:
    return [
        str(value)
        for value in [
            term.get("termId"),
            term.get("preferredTerm"),
            term.get("displayNameKo"),
            term.get("displayNameEn"),
            *(term.get("aliases") or []),
        ]
        if value
    ]


def _term_ref(term: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not term:
        return None
    return {
        "termId": term.get("termId"),
        "preferredTerm": term.get("preferredTerm"),
        "displayNameKo": term.get("displayNameKo"),
        "displayNameEn": term.get("displayNameEn"),
        "canonicalField": term.get("canonicalField"),
        "category": term.get("category"),
        "dataType": term.get("dataType"),
        "unit": term.get("unit"),
    }


def _clean_surface(value: Any) -> str:
    text = str(value or "").strip()
    text = _MULTISPACE_RE.sub(" ", text)
    return text.strip(" -_·ㆍ*•=0123456789.")


def _is_noise_surface(surface: str) -> bool:
    normalized = normalize_match_text(surface)
    if len(normalized) < 2:
        return True
    if len(surface) > 60:
        return True
    if surface.replace(".", "").replace("%", "").isdigit():
        return True
    if normalized in {"pdf", "xlsx", "xls", "docx", "csv", "page", "sheet"}:
        return True
    return False


def _coerce_source(raw_source: TextSource | Dict[str, Any]) -> TextSource:
    if isinstance(raw_source, TextSource):
        return raw_source
    return TextSource(
        source_name=str(raw_source.get("sourceName") or raw_source.get("source_name") or "unknown-source"),
        text=str(raw_source.get("text") or ""),
        source_type=str(raw_source.get("sourceType") or raw_source.get("source_type") or "sample_corpus"),
        source_locator=raw_source.get("sourceLocator") or raw_source.get("source_locator") or {},
    )
