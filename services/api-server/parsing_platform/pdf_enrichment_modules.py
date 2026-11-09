from __future__ import annotations

"""Composable Parser V2 wrappers for PDF structure enrichment utilities."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from parsing_platform.adapters import (
    ArchivedDocument,
    DocumentAdapterRegistry,
    ExtractionArtifact,
    ExtractionChunk,
    bind_artifact_to_document_version,
)
from parsing_platform.contracts import SourceLocator
from parsing_platform.default_modules import (
    build_default_parser_module_registry,
)
from parsing_platform.document_taxonomy import (
    CLASSIFIER_DOCUMENT_TYPES,
    DOCUMENT_TAXONOMY_VERSION,
)
from parsing_platform.orchestrator import (
    ParsingOrchestrator,
    ProjectParsingProfile,
)
from parsing_platform.pdf_ocr import (
    EXTERNAL_PDF_OCR_MANIFEST,
    OCR_EXECUTOR_ENV_VAR,
    OcrRequiredDispatchBlocked,
    OcrRequiredExecutionPending,
    PDF_OCR_ASSESSMENT_SCHEMA_VERSION,
    assess_pdf_ocr_requirement,
    external_ocr_executor_contract,
    plan_external_pdf_ocr,
)
from parsing_platform.pdf_structure_enrichment import (
    PDF_NATIVE_TABLE_MANIFEST,
    SEMANTIC_SECTION_MANIFEST,
    SEMANTIC_SECTION_SCHEMA_VERSION,
    DeterministicSemanticSectionIndexer,
    NativePdfTableExtractor,
)
from parsing_platform.registry import ParserModuleManifest, ParserModuleRegistry
from parsing_platform.schema_contracts import (
    SchemaContractRegistry,
    build_default_schema_contract_registry,
)


PDF_OCR_PLANNING_INPUT_SCHEMA = "realytics-pdf-ocr-planning-input-v2"
PDF_OCR_PLANNING_OUTPUT_SCHEMA = "realytics-pdf-ocr-planning-output-v2"
PDF_OCR_PLANNING_MANIFEST = ParserModuleManifest(
    module_id="pdf_ocr_assessment_dispatch_plan",
    version="2.0.0",
    capabilities=("document.ocr_assessment", "document.ocr_dispatch_planning"),
    document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
    document_type_scope="all",
    requires=("document.safe",),
    conditional_provides=(
        "document.ocr_assessed",
        "document.ocr_dispatch_ready",
    ),
    execution_class="deterministic",
    input_schema=PDF_OCR_PLANNING_INPUT_SCHEMA,
    output_schema=PDF_OCR_PLANNING_OUTPUT_SCHEMA,
    required_locator_precision="document",
    data_classification="restricted",
    max_output_bytes=2 * 1024 * 1024,
    metadata={
        "deterministic": True,
        "authoritative": False,
        "executorInvocationImplemented": False,
        "externalExecutorManifestRef": EXTERNAL_PDF_OCR_MANIFEST.ref,
        "executorEnvironmentVariable": OCR_EXECUTOR_ENV_VAR,
        "failClosed": True,
        "factPromotionAllowed": False,
    },
)


class PdfNativeTableModule:
    manifest = PDF_NATIVE_TABLE_MANIFEST

    def __init__(
        self,
        extractor: NativePdfTableExtractor | None = None,
    ) -> None:
        self.extractor = extractor or NativePdfTableExtractor()

    def execute(
        self,
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        document = _document_input(payload)
        if not self.extractor.supports(document):
            return {
                "status": "not_applicable",
                "artifact": None,
                "diagnostics": [
                    {
                        "code": "PDF_TABLE_NOT_APPLICABLE",
                        "severity": "info",
                        "blocking": False,
                    }
                ],
                "providedCapabilities": [],
                **_authority_boundary(),
            }

        artifact = self.extractor.extract(document)
        artifact = bind_artifact_to_document_version(
            artifact,
            document=document,
            document_version_id=str(
                context.get("document_version_id")
                or f"archive-version:{document.archive_id}"
            ),
            document_id=(
                str(context["document_id"])
                if context.get("document_id") is not None
                else None
            ),
        )
        _validate_strict_chunk_locators(artifact)
        return {
            "status": str(
                artifact.metadata.get("status") or "completed"
            ),
            "artifact": artifact.to_dict(include_text=True),
            "diagnostics": list(
                artifact.metadata.get("diagnostics") or []
            ),
            "providedCapabilities": (
                ["document.pdf_tables"] if artifact.complete else []
            ),
            **_authority_boundary(),
        }


class SemanticSectionModule:
    manifest = SEMANTIC_SECTION_MANIFEST

    def __init__(
        self,
        indexer: DeterministicSemanticSectionIndexer | None = None,
    ) -> None:
        self.indexer = indexer or DeterministicSemanticSectionIndexer()

    def execute(
        self,
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        document = _document_input(payload)
        artifact = _upstream_extraction_artifact(
            payload,
            "structural_extraction",
        )
        attestation_error = _semantic_source_attestation_error(
            artifact,
            document=document,
            context=context,
        )
        if attestation_error is not None:
            code, message = attestation_error
            return _semantic_abstention_output(
                artifact,
                code=code,
                message=message,
            )

        section_index = self.indexer.index(artifact)
        for section in section_index.sections:
            for reference in section.chunk_references:
                SourceLocator.model_validate(reference.source_locator)
        completed = bool(
            section_index.complete and section_index.sections
        )
        return {
            "status": "completed" if completed else "abstained",
            "sectionIndex": section_index.to_dict(),
            "providedCapabilities": (
                ["document.semantic_sections"] if completed else []
            ),
            **_authority_boundary(),
        }


class PdfOcrAssessmentDispatchPlanModule:
    manifest = PDF_OCR_PLANNING_MANIFEST

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.environ = environ

    def execute(
        self,
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        document = _document_input(payload)
        if not _is_pdf(document):
            return {
                "status": "not_applicable",
                "assessment": {
                    "schemaVersion": PDF_OCR_ASSESSMENT_SCHEMA_VERSION,
                    "documentId": document.archive_id,
                    "status": "not_applicable",
                    "ocrRequired": False,
                    **_authority_boundary(),
                },
                "dispatchPlan": {
                    "status": "not_applicable",
                    "blocked": False,
                    "executorManifest": (
                        external_ocr_executor_contract()
                    ),
                    **_authority_boundary(),
                },
                "providedCapabilities": [],
                **_authority_boundary(),
            }
        assessment = assess_pdf_ocr_requirement(document)
        plan = plan_external_pdf_ocr(
            document,
            assessment=assessment,
            document_version_id=(
                str(context["document_version_id"])
                if context.get("document_version_id") is not None
                else None
            ),
            environ=self.environ,
        )
        if assessment.ocr_required:
            if plan.status == "blocked":
                raise OcrRequiredDispatchBlocked(plan)
            # A ready dispatch request is still not verified OCR evidence.
            # This planning-only profile must remain closed until a separate
            # executor module validates and binds the response.
            raise OcrRequiredExecutionPending(plan)
        return {
            "status": plan.status,
            "assessment": assessment.to_dict(),
            "dispatchPlan": plan.to_dict(),
            "providedCapabilities": ["document.ocr_assessed"],
            **_authority_boundary(),
        }


def build_pdf_enrichment_schema_registry() -> SchemaContractRegistry:
    registry = build_default_schema_contract_registry()
    register_pdf_enrichment_schema_contracts(registry)
    return registry


def register_pdf_enrichment_schema_contracts(
    registry: SchemaContractRegistry,
) -> SchemaContractRegistry:
    """Add only the PDF pack contracts to an existing runtime registry.

    The full Parser V2 runtime composes several independently versioned packs.
    Keeping this registration step separate avoids rebuilding (and therefore
    attempting to register) the immutable default schema IDs more than once.
    """

    registry.register(
        PDF_NATIVE_TABLE_MANIFEST.input_schema,
        _validate_document_module_input,
        definition={
            "type": "module-envelope",
            "input": "ArchivedDocument",
            "upstreamCapability": "document.safe",
        },
        description="Safe PDF native table extraction input",
    )
    registry.register(
        PDF_NATIVE_TABLE_MANIFEST.output_schema,
        _validate_pdf_table_output,
        definition={
            "type": "pdf-native-table-output",
            "authoritative": False,
        },
        description="Exact PDF table cell structural evidence",
    )
    registry.register(
        SEMANTIC_SECTION_MANIFEST.input_schema,
        _validate_semantic_section_input,
        definition={
            "type": "module-envelope",
            "upstream": "structural_extraction",
        },
        description="Bound structural chunks for semantic indexing",
    )
    registry.register(
        SEMANTIC_SECTION_MANIFEST.output_schema,
        _validate_semantic_section_output,
        definition={
            "type": "semantic-section-index",
            "storesOriginalText": False,
        },
        description="Text-free deterministic semantic section index",
    )
    registry.register(
        PDF_OCR_PLANNING_INPUT_SCHEMA,
        _validate_document_module_input,
        definition={
            "type": "module-envelope",
            "input": "ArchivedDocument",
            "upstreamCapability": "document.safe",
        },
        description="Safe PDF OCR assessment input",
    )
    registry.register(
        PDF_OCR_PLANNING_OUTPUT_SCHEMA,
        _validate_pdf_ocr_planning_output,
        definition={
            "type": "pdf-ocr-assessment-dispatch-plan",
            "executorInvocationImplemented": False,
            "failClosed": True,
        },
        description="Fail-closed OCR assessment and dispatch plan",
    )
    return registry


def build_pdf_enrichment_module_registry(
    *,
    adapter_registry: DocumentAdapterRegistry | None = None,
    table_extractor: NativePdfTableExtractor | None = None,
    ocr_environ: Mapping[str, str] | None = None,
) -> ParserModuleRegistry:
    registry = build_default_parser_module_registry(
        adapter_registry=adapter_registry
    )
    for module in (
        PdfNativeTableModule(table_extractor),
        SemanticSectionModule(),
        PdfOcrAssessmentDispatchPlanModule(environ=ocr_environ),
    ):
        registry.register(module)
    return registry


def build_pdf_enrichment_orchestrator(
    *,
    adapter_registry: DocumentAdapterRegistry | None = None,
    table_extractor: NativePdfTableExtractor | None = None,
    ocr_environ: Mapping[str, str] | None = None,
) -> ParsingOrchestrator:
    return ParsingOrchestrator(
        build_pdf_enrichment_module_registry(
            adapter_registry=adapter_registry,
            table_extractor=table_extractor,
            ocr_environ=ocr_environ,
        ),
        schema_registry=build_pdf_enrichment_schema_registry(),
    )


def pdf_enrichment_profile(
    *,
    project_id: str | None = None,
    document_types: Sequence[str] = CLASSIFIER_DOCUMENT_TYPES,
) -> ProjectParsingProfile:
    return ProjectParsingProfile.from_value(
        {
            "profileId": "realytics.pdf_enrichment",
            "version": "2.0.0",
            "documentTaxonomyVersion": DOCUMENT_TAXONOMY_VERSION,
            "documentTypes": list(document_types),
            "projectId": project_id,
            "failurePolicy": "fail_closed",
            "orderedStages": [
                {
                    "stageId": "security",
                    "moduleRefs": ["document_inspection@1.0.0"],
                },
                {
                    "stageId": "structure",
                    "moduleRefs": ["structural_extraction@1.0.0"],
                },
                {
                    "stageId": "ocr_gate",
                    "moduleRefs": [
                        PDF_OCR_PLANNING_MANIFEST.ref,
                    ],
                },
                {
                    "stageId": "pdf_enrichment",
                    "moduleRefs": [
                        PDF_NATIVE_TABLE_MANIFEST.ref,
                        SEMANTIC_SECTION_MANIFEST.ref,
                    ],
                },
                {
                    "stageId": "classification",
                    "moduleRefs": ["document_classification@1.0.0"],
                },
            ],
            "metadata": {
                "candidateOnly": True,
                "authoritativeProjectionAllowed": False,
                "storesOriginalTextInSemanticIndex": False,
                "externalOcrInvocationImplemented": False,
            },
        }
    )


def _document_input(payload: Mapping[str, Any]) -> ArchivedDocument:
    root = payload.get("input")
    if not isinstance(root, Mapping):
        raise ValueError("module payload input is required")
    document = root.get("document")
    if not isinstance(document, ArchivedDocument):
        raise TypeError(
            "input.document must be an ArchivedDocument resolved from "
            "object storage"
        )
    return document


def _upstream_extraction_artifact(
    payload: Mapping[str, Any],
    module_id: str,
) -> ExtractionArtifact:
    results = payload.get("module_results")
    if not isinstance(results, Mapping):
        raise ValueError("module_results are required")
    result = results.get(module_id)
    if not isinstance(result, Mapping):
        raise ValueError(f"{module_id} result is required")
    if result.get("execution_status") != "completed":
        raise ValueError(f"{module_id} must complete before enrichment")
    output = result.get("output")
    if not isinstance(output, Mapping):
        raise ValueError(f"{module_id} output is required")
    raw_artifact = output.get("artifact")
    if not isinstance(raw_artifact, Mapping):
        raise ValueError(f"{module_id} artifact is required")
    raw_chunks = raw_artifact.get("chunks")
    if not isinstance(raw_chunks, list):
        raise ValueError(f"{module_id} artifact chunks are required")
    chunks: list[ExtractionChunk] = []
    for item in raw_chunks:
        if not isinstance(item, Mapping):
            raise ValueError("structural extraction chunk must be an object")
        text = item.get("text")
        if not isinstance(text, str):
            raise ValueError(
                "semantic indexing requires in-process structural text"
            )
        locator = item.get("sourceLocator")
        if not isinstance(locator, Mapping):
            raise ValueError(
                "structural extraction chunk locator is required"
            )
        chunks.append(
            ExtractionChunk(
                chunk_id=str(item.get("chunkId") or ""),
                kind=str(item.get("kind") or ""),
                text=text,
                source_locator=dict(locator),
                metadata=(
                    dict(item.get("metadata") or {})
                    if isinstance(item.get("metadata"), Mapping)
                    else {}
                ),
            )
        )
    return ExtractionArtifact(
        adapter_id=str(raw_artifact.get("adapterId") or ""),
        adapter_version=str(raw_artifact.get("adapterVersion") or ""),
        document_id=str(raw_artifact.get("documentId") or ""),
        chunks=tuple(chunks),
        complete=raw_artifact.get("complete") is True,
        warnings=tuple(
            str(item) for item in raw_artifact.get("warnings") or []
        ),
        metadata=(
            dict(raw_artifact.get("metadata") or {})
            if isinstance(raw_artifact.get("metadata"), Mapping)
            else {}
        ),
    )


def _validate_strict_chunk_locators(
    artifact: ExtractionArtifact,
) -> None:
    for chunk in artifact.chunks:
        SourceLocator.model_validate(chunk.source_locator)


def _semantic_source_attestation_error(
    artifact: ExtractionArtifact,
    *,
    document: ArchivedDocument,
    context: Mapping[str, Any],
) -> tuple[str, str] | None:
    if artifact.complete is not True:
        return (
            "SEMANTIC_SOURCE_ARTIFACT_INCOMPLETE",
            "Semantic indexing abstained because structural extraction was incomplete.",
        )
    document_version_id = str(
        context.get("document_version_id") or ""
    ).strip()
    if not document_version_id:
        return (
            "SEMANTIC_DOCUMENT_VERSION_UNATTESTED",
            "Semantic indexing requires the current persisted document version.",
        )
    expected_document_id = str(
        context.get("document_id") or document.archive_id
    ).strip()
    if (
        document.sha256.casefold() != document.archive_id.casefold()
        or artifact.document_id != expected_document_id
        or artifact.metadata.get("locatorsBound") is not True
        or artifact.metadata.get("documentVersionId")
        != document_version_id
        or str(artifact.metadata.get("fileSha256") or "").casefold()
        != document.sha256.casefold()
    ):
        return (
            "SEMANTIC_SOURCE_ATTESTATION_MISMATCH",
            "Semantic indexing abstained because the source artifact binding does not match the current archived document.",
        )

    parser_method = f"{artifact.adapter_id}@{artifact.adapter_version}"
    for chunk in artifact.chunks:
        try:
            locator = SourceLocator.model_validate(
                chunk.source_locator
            )
        except Exception:
            return (
                "SEMANTIC_SOURCE_LOCATOR_INVALID",
                "Semantic indexing abstained because a structural locator is invalid.",
            )
        raw_text_sha256 = hashlib.sha256(
            chunk.text.encode("utf-8")
        ).hexdigest()
        evidence_id = _expected_evidence_id(
            document_version_id,
            chunk.chunk_id,
        )
        if (
            locator.document_id != expected_document_id
            or locator.document_version_id != document_version_id
            or str(locator.file_sha256 or "").casefold()
            != document.sha256.casefold()
            or str(locator.raw_text_sha256 or "").casefold()
            != raw_text_sha256
            or locator.evidence_id != evidence_id
            or locator.parser_method != parser_method
        ):
            return (
                "SEMANTIC_SOURCE_ATTESTATION_MISMATCH",
                "Semantic indexing abstained because a structural locator does not attest to the current document version.",
            )
    return None


def _semantic_abstention_output(
    artifact: ExtractionArtifact,
    *,
    code: str,
    message: str,
) -> dict[str, Any]:
    source_artifact_hash = hashlib.sha256(
        json.dumps(
            artifact.to_dict(include_text=False),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "status": "abstained",
        "sectionIndex": {
            "schemaVersion": SEMANTIC_SECTION_SCHEMA_VERSION,
            "documentId": artifact.document_id,
            "complete": False,
            "sectionCount": 0,
            "sourceArtifactHash": source_artifact_hash,
            "sections": [],
            "diagnostics": [
                {
                    "code": code,
                    "severity": "high",
                    "blocking": True,
                    "message": message,
                }
            ],
            "metadata": {
                "abstained": True,
                "storesOriginalText": False,
                "authorityStatus": "structural_index_only",
                "approvalEligible": False,
                "reportEligible": False,
                "factPromotionAllowed": False,
            },
        },
        "providedCapabilities": [],
        **_authority_boundary(),
    }


def _expected_evidence_id(
    document_version_id: str,
    chunk_id: str,
) -> str:
    material = f"{document_version_id}|{chunk_id}".encode("utf-8")
    return (
        "parser-evidence:"
        + hashlib.sha256(material).hexdigest()[:32]
    )


def _validate_module_envelope(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("module envelope is required")
    if set(value) != {
        "input",
        "module_results",
        "available_capabilities",
    }:
        raise ValueError("module envelope fields are invalid")
    if not isinstance(value.get("input"), Mapping):
        raise ValueError("module input is required")
    if not isinstance(value.get("module_results"), Mapping):
        raise ValueError("module results are required")
    capabilities = value.get("available_capabilities")
    if not isinstance(capabilities, list):
        raise ValueError("available capabilities must be a list")
    return value


def _validate_document_module_input(value: Any) -> None:
    envelope = _validate_module_envelope(value)
    document = envelope["input"].get("document")
    if not isinstance(document, ArchivedDocument):
        raise ValueError("archived document is required")
    if document.sha256.casefold() != document.archive_id.casefold():
        raise ValueError("archived document hash mismatch")


def _validate_semantic_section_input(value: Any) -> None:
    envelope = _validate_module_envelope(value)
    _validate_document_module_input(value)
    _upstream_extraction_artifact(envelope, "structural_extraction")


def _validate_pdf_table_output(value: Any) -> None:
    output = _output_mapping(value)
    _required_output_fields(
        output,
        {
            "status",
            "artifact",
            "diagnostics",
            "providedCapabilities",
            *_authority_boundary(),
        },
    )
    if output["artifact"] is not None and not isinstance(
        output["artifact"], Mapping
    ):
        raise ValueError("PDF table artifact must be an object or null")


def _validate_semantic_section_output(value: Any) -> None:
    output = _output_mapping(value)
    _required_output_fields(
        output,
        {
            "status",
            "sectionIndex",
            "providedCapabilities",
            *_authority_boundary(),
        },
    )
    index = output.get("sectionIndex")
    if not isinstance(index, Mapping):
        raise ValueError("semantic section index is required")
    serialized = str(index)
    if index.get("metadata", {}).get("storesOriginalText") is not False:
        raise ValueError("semantic section index must not store source text")
    if "'text':" in serialized or '"text":' in serialized:
        raise ValueError("semantic section index contains a text field")
    provided = output.get("providedCapabilities")
    completed = output.get("status") == "completed"
    if completed:
        if (
            index.get("complete") is not True
            or not index.get("sections")
            or provided != ["document.semantic_sections"]
        ):
            raise ValueError(
                "completed semantic output requires attested sections and capability"
            )
    elif provided != []:
        raise ValueError(
            "abstained semantic output cannot provide a capability"
        )


def _validate_pdf_ocr_planning_output(value: Any) -> None:
    output = _output_mapping(value)
    _required_output_fields(
        output,
        {
            "status",
            "assessment",
            "dispatchPlan",
            "providedCapabilities",
            *_authority_boundary(),
        },
    )
    if not isinstance(output.get("assessment"), Mapping):
        raise ValueError("OCR assessment is required")
    plan = output.get("dispatchPlan")
    if not isinstance(plan, Mapping):
        raise ValueError("OCR dispatch plan is required")
    if plan.get("factPromotionAllowed") is not False:
        raise ValueError("OCR dispatch plans cannot promote facts")
    provided = output.get("providedCapabilities")
    if output.get("status") == "not_required":
        if provided != ["document.ocr_assessed"]:
            raise ValueError(
                "a completed PDF OCR assessment must provide only "
                "document.ocr_assessed"
            )
    elif provided != []:
        raise ValueError(
            "non-applicable OCR assessment cannot provide capabilities"
        )


def _output_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("module output must be an object")
    return value


def _required_output_fields(
    output: Mapping[str, Any],
    required: set[str],
) -> None:
    if set(output) != required:
        raise ValueError("module output fields are invalid")


def _authority_boundary() -> dict[str, bool | str]:
    return {
        "authorityStatus": "structural_evidence_only",
        "approvalEligible": False,
        "reportEligible": False,
        "factPromotionAllowed": False,
    }


def _is_pdf(document: ArchivedDocument) -> bool:
    return (
        document.extension == ".pdf"
        or str(document.content_type or "").casefold()
        == "application/pdf"
    )


__all__ = [
    "PDF_OCR_PLANNING_INPUT_SCHEMA",
    "PDF_OCR_PLANNING_MANIFEST",
    "PDF_OCR_PLANNING_OUTPUT_SCHEMA",
    "PdfNativeTableModule",
    "PdfOcrAssessmentDispatchPlanModule",
    "SemanticSectionModule",
    "build_pdf_enrichment_module_registry",
    "build_pdf_enrichment_orchestrator",
    "build_pdf_enrichment_schema_registry",
    "pdf_enrichment_profile",
    "register_pdf_enrichment_schema_contracts",
]
