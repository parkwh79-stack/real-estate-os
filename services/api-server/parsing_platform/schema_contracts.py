"""Central, versioned payload contracts for independently composed parser modules.

The registry validates the *actual* module envelope immediately before a module
call and the canonical JSON object immediately after it returns.  Validation
errors deliberately expose only schema IDs and controlled reason codes; source
text, values, credentials, and validator exception messages never cross the
module boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from threading import RLock
from typing import Any

from parsing_platform.adapters import ArchivedDocument
from parsing_platform.document_taxonomy import (
    DOCUMENT_TAXONOMY_VERSION,
    canonical_document_type,
)


GENERIC_MODULE_INPUT_SCHEMA = "realytics-parser-module-input-v1"
GENERIC_MODULE_OUTPUT_SCHEMA = "realytics-parser-module-output-v1"

DOCUMENT_INSPECTION_INPUT_SCHEMA = "realytics-document-inspection-input-v1"
DOCUMENT_INSPECTION_OUTPUT_SCHEMA = "realytics-document-inspection-output-v1"
STRUCTURAL_EXTRACTION_INPUT_SCHEMA = "realytics-structural-extraction-input-v1"
STRUCTURAL_EXTRACTION_OUTPUT_SCHEMA = "realytics-structural-extraction-output-v1"
DOCUMENT_CLASSIFICATION_INPUT_SCHEMA = "realytics-document-classification-input-v1"
DOCUMENT_CLASSIFICATION_OUTPUT_SCHEMA = "realytics-document-classification-output-v1"

_SCHEMA_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]*-v[1-9][0-9]*$")
SchemaValidator = Callable[[Any], Any]


class SchemaContractError(ValueError):
    """Base error for schema registration, lookup, and validation."""


class InvalidSchemaContractError(SchemaContractError):
    pass


class DuplicateSchemaContractError(SchemaContractError):
    pass


class UnknownSchemaContractError(SchemaContractError):
    pass


class SchemaValidationError(SchemaContractError):
    """Sanitized boundary failure that never contains the rejected payload."""

    public_message = "Module payload violated its declared schema contract."

    def __init__(
        self,
        *,
        schema_id: str,
        boundary: str,
        reason_code: str,
        module_ref: str | None = None,
    ) -> None:
        self.schema_id = schema_id
        self.boundary = boundary
        self.reason_code = _safe_token(reason_code, fallback="contract_rejected")
        self.module_ref = _safe_token(module_ref, fallback="module") if module_ref else None
        self.code = f"MODULE_{boundary.upper()}_SCHEMA_VALIDATION_FAILED"
        location = f" for {self.module_ref}" if self.module_ref else ""
        super().__init__(
            f"Schema {schema_id} rejected the {boundary} boundary{location} "
            f"({self.reason_code})"
        )


class _ContractViolation(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class RegisteredSchemaContract:
    schema_id: str
    validator: SchemaValidator
    contract_hash: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {
            "schemaId": self.schema_id,
            "contractHash": self.contract_hash,
            "description": self.description,
        }


class SchemaContractRegistry:
    """Thread-safe registry with immutable schema-ID ownership."""

    def __init__(self) -> None:
        self._contracts: dict[str, RegisteredSchemaContract] = {}
        self._lock = RLock()

    def register(
        self,
        schema_id: str,
        validator: SchemaValidator,
        *,
        definition: Mapping[str, Any],
        description: str = "",
    ) -> RegisteredSchemaContract:
        normalized_id = _schema_id(schema_id)
        if not callable(validator):
            raise InvalidSchemaContractError("schema validator must be callable")
        try:
            encoded = json.dumps(
                dict(definition),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise InvalidSchemaContractError(
                "schema definition must be deterministic JSON"
            ) from exc
        contract = RegisteredSchemaContract(
            schema_id=normalized_id,
            validator=validator,
            contract_hash=hashlib.sha256(encoded).hexdigest(),
            description=str(description or "")[:256],
        )
        with self._lock:
            if normalized_id in self._contracts:
                raise DuplicateSchemaContractError(
                    f"schema ID is already registered and immutable: {normalized_id}"
                )
            self._contracts[normalized_id] = contract
        return contract

    def require(self, schema_id: str) -> RegisteredSchemaContract:
        normalized_id = _schema_id(schema_id)
        with self._lock:
            contract = self._contracts.get(normalized_id)
        if contract is None:
            raise UnknownSchemaContractError(
                f"unregistered parser module schema: {normalized_id}"
            )
        return contract

    def validate(
        self,
        schema_id: str,
        payload: Any,
        *,
        boundary: str,
        module_ref: str | None = None,
    ) -> None:
        if boundary not in {"input", "output"}:
            raise InvalidSchemaContractError("schema boundary must be input or output")
        contract = self.require(schema_id)
        try:
            result = contract.validator(payload)
            if result is False:
                raise _ContractViolation("validator_rejected")
        except _ContractViolation as exc:
            raise SchemaValidationError(
                schema_id=contract.schema_id,
                boundary=boundary,
                reason_code=exc.reason_code,
                module_ref=module_ref,
            ) from None
        except Exception:
            # A third-party validator may include source text or secrets in its
            # exception.  Never propagate its type, message, repr, or args.
            raise SchemaValidationError(
                schema_id=contract.schema_id,
                boundary=boundary,
                reason_code="validator_rejected",
                module_ref=module_ref,
            ) from None

    def snapshot(self) -> list[dict[str, str]]:
        with self._lock:
            contracts = list(self._contracts.values())
        return [item.to_dict() for item in sorted(contracts, key=lambda item: item.schema_id)]


def build_default_schema_contract_registry() -> SchemaContractRegistry:
    registry = SchemaContractRegistry()
    registrations: tuple[
        tuple[str, SchemaValidator, Mapping[str, Any], str], ...
    ] = (
        (
            GENERIC_MODULE_INPUT_SCHEMA,
            _validate_generic_input,
            {"type": "module-envelope", "required": ["input", "module_results", "available_capabilities"]},
            "Generic parser module execution envelope",
        ),
        (
            GENERIC_MODULE_OUTPUT_SCHEMA,
            _validate_generic_output,
            {"type": "json-object"},
            "Generic parser module JSON output",
        ),
        (
            DOCUMENT_INSPECTION_INPUT_SCHEMA,
            _validate_inspection_input,
            {"type": "module-envelope", "input": "ArchivedDocument"},
            "Archived document inspection input",
        ),
        (
            DOCUMENT_INSPECTION_OUTPUT_SCHEMA,
            _validate_inspection_output,
            {"type": "inspection-output", "version": 1},
            "Document security and integrity inspection output",
        ),
        (
            STRUCTURAL_EXTRACTION_INPUT_SCHEMA,
            _validate_structural_input,
            {"type": "module-envelope", "upstream": "document_inspection"},
            "Safe archived document plus inspection result",
        ),
        (
            STRUCTURAL_EXTRACTION_OUTPUT_SCHEMA,
            _validate_structural_output,
            {"type": "structural-extraction-output", "version": 1},
            "Native document structure output",
        ),
        (
            DOCUMENT_CLASSIFICATION_INPUT_SCHEMA,
            _validate_classification_input,
            {"type": "module-envelope", "upstream": "structural_extraction"},
            "Archived document plus structural extraction result",
        ),
        (
            DOCUMENT_CLASSIFICATION_OUTPUT_SCHEMA,
            _validate_classification_output,
            {"type": "document-classification-output", "version": 1},
            "Deterministic document routing classification",
        ),
    )
    for schema_id, validator, definition, description in registrations:
        registry.register(
            schema_id,
            validator,
            definition=definition,
            description=description,
        )
    return registry


def _schema_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not _SCHEMA_ID.fullmatch(normalized):
        raise InvalidSchemaContractError(
            "schema ID must be a lowercase, explicitly versioned identifier ending in -vN"
        )
    return normalized


def _safe_token(value: Any, *, fallback: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.:@-]", "_", str(value or ""))[:160]
    return sanitized or fallback


def _mapping(value: Any, reason: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _ContractViolation(reason)
    return value


def _list(value: Any, reason: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise _ContractViolation(reason)
    return value


def _required(value: Mapping[str, Any], keys: Sequence[str], reason: str) -> None:
    if any(key not in value for key in keys):
        raise _ContractViolation(reason)


def _exact_keys(value: Mapping[str, Any], keys: Sequence[str], reason: str) -> None:
    if set(value) != set(keys):
        raise _ContractViolation(reason)


def _strings(value: Any, reason: str) -> Sequence[Any]:
    items = _list(value, reason)
    if any(not isinstance(item, str) or not item for item in items):
        raise _ContractViolation(reason)
    return items


def _validate_generic_input(value: Any) -> None:
    payload = _mapping(value, "input_envelope_required")
    _exact_keys(
        payload,
        ("input", "module_results", "available_capabilities"),
        "input_envelope_fields_invalid",
    )
    _mapping(payload["input"], "input_object_required")
    _mapping(payload["module_results"], "module_results_object_required")
    _strings(payload["available_capabilities"], "capability_list_invalid")


def _validate_generic_output(value: Any) -> None:
    _mapping(value, "output_object_required")


def _archived_document(envelope: Mapping[str, Any]) -> ArchivedDocument:
    root = _mapping(envelope["input"], "input_object_required")
    document = root.get("document")
    if not isinstance(document, ArchivedDocument):
        raise _ContractViolation("archived_document_required")
    if document.sha256 != document.archive_id.lower():
        raise _ContractViolation("archive_hash_mismatch")
    if not document.project_id or not document.tenant_id:
        raise _ContractViolation("document_scope_required")
    return document


def _upstream_output(envelope: Mapping[str, Any], module_id: str) -> Mapping[str, Any]:
    module_results = _mapping(
        envelope["module_results"], "module_results_object_required"
    )
    result = _mapping(module_results.get(module_id), "required_upstream_result_missing")
    if result.get("execution_status") != "completed":
        raise _ContractViolation("upstream_result_not_completed")
    return _mapping(result.get("output"), "upstream_output_required")


def _validate_inspection_input(value: Any) -> None:
    _validate_generic_input(value)
    envelope = _mapping(value, "input_envelope_required")
    _archived_document(envelope)


def _validate_inspection_output(value: Any) -> None:
    output = _mapping(value, "inspection_output_required")
    _exact_keys(
        output,
        ("inspection", "providedCapabilities"),
        "inspection_output_fields_invalid",
    )
    inspection = _mapping(output["inspection"], "inspection_record_required")
    _exact_keys(
        inspection,
        (
            "schemaVersion",
            "archiveId",
            "status",
            "mediaFamily",
            "fileSizeBytes",
            "sha256Verified",
            "blocked",
            "findings",
            "metadata",
        ),
        "inspection_record_fields_invalid",
    )
    if not isinstance(inspection["schemaVersion"], str):
        raise _ContractViolation("inspection_schema_version_invalid")
    if not isinstance(inspection["archiveId"], str):
        raise _ContractViolation("inspection_archive_id_invalid")
    if not isinstance(inspection["fileSizeBytes"], int) or inspection["fileSizeBytes"] < 0:
        raise _ContractViolation("inspection_size_invalid")
    if type(inspection["sha256Verified"]) is not bool or type(inspection["blocked"]) is not bool:
        raise _ContractViolation("inspection_boolean_invalid")
    findings = _list(inspection["findings"], "inspection_findings_invalid")
    for finding in findings:
        item = _mapping(finding, "inspection_finding_invalid")
        _required(item, ("code", "severity", "message", "blocking", "metadata"), "inspection_finding_fields_invalid")
        if type(item["blocking"]) is not bool:
            raise _ContractViolation("inspection_finding_blocking_invalid")
    _mapping(inspection["metadata"], "inspection_metadata_invalid")
    _strings(output["providedCapabilities"], "provided_capabilities_invalid")


def _validate_structural_input(value: Any) -> None:
    _validate_generic_input(value)
    envelope = _mapping(value, "input_envelope_required")
    _archived_document(envelope)
    capabilities = _strings(
        envelope["available_capabilities"], "capability_list_invalid"
    )
    if "document.safe" not in capabilities:
        raise _ContractViolation("document_safe_capability_required")
    _validate_inspection_output(_upstream_output(envelope, "document_inspection"))


def _validate_structural_output(value: Any) -> None:
    output = _mapping(value, "structure_output_required")
    _exact_keys(
        output,
        ("artifact", "securityFindings", "documentTextTrust"),
        "structure_output_fields_invalid",
    )
    artifact = _mapping(output["artifact"], "structure_artifact_required")
    _exact_keys(
        artifact,
        (
            "schemaVersion",
            "adapterId",
            "adapterVersion",
            "documentId",
            "complete",
            "chunkCount",
            "warnings",
            "metadata",
            "chunks",
        ),
        "structure_artifact_fields_invalid",
    )
    chunks = _list(artifact["chunks"], "structure_chunks_invalid")
    if not isinstance(artifact["chunkCount"], int) or artifact["chunkCount"] != len(chunks):
        raise _ContractViolation("structure_chunk_count_invalid")
    if type(artifact["complete"]) is not bool:
        raise _ContractViolation("structure_complete_invalid")
    _strings(artifact["warnings"], "structure_warnings_invalid")
    _mapping(artifact["metadata"], "structure_metadata_invalid")
    for chunk in chunks:
        item = _mapping(chunk, "structure_chunk_invalid")
        _exact_keys(
            item,
            ("chunkId", "kind", "text", "sourceLocator", "metadata"),
            "structure_chunk_fields_invalid",
        )
        if not isinstance(item["chunkId"], str) or not isinstance(item["kind"], str):
            raise _ContractViolation("structure_chunk_identity_invalid")
        if not isinstance(item["text"], str):
            raise _ContractViolation("structure_chunk_text_invalid")
        _mapping(item["sourceLocator"], "structure_source_locator_invalid")
        _mapping(item["metadata"], "structure_chunk_metadata_invalid")
    findings = _list(output["securityFindings"], "security_findings_invalid")
    for finding in findings:
        item = _mapping(finding, "security_finding_invalid")
        _exact_keys(
            item,
            ("code", "severity", "message", "blocking", "metadata"),
            "security_finding_fields_invalid",
        )
    if output["documentTextTrust"] != "untrusted_data_only":
        raise _ContractViolation("document_text_trust_invalid")


def _validate_classification_input(value: Any) -> None:
    _validate_generic_input(value)
    envelope = _mapping(value, "input_envelope_required")
    _archived_document(envelope)
    _validate_structural_output(_upstream_output(envelope, "structural_extraction"))


def _validate_classification_output(value: Any) -> None:
    output = _mapping(value, "classification_output_required")
    _exact_keys(
        output,
        ("classification", "authorityStatus", "legalFinality"),
        "classification_output_fields_invalid",
    )
    classification = _mapping(output["classification"], "classification_record_required")
    _exact_keys(
        classification,
        (
            "schemaVersion",
            "taxonomyVersion",
            "filename",
            "ext",
            "contentType",
            "size",
            "documentType",
            "label",
            "suggestedAction",
            "confidence",
            "confidenceBasis",
            "needsReview",
            "reasons",
            "candidates",
        ),
        "classification_record_fields_invalid",
    )
    if classification["taxonomyVersion"] != DOCUMENT_TAXONOMY_VERSION:
        raise _ContractViolation("classification_taxonomy_version_invalid")
    try:
        canonical_type = canonical_document_type(classification["documentType"])
    except ValueError:
        raise _ContractViolation("classification_document_type_invalid") from None
    if canonical_type != classification["documentType"]:
        raise _ContractViolation("classification_document_type_not_canonical")
    if not isinstance(classification["confidence"], (int, float)):
        raise _ContractViolation("classification_confidence_invalid")
    if type(classification["needsReview"]) is not bool:
        raise _ContractViolation("classification_review_flag_invalid")
    _strings(classification["reasons"], "classification_reasons_invalid")
    candidates = _list(classification["candidates"], "classification_candidates_invalid")
    for candidate in candidates:
        item = _mapping(candidate, "classification_candidate_invalid")
        _exact_keys(
            item,
            ("documentType", "label", "score"),
            "classification_candidate_fields_invalid",
        )
        try:
            candidate_type = canonical_document_type(item["documentType"])
        except ValueError:
            raise _ContractViolation(
                "classification_candidate_document_type_invalid"
            ) from None
        if candidate_type != item["documentType"]:
            raise _ContractViolation(
                "classification_candidate_document_type_not_canonical"
            )


__all__ = [
    "DOCUMENT_CLASSIFICATION_INPUT_SCHEMA",
    "DOCUMENT_CLASSIFICATION_OUTPUT_SCHEMA",
    "DOCUMENT_INSPECTION_INPUT_SCHEMA",
    "DOCUMENT_INSPECTION_OUTPUT_SCHEMA",
    "DuplicateSchemaContractError",
    "GENERIC_MODULE_INPUT_SCHEMA",
    "GENERIC_MODULE_OUTPUT_SCHEMA",
    "InvalidSchemaContractError",
    "RegisteredSchemaContract",
    "STRUCTURAL_EXTRACTION_INPUT_SCHEMA",
    "STRUCTURAL_EXTRACTION_OUTPUT_SCHEMA",
    "SchemaContractError",
    "SchemaContractRegistry",
    "SchemaValidationError",
    "UnknownSchemaContractError",
    "build_default_schema_contract_registry",
]
