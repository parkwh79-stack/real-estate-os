"""Bounded, label-based review candidates from native source text and literal cells."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
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
MODULE_VERSION = "1.1.0"
PROFILE_ID = "realytics.source_review_candidates"
PROFILE_VERSION = "1.1.0"
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


def _worksheet_cells(chunks):
    cells = {}
    for chunk in chunks:
        if chunk.get("kind") != "worksheet_cell":
            continue
        locator = chunk.get("sourceLocator") or {}
        coordinate = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", str(locator.get("cellReference") or ""))
        values = (chunk.get("metadata") or {}).get("cells") or []
        if coordinate and len(values) == 1 and isinstance(values[0], dict):
            cells[(locator.get("sheetName"), coordinate[1], int(coordinate[2]))] = (chunk, values[0])
    return cells


def _bridge_table_rows(chunks):
    """Read explicit source terms, not a future PF facility or inferred contract."""
    cells = _worksheet_cells(chunks)
    for sheet in dict.fromkeys(key[0] for key in cells):
        header = None
        for row in range(1, 31):
            labels = {str(data.get("value") or "").strip(): (column, chunk)
                      for (name, column, index), (chunk, data) in cells.items()
                      if name == sheet and index == row and data.get("calculationState") == "literal"}
            if all(label in labels for label in ("대출금", "대주", "만기", "금리")):
                header = row, labels
                break
        if header is None:
            continue
        header_row, labels = header
        context_cells = [(chunk, data) for (name, _column, row), (chunk, data) in cells.items()
                         if name == sheet and row < header_row and data.get("calculationState") == "literal"]
        titles = [chunk for chunk, data in context_cells
                  if re.search(r"브(?:릿|리)지론현황", re.sub(r"\s+", "", str(data.get("value") or "")))]
        units = [(match[1], chunk) for chunk, data in context_cells
                 if (match := re.fullmatch(r"[（(]?\s*단위\s*[:：]\s*(원|천원|백만원|억원)\s*[）)]?",
                                           str(data.get("value") or "").strip()))]
        if not titles or len({unit for unit, _chunk in units}) != 1:
            continue
        source_unit, unit_chunk = units[0]
        for row in range(header_row + 1, header_row + 101):
            amount_cell = cells.get((sheet, labels["대출금"][0], row))
            lender_cell = cells.get((sheet, labels["대주"][0], row))
            if amount_cell is None or lender_cell is None:
                continue
            amount_chunk, amount_data = amount_cell
            lender_chunk, lender_data = lender_cell
            if amount_data.get("calculationState") != "literal" or lender_data.get("calculationState") != "literal":
                continue
            raw_amount, lender = amount_data.get("value"), lender_data.get("value")
            if isinstance(raw_amount, bool) or not isinstance(lender, str) or not 1 <= len(lender.strip()) <= 200:
                continue
            numeric = _NUMBER.fullmatch(str(raw_amount))
            if not numeric or (numeric[2] and numeric[2] != source_unit):
                continue
            amount = Decimal(numeric[1].replace(",", "")) * _UNITS[source_unit]
            if not amount.is_finite() or amount < 0 or amount > Decimal("1e16"):
                continue
            if amount != amount.to_integral_value():
                continue  # A fractional won needs its own explicit rounding decision.
            entity = "source-bridge:" + hashlib.sha256(
                (str(amount_chunk["sourceLocator"].get("fileSha256")) + ":" + str(sheet) + ":" + str(row)).encode()
            ).hexdigest()[:24]
            common = {
                "extractionRule": "explicit_bridge_financial_table_literal_v1",
                "sourceFinancialContext": "current_bridge_snapshot", "referenceOnly": True,
                "noFuturePfProjection": True, "sourceAsOfStatus": "unconfirmed",
                "financingBalanceBasisStatus": "unconfirmed", "sourceTableCurrencyUnit": source_unit,
                "sourceTable": {"sheetName": sheet, "headerRow": header_row, "row": row},
                "sourceDocumentPurpose": "원본 금융현황·현재 브리지 대출 검토용; 미래 PF 약정 아님",
                "sourceCalculationState": "literal",
            }
            context = [titles[0]["sourceLocator"], unit_chunk["sourceLocator"]]
            yield ("financing.tranche.loan_amount", int(amount), "krw", entity, raw_amount,
                   [*context, labels["대출금"][1]["sourceLocator"], amount_chunk["sourceLocator"]],
                   {**common, "sourceUnit": source_unit})
            yield ("financing.tranche.name", lender.strip(), None, entity, lender,
                   [titles[0]["sourceLocator"], labels["대주"][1]["sourceLocator"], lender_chunk["sourceLocator"]],
                   {**common, "sourceFieldRole": "lender_name"})
            for label in ("금리", "만기"):
                term_cell = cells.get((sheet, labels[label][0], row))
                if term_cell is None or term_cell[1].get("calculationState") != "literal":
                    continue  # No merged-cell carry or unverified formula-cache promotion.
                chunk, data = term_cell
                raw = data.get("value")
                evidence = [titles[0]["sourceLocator"], labels[label][1]["sourceLocator"], chunk["sourceLocator"]]
                if label == "금리" and isinstance(raw, str):
                    rate = re.fullmatch(r"\s*(고정|변동)?\s*([0-9]+(?:\.[0-9]+)?)\s*%\s*", raw)
                    if rate is None or not 0 <= Decimal(rate[2]) <= 100:
                        continue
                    yield ("financing.tranche.interest_rate", float(Decimal(rate[2]) / 100), "ratio", entity,
                           raw, evidence, {**common, "sourceUnit": "percent"})
                    if rate[1]:
                        yield ("financing.tranche.interest_rate_type", "floating" if rate[1] == "변동" else "fixed",
                               None, entity, raw, evidence, common)
                elif label == "만기":
                    try:
                        maturity = date.fromisoformat(str(raw))
                    except (ValueError, TypeError):
                        if data.get("sourceValueType") != "datetime":
                            continue
                        try:
                            maturity = datetime.fromisoformat(str(raw)).date()
                        except (ValueError, TypeError):
                            continue
                    yield ("financing.tranche.maturity_date", maturity.isoformat(), None, entity, raw, evidence, common)


def _review_rows(chunks):
    for label, raw, evidence in _pairs(chunks):
        parsed = _normalized(label, raw)
        if parsed is not None:
            field, value, unit, entity = parsed
            yield field, value, unit, entity, raw, evidence, {}
    yield from _bridge_table_rows(chunks)


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
        for field, value, unit, entity, raw, evidence, source_metadata in _review_rows(limited):
            metadata = {
                "domainModuleId": MODULE_ID, "domainModuleVersion": MODULE_VERSION,
                "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
                "candidateContentHashVersion": DOMAIN_CANDIDATE_CONTENT_HASH_VERSION,
                "sourceRowId": str(evidence[-1].get("evidenceId")) + ":" + field,
                "extractionRule": "exact_label_literal_explicit_unit_v1", "requiresHumanReview": True,
                "sourceSection": {key: evidence[-1][key] for key in ("pageNumber", "sheetName") if key in evidence[-1]},
                "requiresProjectAssociation": True,
                **source_metadata,
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
        bridge_sheets = {item["metadata"]["sourceTable"]["sheetName"] for item in candidates.values()
                         if item["metadata"].get("sourceFinancialContext") == "current_bridge_snapshot"}
        if bridge_sheets:
            summary["sourceFinancialContext"] = "current_bridge_snapshot"
            summary["referenceOnly"] = True
            summary["formulaControls"] = [
                {"sourceLocator": chunk["sourceLocator"], "formula": data.get("formula"),
                 "cachedValue": data.get("value"), "calculationState": data.get("calculationState"),
                 "approvalEligible": False}
                for (sheet, _column, _row), (chunk, data) in _worksheet_cells(limited).items()
                if sheet in bridge_sheets and data.get("formula") is not None
            ][:20]
        return {
            "schemaVersion": FEASIBILITY_DOMAIN_OUTPUT_SCHEMA, "outputKind": "candidate_batch",
            "moduleId": MODULE_ID, "moduleVersion": MODULE_VERSION, "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
            "domain": "source_review", "candidateOnly": True, "authoritative": False,
            "candidates": list(candidates.values()), "diagnostics": [], "summary": summary,
            "pendingCapabilities": ["arbitrary_table_semantics", "scanned_pdf_fact_extraction", "formula_recalculation"],
            "providedCapabilities": [],
        }
