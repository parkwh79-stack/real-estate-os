from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from core.text_normalization import build_term_surface_record


PARSER_FIELD_PROVENANCE_SCHEMA_VERSION = "realytics-parser-field-provenance-v1"

NON_MARKET_PARSER_DOMAINS = {
    "project",
    "land",
    "regulation",
    "architectural_plan",
    "sales_assumptions",
    "cost_assumptions",
    "feasibility_input",
    "pf_structure",
    "developer_info",
    "contractor_info",
    "schedule",
    "covenants",
}

MARKET_ONLY_PATH_PREFIXES = (
    "market.",
    "document_market_evidence.",
    "sales_assumptions.product_mix",
    "sales_assumptions.productMix",
)

_INDEX_RE = re.compile(r"\[\d+\]")

PARSER_FIELD_TERM_MAP: Dict[str, Dict[str, str]] = {
    "land.address": {"termId": "project_site", "canonicalField": "land.address"},
    "land.pnu": {"termId": "pnu", "canonicalField": "land.pnu"},
    "land.area_sqm": {"termId": "land_area_sqm", "canonicalField": "land.area_sqm"},
    "land.officialLandPricePerSqm": {
        "termId": "official_land_price",
        "canonicalField": "landEvidence.officialLandPrice.pricePerSqm",
    },
    "landEvidence.officialLandPrice.pricePerSqm": {
        "termId": "official_land_price",
        "canonicalField": "landEvidence.officialLandPrice.pricePerSqm",
    },
    "regulation.applied_far": {"termId": "floor_area_ratio", "canonicalField": "regulation.applied_far"},
    "regulation.legal_far": {"termId": "floor_area_ratio", "canonicalField": "regulation.applied_far"},
    "regulation.applied_bcr": {"termId": "building_coverage_ratio", "canonicalField": "regulation.applied_bcr"},
    "regulation.legal_bcr": {"termId": "building_coverage_ratio", "canonicalField": "regulation.applied_bcr"},
    "architectural_plan.gross_floor_area_sqm": {
        "termId": "gross_floor_area_sqm",
        "canonicalField": "architectural_plan.gross_floor_area_sqm",
    },
    "architectural_plan.unit_count": {"termId": "unit_count", "canonicalField": "architectural_plan.unit_count"},
    "sales_assumptions.avg_price_per_sqm": {
        "termId": "sales_price_per_py",
        "canonicalField": "sales_assumptions.avgPricePerPy",
    },
    "cost_assumptions.construction_cost_per_sqm": {
        "termId": "construction_cost",
        "canonicalField": "feasibility_input.construction_cost",
    },
    "feasibility_input.total_revenue": {
        "termId": "total_sales_revenue",
        "canonicalField": "feasibility_input.total_revenue",
    },
    "feasibility_input.total_cost": {
        "termId": "total_project_cost",
        "canonicalField": "feasibility_input.total_cost",
    },
    "feasibility_input.land_cost": {"termId": "land_cost", "canonicalField": "feasibility_input.land_cost"},
    "feasibility_input.construction_cost": {
        "termId": "construction_cost",
        "canonicalField": "feasibility_input.construction_cost",
    },
    "feasibility_input.finance_cost": {
        "termId": "finance_cost",
        "canonicalField": "feasibility_input.finance_cost",
    },
    "schedule.completion": {"termId": "completion", "canonicalField": "schedule.completion"},
    "contractor_info.name": {"termId": "contractor", "canonicalField": "contractor.name"},
    "developer_info.name": {"termId": "developer_borrower", "canonicalField": "developer.name"},
    "covenants.claim_preservation": {"termId": "covenant", "canonicalField": "legal.covenants"},
    "covenants.drawdown_conditions": {"termId": "covenant", "canonicalField": "legal.covenants"},
    "covenants.execution_order": {"termId": "waterfall", "canonicalField": "cashFlow.waterfall"},
    "covenants.eod": {"termId": "eod", "canonicalField": "legal.eodTriggers"},
}


def attach_parser_field_provenance(
    parsed_data: Dict[str, Any],
    *,
    upload_meta: Optional[Dict[str, Any]] = None,
    parser_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Attach field-level provenance coverage for non-market parser fields.

    The LLM extraction prompt asks for source locators. This function makes the
    result auditable even when the LLM omits some locators by recording coverage
    and missing fields without fabricating page/cell evidence.
    """
    source_locators = parsed_data.get("source_locators")
    if not isinstance(source_locators, dict):
        source_locators = {}

    fields = []
    term_surfaces = []
    for path, value in _iter_non_market_leaf_fields(parsed_data):
        locator = _locator_for_path(parsed_data, source_locators, path)
        term_surface = _term_surface_for_path(path, value, parsed_data, locator)
        term_surfaces.append(term_surface)
        fields.append(
            {
                "path": path,
                "domain": path.split(".", 1)[0].split("[", 1)[0],
                "valuePreview": _preview(value),
                "hasValue": not _empty(value),
                "sourceLocator": locator or {},
                "locatorStatus": "located" if locator else "missing",
                "locatorPrecision": _locator_precision(locator),
                "termId": term_surface.get("termId"),
                "canonicalField": term_surface.get("canonicalField"),
                "termSurface": term_surface,
            }
        )

    located = [field for field in fields if field["locatorStatus"] == "located"]
    domain_coverage = _domain_coverage(fields)
    coverage = {
        "status": _coverage_status(len(fields), len(located)),
        "fieldCount": len(fields),
        "locatedFieldCount": len(located),
        "missingFieldCount": len(fields) - len(located),
        "coverageRatio": round(len(located) / len(fields), 4) if fields else 1.0,
        "domains": domain_coverage,
    }

    summary = {
        "schemaVersion": PARSER_FIELD_PROVENANCE_SCHEMA_VERSION,
        "parserVersion": parser_version,
        "sourceDocument": upload_meta or {},
        "coverage": coverage,
        "fields": fields,
        "termSurfaces": term_surfaces,
        "missingFields": [
            {
                "path": field["path"],
                "domain": field["domain"],
                "valuePreview": field["valuePreview"],
                "termId": field.get("termId"),
                "canonicalField": field.get("canonicalField"),
                "termSurface": field.get("termSurface"),
            }
            for field in fields
            if field["locatorStatus"] == "missing"
        ],
        "policy": {
            "noFabrication": True,
            "note": (
                "Missing locator means the parsed value exists but has no explicit document page, "
                "table, row, API row, or Excel cell evidence yet."
            ),
        },
    }

    parsed_data.setdefault("_meta", {})
    parsed_data["_meta"]["parserFieldProvenance"] = summary
    parsed_data["parser_field_source_locators"] = {
        field["path"]: field["sourceLocator"]
        for field in fields
        if field["locatorStatus"] == "located"
    }
    parsed_data["parser_term_surfaces"] = term_surfaces
    parsed_data["parser_locator_coverage"] = coverage
    return parsed_data


def _iter_non_market_leaf_fields(data: Dict[str, Any]) -> Iterable[tuple[str, Any]]:
    for domain in sorted(NON_MARKET_PARSER_DOMAINS):
        value = data.get(domain)
        if _empty(value):
            continue
        yield from _iter_leaf_fields(value, domain)


def _iter_leaf_fields(value: Any, path: str) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"sourceLocator", "source_locator", "fieldSourceLocators", "fieldLocators"}:
                continue
            child_path = f"{path}.{key}"
            if _is_market_only_path(child_path):
                continue
            if _empty(child):
                continue
            yield from _iter_leaf_fields(child, child_path)
        return
    if isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            yield path, value
            return
        for index, child in enumerate(value):
            if _empty(child):
                continue
            yield from _iter_leaf_fields(child, f"{path}[{index}]")
        return
    yield path, value


def _locator_for_path(
    parsed_data: Dict[str, Any],
    source_locators: Dict[str, Any],
    path: str,
) -> Optional[Dict[str, Any]]:
    candidates = [
        source_locators.get(path),
        source_locators.get(path.replace("[0]", "[]")),
        _nested_record_locator(parsed_data, path),
    ]
    for candidate in candidates:
        normalized = _normalize_locator(candidate, path)
        if normalized:
            return normalized
    return None


def _nested_record_locator(parsed_data: Dict[str, Any], path: str) -> Optional[Dict[str, Any]]:
    parent_path = path.rsplit(".", 1)[0] if "." in path else path
    parent = _get_path(parsed_data, parent_path)
    if not isinstance(parent, dict):
        return None
    return parent.get("sourceLocator") or parent.get("source_locator")


def _get_path(data: Any, path: str) -> Any:
    current = data
    for part in _split_path(path):
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                return None
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
    return current


def _split_path(path: str) -> List[str | int]:
    parts: List[str | int] = []
    for raw_part in path.split("."):
        while "[" in raw_part and raw_part.endswith("]"):
            prefix, index_raw = raw_part[:-1].split("[", 1)
            if prefix:
                parts.append(prefix)
            try:
                parts.append(int(index_raw))
            except ValueError:
                parts.append(index_raw)
            raw_part = ""
        if raw_part:
            parts.append(raw_part)
    return parts


def _normalize_locator(locator: Any, path: str) -> Optional[Dict[str, Any]]:
    if not isinstance(locator, dict):
        return None
    if not any(value not in {None, "", 0} for value in locator.values()):
        return None
    normalized = dict(locator)
    normalized.setdefault("fieldName", path)
    normalized.setdefault("path", path)
    normalized.setdefault("sourceType", _infer_source_type(normalized))
    return normalized


def _infer_source_type(locator: Dict[str, Any]) -> str:
    if locator.get("sheet") or locator.get("sheetName") or locator.get("cell"):
        return "excel_cell"
    if locator.get("apiRowId") or locator.get("apiEndpoint"):
        return "api_row"
    if locator.get("pageNumber") or locator.get("page"):
        return "document_page"
    if locator.get("evidenceId") or locator.get("sourceUrlOrEvidenceId"):
        return "evidence_record"
    return "agent_extraction"


def _locator_precision(locator: Optional[Dict[str, Any]]) -> str:
    if not locator:
        return "missing"
    if locator.get("cell") or locator.get("apiRowId"):
        return "cell_or_row"
    if locator.get("row") or locator.get("rowIndex"):
        return "table_row"
    if locator.get("pageNumber") or locator.get("page"):
        return "page"
    if locator.get("sourceUrlOrEvidenceId") or locator.get("evidenceId"):
        return "evidence"
    return "artifact"


def _domain_coverage(fields: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for field in fields:
        domain = field["domain"]
        item = result.setdefault(domain, {"fieldCount": 0, "locatedFieldCount": 0})
        item["fieldCount"] += 1
        if field["locatorStatus"] == "located":
            item["locatedFieldCount"] += 1
    for item in result.values():
        item["coverageRatio"] = round(item["locatedFieldCount"] / item["fieldCount"], 4) if item["fieldCount"] else 1.0
        item["status"] = _coverage_status(item["fieldCount"], item["locatedFieldCount"])
    return result


def _coverage_status(field_count: int, located_count: int) -> str:
    if field_count == 0:
        return "not_applicable"
    if located_count == field_count:
        return "complete"
    if located_count > 0:
        return "partial"
    return "missing"


def _is_market_only_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in MARKET_ONLY_PATH_PREFIXES)


def _term_surface_for_path(
    path: str,
    value: Any,
    parsed_data: Dict[str, Any],
    locator: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    term = _term_metadata_for_path(path, value, parsed_data)
    source_locator = locator or {
        "fieldName": path,
        "path": path,
        "sourceType": "parsed_field_path",
    }
    surface = _surface_text_for_path(path, value, term)
    return build_term_surface_record(
        surface,
        source_field=path,
        source_locator=source_locator,
        term_id=term.get("termId"),
        canonical_field=term.get("canonicalField"),
        match_type=term.get("matchType", "parser_field_map" if term else "unmapped_parser_field"),
        confidence=float(term.get("confidence", 0.7 if term else 0.0)),
    )


def _term_metadata_for_path(path: str, value: Any, parsed_data: Dict[str, Any]) -> Dict[str, Any]:
    normalized_path = _normalize_indexed_path(path)
    if normalized_path == "pf_structure[].tranche":
        return _tranche_term_metadata(value)
    if normalized_path in {"pf_structure[].amount", "pf_structure[].all_in_cost"}:
        parent = _get_path(parsed_data, _parent_list_record_path(path))
        tranche = (parent.get("tranche") or parent.get("name")) if isinstance(parent, dict) else None
        tranche_term = _tranche_term_metadata(tranche)
        if normalized_path.endswith(".all_in_cost"):
            return {"termId": "all_in_cost", "canonicalField": "pf_structure.tranches.allInCost", "confidence": 0.86}
        if tranche_term.get("termId") == "senior_tranche":
            return {"termId": "senior_tranche", "canonicalField": "pf_structure.tranches.senior.amount", "confidence": 0.86}
        if tranche_term.get("termId") == "mezzanine_tranche":
            return {"termId": "mezzanine_tranche", "canonicalField": "pf_structure.tranches.mezzanine.amount", "confidence": 0.86}
        if tranche_term.get("termId") == "junior_tranche":
            return {"termId": "junior_tranche", "canonicalField": "pf_structure.tranches.junior.amount", "confidence": 0.86}
        return {"termId": "pf_total_amount", "canonicalField": "pf_structure.amount.sum", "confidence": 0.65}
    if normalized_path.startswith("covenants."):
        root = normalized_path.split("[", 1)[0]
        return PARSER_FIELD_TERM_MAP.get(root, {})
    return PARSER_FIELD_TERM_MAP.get(normalized_path, {})


def _tranche_term_metadata(value: Any) -> Dict[str, Any]:
    text = str(value or "").strip().lower()
    if any(token in text for token in ("tr.a", "tra", "senior", "선순위", "a tranche")):
        return {"termId": "senior_tranche", "canonicalField": "pf_structure.tranches.senior.amount", "confidence": 0.88}
    if any(token in text for token in ("tr.b", "trb", "mezz", "mezzanine", "중순위", "b tranche")):
        return {"termId": "mezzanine_tranche", "canonicalField": "pf_structure.tranches.mezzanine.amount", "confidence": 0.88}
    if any(token in text for token in ("tr.c", "trc", "junior", "후순위", "c tranche")):
        return {"termId": "junior_tranche", "canonicalField": "pf_structure.tranches.junior.amount", "confidence": 0.88}
    return {"termId": "pf_total_amount", "canonicalField": "pf_structure.amount.sum", "confidence": 0.55}


def _surface_text_for_path(path: str, value: Any, term: Dict[str, Any]) -> str:
    if path.endswith(".tranche") and value not in (None, ""):
        return str(value)
    return str(term.get("termId") or path.rsplit(".", 1)[-1])


def _normalize_indexed_path(path: str) -> str:
    return _INDEX_RE.sub("[]", path)


def _parent_list_record_path(path: str) -> str:
    return path.rsplit(".", 1)[0]


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _preview(value: Any) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return f"{len(value)} item(s)"
    text = str(value)
    return text if len(text) <= 120 else f"{text[:117]}..."
