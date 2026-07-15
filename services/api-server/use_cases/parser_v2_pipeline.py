from __future__ import annotations

import hashlib
from typing import Any, Mapping
from uuid import uuid4

from parsing_platform.adapters import ArchivedDocument
from parsing_platform.default_modules import sanitize_parsing_orchestration_result
from parsing_platform.orchestrator import (
    ParsingOrchestrator,
    ProjectParsingProfile,
    build_plan_hash,
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
        return {
            "schemaVersion": "realytics-parser-v2-structural-run-result-v1",
            "candidateOnly": True,
            "authoritative": False,
            "replayed": True,
            "documentVersion": document_record,
            "parsingRun": current_run,
            "orchestration": current_run.get("orchestrationArtifact"),
        }
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
    try:
        result = orchestrator.execute_sync(
            profile,
            {"document": document},
            context={
                "tenant_id": tenant_id,
                "project_id": document.project_id,
                "document_id": logical_document_id,
                "actor_id": actor_id,
                "parsing_run_id": run_id,
                "document_version_id": version_id,
                "candidate_only": True,
            },
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
    sanitized = sanitize_parsing_orchestration_result(result.to_dict())
    record_store.append_parsing_run_event(
        tenant_id=tenant_id,
        project_id=document.project_id,
        run_id=run_id,
        actor_id=actor_id,
        execution_status=final_status,
        error_codes=error_codes,
        orchestration_artifact=sanitized,
    )
    final_run = record_store.get_parsing_run(tenant_id, document.project_id, run_id)
    return {
        "schemaVersion": "realytics-parser-v2-structural-run-result-v1",
        "candidateOnly": True,
        "authoritative": False,
        "replayed": False,
        "documentVersion": document_record,
        "parsingRun": final_run,
        "orchestration": sanitized,
    }


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
    )
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()
