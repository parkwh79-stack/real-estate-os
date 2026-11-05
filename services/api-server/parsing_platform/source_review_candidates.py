"""Bounded, label-based review candidates from native source text and literal cells."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from parsing_platform.contracts import ParsedFactCandidate
from parsing_platform.default_modules import intake_core_profile, _module_output
from parsing_platform.document_taxonomy import DOCUMENT_TAXONOMY_VERSION, DOCUMENT_TYPE_SCOPE_ALL
from parsing_platform.feasibility_domain_modules import (
    DOMAIN_CANDIDATE_CONTENT_HASH_VERSION, FEASIBILITY_DOMAIN_OUTPUT_SCHEMA,
    FEASIBILITY_DOMAIN_PACK_VERSION, build_domain_candidate_content_hash, build_domain_candidate_id,
)
from parsing_platform.orchestrator import ProjectParsingProfile
from parsing_platform.registry import ParserModuleManifest
from parsing_platform.schema_contracts import GENERIC_MODULE_INPUT_SCHEMA

MODULE_ID = "source_review_candidates"
MODULE_VERSION = "1.0.0"
PROFILE_ID = "realytics.source_review_candidates"
PROFILE_VERSION = "1.0.0"
MAX_PAGES = 20
MAX_CHUNKS = 10000
MAX_WORKBOOK_CHUNKS = 2500
MAX_CANDIDATES = 60
_RULES = {
    "사업명": ("project.name", None), "프로젝트명": ("project.name", None),
    "소재지": ("land.address", None), "대지위치": ("land.address", None), "위치": ("land.address", None),
    "PNU": ("land.pnu", None),
    "대지면적": ("land.area_sqm", "sqm"), "연면적": ("program.gross_floor_area_sqm", "sqm"),
    "총분양수입": ("revenue.total_sales_revenue", "krw"), "총매출액": ("revenue.total_sales_revenue", "krw"),
    "총사업비": ("cost.total_project_cost", "krw"), "토지비": ("cost.land_cost", "krw"), "토지매입비": ("cost.land_cost", "krw"),
    "PF대출금액": ("financing.pf_amount", "krw"),
}
_UNITS = {"원": Decimal(1), "천원": Decimal(1000), "백만원": Decimal(1000000), "억원": Decimal(100000000),
          "㎡": Decimal(1), "m²": Decimal(1), "m2": Decimal(1), "제곱미터": Decimal(1)}
_LABEL = re.compile(r"^\s*(" + "|".join(_RULES) + r")\s*(?:[（(]\s*(원|천원|백만원|억원|㎡|m²|m2|제곱미터)\s*[）)])?\s*[:：]?\s*$")
_NUMBER = re.compile(r"^\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(원|천원|백만원|억원|㎡|m²|m2|제곱미터)?\s*$")


def source_review_profile(*, project_id: str | None = None) -> ProjectParsingProfile:
    value = intake_core_profile(project_id=project_id).to_dict()
    value.update(profile_id=PROFILE_ID, version=PROFILE_VERSION)
    value["ordered_stages"] = [
        *value["ordered_stages"],
        {"stage_id": "review_candidates", "module_refs": [MODULE_ID + "@" + MODULE_VERSION]},
    ]
    value["metadata"] = {
        **value["metadata"], "candidateOnly": True, "authoritativeProjectionAllowed": False,
        "nativePageLimit": MAX_PAGES, "chunkScanLimit": MAX_CHUNKS, "workbookCellLimit": MAX_WORKBOOK_CHUNKS,
    }
    return ProjectParsingProfile.from_value(value)


def _normalized(label: str, value):
    match = _LABEL.fullmatch(re.sub(r"\s+", "", label.lstrip("·• ")))
    if not match or isinstance(value, bool):
        return None
    field, unit = _RULES[match[1]]
    raw = str(value).strip()
    if not raw or len(raw) > 200:
        return None
    if field == "land.pnu":
        return (field, raw, None, "parcel:" + raw) if re.fullmatch(r"[0-9]{19}", raw) else None
    if field == "land.address":
        return (field, raw, None, None) if re.match(r"^(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청|전라|경상|제주)", raw) else None
    if field == "project.name":
        return (field, raw, None, None) if len(raw) >= 3 else None
    numeric_text = raw
    if unit == "sqm":
        dual_area = re.fullmatch(r"(?:[0-9,.]+\s*평\s*[（(]\s*)?([0-9,.]+\s*(?:㎡|m²|m2|제곱미터))\s*(?:[）)]|\s*[（(]\s*[0-9,.]+\s*평\s*[）)])?", raw)
        if dual_area:
            numeric_text = dual_area[1]
    number = _NUMBER.fullmatch(numeric_text)
    if not number or (match[2] and number[2] and match[2] != number[2]):
        return None
    source_unit = number[2] or match[2]
    if source_unit not in _UNITS or (unit == "krw") != (source_unit in {"원", "천원", "백만원", "억원"}):
        return None
    try:
        amount = Decimal(number[1].replace(",", "")) * _UNITS[source_unit]
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0 or amount > Decimal("1e16"):
        return None
    normalized = int(amount) if amount == amount.to_integral_value() else float(amount)
    return field, normalized, unit, None


def _pairs(chunks):
    cells = {}
    for chunk in chunks:
        if chunk.get("kind") != "worksheet_cell":
            continue
        locator = chunk.get("sourceLocator") or {}
        cell = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", str(locator.get("cellReference") or ""))
        values = (chunk.get("metadata") or {}).get("cells") or []
        if cell and len(values) == 1 and values[0].get("calculationState") == "literal":
            cells[(locator.get("sheetName"), cell[1], int(cell[2]))] = (chunk, values[0].get("value"))
    for chunk, value in cells.values():
        if isinstance(value, str):
            parts = re.split(r"[:：]", value, maxsplit=1)
            if len(parts) == 2:
                yield parts[0], parts[1], [chunk["sourceLocator"]]
    from openpyxl.utils.cell import column_index_from_string, get_column_letter
    for (sheet, column, row), (chunk, label) in cells.items():
        if not isinstance(label, str) or not _LABEL.fullmatch(re.sub(r"[ \t]+", "", label)):
            continue
        adjacent = cells.get((sheet, get_column_letter(column_index_from_string(column) + 1), row))
        if adjacent:
            yield label, adjacent[1], [chunk["sourceLocator"], adjacent[0]["sourceLocator"]]
    for chunk in chunks:
        if chunk.get("kind") != "pdf_text_block":
            continue
        locator = chunk.get("sourceLocator") or {}
        if not 1 <= int(locator.get("pageNumber") or 0) <= MAX_PAGES:
            continue
        lines = [line.strip() for line in str(chunk.get("text") or "").splitlines() if line.strip()]
        for index, line in enumerate(lines):
            parts = re.split(r"[:：]", line, maxsplit=1)
            if len(parts) == 2:
                yield parts[0], parts[1], [locator]
            else:
                for end in range(index + 1, min(index + 6, len(lines))):
                    label = "".join(lines[index:end])
                    if _LABEL.fullmatch(re.sub(r"\s+", "", label)):
                        yield label, lines[end], [locator]


class SourceReviewCandidateModule:
    manifest = ParserModuleManifest(
        module_id=MODULE_ID, version=MODULE_VERSION, capabilities=("document.source_review_candidates",),
        document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION, document_type_scope=DOCUMENT_TYPE_SCOPE_ALL,
        requires=("document.structure",), provides=("document.source_review_candidates_completed",),
        execution_class="deterministic", input_schema=GENERIC_MODULE_INPUT_SCHEMA,
        output_schema=FEASIBILITY_DOMAIN_OUTPUT_SCHEMA, required_locator_precision="exact",
        data_classification="restricted", max_output_bytes=2 * 1024 * 1024,
        metadata={"candidateOnly": True, "authoritative": False, "nativePageLimit": MAX_PAGES},
    )

    def execute(self, payload, context):
        structural = _module_output(payload, "structural_extraction")
        artifact = structural.get("artifact") or {}
        chunks = artifact.get("chunks") or []
        limited = chunks[:MAX_CHUNKS]
        candidates = {}
        for label, raw, evidence in _pairs(limited):
            parsed = _normalized(label, raw)
            if parsed is None:
                continue
            field, value, unit, entity = parsed
            metadata = {
                "domainModuleId": MODULE_ID, "domainModuleVersion": MODULE_VERSION,
                "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
                "candidateContentHashVersion": DOMAIN_CANDIDATE_CONTENT_HASH_VERSION,
                "sourceRowId": str(evidence[-1].get("evidenceId")) + ":" + field,
                "extractionRule": "exact_label_literal_explicit_unit_v1", "requiresHumanReview": True,
                "sourceSection": {key: evidence[-1][key] for key in ("pageNumber", "sheetName") if key in evidence[-1]},
                "requiresProjectAssociation": True,
            }
            wire = {
                "candidateId": "pending", "tenantId": context["tenant_id"], "projectId": context["project_id"],
                "parsingRunId": context["parsing_run_id"], "documentVersionId": context["document_version_id"],
                "fieldKey": field, "entityId": entity, "rawValue": raw, "normalizedValue": value, "unit": unit,
                "originType": "extracted", "presenceStatus": "present", "confidence": 0.8,
                "authority": context["source_authority"], "evidence": evidence,
                "parserVersion": MODULE_ID + "@" + MODULE_VERSION, "ontologyVersion": context["ontology_version"],
                "promptVersion": "source-review-labels-v1", "modelVersion": "deterministic-only",
                "createdAt": context["candidate_created_at"], "metadata": metadata,
            }
            try:
                candidate = ParsedFactCandidate.model_validate(wire)
                wire = candidate.model_dump(by_alias=True, mode="json", exclude_none=True)
                wire["metadata"]["candidateContentHash"] = build_domain_candidate_content_hash(wire)
                wire["candidateId"] = build_domain_candidate_id(wire)
                candidate = ParsedFactCandidate.model_validate(wire)
            except ValueError:
                continue
            candidates[candidate.candidate_id] = candidate.model_dump(by_alias=True, mode="json", exclude_none=True)
            if len(candidates) >= MAX_CANDIDATES:
                break
        partial = (len(chunks) > MAX_CHUNKS or len(candidates) >= MAX_CANDIDATES
                   or artifact.get("complete") is not True
                   or int((artifact.get("metadata") or {}).get("pageCount") or 0) > MAX_PAGES)
        summary = {"candidateCount": len(candidates), "scannedChunkCount": len(limited),
                   "nativePageLimit": MAX_PAGES, "coverage": "partial" if partial else "native_source",
                   "outcome": "review_candidates" if candidates else "needs_mapping_or_reading"}
        return {
            "schemaVersion": FEASIBILITY_DOMAIN_OUTPUT_SCHEMA, "outputKind": "candidate_batch",
            "moduleId": MODULE_ID, "moduleVersion": MODULE_VERSION, "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
            "domain": "source_review", "candidateOnly": True, "authoritative": False,
            "candidates": list(candidates.values()), "diagnostics": [], "summary": summary,
            "pendingCapabilities": ["arbitrary_table_semantics", "scanned_pdf_fact_extraction", "formula_recalculation"],
            "providedCapabilities": [],
        }