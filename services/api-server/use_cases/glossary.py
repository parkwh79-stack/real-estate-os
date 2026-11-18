from __future__ import annotations

import json
import os
from difflib import SequenceMatcher
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from agent_platform.contracts import LearningCandidate
from agent_platform.learning import LearningLifecycleError, LearningLifecycleService
from agent_platform.operational import OperationalRepository

from core.text_normalization import build_term_surface_record, normalize_match_text
from db.repositories.glossary_repository import PostgresGlossaryRepository
from storage import (
    get_glossary_term_candidate_record,
    get_glossary_term_record,
    list_glossary_term_candidate_records,
    list_glossary_term_records,
    update_glossary_term_candidate_status_record,
    upsert_glossary_term_candidate_record,
    upsert_glossary_term_record,
)


GLOSSARY_SCHEMA_VERSION = "realytics-glossary-term-v1"
GLOSSARY_LIST_SCHEMA_VERSION = "realytics-glossary-term-list-v1"
CANONICAL_FIELD_MAP_SCHEMA_VERSION = "realytics-canonical-field-map-v1"
GLOSSARY_CANDIDATE_LIST_SCHEMA_VERSION = "realytics-glossary-term-candidate-list-v1"
GLOSSARY_CANDIDATE_CAPTURE_SCHEMA_VERSION = "realytics-glossary-term-candidate-capture-v1"
GLOSSARY_CANDIDATE_REVIEW_SCHEMA_VERSION = "realytics-glossary-term-candidate-review-v1"
GLOSSARY_STANDARD_IMPORT_SCHEMA_VERSION = "realytics-term-standard-import-v1"
GLOSSARY_NORMALIZE_SCHEMA_VERSION = "realytics-term-standard-normalize-v1"
GLOSSARY_SEED_FILE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "real_estate_glossary_seed.json"
TERM_STANDARD_SEED_ENV = "REALYTICS_TERM_STANDARD_SEED_FILE"

ALLOWED_REVIEW_STATUSES = {
    "approved",
    "pending_review",
    "needs_revision",
    "rejected",
    "free_text",
    "deprecated",
}

STATUSES_REQUIRING_CANONICAL_FIELD = {"approved", "pending_review", "needs_revision"}
# Candidate resolutions mirror the AI triage decisions: new-term(approved),
# same-as(merged), free-text(free_text), reject(rejected), plus needs_revision.
CANDIDATE_REVIEW_STATUSES = {"approved", "rejected", "needs_revision", "merged", "free_text"}

_CANONICAL_FIELD_HINTS = {
    "ltv": "pf_metrics.ltv",
    "ltc": "pf_metrics.ltc",
    "dscr": "pf_metrics.dscr",
    "pnu": "land.pnu",
    "max minus": "cashFlow.maxMinus",
    "exit 분양률": "scenario.exitSalesRate",
    "exit분양률": "scenario.exitSalesRate",
    "분양수입": "feasibility_input.total_revenue",
    "총분양수입": "feasibility_input.total_revenue",
    "총사업비": "feasibility_input.total_cost",
    "대지면적": "land.area_sqm",
    "연면적": "architectural_plan.gross_floor_area_sqm",
    "개별공시지가": "landEvidence.officialLandPrice.pricePerSqm",
    "공시지가": "landEvidence.officialLandPrice.pricePerSqm",
    "책임준공": "credit_enhancement.responsible_completion",
    "채무인수": "credit_enhancement.debt_assumption",
    "관리형토지신탁": "legal.trust.managementLandTrust",
    "우선수익권": "legal.trust.beneficialCertificate",
    "covenant": "legal.covenants",
    "eod": "legal.eodTriggers",
    "기한이익상실": "legal.eodTriggers",
    "cap rate": "real_asset.capRate",
    "noi": "real_asset.noi",
    "미분양 ltv": "scenario.unsoldLtv",
}


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
    repository: PostgresGlossaryRepository | None = None,
) -> Dict[str, Any]:
    if repository is None:
        ensure_seed_glossary_terms()
    terms = (repository.list_terms if repository is not None else list_glossary_term_records)(
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


def get_glossary_term_use_case(
    term_id: str, *, repository: PostgresGlossaryRepository | None = None,
) -> Dict[str, Any] | None:
    if repository is not None:
        return repository.get_term(term_id)
    ensure_seed_glossary_terms()
    return get_glossary_term_record(term_id)


def upsert_glossary_term_use_case(payload: Dict[str, Any], *, actor: str = "anonymous") -> Dict[str, Any]:
    ensure_seed_glossary_terms()
    current = get_glossary_term_record(payload.get("termId") or payload.get("term_id") or "")
    normalized = _normalize_glossary_term(payload, actor=actor, current=current)
    return upsert_glossary_term_record(normalized)


def resolve_glossary_term_use_case(
    term: str, *, include_pending: bool = False, repository: PostgresGlossaryRepository | None = None,
) -> Dict[str, Any]:
    if repository is None:
        ensure_seed_glossary_terms()
    normalized = _normalize_match_text(term)
    candidates: list[Dict[str, Any]] = []
    for item in repository.list_terms() if repository is not None else list_glossary_term_records():
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


def canonical_field_map_use_case(*, repository: PostgresGlossaryRepository | None = None) -> Dict[str, Any]:
    if repository is None:
        ensure_seed_glossary_terms()
    field_map: Dict[str, Dict[str, Any]] = {}
    category_counts: Dict[str, int] = {}
    term_count = 0
    for term in (repository.list_terms if repository is not None else list_glossary_term_records)(review_status="approved"):
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


def import_term_standard_seed_use_case(
    payload: Dict[str, Any],
    *,
    actor: str = "system_import",
    replace_existing: bool = False,
) -> Dict[str, Any]:
    """Import REALYTICS term-standard seed packs into the runtime glossary.

    The v3 term-standard database is broader than the app's automation field map.
    Known automation fields keep their domain canonical fields; the rest are
    stored under ``term_standard.<term_id>`` while preserving v3 operational
    metadata for routing, report, calculation, and risk use.
    """
    terms = _terms_from_seed_payload(payload)
    imported: list[Dict[str, Any]] = []
    skipped = 0
    for term in terms:
        current = get_glossary_term_record(term.get("termId") or "")
        if current and not replace_existing:
            skipped += 1
            continue
        imported.append(upsert_glossary_term_record(_normalize_glossary_term(term, actor=actor, current=current)))
    return {
        "schemaVersion": GLOSSARY_STANDARD_IMPORT_SCHEMA_VERSION,
        "sourceSchemaVersion": payload.get("schemaVersion") or (payload.get("metadata") or {}).get("version"),
        "sourceMetadata": payload.get("metadata") or {},
        "replaceExisting": replace_existing,
        "termCount": len(terms),
        "importedCount": len(imported),
        "skippedCount": skipped,
        "sampleTerms": imported[:5],
    }


def normalize_glossary_surfaces_use_case(
    items: list[Dict[str, Any]],
    *,
    project_id: str | None = None,
    run_id: str | None = None,
    min_suggestion_score: float = 0.72,
) -> Dict[str, Any]:
    ensure_seed_glossary_terms()
    approved = list_glossary_term_records(review_status="approved")
    index = _surface_index(approved)
    results: list[Dict[str, Any]] = []
    for raw in items or []:
        surface = str(raw.get("surface") or raw.get("rawTerm") or raw.get("term") or "").strip()
        if not surface:
            continue
        normalized = _normalize_match_text(surface)
        exact = index.get(normalized)
        if exact:
            results.append(
                {
                    "surface": surface,
                    "action": "matched",
                    "matchType": "exact_alias",
                    "confidence": 1.0,
                    "term": exact,
                    "candidate": None,
                }
            )
            continue

        nearest, score = _nearest_term(surface, approved)
        candidate_payload = {
            "surface": surface,
            "termSurface": build_term_surface_record(
                surface,
                source_field=raw.get("sourceField"),
                source_locator=raw.get("sourceLocator") or {},
                canonical_field=raw.get("canonicalField"),
                match_type="fuzzy_suggestion" if score >= min_suggestion_score else "unmapped",
                confidence=round(score, 4),
            ),
            "projectId": project_id or raw.get("projectId"),
            "runId": run_id or raw.get("runId"),
            "sourceField": raw.get("sourceField"),
            "sourceLocator": raw.get("sourceLocator") or {},
            "confidence": round(score, 4),
            "status": "pending_review",
            "category": raw.get("category") or (nearest or {}).get("category") or _candidate_category(raw.get("sourceField")),
            "suggestedTermId": (nearest or {}).get("termId") if score >= min_suggestion_score else None,
            "suggestedCanonicalField": (nearest or {}).get("canonicalField") if score >= min_suggestion_score else raw.get("canonicalField"),
            "nearestTerm": {
                "termId": (nearest or {}).get("termId"),
                "preferredTerm": (nearest or {}).get("preferredTerm"),
                "displayNameKo": (nearest or {}).get("displayNameKo"),
                "canonicalField": (nearest or {}).get("canonicalField"),
                "similarityScore": round(score, 4),
            } if nearest else None,
            "contextExcerpt": raw.get("context") or raw.get("contextExcerpt"),
        }
        candidate = upsert_glossary_term_candidate_record(candidate_payload)
        results.append(
            {
                "surface": surface,
                "action": "candidate_created",
                "matchType": "fuzzy_suggestion" if score >= min_suggestion_score else "unmapped",
                "confidence": round(score, 4),
                "term": None,
                "candidate": candidate,
            }
        )
    return {
        "schemaVersion": GLOSSARY_NORMALIZE_SCHEMA_VERSION,
        "projectId": project_id,
        "runId": run_id,
        "inputCount": len(items or []),
        "matchedCount": len([r for r in results if r["action"] == "matched"]),
        "candidateCount": len([r for r in results if r["action"] == "candidate_created"]),
        "results": results,
    }


def list_glossary_candidates_use_case(
    *,
    query: str | None = None,
    status: str | None = None,
    project_id: str | None = None,
    category: str | None = None,
    limit: int = 100,
    repository: PostgresGlossaryRepository | None = None,
) -> Dict[str, Any]:
    candidates = (repository.list_candidates if repository is not None else list_glossary_term_candidate_records)(
        query=query,
        status=status,
        project_id=project_id,
        category=category,
        limit=limit,
    )
    return {
        "schemaVersion": GLOSSARY_CANDIDATE_LIST_SCHEMA_VERSION,
        "filters": {
            "query": query,
            "status": status,
            "projectId": project_id,
            "category": category,
            "limit": limit,
        },
        "count": len(candidates),
        "candidates": candidates,
    }


def capture_glossary_candidates_use_case(
    annotations: list[Dict[str, Any]],
    *,
    project_id: str | None = None,
    run_id: str | None = None,
) -> Dict[str, Any]:
    captured: list[Dict[str, Any]] = []
    for annotation in annotations or []:
        if annotation.get("status") != "unmapped_candidate":
            continue
        surface = str(annotation.get("surface") or "").strip()
        if not surface:
            continue
        captured.append(
            upsert_glossary_term_candidate_record(
                {
                    "surface": surface,
                    "termSurface": build_term_surface_record(
                        surface,
                        source_field=annotation.get("sourceField"),
                        source_locator=annotation.get("sourceLocator") or {},
                        canonical_field=annotation.get("canonicalField"),
                        match_type=annotation.get("matchType") or "unmapped_candidate",
                        confidence=annotation.get("confidence"),
                    ),
                    "projectId": project_id,
                    "runId": run_id,
                    "sourceField": annotation.get("sourceField"),
                    "sourceLocator": annotation.get("sourceLocator") or {},
                    "confidence": annotation.get("confidence"),
                    "status": "pending_review",
                    "category": _candidate_category(annotation.get("sourceField")),
                }
            )
        )
    return {
        "schemaVersion": GLOSSARY_CANDIDATE_CAPTURE_SCHEMA_VERSION,
        "projectId": project_id,
        "runId": run_id,
        "capturedCount": len(captured),
        "candidates": captured,
    }


def capture_unmapped_term_surface_candidates_use_case(
    term_surfaces: list[Dict[str, Any]],
    *,
    project_id: str | None = None,
    run_id: str | None = None,
    source: str = "evidence_graph",
    tenant_id: str = "default",
    created_by: str = "system_learning_loop",
) -> Dict[str, Any]:
    """Route unmapped DB-to-Pod term surfaces into the glossary review queue.

    Only surfaces without both a standard ``termId`` and ``canonicalField`` are
    considered. Exact approved aliases are resolved by the normalize use case;
    genuinely new expressions become pending glossary candidates.
    """
    items: list[Dict[str, Any]] = []
    for surface in term_surfaces or []:
        if not isinstance(surface, dict):
            continue
        if surface.get("termId") and surface.get("canonicalField"):
            continue
        raw_text = str(surface.get("rawText") or surface.get("surface") or "").strip()
        if not raw_text:
            continue
        items.append(
            {
                "surface": raw_text,
                "sourceField": surface.get("sourceField"),
                "sourceLocator": surface.get("sourceLocator") or {},
                "canonicalField": surface.get("canonicalField"),
                "context": surface.get("contextExcerpt") or surface.get("sourceField") or source,
                "category": surface.get("category") or _candidate_category(surface.get("sourceField")),
                "projectId": project_id,
                "runId": run_id,
            }
        )
    normalized = normalize_glossary_surfaces_use_case(
        items,
        project_id=project_id,
        run_id=run_id,
    )
    learning_candidate_ids: list[str] = []
    learning_service = LearningLifecycleService(OperationalRepository())
    now = datetime.now(timezone.utc)
    for result in normalized.get("results", []):
        candidate = result.get("candidate") if isinstance(result, dict) else None
        if not isinstance(candidate, dict) or not candidate.get("candidateId"):
            continue
        source_locator = candidate.get("sourceLocator") or {}
        evidence_id = source_locator.get("evidenceId") if isinstance(source_locator, dict) else None
        learning_id = f"learning:glossary:{candidate['candidateId']}"
        learning_candidate = LearningCandidate(
            candidate_id=learning_id,
            tenant_id=tenant_id or "default",
            project_id=project_id,
            candidate_type="glossary_term_mapping",
            target_key=str(candidate.get("surface") or candidate["candidateId"]),
            proposed_change={
                "glossaryCandidateId": candidate["candidateId"],
                "surface": candidate.get("surface"),
                "suggestedTermId": candidate.get("suggestedTermId"),
                "suggestedCanonicalField": candidate.get("suggestedCanonicalField"),
                "sourceLocator": source_locator,
            },
            evidence_ids=[str(evidence_id)] if evidence_id else [],
            source_run_ids=[str(run_id)] if run_id else [],
            created_by=created_by,
            created_at=now,
            updated_at=now,
            metadata={
                "source": source,
                "promotionPolicy": "human_evaluation_canary_required",
                "autoPromotionAllowed": False,
                "correlationId": str(uuid4()),
            },
        )
        try:
            learning_service.create_candidate(learning_candidate)
            learning_candidate_ids.append(learning_id)
        except LearningLifecycleError:
            # A source run or explicit Evidence ID is mandatory. The glossary
            # review queue remains valid, but it is not silently promoted into
            # the governed learning lifecycle without provenance.
            continue
    return {
        "schemaVersion": GLOSSARY_CANDIDATE_CAPTURE_SCHEMA_VERSION,
        "source": source,
        "projectId": project_id,
        "runId": run_id,
        "surfaceCount": len(term_surfaces or []),
        "unmappedSurfaceCount": len(items),
        "matchedCount": normalized.get("matchedCount", 0),
        "capturedCount": normalized.get("candidateCount", 0),
        "learningCandidateCount": len(learning_candidate_ids),
        "learningCandidateIds": learning_candidate_ids,
        "results": normalized.get("results", []),
        "candidates": [
            result.get("candidate")
            for result in normalized.get("results", [])
            if result.get("candidate")
        ],
    }


def review_glossary_candidate_use_case(
    candidate_id: str,
    payload: Dict[str, Any],
    *,
    actor: str = "anonymous",
) -> Dict[str, Any]:
    candidate = get_glossary_term_candidate_record(candidate_id)
    if not candidate:
        raise KeyError("glossary term candidate not found")
    status = payload.get("status") or payload.get("reviewStatus")
    if status not in CANDIDATE_REVIEW_STATUSES:
        raise ValueError(f"status must be one of {sorted(CANDIDATE_REVIEW_STATUSES)}")

    term = None
    if status == "approved":
        canonical_field = payload.get("canonicalField") or candidate.get("suggestedCanonicalField")
        if not canonical_field:
            raise ValueError("canonicalField is required to approve a candidate")
        preferred_term = payload.get("preferredTerm") or candidate.get("surface")
        term_payload = {
            "termId": payload.get("termId") or _term_id_from_preferred(preferred_term),
            "preferredTerm": preferred_term,
            "displayNameKo": payload.get("displayNameKo") or preferred_term,
            "displayNameEn": payload.get("displayNameEn"),
            "aliases": _distinct_strings([candidate.get("surface"), *(payload.get("aliases") or [])]),
            "definitionKo": payload.get("definitionKo") or "",
            "canonicalField": canonical_field,
            "canonicalPath": payload.get("canonicalPath") or canonical_field,
            "category": payload.get("category") or candidate.get("category") or "unclassified",
            "dataType": payload.get("dataType") or "text",
            "unit": payload.get("unit"),
            "sourceExamples": [
                {
                    "surface": candidate.get("surface"),
                    "sourceField": candidate.get("sourceField"),
                    "sourceLocator": candidate.get("sourceLocator"),
                    "projectId": candidate.get("projectId"),
                    "runId": candidate.get("runId"),
                }
            ],
            "reviewStatus": "approved",
            "reviewReason": payload.get("reason") or "approved from glossary candidate queue",
        }
        term = upsert_glossary_term_use_case(term_payload, actor=actor)

    elif status == "merged":
        # Same-as: fold the candidate surface into an EXISTING term as an alias so
        # the next normalize() resolves it exactly instead of re-queueing it.
        target_term_id = payload.get("termId") or candidate.get("suggestedTermId")
        if not target_term_id:
            raise ValueError("termId is required to merge a candidate into an existing term")
        existing = get_glossary_term_record(str(target_term_id))
        if not existing:
            raise KeyError(f"target glossary term not found: {target_term_id}")
        merged_aliases = _distinct_strings([*(existing.get("aliases") or []), candidate.get("surface")])
        source_examples = list(existing.get("sourceExamples") or [])
        source_examples.append(
            {
                "surface": candidate.get("surface"),
                "sourceField": candidate.get("sourceField"),
                "sourceLocator": candidate.get("sourceLocator"),
                "projectId": candidate.get("projectId"),
                "runId": candidate.get("runId"),
            }
        )
        term = upsert_glossary_term_use_case(
            {
                **existing,
                "aliases": merged_aliases,
                "sourceExamples": source_examples,
                "reviewReason": payload.get("reason")
                or f"merged candidate '{candidate.get('surface')}' as alias",
            },
            actor=actor,
        )

    updated = update_glossary_term_candidate_status_record(
        candidate_id,
        status=status,
        actor=actor,
        reason=payload.get("reason") or payload.get("reviewReason"),
        term_id=(term or {}).get("termId") or payload.get("termId"),
        canonical_field=(term or {}).get("canonicalField") or payload.get("canonicalField"),
    )
    return {
        "schemaVersion": GLOSSARY_CANDIDATE_REVIEW_SCHEMA_VERSION,
        "candidate": updated,
        "term": term,
    }


def _load_seed_terms() -> list[Dict[str, Any]]:
    configured = os.environ.get(TERM_STANDARD_SEED_ENV)
    seed_file = Path(configured) if configured else GLOSSARY_SEED_FILE
    with seed_file.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    return _terms_from_seed_payload(payload)


def _terms_from_seed_payload(payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    terms = payload.get("terms") or []
    if not terms:
        return []
    first = terms[0]
    if "canonical_ko" not in first:
        return list(terms)

    synonym_mapping: dict[str, list[str]] = {}
    raw_mapping = payload.get("synonym_mapping") or {}
    if isinstance(raw_mapping, dict):
        for raw_term, term_id in raw_mapping.items():
            synonym_mapping.setdefault(str(term_id), []).append(str(raw_term))
    elif isinstance(raw_mapping, list):
        for row in raw_mapping:
            if isinstance(row, dict):
                synonym_mapping.setdefault(str(row.get("term_id") or ""), []).append(str(row.get("raw_term") or ""))

    return [_external_standard_term_to_glossary(term, synonym_mapping=synonym_mapping) for term in terms]


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
        "standardUsageRule": payload.get("standardUsageRule") or payload.get("standard_usage_rule"),
        "formulaOrLogic": payload.get("formulaOrLogic") or payload.get("formula_or_logic"),
        "criticality": payload.get("criticality"),
        "confidence": payload.get("confidence"),
        "sourceKey": payload.get("sourceKey") or payload.get("source_key"),
        "sourceDoc": payload.get("sourceDoc") or payload.get("source_doc"),
        "realyticsModule": payload.get("realyticsModule") or payload.get("realytics_module"),
        "agentModule": payload.get("agentModule") or payload.get("agent_module"),
        "reportSection": payload.get("reportSection") or payload.get("report_section"),
        "promptTemplateId": payload.get("promptTemplateId") or payload.get("prompt_template_id"),
        "calculationRequired": payload.get("calculationRequired") or payload.get("calculation_required"),
        "dataSourceRequired": payload.get("dataSourceRequired") or payload.get("data_source_required"),
        "validationRule": payload.get("validationRule") or payload.get("validation_rule"),
        "riskScore": payload.get("riskScore") if payload.get("riskScore") is not None else payload.get("risk_score"),
        "riskTier": payload.get("riskTier") or payload.get("risk_tier"),
        "productionReady": payload.get("productionReady") or payload.get("production_ready"),
        "reportSentenceTemplate": payload.get("reportSentenceTemplate") or payload.get("report_sentence_template"),
        "termStandard": payload.get("termStandard") or payload.get("term_standard") or {},
        "reviewHistory": history,
    }


def _external_standard_term_to_glossary(
    term: Dict[str, Any],
    *,
    synonym_mapping: dict[str, list[str]],
) -> Dict[str, Any]:
    term_id = str(term.get("term_id") or "").strip()
    canonical_ko = str(term.get("canonical_ko") or term_id).strip()
    canonical_en = str(term.get("canonical_en") or "").strip()
    aliases = _distinct_strings(
        [
            canonical_ko,
            canonical_en,
            term.get("abbreviation"),
            *_split_synonyms(term.get("synonyms")),
            *synonym_mapping.get(term_id, []),
        ]
    )
    return {
        "termId": term_id,
        "preferredTerm": canonical_ko or term_id,
        "displayNameKo": canonical_ko or term_id,
        "displayNameEn": canonical_en,
        "aliases": aliases,
        "definitionKo": term.get("definition_ko") or "",
        "canonicalField": _canonical_field_for_external_term(term),
        "canonicalPath": _canonical_field_for_external_term(term),
        "category": _category_for_external_term(term),
        "dataType": term.get("data_type") or "text",
        "unit": term.get("unit"),
        "sourceExamples": _source_examples_for_external_term(term),
        "reviewStatus": _review_status_for_external_term(term),
        "reviewedBy": term.get("owner") or "REALYTICS AI LAB",
        "reviewedAt": term.get("updated_at") or term.get("created_at"),
        "reviewReason": "imported from REALYTICS term-standard seed",
        "version": _version_number(term.get("version")),
        "createdAt": term.get("created_at"),
        "updatedAt": term.get("updated_at") or term.get("created_at"),
        "standardUsageRule": term.get("standard_usage_rule"),
        "formulaOrLogic": term.get("formula_or_logic"),
        "criticality": term.get("criticality"),
        "confidence": term.get("confidence"),
        "sourceKey": term.get("source_key"),
        "sourceDoc": term.get("source_doc"),
        "realyticsModule": term.get("realytics_module"),
        "agentModule": term.get("agent_module"),
        "reportSection": term.get("report_section"),
        "promptTemplateId": term.get("prompt_template_id"),
        "calculationRequired": term.get("calculation_required"),
        "dataSourceRequired": term.get("data_source_required"),
        "validationRule": term.get("validation_rule"),
        "riskScore": term.get("risk_score"),
        "riskTier": term.get("risk_tier"),
        "productionReady": term.get("production_ready"),
        "reportSentenceTemplate": term.get("report_sentence_template"),
        "termStandard": {
            "seedFormat": "realytics-term-standard-v3",
            "categoryL1": term.get("category_l1"),
            "categoryL2": term.get("category_l2"),
            "sourceKey": term.get("source_key"),
            "sourceDoc": term.get("source_doc"),
        },
    }


def _split_synonyms(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return [part.strip() for part in str(value or "").split("|") if part.strip()]


def _category_for_external_term(term: Dict[str, Any]) -> str:
    category_l1 = str(term.get("category_l1") or "").strip()
    category_l2 = str(term.get("category_l2") or "").strip()
    module = str(term.get("realytics_module") or "").strip()
    return " / ".join(part for part in [module, category_l1, category_l2] if part) or "term_standard"


def _canonical_field_for_external_term(term: Dict[str, Any]) -> str:
    explicit = term.get("canonicalField") or term.get("canonical_field")
    if explicit:
        return str(explicit)
    surfaces = [
        term.get("canonical_ko"),
        term.get("canonical_en"),
        term.get("abbreviation"),
        *_split_synonyms(term.get("synonyms")),
    ]
    for surface in surfaces:
        key = _normalize_match_text(surface)
        if key in _CANONICAL_FIELD_HINTS:
            return _CANONICAL_FIELD_HINTS[key]
    term_id = str(term.get("term_id") or "").lower().replace("-", "_")
    return f"term_standard.{term_id or _term_id_from_preferred(str(term.get('canonical_ko') or 'term'))}"


def _source_examples_for_external_term(term: Dict[str, Any]) -> list[Dict[str, Any]]:
    examples: list[Dict[str, Any]] = []
    if term.get("source_doc") or term.get("source_key"):
        examples.append(
            {
                "sourceType": "term_standard_seed",
                "sourceKey": term.get("source_key"),
                "sourceDoc": term.get("source_doc"),
                "locatorExample": f"term_id:{term.get('term_id')}",
            }
        )
    return examples


def _review_status_for_external_term(term: Dict[str, Any]) -> str:
    status = str(term.get("status") or "").lower()
    if status in {"approved", "active", "production", "ready"}:
        return "approved"
    if str(term.get("production_ready") or "").upper() == "Y":
        return "approved"
    return "pending_review"


def _version_number(value: Any) -> int:
    text = str(value or "1").strip().lower().lstrip("v")
    try:
        return int(float(text))
    except ValueError:
        return 1


def _surface_index(terms: list[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for term in terms:
        for value in [
            term.get("termId"),
            term.get("preferredTerm"),
            term.get("displayNameKo"),
            term.get("displayNameEn"),
            *(term.get("aliases") or []),
        ]:
            if value:
                index.setdefault(_normalize_match_text(value), term)
    return index


def _nearest_term(surface: str, terms: list[Dict[str, Any]]) -> tuple[Dict[str, Any] | None, float]:
    query = _normalize_match_text(surface)
    best: Dict[str, Any] | None = None
    best_score = 0.0
    for term in terms:
        surfaces = [
            term.get("preferredTerm"),
            term.get("displayNameKo"),
            term.get("displayNameEn"),
            *(term.get("aliases") or []),
        ]
        for item in surfaces:
            candidate = _normalize_match_text(item)
            if not candidate:
                continue
            score = SequenceMatcher(None, query, candidate).ratio()
            if query and candidate and (query in candidate or candidate in query):
                score = max(score, min(len(query), len(candidate)) / max(len(query), len(candidate)))
            if score > best_score:
                best = term
                best_score = score
    return best, best_score


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
    return normalize_match_text(value)


def _candidate_category(source_field: Any) -> str:
    text = str(source_field or "").lower()
    if text.startswith("land"):
        return "land"
    if text.startswith("regulation"):
        return "regulation"
    if text.startswith("legal") or "covenant" in text or "eod" in text:
        return "legal"
    if text.startswith("market") or "pricing" in text:
        return "marketability"
    if text.startswith("cashflow") or text.startswith("scenario"):
        return "cashflow"
    if text.startswith("pf_") or text.startswith("pf.") or "tranche" in text:
        return "pf_structure"
    if text.startswith("feasibility") or text.startswith("cost"):
        return "feasibility"
    return "unclassified"
