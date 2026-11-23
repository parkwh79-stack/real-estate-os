import copy
import hashlib
import json
import re
from typing import Any, Dict, Mapping, Sequence

import fitz  # PyMuPDF

from agents.ai_connection import AIConnection, resolve_ai_context
from agents.agent_policy import system_prompt_for
from core.text_chunking import chunk_text_preserving_context


PARSER_AGENT_VERSION = "realytics-parser-v2.4"
PARSER_EXTRACTION_CHUNK_CHARS = 30_000
_PAGE_MARKER = re.compile(r"^\s*--- PAGE \d+ ---\s*$")
_ENTITY_ARRAY_IDENTITY_KEYS: dict[str, tuple[str, ...]] = {
    "sales_assumptions.product_mix": ("type",),
    "market.comparables": ("caseId",),
    "market.rentComparables": ("caseId",),
    "market.data_sources": ("sourceUrlOrEvidenceId", "sourceName"),
    "document_market_evidence.productMix": ("type",),
    "document_market_evidence.comparables": ("caseId",),
    "document_market_evidence.manualComparables": ("caseId",),
    "document_market_evidence.transactionComparables": ("caseId",),
    "document_market_evidence.presaleComparables": ("caseId",),
    "document_market_evidence.listingComparables": ("caseId",),
    "document_market_evidence.rentComparables": ("caseId",),
    "document_market_evidence.data_sources": (
        "sourceUrlOrEvidenceId",
        "sourceName",
    ),
    "pf_structure": ("tranche",),
    "data_sources": ("sourceUrlOrEvidenceId", "sourceName"),
}


class ParserAgent:
    @staticmethod
    def read_evidence_pages(document, *, page_numbers, tessdata_dir=None, timeout_seconds=20.0) -> dict:
        """Read source-bound native/OCR text for review without promoting facts.

        The caller resolves an authorized, integrity-checked original. Results
        retain page locators and are inputs for review and subsequent extraction;
        this method never calls a paid provider or applies financial values.
        """
        from parsing_platform.local_pdf_reading import read_pdf_pages

        return read_pdf_pages(document, page_numbers=page_numbers,
                              tessdata_dir=tessdata_dir, timeout_seconds=timeout_seconds)

    def __init__(self, ai_context: Dict[str, Any] | None = None):
        self.ai_context = resolve_ai_context(ai_context)
        self.ai = AIConnection()

    def parse_document(self, file_path: str) -> str:
        """Extract text from a PDF document."""
        text = ""
        try:
            doc = fitz.open(file_path)
            for page_index, page in enumerate(doc, start=1):
                text += f"\n\n--- PAGE {page_index} ---\n"
                text += page.get_text()
            doc.close()
        except Exception as e:
            print(f"Error reading PDF {file_path}: {e}")
            return ""
        return text

    def extract_project_data(self, text: str, ai_context: Dict[str, Any] | None = None) -> dict:
        """Use the configured AI provider to extract structured project data from raw text."""
        context = resolve_ai_context(ai_context or self.ai_context)
        chunks = chunk_text_preserving_context(
            text,
            max_chars=PARSER_EXTRACTION_CHUNK_CHARS,
            is_context_line=lambda line: bool(_PAGE_MARKER.match(line)),
        )
        prompts = [self.build_extraction_prompt(chunk) for chunk in chunks]
        if context["runtime_provider"] == "mcp_host_llm":
            return self._get_host_llm_extraction_request(
                chunks,
                prompts,
                context,
                source_text_sha256=_sha256_text(text),
            )

        successful: list[tuple[int, Mapping[str, Any], Mapping[str, Any]]] = []
        failed_chunks: list[dict[str, Any]] = []
        system = system_prompt_for(
            "parser",
            base="You are a precise data extraction assistant. Output only JSON.",
        )
        for index, prompt in enumerate(prompts):
            result = self.ai.extract_json(
                prompt,
                system=system,
                ai_context=context,
            )
            if isinstance(result.data, Mapping):
                successful.append((index, result.data, result.meta or {}))
            else:
                failed_chunks.append(
                    {
                        "chunkIndex": index,
                        "error": result.error or "AI extraction unavailable",
                    }
                )
        if successful:
            merged, conflicts = _merge_chunk_extractions(successful)
            extraction_empty = not _has_meaningful_value(merged)
            complete = not failed_chunks and not conflicts and not extraction_empty
            merged["_meta"] = {
                **dict(merged.get("_meta") or {}),
                "parsing_source": "ai" if len(chunks) == 1 else "ai_chunked",
                "confidence": 0.75 if complete else None,
                "ai_connection": successful[0][2],
                "sourceTextSha256": _sha256_text(text),
                "chunkCount": len(chunks),
                "completedChunkCount": len(successful),
                "failedChunks": failed_chunks,
                "extractionConflicts": conflicts,
                "extractionEmpty": extraction_empty,
                "inputCoverage": "complete" if not failed_chunks else "partial",
                "analysisOnly": not complete,
                "reportEligible": complete,
            }
            return merged

        error = "; ".join(item["error"] for item in failed_chunks)
        print(f"Warning: AI extraction fallback used. {error}")
        return self._get_mock_parsed_data(
            error or "AI extraction unavailable",
            context,
        )

    def build_extraction_prompt(self, text: str) -> str:
        return """
        You are a Real Estate PF (Project Financing) expert. Your task is to extract structured information from the following business plan text.
        Output ONLY valid JSON matching the following structure. Do not include any markdown formatting (like ```json).

        {
          "project": {
            "name": "Project Name",
            "type": "residential|commercial|mixed_use|logistics",
            "review_date": "YYYY-MM-DD",
            "financial_institution": "Institution Name"
          },
          "land": {
            "address": "Address",
            "pnu": "PNU if available",
            "area_sqm": 0.0
          },
          "regulation": {
            "zoning": "Zoning Type",
            "legal_far": 0.0,
            "legal_bcr": 0.0,
            "applied_far": 0.0,
            "applied_bcr": 0.0
          },
          "architectural_plan": {
            "gross_floor_area_sqm": 0.0,
            "saleable_area_sqm": 0.0,
            "building_coverage_sqm": 0.0,
            "unit_count": 0,
            "parking_count": 0
          },
          "sales_assumptions": {
            "avg_price_per_sqm": 0,
            "other_revenue": 0,
            "product_mix": [
              {
                "type": "Unit Type",
                "productType": "apartment|officetel_residential|officetel_office|mixed_use_residential|commercial_retail|knowledge_industry_center|section_office|serviced_residence|other_saleable",
                "exclusiveAreaPy": 0,
                "contractAreaPy": 0,
                "units": 0,
                "pricePerPy": 0,
                "totalRevenue": 0,
                "evidenceIds": ["source-page-or-document-id"],
                "sourceLocator": {
                  "pageNumber": 0,
                  "table": "Table name or index",
                  "row": "Row label or index",
                  "column": "Column label",
                  "fieldName": "pricePerPy|totalRevenue|units|contractAreaPy",
                  "textExcerpt": "Short source excerpt",
                  "confidence": 0.0
                }
              }
            ]
          },
          "market": {
            "proposedPricePerPy": 0,
            "demandLevel": "strong|moderate|weak|unknown",
            "upcomingSupplyUnits": 0,
            "comparables": [
              {
                "caseId": "COMP-001",
                "name": "Comparable Project Name",
                "productType": "apartment|officetel_residential|commercial_retail|knowledge_industry_center|serviced_residence|other_saleable",
                "distanceM": 0,
                "pricePerPy": 0,
                "sourceName": "Document or data source name",
                "sourceType": "manual_upload|public_api|partner_api|internal_db|web|estimate",
                "sourceUrlOrEvidenceId": "document-page-or-evidence-id",
                "baseDate": "YYYY-MM-DD",
                "confidence": 0.0,
                "sourceLocator": {
                  "pageNumber": 0,
                  "table": "Table name or index",
                  "row": "Row label or index",
                  "column": "Column label",
                  "fieldName": "pricePerPy",
                  "textExcerpt": "Short source excerpt",
                  "confidence": 0.0
                }
              }
            ],
            "rentComparables": [
              {
                "caseId": "RENT-001",
                "name": "Rent Comparable Name",
                "productType": "officetel_residential|commercial_retail|knowledge_industry_center|section_office|serviced_residence",
                "distanceM": 0,
                "monthlyRentPerPy": 0,
                "sourceName": "Document or data source name",
                "sourceType": "manual_upload|public_api|partner_api|internal_db|web|estimate",
                "sourceUrlOrEvidenceId": "document-page-or-evidence-id",
                "baseDate": "YYYY-MM-DD",
                "confidence": 0.0,
                "sourceLocator": {
                  "pageNumber": 0,
                  "table": "Table name or index",
                  "row": "Row label or index",
                  "column": "Column label",
                  "fieldName": "monthlyRentPerPy",
                  "textExcerpt": "Short source excerpt",
                  "confidence": 0.0
                }
              }
            ],
            "rentalAssumptions": {
              "monthlyRentPerPy": 0,
              "requiredYield": 0.0,
              "sourceUrlOrEvidenceId": "document-page-or-evidence-id",
              "sourceLocator": {
                "pageNumber": 0,
                "table": "Table name or index",
                "row": "Row label or index",
                "column": "Column label",
                "fieldName": "monthlyRentPerPy|requiredYield",
                "textExcerpt": "Short source excerpt",
                "confidence": 0.0
              }
            },
            "data_sources": [
              {
                "sourceName": "Source name",
                "sourceType": "manual_upload|public_api|partner_api|internal_db|web|estimate",
                "sourceUrlOrEvidenceId": "document-page-or-evidence-id",
                "baseDate": "YYYY-MM-DD",
                "licenseOrUsageNote": "Usage note",
                "confidence": 0.0,
                "sourceLocator": {
                  "pageNumber": 0,
                  "table": "Table name or index",
                  "row": "Row label or index",
                  "column": "Column label",
                  "fieldName": "sourceName|baseDate|licenseOrUsageNote",
                  "textExcerpt": "Short source excerpt",
                  "confidence": 0.0
                }
              }
            ]
          },
          "document_market_evidence": {
            "extractionNote": "Market, sale price, rent, and comparable data extracted from uploaded documents. Include only values found in the document; do not invent market evidence.",
            "sourceLocatorPolicy": "Use the PAGE markers in the document text and include pageNumber, table, row, column, fieldName, short textExcerpt, and confidence whenever a value is extracted from a document table or paragraph.",
            "proposedPricePerPy": 0,
            "demandLevel": "strong|moderate|weak|unknown",
            "upcomingSupplyUnits": 0,
            "productMix": [
              {
                "type": "Unit Type",
                "productType": "apartment|officetel_residential|officetel_office|mixed_use_residential|commercial_retail|knowledge_industry_center|section_office|serviced_residence|other_saleable",
                "exclusiveAreaPy": 0,
                "contractAreaPy": 0,
                "units": 0,
                "pricePerPy": 0,
                "totalRevenue": 0,
                "evidenceIds": ["document-page-or-evidence-id"],
                "sourceLocator": {
                  "pageNumber": 0,
                  "table": "Table name or index",
                  "row": "Row label or index",
                  "column": "Column label",
                  "fieldName": "pricePerPy|totalRevenue|units|contractAreaPy",
                  "textExcerpt": "Short source excerpt",
                  "confidence": 0.0
                }
              }
            ],
            "comparables": [],
            "manualComparables": [],
            "transactionComparables": [],
            "presaleComparables": [],
            "listingComparables": [],
            "rentComparables": [],
            "rentalAssumptions": {
              "monthlyRentPerPy": 0,
              "requiredYield": 0.0,
              "sourceUrlOrEvidenceId": "document-page-or-evidence-id",
              "sourceLocator": {
                "pageNumber": 0,
                "table": "Table name or index",
                "row": "Row label or index",
                "column": "Column label",
                "fieldName": "monthlyRentPerPy|requiredYield",
                "textExcerpt": "Short source excerpt",
                "confidence": 0.0
              }
            },
            "data_sources": []
          },
          "cost_assumptions": {
            "construction_cost_per_sqm": 0
          },
          "feasibility_input": {
            "total_revenue": 0,
            "total_cost": 0,
            "land_cost": 0,
            "construction_cost": 0,
            "finance_cost": 0,
            "other_cost": 0
          },
          "pf_structure": [
            {
              "tranche": "Tranche Name (e.g., Tranche A)",
              "amount": 0,
              "interest_rate": 0.0,
              "fee_rate": 0.0,
              "priority": 1,
              "all_in_cost": 0.0
            }
          ],
          "developer_info": {
            "name": "Developer Name",
            "credit_rating": "Rating",
            "financials": "Summary"
          },
          "contractor_info": {
            "name": "Contractor Name",
            "credit_rating": "Rating",
            "order_backlog": "Backlog",
            "financials": "Summary"
          },
          "schedule": {
            "land_purchase": "YYYY-MM",
            "permit_approval": "YYYY-MM",
            "construction_start": "YYYY-MM",
            "completion": "YYYY-MM"
          },
          "covenants": {
            "claim_preservation": ["Item 1", "Item 2"],
            "eod": ["EOD 1", "EOD 2"],
            "drawdown_conditions": ["Cond 1", "Cond 2"],
            "execution_order": ["1. Item", "2. Item"]
          },
          "source_locators": {
            "project.name": {
              "pageNumber": 0,
              "table": "Table name or index",
              "row": "Row label or index",
              "column": "Column label",
              "fieldName": "project.name",
              "textExcerpt": "Short source excerpt",
              "confidence": 0.0
            },
            "land.address": {},
            "land.area_sqm": {},
            "regulation.applied_far": {},
            "architectural_plan.gross_floor_area_sqm": {},
            "sales_assumptions.avg_price_per_sqm": {},
            "feasibility_input.total_revenue": {},
            "pf_structure[0].amount": {},
            "schedule.completion": {},
            "covenants.drawdown_conditions": {}
          }
        }

        Source locator rule:
        - For every extracted project, land, regulation, architectural, sales, cost, feasibility, PF, schedule, covenant, and market field, include pageNumber, table, row, column, fieldName, textExcerpt, and confidence whenever the source text supports it.
        - If a value is inferred and no document location exists, leave the locator empty and keep confidence conservative.

        Business Plan Text:
        ---
        """ + text + "\n---"

    def _get_host_llm_extraction_request(
        self,
        chunks: Sequence[str],
        prompts: Sequence[str],
        ai_context: Dict[str, Any],
        *,
        source_text_sha256: str,
    ) -> dict:
        extraction_instructions = prompts[0].rsplit("Business Plan Text:", 1)[0].strip()
        chunk_payloads = [
            {
                "chunkIndex": index,
                "text": chunk,
                "sha256": _sha256_text(chunk),
            }
            for index, chunk in enumerate(chunks)
        ]
        return {
            "_meta": {
                "parsing_source": "host_llm_required",
                "confidence": None,
                "warning": (
                    "Claude Desktop is the MCP host LLM. The MCP server extracted PDF text "
                    "and is returning instructions for the host LLM to perform the JSON extraction."
                ),
                "ai_connection": ai_context,
                "sourceTextSha256": source_text_sha256,
                "chunkCount": len(chunk_payloads),
                "inputCoverage": "complete",
                "analysisOnly": True,
                "reportEligible": False,
            },
            "host_llm_extraction": {
                "instruction": (
                    "Extract each document chunk according to extraction_prompt, reconcile "
                    "conflicting values explicitly, and produce one project_data JSON. After extraction, call "
                    "orchestrate_pf_review or generate_pf_report with that project_data."
                ),
                "expected_output": "project_data_json",
                "document_text": chunks[0] if len(chunks) == 1 else None,
                "document_chunks": chunk_payloads,
                "extraction_prompt": extraction_instructions,
            },
        }

    def _get_mock_parsed_data(
        self,
        reason: str = "extraction unavailable",
        ai_context: Dict[str, Any] | None = None,
    ) -> dict:
        """Honest fallback when real AI extraction is unavailable.

        Per AGENTS.md (no fabrication): this returns NO invented project/financial
        values. It only reports that extraction did not run, so the caller leaves
        the project's real data untouched instead of injecting plausible-but-fake
        numbers (the old behaviour silently populated 200B revenue etc.).
        """
        resolved_context = resolve_ai_context(ai_context or self.ai_context)
        return {
          "_meta": {
            "parsing_source": "extraction_unavailable",
            "confidence": 0.0,
            "warning": "AI 추출을 사용할 수 없어 문서에서 수치를 추출하지 못했습니다. 가짜 값을 생성하지 않습니다.",
            "reason": reason,
            "requiresManualOrLlmExtraction": True,
            "ai_connection": resolved_context,
            "analysisOnly": True,
            "reportEligible": False,
          },
        }


def _merge_chunk_extractions(
    chunks: Sequence[tuple[int, Mapping[str, Any], Mapping[str, Any]]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    merged: dict[str, Any] = {}
    origins: dict[str, list[int]] = {}
    conflicts: dict[str, dict[str, Any]] = {}
    for chunk_index, payload, _meta in chunks:
        for key, value in payload.items():
            if key == "_meta":
                continue
            _merge_value(
                merged,
                key,
                copy.deepcopy(value),
                path=key,
                chunk_index=chunk_index,
                origins=origins,
                conflicts=conflicts,
            )
    return merged, [conflicts[key] for key in sorted(conflicts)]


def _merge_value(
    container: dict[str, Any],
    key: str,
    value: Any,
    *,
    path: str,
    chunk_index: int,
    origins: dict[str, list[int]],
    conflicts: dict[str, dict[str, Any]],
) -> None:
    if path in conflicts:
        _append_conflict_candidate(conflicts[path], value, chunk_index)
        return
    if key not in container or _is_empty(container[key]):
        if isinstance(value, Mapping):
            nested: dict[str, Any] = {}
            container[key] = nested
            _record_path_origin(path, chunk_index, origins)
            for child_key, child_value in value.items():
                _merge_value(
                    nested,
                    str(child_key),
                    copy.deepcopy(child_value),
                    path=f"{path}.{child_key}",
                    chunk_index=chunk_index,
                    origins=origins,
                    conflicts=conflicts,
                )
            return
        if isinstance(value, list) and path in _ENTITY_ARRAY_IDENTITY_KEYS:
            entity_items: list[Any] = []
            container[key] = entity_items
            _record_path_origin(path, chunk_index, origins)
            _merge_entity_array(
                entity_items,
                value,
                path=path,
                chunk_index=chunk_index,
                origins=origins,
                conflicts=conflicts,
            )
            return
        container[key] = value
        _record_value_origins(value, path, chunk_index, origins)
        return
    if _is_empty(value):
        return
    existing = container[key]
    if existing == value:
        _record_value_origins(value, path, chunk_index, origins)
        return
    if isinstance(existing, dict) and isinstance(value, Mapping):
        _record_path_origin(path, chunk_index, origins)
        for child_key, child_value in value.items():
            _merge_value(
                existing,
                str(child_key),
                copy.deepcopy(child_value),
                path=f"{path}.{child_key}",
                chunk_index=chunk_index,
                origins=origins,
                conflicts=conflicts,
            )
        return
    if isinstance(existing, list) and isinstance(value, list):
        _record_path_origin(path, chunk_index, origins)
        if path in _ENTITY_ARRAY_IDENTITY_KEYS:
            _merge_entity_array(
                existing,
                value,
                path=path,
                chunk_index=chunk_index,
                origins=origins,
                conflicts=conflicts,
            )
        else:
            _merge_exact_union(existing, value)
        return
    conflict = {
        "fieldPath": path,
        "status": "open",
        "candidates": [],
    }
    for origin in origins.get(path, [-1]):
        _append_conflict_candidate(conflict, existing, origin)
    _append_conflict_candidate(conflict, value, chunk_index)
    conflicts[path] = conflict
    container[key] = None


def _append_conflict_candidate(
    conflict: dict[str, Any],
    value: Any,
    chunk_index: int,
) -> None:
    signature = _stable_json(value)
    if any(
        item.get("valueHash") == signature and item.get("chunkIndex") == chunk_index
        for item in conflict["candidates"]
    ):
        return
    conflict["candidates"].append(
        {
            "chunkIndex": chunk_index,
            "value": copy.deepcopy(value),
            "valueHash": signature,
        }
    )


def _record_value_origins(
    value: Any,
    path: str,
    chunk_index: int,
    origins: dict[str, list[int]],
) -> None:
    _record_path_origin(path, chunk_index, origins)

    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            _record_value_origins(
                child_value,
                f"{path}.{child_key}",
                chunk_index,
                origins,
            )
    elif isinstance(value, list) and path in _ENTITY_ARRAY_IDENTITY_KEYS:
        for item in value:
            if not isinstance(item, Mapping):
                continue
            identity = _entity_identity(path, item)
            if identity is None:
                continue
            _record_value_origins(
                item,
                _entity_item_path(path, identity),
                chunk_index,
                origins,
            )


def _record_path_origin(
    path: str,
    chunk_index: int,
    origins: dict[str, list[int]],
) -> None:
    path_origins = origins.setdefault(path, [])
    if chunk_index not in path_origins:
        path_origins.append(chunk_index)


def _merge_entity_array(
    existing: list[Any],
    incoming: list[Any],
    *,
    path: str,
    chunk_index: int,
    origins: dict[str, list[int]],
    conflicts: dict[str, dict[str, Any]],
) -> None:
    identity_index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in existing:
        if not isinstance(item, dict):
            continue
        identity = _entity_identity(path, item)
        if identity is not None:
            identity_index[_entity_identity_token(identity)] = item

    unkeyed_signatures = {
        _stable_json(item)
        for item in existing
        if not isinstance(item, Mapping) or _entity_identity(path, item) is None
    }
    for item in incoming:
        if not isinstance(item, Mapping):
            signature = _stable_json(item)
            if signature not in unkeyed_signatures:
                unkeyed_signatures.add(signature)
                existing.append(copy.deepcopy(item))
            continue
        identity = _entity_identity(path, item)
        if identity is None:
            signature = _stable_json(item)
            if signature not in unkeyed_signatures:
                unkeyed_signatures.add(signature)
                existing.append(copy.deepcopy(item))
            continue

        identity_token = _entity_identity_token(identity)
        existing_item = identity_index.get(identity_token)
        item_path = _entity_item_path(path, identity)
        if existing_item is None:
            copied_item = copy.deepcopy(dict(item))
            existing.append(copied_item)
            identity_index[identity_token] = copied_item
            _record_value_origins(copied_item, item_path, chunk_index, origins)
            continue
        for child_key, child_value in item.items():
            _merge_value(
                existing_item,
                str(child_key),
                copy.deepcopy(child_value),
                path=f"{item_path}.{child_key}",
                chunk_index=chunk_index,
                origins=origins,
                conflicts=conflicts,
            )


def _merge_exact_union(existing: list[Any], incoming: list[Any]) -> None:
    seen = {_stable_json(item) for item in existing}
    for item in incoming:
        signature = _stable_json(item)
        if signature not in seen:
            seen.add(signature)
            existing.append(copy.deepcopy(item))


def _entity_identity(
    path: str,
    item: Mapping[str, Any],
) -> tuple[str, Any] | None:
    for identity_key in _ENTITY_ARRAY_IDENTITY_KEYS.get(path, ()):
        identity_value = item.get(identity_key)
        if _is_empty(identity_value) or isinstance(
            identity_value,
            (Mapping, list, tuple, set),
        ):
            continue
        return identity_key, identity_value
    return None


def _entity_identity_token(identity: tuple[str, Any]) -> tuple[str, str]:
    identity_key, identity_value = identity
    return identity_key, _stable_json(identity_value)


def _entity_item_path(path: str, identity: tuple[str, Any]) -> str:
    identity_key, identity_value = identity
    rendered_value = json.dumps(
        identity_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"{path}[{identity_key}={rendered_value}]"


def _stable_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _has_meaningful_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_has_meaningful_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_meaningful_value(item) for item in value)
    return not _is_empty(value)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
