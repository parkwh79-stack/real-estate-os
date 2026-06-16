from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


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
    for path, value in _iter_non_market_leaf_fields(parsed_data):
        locator = _locator_for_path(parsed_data, source_locators, path)
        fields.append(
            {
                "path": path,
                "domain": path.split(".", 1)[0].split("[", 1)[0],
                "valuePreview": _preview(value),
                "hasValue": not _empty(value),
                "sourceLocator": locator or {},
                "locatorStatus": "located" if locator else "missing",
                "locatorPrecision": _locator_precision(locator),
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
        "missingFields": [
            {"path": field["path"], "domain": field["domain"], "valuePreview": field["valuePreview"]}
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


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _preview(value: Any) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return f"{len(value)} item(s)"
    text = str(value)
    return text if len(text) <= 120 else f"{text[:117]}..."
