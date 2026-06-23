from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from storage import (
    get_glossary_term_record,
    list_glossary_term_records,
    upsert_glossary_term_record,
)


GLOSSARY_SCHEMA_VERSION = "realytics-glossary-term-v1"
GLOSSARY_LIST_SCHEMA_VERSION = "realytics-glossary-term-list-v1"
CANONICAL_FIELD_MAP_SCHEMA_VERSION = "realytics-canonical-field-map-v1"
GLOSSARY_SEED_FILE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "real_estate_glossary_seed.json"

ALLOWED_REVIEW_STATUSES = {
    "approved",
    "pending_review",
    "needs_revision",
    "rejected",
    "free_text",
    "deprecated",
}

STATUSES_REQUIRING_CANONICAL_FIELD = {"approved", "pending_review", "needs_revision"}


def ensure_seed_glossary_terms() -> None:
    if list_glossary_term_records():
        return
    for term in _load_seed_terms():
        upsert_glossary_term_record(_normalize_glossary_term(term, actor="system_seed"))


def list_glossary_terms_use_case(
    *,
    query: str | None = None,
    category: str | None = None,
    canonical_field: str | None = None,
    review_status: str | None = None,
) -> Dict[str, Any]:
    ensure_seed_glossary_terms()
    terms = list_glossary_term_records(
        query=query,
        category=category,
        canonical_field=canonical_field,
        review_status=review_status,
    )
    return {
        "schemaVersion": GLOSSARY_LIST_SCHEMA_VERSION,
        "filters": {
            "query": query,
            "category": category,
            "canonicalField": canonical_field,
            "reviewStatus": review_status,
        },
        "count": len(terms),
        "terms": terms,
    }


def get_glossary_term_use_case(term_id: str) -> Dict[str, Any] | None:
    ensure_seed_glossary_terms()
    return get_glossary_term_record(term_id)


def upsert_glossary_term_use_case(payload: Dict[str, Any], *, actor: str = "anonymous") -> Dict[str, Any]:
    ensure_seed_glossary_terms()
    current = get_glossary_term_record(payload.get("termId") or payload.get("term_id") or "")
    normalized = _normalize_glossary_term(payload, actor=actor, current=current)
    return upsert_glossary_term_record(normalized)


def resolve_glossary_term_use_case(term: str, *, include_pending: bool = False) -> Dict[str, Any]:
    ensure_seed_glossary_terms()
    normalized = _normalize_match_text(term)
    candidates: list[Dict[str, Any]] = []
    for item in list_glossary_term_records():
        if item.get("reviewStatus") != "approved" and not include_pending:
            continue
        names = [
            item.get("termId"),
            item.get("preferredTerm"),
            item.get("displayNameKo"),
            item.get("displayNameEn"),
            *(item.get("aliases") or []),
        ]
        if normalized in {_normalize_match_text(value) for value in names if value}:
            candidates.append(item)
    return {
        "schemaVersion": "realytics-glossary-resolution-v1",
        "query": term,
        "matched": len(candidates) == 1,
        "matchCount": len(candidates),
        "term": candidates[0] if len(candidates) == 1 else None,
        "candidates": candidates,
    }


def canonical_field_map_use_case() -> Dict[str, Any]:
    ensure_seed_glossary_terms()
    field_map: Dict[str, Dict[str, Any]] = {}
    category_counts: Dict[str, int] = {}
    term_count = 0
    for term in list_glossary_term_records(review_status="approved"):
        term_count += 1
        category = term.get("category") or "uncategorized"
        category_counts[category] = category_counts.get(category, 0) + 1
        canonical_field = term.get("canonicalField")
        if not canonical_field:
            continue
        entry = field_map.setdefault(
            canonical_field,
            {
                "canonicalField": canonical_field,
                "canonicalPath": term.get("canonicalPath"),
                "terms": [],
                "aliases": [],
                "categories": [],
            },
        )
        entry["terms"].append(
            {
                "termId": term.get("termId"),
                "displayNameKo": term.get("displayNameKo"),
                "displayNameEn": term.get("displayNameEn"),
                "dataType": term.get("dataType"),
                "unit": term.get("unit"),
            }
        )
        entry["aliases"].extend(term.get("aliases") or [])
        if term.get("category") and term.get("category") not in entry["categories"]:
            entry["categories"].append(term["category"])
    for entry in field_map.values():
        entry["aliases"] = sorted(set(entry["aliases"]))
        entry["categories"] = sorted(entry["categories"])
    return {
        "schemaVersion": CANONICAL_FIELD_MAP_SCHEMA_VERSION,
        "termCount": term_count,
        "fieldCount": len(field_map),
        "categoryCounts": dict(sorted(category_counts.items())),
        "fields": dict(sorted(field_map.items())),
    }


def _load_seed_terms() -> list[Dict[str, Any]]:
    with GLOSSARY_SEED_FILE.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    return list(payload.get("terms") or [])


def _normalize_glossary_term(
    payload: Dict[str, Any],
    *,
    actor: str,
    current: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    preferred_term = str(payload.get("preferredTerm") or payload.get("preferred_term") or "").strip()
    term_id = str(payload.get("termId") or payload.get("term_id") or _term_id_from_preferred(preferred_term)).strip()
    if not term_id:
        raise ValueError("termId or preferredTerm is required")

    review_status = payload.get("reviewStatus") or payload.get("review_status") or "pending_review"
    if review_status not in ALLOWED_REVIEW_STATUSES:
        raise ValueError(f"reviewStatus must be one of {sorted(ALLOWED_REVIEW_STATUSES)}")
    canonical_field = payload.get("canonicalField") or payload.get("canonical_field")
    if review_status in STATUSES_REQUIRING_CANONICAL_FIELD and not canonical_field:
        raise ValueError("canonicalField is required for reviewed automation terms")

    aliases = _distinct_strings(payload.get("aliases") or [])
    if preferred_term and preferred_term not in aliases:
        aliases.append(preferred_term)
    if payload.get("displayNameKo") and payload["displayNameKo"] not in aliases:
        aliases.append(payload["displayNameKo"])

    version = int((current or {}).get("version") or 0) + 1 if current else int(payload.get("version") or 1)
    history = list((current or {}).get("reviewHistory") or [])
    history.append(
        {
            "at": now,
            "by": actor,
            "reviewStatus": review_status,
            "canonicalField": canonical_field,
            "reason": payload.get("reviewReason") or payload.get("reason") or "glossary term upsert",
        }
    )

    return {
        "schemaVersion": GLOSSARY_SCHEMA_VERSION,
        "termId": term_id,
        "preferredTerm": preferred_term or term_id,
        "displayNameKo": payload.get("displayNameKo") or payload.get("display_name_ko") or preferred_term or term_id,
        "displayNameEn": payload.get("displayNameEn") or payload.get("display_name_en"),
        "aliases": aliases,
        "definitionKo": payload.get("definitionKo") or payload.get("definition_ko") or "",
        "canonicalField": canonical_field,
        "canonicalPath": payload.get("canonicalPath") or payload.get("canonical_path") or canonical_field,
        "category": payload.get("category") or "uncategorized",
        "dataType": payload.get("dataType") or payload.get("data_type") or "text",
        "unit": payload.get("unit"),
        "sourceExamples": payload.get("sourceExamples") or payload.get("source_examples") or [],
        "reviewStatus": review_status,
        "reviewedBy": payload.get("reviewedBy") or payload.get("reviewed_by") or actor,
        "reviewedAt": payload.get("reviewedAt") or payload.get("reviewed_at") or now,
        "reviewReason": payload.get("reviewReason") or payload.get("reason"),
        "version": version,
        "createdAt": (current or {}).get("createdAt") or payload.get("createdAt") or now,
        "updatedAt": now,
        "notes": payload.get("notes"),
        "reviewHistory": history,
    }


def _term_id_from_preferred(value: str) -> str:
    normalized: list[str] = []
    previous_separator = False
    for char in value.strip().lower():
        if char.isalnum():
            normalized.append(char)
            previous_separator = False
        elif not previous_separator:
            normalized.append("_")
            previous_separator = True
    return "".join(normalized).strip("_")


def _distinct_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        key = _normalize_match_text(text)
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _normalize_match_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("-", "_").split())
