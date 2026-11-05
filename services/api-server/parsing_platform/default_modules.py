from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agents.document_classifier_agent import classify_document
from parsing_platform.adapters import (
    ArchivedDocument,
    DocumentAdapterRegistry,
    XlsxStructureAdapter,
    bind_artifact_to_document_version,
    detect_document_instruction_text,
    inspect_archived_document,
)
from parsing_platform.document_taxonomy import (
    CLASSIFIER_DOCUMENT_TYPES,
    DOCUMENT_TAXONOMY_VERSION,
    DOCUMENT_TYPE_SCOPE_ALL,
)
from parsing_platform.orchestrator import ParsingOrchestrator, ProjectParsingProfile
from parsing_platform.registry import ParserModuleManifest, ParserModuleRegistry
from parsing_platform.schema_contracts import (
    DOCUMENT_CLASSIFICATION_INPUT_SCHEMA,
    DOCUMENT_CLASSIFICATION_OUTPUT_SCHEMA,
    DOCUMENT_INSPECTION_INPUT_SCHEMA,
    DOCUMENT_INSPECTION_OUTPUT_SCHEMA,
    STRUCTURAL_EXTRACTION_INPUT_SCHEMA,
    STRUCTURAL_EXTRACTION_OUTPUT_SCHEMA,
)


class DocumentInspectionBlocked(RuntimeError):
    code = "DOCUMENT_INSPECTION_BLOCKED"
    public_message = "Document security inspection did not grant a safe-to-parse capability."


class UnsupportedDocumentAdapter(RuntimeError):
    code = "DOCUMENT_ADAPTER_NOT_AVAILABLE"
    public_message = "No approved structural adapter is available for this document type."


@dataclass
class DocumentInspectionModule:
    manifest = ParserModuleManifest(
        module_id="document_inspection",
        version="1.0.0",
        capabilities=("document.security", "document.integrity"),
        document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
        document_type_scope=DOCUMENT_TYPE_SCOPE_ALL,
        provides=("document.inspection_completed",),
        conditional_provides=("document.safe",),
        execution_class="deterministic",
        input_schema=DOCUMENT_INSPECTION_INPUT_SCHEMA,
        output_schema=DOCUMENT_INSPECTION_OUTPUT_SCHEMA,
        required_locator_precision="document",
        data_classification="restricted",
        max_output_bytes=1 * 1024 * 1024,
        metadata={"deterministic": True, "authoritative": False},
    )

    def execute(self, payload: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        document = _document_input(payload)
        inspection = inspect_archived_document(document)
        return {
            "inspection": inspection.to_dict(),
            "providedCapabilities": [] if inspection.blocked else ["document.safe"],
        }


@dataclass
class StructuralExtractionModule:
    adapter_registry: DocumentAdapterRegistry

    manifest = ParserModuleManifest(
        module_id="structural_extraction",
        version="1.0.0",
        capabilities=("document.native_structure",),
        document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
        document_type_scope=DOCUMENT_TYPE_SCOPE_ALL,
        requires=("document.safe",),
        provides=("document.structure",),
        execution_class="deterministic",
        input_schema=STRUCTURAL_EXTRACTION_INPUT_SCHEMA,
        output_schema=STRUCTURAL_EXTRACTION_OUTPUT_SCHEMA,
        required_locator_precision="exact",
        data_classification="restricted",
        max_output_bytes=20 * 1024 * 1024,
        metadata={
            "deterministic": True,
            "authoritative": False,
            "untrustedDocumentText": True,
        },
    )

    def execute(self, payload: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        inspection = _module_output(payload, "document_inspection").get("inspection") or {}
        if inspection.get("blocked"):
            codes = [item.get("code") for item in inspection.get("findings") or [] if item.get("blocking")]
            raise DocumentInspectionBlocked(
                "Document inspection blocked structural extraction: " + ", ".join(codes)
            )
        document = _document_input(payload)
        try:
            adapter = self.adapter_registry.resolve(document)
        except LookupError as exc:
            raise UnsupportedDocumentAdapter(str(exc)) from exc
        # This profile promises a bounded source preview, not full workbook
        # mapping. Respect that budget before binding/serializing huge sheets.
        if isinstance(adapter, XlsxStructureAdapter):
            from parsing_platform.source_review_candidates import PROFILE_ID, PROFILE_VERSION, MAX_CHUNKS, MAX_WORKBOOK_CHUNKS
            if context.get("profile_id") == PROFILE_ID and context.get("profile_version") == PROFILE_VERSION:
                adapter = XlsxStructureAdapter(max_rows_per_sheet=adapter.max_rows_per_sheet,
                                               max_cells=adapter.max_cells, max_chunks=min(MAX_CHUNKS, MAX_WORKBOOK_CHUNKS))
        artifact = adapter.extract(document)
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
        injection_findings = detect_document_instruction_text(artifact.chunks)
        return {
            "artifact": artifact.to_dict(include_text=True),
            "securityFindings": [
                {
                    "code": finding.code,
                    "severity": finding.severity,
                    "message": finding.message,
                    "blocking": finding.blocking,
                    "metadata": finding.metadata,
                }
                for finding in injection_findings
            ],
            "documentTextTrust": "untrusted_data_only",
        }


@dataclass
class DocumentClassificationModule:
    manifest = ParserModuleManifest(
        module_id="document_classification",
        version="1.0.0",
        capabilities=("document.routing",),
        supported_document_types=CLASSIFIER_DOCUMENT_TYPES,
        document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
        requires=("document.structure",),
        provides=("document.classified",),
        execution_class="deterministic",
        input_schema=DOCUMENT_CLASSIFICATION_INPUT_SCHEMA,
        output_schema=DOCUMENT_CLASSIFICATION_OUTPUT_SCHEMA,
        required_locator_precision="page_or_cell",
        data_classification="confidential",
        max_output_bytes=1 * 1024 * 1024,
        metadata={"deterministic": True, "authoritative": False},
    )

    def execute(self, payload: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        document = _document_input(payload)
        artifact = _module_output(payload, "structural_extraction").get("artifact") or {}
        chunks = artifact.get("chunks") if isinstance(artifact.get("chunks"), list) else []
        sample_text = "\n".join(
            str(item.get("text") or "")
            for item in chunks[:50]
            if isinstance(item, dict)
        )[:8_000]
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        sheet_names = metadata.get("sheetNames") if isinstance(metadata.get("sheetNames"), list) else None
        classification = classify_document(
            document.filename,
            content_type=document.content_type,
            size=len(document.content),
            sheet_names=sheet_names,
            sample_text=sample_text,
        )
        return {
            "classification": classification,
            "authorityStatus": "unreviewed",
            "legalFinality": "unknown",
        }


def build_default_parser_module_registry(
    *,
    adapter_registry: DocumentAdapterRegistry | None = None,
) -> ParserModuleRegistry:
    registry = ParserModuleRegistry()
    registry.register(DocumentInspectionModule())
    registry.register(StructuralExtractionModule(adapter_registry or DocumentAdapterRegistry()))
    registry.register(DocumentClassificationModule())
    return registry


def intake_core_profile(*, project_id: str | None = None) -> ProjectParsingProfile:
    return ProjectParsingProfile.from_value(
        {
            "profileId": "realytics.intake_core",
            "version": "1.0.0",
            "documentTaxonomyVersion": DOCUMENT_TAXONOMY_VERSION,
            "documentTypes": list(CLASSIFIER_DOCUMENT_TYPES),
            "projectId": project_id,
            "failurePolicy": "fail_closed",
            "orderedStages": [
                {"stageId": "security", "moduleRefs": ["document_inspection@1.0.0"]},
                {"stageId": "structure", "moduleRefs": ["structural_extraction@1.0.0"]},
                {"stageId": "classification", "moduleRefs": ["document_classification@1.0.0"]},
            ],
            "metadata": {
                "candidateOnly": True,
                "authoritativeProjectionAllowed": False,
            },
        }
    )


def build_default_intake_orchestrator() -> ParsingOrchestrator:
    return ParsingOrchestrator(build_default_parser_module_registry())


def sanitize_parsing_orchestration_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Remove sensitive module bodies before API/audit persistence.

    This is deliberately recursive so a newly registered module cannot bypass
    the boundary merely by choosing a different output path.
    """

    sanitized = _deep_copy(result)
    _redact_sensitive_transport(sanitized)
    # ``outputs`` duplicates module output in the orchestration transport.
    sanitized.pop("outputs", None)
    sanitized["rawDocumentTextPersisted"] = False
    sanitized["candidateOnly"] = True
    sanitized["authoritative"] = False
    return sanitized


def _document_input(payload: Mapping[str, Any]) -> ArchivedDocument:
    root = payload.get("input")
    if not isinstance(root, Mapping):
        raise ValueError("module payload input is required")
    document = root.get("document")
    if not isinstance(document, ArchivedDocument):
        raise TypeError("input.document must be an ArchivedDocument resolved from object storage")
    return document


def _module_output(payload: Mapping[str, Any], module_id: str) -> dict[str, Any]:
    results = payload.get("module_results")
    if not isinstance(results, Mapping):
        return {}
    result = results.get(module_id)
    if not isinstance(result, Mapping):
        return {}
    output = result.get("output")
    return dict(output) if isinstance(output, Mapping) else {}


def _deep_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    import copy

    return copy.deepcopy(dict(value))


def _redact_sensitive_transport(value: Any) -> None:
    import hashlib
    import json
    import re

    if isinstance(value, list):
        for item in value:
            _redact_sensitive_transport(item)
        return
    if not isinstance(value, dict):
        return
    secret_keys = {
        "apikey",
        "authorization",
        "credential",
        "password",
        "secret",
        "token",
    }
    body_keys = {
        "body",
        "cachedvalue",
        "cells",
        "content",
        "documenttext",
        "formula",
        "normalizedvalue",
        "prompt",
        "rawbody",
        "rawcontent",
        "rawtextexcerpt",
        "rawvalue",
        "systemprompt",
        "text",
        "value",
        "workbookcachedvalue",
        "workbookformula",
    }
    for key in list(value):
        normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
        item = value[key]
        if normalized in secret_keys:
            value.pop(key, None)
            continue
        if normalized in body_keys:
            if isinstance(item, str):
                raw = item.encode("utf-8")
            elif isinstance(item, (bytes, bytearray)):
                raw = bytes(item)
            else:
                raw = json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            value.pop(key, None)
            if normalized in {"workbookformula", "workbookcachedvalue"}:
                metadata = value.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                    value["metadata"] = metadata
                if normalized == "workbookformula":
                    metadata["workbookFormulaSha256"] = hashlib.sha256(raw).hexdigest()
                    metadata["workbookFormulaPersisted"] = False
                else:
                    metadata["workbookCachedValueSha256"] = hashlib.sha256(raw).hexdigest()
                    metadata["workbookCachedValuePersisted"] = False
                metadata["formulaCacheAuthoritative"] = False
                metadata["workbookCachedValueApprovalEligible"] = False
                continue
            hash_key = "textSha256" if normalized == "text" else f"{key}Sha256"
            value.setdefault(hash_key, hashlib.sha256(raw).hexdigest())
            continue
        _redact_sensitive_transport(item)
