"""Evidence-first Parser V2 HTTP boundary.

The router is intentionally a sidecar to the legacy project JSON model.  It
can register parsing candidates and explicitly approved snapshots, but it never
projects parser output into a project or invokes a financial calculation.
Source bytes are accepted only after an ``archiveId`` has been resolved through
the project-scoped immutable upload archive.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field, JsonValue, field_validator, model_validator

from core.authz import authorize_request, enforce_project_tenant_scope
from parsing_platform.adapters import ArchivedDocument
from parsing_platform.contracts import (
    ApprovedInputSnapshot,
    OriginType,
    ParsedFactCandidate,
    ParsingContract,
    PresenceStatus,
    ReviewAction,
    ReviewDecision,
    SourceAuthority,
    SourceConflict,
    SourceLocator,
    generate_contract_json_schemas,
)
from parsing_platform.default_modules import (
    intake_core_profile,
)
from parsing_platform.source_review_candidates import PROFILE_ID as SOURCE_REVIEW_PROFILE_ID, PROFILE_VERSION as SOURCE_REVIEW_PROFILE_VERSION, source_review_profile
from parsing_platform.canonical_fields import canonical_field_key
from parsing_platform.domain_standards import domain_standard_catalog
from parsing_platform.document_taxonomy import (
    DOCUMENT_TAXONOMY_VERSION,
    canonical_document_types,
    document_taxonomy_catalog,
)
from parsing_platform.field_registry import field_registry_catalog
from parsing_platform.orchestrator import (
    ParsingOrchestrationError,
    ParsingOrchestrator,
    ProjectParsingProfile,
)
from parsing_platform.runtime_modules import (
    build_parser_v2_runtime_orchestrator,
)
from use_cases.parser_v2_pipeline import run_structural_parsing_use_case
from use_cases.parser_v2_profiles import (
    ProjectParsingProfileConflict,
    get_project_parsing_profile,
    list_project_parsing_profiles,
    save_project_parsing_profile,
)
from use_cases.parser_v2_records import (
    ImmutableRecordConflict,
    ParserV2ApprovalError,
    ParserV2RecordError,
    ParserV2RecordStore,
    ParserV2ReferenceError,
    approved_input_snapshot_wire_payload,
    validate_source_locator_attestations,
)
from use_cases.source_documents import get_source_document_download_use_case
from storage import list_calculation_run_records


ProjectLookup = Callable[[str], dict[str, Any] | None]
RepositoryFactory = Callable[[], Any]
RecordStoreFactory = Callable[[], ParserV2RecordStore]
OrchestratorFactory = Callable[[], ParsingOrchestrator]
AuditRecorder = Callable[..., None]
ProfileUpsert = Callable[[str, dict[str, Any]], dict[str, Any]]
ProfileList = Callable[..., list[dict[str, Any]]]
CalculationRunList = Callable[..., list[dict[str, Any]]]


class ParsingStageProfileInput(ParsingContract):
    stage_id: str = Field(..., min_length=1)
    module_refs: list[str] = Field(..., min_length=1)
    failure_policy: Literal["fail_closed", "preserve_partial"] | None = None


class ProjectParsingProfileInput(ParsingContract):
    profile_id: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    document_taxonomy_version: str = Field(..., min_length=1)
    selected_modules: list[str] = Field(default_factory=list)
    excluded_modules: list[str] = Field(default_factory=list)
    ordered_stages: list[ParsingStageProfileInput] = Field(default_factory=list)
    failure_policy: Literal["fail_closed", "preserve_partial"] = "fail_closed"
    project_id: str | None = Field(None, min_length=1)
    document_types: list[str] = Field(..., min_length=1)
    asset_types: list[str] = Field(default_factory=list)
    deal_types: list[str] = Field(default_factory=list)
    capability_bindings: dict[str, list[str]] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("document_taxonomy_version")
    @classmethod
    def require_current_document_taxonomy(cls, value: str) -> str:
        if value != DOCUMENT_TAXONOMY_VERSION:
            raise ValueError(
                "documentTaxonomyVersion must equal "
                f"{DOCUMENT_TAXONOMY_VERSION}"
            )
        return value

    @field_validator("document_types")
    @classmethod
    def require_canonical_document_types(
        cls,
        value: list[str],
    ) -> list[str]:
        try:
            return list(canonical_document_types(value))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class StructuralParsingRunInput(ParsingContract):
    archive_id: str = Field(..., pattern=r"^[0-9a-fA-F]{64}$")
    profile_id: str | None = Field(None, min_length=1)
    profile_version: str | None = Field(None, min_length=1)
    document_id: str | None = Field(None, min_length=1)
    document_version_id: str | None = Field(None, min_length=1)
    version_number: int = Field(1, ge=1)
    predecessor_version_id: str | None = Field(None, min_length=1)
    parsing_run_id: str | None = Field(None, min_length=1)
    idempotency_key: str | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def require_complete_profile_reference(self) -> "StructuralParsingRunInput":
        if bool(self.profile_id) != bool(self.profile_version):
            raise ValueError("profileId and profileVersion must be supplied together")
        return self


class ParsedFactCandidateInput(ParsingContract):
    candidate_id: str = Field(..., min_length=1)
    document_version_id: str = Field(..., min_length=1)
    field_key: str = Field(..., min_length=1)
    entity_id: str | None = Field(None, min_length=1)
    raw_value: JsonValue
    normalized_value: JsonValue
    unit: str | None = Field(None, min_length=1)
    origin_type: OriginType
    presence_status: PresenceStatus = PresenceStatus.PRESENT
    confidence: float | None = Field(None, ge=0, le=1)
    authority: SourceAuthority = SourceAuthority.UNKNOWN
    effective_date: date | None = None
    as_of_date: date | None = None
    evidence: list[SourceLocator] = Field(default_factory=list, max_length=100)
    validation_codes: list[str] = Field(default_factory=list)
    formula_id: str | None = Field(None, min_length=1)
    formula_version: str | None = Field(None, min_length=1)
    input_fact_ids: list[str] = Field(default_factory=list)
    calculation_run_id: str | None = Field(None, min_length=1)
    calculation_engine_version: str | None = Field(None, min_length=1)
    calculation_output_path: str | None = Field(None, min_length=1, pattern=r"^/")
    calculation_output_hash: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    input_snapshot_hash: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    supersedes_candidate_id: str | None = Field(None, min_length=1)
    target_modules: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class SourceConflictInput(ParsingContract):
    conflict_id: str = Field(..., min_length=1)
    field_key: str = Field(..., min_length=1)
    candidate_ids: list[str] = Field(..., min_length=2)
    conflict_type: str = Field(..., min_length=1)
    severity: Literal["low", "medium", "high", "critical"] = "high"
    notes: str | None = None


class CandidateSetSealInput(ParsingContract):
    expected_candidate_ids: list[str] = Field(..., min_length=1)
    expected_conflict_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_expected_ids(self) -> "CandidateSetSealInput":
        if len(set(self.expected_candidate_ids)) != len(self.expected_candidate_ids):
            raise ValueError("expectedCandidateIds must not contain duplicates")
        if len(set(self.expected_conflict_ids)) != len(self.expected_conflict_ids):
            raise ValueError("expectedConflictIds must not contain duplicates")
        return self


class ReviewDecisionInput(ParsingContract):
    review_decision_id: str | None = Field(None, min_length=1)
    expected_decision_sequence: int = Field(..., ge=0)
    action: ReviewAction
    reason: str = Field(..., min_length=1)
    conflict_ids: list[str] = Field(default_factory=list)


class ConflictResolutionInput(ParsingContract):
    review_decision_id: str = Field(..., min_length=1)
    expected_decision_sequence: int = Field(..., ge=1)
    resolution_status: Literal["resolved", "waived"] = "resolved"
    notes: str | None = None


class CanonicalSelectionInput(ParsingContract):
    selection_id: str = Field(..., min_length=1)
    candidate_id: str = Field(..., min_length=1)
    review_decision_id: str = Field(..., min_length=1)
    expected_decision_sequence: int = Field(..., ge=1)
    selection_version: int = Field(1, ge=1)
    supersedes_selection_id: str | None = Field(None, min_length=1)


class ApprovedInputSnapshotInput(ParsingContract):
    snapshot_id: str = Field(..., min_length=1)
    selection_ids: list[str] = Field(..., min_length=1)
    purpose: str = Field(..., min_length=1)
    expected_selection_versions: dict[str, int] = Field(..., min_length=1)
    supersedes_snapshot_id: str | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def validate_expected_selection_versions(self) -> "ApprovedInputSnapshotInput":
        if len(set(self.selection_ids)) != len(self.selection_ids):
            raise ValueError("selectionIds must not contain duplicates")
        if any(version < 1 for version in self.expected_selection_versions.values()):
            raise ValueError("expectedSelectionVersions values must be at least 1")
        return self


def build_project_parser_v2_router(
    *,
    project_lookup: ProjectLookup,
    source_document_repository_factory: RepositoryFactory,
    upload_archive_repository_factory: RepositoryFactory,
    audit_recorder: AuditRecorder,
    record_store_factory: RecordStoreFactory | None = None,
    orchestrator_factory: OrchestratorFactory | None = None,
    profile_upsert_fn: ProfileUpsert | None = None,
    profile_list_fn: ProfileList | None = None,
    calculation_run_list_fn: CalculationRunList = list_calculation_run_records,
    review_pilot_enabled: bool | None = None,
) -> APIRouter:
    """Build a Parser V2 router with replaceable persistence seams for tests/DBs."""

    router = APIRouter(tags=["parser-v2"])
    make_store = record_store_factory or ParserV2RecordStore
    make_orchestrator = (
        orchestrator_factory or build_parser_v2_runtime_orchestrator
    )

    def is_review_pilot_enabled() -> bool:
        if review_pilot_enabled is not None:
            return review_pilot_enabled
        return os.environ.get("REALYTICS_PARSER_V2_REVIEW_PILOT_ENABLED", "false").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def require_review_pilot(authz: dict[str, Any]) -> None:
        if not is_review_pilot_enabled():
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "parser_v2_review_pilot_disabled",
                    "message": (
                        "Review, conflict resolution, canonical selection, and approved "
                        "snapshot routes are disabled outside the controlled Phase 2 pilot."
                    ),
                },
            )
        if str(authz.get("rbacMode") or "").lower() != "strict":
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "parser_v2_review_pilot_requires_strict_rbac",
                    "message": "The approval pilot cannot run in permissive RBAC mode.",
                },
            )
        if authz.get("authenticated") is not True:
            raise HTTPException(
                status_code=401,
                detail={"error": "parser_v2_review_pilot_authentication_required"},
            )
        user = authz.get("user") if isinstance(authz.get("user"), dict) else {}
        actor_id = str(user.get("userId") or "")
        tenant_id = str(user.get("tenantId") or "")
        if not actor_id or actor_id == "anonymous" or not tenant_id:
            raise HTTPException(
                status_code=401,
                detail={"error": "parser_v2_review_pilot_human_identity_required"},
            )
        if str(user.get("role") or "") not in {"admin", "reviewer"}:
            raise HTTPException(
                status_code=403,
                detail={"error": "parser_v2_review_pilot_reviewer_role_required"},
            )
        metadata = user.get("metadata") if isinstance(user.get("metadata"), dict) else {}
        if str(metadata.get("principalType") or "unknown").lower() != "human":
            raise HTTPException(
                status_code=403,
                detail={"error": "parser_v2_review_pilot_service_identity_forbidden"},
            )
        if authz.get("authSource") != "bearer_token":
            raise HTTPException(
                status_code=401,
                detail={"error": "parser_v2_review_pilot_verified_bearer_required"},
            )
        identity_provider = (
            authz.get("identityProvider")
            if isinstance(authz.get("identityProvider"), dict)
            else {}
        )
        if str(identity_provider.get("mode") or "").lower() not in {
            "oidc",
            "jwks",
            "rs256",
        }:
            raise HTTPException(
                status_code=503,
                detail={"error": "parser_v2_review_pilot_oidc_required"},
            )
        allowed_tenants = _configured_allowlist(
            "REALYTICS_PARSER_V2_REVIEW_PILOT_TENANT_ALLOWLIST"
        )
        allowed_users = _configured_allowlist(
            "REALYTICS_PARSER_V2_REVIEW_PILOT_USER_ALLOWLIST"
        )
        if not allowed_tenants or not allowed_users:
            raise HTTPException(
                status_code=503,
                detail={"error": "parser_v2_review_pilot_allowlist_not_configured"},
            )
        if tenant_id not in allowed_tenants or actor_id not in allowed_users:
            raise HTTPException(
                status_code=403,
                detail={"error": "parser_v2_review_pilot_identity_not_allowlisted"},
            )
        require_mfa = os.environ.get(
            "REALYTICS_PARSER_V2_REVIEW_PILOT_REQUIRE_MFA", "true"
        ).lower() in {"1", "true", "yes", "on"}
        assurance = (
            authz.get("identityAssurance")
            if isinstance(authz.get("identityAssurance"), dict)
            else {}
        )
        if require_mfa and not (
            assurance.get("source") == "verified_bearer_claims"
            and assurance.get("mfaVerified") is True
        ):
            raise HTTPException(
                status_code=403,
                detail={"error": "parser_v2_review_pilot_mfa_required"},
            )

    def require_project(project_id: str) -> dict[str, Any]:
        project = project_lookup(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    def authorize_project(
        request: Request,
        project_id: str,
        permission: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        project = require_project(project_id)
        enforce_project_tenant_scope(request, project)
        authz = authorize_request(request, permission)
        user = authz.get("user") or {}
        metadata = project.get("_meta") if isinstance(project.get("_meta"), dict) else {}
        project_tenant = metadata.get("tenantId") or metadata.get("tenant_id")
        actor_tenant = user.get("tenantId")
        if project_tenant and actor_tenant and str(project_tenant) != str(actor_tenant):
            raise HTTPException(
                status_code=403,
                detail={"error": "tenant_scope_mismatch", "scope": "parser_v2"},
            )
        tenant_id = str(actor_tenant or project_tenant or "default")
        actor_id = str(user.get("userId") or "anonymous")
        return project, authz, tenant_id, actor_id

    def profile_list_kwargs() -> dict[str, Any]:
        return {"list_fn": profile_list_fn} if profile_list_fn is not None else {}

    def profile_write_kwargs() -> dict[str, Any]:
        kwargs = profile_list_kwargs()
        if profile_upsert_fn is not None:
            kwargs["upsert_fn"] = profile_upsert_fn
        return kwargs

    def translate_domain_error(exc: Exception) -> None:
        if isinstance(
            exc,
            (
                ProjectParsingProfileConflict,
                ImmutableRecordConflict,
                ParserV2ApprovalError,
                ParserV2ReferenceError,
            ),
        ):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if isinstance(exc, LookupError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc, (ParserV2RecordError, ParsingOrchestrationError, ValueError)):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise exc

    @router.get("/parser-v2/contracts")
    def get_parser_v2_contracts(request: Request):
        authz = authorize_request(request, "tool:read")
        return {
            "schemaVersion": "realytics-parser-v2-contract-catalog-v1",
            "candidateOnly": True,
            "authoritativeProjectionAllowed": False,
            "reviewPilotEnabled": is_review_pilot_enabled(),
            "contracts": generate_contract_json_schemas(),
            "authorization": authz.get("authorization"),
        }

    @router.get("/parser-v2/catalog")
    def get_parser_v2_catalog(request: Request):
        authz = authorize_request(request, "tool:read")
        orchestrator = make_orchestrator()
        default_profile = ProjectParsingProfileInput.model_validate(
            intake_core_profile().to_dict()
        )
        modules = []
        for manifest in orchestrator.registry.snapshot():
            modules.append(
                {
                    "moduleId": manifest["module_id"],
                    "version": manifest["version"],
                    "capabilities": manifest["capabilities"],
                    "supportedDocumentTypes": manifest["supported_document_types"],
                    "documentTaxonomyVersion": manifest[
                        "document_taxonomy_version"
                    ],
                    "documentTypeScope": manifest["document_type_scope"],
                    "supportedAssetTypes": manifest["supported_asset_types"],
                    "supportedDealTypes": manifest["supported_deal_types"],
                    "requires": manifest["requires"],
                    "provides": manifest["provides"],
                    "conditionalProvides": manifest["conditional_provides"],
                    "executionClass": manifest["execution_class"],
                    "inputSchema": manifest["input_schema"],
                    "outputSchema": manifest["output_schema"],
                    "requiredLocatorPrecision": manifest[
                        "required_locator_precision"
                    ],
                    "dataClassification": manifest["data_classification"],
                    "maxOutputBytes": manifest["max_output_bytes"],
                    "active": manifest["active"],
                    "metadata": manifest["metadata"],
                }
            )
        return {
            "schemaVersion": "realytics-parser-v2-module-catalog-v2",
            "candidateOnly": True,
            "authoritativeProjectionAllowed": False,
            "reviewPilotEnabled": is_review_pilot_enabled(),
            "executionMode": "inline_controlled_implementation",
            "externalWorkerDispatchReady": False,
            "modules": modules,
            "documentTaxonomyVersion": DOCUMENT_TAXONOMY_VERSION,
            "documentTaxonomy": document_taxonomy_catalog(),
            "schemaContracts": orchestrator.schema_registry.snapshot(),
            "defaultProfile": default_profile.model_dump(mode="json", by_alias=True),
            "domainStandard": domain_standard_catalog(),
            "fieldRegistry": field_registry_catalog(),
            "authorization": authz.get("authorization"),
        }

    @router.get("/projects/{project_id}/parsing-v2/profiles")
    def list_profiles(project_id: str, request: Request):
        _, _, tenant_id, _ = authorize_project(request, project_id, "tool:read")
        return {
            "schemaVersion": "realytics-project-parsing-profile-list-v1",
            "projectId": project_id,
            "items": list_project_parsing_profiles(
                project_id,
                tenant_id,
                **profile_list_kwargs(),
            ),
        }

    @router.post(
        "/projects/{project_id}/parsing-v2/profiles",
        status_code=status.HTTP_201_CREATED,
    )
    def save_profile(
        project_id: str,
        request: Request,
        input_data: ProjectParsingProfileInput,
    ):
        _, authz, tenant_id, actor_id = authorize_project(
            request, project_id, "agent:operate"
        )
        try:
            record = save_project_parsing_profile(
                project_id,
                tenant_id,
                actor_id,
                input_data.model_dump(mode="python", by_alias=True),
                make_orchestrator(),
                **profile_write_kwargs(),
            )
        except Exception as exc:
            translate_domain_error(exc)
        audit_recorder(
            request,
            event_type="parser_v2_profile_saved",
            project_id=project_id,
            metadata={
                "profileId": input_data.profile_id,
                "profileVersion": input_data.version,
                "authorization": authz.get("authorization"),
            },
        )
        return record

    @router.get("/projects/{project_id}/parsing-v2/runs")
    def list_parsing_runs(project_id: str, request: Request):
        _, _, tenant_id, _ = authorize_project(request, project_id, "evidence:read")
        return {
            "schemaVersion": "realytics-parser-v2-run-list-v1",
            "candidateOnly": True,
            "projectId": project_id,
            "items": make_store().list_parsing_runs(tenant_id, project_id),
        }

    @router.get(
        "/projects/{project_id}/parsing-v2/evidence/{evidence_id}"
    )
    def resolve_parser_v2_evidence(
        project_id: str, evidence_id: str, request: Request
    ):
        _, _, tenant_id, _ = authorize_project(
            request, project_id, "evidence:read"
        )
        try:
            return make_store().resolve_evidence_locator(
                tenant_id, project_id, evidence_id
            )
        except ParserV2ReferenceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            translate_domain_error(exc)

    @router.post(
        "/projects/{project_id}/parsing-v2/runs",
        status_code=status.HTTP_201_CREATED,
    )
    def run_structural_parsing(
        project_id: str,
        request: Request,
        input_data: StructuralParsingRunInput,
    ):
        _, authz, tenant_id, actor_id = authorize_project(
            request, project_id, "project:analyze"
        )
        download = get_source_document_download_use_case(
            source_document_repository_factory(),
            upload_archive_repository_factory(),
            project_id,
            input_data.archive_id.lower(),
        )
        if not download:
            raise HTTPException(status_code=404, detail="Source document not found")
        try:
            if download.get("path"):
                # The path is supplied by the archive repository after its own
                # project-scope and hash checks; no request path is accepted.
                content = Path(download["path"]).read_bytes()
            else:
                content = bytes(download.get("content") or b"")
            source_document = (
                download.get("document")
                if isinstance(download.get("document"), dict)
                else {}
            )
            source_authority = SourceAuthority(
                source_document.get("sourceAuthority") or SourceAuthority.UNKNOWN.value
            )
            object_version = str(
                download.get("archiveObjectVersion")
                or download.get("archiveObjectKey")
                or input_data.archive_id
            )
            document = ArchivedDocument(
                project_id=project_id,
                tenant_id=tenant_id,
                archive_id=input_data.archive_id.lower(),
                filename=str(download.get("filename") or input_data.archive_id),
                content_type=str(
                    download.get("mediaType") or "application/octet-stream"
                ),
                content=content,
                object_key=str(download.get("archiveObjectKey") or "") or None,
                object_version=object_version,
            )
            orchestrator = make_orchestrator()
            if input_data.profile_id == SOURCE_REVIEW_PROFILE_ID and input_data.profile_version == SOURCE_REVIEW_PROFILE_VERSION:
                builtin = save_project_parsing_profile(
                    project_id, tenant_id, actor_id, source_review_profile(project_id=project_id), orchestrator,
                    **profile_write_kwargs(),
                )
                profile = ProjectParsingProfile.from_value(builtin["profile"])
            elif input_data.profile_id:
                profile = get_project_parsing_profile(
                    project_id,
                    tenant_id,
                    str(input_data.profile_id),
                    str(input_data.profile_version),
                    **profile_list_kwargs(),
                )
            else:
                default_profile_record = save_project_parsing_profile(
                    project_id,
                    tenant_id,
                    actor_id,
                    intake_core_profile(project_id=project_id),
                    orchestrator,
                    **profile_write_kwargs(),
                )
                profile = ProjectParsingProfile.from_value(
                    default_profile_record["profile"]
                )
            result = run_structural_parsing_use_case(
                document=document,
                actor_id=actor_id,
                profile=profile,
                orchestrator=orchestrator,
                record_store=make_store(),
                document_id=input_data.document_id,
                document_version_id=input_data.document_version_id,
                object_version=object_version,
                version_number=input_data.version_number,
                source_authority=source_authority.value,
                predecessor_version_id=input_data.predecessor_version_id,
                parsing_run_id=input_data.parsing_run_id,
                idempotency_key=input_data.idempotency_key,
            )
            result["executionMode"] = "inline_controlled_implementation"
            result["externalWorkerDispatched"] = False
        except Exception as exc:
            translate_domain_error(exc)
        run = result["parsingRun"]
        audit_recorder(
            request,
            event_type="parser_v2_structural_run_completed",
            project_id=project_id,
            run_id=run.get("parsingRunId"),
            metadata={
                "archiveId": input_data.archive_id.lower(),
                "documentVersionId": run.get("documentVersionId"),
                "executionStatus": run.get("executionStatus"),
                "approvalStatus": run.get("approvalStatus"),
                "candidateOnly": True,
                "authorization": authz.get("authorization"),
            },
        )
        return result

    @router.get("/projects/{project_id}/parsing-v2/runs/{run_id}")
    def get_parsing_run_package(project_id: str, run_id: str, request: Request):
        _, _, tenant_id, _ = authorize_project(request, project_id, "evidence:read")
        store = make_store()
        try:
            run = store.get_parsing_run(tenant_id, project_id, run_id)
            document = store.get_document_version(
                tenant_id, project_id, str(run["documentVersionId"])
            )
        except Exception as exc:
            translate_domain_error(exc)
        candidates = store.list_candidates(tenant_id, project_id, run_id=run_id)
        candidate_ids = {str(item["candidateId"]) for item in candidates}
        conflicts = [
            item
            for item in store.list_source_conflicts(tenant_id, project_id)
            if run_id in item.get("sourceParsingRunIds", [])
            or item.get("parsingRunId") == run_id
        ]
        reviews = [
            item
            for item in store.list_review_decisions(tenant_id, project_id)
            if item.get("candidateId") in candidate_ids
        ]
        selections = [
            item
            for item in store.list_canonical_selections(tenant_id, project_id)
            if item.get("candidateId") in candidate_ids
        ]
        selection_ids = {str(item["selectionId"]) for item in selections}
        snapshots = [
            item
            for item in store.list_approved_input_snapshots(tenant_id, project_id)
            if selection_ids.intersection(str(value) for value in item.get("selectionIds", []))
        ]
        public_snapshots = [
            ApprovedInputSnapshot.model_validate(
                approved_input_snapshot_wire_payload(item)
            ).model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in snapshots
        ]
        candidate_set_seal = next(
            (
                item
                for item in store.list_candidate_set_seals(tenant_id, project_id)
                if item.get("parsingRunId") == run_id
            ),
            None,
        )
        return {
            "schemaVersion": "realytics-parser-v2-run-package-v2",
            "candidateOnly": True,
            "authoritative": False,
            "projectProjectionMutated": False,
            "projectId": project_id,
            "parsingRun": run,
            "documentVersion": document,
            "candidates": candidates,
            "conflicts": conflicts,
            "candidateSetSeal": candidate_set_seal,
            "reviewDecisions": reviews,
            "canonicalSelections": selections,
            "approvedInputSnapshots": public_snapshots,
        }

    @router.post(
        "/projects/{project_id}/parsing-v2/runs/{run_id}/candidates",
        status_code=status.HTTP_201_CREATED,
    )
    def submit_candidate(
        project_id: str,
        run_id: str,
        request: Request,
        input_data: ParsedFactCandidateInput,
    ):
        _, authz, tenant_id, actor_id = authorize_project(
            request, project_id, "agent:operate"
        )
        store = make_store()
        try:
            run = store.get_parsing_run(tenant_id, project_id, run_id)
            if run.get("executionStatus") not in {
                "running",
                "completed",
                "completed_with_warnings",
            }:
                raise ParserV2RecordError(
                    "candidate submission requires a running or successfully completed parsing run"
                )
            if run.get("documentVersionId") != input_data.document_version_id:
                raise ParserV2ReferenceError(
                    "candidate documentVersionId does not match the parsing run"
                )
            document = store.get_document_version(
                tenant_id, project_id, input_data.document_version_id
            )
            if input_data.authority.value != document.get("sourceAuthority"):
                raise ParserV2ReferenceError(
                    "candidate authority must match the archived document sourceAuthority"
                )
            _validate_candidate_evidence_identity(
                input_data.evidence,
                document_version_id=input_data.document_version_id,
                source_hash=str(document["sha256"]),
                orchestration_artifact=run.get("orchestrationArtifact"),
            )
            if input_data.origin_type == OriginType.DERIVED:
                _validate_derived_candidate_lineage(
                    store,
                    calculation_run_list_fn,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    candidate_id=input_data.candidate_id,
                    input_fact_ids=input_data.input_fact_ids,
                    calculation_run_id=input_data.calculation_run_id,
                )
            candidate = ParsedFactCandidate(
                candidateId=input_data.candidate_id,
                tenantId=tenant_id,
                projectId=project_id,
                parsingRunId=run_id,
                documentVersionId=input_data.document_version_id,
                fieldKey=input_data.field_key,
                entityId=input_data.entity_id,
                rawValue=input_data.raw_value,
                normalizedValue=input_data.normalized_value,
                unit=input_data.unit,
                originType=input_data.origin_type,
                presenceStatus=input_data.presence_status,
                confidence=input_data.confidence,
                authority=input_data.authority,
                effectiveDate=input_data.effective_date,
                asOfDate=input_data.as_of_date,
                evidence=input_data.evidence,
                validationCodes=input_data.validation_codes,
                parserVersion=run["parserVersion"],
                ontologyVersion=run["ontologyVersion"],
                promptVersion=run["promptVersion"],
                modelVersion=run["modelVersion"],
                formulaId=input_data.formula_id,
                formulaVersion=input_data.formula_version,
                inputFactIds=input_data.input_fact_ids,
                calculationRunId=input_data.calculation_run_id,
                calculationEngineVersion=input_data.calculation_engine_version,
                calculationOutputPath=input_data.calculation_output_path,
                calculationOutputHash=input_data.calculation_output_hash,
                inputSnapshotHash=input_data.input_snapshot_hash,
                supersedesCandidateId=input_data.supersedes_candidate_id,
                createdAt=datetime.now(timezone.utc),
                metadata=input_data.metadata,
            )
            record = store.create_candidate(
                tenant_id=tenant_id,
                project_id=project_id,
                run_id=run_id,
                document_version_id=input_data.document_version_id,
                candidate_id=input_data.candidate_id,
                actor_id=actor_id,
                canonical_field=input_data.field_key,
                value=candidate.normalized_value,
                raw_value=candidate.raw_value,
                normalized_value=candidate.normalized_value,
                evidence=[
                    item.model_dump(mode="json", by_alias=True)
                    for item in candidate.evidence
                ],
                unit=candidate.unit,
                origin_type=candidate.origin_type.value,
                presence_status=candidate.presence_status.value,
                confidence=candidate.confidence,
                target_modules=input_data.target_modules,
                entity_id=candidate.entity_id,
                authority=candidate.authority.value,
                validation_codes=candidate.validation_codes,
                formula_id=candidate.formula_id,
                formula_version=candidate.formula_version,
                input_fact_ids=candidate.input_fact_ids,
                calculation_run_id=candidate.calculation_run_id,
                calculation_engine_version=candidate.calculation_engine_version,
                calculation_output_path=candidate.calculation_output_path,
                calculation_output_hash=candidate.calculation_output_hash,
                input_snapshot_hash=candidate.input_snapshot_hash,
                supersedes_candidate_id=candidate.supersedes_candidate_id,
                effective_date=candidate.effective_date,
                as_of_date=candidate.as_of_date,
                metadata=candidate.metadata,
            )
        except Exception as exc:
            translate_domain_error(exc)
        audit_recorder(
            request,
            event_type="parser_v2_candidate_submitted",
            project_id=project_id,
            run_id=run_id,
            metadata={
                "candidateId": input_data.candidate_id,
                "documentVersionId": input_data.document_version_id,
                "fieldKey": input_data.field_key,
                "evidenceLocatorCount": len(input_data.evidence),
                "candidateOnly": True,
                "authorization": authz.get("authorization"),
            },
        )
        return record

    @router.post(
        "/projects/{project_id}/parsing-v2/runs/{run_id}/seal",
        status_code=status.HTTP_201_CREATED,
    )
    def seal_candidate_set(
        project_id: str,
        run_id: str,
        request: Request,
        input_data: CandidateSetSealInput,
    ):
        _, authz, tenant_id, actor_id = authorize_project(
            request, project_id, "agent:operate"
        )
        store = make_store()
        try:
            record = store.seal_candidate_set(
                tenant_id=tenant_id,
                project_id=project_id,
                run_id=run_id,
                actor_id=actor_id,
                expected_candidate_ids=input_data.expected_candidate_ids,
                expected_conflict_ids=input_data.expected_conflict_ids,
            )
        except Exception as exc:
            translate_domain_error(exc)
        audit_recorder(
            request,
            event_type="parser_v2_candidate_set_sealed",
            project_id=project_id,
            run_id=run_id,
            metadata={
                "candidateCount": record.get("candidateCount"),
                "conflictCount": record.get("conflictCount"),
                "candidateSetHash": record.get("candidateSetHash"),
                "authorization": authz.get("authorization"),
            },
        )
        return record

    @router.post(
        "/projects/{project_id}/parsing-v2/conflicts",
        status_code=status.HTTP_201_CREATED,
    )
    @router.post(
        "/projects/{project_id}/parsing-v2/runs/{run_id}/conflicts",
        status_code=status.HTTP_201_CREATED,
    )
    def create_conflict(
        project_id: str,
        request: Request,
        input_data: SourceConflictInput,
        run_id: str | None = None,
    ):
        _, authz, tenant_id, actor_id = authorize_project(
            request, project_id, "agent:operate"
        )
        store = make_store()
        try:
            if run_id is not None:
                store.get_parsing_run(tenant_id, project_id, run_id)
            candidates = [
                store.get_candidate(tenant_id, project_id, candidate_id)
                for candidate_id in input_data.candidate_ids
            ]
            source_run_ids = sorted(
                {str(item.get("parsingRunId")) for item in candidates}
            )
            if run_id is not None and run_id not in source_run_ids:
                raise ParserV2ReferenceError(
                    "the route parsing run must be one of the conflict source runs"
                )
            conflict_field_key = canonical_field_key(input_data.field_key)
            if any(
                canonical_field_key(str(item.get("fieldKey") or ""))
                != conflict_field_key
                for item in candidates
            ):
                raise ParserV2ReferenceError(
                    "all conflict candidates must target fieldKey"
                )
            entity_id = candidates[0].get("entityId")
            if any(item.get("entityId") != entity_id for item in candidates):
                raise ParserV2ReferenceError(
                    "all conflict candidates must target the same entityId"
                )
            SourceConflict(
                conflictId=input_data.conflict_id,
                tenantId=tenant_id,
                projectId=project_id,
                parsingRunId=run_id,
                sourceParsingRunIds=source_run_ids,
                fieldKey=conflict_field_key,
                entityId=entity_id,
                candidateIds=input_data.candidate_ids,
                conflictType=input_data.conflict_type,
                conflictStatus="open",
                detectedAt=datetime.now(timezone.utc),
                notes=input_data.notes,
            )
            record = store.create_source_conflict(
                tenant_id=tenant_id,
                project_id=project_id,
                conflict_id=input_data.conflict_id,
                actor_id=actor_id,
                canonical_field=conflict_field_key,
                candidate_ids=input_data.candidate_ids,
                conflict_code=input_data.conflict_type,
                severity=input_data.severity,
                notes=input_data.notes,
                detection_run_id=run_id,
            )
        except Exception as exc:
            translate_domain_error(exc)
        audit_recorder(
            request,
            event_type="parser_v2_conflict_created",
            project_id=project_id,
            run_id=run_id,
            metadata={
                "conflictId": input_data.conflict_id,
                "fieldKey": conflict_field_key,
                "candidateIds": input_data.candidate_ids,
                "sourceParsingRunIds": source_run_ids,
                "authorization": authz.get("authorization"),
            },
        )
        return record

    @router.post(
        "/projects/{project_id}/parsing-v2/candidates/{candidate_id}/reviews",
        status_code=status.HTTP_201_CREATED,
    )
    def review_candidate(
        project_id: str,
        candidate_id: str,
        request: Request,
        input_data: ReviewDecisionInput,
    ):
        _, authz, tenant_id, actor_id = authorize_project(
            request, project_id, "risk:review"
        )
        require_review_pilot(authz)
        store = make_store()
        try:
            candidate = store.get_candidate(tenant_id, project_id, candidate_id)
            run = store.get_parsing_run(
                tenant_id, project_id, str(candidate["parsingRunId"])
            )
            _require_successful_run_for_approval(run)
            existing_decisions = store.list_review_decisions(
                tenant_id, project_id, candidate_id=candidate_id
            )
            existing_same_id = next(
                (
                    item
                    for item in existing_decisions
                    if item.get("reviewDecisionId") == input_data.review_decision_id
                ),
                None,
            )
            if (
                len(existing_decisions) != input_data.expected_decision_sequence
                and existing_same_id is None
            ):
                raise ImmutableRecordConflict(
                    "review decision sequence changed; reload the candidate review package"
                )
            review_status, approval_status = {
                ReviewAction.APPROVE: ("approved", "approved"),
                ReviewAction.REJECT: ("rejected", "rejected"),
                ReviewAction.REQUEST_REVISION: ("needs_revision", "pending"),
            }[input_data.action]
            ReviewDecision(
                reviewDecisionId=input_data.review_decision_id or "server-generated",
                tenantId=tenant_id,
                projectId=project_id,
                candidateId=candidate_id,
                conflictIds=input_data.conflict_ids,
                action=input_data.action,
                resultingReviewStatus=review_status,
                resultingApprovalStatus=approval_status,
                reviewerId=actor_id,
                decidedAt=datetime.now(timezone.utc),
                reason=input_data.reason,
            )
            record = store.append_review_decision(
                tenant_id=tenant_id,
                project_id=project_id,
                candidate_id=candidate_id,
                actor_id=actor_id,
                action=input_data.action.value,
                reason=input_data.reason,
                decision_id=input_data.review_decision_id,
                conflict_ids=input_data.conflict_ids,
                expected_decision_sequence=input_data.expected_decision_sequence,
            )
        except Exception as exc:
            translate_domain_error(exc)
        audit_recorder(
            request,
            event_type="parser_v2_review_decision_recorded",
            project_id=project_id,
            run_id=candidate.get("parsingRunId"),
            metadata={
                "candidateId": candidate_id,
                "reviewDecisionId": record.get("reviewDecisionId"),
                "action": input_data.action.value,
                "conflictIds": input_data.conflict_ids,
                "authorization": authz.get("authorization"),
            },
        )
        return record

    @router.post(
        "/projects/{project_id}/parsing-v2/conflicts/{conflict_id}/resolve",
        status_code=status.HTTP_201_CREATED,
    )
    def resolve_conflict(
        project_id: str,
        conflict_id: str,
        request: Request,
        input_data: ConflictResolutionInput,
    ):
        _, authz, tenant_id, actor_id = authorize_project(
            request, project_id, "risk:review"
        )
        require_review_pilot(authz)
        store = make_store()
        try:
            conflict = store.get_source_conflict(tenant_id, project_id, conflict_id)
            decision = store.get_review_decision(
                tenant_id, project_id, input_data.review_decision_id
            )
            if int(decision.get("decisionSequence") or 0) != input_data.expected_decision_sequence:
                raise ImmutableRecordConflict(
                    "conflict resolution review sequence does not match the expected sequence"
                )
            if decision.get("candidateId") not in conflict.get("candidateIds", []):
                raise ParserV2ReferenceError(
                    "conflict resolution decision belongs to a non-member candidate"
                )
            if input_data.resolution_status == "resolved" and decision.get("action") != "approve":
                raise ParserV2ApprovalError(
                    "resolved conflict requires an approved winning-candidate decision"
                )
            record = store.append_source_conflict_resolution(
                tenant_id=tenant_id,
                project_id=project_id,
                conflict_id=conflict_id,
                actor_id=actor_id,
                review_decision_id=input_data.review_decision_id,
                expected_decision_sequence=input_data.expected_decision_sequence,
                resolution_status=input_data.resolution_status,
                notes=input_data.notes,
            )
        except Exception as exc:
            translate_domain_error(exc)
        audit_recorder(
            request,
            event_type="parser_v2_conflict_resolved",
            project_id=project_id,
            run_id=conflict.get("parsingRunId"),
            metadata={
                "conflictId": conflict_id,
                "reviewDecisionId": input_data.review_decision_id,
                "resolutionStatus": input_data.resolution_status,
                "authorization": authz.get("authorization"),
            },
        )
        return record

    @router.post(
        "/projects/{project_id}/parsing-v2/canonical-selections",
        status_code=status.HTTP_201_CREATED,
    )
    def create_canonical_selection(
        project_id: str,
        request: Request,
        input_data: CanonicalSelectionInput,
    ):
        _, authz, tenant_id, actor_id = authorize_project(
            request, project_id, "assumption:override"
        )
        require_review_pilot(authz)
        store = make_store()
        try:
            candidate = store.get_candidate(
                tenant_id, project_id, input_data.candidate_id
            )
            run = store.get_parsing_run(
                tenant_id, project_id, str(candidate["parsingRunId"])
            )
            _require_successful_run_for_approval(run)
            decisions = store.list_review_decisions(
                tenant_id, project_id, candidate_id=input_data.candidate_id
            )
            if len(decisions) != input_data.expected_decision_sequence:
                raise ImmutableRecordConflict(
                    "review decision sequence changed; reload before selecting a canonical value"
                )
            for conflict in store.list_source_conflicts(tenant_id, project_id):
                if input_data.candidate_id not in conflict.get("candidateIds", []):
                    continue
                if conflict.get("conflictStatus") == "resolved":
                    resolution_decision = store.get_review_decision(
                        tenant_id,
                        project_id,
                        str(conflict.get("resolutionReviewDecisionId") or ""),
                    )
                    if resolution_decision.get("candidateId") != input_data.candidate_id:
                        raise ParserV2ApprovalError(
                            "candidate is not the approved winner of a resolved source conflict"
                        )
            record = store.create_canonical_selection(
                tenant_id=tenant_id,
                project_id=project_id,
                selection_id=input_data.selection_id,
                actor_id=actor_id,
                candidate_id=input_data.candidate_id,
                review_decision_id=input_data.review_decision_id,
                selection_version=input_data.selection_version,
                supersedes_selection_id=input_data.supersedes_selection_id,
            )
        except Exception as exc:
            translate_domain_error(exc)
        audit_recorder(
            request,
            event_type="parser_v2_canonical_selection_created",
            project_id=project_id,
            run_id=candidate.get("parsingRunId"),
            metadata={
                "selectionId": input_data.selection_id,
                "candidateId": input_data.candidate_id,
                "reviewDecisionId": input_data.review_decision_id,
                "approvalStatus": record.get("approvalStatus"),
                "authorization": authz.get("authorization"),
            },
        )
        return record

    @router.post(
        "/projects/{project_id}/parsing-v2/approved-snapshots",
        status_code=status.HTTP_201_CREATED,
    )
    def create_approved_snapshot(
        project_id: str,
        request: Request,
        input_data: ApprovedInputSnapshotInput,
    ):
        _, authz, tenant_id, actor_id = authorize_project(
            request, project_id, "assumption:override"
        )
        require_review_pilot(authz)
        store = make_store()
        try:
            if set(input_data.expected_selection_versions) != set(input_data.selection_ids):
                raise ParserV2ApprovalError(
                    "expectedSelectionVersions must contain exactly the requested selectionIds"
                )
            active_selections = {
                str(item["selectionId"]): item
                for item in store.list_canonical_selections(
                    tenant_id, project_id, active_only=True
                )
                if item.get("selectionId") in input_data.selection_ids
            }
            if set(active_selections) != set(input_data.selection_ids):
                raise ParserV2ApprovalError(
                    "approved snapshot references an absent or invalidated selection"
                )
            for selection_id, expected_version in input_data.expected_selection_versions.items():
                selection = active_selections[selection_id]
                if int(selection.get("selectionVersion") or 0) != expected_version:
                    raise ImmutableRecordConflict(
                        "canonical selection version changed; reload before snapshot approval"
                    )
                candidate = store.get_candidate(
                    tenant_id, project_id, str(selection["candidateId"])
                )
                run = store.get_parsing_run(
                    tenant_id, project_id, str(candidate["parsingRunId"])
                )
                _require_successful_run_for_approval(run)
            record = store.create_approved_input_snapshot(
                tenant_id=tenant_id,
                project_id=project_id,
                snapshot_id=input_data.snapshot_id,
                actor_id=actor_id,
                selection_ids=input_data.selection_ids,
                purpose=input_data.purpose,
                supersedes_snapshot_id=input_data.supersedes_snapshot_id,
            )
        except Exception as exc:
            translate_domain_error(exc)
        audit_recorder(
            request,
            event_type="parser_v2_approved_snapshot_created",
            project_id=project_id,
            metadata={
                "snapshotId": input_data.snapshot_id,
                "selectionIds": input_data.selection_ids,
                "purpose": input_data.purpose,
                "approvalStatus": record.get("approvalStatus"),
                "authorization": authz.get("authorization"),
            },
        )
        public_snapshot = ApprovedInputSnapshot.model_validate(
            approved_input_snapshot_wire_payload(record)
        )
        return public_snapshot.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )

    return router


def _validate_candidate_evidence_identity(
    evidence: list[SourceLocator],
    *,
    document_version_id: str,
    source_hash: str,
    orchestration_artifact: Mapping[str, Any] | None,
) -> None:
    """Bind locators to both the archive and the run's structural artifact."""

    validate_source_locator_attestations(
        [item.model_dump(by_alias=True, mode="json", exclude_none=True) for item in evidence],
        dict(orchestration_artifact or {}),
    )

    for locator in evidence:
        if locator.document_version_id and locator.document_version_id != document_version_id:
            raise ParserV2ReferenceError(
                "candidate evidence belongs to another documentVersionId"
            )
        if locator.file_sha256 and locator.file_sha256 != source_hash:
            raise ParserV2ReferenceError("candidate evidence fileSha256 does not match the archive")
    if evidence and not any(
        locator.document_version_id == document_version_id
        and (locator.file_sha256 is None or locator.file_sha256 == source_hash)
        for locator in evidence
    ):
        raise ParserV2ReferenceError(
            "candidate evidence requires a locator bound to its documentVersionId"
        )


def _require_successful_run_for_approval(run: dict[str, Any]) -> None:
    if run.get("executionStatus") not in {"completed", "completed_with_warnings"}:
        raise ParserV2ApprovalError(
            "review and approval require a successfully completed parsing run"
        )


def _configured_allowlist(name: str) -> set[str]:
    return {
        item.strip()
        for item in os.environ.get(name, "").split(",")
        if item.strip()
    }


def _validate_derived_candidate_lineage(
    store: ParserV2RecordStore,
    calculation_run_list_fn: CalculationRunList,
    *,
    tenant_id: str,
    project_id: str,
    candidate_id: str,
    input_fact_ids: list[str],
    calculation_run_id: str | None,
) -> None:
    """Resolve every derived-fact edge against persisted deterministic records."""

    for input_fact_id in input_fact_ids:
        if input_fact_id == candidate_id:
            raise ParserV2ReferenceError("a derived candidate cannot reference itself")
        input_candidate = store.get_candidate(
            tenant_id, project_id, input_fact_id
        )
        if input_candidate.get("presenceStatus") != "present":
            raise ParserV2ApprovalError(
                "derived candidate inputs must be present Parser V2 candidates"
            )

    calculation_runs = calculation_run_list_fn(
        project_id,
        tenant_id=tenant_id,
        calculation_run_id=calculation_run_id,
        limit=1,
    )
    calculation_run = next(
        (
            item
            for item in calculation_runs
            if str(item.get("calculationRunId") or item.get("calculation_run_id"))
            == str(calculation_run_id or "")
        ),
        None,
    )
    if calculation_run is None:
        raise ParserV2ReferenceError(
            "derived candidate calculationRunId was not found in persisted calculation runs"
        )
    run_tenant = calculation_run.get("tenantId") or calculation_run.get("tenant_id")
    if run_tenant and str(run_tenant) != tenant_id:
        raise ParserV2ReferenceError(
            "derived candidate calculation run belongs to another tenant"
        )
    run_status = calculation_run.get("executionStatus") or calculation_run.get("status")
    if run_status and str(run_status) not in {
        "approved",
        "completed",
        "completed_with_warnings",
        "succeeded",
    }:
        raise ParserV2ApprovalError(
            "derived candidate calculation run has not completed successfully"
        )
    recorded_inputs = (
        calculation_run.get("inputFactIds")
        or calculation_run.get("inputCandidateIds")
        or calculation_run.get("input_fact_ids")
    )
    if not isinstance(recorded_inputs, list):
        raise ParserV2ReferenceError(
            "derived candidate calculation run does not contain input-fact lineage"
        )
    if set(str(value) for value in recorded_inputs) != set(input_fact_ids):
        raise ParserV2ReferenceError(
            "derived candidate inputFactIds do not match the persisted calculation run"
        )


__all__ = [
    "ApprovedInputSnapshotInput",
    "CandidateSetSealInput",
    "CanonicalSelectionInput",
    "ConflictResolutionInput",
    "ParsedFactCandidateInput",
    "ProjectParsingProfileInput",
    "ReviewDecisionInput",
    "SourceConflictInput",
    "StructuralParsingRunInput",
    "build_project_parser_v2_router",
]
