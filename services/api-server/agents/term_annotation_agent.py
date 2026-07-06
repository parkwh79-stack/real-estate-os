from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from core.text_normalization import build_term_surface_record, normalize_match_text

TERM_ANNOTATION_SCHEMA_VERSION = "realytics-term-annotation-v1"

# Free-text, term-bearing fields worth scanning for controlled-vocabulary surfaces
# (e.g. "책임준공", "Tr.A", "Cap Rate"). List fields are scanned per item.
_COVENANT_LIST_FIELDS = ("claim_preservation", "eod", "drawdown_conditions", "execution_order", "covenants")
_MARKET_NOTE_FIELDS = ("exitStrategyNote", "capRateNote", "note", "pricingComment", "riskMemo")
_TEXT_SCAN_ROOTS = (
    "land",
    "landLedger",
    "landEvidence",
    "regulation",
    "legal",
    "credit_enhancement",
    "market",
    "marketability",
    "real_asset",
    "sales_assumptions",
    "feasibility_input",
    "cost",
    "schedule",
    "cashFlow",
    "scenario",
    "pf_metrics",
)


def _norm(value: Any) -> str:
    return normalize_match_text(value)


class TermAnnotationAgent:
    """Attach controlled-vocabulary identity to extracted terms.

    Turns plain extracted text/fields into ``termId + canonicalField + sourceLocator``
    annotations using the approved glossary, so domain terms (real-estate dev
    finance, real-asset finance, law) flow into the Phase 11 report and the
    Evidence Graph / audit trail. Unrecognised domain phrases are emitted as
    ``unmapped_candidate`` so they can be routed to glossary review.
    """

    analysis_type = "term_annotation_v1"

    def annotate(self, parsed_data: Dict[str, Any], glossary_terms: List[Dict[str, Any]]) -> Dict[str, Any]:
        approved = [
            term for term in (glossary_terms or [])
            if term.get("reviewStatus") == "approved" and term.get("canonicalField")
        ]
        surface_index: Dict[str, Tuple[Dict[str, Any], str]] = {}
        canon_index: Dict[str, Dict[str, Any]] = {}
        for term in approved:
            surfaces = [
                term.get("termId"),
                term.get("preferredTerm"),
                term.get("displayNameKo"),
                term.get("displayNameEn"),
                *(term.get("aliases") or []),
            ]
            for surface in surfaces:
                if surface:
                    surface_index.setdefault(_norm(surface), (term, str(surface)))
            for key in (term.get("canonicalField"), term.get("canonicalPath")):
                if key:
                    canon_index.setdefault(str(key), term)

        source_locators: Dict[str, Any] = parsed_data.get("source_locators") or {}
        annotations: List[Dict[str, Any]] = []
        seen: set[Tuple[str, str]] = set()

        # Mechanism 1: a located field whose path IS a glossary canonical field/path.
        for field_path, locator in source_locators.items():
            term = canon_index.get(field_path)
            if not term:
                continue
            key = (term["termId"], field_path)
            if key in seen:
                continue
            seen.add(key)
            annotations.append(self._annotation(term, surface=field_path, source_field=field_path,
                                                 locator=locator, match_type="canonical_field", confidence=0.95))

        # Mechanism 2: a parsed canonical field exists even when the parser has not
        # attached a locator yet. This broadens coverage to land, regulation, cost,
        # schedule, legal, PF, and real-asset fields while marking provenance as a
        # parsed field path until document page / Excel cell / API-row evidence arrives.
        for canonical_field, term in canon_index.items():
            value = self._get_path(parsed_data, canonical_field)
            if value in (None, "", [], {}):
                continue
            key = (term["termId"], canonical_field)
            if key in seen:
                continue
            seen.add(key)
            locator = source_locators.get(canonical_field) or {
                "fieldName": canonical_field,
                "sourceType": "parsed_field_path",
                "textExcerpt": self._excerpt(value),
            }
            annotations.append(self._annotation(
                term,
                surface=canonical_field,
                source_field=canonical_field,
                locator=locator,
                match_type="canonical_field_value",
                confidence=0.9,
            ))

        # Mechanism 3: glossary surface (alias) found inside a term-bearing text field.
        for field_path, text in self._scan_targets(parsed_data):
            normalized = _norm(text)
            if not normalized:
                continue
            matched_any = False
            for surface_norm, (term, original) in surface_index.items():
                if len(surface_norm) < 2:
                    continue
                if not self._surface_in_text(surface_norm, normalized):
                    continue
                key = (term["termId"], field_path)
                if key in seen:
                    matched_any = True
                    continue
                seen.add(key)
                matched_any = True
                locator = source_locators.get(field_path) or {
                    "fieldName": field_path,
                    "sourceType": "parsed_field_path",
                    "textExcerpt": str(text)[:120],
                }
                annotations.append(self._annotation(term, surface=original, source_field=field_path,
                                                     locator=locator, match_type="alias_text", confidence=0.85))
            if not matched_any and self._should_emit_unmapped(field_path):
                annotations.append({
                    **build_term_surface_record(
                        str(text)[:120],
                        source_field=field_path,
                        source_locator=source_locators.get(field_path) or {"fieldName": field_path, "sourceType": "parsed_field_path"},
                        match_type="none",
                        confidence=0.0,
                    ),
                    "surface": str(text)[:120],
                    "termId": None,
                    "canonicalField": None,
                    "sourceField": field_path,
                    "sourceLocator": source_locators.get(field_path) or {"fieldName": field_path, "sourceType": "parsed_field_path"},
                    "matchType": "none",
                    "status": "unmapped_candidate",
                    "confidence": 0.0,
                })

        return {
            "schemaVersion": TERM_ANNOTATION_SCHEMA_VERSION,
            "analysisType": self.analysis_type,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "annotations": annotations,
            "summary": self._summary(annotations),
        }

    @staticmethod
    def _surface_in_text(surface_norm: str, text_norm: str) -> bool:
        # Short ASCII surfaces need a token match to avoid false positives inside words.
        if surface_norm.isascii() and len(surface_norm) <= 3:
            return surface_norm in text_norm.split()
        return surface_norm in text_norm

    def _scan_targets(self, parsed_data: Dict[str, Any]) -> Iterable[Tuple[str, str]]:
        for index, tranche in enumerate(parsed_data.get("pf_structure") or []):
            if isinstance(tranche, dict):
                name = tranche.get("tranche") or tranche.get("name")
                if name:
                    yield (f"pf_structure[{index}].tranche", str(name))

        covenants = parsed_data.get("covenants") or {}
        if isinstance(covenants, dict):
            for field in _COVENANT_LIST_FIELDS:
                items = covenants.get(field)
                if isinstance(items, list):
                    for item_index, item in enumerate(items):
                        if isinstance(item, str) and item.strip():
                            yield (f"covenants.{field}[{item_index}]", item)
                        elif isinstance(item, dict):
                            label = item.get("label") or item.get("title") or item.get("name")
                            if label:
                                yield (f"covenants.{field}[{item_index}]", str(label))

        market = parsed_data.get("market") or {}
        if isinstance(market, dict):
            for field in _MARKET_NOTE_FIELDS:
                value = market.get(field)
                if isinstance(value, str) and value.strip():
                    yield (f"market.{field}", value)

        for root in _TEXT_SCAN_ROOTS:
            if root in parsed_data:
                yield from self._walk_text_fields(root, parsed_data[root])

    @staticmethod
    def _should_emit_unmapped(field_path: str) -> bool:
        lowered = field_path.lower()
        candidate_markers = (
            "note",
            "memo",
            "comment",
            "summary",
            "reason",
            "risk",
            "condition",
            "covenant",
            "eod",
            "trigger",
            "strategy",
            "claim_preservation",
            "drawdown_conditions",
            "execution_order",
        )
        return any(marker in lowered for marker in candidate_markers)

    def _walk_text_fields(self, prefix: str, value: Any) -> Iterator[Tuple[str, str]]:
        if isinstance(value, dict):
            for key, child in value.items():
                yield from self._walk_text_fields(f"{prefix}.{key}", child)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from self._walk_text_fields(f"{prefix}[{index}]", child)
        elif isinstance(value, str) and value.strip():
            yield (prefix, value)

    @staticmethod
    def _get_path(data: Dict[str, Any], path: str) -> Any:
        current: Any = data
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    @staticmethod
    def _excerpt(value: Any) -> str:
        return str(value)[:120]

    @staticmethod
    def _annotation(term: Dict[str, Any], *, surface: str, source_field: str,
                    locator: Any, match_type: str, confidence: float) -> Dict[str, Any]:
        return {
            **build_term_surface_record(
                surface,
                source_field=source_field,
                source_locator=locator,
                term_id=term.get("termId"),
                canonical_field=term.get("canonicalField"),
                match_type=match_type,
                confidence=confidence,
            ),
            "surface": surface,
            "termId": term.get("termId"),
            "preferredTerm": term.get("preferredTerm"),
            "displayNameKo": term.get("displayNameKo"),
            "canonicalField": term.get("canonicalField"),
            "canonicalPath": term.get("canonicalPath"),
            "category": term.get("category"),
            "dataType": term.get("dataType"),
            "unit": term.get("unit"),
            "sourceField": source_field,
            "sourceLocator": locator,
            "matchType": match_type,
            "status": "mapped",
            "confidence": confidence,
        }

    @staticmethod
    def _summary(annotations: List[Dict[str, Any]]) -> Dict[str, Any]:
        mapped = [a for a in annotations if a.get("status") == "mapped"]
        unmapped = [a for a in annotations if a.get("status") == "unmapped_candidate"]
        match_counts: Dict[str, int] = {}
        for annotation in annotations:
            match_counts[annotation["matchType"]] = match_counts.get(annotation["matchType"], 0) + 1
        return {
            "mappedCount": len(mapped),
            "unmappedCandidateCount": len(unmapped),
            "distinctTerms": len({a["termId"] for a in mapped if a.get("termId")}),
            "distinctCanonicalFields": len({a["canonicalField"] for a in mapped if a.get("canonicalField")}),
            "matchTypeCounts": match_counts,
        }
