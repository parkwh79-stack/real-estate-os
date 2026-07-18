from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping
from uuid import uuid4

from parsing_platform.adapters import ArchivedDocument
from parsing_platform.default_modules import sanitize_parsing_orchestration_result
from parsing_platform.orchestrator import (
    ParsingOrchestrator,
    ProjectParsingProfile,
    build_plan_hash,
)
from use_cases.parser_v2_candidate_collector import (
    CandidateCollectionResult,
    collect_parser_module_candidates,
    seal_collected_candidate_set,
)
from use_cases.parser_v2_records import ParserV2RecordStore


PARSER_V2_PLATFORM_VERSION = "realytics-parser-platform-v2.0.0"
DEFAULT_ONTOLOGY_VERSION = "realytics-real-estate-domain-standard-v1"
DEFAULT_PROMPT_VERSION = "structural-intake-no-llm-v1"
DEFAULT_MODEL_VERSION = "deterministic-only"


def run_structural_parsing_use_case(
    *,
    document: ArchivedDocument,
    actor_id: str,
    profile: ProjectParsingProfile,
    orchestrator: ParsingOrchestrator,
    record_store: ParserV2RecordStore,
    document_id: str | None = None,
    document_version_id: str | None = None,
    object_version: str | None = None,
    version_number: int = 1,
    source_authority: str = "unknown",
    predecessor_version_id: str | None = None,
    parsing_run_id: str | None = None,
    idempotency_key: str | None = None,
    parser_version: str = PARSER_V2_PLATFORM_VERSION,
    ontology_version: str = DEFAULT_ONTOLOGY_VERSION,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    model_version: str = DEFAULT_MODEL_VERSION,
    canonical_workbook_mapping: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the candidate-only structural lane and persist only audit metadata.

    Raw source text remains in memory while modules execute.  The persisted and
    returned orchestration artifact is sanitized to hashes + locators.  This use
    case never mutates legacy project JSON and never creates an approved value.
    """

    tenant_id = document.tenant_id
    if not tenant_id:
        raise ValueError("tenant-scoped document is required")
    if profile.project_id and profile.project_id != document.project_id:
        raise ValueError("project parsing profile belongs to another project")
    if document.sha256.lower() != document.archive_id.lower():
        raise ValueError(
            "archiveId/content SHA-256 mismatch; no Parser V2 records were written"
        )

    logical_document_id = document_id or f"document:{document.archive_id}"
    version_id = document_version_id or _document_version_id(
        document.project_id,
        logical_document_id,
        document.archive_id,
        object_version or document.object_version or document.archive_id,
    )
    run_id = parsing_run_id or f"parsing-run:{uuid4()}"
    plan = orchestrator.build_plan(profile)
    plan_hash = build_plan_hash(plan)
    requested_modules = [entry.module_id for entry in plan.modules]
    effective_workbook_mapping = _profile_workbook_mapping(
        profile,
        asserted_mapping=canonical_workbook_mapping,
    )
    domain_input_hash = _domain_input_hash(effective_workbook_mapping)
    input_hash = _parsing_input_hash(
        tenant_id=tenant_id,
        project_id=document.project_id,
        document_version_id=version_id,
        archive_id=document.archive_id,
        object_version=object_version
        or document.object_version
        or document.archive_id,
        plan_hash=plan_hash,
        parser_version=parser_version,
        ontology_version=ontology_version,
        prompt_version=prompt_version,
        model_version=model_version,
        domain_input_hash=domain_input_hash,
    )
    effective_idempotency_key = idempotency_key or _parsing_idempotency_key(
        tenant_id=tenant_id,
        project_id=document.project_id,
        document_version_id=version_id,
        profile_id=profile.profile_id,
        profile_version=profile.version,
        plan_hash=plan_hash,
        input_hash=input_hash,
        parser_version=parser_version,
        ontology_version=ontology_version,
        prompt_version=prompt_version,
        model_version=model_version,
    )

    document_record = record_store.create_document_version(
        tenant_id=tenant_id,
        project_id=document.project_id,
        document_id=logical_document_id,
        document_version_id=version_id,
        actor_id=actor_id,
        archive_id=document.archive_id,
        object_version=object_version or document.object_version or document.archive_id,
        source_hash=document.sha256,
        media_type=document.content_type or "application/octet-stream",
        original_file_name=document.filename,
        size_bytes=len(document.content),
        version_number=version_number,
        source_authority=source_authority,
        predecessor_version_id=predecessor_version_id,
    )
    if predecessor_version_id:
        record_store.invalidate_approvals_for_document_change(
            tenant_id=tenant_id,
            project_id=document.project_id,
            superseded_document_version_id=predecessor_version_id,
            replacement_document_version_id=version_id,
            actor_id=actor_id,
            reason=f"source document replaced by {version_id}",
        )
    run_record = record_store.create_parsing_run(
        tenant_id=tenant_id,
        project_id=document.project_id,
        run_id=run_id,
        document_version_id=version_id,
        actor_id=actor_id,
        parser_version=parser_version,
        ontology_version=ontology_version,
        prompt_version=prompt_version,
        model_version=model_version,
        profile_id=profile.profile_id,
        profile_version=profile.version,
        plan_hash=plan_hash,
        input_hash=input_hash,
        requested_modules=requested_modules,
        idempotency_key=effective_idempotency_key,
    )
    run_id = str(run_record["parsingRunId"])
    current_run = record_store.get_parsing_run(tenant_id, document.project_id, run_id)
    if current_run.get("executionStatus") in {
        "completed",
        "completed_with_warnings",
        "failed",
        "cancelled",
    }:
        replayed_result = {
            "schemaVersion": "realytics-parser-v2-structural-run-result-v1",
            "candidateOnly": True,
            "authoritative": False,
            "replayed": True,
            "documentVersion": document_record,
            "parsingRun": current_run,
            "orchestration": current_run.get("orchestrationArtifact"),
        }
        existing_seal = _candidate_set_seal_for_run(
            record_store,
            tenant_id=tenant_id,
            project_id=document.project_id,
            parsing_run_id=run_id,
        )
        if (
            existing_seal is None
            and current_run.get("executionStatus")
            in {"completed", "completed_with_warnings"}
        ):
            existing_seal = _repair_candidate_set_seal(
                record_store,
                tenant_id=tenant_id,
                project_id=document.project_id,
                parsing_run_id=run_id,
                actor_id=actor_id,
                run=current_run,
            )
        if existing_seal is not None:
            replayed_result["candidateSetSeal"] = existing_seal
        return replayed_result
    if current_run.get("executionStatus") == "running":
        return {
            "schemaVersion": "realytics-parser-v2-structural-run-result-v1",
            "candidateOnly": True,
            "authoritative": False,
            "replayed": True,
            "inProgress": True,
            "documentVersion": document_record,
            "parsingRun": current_run,
            "orchestration": None,
        }

    record_store.append_parsing_run_event(
        tenant_id=tenant_id,
        project_id=document.project_id,
        run_id=run_id,
        actor_id=actor_id,
        execution_status="running",
    )
    execution_payload: dict[str, Any] = {"document": document}
    if effective_workbook_mapping is not None:
        mapping_payload = json.loads(
            json.dumps(
                dict(effective_workbook_mapping),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
        execution_payload.update(
            {
                "canonicalWorkbookMapping": mapping_payload,
                "documentType": mapping_payload.get("documentType"),
                "candidateCreatedAt": document_record["ingestedAt"],
            }
        )
    collection: CandidateCollectionResult | None = None
    try:
        result = orchestrator.execute_sync(
            profile,
            execution_payload,
            context={
                "tenant_id": tenant_id,
                "project_id": document.project_id,
                "document_id": logical_document_id,
                "actor_id": actor_id,
                "parsing_run_id": run_id,
                "document_version_id": version_id,
                "source_hash": document.sha256,
                "source_authority": source_authority,
                "ontology_version": ontology_version,
                "mapping_attestation_hash": domain_input_hash,
                "candidate_created_at": document_record["ingestedAt"],
                "candidate_only": True,
            },
        )
        if result.execution_status in {"completed", "partial"}:
            collection = collect_parser_module_candidates(
                orchestration_result=result,
                record_store=record_store,
                tenant_id=tenant_id,
                project_id=document.project_id,
                parsing_run_id=run_id,
                document_version_id=version_id,
                source_hash=document.sha256,
                actor_id=actor_id,
                ontology_version=ontology_version,
                expected_module_ids=requested_modules,
            )
    except Exception as exc:
        record_store.append_parsing_run_event(
            tenant_id=tenant_id,
            project_id=document.project_id,
            run_id=run_id,
            actor_id=actor_id,
            execution_status="failed",
            error_codes=[type(exc).__name__],
        )
        raise

    if result.execution_status == "completed":
        final_status = "completed"
        error_codes: list[str] = []
    elif result.execution_status == "partial":
        final_status = "completed_with_warnings"
        error_codes = [
            *(f"MODULE_FAILED:{item}" for item in result.failed_module_ids),
            *(f"MODULE_SKIPPED:{item}" for item in result.skipped_module_ids),
        ]
    else:
        final_status = "failed"
        error_codes = [
            *(f"MODULE_FAILED:{item}" for item in result.failed_module_ids),
            *(f"MODULE_SKIPPED:{item}" for item in result.skipped_module_ids),
        ] or ["PARSING_ORCHESTRATION_FAILED"]
    if collection is not None and (
        collection.blocking_diagnostic_codes or collection.conflict_ids
    ):
        if final_status == "completed":
            final_status = "completed_with_warnings"
        if final_status == "completed_with_warnings":
            error_codes.extend(
                f"DOMAIN_RECONCILIATION_BLOCKED:{item}"
                for item in collection.blocking_diagnostic_codes
            )
            error_codes.extend(
                f"DOMAIN_SOURCE_CONFLICT:{item}"
                for item in collection.conflict_ids
            )
            error_codes = list(dict.fromkeys(error_codes))
    sanitized = sanitize_parsing_orchestration_result(result.to_dict())
    if collection is not None and (
        collection.candidate_ids
        or collection.source_module_ids
        or collection.expected_module_ids
        or collection.blocking_diagnostic_codes
    ):
        sanitized["candidateCollection"] = collection.to_dict()
    record_store.append_parsing_run_event(
        tenant_id=tenant_id,
        project_id=document.project_id,
        run_id=run_id,
        actor_id=actor_id,
        execution_status=final_status,
        error_codes=error_codes,
        orchestration_artifact=sanitized,
    )
    candidate_set_seal = (
        seal_collected_candidate_set(
            collection=collection,
            record_store=record_store,
            tenant_id=tenant_id,
            project_id=document.project_id,
            parsing_run_id=run_id,
            actor_id=actor_id,
        )
        if collection is not None
        and final_status in {"completed", "completed_with_warnings"}
        else None
    )
    final_run = record_store.get_parsing_run(tenant_id, document.project_id, run_id)
    response = {
        "schemaVersion": "realytics-parser-v2-structural-run-result-v1",
        "candidateOnly": True,
        "authoritative": False,
        "replayed": False,
        "documentVersion": document_record,
        "parsingRun": final_run,
        "orchestration": sanitized,
    }
    if collection is not None and (
        collection.candidate_ids
        or collection.source_module_ids
        or collection.expected_module_ids
    ):
        response["candidateCollection"] = collection.to_dict()
    if candidate_set_seal is not None:
        response["candidateSetSeal"] = candidate_set_seal
    return response


def _document_version_id(
    project_id: str,
    document_id: str,
    archive_id: str,
    object_version: str,
) -> str:
    raw = "|".join((project_id, document_id, archive_id, object_version))
    return f"document-version:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


def _parsing_idempotency_key(
    *,
    tenant_id: str,
    project_id: str,
    document_version_id: str,
    profile_id: str,
    profile_version: str,
    plan_hash: str,
    input_hash: str,
    parser_version: str,
    ontology_version: str,
    prompt_version: str,
    model_version: str,
) -> str:
    values = (
        tenant_id,
        project_id,
        document_version_id,
        profile_id,
        profile_version,
        plan_hash,
        input_hash,
        parser_version,
        ontology_version,
        prompt_version,
        model_version,
    )
    return "parser-v2:" + hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()


def _parsing_input_hash(
    *,
    tenant_id: str,
    project_id: str,
    document_version_id: str,
    archive_id: str,
    object_version: str,
    plan_hash: str,
    parser_version: str,
    ontology_version: str,
    prompt_version: str,
    model_version: str,
    domain_input_hash: str = "",
) -> str:
    values = (
        tenant_id,
        project_id,
        document_version_id,
        archive_id,
        object_version,
        plan_hash,
        parser_version,
        ontology_version,
        prompt_version,
        model_version,
        domain_input_hash,
    )
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()


def _domain_input_hash(value: Mapping[str, Any] | None) -> str:
    if value is None:
        return hashlib.sha256(b"no-domain-input").hexdigest()
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "canonical_workbook_mapping must be deterministic JSON"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _profile_workbook_mapping(
    profile: ProjectParsingProfile,
    *,
    asserted_mapping: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    raw_pinned = profile.metadata.get("canonicalWorkbookMapping")
    pinned_hash = profile.metadata.get("canonicalWorkbookMappingHash")
    if raw_pinned is not None and not isinstance(raw_pinned, Mapping):
        raise ValueError(
            "profile metadata canonicalWorkbookMapping must be an object"
        )
    if raw_pinned is None and pinned_hash is not None:
        raise ValueError(
            "profile canonicalWorkbookMappingHash requires canonicalWorkbookMapping"
        )
    if raw_pinned is not None:
        expected_hash = _domain_input_hash(raw_pinned)
        if pinned_hash != expected_hash:
            raise ValueError(
                "profile canonicalWorkbookMappingHash does not match its immutable mapping"
            )
    if asserted_mapping is not None and raw_pinned is None:
        raise ValueError(
            "canonical_workbook_mapping must be hash-pinned in the parsing profile"
        )
    if asserted_mapping is not None and raw_pinned is not None:
        asserted_hash = _domain_input_hash(asserted_mapping)
        effective_pinned_hash = _domain_input_hash(raw_pinned)
        if asserted_hash != effective_pinned_hash:
            raise ValueError(
                "canonical_workbook_mapping does not match the hash-pinned profile mapping"
            )
    return raw_pinned


def _candidate_set_seal_for_run(
    record_store: ParserV2RecordStore,
    *,
    tenant_id: str,
    project_id: str,
    parsing_run_id: str,
) -> dict[str, Any] | None:
    seals = record_store.list_candidate_set_seals(tenant_id, project_id)
    return next(
        (
            item
            for item in seals
            if item.get("parsingRunId") == parsing_run_id
        ),
        None,
    )


def _repair_candidate_set_seal(
    record_store: ParserV2RecordStore,
    *,
    tenant_id: str,
    project_id: str,
    parsing_run_id: str,
    actor_id: str,
    run: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Idempotently repair a post-completion seal write interruption."""

    artifact = run.get("orchestrationArtifact")
    collection_wire = (
        artifact.get("candidateCollection")
        if isinstance(artifact, Mapping)
        else None
    )
    if not isinstance(collection_wire, Mapping):
        return None
    candidates = record_store.list_candidates(
        tenant_id,
        project_id,
        run_id=parsing_run_id,
    )
    candidate_ids = tuple(
        sorted(str(item["candidateId"]) for item in candidates)
    )
    expected_ids = tuple(
        sorted(str(item) for item in collection_wire.get("candidateIds") or [])
    )
    if candidate_ids != expected_ids:
        return None
    conflicts = [
        item
        for item in record_store.list_source_conflicts(tenant_id, project_id)
        if parsing_run_id in (item.get("sourceParsingRunIds") or [])
        or item.get("parsingRunId") == parsing_run_id
    ]
    conflict_ids = tuple(
        sorted(str(item["conflictId"]) for item in conflicts)
    )
    expected_conflicts = tuple(
        sorted(str(item) for item in collection_wire.get("conflictIds") or [])
    )
    if conflict_ids != expected_conflicts:
        return None
    collection = CandidateCollectionResult(
        candidate_ids=candidate_ids,
        conflict_ids=conflict_ids,
        source_module_ids=tuple(
            sorted(
                str(item)
                for item in collection_wire.get("sourceModuleIds") or []
            )
        ),
        blocking_diagnostic_codes=tuple(
            sorted(
                str(item)
                for item in collection_wire.get("blockingDiagnosticCodes") or []
            )
        ),
        expected_module_ids=tuple(
            sorted(
                str(item)
                for item in collection_wire.get("expectedModuleIds") or []
            )
        ),
        completed_module_ids=tuple(
            sorted(
                str(item)
                for item in collection_wire.get("completedModuleIds") or []
            )
        ),
        unresolved_module_ids=tuple(
            sorted(
                str(item)
                for item in collection_wire.get("unresolvedModuleIds") or []
            )
        ),
        reconciliation_source_candidate_ids=tuple(
            sorted(
                str(item)
                for item in collection_wire.get(
                    "reconciliationSourceCandidateIds"
                )
                or []
            )
        ),
        reconciliation_bound=bool(
            collection_wire.get("reconciliationBound")
        ),
    )
    return seal_collected_candidate_set(
        collection=collection,
        record_store=record_store,
        tenant_id=tenant_id,
        project_id=project_id,
        parsing_run_id=parsing_run_id,
        actor_id=actor_id,
    )
