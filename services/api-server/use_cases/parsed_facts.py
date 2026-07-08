from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List

from core.text_normalization import build_term_surface_record
from storage import list_deal_state_records, upsert_deal_state_record


PARSED_FACT_SCHEMA_VERSION = "realytics-parsed-fact-v1"

_INDEX_RE = re.compile(r"\[\d+\]")

_MARKET_FACT_TERMS: Dict[str, Dict[str, Any]] = {
    "document_market_evidence.proposedPricePerPy": {
        "termId": "sales_price_per_py",
        "canonicalField": "market.proposedPricePerPy",
        "confidence": 0.84,
    },
    "document_market_evidence.productMix[].type": {
        "termId": "product_type",
        "canonicalField": "productMix.type",
        "confidence": 0.82,
    },
    "document_market_evidence.productMix[].units": {
        "termId": "unit_count",
        "canonicalField": "productMix.units",
        "confidence": 0.82,
    },
    "document_market_evidence.productMix[].contractAreaPy": {
        "termId": "contract_area_py",
        "canonicalField": "productMix.contractAreaPy",
        "confidence": 0.82,
    },
    "document_market_evidence.productMix[].pricePerPy": {
        "termId": "sales_price_per_py",
        "canonicalField": "productMix.pricePerPy",
        "confidence": 0.84,
    },
    "document_market_evidence.productMix[].totalRevenue": {
        "termId": "total_sales_revenue",
        "canonicalField": "productMix.totalRevenue",
        "confidence": 0.84,
    },
    "document_market_evidence.manualComparables[].pricePerPy": {
        "termId": "sales_comparable_price_per_py",
        "canonicalField": "marketComparable.pricePerPy",
        "confidence": 0.8,
    },
    "document_market_evidence.transactionComparables[].pricePerPy": {
        "termId": "transaction_comparable_price_per_py",
        "canonicalField": "marketComparable.pricePerPy",
        "confidence": 0.82,
    },
    "document_market_evidence.presaleComparables[].pricePerPy": {
        "termId": "presale_comparable_price_per_py",
        "canonicalField": "marketComparable.pricePerPy",
        "confidence": 0.82,
    },
    "document_market_evidence.listingComparables[].pricePerPy": {
        "termId": "listing_comparable_price_per_py",
        "canonicalField": "marketComparable.pricePerPy",
        "confidence": 0.76,
    },
    "document_market_evidence.rentComparables[].monthlyRentPerPy": {
        "termId": "rent_comparable_monthly_rent_per_py",
        "canonicalField": "marketComparable.monthlyRentPerPy",
        "confidence": 0.76,
    },
    "document_market_evidence.rentalAssumptions[].monthlyRentPerPy": {
        "termId": "rent_assumption_monthly_rent_per_py",
        "canonicalField": "market.rentalAssumptions.monthlyRentPerPy",
        "confidence": 0.76,
    },
}

_CRITICAL_CANONICAL_FIELDS = {
    "land.address",
    "land.pnu",
    "land.area_sqm",
    "landEvidence.officialLandPrice.pricePerSqm",
    "architectural_plan.gross_floor_area_sqm",
    "architectural_plan.unit_count",
    "feasibility_input.total_revenue",
    "feasibility_input.total_cost",
    "feasibility_input.land_cost",
    "feasibility_input.construction_cost",
    "pf_structure.amount.sum",
    "market.proposedPricePerPy",
    "productMix.totalRevenue",
    "productMix.pricePerPy",
    "marketComparable.pricePerPy",
}


def persist_parsed_facts(
    project_id: str,
    parsed_data: Dict[str, Any],
    *,
    upsert_fn: Callable[[str, Dict[str, Any]], Dict[str, Any]] = upsert_deal_state_record,
) -> Dict[str, Any]:
    """Persist parser outputs as first-class parsed_fact deal-state records."""
    records = build_parsed_fact_records(project_id, parsed_data)
    persisted = [upsert_fn(project_id, record) for record in records]
    return {
        "schemaVersion": "realytics-parsed-fact-store-result-v1",
        "projectId": project_id,
        "recordCount": len(persisted),
        "summary": summarize_parsed_fact_records(persisted),
        "records": persisted,
    }


def parsed_fact_package(project_id: str, *, limit: int = 1000) -> Dict[str, Any]:
    records = list_deal_state_records(project_id, record_type="parsed_fact", limit=limit)
    return {
        "schemaVersion": "realytics-parsed-fact-package-v1",
        "projectId": project_id,
        "recordCount": len(records),
        "records": records,
        "summary": summarize_parsed_fact_records(records),
    }


def build_parsed_fact_records(project_id: str, parsed_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    meta = parsed_data.get("_meta") if isinstance(parsed_data.get("_meta"), dict) else {}
    upload_meta = meta.get("upload") if isinstance(meta.get("upload"), dict) else {}
    parser_version = meta.get("parser_version") or upload_meta.get("parserVersion")
    records: List[Dict[str, Any]] = []
    seen: set[str] = set()

    provenance = meta.get("parserFieldProvenance") if isinstance(meta.get("parserFieldProvenance"), dict) else {}
    fields = provenance.get("fields") if isinstance(provenance.get("fields"), list) else []
    for field in fields:
        if not isinstance(field, dict):
            continue
        path = str(field.get("path") or "")
        if not path:
            continue
        value = _get_path(parsed_data, path)
        record = _record_from_parser_field(
            project_id=project_id,
            path=path,
            value=value if value is not None else field.get("valuePreview"),
            field=field,
            upload_meta=upload_meta or provenance.get("sourceDocument") or {},
            parser_version=parser_version,
        )
        if record["recordId"] not in seen:
            seen.add(record["recordId"])
            records.append(record)

    document_market_evidence = parsed_data.get("document_market_evidence")
    if isinstance(document_market_evidence, dict):
        for path, value, locator in _iter_document_market_leaf_fields(document_market_evidence):
            record = _record_from_market_fact(
                project_id=project_id,
                path=path,
                value=value,
                locator=locator,
                upload_meta=upload_meta or document_market_evidence.get("sourceDocument") or {},
                parser_version=parser_version or document_market_evidence.get("parserVersion"),
            )
            if record["recordId"] not in seen:
                seen.add(record["recordId"])
                records.append(record)

    return records


def summarize_parsed_fact_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_domain: Dict[str, int] = {}
    by_pod: Dict[str, int] = {}
    missing_locator = 0
    critical_missing_locator = 0
    needs_review = 0
    for record in records:
        domain = str(record.get("domain") or "unknown")
        by_domain[domain] = by_domain.get(domain, 0) + 1
        for pod_key in record.get("targetPods") or []:
            by_pod[str(pod_key)] = by_pod.get(str(pod_key), 0) + 1
        if record.get("locatorStatus") != "located":
            missing_locator += 1
            if record.get("critical"):
                critical_missing_locator += 1
        if record.get("validationStatus") in {"needs_review", "needs_source_locator"}:
            needs_review += 1
    return {
        "recordCount": len(records),
        "byDomain": by_domain,
        "byTargetPod": by_pod,
        "missingLocatorCount": missing_locator,
        "criticalMissingLocatorCount": critical_missing_locator,
        "needsReviewCount": needs_review,
    }


def _record_from_parser_field(
    *,
    project_id: str,
    path: str,
    value: Any,
    field: Dict[str, Any],
    upload_meta: Dict[str, Any],
    parser_version: Any,
) -> Dict[str, Any]:
    term_surface = field.get("termSurface") if isinstance(field.get("termSurface"), dict) else {}
    locator = field.get("sourceLocator") if isinstance(field.get("sourceLocator"), dict) else {}
    term_id = field.get("termId") or term_surface.get("termId")
    canonical_field = field.get("canonicalField") or term_surface.get("canonicalField") or path
    raw_text = term_surface.get("rawText") if term_surface else _raw_text_for_value(value)
    surface = (
        term_surface
        if term_surface
        else build_term_surface_record(
            raw_text,
            source_field=path,
            source_locator=locator or None,
            term_id=term_id,
            canonical_field=canonical_field,
            match_type="parser_field_map" if term_id else "unmapped_parser_field",
            confidence=0.7 if term_id else 0.0,
        )
    )
    return _base_record(
        project_id=project_id,
        path=path,
        domain=str(field.get("domain") or path.split(".", 1)[0]),
        value=value,
        locator=locator,
        locator_status=str(field.get("locatorStatus") or ("located" if locator else "missing")),
        locator_precision=str(field.get("locatorPrecision") or _locator_precision(locator)),
        term_surface=surface,
        term_id=term_id,
        canonical_field=canonical_field,
        upload_meta=upload_meta,
        parser_version=parser_version,
    )


def _record_from_market_fact(
    *,
    project_id: str,
    path: str,
    value: Any,
    locator: Dict[str, Any],
    upload_meta: Dict[str, Any],
    parser_version: Any,
) -> Dict[str, Any]:
    normalized_path = _normalize_indexed_path(path)
    term = _MARKET_FACT_TERMS.get(normalized_path, {})
    canonical_field = term.get("canonicalField") or path
    term_id = term.get("termId")
    surface = build_term_surface_record(
        _surface_text(path, value, term_id),
        source_field=path,
        source_locator=locator or None,
        term_id=term_id,
        canonical_field=canonical_field,
        match_type="document_market_evidence_map" if term else "unmapped_document_market_field",
        confidence=float(term.get("confidence", 0.55 if term else 0.0)),
    )
    return _base_record(
        project_id=project_id,
        path=path,
        domain="market",
        value=value,
        locator=locator,
        locator_status="located" if _locator_has_specific_position(locator) else "document_only" if locator else "missing",
        locator_precision=_locator_precision(locator),
        term_surface=surface,
        term_id=term_id,
        canonical_field=canonical_field,
        upload_meta=upload_meta,
        parser_version=parser_version,
    )


def _base_record(
    *,
    project_id: str,
    path: str,
    domain: str,
    value: Any,
    locator: Dict[str, Any],
    locator_status: str,
    locator_precision: str,
    term_surface: Dict[str, Any],
    term_id: Any,
    canonical_field: Any,
    upload_meta: Dict[str, Any],
    parser_version: Any,
) -> Dict[str, Any]:
    canonical = str(canonical_field or path)
    critical = canonical in _CRITICAL_CANONICAL_FIELDS
    record_id = f"parsed_fact:{_document_key(upload_meta)}:{_stable_key(path, canonical)}"
    normalized_value = _normalized_value(value)
    validation_status = "needs_source_locator" if critical and locator_status != "located" else "needs_review"
    if locator_status == "located" and term_id:
        validation_status = "parsed"
    return {
        "schemaVersion": PARSED_FACT_SCHEMA_VERSION,
        "projectId": project_id,
        "recordId": record_id,
        "recordType": "parsed_fact",
        "key": canonical,
        "factKey": canonical,
        "parserPath": path,
        "domain": domain,
        "value": value,
        "normalizedValue": normalized_value,
        "unit": _infer_unit(canonical, path),
        "rawText": term_surface.get("rawText"),
        "normalizedSurface": term_surface.get("normalizedSurface"),
        "textQualityFlags": term_surface.get("textQualityFlags") or {},
        "termId": term_id,
        "canonicalField": canonical,
        "termSurface": term_surface,
        "sourceType": "parser_agent",
        "sourceDocument": _source_document_summary(upload_meta),
        "sourceLocator": locator or _document_only_locator(upload_meta, path),
        "locatorStatus": locator_status,
        "locatorPrecision": locator_precision,
        "parserVersion": parser_version,
        "targetPods": _target_pods(canonical, path, domain),
        "critical": critical,
        "validationStatus": validation_status,
        "confidence": _confidence(term_surface, locator_status, bool(term_id)),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }


def _iter_document_market_leaf_fields(data: Dict[str, Any]) -> Iterable[tuple[str, Any, Dict[str, Any]]]:
    skip_roots = {"sourceDocument", "parserVersion", "dataSources", "data_sources", "sourceMetadata"}
    for key, value in data.items():
        if key in skip_roots:
            continue
        yield from _iter_leaf(value, f"document_market_evidence.{key}", data)


def _iter_leaf(value: Any, path: str, root: Dict[str, Any]) -> Iterable[tuple[str, Any, Dict[str, Any]]]:
    if isinstance(value, dict):
        if "sourceLocator" in value or "source_locator" in value:
            locator = _normalize_locator(value.get("sourceLocator") or value.get("source_locator"), path)
        else:
            locator = {}
        for key, child in value.items():
            if key in {"sourceLocator", "source_locator", "sourceMetadata", "dataSources", "data_sources"}:
                continue
            child_path = f"{path}.{key}"
            child_locator = locator or _normalize_locator(root.get("sourceLocator") or root.get("source_locator"), child_path)
            yield from _iter_leaf_with_locator(child, child_path, child_locator)
        return
    if isinstance(value, list):
        inherited = _normalize_locator(root.get("sourceLocator") or root.get("source_locator"), path)
        for index, child in enumerate(value):
            yield from _iter_leaf_with_locator(child, f"{path}[{index}]", inherited)
        return
    yield path, value, _normalize_locator(root.get("sourceLocator") or root.get("source_locator"), path)


def _iter_leaf_with_locator(value: Any, path: str, inherited_locator: Dict[str, Any]) -> Iterable[tuple[str, Any, Dict[str, Any]]]:
    if isinstance(value, dict):
        locator = _normalize_locator(value.get("sourceLocator") or value.get("source_locator"), path) or inherited_locator
        for key, child in value.items():
            if key in {"sourceLocator", "source_locator", "sourceMetadata", "dataSources", "data_sources"}:
                continue
            yield from _iter_leaf_with_locator(child, f"{path}.{key}", locator)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_leaf_with_locator(child, f"{path}[{index}]", inherited_locator)
        return
    if value not in (None, ""):
        yield path, value, inherited_locator


def _target_pods(canonical: str, path: str, domain: str) -> List[str]:
    joined = f"{canonical} {path} {domain}".lower()
    pods: List[str] = []
    if any(token in joined for token in ("land", "pnu", "parcel", "regulation", "covenant", "legal")):
        pods.append("land_legal")
    if any(token in joined for token in ("market", "priceperpy", "productmix", "rent", "comparable", "sales_assumptions")):
        pods.append("market_product")
    if any(token in joined for token in ("cost", "schedule", "construction", "contractor")):
        pods.append("cost_schedule")
    if any(token in joined for token in ("feasibility", "revenue", "cashflow", "productmix", "totalrevenue")):
        pods.append("feasibility_cashflow")
    if any(token in joined for token in ("pf_", "pf.", "tranche", "ltv", "ltc", "dscr", "waterfall")):
        pods.append("pf_credit")
    return pods or ["chief_orchestrator"]


def _source_document_summary(upload_meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: upload_meta.get(key)
        for key in (
            "archiveId",
            "originalFileName",
            "fileName",
            "documentType",
            "contentType",
            "uploadedAt",
            "parserVersion",
        )
        if upload_meta.get(key) is not None
    }


def _document_only_locator(upload_meta: Dict[str, Any], path: str) -> Dict[str, Any]:
    locator = {
        "sourceType": "uploaded_document",
        "fieldName": path,
        "path": path,
    }
    if upload_meta.get("archiveId") or upload_meta.get("originalFileHash"):
        locator["documentId"] = upload_meta.get("archiveId") or upload_meta.get("originalFileHash")
    if upload_meta.get("originalFileName") or upload_meta.get("fileName"):
        locator["documentName"] = upload_meta.get("originalFileName") or upload_meta.get("fileName")
    return locator


def _normalize_locator(locator: Any, path: str) -> Dict[str, Any]:
    if not isinstance(locator, dict):
        return {}
    if not any(value not in (None, "", [], {}, 0) for value in locator.values()):
        return {}
    normalized = dict(locator)
    normalized.setdefault("fieldName", path)
    normalized.setdefault("path", path)
    normalized.setdefault("sourceType", _locator_source_type(normalized))
    return normalized


def _locator_source_type(locator: Dict[str, Any]) -> str:
    if locator.get("sheet") or locator.get("sheetName") or locator.get("cell"):
        return "excel_cell"
    if locator.get("apiRowId") or locator.get("apiEndpoint"):
        return "api_row"
    if locator.get("pageNumber") or locator.get("page"):
        return "document_page"
    if locator.get("evidenceId") or locator.get("sourceUrlOrEvidenceId"):
        return "evidence_record"
    if locator.get("documentId") or locator.get("document"):
        return "document"
    return "agent_extraction"


def _locator_precision(locator: Dict[str, Any]) -> str:
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
    if locator.get("documentId") or locator.get("document"):
        return "document"
    return "artifact"


def _locator_has_specific_position(locator: Dict[str, Any]) -> bool:
    return bool(locator and any(locator.get(key) for key in ("cell", "apiRowId", "row", "rowIndex", "page", "pageNumber", "evidenceId", "sourceUrlOrEvidenceId")))


def _confidence(term_surface: Dict[str, Any], locator_status: str, has_term: bool) -> float:
    base = float(term_surface.get("confidence") or (0.72 if has_term else 0.45))
    if locator_status == "located":
        return min(0.99, round(base + 0.08, 4))
    if locator_status == "document_only":
        return max(0.1, round(base - 0.08, 4))
    return max(0.05, round(base - 0.18, 4))


def _document_key(upload_meta: Dict[str, Any]) -> str:
    raw = str(upload_meta.get("archiveId") or upload_meta.get("originalFileHash") or upload_meta.get("originalFileName") or "unknown_document")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _stable_key(path: str, canonical: str) -> str:
    return hashlib.sha1(f"{canonical}|{path}".encode("utf-8")).hexdigest()[:16]


def _normalize_indexed_path(path: str) -> str:
    return _INDEX_RE.sub("[]", path)


def _surface_text(path: str, value: Any, term_id: Any) -> str:
    return str(term_id or path.rsplit(".", 1)[-1] or value)


def _raw_text_for_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return str(value)
    return "" if value is None else str(value)


def _normalized_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        try:
            if "." in stripped:
                return float(stripped)
            return int(stripped)
        except ValueError:
            return value.strip()
    return value


def _infer_unit(canonical: str, path: str) -> str | None:
    text = f"{canonical} {path}".lower()
    if any(token in text for token in ("sqm", "area_sqm")):
        return "sqm"
    if any(token in text for token in ("py", "pyeong")):
        return "py"
    if any(token in text for token in ("price", "cost", "revenue", "amount", "land_price")):
        return "KRW"
    if any(token in text for token in ("rate", "ratio", "ltv", "ltc", "dscr", "far", "bcr")):
        return "ratio"
    return None


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
