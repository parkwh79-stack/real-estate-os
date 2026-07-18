from __future__ import annotations

"""Composable Parser V2 modules for governed feasibility-domain facts.

The modules in this file deliberately do not infer workbook semantics from a
cell label or position.  They accept a structured extraction batch whose rows
already identify a governed canonical field, then independently revalidate the
field, value, unit, basis, entity identity, and immutable source locator.
Anything incomplete is reported as an abstention diagnostic and never emitted
as a ``ParsedFactCandidate``.

This is a candidate-only foundation.  It is not an approval projector and it
does not claim support for fields listed under ``pendingCapabilities``.
"""

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from pydantic import ValidationError

from parsing_platform.contracts import ParsedFactCandidate, SourceLocator
from parsing_platform.document_taxonomy import (
    DOCUMENT_TAXONOMY_VERSION,
    canonical_document_type,
)
from parsing_platform.field_registry import (
    FIELD_DEFINITIONS,
    SUPPORTED_ONTOLOGY_VERSIONS,
    validate_candidate_against_registry,
)
from parsing_platform.orchestrator import ParsingOrchestrator, ProjectParsingProfile
from parsing_platform.registry import ParserModuleManifest, ParserModuleRegistry
from parsing_platform.schema_contracts import (
    SchemaContractRegistry,
    build_default_schema_contract_registry,
)


FEASIBILITY_DOMAIN_PACK_VERSION = "realytics-feasibility-domain-pack-v1"
FEASIBILITY_DOMAIN_INPUT_SCHEMA = (
    "realytics-feasibility-domain-module-input-v1"
)
FEASIBILITY_DOMAIN_OUTPUT_SCHEMA = (
    "realytics-feasibility-domain-module-output-v1"
)
CANONICAL_WORKBOOK_MAPPING_INPUT_SCHEMA = (
    "realytics-canonical-workbook-mapping-input-v1"
)
CANONICAL_WORKBOOK_MAPPING_OUTPUT_SCHEMA = (
    "realytics-canonical-workbook-mapping-output-v1"
)
CANONICAL_WORKBOOK_MAPPING_VERSION = "realytics-canonical-workbook-mapping-v1"
STRUCTURED_FACT_BATCH_VERSION = "realytics-structured-fact-batch-v1"
RECONCILIATION_POLICY_VERSION = (
    "realytics-feasibility-reconciliation-policy-v1"
)
ENTITY_COMPLETENESS_POLICY_VERSION = (
    "realytics-feasibility-entity-completeness-policy-v1"
)
EXPECTED_ENTITY_POLICY_VERSION = (
    "realytics-expected-entity-coverage-policy-v1"
)
DOMAIN_CANDIDATE_CONTENT_HASH_VERSION = (
    "realytics-domain-candidate-content-hash-v1"
)
DEFAULT_ONTOLOGY_VERSION = "realytics-real-estate-domain-standard-v1"
MAX_STRUCTURED_FACT_ROWS = 20_000

DOMAIN_SCAN_CAPABILITY = "feasibility.domain.scan_completed"
STRUCTURED_FACT_CAPABILITY = "document.canonical_structured_facts"

CANONICAL_WORKBOOK_MAPPING_MODULE_ID = "canonical_workbook_fact_mapping"
PRODUCT_MIX_MODULE_ID = "feasibility_product_mix"
COST_MODULE_ID = "feasibility_cost"
SCHEDULE_MODULE_ID = "feasibility_schedule"
SALES_COLLECTION_MODULE_ID = "feasibility_sales_collection"
PF_TERMS_MODULE_ID = "feasibility_pf_terms"
RECONCILIATION_MODULE_ID = "feasibility_reconciliation"

DOMAIN_MODULE_IDS = (
    PRODUCT_MIX_MODULE_ID,
    COST_MODULE_ID,
    SCHEDULE_MODULE_ID,
    SALES_COLLECTION_MODULE_ID,
    PF_TERMS_MODULE_ID,
)
ALL_FEASIBILITY_MODULE_IDS = (
    CANONICAL_WORKBOOK_MAPPING_MODULE_ID,
    *DOMAIN_MODULE_IDS,
    RECONCILIATION_MODULE_ID,
)


PRODUCT_MIX_FIELDS = frozenset(
    {
        "program.gross_floor_area_sqm",
        "program.product.asset_type",
        "program.product.name",
        "program.product.unit_count",
        "program.product.exclusive_area_sqm",
        "program.product.saleable_area_sqm",
        "program.product.contract_area_sqm",
        "revenue.product.average_sale_price_per_sqm",
        "revenue.product.average_sale_price_per_py",
        "revenue.product.total_sales_revenue",
    }
)
COST_FIELDS = frozenset(
    {
        "cost.total_project_cost",
        "cost.land_cost",
        "cost.line_item.name",
        "cost.line_item.category",
        "cost.line_item.amount",
    }
)
SCHEDULE_FIELDS = frozenset(
    {
        "schedule.milestone.name",
        "schedule.milestone.type",
        "schedule.milestone.planned_date",
        "schedule.milestone.actual_date",
        "schedule.milestone.month_index",
        # Compatibility with the governed monthly cash-flow schedule contract.
        "cash_flow.month_index",
        "cash_flow.period_start_date",
    }
)
SALES_COLLECTION_FIELDS = frozenset(
    {
        "revenue.total_sales_revenue",
        "sales_collection.step.stage",
        "sales_collection.step.sequence",
        "sales_collection.step.collection_ratio",
        "sales_collection.step.due_month_index",
        "sales_collection.step.due_date",
        "sales_collection.step.product_entity_id",
        # Compatibility with the governed monthly cash-flow collection fact.
        "cash_flow.monthly_inflow",
    }
)
PF_TERMS_FIELDS = frozenset(
    {
        "financing.pf_amount",
        "financing.tranche.name",
        "financing.tranche.type",
        "financing.tranche.loan_amount",
        "financing.tranche.interest_rate",
        "financing.tranche.interest_rate_type",
        "financing.tranche.base_rate_name",
        "financing.tranche.spread_rate",
        "financing.tranche.fee_rate",
        "financing.tranche.maturity_date",
        "financing.tranche.term_months",
        "financing.tranche.priority",
        "financing.tranche.draw_priority",
        "financing.tranche.repayment_priority",
        "financing.tranche.repayment_method",
        "financing.tranche.repayment_source",
    }
)


def canonical_workbook_mapping_hash(mapping: Mapping[str, Any]) -> str:
    return _sha256_json(dict(mapping))


def build_structured_fact_lineage(
    *,
    mapping_attestation_hash: str,
    mapping_entry: Mapping[str, Any],
    value: Any,
    source_locator: Mapping[str, Any],
) -> dict[str, Any]:
    """Create stable mapping/unit/basis and normalization lineage."""

    canonical_locator = SourceLocator.model_validate(
        dict(source_locator)
    ).model_dump(by_alias=True, mode="json", exclude_none=True)
    entry_material = {
        key: item
        for key, item in dict(mapping_entry).items()
        if key not in {"rowId", "metadata"}
    }
    entry_hash = _sha256_json(entry_material)
    field_key = str(mapping_entry.get("fieldKey") or "")
    entity_id = _optional_string(mapping_entry.get("entityId"))
    unit = mapping_entry.get("unit")
    basis = mapping_entry.get("basis")
    input_hash = _sha256_json(
        {
            "mappingEntryHash": entry_hash,
            "rawValue": value,
            "sourceLocator": canonical_locator,
        }
    )
    output_hash = _sha256_json(
        {
            "fieldKey": field_key,
            "entityId": entity_id,
            "normalizedValue": value,
            "unit": unit,
            "basis": basis,
        }
    )
    return {
        "sourceRowId": f"mapping-entry:{entry_hash}",
        "mappingAttestationHash": mapping_attestation_hash,
        "mappingEntryHash": entry_hash,
        "normalizationRuleId": "literal_identity",
        "normalizationRuleVersion": "1.0.0",
        "normalizationInputHash": input_hash,
        "normalizationOutputHash": output_hash,
        "unitEvidenceMode": (
            "immutable_profile_mapping_attestation"
            if unit is not None
            else "not_applicable"
        ),
        "basisEvidenceMode": (
            "immutable_profile_mapping_attestation"
            if basis is not None
            else "not_applicable"
        ),
    }


def build_domain_candidate_content_hash(
    candidate_payload: Mapping[str, Any],
) -> str:
    """Hash run-independent semantic candidate content."""

    payload = _json_safe(dict(candidate_payload))
    metadata = dict(payload.get("metadata") or {})
    metadata.pop("candidateContentHash", None)
    # The normalization input attestation binds the record to one immutable
    # document-version locator.  The semantic content hash intentionally
    # excludes that run/document identity so an unchanged fact can be
    # compared across repeat parses; the record ID still binds the run.
    metadata.pop("normalizationInputHash", None)
    evidence = [
        _semantic_locator(item)
        for item in payload.get("evidence") or []
        if isinstance(item, Mapping)
    ]
    material = {
        "contentHashVersion": DOMAIN_CANDIDATE_CONTENT_HASH_VERSION,
        "fieldKey": payload.get("fieldKey"),
        "entityId": payload.get("entityId"),
        "rawValue": payload.get("rawValue"),
        "normalizedValue": payload.get("normalizedValue"),
        "unit": payload.get("unit"),
        "originType": payload.get("originType"),
        "presenceStatus": payload.get("presenceStatus"),
        "authority": payload.get("authority"),
        "confidence": payload.get("confidence"),
        "effectiveDate": payload.get("effectiveDate"),
        "asOfDate": payload.get("asOfDate"),
        "formulaId": payload.get("formulaId"),
        "formulaVersion": payload.get("formulaVersion"),
        "inputFactIds": payload.get("inputFactIds") or [],
        "calculationRunId": payload.get("calculationRunId"),
        "calculationEngineVersion": payload.get(
            "calculationEngineVersion"
        ),
        "calculationOutputPath": payload.get("calculationOutputPath"),
        "calculationOutputHash": payload.get("calculationOutputHash"),
        "inputSnapshotHash": payload.get("inputSnapshotHash"),
        "supersedesCandidateId": payload.get("supersedesCandidateId"),
        "evidence": evidence,
        "parserVersion": payload.get("parserVersion"),
        "ontologyVersion": payload.get("ontologyVersion"),
        "promptVersion": payload.get("promptVersion"),
        "modelVersion": payload.get("modelVersion"),
        "metadata": metadata,
    }
    return _sha256_json(material)


def build_domain_candidate_id(candidate_payload: Mapping[str, Any]) -> str:
    """Build a run record ID from run ID plus semantic content hash."""

    content_hash = build_domain_candidate_content_hash(candidate_payload)
    run_id = str(
        candidate_payload.get("parsingRunId")
        or candidate_payload.get("parsing_run_id")
        or ""
    ).strip()
    if not run_id:
        raise ValueError("parsingRunId is required for a candidate record ID")
    digest = hashlib.sha256(f"{run_id}|{content_hash}".encode("utf-8")).hexdigest()
    return "candidate:" + digest


def build_feasibility_domain_schema_registry() -> SchemaContractRegistry:
    registry = build_default_schema_contract_registry()
    registry.register(
        CANONICAL_WORKBOOK_MAPPING_INPUT_SCHEMA,
        _validate_workbook_mapping_input_contract,
        definition={
            "type": "module-envelope",
            "mappingVersion": CANONICAL_WORKBOOK_MAPPING_VERSION,
            "valueSource": "structural_extraction.xlsx_literal_cell",
        },
        description="Explicit canonical workbook cell mapping",
    )
    registry.register(
        CANONICAL_WORKBOOK_MAPPING_OUTPUT_SCHEMA,
        _validate_workbook_mapping_output_contract,
        definition={
            "type": "structured-fact-provider",
            "structuredFactBatchVersion": STRUCTURED_FACT_BATCH_VERSION,
            "formulaPolicy": "abstain",
        },
        description="Literal workbook cells mapped to governed structured rows",
    )
    registry.register(
        FEASIBILITY_DOMAIN_INPUT_SCHEMA,
        _validate_domain_input_contract,
        definition={
            "type": "module-envelope",
            "structuredFactBatchVersion": STRUCTURED_FACT_BATCH_VERSION,
            "maxRows": MAX_STRUCTURED_FACT_ROWS,
        },
        description="Candidate-only structured feasibility fact batch",
    )
    registry.register(
        FEASIBILITY_DOMAIN_OUTPUT_SCHEMA,
        _validate_domain_output_contract,
        definition={
            "type": "candidate-batch-or-reconciliation-report",
            "candidateContract": "realytics-parsed-fact-candidate-v2",
            "authoritative": False,
        },
        description="Governed feasibility candidates and abstention diagnostics",
    )
    return registry


class CanonicalWorkbookFactMappingModule:
    """Map explicit governed cell declarations to immutable structured rows.

    This provider performs no header guessing and no semantic inference.  Each
    mapping entry must name one canonical field and one literal XLSX cell.
    """

    manifest = ParserModuleManifest(
        module_id=CANONICAL_WORKBOOK_MAPPING_MODULE_ID,
        version="1.0.0",
        capabilities=("document.canonical_workbook_mapping",),
        supported_document_types=(
            "budget",
            "cashflow_workbook",
            "feasibility_workbook",
            "risk_report_workbook",
            "sales_plan",
        ),
        document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
        requires=("document.structure", "document.classified"),
        provides=("document.canonical_mapping_scan_completed",),
        conditional_provides=(STRUCTURED_FACT_CAPABILITY,),
        execution_class="deterministic",
        input_schema=CANONICAL_WORKBOOK_MAPPING_INPUT_SCHEMA,
        output_schema=CANONICAL_WORKBOOK_MAPPING_OUTPUT_SCHEMA,
        required_locator_precision="exact",
        data_classification="restricted",
        max_output_bytes=20 * 1024 * 1024,
        metadata={
            "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
            "mappingContractVersion": CANONICAL_WORKBOOK_MAPPING_VERSION,
            "semanticInferenceAllowed": False,
            "formulaPolicy": "abstain",
            "authoritative": False,
        },
    )

    def execute(
        self,
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        del context
        root = payload.get("input")
        root = root if isinstance(root, Mapping) else {}
        mapping = root.get("canonicalWorkbookMapping")
        diagnostics: list[dict[str, Any]] = []
        structured_rows: list[dict[str, Any]] = []
        if not isinstance(mapping, Mapping):
            diagnostics.append(
                _diagnostic(
                    "CANONICAL_WORKBOOK_MAPPING_REQUIRED",
                    "A versioned explicit workbook mapping is required.",
                )
            )
            return self._output(structured_rows, diagnostics, 0)
        if mapping.get("schemaVersion") != CANONICAL_WORKBOOK_MAPPING_VERSION:
            diagnostics.append(
                _diagnostic(
                    "CANONICAL_WORKBOOK_MAPPING_VERSION_UNSUPPORTED",
                    "The workbook mapping version is not supported.",
                )
            )
            return self._output(structured_rows, diagnostics, 0)
        mapping_attestation_hash = canonical_workbook_mapping_hash(mapping)

        mapped_document_type = mapping.get("documentType")
        root_document_type = root.get("documentType")
        if mapped_document_type != root_document_type:
            diagnostics.append(
                _diagnostic(
                    "CANONICAL_WORKBOOK_DOCUMENT_TYPE_MISMATCH",
                    "The mapping and parser input document types do not match.",
                )
            )
            return self._output(structured_rows, diagnostics, 0)

        chunks = _structural_chunks(payload)
        cell_index: dict[tuple[str, str], Mapping[str, Any]] = {}
        duplicate_cells: set[tuple[str, str]] = set()
        for chunk in chunks:
            if not isinstance(chunk, Mapping) or chunk.get("kind") != "worksheet_cell":
                continue
            locator = chunk.get("sourceLocator")
            if not isinstance(locator, Mapping):
                continue
            sheet_name = str(locator.get("sheetName") or "")
            cell_reference = str(locator.get("cellReference") or "").upper()
            key = (sheet_name, cell_reference)
            if not sheet_name or not cell_reference:
                continue
            if key in cell_index:
                duplicate_cells.add(key)
            else:
                cell_index[key] = chunk

        entries = mapping.get("entries")
        if not isinstance(entries, Sequence) or isinstance(
            entries, (str, bytes, bytearray)
        ):
            diagnostics.append(
                _diagnostic(
                    "CANONICAL_WORKBOOK_MAPPING_ENTRIES_INVALID",
                    "Workbook mapping entries must be a list.",
                )
            )
            return self._output(structured_rows, diagnostics, 0)
        for index, raw_entry in enumerate(entries):
            row_id = f"mapping:{index}"
            if not isinstance(raw_entry, Mapping):
                diagnostics.append(
                    _diagnostic(
                        "CANONICAL_WORKBOOK_MAPPING_ENTRY_INVALID",
                        "A workbook mapping entry must be an object.",
                        row_id=row_id,
                    )
                )
                continue
            entry = dict(raw_entry)
            field_key = str(entry.get("fieldKey") or "").strip()
            entity_id = _optional_string(entry.get("entityId"))
            preliminary_entry_hash = _sha256_json(
                {
                    key: item
                    for key, item in entry.items()
                    if key not in {"rowId", "metadata"}
                }
            )
            row_id = f"mapping-entry:{preliminary_entry_hash}"
            sheet_name = str(entry.get("sheetName") or "")
            cell_reference = str(entry.get("cellReference") or "").upper()
            if (
                not sheet_name
                or re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]*", cell_reference)
                is None
            ):
                diagnostics.append(
                    _diagnostic(
                        "CANONICAL_WORKBOOK_CELL_LOCATOR_INVALID",
                        "A mapping entry requires one exact A1-style literal cell.",
                        row_id=row_id,
                        field_key=field_key or None,
                        entity_id=entity_id,
                    )
                )
                continue
            key = (sheet_name, cell_reference)
            if key in duplicate_cells:
                diagnostics.append(
                    _diagnostic(
                        "CANONICAL_WORKBOOK_CELL_AMBIGUOUS",
                        "The structural artifact contains a duplicate cell locator.",
                        row_id=row_id,
                        field_key=field_key or None,
                        entity_id=entity_id,
                    )
                )
                continue
            chunk = cell_index.get(key)
            if chunk is None:
                diagnostics.append(
                    _diagnostic(
                        "CANONICAL_WORKBOOK_CELL_NOT_FOUND",
                        "The mapped cell was not present in the structural artifact.",
                        row_id=row_id,
                        field_key=field_key or None,
                        entity_id=entity_id,
                    )
                )
                continue
            locator = chunk.get("sourceLocator")
            metadata = chunk.get("metadata")
            cells = (
                metadata.get("cells")
                if isinstance(metadata, Mapping)
                else None
            )
            if (
                not isinstance(locator, Mapping)
                or not isinstance(cells, Sequence)
                or len(cells) != 1
                or not isinstance(cells[0], Mapping)
            ):
                diagnostics.append(
                    _diagnostic(
                        "CANONICAL_WORKBOOK_CELL_STRUCTURE_INVALID",
                        "The mapped structural cell did not have one typed value.",
                        row_id=row_id,
                        field_key=field_key or None,
                        entity_id=entity_id,
                    )
                )
                continue
            cell = cells[0]
            if (
                cell.get("formula") is not None
                or locator.get("workbookFormula") is not None
                or cell.get("approvalEligible") is False
            ):
                diagnostics.append(
                    _diagnostic(
                        "WORKBOOK_FORMULA_RECALCULATION_REQUIRED",
                        "Mapped formula cells require deterministic recalculation lineage.",
                        row_id=row_id,
                        field_key=field_key or None,
                        entity_id=entity_id,
                    )
                )
                continue
            value = cell.get("value")
            if value is None:
                diagnostics.append(
                    _diagnostic(
                        "CANONICAL_WORKBOOK_CELL_VALUE_MISSING",
                        "The mapped literal cell did not contain a value.",
                        row_id=row_id,
                        field_key=field_key or None,
                        entity_id=entity_id,
                    )
                )
                continue
            lineage = build_structured_fact_lineage(
                mapping_attestation_hash=mapping_attestation_hash,
                mapping_entry=entry,
                value=value,
                source_locator=locator,
            )
            row_id = str(lineage["sourceRowId"])
            structured_row = {
                "rowId": row_id,
                "domain": entry.get("domain"),
                "fieldKey": field_key,
                "entityId": entity_id,
                "rawValue": value,
                "normalizedValue": value,
                "unit": entry.get("unit"),
                "basis": entry.get("basis"),
                "authority": entry.get("authority"),
                "documentType": mapped_document_type,
                "originType": "extracted",
                "sourceLocator": dict(locator),
                "metadata": lineage,
            }
            structured_rows.append(
                {
                    key: item
                    for key, item in structured_row.items()
                    if item is not None
                }
            )
        return self._output(structured_rows, diagnostics, len(entries))

    def validate_output(self, output: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(output)
        if result.get("moduleId") != self.manifest.module_id:
            raise ValueError("workbook mapping output moduleId mismatch")
        return result

    def _output(
        self,
        rows: list[dict[str, Any]],
        diagnostics: list[dict[str, Any]],
        entry_count: int,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": CANONICAL_WORKBOOK_MAPPING_OUTPUT_SCHEMA,
            "structuredFactBatchVersion": STRUCTURED_FACT_BATCH_VERSION,
            "moduleId": self.manifest.module_id,
            "moduleVersion": self.manifest.version,
            "candidateOnly": True,
            "authoritative": False,
            "structuredFactRows": rows,
            "diagnostics": diagnostics,
            "summary": {
                "mappingEntryCount": entry_count,
                "structuredFactRowCount": len(rows),
                "abstainedCount": sum(
                    1 for item in diagnostics if item.get("blocking")
                ),
            },
            "providedCapabilities": (
                [STRUCTURED_FACT_CAPABILITY] if rows else []
            ),
        }


class _StructuredFactDomainModule:
    manifest: ClassVar[ParserModuleManifest]
    domain: ClassVar[str]
    field_keys: ClassVar[frozenset[str]]
    candidate_capability: ClassVar[str]
    pending_capabilities: ClassVar[tuple[str, ...]] = ()

    def execute(
        self,
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        rows = _structured_fact_rows(payload)
        diagnostics: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []

        batch_document_type, type_diagnostics = _document_type(
            payload,
            self.manifest,
        )
        diagnostics.extend(type_diagnostics)
        identity, identity_diagnostics = _candidate_identity(payload, context)
        diagnostics.extend(identity_diagnostics)

        if rows is None:
            diagnostics.append(
                _diagnostic(
                    "STRUCTURED_FACT_BATCH_REQUIRED",
                    "A versioned structured fact batch is required; the module abstained.",
                )
            )
            return self._output(
                candidates=candidates,
                diagnostics=diagnostics,
                input_row_count=0,
                matched_row_count=0,
            )
        if not rows:
            diagnostics.append(
                _diagnostic(
                    "STRUCTURED_FACT_BATCH_EMPTY",
                    "The structured fact batch contained no rows; the module abstained.",
                )
            )
        if batch_document_type is None or identity is None:
            return self._output(
                candidates=candidates,
                diagnostics=diagnostics,
                input_row_count=len(rows),
                matched_row_count=0,
            )

        matched = 0
        for index, raw_row in enumerate(rows):
            if not isinstance(raw_row, Mapping):
                diagnostics.append(
                    _diagnostic(
                        "STRUCTURED_FACT_ROW_INVALID",
                        "A structured fact row must be an object.",
                        row_id=f"row:{index}",
                    )
                )
                continue
            row = dict(raw_row)
            row_id = _opaque_structured_row_id(row, index)
            row_domain = str(row.get("domain") or "").strip()
            raw_field_key = str(row.get("fieldKey") or "").strip()
            if row_domain != self.domain:
                if not row_domain and raw_field_key in self.field_keys:
                    diagnostics.append(
                        _diagnostic(
                            "DOMAIN_IDENTITY_REQUIRED",
                            "A governed structured fact row requires an explicit domain.",
                            row_id=row_id,
                            field_key=raw_field_key or None,
                        )
                    )
                continue
            matched += 1
            candidate, row_diagnostics = self._candidate_from_row(
                row=row,
                row_id=row_id,
                identity=identity,
                batch_document_type=batch_document_type,
            )
            diagnostics.extend(row_diagnostics)
            if candidate is not None:
                candidates.append(candidate)

        return self._output(
            candidates=candidates,
            diagnostics=diagnostics,
            input_row_count=len(rows),
            matched_row_count=matched,
        )

    def validate_output(self, output: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(output)
        if result.get("moduleId") != self.manifest.module_id:
            raise ValueError("domain module output moduleId mismatch")
        if result.get("moduleVersion") != self.manifest.version:
            raise ValueError("domain module output moduleVersion mismatch")
        if result.get("domain") != self.domain:
            raise ValueError("domain module output domain mismatch")
        for candidate in result.get("candidates") or []:
            ParsedFactCandidate.model_validate(candidate)
        return result

    def _candidate_from_row(
        self,
        *,
        row: Mapping[str, Any],
        row_id: str,
        identity: Mapping[str, Any],
        batch_document_type: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        diagnostics: list[dict[str, Any]] = []
        raw_field_key = str(row.get("fieldKey") or "").strip()
        entity_id = _optional_string(row.get("entityId"))
        if not raw_field_key:
            return None, [
                _diagnostic(
                    "CANONICAL_FIELD_REQUIRED",
                    "A structured fact row requires a canonical field key.",
                    row_id=row_id,
                    entity_id=entity_id,
                )
            ]
        if raw_field_key not in self.field_keys:
            return None, [
                _diagnostic(
                    "FIELD_NOT_SUPPORTED_BY_MODULE",
                    "The field is outside this module's declared governed capability.",
                    row_id=row_id,
                    field_key=raw_field_key,
                    entity_id=entity_id,
                )
            ]
        if raw_field_key not in FIELD_DEFINITIONS:
            return None, [
                _diagnostic(
                    "CANONICAL_FIELD_UNREGISTERED",
                    "The field is not registered in the current governed field registry.",
                    row_id=row_id,
                    field_key=raw_field_key,
                    entity_id=entity_id,
                )
            ]

        row_document_type = row.get("documentType")
        if row_document_type is not None:
            try:
                normalized_row_document_type = canonical_document_type(
                    str(row_document_type)
                )
            except ValueError:
                return None, [
                    _diagnostic(
                        "DOCUMENT_TYPE_INVALID",
                        "The row document type is not part of the active taxonomy.",
                        row_id=row_id,
                        field_key=raw_field_key,
                        entity_id=entity_id,
                    )
                ]
            if normalized_row_document_type != batch_document_type:
                return None, [
                    _diagnostic(
                        "DOCUMENT_TYPE_MISMATCH",
                        "The row and batch document types do not match.",
                        row_id=row_id,
                        field_key=raw_field_key,
                        entity_id=entity_id,
                    )
                ]

        if row.get("originType", "extracted") != "extracted":
            return None, [
                _diagnostic(
                    "DETERMINISTIC_LINEAGE_REQUIRED",
                    "This foundation emits extracted facts only; derived facts require calculation lineage.",
                    row_id=row_id,
                    field_key=raw_field_key,
                    entity_id=entity_id,
                )
            ]
        asserted_authority = _optional_string(row.get("authority"))
        source_authority = str(identity.get("sourceAuthority") or "unknown")
        if (
            asserted_authority is not None
            and asserted_authority != source_authority
        ):
            return None, [
                _diagnostic(
                    "SOURCE_AUTHORITY_MISMATCH",
                    "A row cannot elevate or change the archived document authority.",
                    row_id=row_id,
                    field_key=raw_field_key,
                    entity_id=entity_id,
                )
            ]
        if "rawValue" not in row or "normalizedValue" not in row:
            return None, [
                _diagnostic(
                    "FACT_VALUE_REQUIRED",
                    "Both rawValue and normalizedValue are required for a present fact.",
                    row_id=row_id,
                    field_key=raw_field_key,
                    entity_id=entity_id,
                )
            ]
        try:
            raw_value = _json_safe(row["rawValue"])
            normalized_value = _json_safe(row["normalizedValue"])
        except (TypeError, ValueError):
            return None, [
                _diagnostic(
                    "FACT_VALUE_NOT_JSON",
                    "Fact values must be finite deterministic JSON values.",
                    row_id=row_id,
                    field_key=raw_field_key,
                    entity_id=entity_id,
                )
            ]

        metadata: dict[str, Any] = {
            "domainModuleId": self.manifest.module_id,
            "domainModuleVersion": self.manifest.version,
            "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
            "structuredInputContract": STRUCTURED_FACT_BATCH_VERSION,
            "sourceRowId": row_id,
            "candidateOnly": True,
            "authoritative": False,
            "candidateContentHashVersion": (
                DOMAIN_CANDIDATE_CONTENT_HASH_VERSION
            ),
        }
        raw_metadata = row.get("metadata")
        if raw_metadata is not None and not isinstance(raw_metadata, Mapping):
            return None, [
                _diagnostic(
                    "FACT_METADATA_INVALID",
                    "Structured fact metadata must be an object.",
                    row_id=row_id,
                    field_key=raw_field_key,
                    entity_id=entity_id,
                )
            ]
        metadata_basis = (
            dict(raw_metadata).get("basis")
            if isinstance(raw_metadata, Mapping)
            else None
        )
        direct_basis = row.get("basis")
        if (
            metadata_basis is not None
            and direct_basis is not None
            and metadata_basis != direct_basis
        ):
            return None, [
                _diagnostic(
                    "FACT_BASIS_CONFLICT",
                    "The row carries conflicting basis declarations.",
                    row_id=row_id,
                    field_key=raw_field_key,
                    entity_id=entity_id,
                )
            ]
        basis = direct_basis if direct_basis is not None else metadata_basis
        if basis is not None:
            if not isinstance(basis, Mapping):
                return None, [
                    _diagnostic(
                        "FACT_BASIS_INVALID",
                        "Fact basis must be a typed object.",
                        row_id=row_id,
                        field_key=raw_field_key,
                        entity_id=entity_id,
                    )
                ]
            metadata["basis"] = _json_safe(dict(basis))

        evidence, locator_diagnostics = _evidence(
            row=row,
            document_version_id=str(identity["documentVersionId"]),
            source_hash=str(identity["sourceHash"]),
            row_id=row_id,
            field_key=raw_field_key,
            entity_id=entity_id,
        )
        diagnostics.extend(locator_diagnostics)
        if diagnostics:
            return None, diagnostics
        unit = _optional_string(row.get("unit"))
        lineage_diagnostics = _validate_structured_fact_lineage(
            raw_metadata=raw_metadata,
            identity=identity,
            raw_value=raw_value,
            normalized_value=normalized_value,
            field_key=raw_field_key,
            entity_id=entity_id,
            unit=unit,
            basis=metadata.get("basis"),
            evidence=evidence,
            row_id=row_id,
        )
        if lineage_diagnostics:
            return None, lineage_diagnostics
        assert isinstance(raw_metadata, Mapping)
        for key in (
            "mappingAttestationHash",
            "mappingEntryHash",
            "normalizationRuleId",
            "normalizationRuleVersion",
            "normalizationInputHash",
            "normalizationOutputHash",
            "unitEvidenceMode",
            "basisEvidenceMode",
        ):
            metadata[key] = raw_metadata[key]

        registry_issues = validate_candidate_against_registry(
            ontology_version=str(identity["ontologyVersion"]),
            field_key=raw_field_key,
            normalized_value=normalized_value,
            unit=unit,
            entity_id=entity_id,
            evidence=evidence,
            presence_status="present",
            origin_type="extracted",
            metadata=metadata,
        )
        if registry_issues:
            return None, [
                _diagnostic(
                    item.code,
                    item.message,
                    row_id=row_id,
                    field_key=raw_field_key,
                    entity_id=entity_id,
                    blocking=item.blocking,
                    severity="error" if item.blocking else "warning",
                )
                for item in registry_issues
            ]

        provisional = {
            "schemaVersion": "realytics-parsed-fact-candidate-v2",
            "candidateId": "candidate:pending",
            "tenantId": identity["tenantId"],
            "projectId": identity["projectId"],
            "parsingRunId": identity["parsingRunId"],
            "documentVersionId": identity["documentVersionId"],
            "fieldKey": raw_field_key,
            "entityId": entity_id,
            "rawValue": raw_value,
            "normalizedValue": normalized_value,
            "unit": unit,
            "originType": "extracted",
            "presenceStatus": "present",
            "reviewStatus": "pending",
            "approvalStatus": "not_requested",
            "conflictStatus": "none",
            "confidence": row.get("confidence"),
            "authority": source_authority,
            "evidence": evidence,
            "validationCodes": [],
            "parserVersion": self.manifest.ref,
            "ontologyVersion": identity["ontologyVersion"],
            "promptVersion": "deterministic-structured-fact-v1",
            "modelVersion": "deterministic-no-model",
            "createdAt": identity["candidateCreatedAt"],
            "metadata": metadata,
        }
        try:
            parsed = ParsedFactCandidate.model_validate(provisional)
            wire = parsed.model_dump(
                by_alias=True,
                mode="json",
                exclude_none=True,
            )
            candidate_metadata = dict(wire.get("metadata") or {})
            candidate_metadata["candidateContentHash"] = (
                build_domain_candidate_content_hash(wire)
            )
            wire["metadata"] = candidate_metadata
            wire["candidateId"] = build_domain_candidate_id(wire)
            parsed = ParsedFactCandidate.model_validate(wire)
        except ValidationError:
            return None, [
                _diagnostic(
                    "PARSED_FACT_CONTRACT_REJECTED",
                    "The row did not satisfy the strict ParsedFactCandidate contract.",
                    row_id=row_id,
                    field_key=raw_field_key,
                    entity_id=entity_id,
                )
            ]
        return (
            parsed.model_dump(
                by_alias=True,
                mode="json",
                exclude_none=True,
            ),
            [],
        )

    def _output(
        self,
        *,
        candidates: list[dict[str, Any]],
        diagnostics: list[dict[str, Any]],
        input_row_count: int,
        matched_row_count: int,
    ) -> dict[str, Any]:
        provided = [self.candidate_capability] if candidates else []
        return {
            "schemaVersion": FEASIBILITY_DOMAIN_OUTPUT_SCHEMA,
            "outputKind": "candidate_batch",
            "moduleId": self.manifest.module_id,
            "moduleVersion": self.manifest.version,
            "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
            "domain": self.domain,
            "candidateOnly": True,
            "authoritative": False,
            "candidates": candidates,
            "diagnostics": diagnostics,
            "summary": {
                "inputRowCount": input_row_count,
                "matchedRowCount": matched_row_count,
                "candidateCount": len(candidates),
                "abstainedCount": sum(
                    1 for item in diagnostics if item.get("blocking")
                ),
            },
            "pendingCapabilities": list(self.pending_capabilities),
            "providedCapabilities": provided,
        }


class ProductMixParserModule(_StructuredFactDomainModule):
    domain = "product_mix"
    field_keys = PRODUCT_MIX_FIELDS
    candidate_capability = "feasibility.product_mix.candidates"
    pending_capabilities = (
        "mixed_use_area_allocation_reconciliation",
        "product_taxonomy_crosswalk",
    )
    manifest = ParserModuleManifest(
        module_id=PRODUCT_MIX_MODULE_ID,
        version="1.0.0",
        capabilities=("feasibility.product_mix.parsing",),
        supported_document_types=(
            "basic_architectural_plan",
            "business_plan",
            "feasibility_workbook",
            "sales_plan",
        ),
        document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
        requires=("document.classified", STRUCTURED_FACT_CAPABILITY),
        provides=(DOMAIN_SCAN_CAPABILITY, "feasibility.product_mix.scan_completed"),
        conditional_provides=(candidate_capability,),
        execution_class="deterministic",
        input_schema=FEASIBILITY_DOMAIN_INPUT_SCHEMA,
        output_schema=FEASIBILITY_DOMAIN_OUTPUT_SCHEMA,
        required_locator_precision="exact",
        data_classification="restricted",
        max_output_bytes=20 * 1024 * 1024,
        metadata={
            "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
            "implementationLevel": "governed_structured_fact_foundation",
            "unsupportedFieldPolicy": "abstain",
            "authoritative": False,
        },
    )


class CostParserModule(_StructuredFactDomainModule):
    domain = "cost"
    field_keys = COST_FIELDS
    candidate_capability = "feasibility.cost.candidates"
    pending_capabilities = (
        "icms_cost_classification_crosswalk",
        "construction_contract_change_order_reconciliation",
    )
    manifest = ParserModuleManifest(
        module_id=COST_MODULE_ID,
        version="1.0.0",
        capabilities=("feasibility.cost.parsing",),
        supported_document_types=(
            "budget",
            "business_plan",
            "construction_contract",
            "feasibility_workbook",
        ),
        document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
        requires=("document.classified", STRUCTURED_FACT_CAPABILITY),
        provides=(DOMAIN_SCAN_CAPABILITY, "feasibility.cost.scan_completed"),
        conditional_provides=(candidate_capability,),
        execution_class="deterministic",
        input_schema=FEASIBILITY_DOMAIN_INPUT_SCHEMA,
        output_schema=FEASIBILITY_DOMAIN_OUTPUT_SCHEMA,
        required_locator_precision="exact",
        data_classification="restricted",
        max_output_bytes=20 * 1024 * 1024,
        metadata={
            "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
            "implementationLevel": "governed_structured_fact_foundation",
            "unsupportedFieldPolicy": "abstain",
            "authoritative": False,
        },
    )


class ScheduleParserModule(_StructuredFactDomainModule):
    domain = "schedule"
    field_keys = SCHEDULE_FIELDS
    candidate_capability = "feasibility.schedule.candidates"
    pending_capabilities = (
        "critical_path_dependency_graph",
        "permit_construction_sales_schedule_alignment",
    )
    manifest = ParserModuleManifest(
        module_id=SCHEDULE_MODULE_ID,
        version="1.0.0",
        capabilities=("feasibility.schedule.parsing",),
        supported_document_types=(
            "business_plan",
            "cashflow_workbook",
            "feasibility_workbook",
            "progress_report",
            "schedule",
        ),
        document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
        requires=("document.classified", STRUCTURED_FACT_CAPABILITY),
        provides=(DOMAIN_SCAN_CAPABILITY, "feasibility.schedule.scan_completed"),
        conditional_provides=(candidate_capability,),
        execution_class="deterministic",
        input_schema=FEASIBILITY_DOMAIN_INPUT_SCHEMA,
        output_schema=FEASIBILITY_DOMAIN_OUTPUT_SCHEMA,
        required_locator_precision="exact",
        data_classification="restricted",
        max_output_bytes=20 * 1024 * 1024,
        metadata={
            "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
            "implementationLevel": "governed_structured_fact_foundation",
            "unsupportedFieldPolicy": "abstain",
            "authoritative": False,
        },
    )


class SalesCollectionParserModule(_StructuredFactDomainModule):
    domain = "sales_collection"
    field_keys = SALES_COLLECTION_FIELDS
    candidate_capability = "feasibility.sales_collection.candidates"
    pending_capabilities = (
        "contract_specific_collection_exception_rules",
        "presale_cancellation_and_refund_schedule",
    )
    manifest = ParserModuleManifest(
        module_id=SALES_COLLECTION_MODULE_ID,
        version="1.0.0",
        capabilities=("feasibility.sales_collection.parsing",),
        supported_document_types=(
            "business_plan",
            "cashflow_workbook",
            "feasibility_workbook",
            "sales_plan",
        ),
        document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
        requires=("document.classified", STRUCTURED_FACT_CAPABILITY),
        provides=(
            DOMAIN_SCAN_CAPABILITY,
            "feasibility.sales_collection.scan_completed",
        ),
        conditional_provides=(candidate_capability,),
        execution_class="deterministic",
        input_schema=FEASIBILITY_DOMAIN_INPUT_SCHEMA,
        output_schema=FEASIBILITY_DOMAIN_OUTPUT_SCHEMA,
        required_locator_precision="exact",
        data_classification="restricted",
        max_output_bytes=20 * 1024 * 1024,
        metadata={
            "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
            "implementationLevel": "governed_structured_fact_foundation",
            "unsupportedFieldPolicy": "abstain",
            "authoritative": False,
        },
    )


class PfTermsParserModule(_StructuredFactDomainModule):
    domain = "pf_terms"
    field_keys = PF_TERMS_FIELDS
    candidate_capability = "feasibility.pf_terms.candidates"
    pending_capabilities = (
        "covenant_and_condition_precedent_parser",
        "cash_sweep_and_mandatory_prepayment_parser",
        "interest_capitalization_and_fee_timing",
    )
    manifest = ParserModuleManifest(
        module_id=PF_TERMS_MODULE_ID,
        version="1.0.0",
        capabilities=("feasibility.pf_terms.parsing",),
        supported_document_types=(
            "business_plan",
            "credit_review_memo",
            "feasibility_workbook",
            "investment_memorandum",
            "loan_agreement",
            "term_sheet",
        ),
        document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
        requires=("document.classified", STRUCTURED_FACT_CAPABILITY),
        provides=(DOMAIN_SCAN_CAPABILITY, "feasibility.pf_terms.scan_completed"),
        conditional_provides=(candidate_capability,),
        execution_class="deterministic",
        input_schema=FEASIBILITY_DOMAIN_INPUT_SCHEMA,
        output_schema=FEASIBILITY_DOMAIN_OUTPUT_SCHEMA,
        required_locator_precision="exact",
        data_classification="restricted",
        max_output_bytes=20 * 1024 * 1024,
        metadata={
            "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
            "implementationLevel": "governed_structured_fact_foundation",
            "unsupportedFieldPolicy": "abstain",
            "authoritative": False,
        },
    )


class SameRunCrossFieldReconciliationModule:
    """Reconcile same-run candidate batches without claiming cross-run scope."""

    manifest = ParserModuleManifest(
        module_id=RECONCILIATION_MODULE_ID,
        version="1.0.0",
        capabilities=("feasibility.same_run_cross_field.reconciliation",),
        supported_document_types=tuple(
            sorted(
                {
                    *ProductMixParserModule.manifest.supported_document_types,
                    *CostParserModule.manifest.supported_document_types,
                    *ScheduleParserModule.manifest.supported_document_types,
                    *SalesCollectionParserModule.manifest.supported_document_types,
                    *PfTermsParserModule.manifest.supported_document_types,
                }
            )
        ),
        document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
        requires=(DOMAIN_SCAN_CAPABILITY,),
        provides=("feasibility.reconciliation.completed",),
        conditional_provides=("feasibility.reconciliation.clean",),
        execution_class="deterministic",
        input_schema=FEASIBILITY_DOMAIN_INPUT_SCHEMA,
        output_schema=FEASIBILITY_DOMAIN_OUTPUT_SCHEMA,
        required_locator_precision="exact",
        data_classification="restricted",
        max_output_bytes=10 * 1024 * 1024,
        metadata={
            "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
            "reconciliationPolicyVersion": RECONCILIATION_POLICY_VERSION,
            "entityCompletenessPolicyVersion": (
                ENTITY_COMPLETENESS_POLICY_VERSION
            ),
            "expectedEntityPolicyVersion": EXPECTED_ENTITY_POLICY_VERSION,
            "implementationLevel": "same-run-candidate-reconciliation",
            "authoritative": False,
            "pendingCapabilities": [
                "repository_wide_cross_run_authority_ranking",
                "multi_document_effective_date_precedence",
            ],
        },
    )

    def execute(
        self,
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        del context
        candidates: list[ParsedFactCandidate] = []
        diagnostics: list[dict[str, Any]] = []
        source_modules: list[str] = []
        results = payload.get("module_results")
        if isinstance(results, Mapping):
            for module_id, raw_result in sorted(
                results.items(), key=lambda item: str(item[0])
            ):
                if not isinstance(raw_result, Mapping):
                    continue
                output = raw_result.get("output")
                if not isinstance(output, Mapping):
                    continue
                if output.get("outputKind") != "candidate_batch":
                    continue
                source_modules.append(str(module_id))
                for raw_candidate in output.get("candidates") or []:
                    try:
                        candidates.append(
                            ParsedFactCandidate.model_validate(raw_candidate)
                        )
                    except ValidationError:
                        diagnostics.append(
                            _diagnostic(
                                "UPSTREAM_CANDIDATE_CONTRACT_INVALID",
                                "An upstream candidate failed strict contract revalidation.",
                            )
                        )

        diagnostics.extend(_duplicate_value_diagnostics(candidates))
        diagnostics.extend(_entity_completeness_diagnostics(candidates))
        diagnostics.extend(_aggregate_reconciliation_diagnostics(candidates))
        diagnostics.extend(
            _expected_entity_coverage_diagnostics(candidates, payload)
        )
        blocking = any(item.get("blocking") for item in diagnostics)
        provided = (
            ["feasibility.reconciliation.clean"]
            if candidates and not blocking
            else []
        )
        if len(source_modules) < 2:
            diagnostics.append(
                _diagnostic(
                    "RECONCILIATION_SOURCE_COVERAGE_LIMITED",
                    "Fewer than two domain modules supplied candidates.",
                    blocking=False,
                    severity="info",
                )
            )
        return {
            "schemaVersion": FEASIBILITY_DOMAIN_OUTPUT_SCHEMA,
            "outputKind": "reconciliation_report",
            "moduleId": self.manifest.module_id,
            "moduleVersion": self.manifest.version,
            "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
            "domain": "same_run_cross_field_reconciliation",
            "candidateOnly": True,
            "authoritative": False,
            "candidates": [],
            "diagnostics": diagnostics,
            "summary": {
                "sourceModuleCount": len(source_modules),
                "candidateCount": len(candidates),
                "blockingDiagnosticCount": sum(
                    1 for item in diagnostics if item.get("blocking")
                ),
            },
            "reconciliationPolicy": {
                "version": RECONCILIATION_POLICY_VERSION,
                "entityCompletenessPolicyVersion": (
                    ENTITY_COMPLETENESS_POLICY_VERSION
                ),
                "expectedEntityPolicyVersion": (
                    EXPECTED_ENTITY_POLICY_VERSION
                ),
                "monetaryAbsoluteToleranceKrw": 1,
                "ratioAbsoluteTolerance": 0.000001,
            },
            "sourceModuleIds": source_modules,
            "sourceCandidateIds": sorted(
                candidate.candidate_id for candidate in candidates
            ),
            "pendingCapabilities": list(
                self.manifest.metadata.get("pendingCapabilities") or []
            ),
            "providedCapabilities": provided,
        }

    def validate_output(self, output: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(output)
        if result.get("moduleId") != self.manifest.module_id:
            raise ValueError("reconciliation module output moduleId mismatch")
        if result.get("outputKind") != "reconciliation_report":
            raise ValueError("reconciliation module outputKind mismatch")
        return result


# Compatibility name for callers of the first P1 draft.  The manifest and wire
# contract explicitly describe the honest same-run/cross-field scope.
CrossDocumentReconciliationModule = SameRunCrossFieldReconciliationModule


def build_feasibility_domain_module_registry() -> ParserModuleRegistry:
    # Lazy import keeps this domain pack safe for optional registration from
    # default_modules without creating an import cycle.
    from parsing_platform.default_modules import build_default_parser_module_registry

    registry = build_default_parser_module_registry()
    for module in (
        CanonicalWorkbookFactMappingModule(),
        ProductMixParserModule(),
        CostParserModule(),
        ScheduleParserModule(),
        SalesCollectionParserModule(),
        PfTermsParserModule(),
        SameRunCrossFieldReconciliationModule(),
    ):
        registry.register(module)
    return registry


def build_feasibility_domain_orchestrator() -> ParsingOrchestrator:
    return ParsingOrchestrator(
        build_feasibility_domain_module_registry(),
        schema_registry=build_feasibility_domain_schema_registry(),
    )


def feasibility_domain_profile(
    *,
    project_id: str | None = None,
    document_types: Sequence[str] = ("feasibility_workbook",),
    domain_module_ids: Sequence[str] = DOMAIN_MODULE_IDS,
    include_intake_core: bool = True,
    include_reconciliation: bool = True,
    canonical_workbook_mapping: Mapping[str, Any] | None = None,
) -> ProjectParsingProfile:
    selected_domain_ids = tuple(dict.fromkeys(str(item) for item in domain_module_ids))
    unknown = sorted(set(selected_domain_ids).difference(DOMAIN_MODULE_IDS))
    if unknown:
        raise ValueError(
            "Unknown feasibility domain module IDs: " + ", ".join(unknown)
        )
    if include_reconciliation and not selected_domain_ids:
        raise ValueError("reconciliation requires at least one domain module")
    pinned_mapping: dict[str, Any] | None = None
    if canonical_workbook_mapping is not None:
        pinned_mapping = _json_safe(dict(canonical_workbook_mapping))
        if (
            pinned_mapping.get("schemaVersion")
            != CANONICAL_WORKBOOK_MAPPING_VERSION
        ):
            raise ValueError(
                "canonical_workbook_mapping must use "
                + CANONICAL_WORKBOOK_MAPPING_VERSION
            )
        if not isinstance(pinned_mapping.get("entries"), list):
            raise ValueError(
                "canonical_workbook_mapping.entries must be a list"
            )
    stages: list[dict[str, Any]] = []
    selected: list[str] = []
    if include_intake_core:
        selected.extend(
            (
                "document_inspection@1.0.0",
                "structural_extraction@1.0.0",
                "document_classification@1.0.0",
            )
        )
        stages.extend(
            (
                {
                    "stageId": "security",
                    "moduleRefs": ["document_inspection@1.0.0"],
                },
                {
                    "stageId": "structure",
                    "moduleRefs": ["structural_extraction@1.0.0"],
                },
                {
                    "stageId": "classification",
                    "moduleRefs": ["document_classification@1.0.0"],
                },
            )
        )
        selected.append(f"{CANONICAL_WORKBOOK_MAPPING_MODULE_ID}@1.0.0")
        stages.append(
            {
                "stageId": "canonical_workbook_mapping",
                "moduleRefs": [
                    f"{CANONICAL_WORKBOOK_MAPPING_MODULE_ID}@1.0.0"
                ],
            }
        )
    domain_refs = [f"{item}@1.0.0" for item in selected_domain_ids]
    selected.extend(domain_refs)
    stages.append({"stageId": "feasibility_domains", "moduleRefs": domain_refs})
    if include_reconciliation:
        selected.append(f"{RECONCILIATION_MODULE_ID}@1.0.0")
        stages.append(
            {
                "stageId": "reconciliation",
                "moduleRefs": [f"{RECONCILIATION_MODULE_ID}@1.0.0"],
            }
        )
    return ProjectParsingProfile.from_value(
        {
            "profileId": "realytics.feasibility_domain_pack",
            "version": "1.0.0",
            "documentTaxonomyVersion": DOCUMENT_TAXONOMY_VERSION,
            "documentTypes": list(document_types),
            "projectId": project_id,
            "selectedModules": selected,
            "failurePolicy": "fail_closed",
            "orderedStages": stages,
            "metadata": {
                "domainPackVersion": FEASIBILITY_DOMAIN_PACK_VERSION,
                "candidateOnly": True,
                "authoritativeProjectionAllowed": False,
                **(
                    {
                        "canonicalWorkbookMapping": pinned_mapping,
                        "canonicalWorkbookMappingHash": (
                            canonical_workbook_mapping_hash(pinned_mapping)
                        ),
                    }
                    if pinned_mapping is not None
                    else {}
                ),
            },
        }
    )


def _structured_fact_rows(
    payload: Mapping[str, Any],
) -> list[Mapping[str, Any] | Any] | None:
    root = payload.get("input")
    if not isinstance(root, Mapping):
        return None
    raw = root.get("structuredFactRows")
    if raw is None:
        results = payload.get("module_results")
        if isinstance(results, Mapping):
            for result in results.values():
                if not isinstance(result, Mapping):
                    continue
                output = result.get("output")
                if (
                    isinstance(output, Mapping)
                    and output.get("structuredFactBatchVersion")
                    == STRUCTURED_FACT_BATCH_VERSION
                    and "structuredFactRows" in output
                ):
                    raw = output.get("structuredFactRows")
                    break
    if raw is None:
        return None
    if not isinstance(raw, Sequence) or isinstance(
        raw, (str, bytes, bytearray)
    ):
        return None
    return list(raw)


def _structural_chunks(payload: Mapping[str, Any]) -> list[Any]:
    results = payload.get("module_results")
    if not isinstance(results, Mapping):
        return []
    result = results.get("structural_extraction")
    if not isinstance(result, Mapping):
        return []
    output = result.get("output")
    if not isinstance(output, Mapping):
        return []
    artifact = output.get("artifact")
    if not isinstance(artifact, Mapping):
        return []
    chunks = artifact.get("chunks")
    if not isinstance(chunks, Sequence) or isinstance(
        chunks, (str, bytes, bytearray)
    ):
        return []
    return list(chunks)


def _document_type(
    payload: Mapping[str, Any],
    manifest: ParserModuleManifest,
) -> tuple[str | None, list[dict[str, Any]]]:
    root = payload.get("input")
    explicit = root.get("documentType") if isinstance(root, Mapping) else None
    classified: Any = None
    results = payload.get("module_results")
    if isinstance(results, Mapping):
        classification_result = results.get("document_classification")
        if isinstance(classification_result, Mapping):
            output = classification_result.get("output")
            if isinstance(output, Mapping):
                classification = output.get("classification")
                if isinstance(classification, Mapping):
                    classified = classification.get("documentType")
    if explicit is not None and classified is not None:
        try:
            explicit_type = canonical_document_type(str(explicit))
            classified_type = canonical_document_type(str(classified))
        except ValueError:
            return None, [
                _diagnostic(
                    "DOCUMENT_TYPE_INVALID",
                    "The document type is not part of the active taxonomy.",
                )
            ]
        if explicit_type != classified_type:
            return None, [
                _diagnostic(
                    "DOCUMENT_CLASSIFICATION_MISMATCH",
                    "Explicit and classified document types do not match.",
                )
            ]
        actual = explicit_type
    else:
        raw = explicit if explicit is not None else classified
        if raw is None:
            return None, [
                _diagnostic(
                    "DOCUMENT_TYPE_REQUIRED",
                    "A canonical document type is required before domain parsing.",
                )
            ]
        try:
            actual = canonical_document_type(str(raw))
        except ValueError:
            return None, [
                _diagnostic(
                    "DOCUMENT_TYPE_INVALID",
                    "The document type is not part of the active taxonomy.",
                )
            ]
    if actual not in manifest.supported_document_types:
        return None, [
            _diagnostic(
                "DOCUMENT_TYPE_UNSUPPORTED_BY_MODULE",
                "This module does not support the classified document type.",
            )
        ]
    return actual, []


def _candidate_identity(
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    root = payload.get("input")
    root = root if isinstance(root, Mapping) else {}
    document = root.get("document")
    tenant_id = context.get("tenant_id") or getattr(document, "tenant_id", None)
    project_id = context.get("project_id") or getattr(document, "project_id", None)
    parsing_run_id = context.get("parsing_run_id")
    document_version_id = context.get("document_version_id")
    source_hash = context.get("source_hash") or getattr(document, "sha256", None)
    ontology_version = context.get("ontology_version")
    mapping_attestation_hash = context.get("mapping_attestation_hash")
    created_at = context.get("candidate_created_at") or root.get(
        "candidateCreatedAt"
    )
    missing = [
        name
        for name, value in (
            ("tenantId", tenant_id),
            ("projectId", project_id),
            ("parsingRunId", parsing_run_id),
            ("documentVersionId", document_version_id),
            ("sourceHash", source_hash),
            ("ontologyVersion", ontology_version),
            ("mappingAttestationHash", mapping_attestation_hash),
            ("candidateCreatedAt", created_at),
        )
        if not str(value or "").strip()
    ]
    if missing:
        return None, [
            _diagnostic(
                "CANDIDATE_CONTEXT_INCOMPLETE",
                "Candidate context is incomplete: " + ", ".join(missing) + ".",
            )
        ]
    if str(ontology_version) not in SUPPORTED_ONTOLOGY_VERSIONS:
        return None, [
            _diagnostic(
                "ONTOLOGY_VERSION_UNREGISTERED",
                "The candidate ontology version is not registered.",
            )
        ]
    try:
        datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return None, [
            _diagnostic(
                "CANDIDATE_TIMESTAMP_INVALID",
                "candidateCreatedAt must be an ISO-8601 timestamp.",
            )
        ]
    if not _is_sha256(source_hash):
        return None, [
            _diagnostic(
                "SOURCE_HASH_INVALID",
                "The immutable source hash must be a SHA-256 digest.",
            )
        ]
    if not _is_sha256(mapping_attestation_hash):
        return None, [
            _diagnostic(
                "MAPPING_ATTESTATION_HASH_INVALID",
                "The mapping attestation hash must be a SHA-256 digest.",
            )
        ]
    return (
        {
            "tenantId": str(tenant_id),
            "projectId": str(project_id),
            "parsingRunId": str(parsing_run_id),
            "documentVersionId": str(document_version_id),
            "sourceHash": str(source_hash).lower(),
            "ontologyVersion": str(ontology_version),
            "mappingAttestationHash": str(mapping_attestation_hash),
            "candidateCreatedAt": str(created_at),
            "sourceAuthority": str(
                context.get("source_authority") or "unknown"
            ),
        },
        [],
    )


def _evidence(
    *,
    row: Mapping[str, Any],
    document_version_id: str,
    source_hash: str,
    row_id: str,
    field_key: str,
    entity_id: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_items: Any
    if "sourceLocators" in row:
        raw_items = row.get("sourceLocators")
    elif "sourceLocator" in row:
        raw_items = [row.get("sourceLocator")]
    else:
        raw_items = []
    if not isinstance(raw_items, Sequence) or isinstance(
        raw_items, (str, bytes, bytearray)
    ):
        raw_items = []
    if not raw_items:
        return [], [
            _diagnostic(
                "EXACT_SOURCE_LOCATOR_REQUIRED",
                "A present fact requires at least one exact source locator.",
                row_id=row_id,
                field_key=field_key,
                entity_id=entity_id,
            )
        ]
    parsed: list[dict[str, Any]] = []
    for raw_locator in raw_items:
        if not isinstance(raw_locator, Mapping):
            return [], [
                _diagnostic(
                    "SOURCE_LOCATOR_INVALID",
                    "Every source locator must be an object.",
                    row_id=row_id,
                    field_key=field_key,
                    entity_id=entity_id,
                )
            ]
        try:
            locator = SourceLocator.model_validate(dict(raw_locator))
        except ValidationError:
            return [], [
                _diagnostic(
                    "SOURCE_LOCATOR_INVALID",
                    "A source locator failed strict contract validation.",
                    row_id=row_id,
                    field_key=field_key,
                    entity_id=entity_id,
                )
            ]
        if locator.document_version_id != document_version_id:
            return [], [
                _diagnostic(
                    "SOURCE_DOCUMENT_VERSION_MISMATCH",
                    "Source evidence is not bound to the current document version.",
                    row_id=row_id,
                    field_key=field_key,
                    entity_id=entity_id,
                )
            ]
        if locator.file_sha256 != source_hash:
            return [], [
                _diagnostic(
                    "SOURCE_HASH_MISMATCH",
                    "Source evidence is not bound to the current immutable hash.",
                    row_id=row_id,
                    field_key=field_key,
                    entity_id=entity_id,
                )
            ]
        parsed.append(
            locator.model_dump(by_alias=True, mode="json", exclude_none=True)
        )
    return parsed, []


def _duplicate_value_diagnostics(
    candidates: Sequence[ParsedFactCandidate],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str | None], list[ParsedFactCandidate]] = defaultdict(
        list
    )
    for candidate in candidates:
        groups[(candidate.field_key, candidate.entity_id)].append(candidate)
    diagnostics: list[dict[str, Any]] = []
    for (field_key, entity_id), items in sorted(
        groups.items(), key=lambda item: (item[0][0], item[0][1] or "")
    ):
        units = {
            str(item.unit) if item.unit is not None else None
            for item in items
        }
        values = {
            json.dumps(
                _json_safe(item.normalized_value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for item in items
        }
        if len(units) > 1:
            diagnostics.append(
                _diagnostic(
                    "CROSS_SOURCE_UNIT_CONFLICT",
                    "The same canonical field and entity has conflicting units; values were not compared before conversion.",
                    field_key=field_key,
                    entity_id=entity_id,
                )
            )
        elif len(values) > 1:
            diagnostics.append(
                _diagnostic(
                    "CROSS_SOURCE_VALUE_CONFLICT",
                    "The same canonical field and entity has conflicting values.",
                    field_key=field_key,
                    entity_id=entity_id,
                )
            )
        bases = {
            json.dumps(
                _json_safe(
                    item.metadata.get("basis")
                    if isinstance(item.metadata, Mapping)
                    else None
                ),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for item in items
        }
        if len(bases) > 1:
            diagnostics.append(
                _diagnostic(
                    "CROSS_SOURCE_BASIS_CONFLICT",
                    "The same canonical field and entity has conflicting monetary basis.",
                    field_key=field_key,
                    entity_id=entity_id,
                )
            )
    return diagnostics


_ENTITY_GROUPS: tuple[
    tuple[str, frozenset[str], tuple[frozenset[str], ...]], ...
] = (
    (
        "program.product.",
        frozenset(
            {
                "program.product.asset_type",
                "program.product.name",
                "program.product.unit_count",
            }
        ),
        (),
    ),
    (
        "cost.line_item.",
        frozenset(
            {
                "cost.line_item.name",
                "cost.line_item.category",
                "cost.line_item.amount",
            }
        ),
        (),
    ),
    (
        "schedule.milestone.",
        frozenset(
            {
                "schedule.milestone.name",
                "schedule.milestone.type",
            }
        ),
        (
            frozenset(
                {
                    "schedule.milestone.planned_date",
                    "schedule.milestone.month_index",
                }
            ),
        ),
    ),
    (
        "sales_collection.step.",
        frozenset(
            {
                "sales_collection.step.stage",
                "sales_collection.step.sequence",
                "sales_collection.step.collection_ratio",
                "sales_collection.step.product_entity_id",
            }
        ),
        (
            frozenset(
                {
                    "sales_collection.step.due_date",
                    "sales_collection.step.due_month_index",
                }
            ),
        ),
    ),
    (
        "financing.tranche.",
        frozenset(
            {
                "financing.tranche.type",
                "financing.tranche.loan_amount",
                "financing.tranche.priority",
            }
        ),
        (),
    ),
)


def _entity_completeness_diagnostics(
    candidates: Sequence[ParsedFactCandidate],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for prefix, required_all, required_one_of in _ENTITY_GROUPS:
        by_entity: dict[str, set[str]] = defaultdict(set)
        for candidate in candidates:
            if candidate.field_key.startswith(prefix) and candidate.entity_id:
                by_entity[candidate.entity_id].add(candidate.field_key)
        for entity_id, present in sorted(by_entity.items()):
            missing_all = sorted(required_all.difference(present))
            missing_one_of = [
                sorted(options)
                for options in required_one_of
                if not present.intersection(options)
            ]
            if missing_all or missing_one_of:
                fragments: list[str] = []
                if missing_all:
                    fragments.append("requiredAll=" + ", ".join(missing_all))
                fragments.extend(
                    "requiredOneOf=(" + " | ".join(options) + ")"
                    for options in missing_one_of
                )
                diagnostics.append(
                    _diagnostic(
                        "ENTITY_FIELD_GROUP_INCOMPLETE",
                        "A repeatable entity is missing governed fields under "
                        + ENTITY_COMPLETENESS_POLICY_VERSION
                        + ": "
                        + "; ".join(fragments)
                        + ".",
                        field_key=prefix.rstrip("."),
                        entity_id=entity_id,
                    )
                )
    return diagnostics


def _aggregate_reconciliation_diagnostics(
    candidates: Sequence[ParsedFactCandidate],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    diagnostics.extend(
        _total_vs_lines(
            candidates,
            total_key="cost.total_project_cost",
            line_key="cost.line_item.amount",
            code="COST_TOTAL_RECONCILIATION_FAILED",
            tolerance=1,
        )
    )
    diagnostics.extend(
        _total_vs_lines(
            candidates,
            total_key="revenue.total_sales_revenue",
            line_key="revenue.product.total_sales_revenue",
            code="REVENUE_TOTAL_RECONCILIATION_FAILED",
            tolerance=1,
        )
    )
    diagnostics.extend(
        _total_vs_lines(
            candidates,
            total_key="financing.pf_amount",
            line_key="financing.tranche.loan_amount",
            code="PF_TOTAL_RECONCILIATION_FAILED",
            tolerance=1,
        )
    )
    product_by_step = {
        item.entity_id: str(item.normalized_value)
        for item in candidates
        if item.field_key == "sales_collection.step.product_entity_id"
        and item.entity_id
        and item.normalized_value is not None
    }
    known_product_ids = {
        str(item.entity_id)
        for item in candidates
        if item.entity_id
        and item.field_key.startswith(
            ("program.product.", "revenue.product.")
        )
    }
    ratios_by_product: dict[str, list[Decimal]] = defaultdict(list)
    for item in candidates:
        if (
            item.field_key != "sales_collection.step.collection_ratio"
            or not item.entity_id
        ):
            continue
        product_entity_id = product_by_step.get(item.entity_id)
        if not product_entity_id:
            diagnostics.append(
                _diagnostic(
                    "SALES_COLLECTION_PRODUCT_FK_REQUIRED",
                    "A sales collection step requires a productEntityId fact.",
                    field_key=item.field_key,
                    entity_id=item.entity_id,
                )
            )
            continue
        if (
            not known_product_ids
            or product_entity_id not in known_product_ids
        ):
            diagnostics.append(
                _diagnostic(
                    "SALES_COLLECTION_PRODUCT_FK_UNRESOLVED",
                    "A sales collection step references an unknown product entity.",
                    field_key=(
                        "sales_collection.step.product_entity_id"
                    ),
                    entity_id=item.entity_id,
                )
            )
            continue
        decimal_value = _decimal_value(item.normalized_value)
        if decimal_value is not None:
            ratios_by_product[product_entity_id].append(decimal_value)
    for product_entity_id, ratios in sorted(ratios_by_product.items()):
        if ratios and abs(sum(ratios) - Decimal("1")) > Decimal("0.000001"):
            diagnostics.append(
                _diagnostic(
                    "SALES_COLLECTION_RATIO_RECONCILIATION_FAILED",
                    "Sales collection ratios do not reconcile to 1.0 for the product entity.",
                    field_key="sales_collection.step.collection_ratio",
                    entity_id=product_entity_id,
                )
            )
    return diagnostics


def _total_vs_lines(
    candidates: Sequence[ParsedFactCandidate],
    *,
    total_key: str,
    line_key: str,
    code: str,
    tolerance: float,
) -> list[dict[str, Any]]:
    totals = [
        value
        for item in candidates
        if item.field_key == total_key
        and (value := _decimal_value(item.normalized_value)) is not None
    ]
    lines = [
        value
        for item in candidates
        if item.field_key == line_key
        and (value := _decimal_value(item.normalized_value)) is not None
    ]
    if len(totals) != 1 or not lines:
        return []
    if abs(totals[0] - sum(lines)) <= Decimal(str(tolerance)):
        return []
    return [
        _diagnostic(
            code,
            "The governed total and repeatable line items do not reconcile under the active policy.",
            field_key=total_key,
        )
    ]


_EXPECTED_ENTITY_PREFIXES = {
    "product": ("program.product.", "revenue.product."),
    "costLine": ("cost.line_item.",),
    "milestone": ("schedule.milestone.",),
    "salesCollectionStep": ("sales_collection.step.",),
    "tranche": ("financing.tranche.",),
}


def _expected_entity_coverage_diagnostics(
    candidates: Sequence[ParsedFactCandidate],
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    root = payload.get("input")
    mapping = (
        root.get("canonicalWorkbookMapping")
        if isinstance(root, Mapping)
        else None
    )
    mapping_metadata = (
        mapping.get("metadata") if isinstance(mapping, Mapping) else None
    )
    expected = (
        mapping_metadata.get("expectedEntities")
        if isinstance(mapping_metadata, Mapping)
        else None
    )
    if expected is None:
        mapping_entries = (
            mapping.get("entries") if isinstance(mapping, Mapping) else None
        )
        repeatable_in_mapping = (
            isinstance(mapping_entries, Sequence)
            and not isinstance(mapping_entries, (str, bytes, bytearray))
            and any(
                isinstance(item, Mapping)
                and _is_repeatable_entity_field(item.get("fieldKey"))
                for item in mapping_entries
            )
        )
        repeatable_in_candidates = any(
            _is_repeatable_entity_field(item.field_key)
            for item in candidates
        )
        policy_required = bool(
            repeatable_in_mapping or repeatable_in_candidates
        )
        return [
            _diagnostic(
                "EXPECTED_ENTITY_POLICY_NOT_DECLARED",
                (
                    "Repeatable facts require explicit expected entity IDs/count."
                    if policy_required
                    else "No expected entity IDs/count policy was declared for this scalar-only mapping."
                ),
                blocking=policy_required,
                severity="error" if policy_required else "info",
            )
        ]
    if not isinstance(expected, Mapping):
        return [
            _diagnostic(
                "EXPECTED_ENTITY_POLICY_INVALID",
                "expectedEntities must be a versioned entity coverage object.",
            )
        ]
    if expected.get("schemaVersion") != EXPECTED_ENTITY_POLICY_VERSION:
        return [
            _diagnostic(
                "EXPECTED_ENTITY_POLICY_VERSION_UNSUPPORTED",
                "expectedEntities must pin "
                + EXPECTED_ENTITY_POLICY_VERSION
                + ".",
            )
        ]
    expected_groups = expected.get("groups")
    if not isinstance(expected_groups, Mapping):
        return [
            _diagnostic(
                "EXPECTED_ENTITY_POLICY_INVALID",
                "expectedEntities.groups must be an object.",
            )
        ]
    diagnostics: list[dict[str, Any]] = []
    present_groups = {
        group_name
        for item in candidates
        if (
            group_name := _expected_entity_group_for_field(item.field_key)
        )
    }
    mapping_entries = (
        mapping.get("entries") if isinstance(mapping, Mapping) else None
    )
    if isinstance(mapping_entries, Sequence) and not isinstance(
        mapping_entries, (str, bytes, bytearray)
    ):
        present_groups.update(
            group_name
            for item in mapping_entries
            if isinstance(item, Mapping)
            and (
                group_name := _expected_entity_group_for_field(
                    item.get("fieldKey")
                )
            )
        )
    for group_name in sorted(present_groups.difference(expected_groups)):
        diagnostics.append(
            _diagnostic(
                "EXPECTED_ENTITY_GROUP_POLICY_MISSING",
                "A repeatable entity namespace requires an explicit IDs/count contract.",
                field_key=group_name,
            )
        )
    for group_name, raw_contract in sorted(
        expected_groups.items(), key=lambda item: str(item[0])
    ):
        prefixes = _EXPECTED_ENTITY_PREFIXES.get(str(group_name))
        if prefixes is None or not isinstance(raw_contract, Mapping):
            diagnostics.append(
                _diagnostic(
                    "EXPECTED_ENTITY_POLICY_INVALID",
                    "An expected entity group is unknown or malformed.",
                    field_key=str(group_name),
                )
            )
            continue
        actual_ids = {
            str(item.entity_id)
            for item in candidates
            if item.entity_id
            and item.field_key.startswith(prefixes)
        }
        raw_ids = raw_contract.get("entityIds")
        expected_ids: set[str] | None = None
        if raw_ids is not None:
            if not isinstance(raw_ids, Sequence) or isinstance(
                raw_ids, (str, bytes, bytearray)
            ):
                diagnostics.append(
                    _diagnostic(
                        "EXPECTED_ENTITY_POLICY_INVALID",
                        "expected entityIds must be a list.",
                        field_key=str(group_name),
                    )
                )
                continue
            expected_ids = {str(item) for item in raw_ids}
            if len(expected_ids) != len(raw_ids) or "" in expected_ids:
                diagnostics.append(
                    _diagnostic(
                        "EXPECTED_ENTITY_POLICY_INVALID",
                        "expected entityIds must be unique and non-empty.",
                        field_key=str(group_name),
                    )
                )
                continue
        raw_count = raw_contract.get("count")
        expected_count: int | None = None
        if raw_count is not None:
            if (
                not isinstance(raw_count, int)
                or isinstance(raw_count, bool)
                or raw_count < 0
            ):
                diagnostics.append(
                    _diagnostic(
                        "EXPECTED_ENTITY_POLICY_INVALID",
                        "expected entity count must be a non-negative integer.",
                        field_key=str(group_name),
                    )
                )
                continue
            expected_count = raw_count
        if expected_ids is None and expected_count is None:
            diagnostics.append(
                _diagnostic(
                    "EXPECTED_ENTITY_POLICY_INVALID",
                    "An expected entity group requires entityIds or count.",
                    field_key=str(group_name),
                )
            )
            continue
        if expected_ids is not None and expected_ids != actual_ids:
            diagnostics.append(
                _diagnostic(
                    "EXPECTED_ENTITY_ID_COVERAGE_MISMATCH",
                    "Actual entity IDs do not exactly match the mapping contract.",
                    field_key=str(group_name),
                )
            )
        if expected_count is not None and expected_count != len(actual_ids):
            diagnostics.append(
                _diagnostic(
                    "EXPECTED_ENTITY_COUNT_MISMATCH",
                    "Actual entity count does not match the mapping contract.",
                    field_key=str(group_name),
                )
            )
    return diagnostics


def _is_repeatable_entity_field(value: Any) -> bool:
    return _expected_entity_group_for_field(value) is not None


def _expected_entity_group_for_field(value: Any) -> str | None:
    field_key = str(value or "")
    return next(
        (
            group_name
            for group_name, prefixes in _EXPECTED_ENTITY_PREFIXES.items()
            if field_key.startswith(prefixes)
        ),
        None,
    )


def _decimal_value(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _validate_domain_input_contract(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("input envelope required")
    if set(value) != {"input", "module_results", "available_capabilities"}:
        raise ValueError("input envelope fields invalid")
    root = value.get("input")
    if not isinstance(root, Mapping):
        raise ValueError("input object required")
    rows = root.get("structuredFactRows")
    if rows is not None:
        if not isinstance(rows, Sequence) or isinstance(
            rows, (str, bytes, bytearray)
        ):
            raise ValueError("structured rows must be a list")
        if len(rows) > MAX_STRUCTURED_FACT_ROWS:
            raise ValueError("structured row count exceeds controlled limit")
    if not isinstance(value.get("module_results"), Mapping):
        raise ValueError("module results object required")
    capabilities = value.get("available_capabilities")
    if not isinstance(capabilities, Sequence) or isinstance(
        capabilities, (str, bytes, bytearray)
    ):
        raise ValueError("available capabilities list required")


def _validate_workbook_mapping_input_contract(value: Any) -> None:
    _validate_domain_input_contract(value)
    root = value.get("input")
    mapping = root.get("canonicalWorkbookMapping")
    if mapping is None:
        return
    if not isinstance(mapping, Mapping):
        raise ValueError("canonical workbook mapping must be an object")
    if set(mapping).difference(
        {"schemaVersion", "documentType", "entries", "metadata"}
    ):
        raise ValueError("canonical workbook mapping contains unknown fields")
    entries = mapping.get("entries")
    if not isinstance(entries, Sequence) or isinstance(
        entries, (str, bytes, bytearray)
    ):
        raise ValueError("canonical workbook mapping entries must be a list")
    if len(entries) > MAX_STRUCTURED_FACT_ROWS:
        raise ValueError("canonical workbook mapping exceeds controlled limit")
    if not all(isinstance(item, Mapping) for item in entries):
        raise ValueError("canonical workbook mapping entries must be objects")


def _validate_workbook_mapping_output_contract(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("workbook mapping output object required")
    required = {
        "schemaVersion",
        "structuredFactBatchVersion",
        "moduleId",
        "moduleVersion",
        "candidateOnly",
        "authoritative",
        "structuredFactRows",
        "diagnostics",
        "summary",
        "providedCapabilities",
    }
    if not required.issubset(value):
        raise ValueError("workbook mapping output fields incomplete")
    if value.get("schemaVersion") != CANONICAL_WORKBOOK_MAPPING_OUTPUT_SCHEMA:
        raise ValueError("workbook mapping output schema mismatch")
    if value.get("structuredFactBatchVersion") != STRUCTURED_FACT_BATCH_VERSION:
        raise ValueError("structured fact batch version mismatch")
    if value.get("candidateOnly") is not True or value.get("authoritative") is not False:
        raise ValueError("workbook mapping authority flags invalid")
    for key in ("structuredFactRows", "diagnostics", "providedCapabilities"):
        item = value.get(key)
        if not isinstance(item, Sequence) or isinstance(
            item, (str, bytes, bytearray)
        ):
            raise ValueError("workbook mapping output collection invalid")


def _validate_domain_output_contract(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("domain output object required")
    required = {
        "schemaVersion",
        "outputKind",
        "moduleId",
        "moduleVersion",
        "domainPackVersion",
        "domain",
        "candidateOnly",
        "authoritative",
        "candidates",
        "diagnostics",
        "summary",
        "pendingCapabilities",
        "providedCapabilities",
    }
    if not required.issubset(value):
        raise ValueError("domain output fields incomplete")
    if value.get("schemaVersion") != FEASIBILITY_DOMAIN_OUTPUT_SCHEMA:
        raise ValueError("domain output schema version mismatch")
    if value.get("candidateOnly") is not True or value.get("authoritative") is not False:
        raise ValueError("domain output authority flags invalid")
    for key in ("candidates", "diagnostics", "pendingCapabilities", "providedCapabilities"):
        item = value.get(key)
        if not isinstance(item, Sequence) or isinstance(
            item, (str, bytes, bytearray)
        ):
            raise ValueError("domain output collection invalid")
    for candidate in value.get("candidates") or []:
        ParsedFactCandidate.model_validate(candidate)
    if not all(
        isinstance(item, Mapping) for item in value.get("diagnostics") or []
    ):
        raise ValueError("domain diagnostics must be objects")


def _diagnostic(
    code: str,
    message: str,
    *,
    row_id: str | None = None,
    field_key: str | None = None,
    entity_id: str | None = None,
    blocking: bool = True,
    severity: str = "error",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "blocking": blocking,
        "message": message,
    }
    if row_id is not None:
        result["rowId"] = row_id
    if field_key is not None:
        result["fieldKey"] = field_key
    if entity_id is not None:
        result["entityId"] = entity_id
    return result


def _safe_row_id(value: Any, index: int) -> str:
    raw = str(value or "").strip()
    if raw and len(raw) <= 200:
        return raw
    return f"row:{index}"


def _opaque_structured_row_id(row: Mapping[str, Any], index: int) -> str:
    metadata = row.get("metadata")
    source_row_id = (
        str(metadata.get("sourceRowId") or "")
        if isinstance(metadata, Mapping)
        else ""
    )
    if re.fullmatch(r"mapping-entry:[0-9a-f]{64}", source_row_id):
        return source_row_id
    identity = {
        "index": index,
        "domain": row.get("domain"),
        "fieldKey": row.get("fieldKey"),
        "entityId": row.get("entityId"),
        "sourceLocator": row.get("sourceLocator"),
    }
    return "structured-row:" + _sha256_json(identity)


def _validate_structured_fact_lineage(
    *,
    raw_metadata: Mapping[str, Any] | Any,
    identity: Mapping[str, Any],
    raw_value: Any,
    normalized_value: Any,
    field_key: str,
    entity_id: str | None,
    unit: str | None,
    basis: Any,
    evidence: Sequence[Mapping[str, Any]],
    row_id: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw_metadata, Mapping):
        return [
            _diagnostic(
                "MAPPING_ATTESTATION_REQUIRED",
                "Unit, basis, and normalization require immutable mapping attestation.",
                row_id=row_id,
                field_key=field_key,
                entity_id=entity_id,
            )
        ]
    required = (
        "sourceRowId",
        "mappingAttestationHash",
        "mappingEntryHash",
        "normalizationRuleId",
        "normalizationRuleVersion",
        "normalizationInputHash",
        "normalizationOutputHash",
        "unitEvidenceMode",
        "basisEvidenceMode",
    )
    if any(not str(raw_metadata.get(key) or "").strip() for key in required):
        return [
            _diagnostic(
                "MAPPING_ATTESTATION_INCOMPLETE",
                "Structured fact mapping lineage is incomplete.",
                row_id=row_id,
                field_key=field_key,
                entity_id=entity_id,
            )
        ]
    if (
        raw_metadata.get("sourceRowId") != row_id
        or raw_metadata.get("mappingAttestationHash")
        != identity.get("mappingAttestationHash")
    ):
        return [
            _diagnostic(
                "MAPPING_ATTESTATION_MISMATCH",
                "Structured fact mapping lineage does not match the pinned profile.",
                row_id=row_id,
                field_key=field_key,
                entity_id=entity_id,
            )
        ]
    hash_fields = (
        "mappingAttestationHash",
        "mappingEntryHash",
        "normalizationInputHash",
        "normalizationOutputHash",
    )
    if any(not _is_sha256(raw_metadata.get(key)) for key in hash_fields):
        return [
            _diagnostic(
                "MAPPING_LINEAGE_HASH_INVALID",
                "Structured fact mapping lineage contains an invalid hash.",
                row_id=row_id,
                field_key=field_key,
                entity_id=entity_id,
            )
        ]
    if (
        raw_metadata.get("normalizationRuleId") != "literal_identity"
        or raw_metadata.get("normalizationRuleVersion") != "1.0.0"
        or raw_value != normalized_value
    ):
        return [
            _diagnostic(
                "NORMALIZATION_RULE_UNSUPPORTED",
                "Only the versioned literal identity normalization rule is supported.",
                row_id=row_id,
                field_key=field_key,
                entity_id=entity_id,
            )
        ]
    if len(evidence) != 1:
        return [
            _diagnostic(
                "NORMALIZATION_SOURCE_LOCATOR_AMBIGUOUS",
                "Literal identity normalization requires one exact source locator.",
                row_id=row_id,
                field_key=field_key,
                entity_id=entity_id,
            )
        ]
    expected_input_hash = _sha256_json(
        {
            "mappingEntryHash": raw_metadata.get("mappingEntryHash"),
            "rawValue": raw_value,
            "sourceLocator": dict(evidence[0]),
        }
    )
    expected_output_hash = _sha256_json(
        {
            "fieldKey": field_key,
            "entityId": entity_id,
            "normalizedValue": normalized_value,
            "unit": unit,
            "basis": basis,
        }
    )
    if (
        raw_metadata.get("normalizationInputHash") != expected_input_hash
        or raw_metadata.get("normalizationOutputHash") != expected_output_hash
    ):
        return [
            _diagnostic(
                "NORMALIZATION_LINEAGE_HASH_MISMATCH",
                "Normalization input/output hashes do not match the structured fact.",
                row_id=row_id,
                field_key=field_key,
                entity_id=entity_id,
            )
        ]
    expected_unit_mode = (
        "immutable_profile_mapping_attestation"
        if unit is not None
        else "not_applicable"
    )
    expected_basis_mode = (
        "immutable_profile_mapping_attestation"
        if basis is not None
        else "not_applicable"
    )
    if (
        raw_metadata.get("unitEvidenceMode") != expected_unit_mode
        or raw_metadata.get("basisEvidenceMode") != expected_basis_mode
    ):
        return [
            _diagnostic(
                "UNIT_BASIS_EVIDENCE_MISMATCH",
                "Unit or basis evidence mode does not match the pinned mapping.",
                row_id=row_id,
                field_key=field_key,
                entity_id=entity_id,
            )
        ]
    return []


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(item in "0123456789abcdefABCDEF" for item in text)


def _json_safe(value: Any) -> Any:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return json.loads(encoded)


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _semantic_locator(locator: Mapping[str, Any]) -> dict[str, Any]:
    payload = _json_safe(dict(locator))
    for key in (
        "documentVersionId",
        "documentId",
        "documentName",
        "evidenceId",
    ):
        payload.pop(key, None)
    return payload


__all__ = [
    "ALL_FEASIBILITY_MODULE_IDS",
    "CANONICAL_WORKBOOK_MAPPING_MODULE_ID",
    "CANONICAL_WORKBOOK_MAPPING_OUTPUT_SCHEMA",
    "CANONICAL_WORKBOOK_MAPPING_VERSION",
    "COST_MODULE_ID",
    "CanonicalWorkbookFactMappingModule",
    "CrossDocumentReconciliationModule",
    "CostParserModule",
    "DOMAIN_CANDIDATE_CONTENT_HASH_VERSION",
    "DOMAIN_MODULE_IDS",
    "ENTITY_COMPLETENESS_POLICY_VERSION",
    "EXPECTED_ENTITY_POLICY_VERSION",
    "FEASIBILITY_DOMAIN_INPUT_SCHEMA",
    "FEASIBILITY_DOMAIN_OUTPUT_SCHEMA",
    "FEASIBILITY_DOMAIN_PACK_VERSION",
    "PF_TERMS_MODULE_ID",
    "PRODUCT_MIX_MODULE_ID",
    "PfTermsParserModule",
    "ProductMixParserModule",
    "RECONCILIATION_MODULE_ID",
    "SALES_COLLECTION_MODULE_ID",
    "SCHEDULE_MODULE_ID",
    "STRUCTURED_FACT_BATCH_VERSION",
    "SalesCollectionParserModule",
    "SameRunCrossFieldReconciliationModule",
    "ScheduleParserModule",
    "build_domain_candidate_id",
    "build_domain_candidate_content_hash",
    "build_structured_fact_lineage",
    "canonical_workbook_mapping_hash",
    "build_feasibility_domain_module_registry",
    "build_feasibility_domain_orchestrator",
    "build_feasibility_domain_schema_registry",
    "feasibility_domain_profile",
]
